FROM python:3.12-slim

WORKDIR /app

# Pre-seed the debconf answer wireshark-common's postinst asks interactively
# (whether non-root users may capture packets) so `apt-get install` never
# blocks waiting for input during the image build.
RUN echo "wireshark-common wireshark-common/install-setuid boolean true" | debconf-set-selections

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        tshark ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/

RUN mkdir -p /data
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/data/agent_buffer.db').execute('SELECT 1')" || exit 1

CMD ["python", "-m", "app.main"]
