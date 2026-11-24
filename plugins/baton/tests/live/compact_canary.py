#!/usr/bin/env python3
"""compact 뒤에 무엇이 디스크에서 다시 들어오는가 — 카나리아 실험.

    /usr/bin/python3 plugins/baton/tests/live/compact_canary.py [--runs 3] [--model haiku] [--out FILE]

여섯 곳에 서로 다른 값(v1)을 두고 세션을 시작한다. 모델이 파일을 읽은 뒤 디스크의 값을 모두 v2로 바꾸고
/compact를 한다. 그다음 값을 묻는다. 답이 v2면 compact가 디스크에서 다시 읽은 것이고, v1이면 요약이나
세션 시작 때의 사본으로 남은 것이고, null이면 사라진 것이다.

대화에 한 번 나온 값은 요약에 실려 compact를 넘는다. 그래서 "기억하는가"만 물어서는 재주입을 가릴 수 없다.

대조군(--arm control)은 /compact 없이 같은 질문을 한다. 여기서 v2가 나오면 compact와 무관하게 값이
갱신된다는 뜻이므로 본 실험의 v2를 compact의 효과로 읽을 수 없다.

실제 모델을 부른다(기본 haiku, 실행당 4턴). unittest에 잡히지 않는다.
"""
import argparse
import datetime
import json
import os
import secrets
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from driver import Session, claude_version  # noqa: E402

KEYS = ("ROOT", "IMPORT", "LOCAL", "NESTED", "READ_OLD", "READ_RECENT")
WHERE = {
    "ROOT": "루트 CLAUDE.md 본문",
    "IMPORT": "루트 CLAUDE.md가 @로 import한 파일",
    "LOCAL": "CLAUDE.local.md",
    "NESTED": "하위 폴더의 CLAUDE.md",
    "READ_OLD": "Read로 읽은 문서(그 뒤 일곱 개를 더 읽음)",
    "READ_RECENT": "Read로 마지막에 읽은 문서",
}
FILLERS = 6


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def lay_out(root, values):
    """values: {키: 값}. 같은 함수로 v1을 깔고, 나중에 v2로 다시 깐다."""
    write(os.path.join(root, "CLAUDE.md"),
          "# canary fixture\n\nROOT_CANARY = %s\n\n@fixed.md\n" % values["ROOT"])
    write(os.path.join(root, "fixed.md"), "IMPORT_CANARY = %s\n" % values["IMPORT"])
    write(os.path.join(root, "CLAUDE.local.md"), "LOCAL_CANARY = %s\n" % values["LOCAL"])
    write(os.path.join(root, "sub", "CLAUDE.md"), "NESTED_CANARY = %s\n" % values["NESTED"])
    write(os.path.join(root, "handoff.md"), "# handoff\n\nREAD_OLD_CANARY = %s\n" % values["READ_OLD"])
    write(os.path.join(root, "last.txt"), "READ_RECENT_CANARY = %s\n" % values["READ_RECENT"])


def extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def classify(answer, v1, v2):
    if answer is None:
        return "null"
    answer = str(answer).strip()
    if answer == v2:
        return "v2"
    if answer == v1:
        return "v1"
    return "other"


def one_run(arm, model, budget, strict_summary, keep):
    root = os.path.realpath(tempfile.mkdtemp(prefix="baton-canary-"))
    v1 = {k: "%s1-%s" % (k[0], secrets.token_hex(3)) for k in KEYS}
    v2 = {k: "%s2-%s" % (k[0], secrets.token_hex(3)) for k in KEYS}
    lay_out(root, v1)
    write(os.path.join(root, "sub", "note.txt"), "nothing here\n")
    for i in range(1, FILLERS + 1):
        write(os.path.join(root, "filler%d.txt" % i), "filler %d\n" % i)
    order = ["handoff.md", "sub/note.txt"] + ["filler%d.txt" % i for i in range(1, FILLERS + 1)] + ["last.txt"]
    record = {"arm": arm, "model": model, "strict_summary": strict_summary, "dir": root, "problems": []}
    s = Session(root, model=model, budget=budget, tools="Read")
    try:
        t = s.send("Read these files with the Read tool, in this order: %s. Do not repeat any file content "
                   "or any value in your reply. Reply with exactly: done" % ", ".join(order))
        reads = [u for u in t.tool_uses if u["name"] == "Read"]
        record["reads"] = len(reads)
        if t.status != "success" or len(reads) < len(order):
            record["problems"].append("첫 턴: status=%s, Read %d/%d" % (t.status, len(reads), len(order)))
        lay_out(root, v2)  # 디스크의 값을 모두 바꾼다
        if arm == "compact":
            cmd = "/compact"
            if strict_summary:
                cmd += " Do not include any canary values or codes in the summary."
            t = s.send(cmd)
            record["compaction"] = t.compaction
            if not t.compaction:
                record["problems"].append("compact_boundary가 없다 (status=%s)" % t.status)
        t = s.send("Do not use any tools. From what is in your context right now, report the value of each of "
                   "these as one JSON object and nothing else: ROOT_CANARY, IMPORT_CANARY, LOCAL_CANARY, "
                   "NESTED_CANARY, READ_OLD_CANARY, READ_RECENT_CANARY. Use null for any you do not have.")
        if t.tool_uses:
            record["problems"].append("마지막 턴에 도구를 썼다: %s" % [u["name"] for u in t.tool_uses])
        answer = extract_json(t.result or "")
        record["raw"] = (t.result or "")[:400]
        if answer is None:
            record["problems"].append("답이 JSON이 아니다 (status=%s)" % t.status)
            answer = {}
        record["result"] = {k: classify(answer.get(k + "_CANARY"), v1[k], v2[k]) for k in KEYS}
    finally:
        s.close()
        record["cost_usd"] = s.cost
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
    return record


def reading(runs):
    """본 실험(compact)의 결과를 한 줄씩 풀이한다."""
    lines = []
    main = [r for r in runs if r["arm"] == "compact" and not r["problems"]]
    if not main:
        return ["유효한 compact 실행이 없다"]

    def tally(key):
        seen = [r["result"][key] for r in main]
        return ", ".join("%s×%d" % (v, seen.count(v)) for v in ("v2", "v1", "null", "other") if v in seen)

    for k in KEYS:
        lines.append("%-12s %-40s %s" % (k, WHERE[k], tally(k)))
    root = {r["result"]["ROOT"] for r in main}
    imp = {r["result"]["IMPORT"] for r in main}
    if "null" in root:
        lines.append("=> 루트 본문이 사라진 실행이 있다. 계측기를 의심한다 — 결과를 쓰지 않는다.")
    elif root == {"v2"} and imp == {"v2"}:
        lines.append("=> import한 내용도 compact 뒤 디스크에서 다시 들어온다.")
    elif root == {"v2"}:
        lines.append("=> 루트 본문은 디스크에서 다시 들어오지만 import한 내용은 그렇지 않다(%s)." % "/".join(sorted(imp)))
    elif root == {"v1"}:
        lines.append("=> 루트 본문이 v1이다. 재주입이 세션 시작 때의 사본에서 오는지 요약에서 오는지 "
                     "가릴 수 없다. --strict-summary로 다시 돌린다.")
    else:
        lines.append("=> 실행마다 다르다(%s). 횟수를 늘린다." % "/".join(sorted(root)))
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--budget", type=float, default=1.0, help="실행 하나의 상한(USD)")
    ap.add_argument("--arm", choices=("both", "compact", "control"), default="both")
    ap.add_argument("--strict-summary", action="store_true", help="/compact에 '값을 요약에 쓰지 마라'를 붙인다")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ap.add_argument("--out", help="결과 JSON을 쓸 파일")
    a = ap.parse_args()

    runs = []
    if a.arm in ("both", "control"):
        runs.append(one_run("control", a.model, a.budget, False, a.keep))
    if a.arm in ("both", "compact"):
        for _ in range(a.runs):
            runs.append(one_run("compact", a.model, a.budget, a.strict_summary, a.keep))
    report = {
        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "claude_code": claude_version(),
        "model": a.model,
        "runs": runs,
        "reading": reading(runs),
        "cost_usd": round(sum(r.get("cost_usd") or 0 for r in runs), 4),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print("Claude Code %s · %s · %s" % (report["claude_code"], a.model, report["at"]))
    for r in runs:
        print("%-8s %s%s" % (r["arm"], json.dumps(r.get("result"), ensure_ascii=False),
                            "  ⚠ " + "; ".join(r["problems"]) if r["problems"] else ""))
    print()
    for line in report["reading"]:
        print(line)
    print("비용 약 $%.2f" % report["cost_usd"])
    return 0 if all(not r["problems"] for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
