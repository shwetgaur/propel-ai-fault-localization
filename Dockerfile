# Single-image deploy for Hugging Face Spaces (and any Docker host).
# Builds the React console, then serves API + UI from one FastAPI process.

FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
ENV VITE_API_URL=
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /web/dist ./static

ENV PYTHONUNBUFFERED=1
ENV DATABASE_URL=sqlite:////data/faultloc.db
ENV SEED_ON_STARTUP=true
ENV SEED_POLES=2200
ENV STATIC_DIR=/app/static
ENV CORS_ORIGINS=*

RUN mkdir -p /data
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
