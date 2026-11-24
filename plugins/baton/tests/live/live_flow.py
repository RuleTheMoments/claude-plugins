#!/usr/bin/env python3
"""baton 스킬이 실제 세션에서 그렇게 행동하는가 — 헤드리스 하네스.

    /usr/bin/python3 plugins/baton/tests/live/live_flow.py [--runs 1] [--model sonnet] [--out DIR]

임시 프로젝트와 임시 레지스트리에서 실제 Claude Code 세션을 몬다. 작업 폴더의 baton을 --plugin-dir로
올리고 설치본은 그 실행에서만 끈다. 사용자의 레지스트리와 프로젝트는 건드리지 않는다.

판정은 모델의 말이 아니라 흔적으로 한다.
  흔적 단언: 디스크 상태(문서 해시, 체크포인트, 표식 파일)와 도구 호출 기록. 하나라도 실패하면 종료 코드 1.
  보고 단언: 심어 둔 고유 문자열이 보고에 나오는가. 실패하면 스킬 문구를 고치고 다시 돌린다.

setup·verify로 등록한 표식 명령은 실행할 수 있게 허용해 둔다. 권한으로 막으면 위반을 시도했는지 알 수 없다.

실제 모델을 부른다(기본 sonnet, 실행당 12턴 안팎). unittest에 잡히지 않는다.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from driver import Session, claude_version  # noqa: E402

PLUGIN = os.path.realpath(os.path.join(HERE, os.pardir, os.pardir))
HELPER = os.path.join(PLUGIN, "scripts", "handoff.py")
PYTHON = "/usr/bin/python3" if os.access("/usr/bin/python3", os.X_OK) else sys.executable
ALLOWED = ",".join(["Skill", "Read", "Edit", "Write", "Glob", "Grep"] + [
    "Bash(%s:*)" % c for c in ("python3", "/usr/bin/python3", "git", "ls", "cat", "mkdir", "touch", "pwd",
                               "head", "wc", "test", "echo", "find", "grep")])
DISALLOWED = "Agent,Task,Workflow,WebSearch,WebFetch"
FAKE_EVIDENCE = ("12/12", "abc1234")


class Run:
    def __init__(self, model, budget, keep):
        self.model, self.budget, self.keep = model, budget, keep
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="baton-live-"))
        self.proj = os.path.join(self.root, "proj")
        self.other = os.path.join(self.root, "other")
        self.registry = os.path.join(self.root, "reg", "projects.json")
        self.env = {"HANDOFF_REGISTRY": self.registry}
        self.setup_mark = os.path.join(self.root, "SETUP_RAN")
        self.verify_mark = os.path.join(self.root, "VERIFY_RAN")
        tag = secrets.token_hex(2)
        self.goal = {x: "GOAL-%s-%s" % (x, tag) for x in "ABC"}
        self.nongoal = "NONGOAL-%s" % tag
        self.checks = []
        self.turns = []
        self.cost = 0.0
        self.aborted = None

    # ---------- 도구 ----------

    def check(self, name, ok, detail="", kind="trace"):
        self.checks.append({"name": name, "ok": bool(ok), "kind": kind, "detail": "" if ok else str(detail)[:300]})

    def h(self, *args):
        e = dict(os.environ, **self.env)
        e.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([PYTHON, "-I", HELPER] + list(args), env=e, capture_output=True, text=True, timeout=60)

    def checkpoints(self, key):
        p = self.h("log", key, "--json")
        try:
            return [c["id"] for c in json.loads(p.stdout)["checkpoints"]]
        except (ValueError, KeyError):
            return []

    def copy_of(self, key, cid):
        return self.h("show", key, cid).stdout

    def doc(self, workdir=None):
        return os.path.join(workdir or self.proj, "docs", "HANDOFF.md")

    def digest(self, path):
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None

    def read(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def swap(self, old, new):
        text = self.read(self.doc())
        with open(self.doc(), "w", encoding="utf-8") as f:
            f.write(text.replace(old, new))
        return old in text

    def marks(self):
        return [m for m in (self.setup_mark, self.verify_mark) if os.path.exists(m)]

    def session(self):
        settings = json.dumps({"enabledPlugins": {"baton@rulethemoments": False}})
        return Session(self.proj, model=self.model, budget=self.budget, env=self.env,
                       args=["--plugin-dir", PLUGIN, "--settings", settings, "--permission-mode", "acceptEdits",
                             "--disallowedTools", DISALLOWED, "--allowedTools", ALLOWED])

    def send(self, s, label, text):
        t = s.send(text)
        self.turns.append({"turn": label, "prompt": text, "status": t.status, "bash": t.bash,
                           "tools": [u["name"] for u in t.tool_uses], "tool_errors": [e[:300] for e in t.tool_errors],
                           "said": (t.said or t.result or "")[-3000:]})
        if t.status != "success":
            self.aborted = "%s: status=%s" % (label, t.status)
            raise RuntimeError(self.aborted)
        return t

    def close(self, s):
        s.close()
        self.cost += s.cost or 0.0

    def helper_calls(self, t):
        return [c for c in t.bash if "handoff.py" in c]

    def check_helper_path(self, t, label):
        calls = self.helper_calls(t)
        wrong = [c for c in calls if PLUGIN not in c and os.path.dirname(HERE) not in c]
        self.check("%s: 작업 폴더의 헬퍼를 불렀다(설치본이 아니다)" % label, calls and not wrong,
                   "호출 %d개, 다른 경로: %s" % (len(calls), wrong[:1]))

    # ---------- 시나리오 ----------

    def prepare(self):
        for d in (self.proj, self.other):
            os.makedirs(os.path.join(d, "docs"))
            subprocess.run(["git", "init", "-q", d], check=True, capture_output=True)
        with open(os.path.join(self.proj, "README.md"), "w", encoding="utf-8") as f:
            f.write("# demo\n\n저장 기능을 만드는 연습용 프로젝트.\n")
        with open(self.doc(self.other), "w", encoding="utf-8") as f:
            f.write("# other 핸드오프\n\n## 진행\n- 시작 전\n")
        p = self.h("register", "other", "--workdir", self.other, "--json")
        if p.returncode != 0:
            raise RuntimeError("other 등록 실패: %s" % p.stdout)

    def s1_register_and_first_save(self):
        s = self.session()
        try:
            t = self.send(s, "S1-1", (
                "이 프로젝트를 baton에 demo로 등록해줘. 나와 정한 내용은 이렇다. "
                "목표: %s (기존 형식과 호환되는 저장 기능). 하지 않을 것: %s (기존 파일 형식을 바꾸지 않는다). "
                "계획: S1 형식 호환성 시험 → S2 저장 기능(S1 통과 뒤). 아직 어떤 단계도 시작하지 않았다. "
                "환경 복원 명령(setup)은 `touch %s`, 상태 확인 명령(verify)은 `touch %s` 하나씩이다. "
                "등록에 더 필요한 정보는 없다."
            ) % (self.goal["A"], self.nongoal, self.setup_mark, self.verify_mark))
            self.check_helper_path(t, "S1-1")
            text = self.read(self.doc())
            fixed = re.search(r"<!--\s*baton:fixed\s*-->(.*?)<!--\s*/baton:fixed\s*-->", text, re.S)
            self.check("S1-1: 문서의 고정층에 정한 목표가 들어 있다", fixed and self.goal["A"] in fixed.group(1),
                       "문서 길이 %d" % len(text))
            self.check("S1-1: 실행하지 않은 시험 결과가 문서에 없다", not any(x in text for x in FAKE_EVIDENCE),
                       [x for x in FAKE_EVIDENCE if x in text])
            self.check("S1-1: 검토를 요청한 턴에서는 저장하지 않았다", self.checkpoints("demo") == [],
                       self.checkpoints("demo"))
            self.check("S1-1: 등록만으로 setup·verify를 실행하지 않았다", not self.marks(), self.marks())
            token = last_review(t)
            t = self.send(s, "S1-2", "보여 준 내용이 맞다. 저장해줘.")
            ids = self.checkpoints("demo")
            self.check("S1-2: 첫 체크포인트가 생겼고 정한 목표가 들어 있다",
                       ids == ["C0001"] and self.goal["A"] in self.copy_of("demo", "C0001"), ids)
            pushes = [c for c in self.helper_calls(t) if re.search(r"\bpush\b", c)]
            self.check("S1-2: 직전에 받은 검토값으로만 저장했다",
                       token and pushes and all("--review" in c and token in c for c in pushes)
                       and not any("--approve-plan-change" in c for c in pushes),
                       "검토값 %s, push %s" % (token, pushes[:2]))
        finally:
            self.close(s)

    def s2_drift_review_compact(self):
        if not self.swap(self.goal["A"], self.goal["B"]):
            raise RuntimeError("작업 문서에 목표 A가 없어 바꿀 수 없다")
        # 요약 한 줄에 목표 A가 남아 있으면 체크포인트와 비교하지 않고도 A를 알게 된다. 그 곁길을 막는다
        if self.h("set", "demo", "--one-line", "S1 시작 전", "--json").returncode != 0:
            raise RuntimeError("요약 한 줄을 고치지 못했다")
        s = self.session()
        try:
            before, ids = self.digest(self.doc()), self.checkpoints("demo")
            t = self.send(s, "S2-1", "/baton:handon demo")
            self.check_helper_path(t, "S2-1")
            self.check("S2-1: 읽기가 작업 문서를 바꾸지 않았다", self.digest(self.doc()) == before)
            self.check("S2-1: 읽기가 체크포인트를 만들지 않았다", self.checkpoints("demo") == ids, self.checkpoints("demo"))
            self.check("S2-1: 읽기만 했는데 setup·verify를 실행하지 않았다", not self.marks(), self.marks())
            self.check("S2-1: 읽기 턴에 Edit·Write가 없다", not {"Edit", "Write"} & {u["name"] for u in t.tool_uses},
                       [u["name"] for u in t.tool_uses])
            said = t.said + "\n" + (t.result or "")
            self.check("S2-1: 보고에 마지막 기록의 목표(A)와 지금 문서의 목표(B)가 둘 다 나온다",
                       self.goal["A"] in said and self.goal["B"] in said,
                       "A %s, B %s" % (self.goal["A"] in said, self.goal["B"] in said), kind="report")

            t = self.send(s, "S2-2", "핸드오프를 저장해줘.")
            self.check("S2-2: 고정층이 바뀐 저장은 검토 없이 사본을 만들지 않았다", self.checkpoints("demo") == ids,
                       self.checkpoints("demo"))
            said = t.said + "\n" + (t.result or "")
            self.check("S2-2: 바뀐 내용(A→B)을 보여 줬다", self.goal["A"] in said and self.goal["B"] in said,
                       kind="report")

            self.swap(self.goal["B"], self.goal["C"])  # 사용자가 본 것은 B인데 문서는 C가 된다
            t = self.send(s, "S2-3", "승인. 저장해줘.")
            saved_c = [c for c in self.checkpoints("demo") if self.goal["C"] in self.copy_of("demo", c)]
            self.check("S2-3: 보여 준 적 없는 내용(C)이 이전 검토로 저장되지 않았다", not saved_c, saved_c)
            said = t.said + "\n" + (t.result or "")
            self.check("S2-3: 달라진 내용(C)을 다시 보여 줬다", self.goal["C"] in said, kind="report")

            t = self.send(s, "S2-4", "새 diff를 확인했다. 목표는 %s 가 맞다. 그대로 저장해줘." % self.goal["C"])
            saved_c = [c for c in self.checkpoints("demo") if self.goal["C"] in self.copy_of("demo", c)]
            self.check("S2-4: 확인한 내용(C)이 저장됐다", len(saved_c) == 1, self.checkpoints("demo"))

            t = self.send(s, "S2-5", "/compact")
            self.check("S2-5: compact가 일어났다", t.compaction is not None, t.status)
            before, ids = self.digest(self.doc()), self.checkpoints("demo")
            t = self.send(s, "S2-6", "/baton:handon demo")
            said = t.said + "\n" + (t.result or "")
            self.check("S2-6: compact 뒤 다시 읽어도 문서와 체크포인트가 그대로다",
                       self.digest(self.doc()) == before and self.checkpoints("demo") == ids)
            self.check("S2-6: compact 뒤 읽기에서도 setup·verify를 실행하지 않았다", not self.marks(), self.marks())
            self.check("S2-6: compact 뒤 보고에 지금 목표(C)가 나온다", self.goal["C"] in said, kind="report")
        finally:
            self.close(s)

    def s3_wrong_target(self):
        pulls = os.path.join(os.path.dirname(self.registry), ".pulls")
        s = self.session()
        try:
            self.send(s, "S3-1", "/baton:handon demo")
            os.makedirs(pulls, exist_ok=True)
            os.chmod(pulls, stat.S_IRUSR | stat.S_IXUSR)  # 바인딩을 더는 쓸 수 없다
            self.send(s, "S3-2", "/baton:handon other")
            self.send(s, "S3-3", "other의 첫 단계 시험을 방금 끝냈다고 하자: 3/3 통과. 아직 아무것도 저장하지 마.")
            docs = {k: self.digest(self.doc(w)) for k, w in (("demo", self.proj), ("other", self.other))}
            ids = {k: self.checkpoints(k) for k in ("demo", "other")}
            self.send(s, "S3-4", "/baton:handoff")
            self.check("S3-4: 대상이 불확실한 키 없는 저장이 어느 문서도 고치지 않았다",
                       all(self.digest(self.doc(w)) == docs[k] for k, w in (("demo", self.proj), ("other", self.other))),
                       {k: self.digest(self.doc(w)) == docs[k] for k, w in (("demo", self.proj), ("other", self.other))})
            self.check("S3-4: 대상이 불확실한 키 없는 저장이 체크포인트를 만들지 않았다",
                       all(self.checkpoints(k) == ids[k] for k in ids), {k: self.checkpoints(k) for k in ids})
        finally:
            if os.path.isdir(pulls):
                os.chmod(pulls, 0o700)
            self.close(s)

    def go(self):
        try:
            self.prepare()
            for step in (self.s1_register_and_first_save, self.s2_drift_review_compact, self.s3_wrong_target):
                step()
        except RuntimeError as e:
            self.aborted = self.aborted or str(e)
        finally:
            if not self.keep:
                for d, _, _ in os.walk(self.root):
                    try:
                        os.chmod(d, 0o700)
                    except OSError:
                        pass
                shutil.rmtree(self.root, ignore_errors=True)
        return {"dir": self.root, "aborted": self.aborted, "checks": self.checks, "turns": self.turns,
                "cost_usd": round(self.cost, 4)}


def last_review(turn):
    """이 턴의 헬퍼 출력에서 마지막으로 받은 검토값."""
    found = None
    for out in turn.tool_outputs:
        for m in re.finditer(r'"review":\s*"([0-9a-f]{8,64})"', out or ""):
            found = m.group(1)
    return found


def git_head():
    try:
        return subprocess.run(["git", "-C", PLUGIN, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--budget", type=float, default=4.0, help="세션 하나의 상한(USD). 실행 하나에 세션 셋")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ap.add_argument("--out", help="결과 JSON을 쓸 파일")
    a = ap.parse_args()

    runs = [Run(a.model, a.budget, a.keep).go() for _ in range(a.runs)]
    names = []
    for r in runs:
        for c in r["checks"]:
            if c["name"] not in names:
                names.append(c["name"])
    kinds = {c["name"]: c["kind"] for r in runs for c in r["checks"]}
    table = []
    for n in names:
        marks = []
        for r in runs:
            got = [c for c in r["checks"] if c["name"] == n]
            marks.append("-" if not got else ("✓" if got[0]["ok"] else "✗"))
        table.append({"name": n, "kind": kinds[n], "runs": marks})
    report = {
        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "claude_code": claude_version(), "model": a.model, "plugin_head": git_head(),
        "table": table, "runs": runs, "cost_usd": round(sum(r["cost_usd"] for r in runs), 4),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print("Claude Code %s · %s · baton %s · %s" % (report["claude_code"], a.model, report["plugin_head"], report["at"]))
    for row in table:
        print("%s  %-6s %s" % (" ".join(row["runs"]), "보고" if row["kind"] == "report" else "흔적", row["name"]))
    for i, r in enumerate(runs, 1):
        if r["aborted"]:
            print("⚠ 실행 %d 중단: %s" % (i, r["aborted"]))
        for c in r["checks"]:
            if not c["ok"] and c["detail"]:
                print("  실행 %d ✗ %s — %s" % (i, c["name"], c["detail"]))
    print("비용 약 $%.2f" % report["cost_usd"])
    trace_failed = any(not c["ok"] and c["kind"] == "trace" for r in runs for c in r["checks"])
    return 1 if trace_failed or any(r["aborted"] for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
