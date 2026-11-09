#!/usr/bin/env python3
"""세션 코어 모니터 서버.

  python3 sessions_server.py [--port 8787] [--account LABEL]

  GET /                   → sessions.html
  GET /api/sessions       → {now, account, sessions:[...]}
  GET /api/session/<id>   → {id, events, files, tokens, tools, model}

stdlib만 사용. ~/.claude 아래는 읽기만 한다.
"""
import argparse
import glob
import pwd
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from session_state import (DeadTracker, build_session, find_transcript, is_alive,
                           load_registry, read_tail, summarize)

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.expanduser("~/.claude")


def now_ms():
    return int(time.time() * 1000)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _frontmatter(path):
    """SKILL.md / agent .md 앞부분의 name·description만 읽는다."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4096)
    except OSError:
        return out
    if not head.startswith("---"):
        return out
    for line in head.split("\n")[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def totals(sessions, now):
    """활성 세션 합계: 오늘 시작한 세션 수, 토큰 합, 서브에이전트 수, 많이 쓴 도구 상위 3개."""
    day_start = now - (now % 86400000)
    counts = {}
    for s in sessions:
        for k, v in (s.get("tool_counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return dict(sessions_today=sum(1 for s in sessions if s.get("startedAt", 0) >= day_start),
                tokens=sum(s.get("tokens", 0) for s in sessions),
                sidechains=sum(s.get("sidechain_msgs", 0) for s in sessions),
                top_tools=[[k, v] for k, v in top])


def load_env(claude_dir):
    """계정 환경: 스킬, 플러그인, 에이전트, 모델/노력 설정. 모두 ~/.claude 아래 읽기 전용."""
    env = dict(skills=[], plugins=[], agents=[], model=None, effort=None)
    for d in sorted(glob.glob(os.path.join(claude_dir, "skills", "*", "SKILL.md"))):
        fm = _frontmatter(d)
        name = fm.get("name") or os.path.basename(os.path.dirname(d))
        env["skills"].append(dict(name=name, description=fm.get("description", "")))
    for d in sorted(glob.glob(os.path.join(claude_dir, "agents", "*.md"))):
        env["agents"].append(_frontmatter(d).get("name") or os.path.splitext(os.path.basename(d))[0])
    settings = _read_json(os.path.join(claude_dir, "settings.json")) or {}
    enabled = settings.get("enabledPlugins") or {}
    installed = (_read_json(os.path.join(claude_dir, "plugins", "installed_plugins.json")) or {}).get("plugins") or {}
    for key, entries in installed.items():
        name, _, mkt = key.partition("@")
        ver = (entries[0].get("version") if isinstance(entries, list) and entries and isinstance(entries[0], dict) else None)
        env["plugins"].append(dict(name=name, marketplace=mkt, version=ver, enabled=bool(enabled.get(key, False))))
    env["model"] = settings.get("model")
    env["effort"] = settings.get("effortLevel")
    return env


class Collector:
    def __init__(self, sessions_dir, projects_dir, meta_path, account, ttl_ms=3000, claude_dir=None):
        self.claude_dir = claude_dir or os.path.dirname(sessions_dir)
        self._env = None
        self._env_at = 0
        self.sessions_dir = sessions_dir
        self.projects_dir = projects_dir
        self.account = account
        self.ttl_ms = ttl_ms
        self.meta = self._load_meta(meta_path)
        self.tracker = None
        self._cache = None
        self._cache_at = 0
        self._lock = threading.Lock()

    @staticmethod
    def _load_meta(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _label(self, cwd):
        base = os.path.basename(cwd.rstrip("/")) or cwd
        entry = self.meta.get(base)
        return entry.get("label", base) if isinstance(entry, dict) else base

    def snapshot(self, now=None):
        now = now or now_ms()
        with self._lock:
            if self._cache and now - self._cache_at < self.ttl_ms:
                return self._cache
            regs = load_registry(self.sessions_dir)
            alive = {r["sessionId"]: is_alive(r["pid"], r.get("procStart", "")) for r in regs}
            if self.tracker is None:
                self.tracker = DeadTracker({sid for sid, a in alive.items() if not a})
            sessions = []
            for r in regs:
                sid = r["sessionId"]
                if not self.tracker.observe(sid, alive[sid], now):
                    continue
                path = find_transcript(self.projects_dir, sid)
                try:
                    sessions.append(build_session(r, path, alive[sid], now, self._label(r["cwd"])))
                except Exception as e:  # 한 세션의 파싱 실패가 전체를 막지 않게
                    print(f"[warn] {sid}: {e}", file=sys.stderr)
            sessions.sort(key=lambda s: s["startedAt"])
            self._cache = dict(now=now, account=self.account, sessions=sessions, totals=totals(sessions, now))
            self._cache_at = now
            return self._cache

    def env(self, now=None):
        now = now or now_ms()
        if self._env is None or now - self._env_at > 30000:
            self._env = load_env(self.claude_dir)
            self._env_at = now
        return self._env

    def detail(self, session_id, now=None):
        now = now or now_ms()
        snap = self.snapshot(now)
        if not any(s["id"] == session_id for s in snap["sessions"]):
            return None
        path = find_transcript(self.projects_dir, session_id)
        summary = summarize(read_tail(path), now) if path else summarize([], now)
        return dict(id=session_id, events=summary["events"], files=summary["files"],
                    tokens=summary["tokens"], tools=summary["tools"], model=summary["model"])


def make_handler(collector, html_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
            return host in ("localhost", "127.0.0.1", "::1", "")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "forbidden host", "text/plain; charset=utf-8")
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html", "/sessions.html"):
                try:
                    with open(html_path, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except OSError:
                    return self._send(404, "sessions.html not found", "text/plain; charset=utf-8")
            if path == "/api/sessions":
                return self._json(200, collector.snapshot())
            if path == "/api/env":
                return self._json(200, collector.env())
            if path.startswith("/api/session/"):
                try:
                    d = collector.detail(path[len("/api/session/"):])
                except Exception as e:
                    print(f"[warn] detail: {e}", file=sys.stderr)
                    return self._json(500, {"error": "detail failed"})
                return self._json(200, d) if d else self._json(404, {"error": "not found"})
            return self._send(404, "not found", "text/plain; charset=utf-8")

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--account", default=pwd.getpwuid(os.getuid()).pw_name)
    a = ap.parse_args(argv)
    collector = Collector(os.path.join(CLAUDE_DIR, "sessions"), os.path.join(CLAUDE_DIR, "projects"),
                          os.path.join(HERE, "projects.meta.json"), a.account, claude_dir=CLAUDE_DIR)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(collector, os.path.join(HERE, "sessions.html")))
    print(f"Serving on http://localhost:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
