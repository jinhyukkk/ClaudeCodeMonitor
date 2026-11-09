# 세션 코어 모니터 (Session Core Monitor) 설계

날짜: 2026-11-09
위치: `~/개발환경/ControlTower`
상태: 시안 A안 v3 승인됨, 실제 데이터 연결 단계

## 의도

이 Mac의 Claude Code 계정에서 돌아가는 세션들을 **관제실 벽 모니터**처럼 보여주는 화면.
글자를 읽기 전에 움직임만 보고 어느 세션이 바쁘고, 어느 세션이 내 응답을 기다리고,
어느 세션이 멈췄는지 알 수 있어야 한다. 세션 목록이나 표가 아니다.

이전 시안(`sessions.html` "Relay" 등)은 모두 폐기한다. 이 문서의 화면이 그 자리를 대신한다.

## 목표 / 비목표 / 실패 조건

**목표**
- 중앙 3D 파티클 코어 하나가 계정 전체 상태를 움직임과 색으로 보여준다.
- 세션마다 유리 카드가 코어 둘레에 고정 배치되고 연결선으로 이어진다.
- 세션 상태가 바뀌면 코어→카드 파동, 카드 섬광, 피드 한 줄로 알린다.
- 카드 클릭 시 상세(최근 흐름, 도구 호출, 변경 파일)가 오른쪽에 펼쳐진다.
- 실제 로컬 세션 데이터를 몇 초 간격으로 읽는다.

**비목표**
- 세션 제어(중지, 메시지 보내기)는 하지 않는다. 읽기 전용.
- 클라우드 세션, 다른 기기의 세션은 다루지 않는다.
- 과거 세션 히스토리 검색은 하지 않는다.

**실패 조건 (기술적으로 동작해도 실패)**
- 작업 중 모션이 산만하다. 불꽃 튀김, 빠른 소용돌이는 금지.
- 카드가 떠다닌다. 카드는 고정.
- 평면 리스트나 숫자 카드 대시보드처럼 보인다.
- 가짜 데이터가 남아 있다.

## 확정된 시각 디자인 (A안 v3)

시안 파일: `.superpowers/brainstorm/45139-1794230276/content/core-v3.html`. 구현은 이 파일의
코어 애니메이션, 카드/연결선 배치, 상세 패널, 파동, 피드, 세션 수 대응 로직을 그대로 가져온다.

- 배경: 미드나이트(#05060c), 중앙 라디얼 글로우, 먼지 파티클.
- 코어: 반지름 1.5의 구형 파티클 2,800개. 각 파티클은 세션 하나에 소속(활동량 비례).
  소속 세션 상태별 움직임:
  - 작업 중: 매우 느린 회전 + 3% 숨결. 밝기 1.05~1.15.
  - 응답 대기: 1.5Hz 팽창·수축(±18%), 밝기 맥동. 따뜻한 호박색.
  - 유휴: 1.35배로 느슨하게 퍼지고 살짝 가라앉음. 밝기 0.45. 무채색.
  - 오류: 미세 떨림(±0.025), 4Hz 약한 깜빡임. 붉은색.
  - 카드 호버 또는 선택 시 코어 전체가 그 세션의 상태로 기운다.
- 색: 작업 중 #5ab0ff, 응답 대기 #ffb15a, 유휴 #59627a, 오류 #ff5a6a.
- 카드: 유리(블러 18px) 214px. 상태 라벨, 이름, 현재 하는 일 한 줄, 경과/토큰, 활동량 바.
  코어 중심 기준 타원(rx≤460, ry≤250)에 균등 배치, 위치 고정.
- 연결선: 카드→코어. 작업 중은 점선이 코어 쪽으로 천천히 흐름(속도 12px/s × 활동량).
- 세션 7개 이상: 유휴 세션은 하단 작은 칩으로, 카드는 0.88배로 축소.
- 상태 변화: 코어에서 카드로 빛 알갱이가 0.9초 이동, 카드 1.4초 섬광, 오른쪽 아래 피드에
  "이름 이전상태 → 새상태" 추가(최근 4줄 유지).
- 상세: 카드 클릭 → 코어가 왼쪽(-2.7 단위)으로 이동, 오른쪽 46% 유리 패널.
  상단 KPI 4개(경과, 토큰, 도구 호출, 모델), 최근 흐름 타임라인, 변경 파일 칩.
  다른 카드는 35% 불투명도. × 또는 같은 카드 재클릭으로 닫힘.
- 상단 왼쪽: 상태별 세션 수 필. 하단 왼쪽: 시계 · N SESSIONS · 계정.
- 카메라: 거의 정지(진폭 0.2, 주기 ~2분).

## 데이터 소스 (조사 결과)

모두 로컬 파일. 외부 네트워크 없음.

1. **세션 레지스트리** `~/.claude/sessions/<pid>.json`
   - 필드: `pid`, `sessionId`, `cwd`, `name`, `status` (`busy` | `idle`), `statusUpdatedAt`(ms),
     `startedAt`(ms), `hostSessionId`, `version`.
   - 프로세스 생존 여부는 `os.kill(pid, 0)`로 확인. 죽은 pid의 파일은 남아 있을 수 있다.
2. **트랜스크립트** `~/.claude/projects/<cwd를 '-'로 인코딩>/<sessionId>.jsonl`
   - 인코딩: cwd의 `/`와 비ASCII 문자가 `-`로 바뀜. 디렉터리 안에서 `<sessionId>.jsonl`을 glob으로 찾는 편이 안전.
   - 한 줄 = 한 JSON. 관심 `type`: `assistant`, `user`, `system`.
   - `assistant.message.content[]`: `text`, `tool_use(name, input)`, `thinking`.
     `assistant.message.stop_reason`: `end_turn` | `tool_use`. `assistant.message.usage.output_tokens`.
   - `user.message.content[]`: 문자열(사용자 입력) 또는 `tool_result(is_error, content)`.
   - 파일은 수 MB. **끝에서 256KB만** 읽어 파싱한다.
3. **프로젝트 메타** `projects.meta.json` (이미 ControlTower에 있음) — cwd → 프로젝트 표시 이름 보조.

## 상태 판정 규칙

레지스트리 + 트랜스크립트 끝부분으로 세션 하나의 상태를 정한다. 위에서부터 먼저 맞는 것.

| 상태 | 조건 |
|---|---|
| `error` | 프로세스가 죽어 있음(레지스트리만 남음) **또는** `status == "idle"` 이고 마지막 턴에서 `tool_result.is_error`가 있고 그 뒤 assistant `text`가 없음 |
| `working` | `status == "busy"` (busy 중의 도구 오류는 에이전트가 스스로 복구하므로 error로 보지 않는다) |
| `waiting` | `status == "idle"` 이고 마지막 assistant `stop_reason == "end_turn"` 이고 `statusUpdatedAt`이 30분 이내 |
| `idle` | 그 외 (`idle`이 30분 초과, 또는 트랜스크립트가 없거나 assistant 메시지가 없음) |

**죽은 프로세스 처리** (빌더 검토 후 설계자 결정)
- 생존 판정: `os.kill(pid, 0)` 성공 **그리고** `ps -o lstart= -p <pid>`가 레지스트리 `procStart`와 같은 분(minute)에 시작했을 것. pid 재사용 방지.
- 서버 시작 시 이미 죽어 있던 세션은 아예 표시하지 않는다.
- 서버가 보는 동안 죽은 세션은 처음 죽은 것을 본 시각을 메모리에 기록하고, 카드에 "끊김"으로 5분간 보여준 뒤 제외한다. 서버 재시작 시 이 기록은 사라져도 된다.
- `kind`/`entrypoint`는 거르지 않는다. 레지스트리에 있는 살아 있는 세션은 전부 카드로 보여준다.

**트랜스크립트가 없거나 비어 있을 때**: 상태는 위 표대로(`busy`면 working, 아니면 idle). "지금 하는 일"은 "시작 중", 모델·도구 호출은 "—", 토큰은 0. 토큰·도구 호출 수는 끝 256KB 범위의 합계라 근사치이며, 카드에는 별도 표시 없이 그대로 쓴다(상세 패널 KPI 라벨만 "토큰(최근)"으로 적는다).

**활동량(act, 0~1)**: 최근 10분간 assistant 메시지 수를 30으로 나눠 0~1로 클램프. 코어 파티클 비율과 카드 바에 쓴다.

**"지금 하는 일" 한 줄**: 마지막 `tool_use`가 있으면 `도구명 · 입력 요약`(예: `Bash · python tower.py scan`, `Edit · tower.py`), 없으면 마지막 assistant `text` 첫 80자. `waiting`이면 마지막 assistant text 첫 80자(질문 내용).

**상세 패널 데이터**: 마지막 40개 이벤트(user text / assistant text / tool_use / tool_result error)를 시각 순으로. 변경 파일은 최근 `Edit`/`Write`/`NotebookEdit` tool_use의 `file_path`를 중복 제거(최대 8개). 토큰은 `usage.output_tokens` 합계(끝 256KB 범위).

## 아키텍처

```
sessions_server.py (Python 3 stdlib only)
  GET /                → sessions.html (정적)
  GET /api/sessions    → JSON { now, account, sessions:[...] }
  GET /api/session/<id>→ JSON 상세(이벤트 40개, 파일)
  3초 캐시. 포트 8787.

sessions.html (단일 파일, three.js 0.160 cdnjs)
  3초마다 /api/sessions 폴링.
  이전 스냅샷과 비교해 상태가 바뀐 세션에 파동/섬광/피드.
  세션 추가·제거 시 카드 재배치(위치 전환 0.7s).
  카드 클릭 시 /api/session/<id> 호출해 상세 채움.
```

JSON 스키마 (`/api/sessions`):
```json
{"now":1794231587847,"account":"me",
 "sessions":[{"id":"586ea9…","pid":44800,"name":"계정 세션 상태 화면","project":"ControlTower",
   "cwd":"/Users/…/ControlTower","state":"working","alive":true,"act":0.8,
   "startedAt":1794230072702,"stateSince":1794231587847,"now":"Bash · ls ~/.claude/sessions",
   "tokens":128400,"model":"claude-fable-5-1","tools":33}]}
```

계정 이름: 로컬 파일에 이메일이 없으므로 macOS 사용자명(`getpass.getuser()`)을 쓴다. `--account <라벨>` 인자로 덮어쓸 수 있다.

실행: `python3 sessions_server.py [--port 8787] [--account 라벨]` 후 `http://localhost:8787`. ControlTower `app/` Swift 래퍼와의 연동은 범위 밖.

## 빌더에게 맡기는 것

- 시안의 세션 수 버튼(3/6/9/12)과 무작위 상태 변화 타이머는 가짜 데이터이므로 제거한다.

- 서버 파일 분할 여부, 테스트 방식(샘플 jsonl 픽스처로 상태 판정 단위 테스트 권장).
- 카드 배치 타원 반경의 세부 수치 조정(단, 고정 배치 원칙 유지).
- 세션이 0개일 때 화면: 코어만 무채색으로 느리게 숨 쉬고 "세션 없음" 한 줄.
- 폴링 실패 시: 마지막 상태 유지, 하단에 "연결 끊김 · 재시도 중" 표시.

## 제약

- 외부 의존성 없음(Python stdlib, three.js CDN 한 개).
- 읽기 전용. `~/.claude` 아래 어떤 파일도 쓰지 않는다.
- 트랜스크립트 전체를 읽지 않는다(끝 256KB).
- 이 저장소는 git 저장소가 아니다.
