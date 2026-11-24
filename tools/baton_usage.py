#!/usr/bin/env python3
"""baton이 실제로 어떻게 쓰였는지 Claude Code 트랜스크립트에서 센다. 개발용 도구이고 플러그인에 들어가지 않는다.

    /usr/bin/python3 tools/baton_usage.py scan   [--since 2026-11-24] [--json] [--include-dev]
    /usr/bin/python3 tools/baton_usage.py review [--since 2026-11-24] [--out docs/baton_field_review.md]

scan   호출 횟수(슬래시, Skill 도구, 헬퍼 직접 호출 — 말로 부탁한 경우도 잡힌다), 헬퍼가 돌려준 결과,
       고정층 검토 요청의 결말, compact와 그 뒤의 handon, 세션 시작 방식.
review 사후에 사람이 표시할 라벨링 시트. handon마다 보고와 그 뒤 사용자 말, 검토 요청마다 diff.

읽기만 한다. ~/.claude/projects 아래 파일을 고치지 않는다.

한계: 트랜스크립트 형식은 Claude Code 내부용이라 버전이 바뀌면 깨질 수 있다. 세션은 있는데 SessionStart
기록이 하나도 없으면 경고한다. 트랜스크립트는 기본 30일쯤 뒤 정리되므로, 오래 볼 것은 scan --json 결과를
그때그때 파일로 남긴다.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

ROOT = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")
# baton을 만들고 시험한 세션(이 저장소, 임시 폴더)은 사용 기록이 아니다
DEV_MARKS = ("claude-plugins", "baton-live-", "baton-canary-", "baton-test-", "scratchpad", "private-tmp",
             "var-folders")
SLASH_RE = re.compile(r"<command-name>/?(?:baton:)?(handon|handoff|handlist)</command-name>")
SUB_RE = re.compile(r"(?:handoff\.py[\"']?|\$\{?H\}?[\"']?)\s+(pull|push|stamp|register|set|list|log|show|current|resolve|title)\b")
REVIEW_STATUSES = ("needs_review", "needs_approval")  # 0.4 초안은 needs_approval이었다


def stamp_of(rec):
    return rec.get("timestamp") or ""


def blocks(rec):
    content = (rec.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def prompt_text(rec):
    """사용자가 직접 넣은 입력이면 그 글, 아니면 None(도구 결과, 주입된 메시지)."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks(rec)):
        return None
    text = "".join(b.get("text", "") for b in blocks(rec) if isinstance(b, dict) and b.get("type") == "text")
    return text or None


def result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def json_objects(text):
    """글 안의 최상위 JSON 객체들. 헬퍼는 --json 결과를 줄 처음의 { 로 시작해 찍는다."""
    out, dec, i = [], json.JSONDecoder(), 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return out
        if i == 0 or text[i - 1] == "\n":
            try:
                obj, end = dec.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            if isinstance(obj, dict) and "status" in obj:
                out.append(obj)
            i = end
        else:
            i += 1


def read_session(path):
    """세션 하나를 순서 있는 사건 목록으로 바꾼다."""
    events, pending = [], {}
    last_start = {}
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return events
    with f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind, ts = rec.get("type"), stamp_of(rec)
            if kind == "attachment":
                name = (rec.get("attachment") or {}).get("hookName") or ""
                if name.startswith("SessionStart:"):
                    source = name.split(":", 1)[1]
                    if ts[:18] != last_start.get(source):  # 한 사건에 훅이 여럿이라 같은 시각의 줄이 여러 개다
                        events.append({"e": "start", "source": source, "ts": ts})
                    last_start[source] = ts[:18]
            elif kind == "system" and rec.get("subtype") == "compact_boundary":
                meta = rec.get("compactMetadata") or rec.get("compact_metadata") or {}
                events.append({"e": "compact", "trigger": meta.get("trigger") or "?", "ts": ts})
            elif kind == "user":
                text = prompt_text(rec)
                if text is not None:
                    events.append({"e": "prompt", "text": text, "ts": ts})
                    m = SLASH_RE.search(text)
                    if m:
                        events.append({"e": "slash", "name": m.group(1), "ts": ts})
                for b in blocks(rec):
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                        subs = pending.pop(b["tool_use_id"])
                        objs = json_objects(result_text(b))
                        for o in objs:
                            if "handoff_doc" in o and "by" in o:
                                events.append({"e": "pull", "status": o.get("status"), "key": o.get("key"),
                                               "fixed_state": o.get("fixed_state"), "fixed_diff": o.get("fixed_diff"),
                                               "bound": o.get("bound"), "ts": ts})
                            elif "checkpoint" in o or o.get("status") in REVIEW_STATUSES + ("conflict", "partial") \
                                    or (o.get("status") == "needs_target" and "push" in subs):
                                events.append({"e": "push", "status": o.get("status"), "key": o.get("key"),
                                               "plan_changed": o.get("plan_changed"), "checkpoint": o.get("checkpoint"),
                                               "diff": o.get("fixed_diff") or o.get("diff"), "ts": ts})
                        if "stamp" in subs:  # 0.3.0: JSON이 아니라 한 줄로 답한다
                            events.append({"e": "push", "status": "stamp", "key": None, "plan_changed": None,
                                           "checkpoint": None, "diff": None, "ts": ts})
            elif kind == "assistant" and not rec.get("isSidechain"):
                for b in blocks(rec):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text"):
                        events.append({"e": "say", "text": b["text"], "ts": ts})
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        if b.get("name") == "Skill" and str(inp.get("skill", "")).startswith("baton:"):
                            events.append({"e": "skill", "name": inp["skill"].split(":", 1)[1],
                                           "args": str(inp.get("args") or ""), "ts": ts})
                        elif b.get("name") == "Bash" and "handoff.py" in str(inp.get("command", "")):
                            pending[b.get("id")] = SUB_RE.findall(inp["command"])
    return events


def turns_of(events):
    """사용자 입력 하나에서 다음 입력 전까지가 한 턴이다."""
    turns, cur = [], None
    for ev in events:
        if ev["e"] == "prompt":
            cur = {"prompt": ev["text"], "ts": ev["ts"], "events": [], "said": []}
            turns.append(cur)
        elif cur is not None:
            if ev["e"] == "say":
                cur["said"].append(ev["text"])
            else:
                cur["events"].append(ev)
    for t in turns:
        kinds = {(e["e"], e.get("name")) for e in t["events"]}
        es = {e["e"] for e in t["events"]}
        save = any(e["e"] == "skill" and e["name"] == "session" and re.search(r"\bsave\b", e["args"]) for e in t["events"])
        t["handoff"] = bool("push" in es or ("slash", "handoff") in kinds or ("skill", "handoff") in kinds or save)
        t["handon"] = bool(not t["handoff"] and ("pull" in es or ("slash", "handon") in kinds or ("skill", "handon") in kinds))
        t["handlist"] = bool(("slash", "handlist") in kinds or ("skill", "handlist") in kinds)
        t["by_slash"] = any(e["e"] == "slash" for e in t["events"])
    return turns


def review_episodes(events):
    """고정층 검토 요청 하나가 어떻게 끝났는가: accepted(바뀐 채 저장), reverted(되돌려 저장), abandoned(저장 없음)."""
    out, open_ = [], {}
    for ev in events:
        if ev["e"] != "push":
            continue
        key = ev.get("key") or "?"
        if ev["status"] in REVIEW_STATUSES:
            ep = open_.setdefault(key, {"key": key, "ts": ev["ts"], "asked": 0, "diff": None, "outcome": "abandoned"})
            ep["asked"] += 1
            ep["diff"] = ev.get("diff") or ep["diff"]
        elif key in open_ and ev["status"] in ("ok", "unchanged", "repaired"):
            ep = open_.pop(key)
            ep["outcome"] = "accepted" if ev.get("plan_changed") else "reverted"
            out.append(ep)
    return out + list(open_.values())


def is_dev(project_dir):
    return any(m in project_dir for m in DEV_MARKS)


def collect(since, include_dev):
    sessions = []
    for path in sorted(glob.glob(os.path.join(ROOT, "*", "*.jsonl"))):
        project = os.path.basename(os.path.dirname(path))
        if not include_dev and is_dev(project):
            continue
        if since and datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat() < since:
            continue
        events = read_session(path)
        if since:
            events = [e for e in events if not e.get("ts") or e["ts"][:10] >= since]
        sessions.append({"project": project, "id": os.path.basename(path)[:-len(".jsonl")], "events": events,
                         "turns": turns_of(events)})
    return sessions


def summarize(sessions):
    c = collections.Counter()
    pulls, pushes, starts, compacts, episodes = (collections.Counter() for _ in range(5))
    pushes_per_session = []
    for s in sessions:
        ev = s["events"]
        used = False
        for t in s["turns"]:
            for kind in ("handon", "handoff", "handlist"):
                if t[kind]:
                    used = True
                    c[kind] += 1
                    c[kind + ("_slash" if t["by_slash"] else "_spoken")] += 1
        c["sessions"] += 1
        c["sessions_using_baton"] += used
        n_push = 0
        for e in ev:
            if e["e"] == "start":
                starts[e["source"]] += 1
            elif e["e"] == "pull":
                pulls[e.get("fixed_state") or "0.4 초안 이전(견주지 않음)"] += 1
                c["pull_unbound"] += e.get("bound") is False
            elif e["e"] == "push":
                pushes[e["status"] or "?"] += 1
                n_push += e["status"] in ("ok", "stamp")
        if n_push:
            pushes_per_session.append(n_push)
        for ep in review_episodes(ev):
            episodes[ep["outcome"]] += 1
        # compact: baton을 쓰던 세션에서 일어났는가, 그 뒤 같은 세션에서 다시 읽었는가
        seen_baton = False
        idx = [i for i, e in enumerate(ev) if e["e"] == "compact"]
        for i, e in enumerate(ev):
            if e["e"] in ("pull", "push", "slash", "skill"):
                seen_baton = True
            if e["e"] == "compact":
                compacts[e["trigger"]] += 1
                if seen_baton:
                    c["compact_in_baton_session"] += 1
                    nxt = min([j for j in idx if j > i] or [len(ev)])
                    c["compact_then_handon"] += any(x["e"] == "pull" or (x["e"] in ("slash", "skill") and x.get("name") == "handon")
                                                    for x in ev[i + 1:nxt])
    warn = []
    if c["sessions"] >= 5 and not starts:
        warn.append("세션은 %d개인데 SessionStart 기록이 없다 — 훅이 없거나 트랜스크립트 형식이 바뀌었다. "
                    "시작·resume 횟수는 믿지 않는다" % c["sessions"])
    return {
        "sessions": c["sessions"], "sessions_using_baton": c["sessions_using_baton"],
        "handon": {"turns": c["handon"], "slash": c["handon_slash"], "spoken": c["handon_spoken"]},
        "handoff": {"turns": c["handoff"], "slash": c["handoff_slash"], "spoken": c["handoff_spoken"]},
        "handlist": c["handlist"],
        "pull_fixed_state": dict(pulls), "pull_unbound": c["pull_unbound"],
        "push_status": dict(pushes),
        "saves_per_session": sorted(pushes_per_session),
        "review_episodes": dict(episodes),
        "compact": {"by_trigger": dict(compacts), "in_baton_session": c["compact_in_baton_session"],
                    "followed_by_handon": c["compact_then_handon"]},
        "session_start": dict(starts),
        "warnings": warn,
    }


def print_summary(r, since):
    print("baton 사용 기록%s — 세션 %d개 중 %d개에서 썼다" % (" (%s부터)" % since if since else "", r["sessions"],
                                                 r["sessions_using_baton"]))
    for w in r["warnings"]:
        print("⚠ " + w)
    for name in ("handon", "handoff"):
        d = r[name]
        print("%-8s %3d턴 (슬래시 %d, 말로 %d)" % (name, d["turns"], d["slash"], d["spoken"]))
    print("handlist %3d턴" % r["handlist"])
    print("읽을 때 고정층 비교   %s%s" % (r["pull_fixed_state"] or "없음",
                                  " · 묶지 못한 읽기 %d" % r["pull_unbound"] if r["pull_unbound"] else ""))
    print("저장 결과            %s" % (r["push_status"] or "없음"))
    print("세션당 저장 횟수      %s" % (r["saves_per_session"] or "없음"))
    print("고정층 검토 요청의 결말 %s  (accepted 바뀐 채 저장 · reverted 되돌려 저장 · abandoned 저장 없음)"
          % (r["review_episodes"] or "없음"))
    cp = r["compact"]
    print("compact             %s · baton을 쓰던 세션에서 %d회 · 그 뒤 같은 세션에서 handon %d회"
          % (cp["by_trigger"] or "없음", cp["in_baton_session"], cp["followed_by_handon"]))
    print("세션 시작            %s" % (r["session_start"] or "기록 없음"))


def clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + " …"


def review_sheet(sessions, since):
    out = ["# baton 사용 라벨링 시트", "",
           "만든 날 %s%s. 항목마다 해당하는 쪽에 x를 넣는다. 5분이면 된다." % (datetime.date.today().isoformat(),
                                                                " · %s부터" % since if since else ""), ""]
    n = 0
    for s in sessions:
        turns = s["turns"]
        for i, t in enumerate(turns):
            if not t["handon"]:
                continue
            n += 1
            later = [clip(x["prompt"], 300) for x in turns[i + 1:] if not x["prompt"].lstrip().startswith("<")][:3]
            out += ["## handon %d — %s · %s · %s" % (n, t["ts"][:16].replace("T", " "), s["project"][-40:], s["id"][:8]), "",
                    "**보고(끝부분)**", "", "```", clip("\n".join(t["said"])[-1500:], 1500), "```", "",
                    "**그 뒤 사용자가 한 말**", ""]
            out += ["- " + x.replace("\n", " ") for x in later] or ["- (없음)"]
            out += ["", "- 보고를 읽고도 다시 설명해야 했나: [ ] 예  [ ] 아니오  — 메모:", ""]
    for s in sessions:
        for ep in review_episodes(s["events"]):
            n += 1
            out += ["## 고정층 검토 요청 %d — %s · %s · 결말 %s" % (n, ep["ts"][:16].replace("T", " "), ep["key"], ep["outcome"]),
                    "", "```diff", clip(ep["diff"], 2500), "```", "",
                    "- 이 변경은: [ ] 내가 의도한 것  [ ] 뜻밖의 것(요약에서 빠졌거나 모델이 고쳤다)  — 메모:", ""]
        for e in s["events"]:
            if e["e"] == "pull" and e.get("fixed_state") == "differs":
                n += 1
                out += ["## 읽을 때 차이 알림 %d — %s · %s" % (n, e["ts"][:16].replace("T", " "), e.get("key")), "",
                        "```diff", clip(e.get("fixed_diff"), 2500), "```", "",
                        "- 문서 쪽 변경은: [ ] 내가 의도한 것  [ ] 뜻밖의 것  — 메모:", ""]
        seen = False
        for e in s["events"]:
            seen = seen or e["e"] in ("pull", "push", "slash", "skill")
            if e["e"] == "compact" and seen:
                n += 1
                out += ["## compact %d — %s · %s · %s" % (n, e["ts"][:16].replace("T", " "), s["project"][-40:], e["trigger"]),
                        "", "- 그 뒤 방향이 틀어졌나: [ ] 예  [ ] 아니오  [ ] 모르겠다  — 메모:", ""]
    if n == 0:
        out += ["표시할 항목이 없다."]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    for name in ("scan", "review"):
        sp = sub.add_parser(name)
        sp.add_argument("--since", help="이 날짜(YYYY-MM-DD)부터의 사건만")
        sp.add_argument("--include-dev", action="store_true", help="baton 개발·시험 세션도 포함한다")
        if name == "scan":
            sp.add_argument("--json", action="store_true")
        else:
            sp.add_argument("--out", help="시트를 쓸 파일(없으면 표준 출력)")
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return 2
    sessions = collect(a.since, a.include_dev)
    if a.cmd == "scan":
        r = summarize(sessions)
        r["since"], r["at"] = a.since, datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        if a.json:
            json.dump(r, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print_summary(r, a.since)
        return 0
    sheet = review_sheet(sessions, a.since)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(sheet)
        print("%s 에 썼다" % a.out)
    else:
        sys.stdout.write(sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
