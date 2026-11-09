import json, os, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from sessions_server import Collector, make_handler

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); os.makedirs(os.path.join(self.d, "sessions")); os.makedirs(os.path.join(self.d, "projects"))
        html = os.path.join(self.d, "sessions.html"); open(html, "w").write("<title>t</title>")
        c = Collector(os.path.join(self.d, "sessions"), os.path.join(self.d, "projects"), os.path.join(self.d, "none.json"), "acct")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(c, html)); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
    def tearDown(self): self.srv.shutdown()
    def get(self, p):
        r = urllib.request.urlopen(self.base + p); return r.status, r.headers.get("Content-Type"), r.read().decode()
    def test_api_empty(self):
        st, ct, body = self.get("/api/sessions"); d = json.loads(body)
        self.assertEqual(st, 200); self.assertIn("application/json", ct); self.assertEqual(d["sessions"], []); self.assertEqual(d["account"], "acct")
    def test_index_html(self):
        st, ct, body = self.get("/"); self.assertEqual(st, 200); self.assertIn("text/html", ct); self.assertIn("<title>", body)
    def test_detail_500_on_parse_failure(self):
        from sessions_server import Collector
        import sessions_server
        class Boom(Collector):
            def detail(self, sid, now=None): raise RuntimeError("boom")
        html = os.path.join(self.d, "sessions.html")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Boom(os.path.join(self.d, "sessions"), os.path.join(self.d, "projects"), "none", "a"), html))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/session/x")
            self.assertEqual(cm.exception.code, 500)
        finally: srv.shutdown()
    def test_foreign_host_rejected(self):
        req = urllib.request.Request(self.base + "/api/sessions", headers={"Host": "evil.example.com"})
        with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)
    def test_detail_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm: self.get("/api/session/nope")
        self.assertEqual(cm.exception.code, 404)

class EnvTests(unittest.TestCase):
    def test_load_env_reads_skills_plugins_agents_settings(self):
        from sessions_server import load_env
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "skills", "unslop")); open(os.path.join(d, "skills", "unslop", "SKILL.md"), "w").write("---\nname: unslop\ndescription: Turns vibecoded UI into designed UI\n---\n")
        os.makedirs(os.path.join(d, "skills", "broken"))
        os.makedirs(os.path.join(d, "agents")); open(os.path.join(d, "agents", "paper-writer.md"), "w").write("---\nname: paper-writer\n---\n")
        os.makedirs(os.path.join(d, "plugins")); open(os.path.join(d, "plugins", "installed_plugins.json"), "w").write(json.dumps(dict(version=2, plugins={"superpowers@superpowers-marketplace": [dict(version="7.0.0", scope="user")], "old@mkt": [dict(version="1.0", scope="user")]})))
        open(os.path.join(d, "settings.json"), "w").write(json.dumps(dict(model="fable[1m]", effortLevel="medium", enabledPlugins={"superpowers@superpowers-marketplace": True, "old@mkt": False})))
        env = load_env(d)
        self.assertEqual(env["skills"], [dict(name="unslop", description="Turns vibecoded UI into designed UI")])
        self.assertEqual(env["agents"], ["paper-writer"])
        self.assertEqual(env["plugins"], [dict(name="superpowers", marketplace="superpowers-marketplace", version="7.0.0", enabled=True), dict(name="old", marketplace="mkt", version="1.0", enabled=False)])
        self.assertEqual((env["model"], env["effort"]), ("fable[1m]", "medium"))
    def test_load_env_missing_dir(self):
        from sessions_server import load_env
        env = load_env(os.path.join(tempfile.mkdtemp(), "nope"))
        self.assertEqual((env["skills"], env["plugins"], env["agents"], env["model"]), ([], [], [], None))

class EnvApiTests(ApiTests):
    def test_api_env(self):
        st, ct, body = self.get("/api/env"); d = json.loads(body)
        self.assertEqual(st, 200); self.assertIn("skills", d); self.assertIn("plugins", d)

class TotalsTests(unittest.TestCase):
    def test_totals_aggregate(self):
        from sessions_server import totals
        day = 1794231600000  # 2026-11-09 UTC
        sessions = [dict(startedAt=day - 60000, tokens=100, tool_counts={"Bash": 3, "Edit": 1}, sidechain_msgs=2, state="working"),
                    dict(startedAt=day - 3 * 86400000, tokens=50, tool_counts={"Bash": 1, "Read": 4}, sidechain_msgs=0, state="idle")]
        t = totals(sessions, day)
        self.assertEqual(t["sessions_today"], 1); self.assertEqual(t["tokens"], 150); self.assertEqual(t["sidechains"], 2)
        self.assertEqual(t["top_tools"], [["Bash", 4], ["Read", 4], ["Edit", 1]])
