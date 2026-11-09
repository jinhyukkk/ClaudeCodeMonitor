import json, unittest, tempfile, os
from session_state import read_tail, summarize

def line(**d): return json.dumps(d, ensure_ascii=False)
def asst(ts, content, stop="end_turn", out=10, model="claude-fable-5-1"):
    return dict(type="assistant", timestamp=ts, message=dict(role="assistant", model=model, stop_reason=stop, usage=dict(output_tokens=out), content=content))
def user(ts, content):
    return dict(type="user", timestamp=ts, message=dict(role="user", content=content))

NOW = 1794231600000  # 2026-11-09T13:40:00Z

def setUpModule():
    import time; os.environ['TZ']='Asia/Seoul'; time.tzset()


class TailTests(unittest.TestCase):
    def test_tail_skips_partial_first_line(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as f:
            f.write("x"*3000 + "\n" + line(type="a") + "\n" + line(type="b") + "\n"); p = f.name
        self.assertEqual([e["type"] for e in read_tail(p, max_bytes=20)], ["b"])
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

class AliveTimezoneTests(unittest.TestCase):
    def test_alive_true_when_ps_local_matches_registry_utc(self):
        import time
        old = os.environ.get("TZ"); os.environ["TZ"] = "Asia/Seoul"; time.tzset()
        try:
            class R: stdout = "Mon Nov  9 22:13:44 2026    \n"
            self.assertTrue(is_alive(os.getpid(), "Mon Nov  9 13:13:44 2026", run=lambda *a, **k: R()))
        finally:
            if old is None: del os.environ["TZ"]
            else: os.environ["TZ"] = old
            time.tzset()


class ReviewFixTests(unittest.TestCase):
    def test_ps_runs_with_c_locale(self):
        seen = {}
        class R: stdout = "Mon Nov  9 22:13:44 2026\n"
        def run(*a, **k): seen.update(k); return R()
        is_alive(os.getpid(), "Mon Nov  9 13:13:44 2026", run=run)
        self.assertEqual(seen.get("env", {}).get("LC_ALL"), "C")
    def test_tokens_and_act_dedupe_by_message_id(self):
        a1 = asst("2026-11-09T13:39:00Z", [dict(type="text", text="a")], out=100); a1["message"]["id"] = "m1"
        a2 = asst("2026-11-09T13:39:00Z", [dict(type="tool_use", name="Read", input=dict(file_path="/x/y.py"))], stop="tool_use", out=100); a2["message"]["id"] = "m1"
        s = summarize([a1, a2], NOW)
        self.assertEqual(s["tokens"], 100); self.assertAlmostEqual(s["act"], 1/30); self.assertEqual(s["tools"], 1)
    def test_user_prompt_clears_error_pending(self):
        s = summarize([user("2026-11-09T13:39:01Z", [dict(type="tool_result", is_error=True, content="denied")]),
                       user("2026-11-09T13:39:05Z", "다시 해줘")], NOW)
        self.assertFalse(s["error_pending"])
    def test_non_dict_blocks_and_null_text_tolerated(self):
        s = summarize([asst("2026-11-09T13:39:00Z", ["weird", dict(type="text", text=None), None]),
                       user("2026-11-09T13:39:01Z", [42, dict(type="text", text=None)])], NOW)
        self.assertEqual(s["events"], [])
    def test_meta_user_lines_skipped(self):
        u = user("2026-11-09T13:39:01Z", "<system-reminder>x</system-reminder>"); u["isMeta"] = True
        self.assertEqual(summarize([u], NOW)["events"], [])

class ExtraInfoTests(unittest.TestCase):
    def test_context_effort_permission_branch(self):
        a = asst("2026-11-09T13:39:00Z", [dict(type="text", text="a")], out=10); a["message"]["usage"].update(input_tokens=32, cache_read_input_tokens=375612, cache_creation_input_tokens=291); a["effort"] = "medium"; a["gitBranch"] = "feat/x"
        u = user("2026-11-09T13:39:01Z", "다음"); u["permissionMode"] = "auto"
        s = summarize([a, u], NOW)
        self.assertEqual(s["context"], 32 + 375612 + 291); self.assertEqual(s["effort"], "medium"); self.assertEqual(s["permission"], "auto"); self.assertEqual(s["branch"], "feat/x")
    def test_sidechain_counts_and_active(self):
        a1 = asst("2026-11-09T13:39:00Z", [dict(type="text", text="main")]); a1["message"]["id"] = "m1"
        a2 = asst("2026-11-09T13:39:30Z", [dict(type="tool_use", name="Read", input={})], stop="tool_use"); a2["isSidechain"] = True; a2["message"]["id"] = "s1"
        a3 = asst("2026-11-09T13:39:40Z", [dict(type="text", text="sub")]); a3["isSidechain"] = True; a3["message"]["id"] = "s1"
        s = summarize([a1, a2, a3], NOW)
        self.assertEqual(s["sidechain_msgs"], 1); self.assertTrue(s["sidechain_active"])
        self.assertEqual(s["last_text"], "main")  # 사이드체인 텍스트는 메인 흐름을 덮지 않는다
        s2 = summarize([a1, a2, a3], NOW + 5 * 60000)
        self.assertFalse(s2["sidechain_active"])
    def test_tool_counts(self):
        s = summarize([asst("2026-11-09T13:39:00Z", [dict(type="tool_use", name="Bash", input={}), dict(type="tool_use", name="Bash", input={}), dict(type="tool_use", name="Edit", input={})], stop="tool_use")], NOW)
        self.assertEqual(s["tool_counts"], {"Bash": 2, "Edit": 1})
    def test_build_session_carries_new_fields(self):
        reg = dict(pid=1, sessionId="s", cwd="/x/P", status="busy", statusUpdatedAt=NOW, startedAt=NOW, name="n", version="2.1.317")
        s = build_session(reg, None, True, NOW, "P")
        for k in ("context", "effort", "permission", "branch", "sidechain_msgs", "sidechain_active", "tool_counts"): self.assertIn(k, s)
        self.assertEqual(s["version"], "2.1.317")
