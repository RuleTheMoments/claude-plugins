# baton

Claude Code 세션을 끝내기 전에 작업 상태를 프로젝트 안의 핸드오프 문서로 남기고, 다음 세션에서 그 문서를 읽어 이어서 작업하게 하는 플러그인이다. 이어달리기에서 바통을 넘기듯이, 한 세션이 알아낸 것과 밟은 함정을 다음 세션에 넘긴다.

| 명령 | 하는 일 |
|---|---|
| `/baton:handon [프로젝트]` | 핸드오프 문서를 읽고 실제 환경 상태를 확인해 이어받을 준비를 한다. 세션 이름을 프로젝트 이름으로 붙인다(훅). 프로젝트를 생략하면 Claude 실행 폴더에 맞는 프로젝트를 고른다 |
| `/baton:handoff [프로젝트]` | 핸드오프 문서를 다시 쓰고, 레지스트리의 `updated`, `last_session_id`, `transcript_dir`를 현재 세션으로 갱신한다 |
| `/baton:handlist` | 등록된 프로젝트를 Claude 실행 폴더 기준으로 나눠 보여 준다. 레지스트리 점검 결과와 현재 세션 이름도 함께 보여 준다 |
| `baton:session` | 위 세 명령이 따르는 절차 본문이다. "myapp 이어서 작업할 준비해줘"처럼 말로 부탁하면 이 스킬이 실행된다. 새 프로젝트 등록(`register`)은 이 스킬로 한다 |

`/baton:handlist` 목록의 표시는 다음과 같다.

- ● 이 폴더에서 이어받거나 갱신할 수 있다
- ◐ 작업 폴더 안에서 실행했지만, 지난 세션은 다른 폴더에서 띄웠다
- ○ 다른 폴더에서 실행해야 한다

"실행 폴더"는 `claude`를 띄운 폴더다. 트랜스크립트는 `~/.claude/projects/<실행 폴더 경로의 영숫자가 아닌 문자를 -로 바꾼 이름>/`에 쌓이고, `claude --resume <id>`는 같은 폴더에서 띄웠을 때만 그 세션을 찾는다. 그래서 목록을 이 기준으로 나눈다.

스킬 본문과 출력은 한국어로 쓰여 있다.

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

## 처음 쓸 때

1. 프로젝트 폴더에서 `claude`를 띄우고 "이 프로젝트를 baton에 등록해줘"라고 하거나 `/baton:session register`를 실행한다. Claude가 저장소를 읽어 환경 복원 명령(`setup`)과 상태 확인 명령(`verify`)을 찾고 레지스트리에 항목을 추가한다. 핸드오프 문서가 없으면 `docs/HANDOFF.md`를 만든다.
2. 세션을 끝내기 전에 `/baton:handoff`를 실행한다.
3. 새 세션을 같은 폴더에서 띄우고 `/baton:handon <프로젝트>`를 실행한다.

## 레지스트리

레지스트리는 `~/.claude/handoffs/projects.json`에 있고, `HANDOFF_REGISTRY` 환경 변수로 위치를 바꿀 수 있다. 레지스트리는 프로젝트 이름으로 작업 폴더와 핸드오프 문서를 찾아가는 색인일 뿐이고, 상세한 내용은 각 프로젝트의 핸드오프 문서에 있다. 핸드오프 문서는 코드와 함께 버전 관리된다.

```json
{
  "projects": {
    "myapp": {
      "aliases": ["my app"],
      "workdir": "/Users/me/src/myapp",
      "handoff_doc": "docs/HANDOFF.md",
      "transcript_dir": "/Users/me/.claude/projects/-Users-me-src-myapp",
      "last_session_id": "<세션 ID>",
      "updated": "2026-11-19",
      "one_line": "다음 세션이 이 한 줄만 읽고도 방향을 잡을 수 있게 쓴다",
      "setup": ["pnpm install"],
      "verify": ["pnpm test"],
      "warnings": ["feature/login 브랜치에 커밋하지 않은 변경이 있다"]
    }
  }
}
```

## 이 플러그인이 읽고 쓰는 것

- **훅**: 프롬프트를 입력할 때마다 `UserPromptSubmit` 훅이 `python3 scripts/handoff.py hook`을 실행한다. 입력에 `handon`이 없고 대기 중인 세션 이름도 없으면 다른 일을 하지 않고 바로 끝난다. 입력이 `/baton:handon <프로젝트>`이면 세션 이름을 프로젝트 이름으로 붙인다. 훅에서 오류가 나도 입력은 막지 않는다.
- **읽는 것**: 레지스트리와 `~/.claude/projects/` 아래의 트랜스크립트를 읽는다. 트랜스크립트는 세션을 띄운 폴더와 세션 이름을 알아내는 데 쓴다.
- **쓰는 것**: 레지스트리(쓰기 전에 `projects.json.bak`으로 백업한다), 다음 입력 때 붙일 세션 이름(`~/.claude/handoffs/.pending-titles/<세션 ID>`, 7일이 지나면 지운다), 각 프로젝트의 핸드오프 문서(`/baton:handoff`를 실행했을 때 Claude가 쓴다)를 쓴다.
- 네트워크에는 접속하지 않는다. 스킬은 사용자의 승인 없이 커밋하지 않는다.

## 도우미 직접 쓰기

```sh
H=~/.claude/plugins/cache/rulethemoments/baton/<버전>/scripts/handoff.py
python3 -I $H list                # 실행 폴더 기준 목록 (--json)
python3 -I $H resolve [이름]      # 프로젝트 하나 고르기
python3 -I $H current             # 현재 세션 ID, 이름, 폴더
python3 -I $H title <키>          # 세션 이름을 대기시킨다. 다음 입력 때 적용된다
python3 -I $H stamp <키>          # 레지스트리 항목을 현재 세션으로 갱신한다
```

## 라이선스

MIT
