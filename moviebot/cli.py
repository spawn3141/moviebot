import argparse
import json
import logging
import sys
from pathlib import Path

from . import catalog, queries, snapshot
from .config import load_config
from .db import SchemaMismatch, connect, get_setting
from .scheduler import SnapshotScheduler, snapshot_lock
from .tmdb import TMDBClient, TMDBError

MEDIA_LABEL = {"movie": "Filme", "tv": "Serien"}


def make_client(cfg) -> TMDBClient:
    return TMDBClient.from_config(cfg)


def open_db(cfg):
    conn = connect(cfg.db_path)
    catalog.bootstrap(conn, cfg)
    return conn


def cmd_init_db(cfg, args) -> int:
    open_db(cfg)
    print(f"Datenbank bereit: {cfg.db_path}")
    return 0


def cmd_providers(cfg, args) -> int:
    client = make_client(cfg)
    by_id = {}
    for media_type in ("movie", "tv"):
        for p in client.watch_providers(media_type):
            by_id.setdefault(p["provider_id"], p)
    conn = open_db(cfg)
    with conn:
        catalog.upsert_providers(conn, list(by_id.values()))

    configured = {p: s.key for s in cfg.services for p in s.provider_ids}
    needle = (args.search or "").lower()
    shown = sorted((p for p in by_id.values() if needle in p["provider_name"].lower()),
                   key=lambda p: p.get("display_priorities", {}).get(cfg.region, 999))
    print(f"{'ID':>6}  Name  (Region {cfg.region}, {len(shown)} von {len(by_id)})")
    for p in shown:
        marker = f"  ← {configured[p['provider_id']]}" if p["provider_id"] in configured else ""
        print(f"{p['provider_id']:>6}  {p['provider_name']}{marker}")

    print("\nKonfigurierte Dienste:")
    for s in cfg.services:
        ids = ", ".join(
            f"{p} = {by_id[p]['provider_name']}" if p in by_id else f"{p} = ⚠ unbekannt"
            for p in s.provider_ids
        )
        print(f"  {s.key:<12} {s.name}: {ids}")
    return 0


def cmd_snapshot(cfg, args) -> int:
    client = make_client(cfg)
    conn = open_db(cfg)
    with snapshot_lock(cfg.db_path) as acquired:
        if not acquired:
            print("Es läuft bereits ein Abgleich (z. B. im Server). Bitte später erneut versuchen.",
                  file=sys.stderr)
            return 3
        since = queries.last_event_id(conn)
        results = snapshot.run(
            conn, client, cfg, service_keys=args.service or None,
            fetch_details=not args.skip_details, details_limit=args.details_limit,
            backfill=args.backfill,
        )
    failed = False
    for (service, media_type), r in results.items():
        label = f"{cfg.service(service).name} – {MEDIA_LABEL[media_type]}"
        if r is None:
            print(f"{label}: FEHLGESCHLAGEN (siehe Log)")
            failed = True
            continue
        print(f"{label}: {r.status}, {r.total} Titel, +{len(r.added)} neu, "
              f"+{len(r.readded)} wieder da, -{len(r.removed)} nicht mehr im Abo")
    summary = queries.run_summary(conn, results, since)
    queries.store_run_summary(conn, summary)
    print(f"Insgesamt: {summary['added']} neu, {summary['readded']} wieder da, "
          f"{summary['removed']} nicht mehr im Abo, {summary['new_seasons']} neue Staffeln")
    print(f"TMDB-Abfragen: {client.request_count}")
    return 1 if failed else 0


def cmd_abo(cfg, args) -> int:
    conn = open_db(cfg)
    if args.service:
        with conn:
            catalog.set_subscribed(conn, args.service, args.state == "an")
    rows = conn.execute(
        "SELECT key, name, subscribed, free FROM services WHERE tracked = 1 ORDER BY name"
    ).fetchall()
    print("Abos:")
    for r in rows:
        if not r["free"]:
            print(f"  {'[x]' if r['subscribed'] else '[ ]'} {r['key']:<12} {r['name']}")
    include_free = get_setting(conn, "include_free") == "1"
    print(f"\nKostenlos (werden {'einbezogen' if include_free else 'NICHT einbezogen'}):")
    for r in rows:
        if r["free"]:
            print(f"      {r['key']:<12} {r['name']}")
    return 0


def cmd_serve(cfg, args) -> int:
    import uvicorn

    from .api import create_app

    open_db(cfg).close()  # sync services from the config before serving
    scheduler = None if args.no_schedule else SnapshotScheduler(cfg)
    print(f"moviebot läuft: http://{args.host}:{args.port}  (API-Doku: /docs, beenden mit Strg+C)")
    if scheduler and cfg.snapshot_time:
        print(f"Täglicher Abgleich um {cfg.snapshot_time.strftime('%H:%M')} "
              "(verpasste Läufe werden beim Start nachgeholt)")
    uvicorn.run(create_app(cfg, scheduler), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_status(cfg, args) -> int:
    conn = open_db(cfg)
    names = {r["key"]: r["name"] for r in conn.execute("SELECT key, name FROM services")}
    print(f"Zeitraum: ab {cfg.min_year}")

    print("\n== Katalog (aktuell im Abo) ==")
    rows = conn.execute(
        """
        SELECT s.name, s.subscribed, t.media_type,
               SUM(a.removed_at IS NULL) AS active,
               SUM(a.removed_at IS NULL AND a.verified = 1) AS ok,
               SUM(a.removed_at IS NULL AND a.verified = 0) AS mismatch,
               SUM(a.removed_at IS NULL AND a.verified IS NULL) AS unchecked
        FROM availability a JOIN titles t ON t.id = a.title_id JOIN services s ON s.key = a.service
        GROUP BY s.key, t.media_type ORDER BY s.subscribed DESC, s.name, t.media_type
        """
    ).fetchall()
    if not rows:
        print("  (noch kein Snapshot)")
    for r in rows:
        abo = " [Abo]" if r["subscribed"] else ""
        print(f"  {r['name']}{abo} – {MEDIA_LABEL[r['media_type']]}: {r['active']} "
              f"(bestätigt {r['ok']}, Widerspruch {r['mismatch']}, ungeprüft {r['unchecked']})")
    t = conn.execute("SELECT COUNT(*), SUM(details_fetched_at IS NOT NULL), "
                     "SUM(offers_fetched_at IS NOT NULL) FROM titles").fetchone()
    print(f"  Titel gesamt: {t[0]}, mit Details: {t[1] or 0}, mit Angebotsdaten: {t[2] or 0}")

    print("\n== Letzte Läufe ==")
    for r in conn.execute("SELECT * FROM snapshot_runs ORDER BY run_id DESC LIMIT ?", (args.runs,)):
        print(f"  #{r['run_id']} {r['started_at'][:16]} {names.get(r['service'], r['service'])} – "
              f"{MEDIA_LABEL[r['media_type']]}: {r['status']}, {r['title_count']} Titel, "
              f"+{r['added_count']} / -{r['removed_count']}"
              + (f" – {r['message']}" if r["message"] else ""))

    print(f"\n== Ereignisse der letzten {args.days} Tage ==")
    events = conn.execute(
        """
        SELECT e.event_date, e.event, e.service, e.season_number, t.title, t.year, t.media_type,
               t.genres, t.vote_average, a.verified
        FROM events e
        JOIN titles t ON t.id = e.title_id
        LEFT JOIN availability a ON a.title_id = e.title_id AND a.service = e.service
        WHERE e.event_date >= date('now', 'localtime', ?)
        ORDER BY e.event_date DESC, e.event, t.popularity DESC
        """,
        (f"-{args.days} days",),
    ).fetchall()
    if not events:
        print("  (keine – nach dem ersten Lauf erscheinen hier Neuzugänge ab dem nächsten Tag)")
    symbols = {"added": "+ neu", "readded": "↺ wieder da", "removed": "− weg",
               "new_season": "★ Staffel"}
    for e in events:
        genres = ", ".join(json.loads(e["genres"])[:3])
        what = symbols[e["event"]]
        if e["event"] == "new_season":
            what += f" {e['season_number']}"
        where = names.get(e["service"], "") if e["service"] else ""
        flag = "  ⚠ nicht bestätigt" if e["event"] in ("added", "readded") and e["verified"] == 0 else ""
        kind = "Serie" if e["media_type"] == "tv" else "Film"
        print(f"  {e['event_date']} {what:<12} {e['title']} ({e['year'] or '?'}, {kind}) "
              f"{where} – {genres} – ★ {e['vote_average'] or 0:.1f}{flag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moviebot", description="Film- und Serien-Empfehlungen")
    parser.add_argument("--config", type=Path, help="Pfad zur config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="SQLite-Schema anlegen")

    p = sub.add_parser("providers", help="Anbieter-IDs der Region auflisten und Konfiguration prüfen")
    p.add_argument("--search", help="Filter auf den Namen, z. B. 'prime' oder 'wow'")

    p = sub.add_parser("snapshot", help="Kataloge holen, mit gestern vergleichen, Details laden")
    p.add_argument("--service", action="append", help="nur dieser Dienst (mehrfach möglich)")
    p.add_argument("--skip-details", action="store_true", help="keine Detailabfragen")
    p.add_argument("--details-limit", type=int, help="max. Anzahl Detailabfragen in diesem Lauf")
    p.add_argument("--backfill", "--backfill-offers", dest="backfill", type=int, default=0,
                   metavar="N", help="zusätzlich Angebote und Altersfreigaben für bis zu N "
                                     "ältere Titel nachladen")

    p = sub.add_parser("abo", help="Meine Abos anzeigen oder ändern, z. B. 'abo disney an'")
    p.add_argument("service", nargs="?")
    p.add_argument("state", nargs="?", choices=["an", "aus"])

    p = sub.add_parser("serve", help="REST-Server starten")
    p.add_argument("--host", default="127.0.0.1",
                   help="127.0.0.1 = nur dieser Rechner, 0.0.0.0 = ganzes Heimnetz")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--no-schedule", action="store_true", help="keinen täglichen Abgleich im Server")

    p = sub.add_parser("status", help="Katalogstand, letzte Läufe und Ereignisse anzeigen")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--runs", type=int, default=10)

    args = parser.parse_args(argv)
    if args.command == "abo" and bool(args.service) != bool(args.state):
        parser.error("abo: Dienst und an/aus angeben, oder beides weglassen")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        "init-db": cmd_init_db, "providers": cmd_providers, "snapshot": cmd_snapshot,
        "abo": cmd_abo, "serve": cmd_serve, "status": cmd_status,
    }
    try:
        cfg = load_config(args.config)
        return handlers[args.command](cfg, args)
    except (TMDBError, ValueError, SchemaMismatch) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
