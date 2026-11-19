# claude-plugins

[Claude Code](https://claude.com/claude-code)용 플러그인 마켓플레이스다.

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

## 플러그인

### [baton](plugins/baton/)

프로젝트별 세션 핸드오프 플러그인이다. 세션을 끝내기 전에 작업 상태를 프로젝트 안의 핸드오프 문서로 남기고, 다음 세션에서 그 문서를 읽어 이어서 작업한다.

| 명령 | 하는 일 |
|---|---|
| `/baton:handon [프로젝트]` | 마지막 세션의 핸드오프를 불러와 이어서 작업할 준비를 하고, 세션 이름을 프로젝트 이름으로 붙인다 |
| `/baton:handoff [프로젝트]` | 다음 세션을 위해 핸드오프 문서와 레지스트리를 갱신한다. 세션을 끝내거나 재시작하기 전에 쓴다 |
| `/baton:handlist` | 이어받을 수 있는 프로젝트 목록을 Claude 실행 폴더 기준으로 보여 준다. `/baton:handon list`도 같은 목록을 보여 준다 |
| `baton:session` | 위 세 명령이 따르는 절차 본문이다. "myapp 이어서 작업할 준비해줘"처럼 말로 부탁하면 실행되고, 새 프로젝트 등록(`register`)도 이 스킬로 한다 |

`/baton:handon`과 `/baton:handoff`에서 프로젝트를 생략하면 Claude 실행 폴더에 맞는 프로젝트를 고른다. 처음 쓰는 방법, 레지스트리 형식, 이 플러그인이 읽고 쓰는 파일은 [plugins/baton/README.md](plugins/baton/README.md)에 있다.

## 고치고 배포하는 순서

1. `plugins/<플러그인>/`에서 고친다. 같은 이름의 설치본이 함께 로드되지 않게 `claude plugin disable <플러그인>@rulethemoments`로 잠시 끄고, `claude --plugin-dir ./plugins/<플러그인>`으로 새 세션을 띄워 확인한다.
2. `claude plugin validate ./plugins/<플러그인>`과 `claude plugin validate .`로 매니페스트를 검증한다.
3. `plugins/<플러그인>/.claude-plugin/plugin.json`과 `.claude-plugin/marketplace.json`의 `version`을 같은 값으로 올린다.
4. 커밋한 뒤 `claude plugin tag ./plugins/<플러그인> --push`로 `<플러그인>--v<버전>` 태그를 만들어 push하고, 커밋도 push한다.
5. 설치한 쪽에서는 `claude plugin marketplace update rulethemoments`를 실행한 뒤 `claude plugin update <플러그인>@rulethemoments`로 새 버전을 받는다.

## 라이선스

[MIT](LICENSE)
