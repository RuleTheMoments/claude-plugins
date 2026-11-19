# baton

긴 작업을 여러 세션에 걸쳐 할 때, 세션이 바뀌거나 compact로 대화가 요약돼도 **합의한 방향이 흐트러지지 않게** 하는 플러그인이다. 사용자가 승인한 목표·결정·계획과, 세션이 알아낸 것·밟은 함정을 프로젝트 안의 핸드오프 문서로 다음 세션에 넘긴다. 이어달리기의 바통처럼.

## 언제 쓰나

| 하고 싶은 것 | 쓸 것 |
|---|---|
| 방금 닫은 대화를 그대로 이어 간다 | Claude Code의 `/resume` 또는 `claude --resume <id>` |
| 새 세션이나 compact 뒤에도, 무엇을 하기로 했고 지금 어느 단계인지 확실히 이어 간다 | `/baton:handon <프로젝트>`로 읽고, 단계를 마칠 때마다 `/baton:handoff <프로젝트>`로 남긴다 |
| 예전 시점에 무엇을 믿고 있었는지 다시 본다 | "<프로젝트> 이전 핸드오프 보여줘" — 저장할 때마다 남은 체크포인트를 본다 |

baton은 Git push/pull도, 대화 전체 백업도 아니다. 남기지 않은 대화를 복원하지 않는다.

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

필요한 것은 Python 3.9 이상이다. 표준 라이브러리만 쓴다. macOS의 `/usr/bin/python3`에서 만들고 시험했다.

## 두 세션에 걸친 예

1. **등록** — 프로젝트 폴더에서 `claude`를 띄우고 "이 프로젝트를 baton에 myapp으로 등록해줘"라고 한다(`/baton:session register`와 같다).
   - Claude가 저장소를 읽어 환경 복원 명령(`setup`)과 상태 확인 명령(`verify`)을 찾는다.
   - 핸드오프 문서(`docs/HANDOFF.md`)가 없으면 골격을 만든다. 목표·하지 않을 것·계획에는 사용자와 정한 것만 적는다.
   - 첫 저장 때 이 내용을 보여 주고 승인을 받는다.
2. **단계를 마칠 때마다 남긴다** — 예를 들어 "① 형식 호환성 시험 → ② 저장 기능 → ③ UI, 앞 단계가 끝나야 다음 단계"로 합의했고 ①을 끝냈다. 그 결정과 시험 결과가 아직 대화에 있을 때 `/baton:handoff myapp`을 실행한다.
   - Claude는 문서에서 바뀐 절만 고치고 체크포인트 `C0002`를 남긴다.
   - 목표·결정·계획이 바뀌었으면 diff를 보여 주고 승인을 받는다. 승인 없이는 저장하지 않는다. compact 요약에서 빠진 결정이 문서에서도 조용히 사라지는 것을 막는 장치다.
3. **새 세션에서, 또는 compact 뒤에** — `/baton:handon myapp`.
   - 문서를 읽고 git 상태만 확인한 뒤 "①은 끝났다(시험 12/12). 다음은 ②, ③ UI는 그 뒤"처럼 보고한다.
   - 설치·빌드·테스트(`setup`·`verify`)는 묻고 나서 실행한다.

프로젝트를 생략하면 `/baton:handon`은 실행 폴더로 프로젝트를 고른다. `/baton:handoff`는 이 세션이 이어받았거나 앞서 저장한 프로젝트에 쓰고, 그것도 없으면 어디에 쓸지 묻는다.

## 핸드오프 문서

문서는 두 층이다.

- **고정층**: `<!-- baton:fixed -->`와 `<!-- /baton:fixed -->` 사이. 목표, 하지 않을 것, 결정(날짜·이유·버린 대안), 계획(단계 ID·선행 단계·완료 기준)이 들어간다. 바꾸려면 사용자 승인이 필요하다. 이미 쓰던 계획 파일이 있으면 등록할 때 가리킬 수 있고, 그 파일 전체도 고정층이 된다.
- **변동층**: 위험, 진행(단계별 상태와 증거), 다음 행동, 함정. 저장할 때마다 확인된 것으로 고친다.

0.3.0에서 쓰던 문서에는 고정층 표시가 없다. 목표·결정·계획을 표시 안으로 옮기면 다음 저장 때 승인을 받는다.

## 무엇이 어디에 남나

| 위치 | 담는 것 | 기대하지 말 것 |
|---|---|---|
| 핸드오프 문서 `<작업 폴더>/docs/HANDOFF.md` | 위의 두 층. 사람이 읽고 고친다 | 자동 커밋. Git에 남기려면 직접 커밋한다 |
| 체크포인트 `<작업 폴더>/.baton/checkpoints/<프로젝트>/` | 저장할 때마다 남는 문서(와 계획 파일) 사본 `C0001`, `C0002`, …. 덮어쓰지 않고, 읽을 때 저장 때의 해시와 대조한다. `.baton/` 안의 `.gitignore`가 자기 자신을 Git 추적에서 뺀다 | 다른 머신으로의 이동. `git clean -fdx` 같은 정리에 지워진다. 지워지면 baton이 알리고, 번호는 이어서 매긴다 |
| 레지스트리 `~/.claude/handoffs/projects.json` | 프로젝트 이름 → 작업 폴더, 문서 경로, 한 줄 요약, `setup`·`verify`, 최신 체크포인트. 쓰기 전에 `projects.json.bak`을 남긴다. `HANDOFF_REGISTRY`(절대 경로)로 위치를 바꿀 수 있다 | 손으로 고치기. 도우미로만 고친다 |
| 세션 바인딩 `~/.claude/handoffs/.pulls/` | 세션마다 마지막으로 이어받거나 저장한 프로젝트와 그 시점 | 오래 보관. 30일이 지나면 지운다 |
| Claude Code 트랜스크립트 `~/.claude/projects/…` | 원 대화. 체크포인트마다 그 세션 ID를 기록한다 | 영구 보관. Claude Code가 설정(`cleanupPeriodDays`, 기본 30일)에 따라 정리한다 |

**부작용**

- 프롬프트를 입력할 때마다 `UserPromptSubmit` 훅이 돈다. `/handon`이 아니면 바로 끝난다(약 45ms). `/baton:handon <프로젝트>`이면 세션 이름을 프로젝트 이름으로 붙인다. 훅에서 오류가 나도 입력은 막지 않는다.
- 도우미 스크립트는 네트워크에 접속하지 않고, 프로젝트 명령을 실행하지 않는다.
- 등록한 `setup`·`verify`는 사용자가 원할 때만 Claude가 실행한다. 그 명령은 네트워크를 쓸 수 있다.
- 스킬은 사용자의 승인 없이 커밋하지 않는다.

## resume과 실행 폴더

`claude --resume <id>`는 다른 폴더에서 띄워도 그 세션을 찾아 이어 간다(Claude Code 2.1.327에서 확인). 다만 이어받은 대화의 명령은 새로 띄운 폴더에서 실행되므로 원래 폴더에서 띄우는 편이 안전하다. 목록은 원 대화가 남아 있으면 `cd <원래 폴더> && claude --resume <id>`를 알려 준다.

## 막혔을 때

| 메시지 | 뜻과 할 일 |
|---|---|
| 대상 프로젝트를 정하지 못했다 | 프로젝트 이름을 주거나 `/baton:handlist`로 목록을 본다 |
| 고정층이 바뀌었다 | diff를 보고 승인하거나 되돌린다. 승인 전에는 저장되지 않는다 |
| 다른 세션이 그 사이 저장했다 | 두 기록을 합친 뒤 다시 저장한다. 먼저 저장된 내용은 체크포인트에, 이 세션의 내용은 문서에 그대로 있다 |
| `C0005 저장됨 / 색인은 C0004` | 사본은 저장됐다. 같은 명령을 다시 실행하면 색인만 고친다 |
| 체크포인트 이력이 없다 | `.baton/`이 지워졌다. 문서는 그대로이고, 다음 저장부터 이력이 다시 쌓인다 |
| 레지스트리가 깨졌다 | `projects.json.bak`이 읽히면 그렇다고 알려 준다. 확인한 뒤 복구한다 |
| 원 대화 ✗ | 트랜스크립트가 정리됐다. 문서와 체크포인트는 남아 있다 |

## 명령

| 명령 | 하는 일 |
|---|---|
| `/baton:handon [프로젝트]` | **읽기(pull)**. 문서를 읽고 상태를 보고한다. 아무 명령도 실행하지 않는다. 이 세션을 그 프로젝트에 묶고 세션 이름을 붙인다 |
| `/baton:handoff [프로젝트]` | **남기기(push)**. 바뀐 절만 고치고 체크포인트를 남긴다. 고정층 변경은 승인을 받는다 |
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
python3 -I $H pull [myapp] --json          # 읽기. 아무것도 실행하지 않는다
python3 -I $H push [myapp] --json          # 체크포인트 남기기 (--approve-plan-change는 승인 뒤에만)
python3 -I $H log myapp                    # 체크포인트 이력
python3 -I $H show myapp C0002 [--against C0003]
python3 -I $H current                      # 현재 세션과 묶인 프로젝트
```

`--json` 결과의 `status`가 `ok`·`unchanged`·`repaired`면 종료 코드 0, 그 밖(`needs_target`·`needs_approval`·`conflict`·`partial`·`error`)이면 1이다. 0.3.0의 `stamp`는 없어졌다(`push`로 바뀌었다).

## 개발

```sh
cd plugins/baton && /usr/bin/python3 -m unittest discover -s tests
```

테스트는 임시 폴더만 쓴다. 사용자의 레지스트리·세션·git 설정은 건드리지 않는다.

## 라이선스

MIT
