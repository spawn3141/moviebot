#!/bin/sh
# Startet moviebot unter dem Benutzer, den Unraid per PUID/PGID vorgibt
# (Standard 99:100 = nobody:users). So gehören die Dateien in /config nicht root,
# und du kommst über die Unraid-Freigabe ohne Rechteärger an sie heran.
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"

if [ ! -e /config/config.toml ]; then
    cp /opt/moviebot/config.example.toml /config/config.toml
    echo "moviebot: /config/config.toml aus der Vorlage angelegt – bitte TMDB-Token eintragen"
fi

# Ohne root-Rechte (z. B. 'docker run --user') bleibt nur, direkt zu starten.
if [ "$(id -u)" -ne 0 ]; then
    echo "moviebot: läuft als $(id -u):$(id -g), PUID/PGID werden übersprungen"
    exec python -m moviebot "$@"
fi

getent group "$PGID" >/dev/null 2>&1 || groupadd -g "$PGID" moviebot
getent passwd "$PUID" >/dev/null 2>&1 || useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin moviebot

chown -R "$PUID:$PGID" /config

echo "moviebot: startet als $(getent passwd "$PUID" | cut -d: -f1):$(getent group "$PGID" | cut -d: -f1) ($PUID:$PGID), Zeitzone ${TZ:-UTC}"
exec gosu "$PUID:$PGID" python -m moviebot "$@"
