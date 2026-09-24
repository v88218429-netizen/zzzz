FROM tailscale/tailscale:stable AS tailscale

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl socat xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY server.py .
COPY start.sh .
RUN chmod +x /app/start.sh \
    && mkdir -p /var/run/tailscale /var/lib/tailscale /tmp/tailscale

CMD ["/app/start.sh"]
