import unittest

from fastapi.testclient import TestClient

from moviebot.api import create_app
from tests import test_api

KEY = "geheim"
HEADERS = {"Authorization": f"Bearer {KEY}", "Accept": "application/json, text/event-stream"}


class McpTest(unittest.TestCase):
    """The MCP endpoint, spoken to over HTTP like Hermes does – same test data as ApiTest."""

    def setUp(self):
        test_api.ApiTest.setUp(self)  # builds self.cfg and the titles in self.ids
        self.cfg.mcp_token = KEY
        self.client = TestClient(create_app(self.cfg, client_factory=lambda cfg: test_api.FakeTmdb()))
        self.client.__enter__()  # runs the lifespan, which starts the MCP session manager
        self.addCleanup(self.client.__exit__, None, None, None)

    def rpc(self, method: str, params: dict | None = None, headers: dict | None = None):
        return self.client.post("/mcp", headers=HEADERS if headers is None else headers,
                                json={"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": params or {}})

    def call(self, tool: str, **arguments) -> str:
        r = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertFalse(result["isError"], result)
        return result["content"][0]["text"]

    def call_fails(self, tool: str, **arguments) -> str:
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments}).json()["result"]
        self.assertTrue(result["isError"], result)
        return result["content"][0]["text"]

    # --- access ---------------------------------------------------------------------------

    def test_key_is_required(self):
        self.assertEqual(self.rpc("tools/list", headers={}).status_code, 401)
        wrong = {**HEADERS, "Authorization": "Bearer falsch"}
        self.assertEqual(self.rpc("tools/list", headers=wrong).status_code, 401)
        self.assertEqual(self.rpc("tools/list").status_code, 200)

    def test_off_without_key(self):
        self.cfg.mcp_token = None
        client = TestClient(create_app(self.cfg))
        r = client.post("/mcp", json={})
        self.assertEqual(r.status_code, 404)
        self.assertIn("MOVIEBOT_MCP_TOKEN", r.json()["detail"])
        self.assertFalse(client.get("/api/status").json()["mcp"])
        self.assertTrue(self.client.get("/api/status").json()["mcp"])

    def test_handshake_and_tools(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Merkliste", r.json()["result"]["instructions"])
        tools = {t["name"] for t in self.rpc("tools/list").json()["result"]["tools"]}
        self.assertEqual(tools, {"titel_suchen", "tmdb_suchen", "titel_hinzufuegen",
                                 "auf_liste_setzen", "liste_anzeigen"})

    # --- searching ------------------------------------------------------------------------

    def test_search_defaults_to_my_subscriptions(self):
        text = self.call("titel_suchen")
        self.assertTrue(text.startswith("3 Treffer:"), text)
        self.assertIn(f"#{self.ids['action']} Action A (2025, Film) · ★ 7,5 · Action · "
                      "läuft auf Prime Video (Abo) · auf keiner Liste", text)
        self.assertNotIn("Netflix C", text)

    def test_search_by_title_looks_everywhere(self):
        # not subscribed, removed from its service – a targeted search still finds it
        self.assertIn("Netflix C (2025, Film) · ★ 7,0 · Komödie · läuft auf Netflix · auf keiner Liste",
                      self.call("titel_suchen", suchtext="netflix"))
        self.assertIn("Weg F (2025, Film) · ★ 7,0 · Action · läuft gerade nirgends",
                      self.call("titel_suchen", suchtext="weg"))
        self.assertIn("tmdb_suchen", self.call("titel_suchen", suchtext="gibtsnicht"))

    def test_search_filters(self):
        self.assertIn("Serie B", self.call("titel_suchen", typ="serie"))
        self.assertNotIn("Action A", self.call("titel_suchen", typ="serie"))
        self.assertIn("Netflix C", self.call("titel_suchen", dienste=["netflix"]))
        self.assertIn("Frei D", self.call("titel_suchen", dienste=["alle"]))
        # genre names are matched without regard to case
        text = self.call("titel_suchen", genres=["action"])
        self.assertTrue(text.startswith("2 Treffer:"), text)
        self.assertIn("Möglich: ", self.call_fails("titel_suchen", genres=["Westernn"]))
        self.assertIn("Unbekannte Dienste", self.call_fails("titel_suchen", dienste=["disney"]))

    def test_paging(self):
        text = self.call("titel_suchen", anzahl=1, seite=2)
        self.assertTrue(text.startswith("3 Treffer, hier 2–2:"), text)

    # --- lists ----------------------------------------------------------------------------

    def test_watchlist(self):
        action = self.ids["action"]
        self.assertEqual(self.call("auf_liste_setzen", titel_id=action),
                         "Action A (2025) steht jetzt auf „Merkliste“.")
        self.assertEqual(self.call("auf_liste_setzen", titel_id=action),
                         "Action A (2025) stand schon auf „Merkliste“ – nichts geändert.")
        self.assertIn("Action A (2025, Film) · ★ 7,5 · Action · läuft auf Prime Video (Abo) · "
                      "✓ auf Merkliste", self.call("titel_suchen", suchtext="action"))
        text = self.call("liste_anzeigen")
        self.assertTrue(text.startswith("„Merkliste“: 1 Treffer:"), text)
        self.assertTrue(text.endswith("Listen: Merkliste (1)"), text)

    def test_other_lists_stay(self):
        other = self.client.post("/api/lists", json={"name": "Mit Kindern"}).json()["id"]
        action = self.ids["action"]
        self.call("auf_liste_setzen", titel_id=action, liste="mit kindern")
        self.call("auf_liste_setzen", titel_id=action)
        detail = self.client.get(f"/api/titles/{action}").json()
        self.assertEqual(len(detail["in_lists"]), 2)
        self.assertIn(other, detail["in_lists"])
        self.assertIn("✓ auf Merkliste, Mit Kindern", self.call("titel_suchen", suchtext="action"))

    def test_list_shows_titles_without_offer_but_hides_seen(self):
        for key in ("removed", "lucky"):
            self.call("auf_liste_setzen", titel_id=self.ids[key])
        self.client.put(f"/api/titles/{self.ids['lucky']}/state", json={"status": "seen"})
        text = self.call("liste_anzeigen")
        self.assertIn("Weg F", text)
        self.assertNotIn("Zufall G", text)
        self.assertIn("Zufall G (2025, Film) · Drama · läuft auf Prime Video (Abo) · "
                      "✓ auf Merkliste · gesehen", self.call("liste_anzeigen", auch_gesehene=True))

    def test_list_errors(self):
        self.assertIn("Vorhandene Listen: Merkliste",
                      self.call_fails("auf_liste_setzen", titel_id=self.ids["action"], liste="Kino"))
        self.assertIn("keinen Titel #999", self.call_fails("auf_liste_setzen", titel_id=999))
        self.assertTrue(self.call("liste_anzeigen").startswith("„Merkliste“ ist leer"))

    # --- adding from TMDB -----------------------------------------------------------------

    def test_add_from_tmdb_then_remember(self):
        self.assertIn("Film tmdb_id=603 – Matrix (1999) · noch nicht in Moviebot · Neo …",
                      self.call("tmdb_suchen", suchtext="matrix"))
        added = self.call("titel_hinzufuegen", typ="film", tmdb_id=603)
        self.assertRegex(added, r"^Aufgenommen: #\d+ Matrix \(1999, Film\) · Action · "
                                r"läuft auf Prime Video \(Abo\) · auf keiner Liste$")
        title_id = int(added.split("#")[1].split()[0])
        self.call("auf_liste_setzen", titel_id=title_id)
        self.assertIn(f"schon in Moviebot als #{title_id} · ✓ auf Merkliste",
                      self.call("tmdb_suchen", suchtext="matrix"))


if __name__ == "__main__":
    unittest.main()
