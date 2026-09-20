# --- 1. Weboberfläche bauen -------------------------------------------------
# vite.config.js legt das Ergebnis nach ../moviebot/web, also /build/moviebot/web.
FROM node:22-alpine AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Server --------------------------------------------------------------
FROM python:3.13-slim

# gosu: am Ende die Rechte an den Unraid-Benutzer abgeben.
# tzdata: damit TZ=Europe/Berlin wirkt (täglicher Abgleich läuft nach lokaler Zeit).
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MOVIEBOT_CONFIG=/config/config.toml \
    PUID=99 \
    PGID=100 \
    TZ=Europe/Berlin

COPY pyproject.toml /src/
COPY moviebot/ /src/moviebot/
COPY --from=web /build/moviebot/web/ /src/moviebot/web/
RUN pip install --no-cache-dir /src && rm -rf /src

COPY config.example.toml /opt/moviebot/config.example.toml
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /config
VOLUME /config
EXPOSE 8080

# Läuft der Server und antwortet die API? Unraid zeigt das Ergebnis in der Übersicht.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/status', timeout=4)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
