# claude-plugins

[Claude Code](https://claude.com/claude-code)용 플러그인 마켓플레이스다.

```
/plugin marketplace add RuleTheMoments/claude-plugins
```

## 플러그인

| 플러그인 | 설치 | 설명 |
|---|---|---|
| [baton](plugins/baton/) | `/plugin install baton@rulethemoments` | 프로젝트별 세션 핸드오프. 세션을 끝내기 전에 작업 상태를 남기고(`/baton:handoff`), 다음 세션에서 이어받는다(`/baton:handon`) |

## 고치고 배포하는 순서

1. `plugins/<플러그인>/`에서 고친다. 같은 이름의 설치본이 함께 로드되지 않게 `claude plugin disable <플러그인>@rulethemoments`로 잠시 끄고, `claude --plugin-dir ./plugins/<플러그인>`으로 새 세션을 띄워 확인한다.
2. `claude plugin validate ./plugins/<플러그인>`과 `claude plugin validate .`로 매니페스트를 검증한다.
3. `plugins/<플러그인>/.claude-plugin/plugin.json`과 `.claude-plugin/marketplace.json`의 `version`을 같은 값으로 올린다.
4. 커밋한 뒤 `claude plugin tag ./plugins/<플러그인> --push`로 `<플러그인>--v<버전>` 태그를 만들어 push하고, 커밋도 push한다.
5. 설치한 쪽에서는 `claude plugin marketplace update rulethemoments`를 실행한 뒤 `claude plugin update <플러그인>@rulethemoments`로 새 버전을 받는다.

## 라이선스

[MIT](LICENSE)
