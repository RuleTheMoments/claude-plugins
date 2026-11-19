#!/usr/bin/env python3
"""baton 플러그인 도우미. 표준 라이브러리만 쓴다(macOS /usr/bin/python3 3.9 호환).

  list     [--json]         등록된 프로젝트를 Claude 실행 폴더 기준으로 나눠 보여준다
  resolve  [NAME]           이름(생략하면 실행 폴더)으로 프로젝트 하나를 고른다 — JSON
  current  [--json]         현재 세션 ID·이름·실행 폴더·트랜스크립트 폴더
  title    NAME             현재 세션 이름을 NAME으로 — 다음 입력 때 훅이 적용한다
  stamp    NAME             레지스트리 엔트리의 updated·last_session_id·transcript_dir를 현재 세션으로
  hook                      UserPromptSubmit 훅(stdin JSON). /handon <이름> 이면 세션 이름을 붙인다

공통 옵션: --session-id ID (기본 $CLAUDE_CODE_SESSION_ID), --cwd DIR (기본 현재 디렉터리)

"실행 폴더"는 claude를 띄운 폴더다. 세션 트랜스크립트는 ~/.claude/projects/<실행 폴더를 -로 바꾼 이름>/ 에
쌓이고, `claude --resume <id>`는 같은 폴더에서 띄웠을 때만 그 세션을 찾는다.
"""
import os
import sys

HOME = os.path.expanduser("~")
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
PROJECTS_ROOT = os.path.join(CONFIG_DIR, "projects")
REGISTRY = os.environ.get("HANDOFF_REGISTRY") or os.path.join(HOME, ".claude", "handoffs", "projects.json")
PENDING_DIR = os.path.join(os.path.dirname(REGISTRY), ".pending-titles")
PENDING_TTL = 7 * 24 * 3600

# 훅은 입력마다 돈다. /handon도 대기 중인 이름도 없으면 나머지 import 전에 끝낸다(~45ms vs ~130ms).
HOOK_STDIN = None
if __name__ == "__main__" and sys.argv[1:] == ["hook"]:
    try:
        HOOK_STDIN = sys.stdin.buffer.read().decode("utf-8", "replace")
        idle = "handon" not in HOOK_STDIN and not os.listdir(PENDING_DIR)
    except Exception:
        idle = "handon" not in (HOOK_STDIN or "")
    if idle:
        sys.exit(0)

import argparse  # noqa: E402
import datetime  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

SID_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")
# /handon, /handon myapp, /baton:handon myapp — 인자는 첫 줄 나머지
SLASH_RE = re.compile(r"^\s*/(?:[\w.-]+:)?handon(?![\w-])(?:[ \t]+([^\r\n]*))?")
# 슬래시 명령이 펼쳐진 형태로 들어올 때
TAG_RE = re.compile(r"<command-name>/?(?:[\w.-]+:)?handon</command-name>")
ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
LIST_ARGS = {"list", "ls", "목록"}


# ---------- 경로·트랜스크립트 ----------

def norm(p):
    return os.path.realpath(os.path.expanduser(p)) if p else None


def encode_dir(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def find_transcript(sid):
    if not sid or not SID_RE.match(sid):
        return None
    hits = glob.glob(os.path.join(PROJECTS_ROOT, "*", sid + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def first_cwd(path, limit=400):
    """트랜스크립트에서 처음 나오는 cwd = 그 세션을 띄운 폴더."""
    try:
        with open(path, "rb") as f:
            for i, line in enumerate(f):
                if i >= limit:
                    break
                if b'"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except (ValueError, AttributeError):
                    continue
                if cwd:
                    return norm(cwd)
    except OSError:
        pass
    return None


def last_title(path):
    """세션 이름(= /rename 또는 훅이 붙인 이름). <세션>/custom-title.json 사이드카, 없으면 마지막 custom-title 레코드."""
    try:
        with open(os.path.join(path[: -len(".jsonl")], "custom-title.json"), encoding="utf-8") as f:
            title = json.load(f).get("customTitle")
        if title:
            return title
    except (OSError, ValueError, AttributeError):
        pass
    title = None
    try:
        with open(path, "rb") as f:
            for line in f:
                if b'"custom-title"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "custom-title":
                    title = rec.get("customTitle") or None
    except OSError:
        pass
    return title


# ---------- 대기 중인 세션 이름 ----------

def _pending_path(sid):
    return os.path.join(PENDING_DIR, sid) if sid and SID_RE.match(sid) else None


def read_pending(sid):
    p = _pending_path(sid)
    if not p:
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip() or None
    except OSError:
        return None


def clear_pending(sid):
    p = _pending_path(sid)
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def pop_pending(sid):
    """내용이 비었어도 파일은 지운다 — 남으면 모든 세션의 훅이 빠른 경로를 못 탄다."""
    title = read_pending(sid)
    clear_pending(sid)
    return title


def write_pending(sid, title):
    os.makedirs(PENDING_DIR, exist_ok=True)
    now = time.time()
    for old in glob.glob(os.path.join(PENDING_DIR, "*")):
        try:
            if now - os.path.getmtime(old) > PENDING_TTL:
                os.remove(old)
        except OSError:
            pass
    with open(_pending_path(sid), "w", encoding="utf-8") as f:
        f.write(title + "\n")


# ---------- 레지스트리 ----------

def load_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"projects": {}}  # 처음 쓰는 사람: 등록 전에도 list·resolve가 동작한다


def projects_of(reg):
    projects = reg.get("projects") if isinstance(reg, dict) else None
    if not isinstance(projects, dict):
        return {}
    return {k: e for k, e in projects.items() if isinstance(e, dict)}


def write_registry(reg):
    target = os.path.realpath(REGISTRY)  # 심링크면 링크가 아니라 대상을 바꾼다
    mode = stat.S_IMODE(os.stat(target).st_mode)
    shutil.copy2(target, target + ".bak")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".projects-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------- 현재 세션·프로젝트 상태 ----------

def current_context(sid=None, cwd=None, transcript=None, want_title=True):
    sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    hinted_dir = os.path.dirname(transcript) if transcript else None
    if not (transcript and os.path.isfile(transcript)):
        transcript = find_transcript(sid)
    cwd = norm(cwd) or os.getcwd()
    if transcript:
        project_dir = os.path.dirname(transcript)
        launch = first_cwd(transcript) or cwd
    else:
        # 첫 입력 전이라 트랜스크립트가 아직 없을 때 — 훅이 준 경로가 있으면 그 폴더
        launch = cwd
        project_dir = hinted_dir or os.path.join(PROJECTS_ROOT, encode_dir(launch))
    return {
        "session_id": sid,
        "transcript": transcript,
        "project_dir": project_dir,
        "launch_cwd": launch,
        "title": last_title(transcript) if (want_title and transcript) else None,
        "pending_title": read_pending(sid),
    }


def _inside(path, root):
    return bool(path and root) and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def project_status(key, entry, ctx):
    workdir = norm(entry.get("workdir"))
    doc = os.path.join(workdir, entry["handoff_doc"]) if workdir and entry.get("handoff_doc") else None
    reg_dir = norm(entry.get("transcript_dir"))
    last = entry.get("last_session_id")
    last_path = find_transcript(last)
    last_dir = os.path.dirname(last_path) if last_path else None
    here_dir = ctx["project_dir"]

    if here_dir in (reg_dir, last_dir):
        where = "here"
    elif _inside(ctx["launch_cwd"], workdir):
        where = "inside"
    else:
        where = "elsewhere"

    issues = []
    if not (workdir and os.path.isdir(workdir)):
        issues.append("workdir 없음: %s" % workdir)
    elif doc and not os.path.isfile(doc):
        issues.append("핸드오프 문서 없음: %s" % doc)
    if last and not last_path:
        issues.append("마지막 세션 %s 트랜스크립트 없음" % last[:8])
    if last_dir and reg_dir and last_dir != reg_dir:
        issues.append("transcript_dir 불일치 — 등록 %s / 마지막 세션 실제 %s"
                      % (os.path.basename(reg_dir), os.path.basename(last_dir)))

    return {
        "key": key,
        "where": where,
        "registered_here": reg_dir == here_dir,
        "updated": entry.get("updated") or "",
        "one_line": entry.get("one_line") or "",
        "workdir": workdir,
        "handoff_doc": doc,
        "last_session_id": last,
        "resumable": bool(last_dir and last_dir == here_dir),
        "run_from": first_cwd(last_path) if last_path else None,
        "issues": issues,
    }


def match_name(name, projects):
    """키·별칭 대조(대소문자 무시). 별칭에 공백이 있을 수 있어 전체 문자열 먼저, 안 되면 첫 단어."""
    words = (name or "").split()
    for cand in (" ".join(words), words[0] if words else ""):
        n = cand.casefold()
        if not n:
            continue
        for key, entry in projects.items():
            aliases = entry.get("aliases") or []
            if n == key.casefold() or any(isinstance(a, str) and n == a.casefold() for a in aliases):
                return key
    return None


def resolve(name, reg, ctx):
    projects = projects_of(reg)
    if name and name.strip():
        key = match_name(name, projects)
        if key:
            return {"match": key, "by": "name"}
        return {"match": None, "by": "name", "candidates": [], "reason": "등록되지 않은 이름: %s" % name.strip()}

    states = [project_status(k, e, ctx) for k, e in projects.items()]
    for tier in ("here", "inside"):
        cands = [s for s in states if s["where"] == tier]
        if tier == "here" and len(cands) > 1:
            # 이 폴더 세션이 여럿이면: 레지스트리가 이 폴더로 등록한 쪽 → 실행 폴더가 workdir 안인 쪽
            for keep in (lambda s: s["registered_here"], lambda s: _inside(ctx["launch_cwd"], s["workdir"])):
                narrowed = [s for s in cands if keep(s)]
                if narrowed:
                    cands = narrowed
                if len(cands) == 1:
                    break
        if len(cands) == 1:
            return {"match": cands[0]["key"], "by": tier}
        if cands:
            return {"match": None, "by": tier, "candidates": [s["key"] for s in cands]}
    return {"match": None, "by": "none", "candidates": []}


# ---------- 출력 ----------

def short(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def describe_session(ctx):
    sid = ctx["session_id"]
    if not sid:
        return "알 수 없음"
    name = '이름 "%s"' % ctx["title"] if ctx["title"] else "이름 없음"
    s = "%s · %s" % (sid[:8], name)
    if ctx["pending_title"] and ctx["pending_title"] != ctx["title"]:
        s += ' (대기: "%s" — 다음 입력 때 적용)' % ctx["pending_title"]
    return s


GROUPS = [
    ("here", "● 이 폴더에서 이어받기·갱신"),
    ("inside", "◐ 작업 폴더 안에서 실행됨 — 지난 세션 resume은 다른 폴더"),
    ("elsewhere", "○ 다른 폴더에서 실행해야 함"),
]


def print_list(ctx, states):
    print("실행 폴더  %s" % ctx["launch_cwd"])
    print("현재 세션  %s" % describe_session(ctx))
    if not states:
        print("\n등록된 프로젝트가 없다 (%s)" % REGISTRY)
        return
    width = max(len(s["key"]) for s in states)
    pad = " " * width
    for tier, head in GROUPS:
        items = sorted((s for s in states if s["where"] == tier), key=lambda s: s["updated"], reverse=True)
        if not items:
            continue
        print("\n%s (%d)" % (head, len(items)))
        for s in items:
            if tier == "here":
                if s["resumable"]:
                    tail = "resume %s" % s["last_session_id"]
                else:
                    tail = "resume ✗ (지난 세션은 %s)" % (s["run_from"] or "?")
            else:
                tail = "→ %s 에서 실행" % (s["run_from"] or s["workdir"])
            print("  %-*s  %s  %s" % (width, s["key"], s["updated"] or "----------", tail))
            if s["one_line"]:
                print("  %s  %s" % (pad, short(s["one_line"], 90)))
    flagged = [s for s in states if s["issues"]]
    if flagged:
        print("\n⚠ 점검")
        for s in flagged:
            for issue in s["issues"]:
                print("  %-*s  %s" % (width, s["key"], issue))


# ---------- 명령 ----------

def cmd_list(a):
    ctx = current_context(a.session_id, a.cwd)
    states = [project_status(k, e, ctx) for k, e in projects_of(load_registry()).items()]
    if a.json:
        json.dump({"context": ctx, "projects": states}, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_list(ctx, states)
    return 0


def cmd_resolve(a):
    ctx = current_context(a.session_id, a.cwd, want_title=False)
    json.dump(resolve(" ".join(a.name), load_registry(), ctx), sys.stdout, ensure_ascii=False)
    print()
    return 0


def cmd_current(a):
    ctx = current_context(a.session_id, a.cwd)
    if a.json:
        json.dump(ctx, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print("세션       %s" % describe_session(ctx))
        print("세션 ID    %s" % (ctx["session_id"] or "?"))
        print("실행 폴더  %s" % ctx["launch_cwd"])
        print("트랜스크립트 폴더  %s" % ctx["project_dir"])
    return 0


def cmd_title(a):
    name = " ".join(a.name).strip()
    if not name:
        print("이름이 비었다", file=sys.stderr)
        return 1
    ctx = current_context(a.session_id, a.cwd)
    sid = ctx["session_id"]
    if not (sid and SID_RE.match(sid)):
        print("세션 ID를 알 수 없다 — /rename %s 로 직접 붙인다" % name)
        return 1
    if ctx["title"] == name:
        clear_pending(sid)  # 예전에 대기시킨 다른 이름이 덮어쓰지 않게
        print("세션 이름: %s (설정됨 · %s)" % (name, sid[:8]))
        return 0
    write_pending(sid, name)
    print("세션 이름: %s (다음 입력 때 적용 · 지금 %s · %s)" % (name, ctx["title"] or "이름 없음", sid[:8]))
    return 0


def cmd_stamp(a):
    reg = load_registry()
    ctx = current_context(a.session_id, a.cwd, want_title=False)
    sid = ctx["session_id"]
    if not (sid and SID_RE.match(sid)):
        print("세션 ID를 알 수 없다 — --session-id로 준다", file=sys.stderr)
        return 1
    if not ctx["transcript"]:
        # 트랜스크립트 폴더를 추측해서 쓰지 않는다(cwd는 cd로 바뀌었을 수 있다)
        print("세션 %s의 트랜스크립트를 %s 아래에서 찾지 못했다 — 기록하지 않았다" % (sid[:8], PROJECTS_ROOT), file=sys.stderr)
        return 1
    r = resolve(" ".join(a.name), reg, ctx)
    if not r["match"]:
        print(r.get("reason") or "프로젝트를 찾지 못했다", file=sys.stderr)
        return 1
    entry = reg["projects"][r["match"]]
    changes = []
    for field, value in (
        ("updated", datetime.date.today().isoformat()),
        ("last_session_id", sid),
        ("transcript_dir", ctx["project_dir"]),
    ):
        if entry.get(field) != value:
            changes.append((field, entry.get(field), value))
            entry[field] = value
    if changes:
        write_registry(reg)
    print("%s: %s" % (r["match"], "바뀐 것 없음" if not changes else "갱신"))
    for field, old, new in changes:
        print("  %s: %s → %s" % (field, old, new))
    return 0


def cmd_hook(_a):
    try:
        data = json.loads(HOOK_STDIN if HOOK_STDIN is not None else sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(data, dict) or data.get("hook_event_name", "UserPromptSubmit") != "UserPromptSubmit":
        return 0
    sid = data.get("session_id")
    prompt = data.get("prompt") or ""

    hit, arg = False, None
    m = SLASH_RE.match(prompt)
    if m:
        hit, arg = True, m.group(1)
    elif TAG_RE.search(prompt):
        hit = True
        am = ARGS_RE.search(prompt)
        arg = am.group(1) if am else None

    title = None
    first = (arg or "").split()[:1]
    if hit and not (first and first[0].casefold() in LIST_ARGS):
        try:
            ctx = current_context(sid, data.get("cwd"), data.get("transcript_path"), want_title=False)
            title = resolve(arg, load_registry(), ctx).get("match")
        except Exception:
            title = None  # 레지스트리가 깨져도 대기 중인 이름은 적용한다

    pending = pop_pending(sid)
    title = title or pending
    if not title:
        return 0
    note = "[baton] 이 세션 이름을 '%s'(으)로 붙였다 (세션 %s)." % (title, (sid or "?")[:8])
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "sessionTitle": title,
            "additionalContext": note,
        }
    }))
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session-id")
    common.add_argument("--cwd")
    p = argparse.ArgumentParser(prog="handoff.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("list", parents=[common])
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("resolve", parents=[common])
    sp.add_argument("name", nargs="*")
    sp = sub.add_parser("current", parents=[common])
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("title", parents=[common])
    sp.add_argument("name", nargs="+")
    sp = sub.add_parser("stamp", parents=[common])
    sp.add_argument("name", nargs="+")
    sub.add_parser("hook")
    a = p.parse_args(argv)
    handlers = {"list": cmd_list, "resolve": cmd_resolve, "current": cmd_current,
                "title": cmd_title, "stamp": cmd_stamp, "hook": cmd_hook}
    if a.cmd not in handlers:
        p.print_help()
        return 2
    if a.cmd == "hook":
        # 훅은 무슨 일이 있어도 프롬프트를 막지 않는다
        try:
            return cmd_hook(a)
        except Exception:
            return 0
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
