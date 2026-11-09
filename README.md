# ClaudeCodeMonitor

A local, read-only monitor for the Claude Code sessions running on this Mac.

Each live session appears as a glass card showing its state (working, waiting for you, idle, error), what it is doing right now, elapsed time, tokens, context usage, effort level, permission mode, git branch, and sub-agent activity. Cards are sorted by state, and a card flashes when its state changes. Click a card to see the session's recent turns and changed files.

The top strip shows the account's model, plugins, skills, agents, and today's totals.

## Run

```bash
python3 sessions_server.py
```

Then open http://localhost:8787.

Options:

```bash
python3 sessions_server.py --port 8787 --account "my-label"
```

## How it works

- `sessions_server.py` serves the page and a small JSON API on 127.0.0.1 only. Python standard library, no dependencies.
- `session_state.py` reads `~/.claude/sessions/*.json` and the last 256 KB of each session transcript, then classifies the session state.
- `sessions.html` polls the API every 3 seconds.

Nothing under `~/.claude` is ever written.

| Endpoint | Returns |
|---|---|
| `GET /api/sessions` | live sessions and today's totals |
| `GET /api/session/<id>` | recent events and changed files for one session |
| `GET /api/env` | model, plugins, skills, agents |

`projects.meta.json` maps a project folder name to a display label.

## Test

```bash
python3 -m unittest -v
```

Design notes live in `docs/superpowers/`.
