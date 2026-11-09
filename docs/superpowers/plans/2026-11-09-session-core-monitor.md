# 세션 코어 모니터 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이 Mac의 살아 있는 Claude Code 세션들을 로컬 파일에서 읽어, 승인된 시안(core-v3)의 파티클 코어 화면에 실시간으로 표시한다.

**Architecture:** 순수 함수 모듈 `session_state.py`가 레지스트리 JSON과 트랜스크립트 꼬리를 파싱·판정한다. `sessions_server.py`가 stdlib `http.server`로 `/api/sessions`, `/api/session/<id>`, 정적 `sessions.html`을 서빙한다. `sessions.html`은 시안 `core-v3.html`을 가져와 가짜 데이터와 컨트롤을 걷어내고 3초 폴링으로 교체한다.

**Tech Stack:** Python 3 stdlib(http.server, json, glob, subprocess), unittest, three.js 0.160.0 (cdnjs).

**Spec:** `docs/superpowers/specs/2026-11-09-session-core-monitor-design.md`

## Global Constraints

- 외부 의존성 없음. Python stdlib만, 프런트는 three.js CDN 한 개.
- `~/.claude` 아래 어떤 파일도 쓰지 않는다. 읽기 전용.
- 트랜스크립트는 끝 **262144바이트**만 읽는다.
- 상태 색: working `#5ab0ff`, waiting `#ffb15a`, idle `#59627a`, error `#ff5a6a`.
- 카드 고정 배치, 작업 중 모션은 느린 회전 + 3% 숨결만. 시안의 세션 수 버튼·무작위 상태 타이머는 제거.
- 이 폴더는 git 저장소가 아니다. 커밋 단계 없음. 각 태스크 끝에 테스트 통과를 확인한다.
- 테스트 실행: `cd ~/개발환경/ControlTower && python3 -m unittest -v`

## Review Focus

1. 트랜스크립트 꼬리가 JSON 줄 중간에서 잘릴 때: 첫 불완전 줄은 버리고 나머지를 파싱해야 한다. (Task 1 테스트 `test_tail_skips_partial_first_line`)
2. `user.message.content`가 문자열인 줄과 리스트인 줄이 섞여 있을 때 둘 다 처리해야 한다. (Task 1 `test_user_content_string_and_list`)
3. `ps`가 pid를 못 찾거나 `procStart` 형식이 다를 때 세션을 죽은 것으로 보되 예외를 내면 안 된다. (Task 3 `test_alive_false_when_ps_fails`)
4. 레지스트리 JSON이 깨졌거나 `sessionId`가 없을 때 그 파일만 건너뛰어야 한다. (Task 3 `test_broken_registry_file_skipped`)
5. 세션이 0개일 때 API는 빈 배열을 주고 화면은 무채색 코어 + "세션 없음"을 보여야 한다. (Task 4 `test_api_empty`, Task 5 수동 확인)

---

### Task 1: 트랜스크립트 꼬리 파싱

**Files:**
- Create: `session_state.py`
- Test: `tests/test_session_state.py`, `tests/__init__.py` (빈 파일)

**Interfaces:**
- Produces: `read_tail(path: str, max_bytes: int = 262144) -> list[dict]` — 끝 max_bytes를 읽고 첫 불완전 줄을 버린 뒤 JSON 줄들을 리스트로.
- Produces: `summarize(entries: list[dict], now_ms: int) -> dict` 반환 키:
  `last_stop_reason: str|None`, `last_text: str` (마지막 assistant text 첫 80자), `last_tool: str|None` (`"Bash · python tower.py scan"` 형식), `error_pending: bool` (마지막 assistant text 이후 `tool_result.is_error` 있음), `tokens: int` (usage.output_tokens 합), `tools: int` (tool_use 수), `model: str|None`, `act: float` (최근 10분 assistant 수 / 30, 0~1), `events: list[dict]` (마지막 40개, `{t: iso, kind: "user"|"ai"|"tool"|"err", text: str}`), `files: list[str]` (Edit/Write/NotebookEdit의 file_path, 중복 제거, 최대 8, 최신 우선).
- 도구 입력 요약 규칙: Bash→`input.command` 첫 60자, Edit/Write/Read/NotebookEdit→`input.file_path`의 basename, 그 외→도구명만.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_session_state.py`

```python
import json, unittest, tempfile, os
from session_state import read_tail, summarize

def line(**d): return json.dumps(d, ensure_ascii=False)
def asst(ts, content, stop="end_turn", out=10, model="claude-fable-5-1"):
    return dict(type="assistant", timestamp=ts, message=dict(role="assistant", model=model, stop_reason=stop, usage=dict(output_tokens=out), content=content))
def user(ts, content):
    return dict(type="user", timestamp=ts, message=dict(role="user", content=content))

NOW = 1794231600000  # 2026-11-09T13:40:00Z

class TailTests(unittest.TestCase):
    def test_tail_skips_partial_first_line(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as f:
            f.write("x"*3000 + "\n" + line(type="a") + "\n" + line(type="b") + "\n"); p = f.name
        self.assertEqual([e["type"] for e in read_tail(p, max_bytes=100)], ["b"])
        os.unlink(p)

class SummarizeTests(unittest.TestCase):
    def test_user_content_string_and_list(self):
        s = summarize([user("2026-11-09T13:39:00Z", "안녕"), user("2026-11-09T13:39:01Z", [dict(type="tool_result", is_error=False, content="ok")])], NOW)
        self.assertEqual([e["kind"] for e in s["events"]], ["user"])
    def test_tool_summary_and_counts(self):
        s = summarize([asst("2026-11-09T13:39:00Z", [dict(type="tool_use", name="Bash", input=dict(command="python tower.py scan"))], stop="tool_use", out=5),
                       asst("2026-11-09T13:39:30Z", [dict(type="tool_use", name="Edit", input=dict(file_path="/a/b/tower.py"))], stop="tool_use", out=7)], NOW)
        self.assertEqual(s["last_tool"], "Edit · tower.py"); self.assertEqual(s["tokens"], 12); self.assertEqual(s["tools"], 2)
        self.assertEqual(s["files"], ["/a/b/tower.py"]); self.assertEqual(s["model"], "claude-fable-5-1")
        self.assertAlmostEqual(s["act"], 2/30)
    def test_error_pending_true_without_following_text(self):
        s = summarize([asst("2026-11-09T13:39:00Z", [dict(type="text", text="시작")]),
                       user("2026-11-09T13:39:01Z", [dict(type="tool_result", is_error=True, content="boom")])], NOW)
        self.assertTrue(s["error_pending"]); self.assertEqual(s["events"][-1]["kind"], "err")
    def test_error_pending_false_after_text(self):
        s = summarize([user("2026-11-09T13:39:01Z", [dict(type="tool_result", is_error=True, content="boom")]),
                       asst("2026-11-09T13:39:05Z", [dict(type="text", text="복구했습니다")])], NOW)
        self.assertFalse(s["error_pending"]); self.assertEqual(s["last_text"], "복구했습니다"); self.assertEqual(s["last_stop_reason"], "end_turn")
    def test_act_ignores_old_messages(self):
        s = summarize([asst("2026-11-09T12:00:00Z", [dict(type="text", text="old")])], NOW)
        self.assertEqual(s["act"], 0.0)
    def test_empty(self):
        s = summarize([], NOW)
        self.assertIsNone(s["last_stop_reason"]); self.assertEqual(s["tokens"], 0); self.assertEqual(s["events"], [])
```

- [ ] **Step 2: 실패 확인** — Run: `python3 -m unittest tests.test_session_state -v` → Expected: `ModuleNotFoundError: session_state`

- [ ] **Step 3: `session_state.py`에 `read_tail`, `summarize` 구현** — timestamp는 ISO 문자열을 `datetime.fromisoformat(ts.replace("Z","+00:00"))`로 ms 변환. `thinking` 블록은 무시. events의 `text`는 80자 컷.

- [ ] **Step 4: 통과 확인** — Run: 같은 명령 → Expected: 7 tests OK

---

### Task 2: 상태 판정

**Files:**
- Modify: `session_state.py`
- Test: `tests/test_session_state.py` (클래스 추가)

**Interfaces:**
- Produces: `classify(status: str, alive: bool, status_updated_at_ms: int, summary: dict, now_ms: int) -> str` — `"error"|"working"|"waiting"|"idle"`. 규칙은 스펙 표 그대로. WAITING_WINDOW_MS = 30·60·1000.
- Produces: `now_line(state: str, summary: dict) -> str` — working이고 last_tool 있으면 last_tool, 아니면 last_text, 비어 있으면 `"시작 중"`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from session_state import classify, now_line
S = lambda **k: dict(dict(last_stop_reason=None, last_text="", last_tool=None, error_pending=False), **k)
class ClassifyTests(unittest.TestCase):
    def test_dead_is_error(self): self.assertEqual(classify("idle", False, NOW, S(), NOW), "error")
    def test_busy_with_tool_error_is_working(self): self.assertEqual(classify("busy", True, NOW, S(error_pending=True), NOW), "working")
    def test_idle_with_tool_error_is_error(self): self.assertEqual(classify("idle", True, NOW, S(error_pending=True), NOW), "error")
    def test_idle_recent_end_turn_is_waiting(self): self.assertEqual(classify("idle", True, NOW-5*60000, S(last_stop_reason="end_turn"), NOW), "waiting")
    def test_idle_old_is_idle(self): self.assertEqual(classify("idle", True, NOW-31*60000, S(last_stop_reason="end_turn"), NOW), "idle")
    def test_idle_no_transcript_is_idle(self): self.assertEqual(classify("idle", True, NOW, S(), NOW), "idle")
    def test_now_line(self):
        self.assertEqual(now_line("working", S(last_tool="Bash · ls", last_text="x")), "Bash · ls")
        self.assertEqual(now_line("waiting", S(last_tool="Bash · ls", last_text="진행할까요?")), "진행할까요?")
        self.assertEqual(now_line("idle", S()), "시작 중")
```

- [ ] **Step 2: 실패 확인** — Expected: `ImportError: cannot import name 'classify'`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — Expected: 14 tests OK

---

### Task 3: 레지스트리 로딩과 생존 판정

**Files:**
- Modify: `session_state.py`
- Test: `tests/test_session_state.py`

**Interfaces:**
- Produces: `is_alive(pid: int, proc_start: str, run=subprocess.run) -> bool` — `os.kill(pid,0)` 성공 **그리고** `run(["ps","-o","lstart=","-p",str(pid)])`의 stdout을 `datetime.strptime(s.strip(), "%a %b %d %H:%M:%S %Y")`로 파싱해 레지스트리 `procStart`(같은 포맷, 공백 2개 가능하므로 `" ".join(s.split())`로 정규화)와 분 단위까지 같으면 True. 어떤 예외든 False.
- Produces: `load_registry(sessions_dir: str) -> list[dict]` — `*.json`을 읽어 `sessionId`, `pid`, `cwd`, `status` 있는 것만. 깨진 파일 건너뜀.
- Produces: `find_transcript(projects_dir: str, session_id: str) -> str|None` — `glob(projects_dir + "/*/" + session_id + ".jsonl")` 첫 결과.
- Produces: `class DeadTracker` — `__init__(initial_dead: set[str])`, `observe(session_id: str, alive: bool, now_ms: int) -> bool` (표시해야 하면 True: 살아 있거나, 죽은 지 5분 이내이며 initial_dead에 없음). GRACE_MS = 5·60·1000.
- Produces: `build_session(reg: dict, transcript_path: str|None, alive: bool, now_ms: int, project_label: str) -> dict` — 스펙의 JSON 스키마 한 항목. `id, pid, name, project, cwd, state, alive, act, startedAt, stateSince(=statusUpdatedAt), now, tokens, model, tools`. 죽은 세션은 `name` 그대로, `now = "끊김"`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from session_state import is_alive, load_registry, DeadTracker, build_session
class AliveTests(unittest.TestCase):
    def test_alive_false_when_ps_fails(self):
        def run(*a, **k): raise OSError("no ps")
        self.assertFalse(is_alive(os.getpid(), "Mon Nov  9 13:13:44 2026", run=run))
    def test_alive_true_when_start_matches(self):
        class R: stdout = "Mon Nov  9 13:13:50 2026\n"
        self.assertTrue(is_alive(os.getpid(), "Mon Nov  9 13:13:44 2026", run=lambda *a, **k: R()))
    def test_alive_false_when_start_differs(self):
        class R: stdout = "Mon Nov  9 14:13:50 2026\n"
        self.assertFalse(is_alive(os.getpid(), "Mon Nov  9 13:13:44 2026", run=lambda *a, **k: R()))
class RegistryTests(unittest.TestCase):
    def test_broken_registry_file_skipped(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "1.json"), "w").write("{not json")
        open(os.path.join(d, "2.json"), "w").write(json.dumps(dict(pid=2, sessionId="s2", cwd="/x/Proj", status="idle", statusUpdatedAt=NOW, startedAt=NOW, name="n")))
        open(os.path.join(d, "3.json"), "w").write(json.dumps(dict(pid=3)))
        self.assertEqual([r["sessionId"] for r in load_registry(d)], ["s2"])
class DeadTrackerTests(unittest.TestCase):
    def test_initially_dead_hidden(self):
        self.assertFalse(DeadTracker({"a"}).observe("a", False, NOW))
    def test_newly_dead_shown_for_five_minutes(self):
        t = DeadTracker(set())
        self.assertTrue(t.observe("b", True, NOW)); self.assertTrue(t.observe("b", False, NOW+1000))
        self.assertTrue(t.observe("b", False, NOW+4*60000)); self.assertFalse(t.observe("b", False, NOW+6*60000))
class BuildTests(unittest.TestCase):
    def test_build_without_transcript(self):
        reg = dict(pid=1, sessionId="s", cwd="/Users/me/개발환경/ControlTower", status="busy", statusUpdatedAt=NOW, startedAt=NOW-60000, name="작업")
        s = build_session(reg, None, True, NOW, "ControlTower")
        self.assertEqual((s["state"], s["now"], s["tokens"], s["model"], s["project"]), ("working", "시작 중", 0, None, "ControlTower"))
    def test_build_dead(self):
        reg = dict(pid=1, sessionId="s", cwd="/x/P", status="idle", statusUpdatedAt=NOW, startedAt=NOW, name="n")
        s = build_session(reg, None, False, NOW, "P")
        self.assertEqual((s["state"], s["now"], s["alive"]), ("error", "끊김", False))
```

- [ ] **Step 2: 실패 확인** — Expected: ImportError
- [ ] **Step 3: 구현** — `build_session`은 transcript_path가 있으면 `summarize(read_tail(path), now_ms)` 호출.
- [ ] **Step 4: 통과 확인** — Expected: 22 tests OK

---

### Task 4: HTTP 서버

**Files:**
- Create: `sessions_server.py`
- Test: `tests/test_sessions_server.py`

**Interfaces:**
- Consumes: Task 3의 `load_registry`, `find_transcript`, `is_alive`, `DeadTracker`, `build_session`; Task 1의 `read_tail`, `summarize`.
- Produces: `class Collector` — `__init__(sessions_dir, projects_dir, meta_path, account, ttl_ms=3000)`; `snapshot(now_ms) -> dict` (`{now, account, sessions}`; ttl 내 재호출은 캐시); `detail(session_id, now_ms) -> dict|None` (`{id, events, files, tokens, tools, model}`). 첫 `snapshot` 호출 때 죽어 있는 세션 id 집합으로 `DeadTracker`를 만든다. `project_label`은 `projects.meta.json`의 `[basename(cwd)]["label"]`, 없으면 basename.
- Produces: `make_handler(collector, html_path) -> type` — `BaseHTTPRequestHandler` 서브클래스. 라우트: `/` → html, `/api/sessions`, `/api/session/<id>` (없으면 404). JSON은 `ensure_ascii=False`, `Content-Type: application/json; charset=utf-8`, `Cache-Control: no-store`.
- Produces: CLI `python3 sessions_server.py [--port 8787] [--account LABEL]`; 기본 account = `getpass.getuser()`. 시작 시 `Serving on http://localhost:<port>` 출력.

- [ ] **Step 1: 실패하는 테스트 작성** — 빈 임시 sessions_dir로 Collector를 만들고, `http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)`를 스레드로 띄워 `urllib.request`로 호출.

```python
import json, os, tempfile, threading, unittest, urllib.request
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
    def test_detail_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm: self.get("/api/session/nope")
        self.assertEqual(cm.exception.code, 404)
```

- [ ] **Step 2: 실패 확인** — Expected: ModuleNotFoundError
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — Run: `python3 -m unittest -v` → Expected: 25 tests OK
- [ ] **Step 5: 실데이터 스모크** — Run: `python3 sessions_server.py --port 8787 &` 그리고 `curl -s localhost:8787/api/sessions | python3 -m json.tool | head -40` → Expected: 현재 살아 있는 세션들이 `state`와 `now`를 갖고 나열됨. 끝나면 서버 종료.

---

### Task 5: 프런트엔드 `sessions.html`

**Files:**
- Replace: `sessions.html` (기존 "Relay" 시안 삭제 후 새로 작성)
- Source: `.superpowers/brainstorm/45139-1794230276/content/core-v3.html`

**Interfaces:**
- Consumes: `GET /api/sessions` → `{now, account, sessions[]}`; `GET /api/session/<id>` → `{events[], files[], tokens, tools, model}` (Task 4 스키마).

- [ ] **Step 1: 시안 복사** — `core-v3.html`을 `sessions.html`로 복사. `<h2>`·`.subtitle`·`.body` 설명 블록과 `.wrap` 패딩을 제거하고 `.stage`를 `position:fixed; inset:0`으로 전체 화면화. `<title>`은 `세션 코어`.
- [ ] **Step 2: 가짜 데이터 제거** — `POOL`, `TL`, `FILES`, `.ctl` 버튼 블록, `setInterval(... NEXT ...)` 상태 변화 타이머, `build(6)` 호출을 삭제.
- [ ] **Step 3: 데이터 계층 작성** — 전역 `SESS=[]`. `function applySnapshot(snap)`: 이전 `SESS`를 id로 맵핑해 두고, 새 배열로 교체. 새 id는 카드 생성, 사라진 id는 카드·선 제거, `state`가 바뀐 id는 `paint(i)` + `pulse(i)` + `logFeed(s, from, to)` + `flash`. 모든 카드 `paint`. `owner` 재배정은 세션 집합이나 `act` 합이 바뀔 때만. 폴링: `setInterval(fetchSnapshot, 3000)`, 실패 시 하단 `.clock`에 `연결 끊김 · 재시도 중` 덧붙임.
- [ ] **Step 4: 필드 매핑** — 카드: `name`, `now`, 경과 = `now_ms - startedAt`을 `HH:MM`, 토큰 = `tokens`를 `k` 단위(`128k`), 바 = `act`. 상태 라벨 `STN`에 `error: '오류'`는 유지하되 `alive === false`면 `'끊김'`. 하단 시계 줄: `시각 · N SESSIONS · account`.
- [ ] **Step 5: 상세 패널** — `select(i)`에서 `fetch('/api/session/'+id)` 후 채움. KPI: 경과, `토큰(최근)`, 도구 호출, 모델(`claude-fable-5-1` → `Fable 5.1` 식으로 `claude-` 접두어 제거 후 첫 글자 대문자, 하이픈을 공백·점으로). 타임라인은 `events` 그대로(`kind` → 기존 `.ev.user/.ai/.tool/.err` 클래스, `t`는 `HH:MM`). 변경 파일은 `files`의 basename.
- [ ] **Step 6: 빈 상태** — `SESS.length === 0`이면 코어 소속을 전부 idle로 그리고 화면 중앙 아래에 `.corelabel`로 `세션 없음`.
- [ ] **Step 7: 확인** — 서버를 띄우고 `http://localhost:8787` 열기. 콘솔 에러 없음, 현재 살아 있는 세션 수만큼 카드, 이 세션(ControlTower, busy)이 `작업 중` 파랑으로 표시됨을 확인.

---

### Task 6: 라이브 검증

- [ ] **Step 1: 상태 전이 관찰** — 다른 세션에 메시지를 보내거나 이 세션이 응답을 마치는 순간을 기다려 카드가 `작업 중 → 응답 대기`로 바뀌며 파동·섬광·피드가 동작하는지 확인.
- [ ] **Step 2: 상세 패널** — 카드 클릭 → 실제 최근 흐름·변경 파일이 뜨는지 확인.
- [ ] **Step 3: 모션 점검** — 1분간 화면을 보며 "산만하지 않음" 기준(불꽃 없음, 카드 고정)을 확인. 스크린샷을 남긴다.
- [ ] **Step 4: 전체 테스트** — `python3 -m unittest -v` → 25 tests OK.
