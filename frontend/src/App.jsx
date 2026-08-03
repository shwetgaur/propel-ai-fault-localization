import { useCallback, useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, Polyline, useMap } from "react-leaflet";

const API = import.meta.env.VITE_API_URL || "";

async function api(path, options) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg = data?.detail?.message || data?.detail || data?.raw || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function FitBounds({ points }) {
  const map = useMap();
  useEffect(() => {
    if (!points?.length) return;
    const lats = points.map((p) => p[0]);
    const lons = points.map((p) => p[1]);
    map.fitBounds(
      [
        [Math.min(...lats), Math.min(...lons)],
        [Math.max(...lats), Math.max(...lons)],
      ],
      { padding: [40, 40] }
    );
  }, [map, points]);
  return null;
}

const NEXT = {
  detected: ["acknowledged"],
  acknowledged: ["crew_assigned"],
  crew_assigned: ["resolved"],
  resolved: [],
  verified: ["closed"],
  closed: [],
};

export default function App() {
  const [stats, setStats] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [poles, setPoles] = useState([]);
  const [transformers, setTransformers] = useState([]);
  const [brief, setBrief] = useState(null);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);

  const selected = useMemo(
    () => tickets.find((t) => t.id === selectedId) || null,
    [tickets, selectedId]
  );

  const refresh = useCallback(async () => {
    const [s, t, p, dts] = await Promise.all([
      api("/api/stats"),
      api("/api/tickets"),
      api("/api/network/poles?limit=8000"),
      api("/api/network/transformers"),
    ]);
    setStats(s);
    setTickets(t);
    setPoles(p);
    setTransformers(dts);
    if (!selectedId && t.length) setSelectedId(t[0].id);
  }, [selectedId]);

  useEffect(() => {
    refresh().catch((e) => setToast({ error: true, text: e.message }));
    const id = setInterval(() => {
      refresh().catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    setBrief(selected?.ai_brief || null);
  }, [selected?.id, selected?.ai_brief]);

  const focusPoints = useMemo(() => {
    if (selected) {
      const pts = [[selected.lat, selected.lon]];
      const dark = new Set(selected.dark_pole_ids || []);
      for (const p of poles) {
        if (dark.has(p.id) || p.dt_id === selected.dt_id) pts.push([p.lat, p.lon]);
      }
      return pts.slice(0, 400);
    }
    return transformers.slice(0, 50).map((d) => [d.lat, d.lon]);
  }, [selected, poles, transformers]);

  const spanLine = useMemo(() => {
    if (!selected || selected.fault_type !== "span") return null;
    const down = poles.find((p) => p.id === selected.downstream_pole_id);
    const up = poles.find((p) => p.id === selected.upstream_pole_id);
    if (down && up) return [[up.lat, up.lon], [down.lat, down.lon]];
    if (down) {
      const dt = transformers.find((d) => d.id === selected.dt_id);
      if (dt) return [[dt.lat, dt.lon], [down.lat, down.lon]];
    }
    return null;
  }, [selected, poles, transformers]);

  async function transition(status) {
    if (!selected) return;
    setBusy(true);
    try {
      const t = await api(`/api/tickets/${selected.id}/transition`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      setToast({ text: `Ticket → ${t.status}` });
      await refresh();
    } catch (e) {
      setToast({ error: true, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function loadBrief() {
    if (!selected) return;
    setBusy(true);
    try {
      const r = await api(`/api/tickets/${selected.id}/brief`, { method: "POST" });
      setBrief(r.brief);
      setToast({ text: `Brief from ${r.source}` });
      await refresh();
    } catch (e) {
      setToast({ error: true, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function runSim(path, body = {}) {
    setBusy(true);
    try {
      const r = await api(path, { method: "POST", body: JSON.stringify(body) });
      const ticketNote = r.tickets?.length
        ? `Tickets: ${r.tickets.join(", ")}`
        : r.note || "No tickets (expected for noise cases)";
      setToast({ text: ticketNote });
      if (r.tickets?.length) setSelectedId(r.tickets[0]);
      await refresh();
    } catch (e) {
      setToast({ error: true, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  const openTickets = tickets.filter((t) =>
    ["detected", "acknowledged", "crew_assigned", "resolved", "verified"].includes(t.status)
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>KSPDB Control Room</strong>
          <span>Subdivision SD-07 · Fault localization & ticket desk</span>
        </div>
        <div className="stats">
          <div className="stat">
            <b>{stats?.open_tickets ?? "—"}</b>
            <small>Open tickets</small>
          </div>
          <div className="stat">
            <b>{stats?.dark_poles ?? "—"}</b>
            <small>Dark poles</small>
          </div>
          <div className="stat">
            <b>{stats?.poles ?? "—"}</b>
            <small>Poles seeded</small>
          </div>
          <div className="stat">
            <b>
              {stats
                ? `${stats.topology_missing_dts}/${stats.transformers}`
                : "—"}
            </b>
            <small>DTs missing topology</small>
          </div>
        </div>
      </header>

      <div className="layout">
        <aside className="panel">
          <h2>Incidents</h2>
          {!openTickets.length && <div className="empty">No open faults. Use the simulator →</div>}
          {openTickets.map((t) => (
            <button
              key={t.id}
              className={`ticket ${selectedId === t.id ? "active" : ""}`}
              onClick={() => setSelectedId(t.id)}
            >
              <div className="row">
                <div className="title">{t.asset_label}</div>
                <span className={`badge ${t.status}`}>{t.status.replace("_", " ")}</span>
              </div>
              <div className="meta">
                {t.fault_type.toUpperCase()} · PIN {t.pincode || "?"} ·{" "}
                {(t.confidence * 100).toFixed(0)}% · {t.affected_poles} poles
              </div>
              <div className="meta">
                {t.topology_source === "inferred" ? "Inferred topology" : "Recorded topology"}
              </div>
            </button>
          ))}
          <h2>History</h2>
          {tickets
            .filter((t) => t.status === "closed" || t.status === "verified")
            .slice(0, 8)
            .map((t) => (
              <button
                key={t.id}
                className={`ticket ${selectedId === t.id ? "active" : ""}`}
                onClick={() => setSelectedId(t.id)}
              >
                <div className="row">
                  <div className="title">{t.asset_label}</div>
                  <span className={`badge ${t.status}`}>{t.status}</span>
                </div>
              </button>
            ))}
        </aside>

        <section className="map-wrap">
          <MapContainer center={[12.95, 77.59]} zoom={13} scrollWheelZoom>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <FitBounds points={focusPoints} />
            {transformers.map((d) => (
              <CircleMarker
                key={d.id}
                center={[d.lat, d.lon]}
                radius={6}
                pathOptions={{
                  color: d.topology_known ? "#0b6e4f" : "#a16207",
                  fillColor: d.topology_known ? "#0b6e4f" : "#a16207",
                  fillOpacity: 0.7,
                }}
              >
                <Popup>
                  DT {d.id}
                  <br />
                  {d.topology_known ? "Topology recorded" : "Topology missing (inferred)"}
                  <br />
                  {d.households_served} households
                </Popup>
              </CircleMarker>
            ))}
            {poles
              .filter((p) => !p.energized)
              .map((p) => (
                <CircleMarker
                  key={p.id}
                  center={[p.lat, p.lon]}
                  radius={4}
                  pathOptions={{ color: "#9f1239", fillColor: "#9f1239", fillOpacity: 0.85 }}
                >
                  <Popup>
                    {p.id} · dark
                    <br />
                    DT {p.dt_id}
                  </Popup>
                </CircleMarker>
              ))}
            {selected && (
              <CircleMarker
                center={[selected.lat, selected.lon]}
                radius={10}
                pathOptions={{ color: "#b45309", fillColor: "#fdba74", fillOpacity: 0.9, weight: 3 }}
              >
                <Popup>
                  Fault location
                  <br />
                  {selected.asset_label}
                </Popup>
              </CircleMarker>
            )}
            {spanLine && (
              <Polyline positions={spanLine} pathOptions={{ color: "#9f1239", weight: 4, dashArray: "6 6" }} />
            )}
          </MapContainer>
          <div className="map-legend">
            <div>Green DT = recorded wiring · Amber DT = inferred</div>
            <div>Red dots = dark poles · Orange = localized fault</div>
          </div>
        </section>

        <aside className="panel">
          <h2>Ticket detail</h2>
          {!selected ? (
            <div className="empty">Select an incident</div>
          ) : (
            <div className="detail">
              <h3>{selected.asset_label}</h3>
              <span className={`badge ${selected.status}`}>{selected.status.replace("_", " ")}</span>
              <div className="kv">
                <span>ID</span>
                <div>{selected.id}</div>
                <span>Navigate</span>
                <div>
                  {selected.lat.toFixed(5)}, {selected.lon.toFixed(5)}
                </div>
                <span>PIN</span>
                <div>{selected.pincode || "Not on registry — confirm on site"}</div>
                <span>Impact</span>
                <div>
                  {selected.affected_poles} poles · ~{selected.affected_households_est} households
                </div>
                <span>Confidence</span>
                <div>
                  {(selected.confidence * 100).toFixed(0)}% — {selected.confidence_reason}
                </div>
                <span>Topology</span>
                <div>{selected.topology_source}</div>
              </div>
              <div className="actions">
                {(NEXT[selected.status] || []).map((s) => (
                  <button
                    key={s}
                    className="primary"
                    disabled={busy}
                    onClick={() => transition(s)}
                  >
                    Mark {s.replace("_", " ")}
                  </button>
                ))}
                <button disabled={busy} onClick={loadBrief}>
                  AI dispatch brief
                </button>
                {selected.status === "verified" && (
                  <button className="primary" disabled={busy} onClick={() => transition("closed")}>
                    Close
                  </button>
                )}
              </div>
              {brief && <pre className="brief">{brief}</pre>}
            </div>
          )}

          <h2>Fault simulator</h2>
          <div className="sim">
            <p>
              Inject faults and noise the way reviewers will. Span / DT / feeder create tickets.
              Dead sensor and scheduled outage must not.
            </p>
            <div className="sim-actions">
              <button className="primary" disabled={busy} onClick={() => runSim("/api/simulator/span")}>
                Inject span fault
              </button>
              <button disabled={busy} onClick={() => runSim("/api/simulator/dt")}>
                Inject DT fault
              </button>
              <button disabled={busy} onClick={() => runSim("/api/simulator/feeder")}>
                Inject feeder fault
              </button>
              <button disabled={busy} onClick={() => runSim("/api/simulator/dead-sensor")}>
                Kill sensor (no outage)
              </button>
              <button disabled={busy} onClick={() => runSim("/api/simulator/scheduled-outage")}>
                Run scheduled outage
              </button>
              <button disabled={busy} onClick={() => runSim("/api/simulator/repair", { fault_index: -1 })}>
                Repair latest fault
              </button>
            </div>
            {toast && <div className={`toast ${toast.error ? "error" : ""}`}>{toast.text}</div>}
          </div>
        </aside>
      </div>
    </div>
  );
}
