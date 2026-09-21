# moviebot

Persönliche Film- und Serien-Empfehlungen für die deutschen Streaming-Dienste.
Aktueller Stand: Datenbasis (Kataloge, Neuzugänge, neue Staffeln, „Meine Abos“), REST-Server
und Weboberfläche. Python ≥ 3.11 (FastAPI), Oberfläche mit Vue.

Streaming-Verfügbarkeitsdaten: JustWatch (über TMDB).

## Einrichtung

0. Python-Umgebung (einmalig):
   `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
   Danach alle Befehle mit `.venv/bin/python -m moviebot …` statt `python3 -m moviebot …` ausführen.
   Weboberfläche bauen (einmalig und nach Änderungen daran):
   `cd frontend && npm install && npm run build`
1. TMDB-Token in `config.toml` bei `read_access_token` eintragen.
2. Anbieter-IDs prüfen: `python3 -m moviebot providers`
   (zeigt unten, ob die IDs aus der Config bekannt sind)
3. Erster Lauf: `python3 -m moviebot snapshot`

## Docker / Unraid

Das Image wird von GitHub Actions gebaut (`.github/workflows/docker.yml`) und liegt danach als
`ghcr.io/spawn3141/moviebot:latest` in der GitHub Container Registry. Unraid zieht es von dort –
auf dem Server wird nichts gebaut, und Node braucht es dort auch nicht.

Gebaut wird **nicht automatisch** – ein Push auf `main` lässt nur die Tests laufen. Eine neue
Image-Version löst du selbst aus:

- GitHub → Reiter **Actions** → Workflow **Docker-Image** → **Run workflow**
- oder im Terminal: `gh workflow run docker.yml`
- oder ein Versions-Tag: `git tag v1.0 && git push origin v1.0` (baut zusätzlich `:v1.0`)

Jeder Lauf überschreibt `:latest` und legt eine unveränderliche Marke `:sha-<commit>` daneben –
die ist dein Weg zurück, falls eine neue Version auf Unraid Ärger macht. Gebaut wird nur, wenn
die Tests durchlaufen. Wer lokal bauen will:

```
docker compose up --build          # lokal ausprobieren → http://localhost:8080
```

**Ein einziges Volume** genügt: `/config`. Darin liegt alles, was bleiben soll:

```
/config/config.toml           ← Token und Dienste; wird beim ersten Start aus der Vorlage angelegt
/config/data/moviebot.db      ← Datenbank, Sicherungen, Sperrdatei
```

Der Datenbankpfad aus `[database] path` wird relativ zur `config.toml` aufgelöst – darum landet
die Datenbank von selbst neben ihr im Volume.

**Umgebungsvariablen**

| Variable | Standard | Zweck |
|---|---|---|
| `PUID` / `PGID` | `99` / `100` | Benutzer, dem die Dateien in `/config` gehören (Unraid: `nobody:users`) |
| `TZ` | `Europe/Berlin` | Der tägliche Abgleich läuft nach lokaler Zeit – ohne das wäre 06:00 die UTC-Zeit |
| `TMDB_READ_ACCESS_TOKEN` | – | Token, falls er nicht in der `config.toml` stehen soll (hat Vorrang) |
| `MOVIEBOT_CONFIG` | `/config/config.toml` | Pfad zur Konfiguration |

### Auf Unraid einrichten

1. **Docker → Add Container**, oben auf **Advanced View** umschalten.
2. **Name**: `moviebot`
   **Repository**: `ghcr.io/spawn3141/moviebot:latest`
3. **Add another Path**: Container `/config` → Host `/mnt/user/appdata/moviebot`, Access `Read/Write`
4. **Add another Port**: Container `8080` → Host `8080` (oder eine freie Nummer)
5. **Add another Variable** für `PUID` = `99`, `PGID` = `100`, `TZ` = `Europe/Berlin`
6. **Apply**. Unraid lädt das Image und startet den Container. Beim ersten Start entsteht
   `/mnt/user/appdata/moviebot/config.toml` – dort den TMDB-Token bei `read_access_token`
   eintragen (oder als Variable `TMDB_READ_ACCESS_TOKEN` setzen) und den Container neu starten.
7. Oberfläche öffnen: `http://<unraid-ip>:8080`. In den Einstellungen die Abos setzen und
   **„Jetzt abgleichen"** drücken – der erste Lauf dauert eine Weile und ist nur Ausgangsstand,
   Neuzugänge erscheinen ab dem nächsten Tag.

Neue Version einspielen: im Docker-Reiter auf **Check for Updates** → **Apply Update**.
Die Daten in `/config` bleiben dabei erhalten; Schemaänderungen werden beim Start automatisch
angewendet (vorher legt moviebot eine Sicherung an).

Ohne Token startet der Server trotzdem; die Oberfläche zeigt dann den Fehler beim Abgleich an.
Danach läuft der Abgleich täglich um `[schedule] time` im Container selbst – der Container
sollte also durchlaufen. Verpasste Läufe werden nach einem Neustart nachgeholt.

Für die CLI im laufenden Container:

```
docker exec -it moviebot python -m moviebot status
docker exec -it moviebot python -m moviebot providers --search wow
```

## Befehle

| Befehl | Zweck |
|---|---|
| `python3 -m moviebot providers [--search X]` | Anbieter-IDs nachschlagen, Config prüfen |
| `python3 -m moviebot snapshot` | Kataloge holen, mit gestern vergleichen, Details laden |
| `python3 -m moviebot snapshot --service wow --skip-details` | nur ein Dienst, ohne Details (schnell) |
| `python3 -m moviebot snapshot --backfill 500` | zusätzlich Angebote + Altersfreigaben für 500 ältere Titel nachladen |
| `python3 -m moviebot abo` / `abo disney an` | Meine Abos anzeigen / ändern |
| `python3 -m moviebot serve` | Server starten → Oberfläche http://127.0.0.1:8080, API-Doku /docs |
| `python3 -m moviebot status` | Katalogstand, letzte Läufe, Neuzugänge, neue Staffeln |
| `python3 -m unittest` | Tests (offline) |

## Weboberfläche

- **Entdecken**: filtern nach Film/Serie, Genre, Jahr, Dienst, Suche, „neu in N Tagen“; sortieren;
  gesehen / Sterne / nicht interessiert direkt auf der Kachel (mit Rückgängig).
- Neuzugänge und neue Staffeln über den Filter „Neu: N Tage“ (Kacheln zeigen „Neu bei WOW“
  bzw. „Staffel 3“); beim ersten Start liegt dafür der gespeicherte Filter „Neu“ bereit.
- **Titel hinzufügen** (Einstellungen): Suche bei TMDB, um ältere Titel außerhalb von `min_year`
  einzeln aufzunehmen – zum Bewerten oder um zu erfahren, wenn sie in einem Dienst auftauchen.
  Ihre Verfügbarkeit wird aus den eigenen Angeboten des Titels abgeleitet und täglich aufgefrischt.
- **Staffeln einzeln abhaken** (Detailansicht einer Serie): ✓-Knopf neben jeder erschienenen
  Staffel, derselbe wie auf der Kachel. Die Serie gilt als gesehen, sobald alle erschienenen Staffeln markiert sind, und
  wieder als ungesehen, sobald eine neue erscheint – so taucht sie von selbst wieder auf,
  wenn es weitergeht. Das ✓ auf der Kachel hakt alle erschienenen Staffeln auf einmal ab.
- **Listen**: Sammlungen von Titeln (☆ auf der Kachel = Standardliste, Detailansicht/Picker für
  mehrere Listen); anlegen, umbenennen, löschen, Standard festlegen. Titel dürfen auf 0..n Listen
  stehen und bleiben auch dann in der Liste, wenn sie in keinem Dienst mehr laufen.
- **Gespeicherte Filter**: gespeicherte Suchen, als Knöpfe über der Filterleiste auf „Entdecken“.
  Ein Klick setzt den Filter; danach Speichern / Verwerfen / Als neuen Filter speichern.
- **Einstellungen**: Abos an/aus, kostenlose Angebote, Datenstand.

Code in `frontend/` (Vue + Vite). Beim Entwickeln: `.venv/bin/python -m moviebot serve` und
parallel `cd frontend && npm run dev` → http://localhost:5173 (lädt Änderungen sofort neu).
`npm run build` legt die fertige Oberfläche nach `moviebot/web/`, von wo `serve` sie ausliefert.

## REST-API (Auszug, alles ausprobierbar unter `/docs`)

| Endpunkt | Zweck |
|---|---|
| `GET /api/titles` | filtern (Dienst, Film/Serie, Genre, Jahr, Suche, neu in N Tagen) und sortieren |
| `GET /api/titles/{id}` | alle Infos inkl. Staffeln und Angeboten |
| `PUT /api/titles/{id}/state` | gesehen / Bewertung 1–5 / nicht interessiert |
| `PUT /api/titles/{id}/seasons/{n}` | einzelne Staffel einer Serie als gesehen markieren |
| `GET/PUT /api/services` | Dienste, Abos an/aus |
| `GET/PUT /api/settings` | kostenlose Angebote einbeziehen |
| `GET/POST /api/lists`, `PATCH/DELETE /api/lists/{id}` | Listen verwalten |
| `GET/POST /api/filters`, `PATCH/DELETE /api/filters/{id}` | gespeicherte Filter verwalten |
| `GET /api/search`, `POST /api/titles/import`, `DELETE /api/titles/{id}/import` | Titel von Hand aufnehmen |
| `PUT /api/titles/{id}/lists` | Listen eines Titels setzen |
| `GET /api/genres`, `GET /api/status` | Genre-Liste, Datenstand |

Genres von Filmen und Serien sind vereinheitlicht („Action & Adventure“ zählt als Action und Abenteuer).
Gezeigt werden nur Titel, deren Verfügbarkeit bestätigt ist. Sortierung „rating“ gewichtet nach Anzahl
der Stimmen, damit 9,5 bei 3 Stimmen nicht vor 7,8 bei 5000 Stimmen landet.

## Wie es funktioniert

- **Dienste** (`[[services]]` in der Config) werden täglich verfolgt, egal ob abonniert.
  Ein Dienst kann mehrere TMDB-IDs haben (z. B. Prime Video + „Prime Video with Ads“).
- **Meine Abos** ist davon getrennt und bestimmt, was gezeigt und empfohlen wird.
- **Kostenlose Dienste** (`free = true`: Mediatheken, Joyn, Pluto TV …) braucht man nicht zu abonnieren;
  sie werden einbezogen, wenn die Einstellung `include_free` an ist.
- **Zeitraum** `min_year`: Filme ab diesem Erscheinungsjahr, Serien mit Folgen seit diesem Jahr.
- **Neuzugänge**: täglicher Katalog-Vergleich. Der erste Lauf ist nur Ausgangsstand (keine Meldungen).
  Wird `min_year` geändert, gelten dazukommende Titel nicht als neu, und herausfallende nicht als weg.
- **Nicht mehr im Abo**: erst nach 2 Tagen in Folge fehlend; der Titel selbst bleibt in der Datenbank.
  Bricht ein Katalog um > 20 % ein, wird nichts als weg gezählt (vermutlich API-Lücke).
- **Neue Staffeln**: geprüft werden nur Serien, die TMDB seit dem letzten Lauf als geändert meldet
  (`/tv/changes`, ~25 Abfragen statt einer pro Serie); zusätzlich jede Serie spätestens alle
  `series_refresh_days` Tage. Eine Staffel gilt als neu, sobald ihr Ausstrahlungsdatum erreicht ist. Hinweis: TMDB weiß nur, dass die *Serie* im Abo ist, nicht,
  ob die neue Staffel dort schon verfügbar ist.
- **Gegenprüfung**: pro Titel wird die Verfügbarkeit einzeln nachgeschlagen (`verified`),
  um Titel auszusortieren, die beim Dienst nur zum Leihen/Kaufen sind. Kennt TMDB für die Region
  noch gar keine Angebote (oft bei brandneuen Titeln), gilt das als *unbekannt* (`NULL`): Der Titel
  bleibt sichtbar, wird täglich erneut geprüft und erst nach 14 Tagen ohne Daten ausgeblendet.
- **Angebote** (`offers`): bei jeder Detailabfrage werden alle Angebote des Titels gespeichert
  (Abo, kostenlos, mit Werbung, Leihen, Kaufen – bei allen Anbietern). Titel aus der Zeit davor
  bekommen sie beim nächsten ohnehin fälligen Abruf oder per `--backfill`.
- **Täglicher Abgleich**: `serve` gleicht jeden Tag um `[schedule] time` (Standard 06:00) ab;
  verpasste Läufe werden nach dem Start nachgeholt. „Jetzt abgleichen“ in den Einstellungen bzw.
  `POST /api/snapshot`. Eine Sperrdatei verhindert zwei gleichzeitige Abgleiche (Server und CLI).
- **Altersfreigabe**: deutsche FSK, sonst die US-Freigabe umgerechnet (G→0, PG→6, PG-13→12, R→16,
  NC-17→18; Serien TV-Y/TV-G→0, TV-Y7/TV-PG→6, TV-14→12, TV-MA→16), in der Oberfläche mit * markiert.
  Kommt mit derselben Detailabfrage; ältere Titel per `--backfill`. Filter `max_age` (+ `include_unrated`).
- **Listen und Filter sind getrennt**: `lists` + `list_items` enthalten Titel, `saved_filters`
  nur gespeicherte Suchen (`filter_id` in `GET /api/titles` wendet einen an). Ein gespeicherter
  Filter gewinnt gegenüber gleichnamigen Parametern; andere Parameter schränken zusätzlich ein.
- **Gesehene Staffeln** (`season_state`) sind Benutzerdaten und stehen deshalb neben `seasons`,
  das bei jedem Abruf aus TMDB überschrieben wird. Zeile vorhanden = gesehen. Der Status der
  Serie folgt daraus: gesehen, wenn jede erschienene Staffel markiert ist. Eine Bewertung
  überlebt den automatischen Rückfall auf „ungesehen“; nur ein Klick von Hand löscht sie.
  `PUT /api/titles/{id}/state` liefert in `seasons_before` die Haken von vor der Änderung und
  nimmt sie als `seasons` wieder entgegen – damit „Rückgängig“ auch einzeln gesetzte Haken
  zurückholt, die das titelweite „ungesehen“ sonst mitlöschen würde.
- **Migrationen**: Schemaänderungen werden beim Start automatisch angewendet; vorher wird eine
  Sicherung `moviebot.db.bak-v<alte Version>` angelegt.
