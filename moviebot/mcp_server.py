"""MCP endpoint (/mcp): lets an AI assistant – e.g. a Hermes agent you talk to via Telegram –
search titles, add titles from TMDB and put them on lists.

The tools answer in short German text, one line per title, because the answers end up in a
chat. Each line starts with the internal id (#123), which the other tools take.

The endpoint only runs when a key is configured (MOVIEBOT_MCP_TOKEN or [mcp] token); every
request must then carry it as `Authorization: Bearer <key>`.
"""

import hmac
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import queries, version
from .config import Config
from .snapshot import import_title
from .tmdb import TMDBError

PATH = "/mcp"

Typ = Literal["film", "serie"]
MEDIA_TYPES = {"film": "movie", "serie": "tv"}
TYP_NAMES = {"movie": "Film", "tv": "Serie"}
Sortierung = Literal["beliebt", "bewertung", "neueste", "neu_im_dienst"]
SORTS = {"beliebt": "popularity", "bewertung": "rating", "neueste": "newest",
         "neu_im_dienst": "added"}

INSTRUCTIONS = """\
Moviebot kennt Filme und Serien bei deutschen Streaming-Diensten und die Listen des Nutzers \
(die Standardliste heißt meist „Merkliste“).
Typischer Ablauf, um etwas zu merken: titel_suchen mit einem kurzen Teil des Titels. \
Nicht gefunden → tmdb_suchen, dann titel_hinzufuegen, dann auf_liste_setzen.
Jeder Treffer zeigt, auf welchen Listen er steht – damit lässt sich auch prüfen, \
ob etwas schon auf der Merkliste ist. Antworte dem Nutzer kurz; er liest am Handy."""


def create_mcp(cfg: Config, client_factory: Callable) -> MCPServer:
    services_hint = ", ".join(f"{s.key} ({s.name})" for s in cfg.services)

    @contextmanager
    def db() -> Iterator[sqlite3.Connection]:
        # like the REST API: one plain connection per call (tools run in worker threads)
        conn = sqlite3.connect(cfg.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    # The SDK logs every single request at INFO ("Terminating session") – too chatty for the
    # container log. Warnings and errors still show.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    server = MCPServer(name="moviebot", title="Moviebot", version=version.info()["version"],
                       instructions=INSTRUCTIONS, log_level="WARNING")
    reading = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(annotations=reading, structured_output=False)
    def titel_suchen(
        suchtext: Annotated[str | None, Field(
            description="Teil des Titels, z. B. 'dune'. Dann wird überall gesucht – auch in "
                        "Gesehenem, Ungelaufenem und Titeln ohne aktuelles Angebot.")] = None,
        typ: Typ | None = None,
        genres: Annotated[list[str] | None, Field(
            description="Mindestens eines davon, z. B. ['Thriller', 'Komödie']")] = None,
        dienste: Annotated[list[str] | None, Field(
            description="'meine' = meine Abos (Standard ohne suchtext), 'alle' oder einzelne: "
                        + services_hint)] = None,
        jahr_von: int | None = None,
        jahr_bis: int | None = None,
        min_bewertung: Annotated[float | None, Field(ge=0, le=10,
                                                     description="TMDB-Bewertung 0–10; Titel mit zu wenigen "
                                                                 "Stimmen (Einstellung) fallen dann weg")] = None,
        neu_seit_tagen: Annotated[int | None, Field(
            ge=1, description="Nur neu im Dienst oder neue Staffel in den letzten N Tagen")] = None,
        sortierung: Sortierung = "beliebt",
        auch_gesehene: bool = False,
        anzahl: Annotated[int, Field(ge=1, le=50)] = 10,
        seite: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Filme und Serien in Moviebot suchen und filtern. Jede Zeile zeigt Dienste,
        Bewertung und die Listen, auf denen der Titel steht – auch zum Prüfen, ob etwas
        schon auf der Merkliste ist."""
        targeted = bool(suchtext and suchtext.strip())
        with db() as conn:
            f = queries.TitleFilter(
                services=_services(dienste) or (["all"] if targeted else None),
                media_type=MEDIA_TYPES[typ] if typ else None,
                genres=_genres(conn, genres),
                year_from=jahr_von, year_to=jahr_bis, min_rating=min_bewertung,
                new_days=neu_seit_tagen, q=suchtext.strip() if targeted else None,
                only_available=False if targeted and not dienste else None,
                show_unreleased=True if targeted else None,
                show_seen=targeted or auch_gesehene, show_not_interested=targeted,
                sort=SORTS[sortierung], page=seite, page_size=anzahl,
            )
            try:
                page = queries.search_titles(conn, f)
            except ValueError as e:
                raise ToolError(str(e))
            if not page["items"]:
                if targeted:
                    return (f"„{suchtext.strip()}“ ist nicht in Moviebot (oder läuft auf keinem der "
                            "gewählten Dienste). Ein kürzerer Suchtext hilft manchmal. Sonst mit "
                            "tmdb_suchen nachsehen und mit titel_hinzufuegen aufnehmen.")
                return "Keine Treffer. Weniger Filter versuchen."
            return _header(page) + "\n" + "\n".join(_lines(conn, page["items"]))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
                 structured_output=False)
    def tmdb_suchen(
        suchtext: Annotated[str, Field(min_length=2, description="Titel oder Teil davon")],
    ) -> str:
        """Bei TMDB suchen – findet auch Titel, die noch nicht in Moviebot sind (z. B. ältere).
        Liefert die TMDB-ID für titel_hinzufuegen und zeigt, was schon in Moviebot ist."""
        try:
            hits = client_factory(cfg).search(suchtext.strip())
        except TMDBError as e:
            raise ToolError(f"TMDB antwortet nicht: {e}")
        with db() as conn:
            results = queries.search_results(conn, hits)[:8]
            if not results:
                return f"TMDB kennt nichts zu „{suchtext.strip()}“."
            known = [r["title_id"] for r in results if r["title_id"]]
            on_lists = queries._lists_for(conn, known)
            names = _list_names(conn)
            lines = []
            for r in results:
                line = (f"{_typ(r['media_type'])} tmdb_id={r['tmdb_id']} – {r['title']} "
                        f"({r['year'] or '?'})")
                if r["title_id"]:
                    line += f" · schon in Moviebot als #{r['title_id']} · " + _on_lists(
                        on_lists.get(r["title_id"], []), names)
                else:
                    line += " · noch nicht in Moviebot"
                if r["overview"]:
                    line += f" · {_shorten(r['overview'], 120)}"
                lines.append(line)
            return "\n".join(lines)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                             idempotent_hint=True, open_world_hint=True),
                 structured_output=False)
    def titel_hinzufuegen(
        typ: Typ,
        tmdb_id: Annotated[int, Field(description="aus tmdb_suchen")],
    ) -> str:
        """Einen Titel von TMDB in Moviebot aufnehmen (holt Details und Streaming-Angebote).
        Danach mit auf_liste_setzen merken."""
        with db() as conn:
            try:
                title_id = import_title(conn, client_factory(cfg), cfg, MEDIA_TYPES[typ], tmdb_id)
            except TMDBError as e:
                raise ToolError(f"TMDB antwortet nicht oder kennt die ID nicht: {e}")
            title = queries.get_title(conn, title_id)
            return "Aufgenommen: " + _lines(conn, [title])[0]

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                             idempotent_hint=True, open_world_hint=False),
                 structured_output=False)
    def auf_liste_setzen(
        titel_id: Annotated[int, Field(description="die #-Nummer aus titel_suchen")],
        liste: Annotated[str | None, Field(
            description="Name der Liste; leer = Standardliste (Merkliste)")] = None,
    ) -> str:
        """Einen Titel auf eine Liste setzen. Andere Listen des Titels bleiben, wie sie sind.
        Meldet, wenn er schon draufstand."""
        with db() as conn:
            target = _find_list(conn, liste)
            title = queries.get_title(conn, titel_id)
            if title is None:
                raise ToolError(f"Es gibt keinen Titel #{titel_id}. Erst mit titel_suchen suchen.")
            added = queries.add_to_list(conn, titel_id, target["id"])
            name = f"{title['title']} ({title['year'] or '?'})"
            if added:
                return f"{name} steht jetzt auf „{target['name']}“."
            return f"{name} stand schon auf „{target['name']}“ – nichts geändert."

    @server.tool(annotations=reading, structured_output=False)
    def liste_anzeigen(
        liste: Annotated[str | None, Field(
            description="Name der Liste; leer = Standardliste (Merkliste)")] = None,
        auch_gesehene: Annotated[bool, Field(
            description="Auch schon Gesehenes zeigen")] = False,
        anzahl: Annotated[int, Field(ge=1, le=100)] = 30,
        seite: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Zeigt, was auf einer Liste steht (zuletzt Hinzugefügtes zuerst), dazu alle Listen."""
        with db() as conn:
            target = _find_list(conn, liste)
            page = queries.search_titles(conn, queries.TitleFilter(
                services=["all"], list_id=target["id"], show_seen=auch_gesehene,
                sort="list_added", page=seite, page_size=anzahl))
            overview = "Listen: " + ", ".join(
                f"{entry['name']} ({entry['count']})" for entry in queries.list_lists(conn))
            if not page["items"]:
                hidden = "" if auch_gesehene else " (Gesehenes ausgeblendet)"
                return f"„{target['name']}“ ist leer{hidden}.\n{overview}"
            return (f"„{target['name']}“: {_header(page)}\n"
                    + "\n".join(_lines(conn, page["items"])) + f"\n{overview}")

    return server


# --- helpers -----------------------------------------------------------------------------

def _services(dienste: list[str] | None) -> list[str] | None:
    if not dienste:
        return None
    aliases = {"meine": queries.MY_SERVICES, "alle": queries.ALL_SERVICES}
    return [aliases.get(d.strip().lower(), d.strip().lower()) for d in dienste]


def _genres(conn: sqlite3.Connection, wanted: list[str] | None) -> list[str]:
    """Match genre names case-insensitively, so a guessed 'thriller' works – and say which names
    exist instead of silently finding nothing."""
    if not wanted:
        return []
    known = {g["name"].lower(): g["name"] for g in queries.list_genres(conn)}
    unknown = [g for g in wanted if g.strip().lower() not in known]
    if unknown:
        raise ToolError(f"Unbekannte Genres: {', '.join(unknown)}. "
                        f"Möglich: {', '.join(known.values())}")
    return [known[g.strip().lower()] for g in wanted]


def _find_list(conn: sqlite3.Connection, name: str | None) -> dict:
    lists = queries.list_lists(conn)
    if not name or not name.strip():
        found = [entry for entry in lists if entry["is_default"]]
    else:
        found = [entry for entry in lists if entry["name"].lower() == name.strip().lower()]
    if not found:
        what = f"die Liste „{name}“" if name else "keine Standardliste"
        raise ToolError(f"Es gibt {what}. Vorhandene Listen: "
                        + (", ".join(entry["name"] for entry in lists) or "keine"))
    return found[0]


def _list_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {entry["id"]: entry["name"] for entry in queries.list_lists(conn)}


def _on_lists(list_ids: list[int], names: dict[int, str]) -> str:
    if not list_ids:
        return "auf keiner Liste"
    return "✓ auf " + ", ".join(names.get(i, f"Liste {i}") for i in list_ids)


def _typ(media_type: str) -> str:
    return TYP_NAMES.get(media_type, media_type)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _header(page: dict) -> str:
    shown = len(page["items"])
    if page["total"] == shown:
        return f"{shown} Treffer:"
    first = (page["page"] - 1) * page["page_size"] + 1
    return f"{page['total']} Treffer, hier {first}–{first + shown - 1}:"


def _lines(conn: sqlite3.Connection, items: list[dict]) -> list[str]:
    subscribed = {s["key"] for s in queries.list_services(conn) if s["subscribed"]}
    names = _list_names(conn)
    return [_line(item, subscribed, names) for item in items]


def _line(item: dict, subscribed: set[str], list_names: dict[int, str]) -> str:
    kind = _typ(item["media_type"])
    if item["media_type"] == "tv" and item.get("number_of_seasons"):
        kind += f", {item['number_of_seasons']} St."
    parts = [f"#{item['id']} {item['title']} ({item['year'] or '?'}, {kind})"]
    if item.get("vote_average") and (item.get("vote_count") or 0) >= 10:
        parts.append(f"★ {item['vote_average']:.1f}".replace(".", ","))
    if item.get("genres"):
        parts.append(", ".join(item["genres"][:3]))
    services = []
    for a in item.get("available_on", []):
        label = a["name"]
        if a["service"] in subscribed:
            label += " (Abo)"
        elif a["free"]:
            label += " (kostenlos)"
        services.append(label)
    parts.append("läuft auf " + ", ".join(services) if services else "läuft gerade nirgends")
    parts.append(_on_lists(item.get("in_lists", []), list_names))
    status = (item.get("user") or {}).get("status")
    if status == "seen":
        parts.append("gesehen")
    elif status == "not_interested":
        parts.append("nicht interessiert")
    return " · ".join(parts)


# --- HTTP --------------------------------------------------------------------------------

class RequireKey:
    """Lets a request through only with `Authorization: Bearer <key>`."""

    def __init__(self, app: ASGIApp, key: str):
        self.app = app
        self.expected = f"Bearer {key}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            given = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                response = JSONResponse({"detail": "Falscher oder fehlender MCP-Schlüssel"},
                                        status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
