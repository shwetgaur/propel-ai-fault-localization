from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Feeder(Base):
    __tablename__ = "feeders"
    id = Column(String, primary_key=True)
    substation_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)


class DistributionTransformer(Base):
    __tablename__ = "transformers"
    id = Column(String, primary_key=True)
    feeder_id = Column(String, ForeignKey("feeders.id"), nullable=False, index=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    capacity_kva = Column(Integer, nullable=False)
    households_served = Column(Integer, nullable=False)
    topology_known = Column(Boolean, nullable=False, default=False)


class Pole(Base):
    __tablename__ = "poles"
    id = Column(String, primary_key=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    feeder_id = Column(String, ForeignKey("feeders.id"), nullable=False, index=True)
    dt_id = Column(String, ForeignKey("transformers.id"), nullable=False, index=True)
    seq_on_line = Column(Integer, nullable=True)
    parent_pole_id = Column(String, nullable=True, index=True)
    pole_type = Column(String, nullable=False, default="LT-9m-PCC")
    ward = Column(String, nullable=False)
    pincode = Column(String, nullable=True)
    device_id = Column(String, nullable=True, unique=True)
    has_device = Column(Boolean, nullable=False, default=True)
    firmware = Column(String, nullable=False, default="1.4.2")
    # Runtime state
    energized = Column(Boolean, nullable=False, default=True)
    last_seq = Column(Integer, nullable=False, default=0)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    device_online = Column(Boolean, nullable=False, default=True)
    inferred_parent_id = Column(String, nullable=True)


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (UniqueConstraint("device_id", "seq", name="uq_device_seq"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, nullable=False, index=True)
    pole_id = Column(String, ForeignKey("poles.id"), nullable=False, index=True)
    event = Column(String, nullable=False)
    energized = Column(Boolean, nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    seq = Column(Integer, nullable=False)
    battery_mv = Column(Integer, nullable=True)
    rssi = Column(Integer, nullable=True)
    fw = Column(String, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    accepted = Column(Boolean, nullable=False, default=True)
    drop_reason = Column(String, nullable=True)


class ScheduledOutage(Base):
    __tablename__ = "scheduled_outages"
    id = Column(String, primary_key=True)
    scope = Column(String, nullable=False)  # feeder | dt
    target_id = Column(String, nullable=False, index=True)
    start = Column(DateTime(timezone=True), nullable=False)
    end = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String, nullable=False)
    cancelled = Column(Boolean, nullable=False, default=False)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="detected", index=True)
    # detected | acknowledged | crew_assigned | resolved | verified | closed | rejected
    fault_type = Column(String, nullable=False)  # span | dt | feeder
    feeder_id = Column(String, nullable=False)
    dt_id = Column(String, nullable=True)
    upstream_pole_id = Column(String, nullable=True)
    downstream_pole_id = Column(String, nullable=True)
    asset_label = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    pincode = Column(String, nullable=True)
    affected_poles = Column(Integer, nullable=False, default=0)
    affected_households_est = Column(Integer, nullable=False, default=0)
    confidence = Column(Float, nullable=False)
    confidence_reason = Column(Text, nullable=False)
    topology_source = Column(String, nullable=False)  # recorded | inferred | coarse
    dark_pole_ids = Column(Text, nullable=False, default="[]")  # JSON list
    evidence = Column(Text, nullable=False, default="{}")  # JSON
    ai_brief = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    crew_assigned_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    operator_note = Column(Text, nullable=True)
    suppress_reason = Column(String, nullable=True)


class SimulatorScenario(Base):
    __tablename__ = "simulator_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    active_faults = Column(Text, nullable=False, default="[]")
    last_action = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def make_engine(url: str | None = None):
    db_url = url or settings.database_url
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
