---
name: handoff
description: 지금까지 확인된 것을 프로젝트의 핸드오프 문서에 남기고 체크포인트를 저장한다(push). 단계를 마쳤을 때나 중요한 결정 뒤, 세션을 끝내기 전에 쓴다. 목표·결정·계획(고정층)이 바뀌면 diff를 보여 주고 승인을 받는다. "/handoff myapp"는 "myapp 핸드오프 갱신해줘"와 같다. 인자는 프로젝트 이름 — 생략하면 이 세션이 이어받은(또는 앞서 저장한) 프로젝트, 그것도 없으면 어디에 쓸지 묻는다. `list`면 목록만 보여준다. 짝: /handon(이어받기), /handlist(목록).
argument-hint: "[프로젝트 | list]"
---

# handoff — 갱신 (push)

`session` 스킬의 **§2 갱신**을 부르는 지름길이다. 아래에서 `H`는 `python3 -I "${CLAUDE_SKILL_DIR}/../../scripts/handoff.py"`다.

1. 인자가 `list`·`ls`·`목록`이면 `/handlist`와 같다 — `H list` 출력을 코드블록으로 보여주고 끝낸다.
2. **대상을 먼저 정한다** — 문서를 고치기 전에.
   - 인자가 있으면 그 프로젝트. 등록되어 있지 않으면(`H resolve <인자> --json`의 `match`가 없으면) 문서를 고치기 전에 이 폴더를 그 이름으로 등록할지 묻는다. 묻지 않고 등록하지 않는다.
   - 인자가 없으면 `H current --json`의 `bound`(이 세션이 이어받았거나 앞서 저장한 프로젝트).
   - 둘 다 없으면 `H resolve --json`의 `candidates`를 보여 주고, 그중 하나를 고르거나 새 이름을 정해 달라고 묻는다. 새 이름을 받으면 `session` §3으로 등록한다.
   - 확인하려고 `H pull`을 부르지 않는다. 그 사이 다른 세션이 저장한 것을 충돌로 잡지 못하게 된다.
3. `Skill` 도구로 `baton:session`을 `<키> save` 인자로 호출하고 **§2 갱신**을 그대로 따른다. 요점:
   - 돌고 있는 작업은 멈추지 않고 "실행 중, 결과 미확인"으로 적는다. 세션을 끝낸다고 들었을 때만 정리한다.
   - 문서를 다시 읽고 바뀐 절만 Edit로 고친다. 고정층은 사용자가 이번 세션에 승인한 변경만.
   - `H push <키> --json`. `needs_approval`이면 `fixed_diff`를 보여 주고 승인을 받은 뒤에만 `--approve-plan-change`. `conflict`면 합친 뒤 pull하고 다시 push. `partial`이면 같은 명령을 다시.
   - `H set <키> --one-line "…" --json`으로 한 줄 요약.
4. push와 요약이 모두 성공했을 때만 "이 세션을 닫아도 됩니다"라고 하고, 다음 세션 첫 명령을 알려준다 — 실행 폴더(`H current`)와 함께: `cd <실행 폴더> && claude` → `/baton:handon <키>`. 승인 없이 커밋하지 않는다.

사용자와는 한국어로 말한다.
