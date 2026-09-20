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
- **Neu**: Neuzugänge und neue Staffeln in deinen Diensten, nach Tagen gruppiert.
- **Listen**: manuelle Merklisten (☆ auf der Kachel = Standardliste, Detailansicht/Picker für
  mehrere Listen); anlegen, umbenennen, löschen, Standard festlegen. Titel dürfen auf 0..n Listen
  stehen und bleiben auch dann in der Liste, wenn sie in keinem Dienst mehr laufen.
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
| `GET /api/new` | Neuzugänge und neue Staffeln in meinen Diensten |
| `GET/PUT /api/services` | Dienste, Abos an/aus |
| `GET/PUT /api/settings` | kostenlose Angebote einbeziehen |
| `GET/POST /api/lists`, `PATCH/DELETE /api/lists/{id}` | Listen verwalten |
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
- **Neue Staffeln**: laufende Serien werden täglich geprüft; eine Staffel gilt als neu, sobald ihr
  Ausstrahlungsdatum erreicht ist. Hinweis: TMDB weiß nur, dass die *Serie* im Abo ist, nicht,
  ob die neue Staffel dort schon verfügbar ist.
- **Gegenprüfung**: pro Titel wird die Verfügbarkeit einzeln nachgeschlagen (`verified`),
  um Titel auszusortieren, die beim Dienst nur zum Leihen/Kaufen sind.
- **Angebote** (`offers`): bei jeder Detailabfrage werden alle Angebote des Titels gespeichert
  (Abo, kostenlos, mit Werbung, Leihen, Kaufen – bei allen Anbietern). Titel aus der Zeit davor
  bekommen sie beim nächsten ohnehin fälligen Abruf oder per `--backfill`.
- **Täglicher Abgleich**: `serve` gleicht jeden Tag um `[schedule] time` (Standard 06:00) ab;
  verpasste Läufe werden nach dem Start nachgeholt. „Jetzt abgleichen“ in den Einstellungen bzw.
  `POST /api/snapshot`. Eine Sperrdatei verhindert zwei gleichzeitige Abgleiche (Server und CLI).
- **Altersfreigabe**: deutsche FSK, sonst die US-Freigabe umgerechnet (G→0, PG→6, PG-13→12, R→16,
  NC-17→18; Serien TV-Y/TV-G→0, TV-Y7/TV-PG→6, TV-14→12, TV-MA→16), in der Oberfläche mit * markiert.
  Kommt mit derselben Detailabfrage; ältere Titel per `--backfill`. Filter `max_age` (+ `include_unrated`).
- **Listen**: `lists` + `list_items`. Das Schema kennt schon `kind` ('manual'/'dynamic') und
  `filters`; dynamische Listen (gespeicherter Filter) sind damit ohne Migration nachrüstbar.
- **Migrationen**: Schemaänderungen werden beim Start automatisch angewendet; vorher wird eine
  Sicherung `moviebot.db.bak-v<alte Version>` angelegt.
