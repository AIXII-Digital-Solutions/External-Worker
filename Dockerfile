# external-worker — ARQ worker: external APIs (FlightRadar/AviationEdge/Airlabs/MS Graph)
# + scheduled (ARQ cron) domain jobs.
# Build: docker build -t external-worker:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/worker

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/api_data /app/Logs \
    && useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app \
    && chmod +x entrypoint.sh
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD pgrep -f "arq main.WorkerSettings" >/dev/null || exit 1

ENTRYPOINT ["./entrypoint.sh"]
