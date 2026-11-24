# baton

긴 작업을 여러 세션에 걸쳐 할 때, **무엇을 하기로 했고 어디까지 확인됐는지**를 프로젝트 안의 핸드오프 문서로 다음 세션에 넘기는 플러그인이다. 여러 프로젝트를 오가도 프로젝트 이름 하나로 그 문서를 찾는다. 이어달리기의 바통처럼.

## 언제 쓰나

| 하고 싶은 것 | 쓸 것 |
|---|---|
| 방금 닫은 대화를 그대로 이어 간다 | Claude Code의 `/resume` 또는 `claude --resume <id>` |
| 새 세션에서, 무엇을 하기로 했고 지금 어느 단계인지부터 잡는다 | `/baton:handon <프로젝트>`로 읽고, 단계를 마칠 때마다 `/baton:handoff <프로젝트>`로 남긴다 |
| 여러 프로젝트 중 어디까지 했는지 본다 | `/baton:handlist` |
| 예전 시점에 무엇을 믿고 있었는지 다시 본다 | "<프로젝트> 이전 핸드오프 보여줘" — 저장할 때마다 남은 체크포인트를 본다 |
| compact 뒤에도 합의 사항이 자동으로 실려 있게 한다 | baton이 아니라 루트 `CLAUDE.md`다. 아래 [compact와 CLAUDE.md](#compact와-claudemd) |

baton은 Git push/pull도, 대화 전체 백업도 아니다. 남기지 않은 대화를 복원하지 않는다. 사람의 승인을 증명하지도 않는다.

프로젝트가 하나뿐이면 합의 사항을 `CLAUDE.md`에 적고 진행 상황을 `HANDOFF.md` 한 장에 적는 것으로 충분하다. baton이 설치할 값을 하는 것은 여러 프로젝트를 몇 주에 걸쳐 오갈 때다.

## 설치

Claude Code 안에서:

```
/plugin marketplace add RuleTheMoments/claude-plugins
/plugin install baton@rulethemoments
```

터미널에서:

```sh
claude plugin marketplace add RuleTheMoments/claude-plugins
claude plugin install baton@rulethemoments
```

macOS와 Linux에서 쓴다. 파일 잠금에 `fcntl`을 쓰므로 Windows에서는 동작하지 않는다. Python 3.9 이상이 필요하고 표준 라이브러리만 쓴다. macOS의 `/usr/bin/python3`에서 만들고 시험했다.

## 두 세션에 걸친 예

1. **등록** — 프로젝트 폴더에서 `claude`를 띄우고 "이 프로젝트를 baton에 myapp으로 등록해줘"라고 한다(`/baton:session register`와 같다).
   - Claude가 저장소를 읽어 환경 복원 명령(`setup`)과 상태 확인 명령(`verify`)을 찾는다. 등록할 때 실행하지는 않는다.
   - 핸드오프 문서(`docs/HANDOFF.md`)가 없으면 골격을 만든다. 목표·하지 않을 것·계획에는 사용자와 정한 것만 적는다.
   - 첫 저장 때 그 내용을 diff로 보여 준다. 저장하라고 답하면 체크포인트 `C0001`이 생긴다.
2. **단계를 마칠 때마다 남긴다** — 예를 들어 "① 형식 호환성 시험 → ② 저장 기능 → ③ UI, 앞 단계가 끝나야 다음 단계"로 정했고 ①을 끝냈다. 그 결정과 시험 결과가 아직 대화에 있을 때 `/baton:handoff myapp`을 실행한다.
   - Claude는 문서에서 바뀐 절만 고치고 체크포인트 `C0002`를 남긴다.
   - 목표·결정·계획이 바뀌었으면 저장하지 않고 diff를 먼저 보여 준다. 저장하라고 답하면 그때 저장한다. compact 요약에서 빠진 결정이 문서에서도 사라졌다면 이 diff에 그 줄이 나온다.
3. **새 세션에서** — `/baton:handon myapp`.
   - 문서를 읽고 git 상태만 확인한 뒤 "①은 끝났다(시험 12/12). 다음은 ②, ③ UI는 그 뒤"처럼 보고한다.
   - 문서의 목표·결정·계획이 마지막 체크포인트와 다르면 양쪽을 보여 주고 어느 쪽이 맞는지 묻는다.
   - 설치·빌드·테스트(`setup`·`verify`)는 묻고 나서 실행한다.

프로젝트를 생략하면 `/baton:handon`은 실행 폴더로 프로젝트를 고른다. `/baton:handoff`는 이 세션이 묶인 프로젝트와 실행 폴더의 프로젝트가 같을 때 그 프로젝트에 쓰고, 어긋나면 문서를 고치기 전에 어디에 쓸지 묻는다.

## 핸드오프 문서

문서는 두 층이다.

- **고정층**: `<!-- baton:fixed -->`와 `<!-- /baton:fixed -->` 사이. 목표, 하지 않을 것, 결정(날짜·이유·버린 대안), 계획(단계 ID·선행 단계·완료 기준)이 들어간다. 이미 쓰던 계획 파일이 있으면 등록할 때 가리킬 수 있고, 그 파일 전체도 고정층이 된다.
- **변동층**: 위험, 진행(단계별 상태와 증거), 다음 행동, 함정. 저장할 때마다 확인된 것으로 고친다.

**고정층에 대해 baton이 하는 일은 두 가지다.**

- 고정층이 바뀐 채로 저장하려 하면 저장하지 않고 diff를 돌려준다. 그 diff를 본 뒤의 저장만 체크포인트가 된다. diff를 본 뒤 문서가 또 바뀌었으면 새 diff가 나온다.
- 읽을 때 문서의 고정층이 마지막 체크포인트와 다르면 그 차이를 알린다.

**하지 않는 일도 분명하다.** 문서 자체가 바뀌는 것은 막지 않는다. 사람이 그 diff를 실제로 봤는지도 확인하지 못한다. 체크포인트는 그 시점의 사본일 뿐 승인의 증거가 아니다. 그래서 문서와 체크포인트가 다르면 어느 쪽이 맞다고 정하지 않고 양쪽을 보여 준다.

**문서는 짧게 둔다.** 지금 유효한 제약, 마지막으로 검증한 것, 다음 행동, 아직 풀리지 않은 함정을 우선 남긴다. 끝난 단계의 긴 설명은 체크포인트에 남아 있으므로 문서에서는 정리해도 된다. baton이 스스로 지우지는 않는다. 다만 `.baton/`의 체크포인트는 백업이 아니다. 오래 남겨야 할 결정은 계획 파일이나 Git에 둔다.

0.3.0에서 쓰던 문서에는 고정층 표시가 없다. 표시가 없으면 변경 감지도 없다. 목표·결정·계획을 표시 안으로 옮기면 다음 저장 때 그 내용을 diff로 보여 준다.

## 무엇이 어디에 남나

| 위치 | 담는 것 | 기대하지 말 것 |
|---|---|---|
| 핸드오프 문서 `<작업 폴더>/docs/HANDOFF.md` | 위의 두 층. 사람이 읽고 고친다 | 자동 커밋. Git에 남기려면 직접 커밋한다 |
| 체크포인트 `<작업 폴더>/.baton/checkpoints/<프로젝트>/` | 저장할 때마다 남는 문서(와 계획 파일) 사본 `C0001`, `C0002`, …. 덮어쓰지 않고, 읽을 때 저장 때의 해시와 대조한다. `.baton/` 안의 `.gitignore`가 자기 자신을 Git 추적에서 뺀다 | 다른 머신으로의 이동. `git clean -fdx` 같은 정리에 지워진다. 지워지면 baton이 알리고, 번호는 이어서 매긴다 |
| 레지스트리 `~/.claude/handoffs/projects.json` | 프로젝트 이름 → 작업 폴더, 문서 경로, 한 줄 요약, `setup`·`verify`, 최신 체크포인트. 쓰기 전에 `projects.json.bak`을 남긴다. `HANDOFF_REGISTRY`(절대 경로)로 위치를 바꿀 수 있다 | 손으로 고치기. 도우미로만 고친다 |
| 세션 바인딩 `~/.claude/handoffs/.pulls/` | 세션마다 마지막으로 이어받거나 저장한 프로젝트와 그 시점 | 오래 보관. 30일이 지나면 지운다 |
| Claude Code 트랜스크립트 `~/.claude/projects/…` | 원 대화. 체크포인트마다 그 세션 ID를 기록한다 | 영구 보관. Claude Code가 설정(`cleanupPeriodDays`)에 따라 정리한다 |

**부작용과 전제**

- 프롬프트를 입력할 때마다 `UserPromptSubmit` 훅이 돈다. `/handon`이 아니면 바로 끝난다(약 50ms). `/baton:handon <프로젝트>`이면 세션 이름을 프로젝트 이름으로 붙인다. 훅에서 오류가 나도 입력은 막지 않는다.
- 도우미 스크립트는 네트워크에 접속하지 않고, 프로젝트 명령을 실행하지 않는다.
- 등록한 `setup`·`verify`는 사용자가 원할 때만 Claude가 실행한다. 그 명령은 네트워크를 쓸 수 있다.
- 스킬은 사용자가 시키기 전에는 커밋하지 않는다.
- **한 프로젝트에는 한 번에 한 세션만 쓴다고 전제한다.** 다른 세션이 먼저 체크포인트를 남긴 경우는 충돌로 잡는다. 두 세션이 저장하기 전에 문서의 같은 줄을 고치면 먼저 쓴 내용은 어디에도 남지 않는다.

## compact와 CLAUDE.md

baton은 compact가 일어날 때 아무것도 하지 않는다. compact 뒤에 핸드오프를 다시 보려면 `/baton:handon`을 직접 다시 실행한다. 앞서 handon으로 읽은 문서가 compact 뒤에도 문맥에 남는지는 보장되지 않는다(아래 실측에서 여섯 번 중 세 번 사라졌다).

합의 사항이 compact 뒤에도 자동으로 실려 있게 하려면 Claude Code의 기능을 쓴다. 루트 `CLAUDE.md`의 본문, 거기서 `@경로`로 import한 파일, `CLAUDE.local.md`는 세션을 시작할 때 실리고 compact 뒤에 디스크에서 다시 읽힌다.

1. 목표·하지 않을 것·결정·계획을 별도 파일(예: `docs/PLAN.md`)에 두고, 등록할 때 `--plan-doc docs/PLAN.md`로 가리킨다.
2. 루트 `CLAUDE.md`에 `@docs/PLAN.md` 한 줄을 넣는다. Git에 올리지 않는 프로젝트라면 `CLAUDE.local.md`에 넣는다.

같은 파일을 Claude Code는 매 세션과 compact 뒤에 다시 싣고, baton은 그 파일이 바뀐 저장을 diff로 드러낸다.

실측 범위: Claude Code 2.1.331, 수동 `/compact`, haiku와 sonnet 각 세 번. 세션 도중 디스크의 값을 바꾸고 compact한 뒤 물으면 세 위치 모두 여섯 번 다 새 값을 답했다. 자동 compact는 재지 않았다. 버전이 바뀌면 다시 잰다.

```sh
/usr/bin/python3 plugins/baton/tests/live/compact_canary.py
```

## resume과 실행 폴더

`claude --resume <id>`는 다른 폴더에서 띄워도 그 세션을 찾아 이어 간다(Claude Code 2.1.327에서 확인). 다만 이어받은 대화의 명령은 새로 띄운 폴더에서 실행되므로 원래 폴더에서 띄우는 편이 안전하다. 목록은 원 대화가 남아 있으면 `cd <원래 폴더> && claude --resume <id>`를 알려 준다.

## 막혔을 때

| 메시지 | 뜻과 할 일 |
|---|---|
| 대상 프로젝트를 정하지 못했다 | 프로젝트 이름을 주거나 `/baton:handlist`로 목록을 본다 |
| 키 없이는 쓰지 않는다 / 어디에 쓸지 묻는다 | 이 세션이 묶인 프로젝트와 실행 폴더의 프로젝트가 다르거나 한쪽이 없다. 프로젝트 이름을 준다 |
| 고정층이 바뀌었다. 저장하지 않았다 | diff를 보고 저장하라고 답하거나 그대로 둔다. 답하기 전에는 저장되지 않는다. 문서는 바뀐 그대로다 — 되돌리려면 되돌려 달라고 한다 |
| 검토값이 지금 내용과 맞지 않는다 | diff를 본 뒤에 문서나 기준 체크포인트가 또 바뀌었다. 새 diff가 나온다 |
| 작업 문서의 고정층이 `C000N`과 다르다 | 읽을 때 나온다. 저장되지 않은 변경이 문서에 있다. 문서가 맞으면 `/baton:handoff`로 저장하고, 체크포인트가 맞으면 되돌려 달라고 한다 |
| 다른 세션이 그 사이 저장했다 | 저장되지 않았다. diff를 보고 어떻게 합칠지 정한다. 먼저 저장된 내용은 체크포인트에, 이 세션의 내용은 문서에 있다 |
| 이 세션을 묶지 못했다 | 읽기나 저장 자체는 됐다. 다음 저장에는 프로젝트 이름을 준다 |
| `C0005 저장됨 / 색인은 C0004` | 사본은 저장됐다. 같은 명령을 다시 실행하면 색인만 고친다 |
| 체크포인트 이력이 없다 | `.baton/`이 지워졌다. 문서는 그대로이고, 다음 저장부터 이력이 다시 쌓인다 |
| 레지스트리가 깨졌다 | 파일이 JSON이 아니거나 `projects`가 없다. baton은 그 파일도 백업도 건드리지 않는다. `projects.json.bak`이 읽히면 그렇다고 알려 준다. 확인한 뒤 직접 복구한다 |
| 원 대화 ✗ | 트랜스크립트가 정리됐다. 문서와 체크포인트는 남아 있다 |

## 명령

| 명령 | 하는 일 |
|---|---|
| `/baton:handon [프로젝트]` | **읽기(pull)**. 문서를 읽고 상태를 보고한다. 프로젝트 명령을 실행하지 않고 문서를 고치지 않는다. 이 세션을 그 프로젝트에 묶고 세션 이름을 붙인다 |
| `/baton:handoff [프로젝트]` | **남기기(push)**. 바뀐 절만 고치고 체크포인트를 남긴다. 고정층이 바뀌었으면 diff를 먼저 보여 준다 |
| `/baton:handlist` | 등록된 프로젝트 목록. `/baton:handon list`도 같다 |
| `baton:session` | 위 명령이 따르는 절차 본문. 말로 부탁하면 실행된다. 등록, 이력("이전 핸드오프 보여줘", "언제 방향이 바뀌었나")도 이 스킬로 한다 |

`/baton:handlist` 출력은 이렇게 생겼다.

```
실행 폴더  /Users/me/src/myapp
현재 세션  5682b6c8 · 이름 "myapp" · 묶인 프로젝트 myapp

이 폴더의 프로젝트 (1)
  myapp  C0003  2026-11-19 18:53  문서 ✓  원 대화 ✓
         ② 저장 기능 진행 중 — writer 초안, 시험 실행 중
         원 대화 cd /Users/me/src/myapp && claude --resume 78ca90a1-…
```

## 도우미 직접 쓰기

```sh
H=~/.claude/plugins/cache/rulethemoments/baton/<버전>/scripts/handoff.py
python3 -I $H list                         # 목록 (--json)
python3 -I $H register myapp --workdir ~/src/myapp [--doc docs/HANDOFF.md] [--plan-doc …] [--setup …] [--verify …]
python3 -I $H set myapp --one-line "…"     # 요약 필드 고치기 (--warning, --setup, --verify, --alias, --clear)
python3 -I $H pull [myapp] --json          # 읽기. 문서의 고정층을 마지막 체크포인트와 견준다(fixed_state)
python3 -I $H push myapp --json            # 체크포인트 남기기. 고정층이 바뀌었으면 diff와 검토값을 돌려준다
python3 -I $H push myapp --review <값> --json   # 그 diff를 본 뒤의 저장
python3 -I $H log myapp                    # 체크포인트 이력
python3 -I $H show myapp C0002 [--against C0003]
python3 -I $H current                      # 현재 세션, 묶인 프로젝트, 키 없는 저장의 대상
```

`push`는 프로젝트 이름을 받아야만 쓴다. `--json` 결과의 `status`가 `ok`·`unchanged`·`repaired`면 종료 코드 0, 그 밖(`needs_target`·`needs_review`·`conflict`·`partial`·`error`)이면 1이다. 0.3.0의 `stamp`는 없어졌다(`push`로 바뀌었다).

## 개발

```sh
cd plugins/baton && /usr/bin/python3 -m unittest discover -s tests
```

테스트는 임시 폴더만 쓴다. 사용자의 레지스트리·세션·git 설정은 건드리지 않는다.

스킬이 실제 세션에서 그렇게 행동하는지는 헤드리스 하네스로 본다. 임시 프로젝트와 임시 레지스트리에서 실제 모델을 부르므로 토큰을 쓴다. 판정은 모델의 말이 아니라 문서 해시, 체크포인트, 도구 호출 기록으로 한다.

```sh
/usr/bin/python3 plugins/baton/tests/live/live_flow.py --runs 3
```

## 라이선스

MIT
