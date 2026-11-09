"""세션 코어 모니터 — 레지스트리/트랜스크립트 파싱과 상태 판정 (순수 함수).

읽기 전용. ~/.claude 아래 어떤 파일도 쓰지 않는다.
"""
import calendar
import glob
import json
import os
import subprocess
import time
from datetime import datetime, timezone

TAIL_BYTES = 262144
ACT_WINDOW_MS = 10 * 60 * 1000
ACT_FULL = 30  # 최근 10분에 assistant 메시지 30개면 활동량 1.0
WAITING_WINDOW_MS = 30 * 60 * 1000
GRACE_MS = 5 * 60 * 1000
TEXT_CUT = 80
EVENTS_MAX = 40
FILES_MAX = 8
FILE_TOOLS = {"Edit", "Write", "NotebookEdit", "Read"}


# ---------------------------------------------------------------- 트랜스크립트

def read_tail(path, max_bytes=TAIL_BYTES):
    """파일 끝 max_bytes를 읽어 JSON 줄 리스트로. 첫 줄이 잘렸을 수 있으면 버린다."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read()
            data = data[data.find(b"\n") + 1:]
        else:
            data = f.read()
    out = []
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _ts_ms(ts):
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


def _cut(s, n=TEXT_CUT):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def tool_summary(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash" and inp.get("command"):
        return f"{name} · {_cut(inp['command'], 60)}"
    if name in FILE_TOOLS and inp.get("file_path"):
        return f"{name} · {os.path.basename(inp['file_path'])}"
    return name


def _blocks(content):
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _text_of(blk):
    t = blk.get("text")
    return t.strip() if isinstance(t, str) else ""


def summarize(entries, now_ms):
    s = dict(last_stop_reason=None, last_text="", last_tool=None, error_pending=False,
             tokens=0, tools=0, model=None, act=0.0, events=[], files=[],
             context=0, effort=None, permission=None, branch=None,
             sidechain_msgs=0, sidechain_active=False, tool_counts={})
    recent = 0
    side_ids = set()
    last_side_ms = 0
    files = []
    seen_ids = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        ts = e.get("timestamp", "")
        content = m.get("content")
        side = bool(e.get("isSidechain"))
        if e.get("gitBranch") and e["gitBranch"] != "HEAD":
            s["branch"] = e["gitBranch"]
        if t == "assistant" and side:
            mid = m.get("id") or id(e)
            side_ids.add(mid)
            last_side_ms = max(last_side_ms, _ts_ms(ts))
            for blk in _blocks(content):
                if blk.get("type") == "tool_use":
                    s["tools"] += 1
                    name = blk.get("name", "?")
                    s["tool_counts"][name] = s["tool_counts"].get(name, 0) + 1
            continue
        if t == "assistant":
            if e.get("effort"):
                s["effort"] = e["effort"]
            usage = m.get("usage") or {}
            if usage:
                s["context"] = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
            s["last_stop_reason"] = m.get("stop_reason", s["last_stop_reason"])
            s["model"] = m.get("model") or s["model"]
            mid = m.get("id") or id(e)  # 같은 메시지가 블록마다 한 줄씩 기록되므로 id 기준으로 한 번만 집계
            if mid not in seen_ids:
                seen_ids.add(mid)
                s["tokens"] += int((m.get("usage") or {}).get("output_tokens") or 0)
                if now_ms - _ts_ms(ts) <= ACT_WINDOW_MS:
                    recent += 1
            for blk in _blocks(content):
                bt = blk.get("type")
                if bt == "text" and _text_of(blk):
                    s["last_text"] = _cut(_text_of(blk))
                    s["error_pending"] = False
                    s["events"].append(dict(t=ts, kind="ai", text=s["last_text"]))
                elif bt == "tool_use":
                    s["tools"] += 1
                    name = blk.get("name", "?")
                    s["tool_counts"][name] = s["tool_counts"].get(name, 0) + 1
                    s["last_tool"] = tool_summary(name, blk.get("input"))
                    s["events"].append(dict(t=ts, kind="tool", text=s["last_tool"]))
                    inp = blk.get("input") or {}
                    if blk.get("name") in FILE_TOOLS - {"Read"} and isinstance(inp, dict) and inp.get("file_path"):
                        files.append(inp["file_path"])
        elif t == "user":
            if e.get("permissionMode"):
                s["permission"] = e["permissionMode"]
            if e.get("isMeta") or side:
                continue
            if isinstance(content, str):
                if content.strip():
                    s["error_pending"] = False
                    s["events"].append(dict(t=ts, kind="user", text=_cut(content)))
            else:
                for blk in _blocks(content):
                    if blk.get("type") == "text" and _text_of(blk):
                        s["error_pending"] = False
                        s["events"].append(dict(t=ts, kind="user", text=_cut(_text_of(blk))))
                    elif blk.get("type") == "tool_result" and blk.get("is_error"):
                        s["error_pending"] = True
                        body = blk.get("content")
                        if isinstance(body, list):
                            body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                        s["events"].append(dict(t=ts, kind="err", text=_cut(body or "도구 오류")))
    s["act"] = max(0.0, min(1.0, recent / ACT_FULL))
    s["sidechain_msgs"] = len(side_ids)
    s["sidechain_active"] = bool(last_side_ms) and now_ms - last_side_ms <= 2 * 60 * 1000
    s["events"] = s["events"][-EVENTS_MAX:]
    seen, uniq = set(), []
    for p in reversed(files):
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    s["files"] = uniq[:FILES_MAX]
    return s


# ---------------------------------------------------------------- 상태 판정

def classify(status, alive, status_updated_at_ms, summary, now_ms):
    """"error" | "working" | "waiting" | "idle" — 스펙의 표 순서대로."""
    if not alive:
        return "error"
    if status == "busy":
        return "working"
    if summary.get("error_pending"):
        return "error"
    if summary.get("last_stop_reason") == "end_turn" and now_ms - status_updated_at_ms <= WAITING_WINDOW_MS:
        return "waiting"
    return "idle"


def now_line(state, summary):
    if state == "working" and summary.get("last_tool"):
        return summary["last_tool"]
    return summary.get("last_text") or "시작 중"


# ---------------------------------------------------------------- 레지스트리 / 생존

PS_FMT = "%a %b %d %H:%M:%S %Y"


def _parse_lstart(s):
    return datetime.strptime(" ".join(str(s).split()), PS_FMT)


def is_alive(pid, proc_start, run=subprocess.run):
    """pid가 살아 있고, ps가 보고하는 시작 시각이 레지스트리 procStart와 같은 분이면 True."""
    try:
        os.kill(int(pid), 0)
        r = run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=3,
                env={**os.environ, "LC_ALL": "C"})
        a = _parse_lstart(r.stdout)   # ps: 로컬 시각
        b = _parse_lstart(proc_start)  # 레지스트리 procStart: UTC
        if a.replace(second=0) == b.replace(second=0):
            return True
        a_epoch = time.mktime(a.timetuple())
        b_epoch = calendar.timegm(b.timetuple())
        return abs(a_epoch - b_epoch) < 60
    except Exception:
        return False


def load_registry(sessions_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(sessions_dir, "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict) or not all(k in d for k in ("sessionId", "pid", "cwd", "status")):
            continue
        out.append(d)
    return out


def find_transcript(projects_dir, session_id):
    hits = glob.glob(os.path.join(projects_dir, "*", session_id + ".jsonl"))
    return hits[0] if hits else None


class DeadTracker:
    """죽은 세션을 처음 본 시각을 기억해 5분간만 보여준다. 시작 시 이미 죽어 있던 것은 숨긴다."""

    def __init__(self, initial_dead):
        self.initial_dead = set(initial_dead)
        self.first_dead = {}

    def observe(self, session_id, alive, now_ms):
        if alive:
            self.first_dead.pop(session_id, None)
            return True
        if session_id in self.initial_dead:
            return False
        t0 = self.first_dead.setdefault(session_id, now_ms)
        return now_ms - t0 <= GRACE_MS


def build_session(reg, transcript_path, alive, now_ms, project_label):
    summary = summarize(read_tail(transcript_path), now_ms) if transcript_path else summarize([], now_ms)
    status_at = int(reg.get("statusUpdatedAt") or reg.get("updatedAt") or now_ms)
    state = classify(reg.get("status"), alive, status_at, summary, now_ms)
    return dict(
        id=reg["sessionId"], pid=reg["pid"], name=reg.get("name") or os.path.basename(reg["cwd"]),
        project=project_label, cwd=reg["cwd"], state=state, alive=bool(alive),
        act=summary["act"] if alive else 0.0, startedAt=int(reg.get("startedAt") or now_ms), stateSince=status_at,
        now="끊김" if not alive else now_line(state, summary),
        tokens=summary["tokens"], model=summary["model"], tools=summary["tools"],
        context=summary["context"], effort=summary["effort"], permission=summary["permission"], branch=summary["branch"],
        sidechain_msgs=summary["sidechain_msgs"], sidechain_active=summary["sidechain_active"] if alive else False,
        tool_counts=summary["tool_counts"], version=reg.get("version"),
    )
