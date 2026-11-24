# claude-plugins

[Claude Code](https://claude.com/claude-code)용 플러그인 마켓플레이스다.

## 플러그인

### [baton](plugins/baton/) — 대화를 재개하는 대신, 프로젝트에서 무엇을 이어갈지 남긴다

여러 프로젝트를 몇 주에 걸쳐 오가며 세션을 자주 새로 여는 작업을 위한 플러그인이다.

- 단계를 마쳤을 때나 중요한 결정 뒤, 세션을 닫기 전에 `/baton:handoff myapp`으로 작업 상태를 프로젝트의 핸드오프 문서에 남긴다.
- 새 세션에서는 `/baton:handon myapp`으로 그 문서를 읽고 이어 간다. compact 뒤에도 직접 다시 실행한다.
- 저장할 때마다 문서 사본(체크포인트)이 남는다. 목표·결정·계획이 바뀐 저장은 diff를 먼저 보여 주고, 읽을 때 문서가 마지막 체크포인트와 다르면 알린다.
- 같은 대화를 그대로 이어 가려면 Claude Code의 `/resume`을 쓴다.

baton은 Git push/pull도, 대화 전체 백업도 아니고, 사람의 승인을 증명하지도 않는다. macOS와 Linux에서 쓴다. 쓰는 법, 저장 위치, 한계는 [plugins/baton/README.md](plugins/baton/README.md)에 있다.

| 명령 | 하는 일 |
|---|---|
| `/baton:handon [프로젝트]` | 핸드오프 문서를 읽고 상태를 보고한다. 프로젝트 명령을 실행하지 않고 문서를 고치지 않는다 |
| `/baton:handoff [프로젝트]` | 확인된 것을 문서에 남기고 체크포인트를 저장한다 |
| `/baton:handlist` | 등록된 프로젝트 목록 |

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

## 유지보수자: 고치고 배포하는 순서

1. `plugins/<플러그인>/`에서 고친다. 테스트가 있으면 돌린다(baton: `cd plugins/baton && /usr/bin/python3 -m unittest discover -s tests`).
2. 설치본과 섞이지 않게, 그 세션에서만 설치본을 끄고 작업 중인 폴더를 올려 확인한다.

   ```sh
   claude --plugin-dir ./plugins/<플러그인> --settings '{"enabledPlugins":{"<플러그인>@rulethemoments":false}}'
   ```

3. `claude plugin validate ./plugins/<플러그인>`과 `claude plugin validate .`로 매니페스트를 검증한다.
4. `plugins/<플러그인>/.claude-plugin/plugin.json`과 `.claude-plugin/marketplace.json`의 `version`을 같은 값으로 올린다.
5. 커밋한 뒤 `claude plugin tag ./plugins/<플러그인> --push`로 `<플러그인>--v<버전>` 태그를 만들어 push하고, 커밋도 push한다.
6. 설치한 쪽에서는 `claude plugin marketplace update rulethemoments`를 실행한 뒤 `claude plugin update <플러그인>@rulethemoments`로 새 버전을 받는다.

## 라이선스

[MIT](LICENSE)
