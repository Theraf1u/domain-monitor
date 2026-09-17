FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8280

# PORT is read at container start (not build time) so this same image works
# both with the default bridge network (Docker remaps external:internal,
# internal stays 8280) and with network_mode: host (no remap - uvicorn must
# bind the actual external port directly, e.g. when the host needs to reach
# a host-local proxy that bridge networking can't route to).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD sh -c 'curl -fsS http://localhost:${PORT:-8280}/healthz' || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8280}"]
