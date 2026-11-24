#!/usr/bin/env python3
"""baton 플러그인 도우미. 표준 라이브러리만 쓴다(macOS /usr/bin/python3 3.9 호환).

  register KEY --workdir DIR [...]        새 프로젝트를 레지스트리에 등록한다
  set      KEY [--one-line T ...]          레지스트리 엔트리의 요약 필드를 고친다
  pull     [KEY]                           이어받을 프로젝트를 정하고 읽은 체크포인트를 이 세션에 기록한다.
                                           프로젝트 명령은 실행하지 않는다
  push     [KEY] [--approve-plan-change]   핸드오프 문서의 체크포인트를 남기고 레지스트리를 갱신한다
  log      KEY                             체크포인트 목록
  show     KEY ID [--against ID2]          그 체크포인트의 문서, 또는 두 체크포인트의 diff
  list     [--json]                        등록된 프로젝트를 Claude 실행 폴더 기준으로 나눠 보여준다
  resolve  [NAME]                          이름(생략하면 실행 폴더)으로 프로젝트 하나를 고른다 — JSON
  current  [--json]                        현재 세션 ID·이름·실행 폴더·트랜스크립트 폴더
  title    NAME                            현재 세션 이름을 NAME으로 — 다음 입력 때 훅이 적용한다
  hook                                     UserPromptSubmit 훅(stdin JSON). /handon <이름>이면 세션 이름을 붙인다

공통 옵션: --session-id ID (기본 $CLAUDE_CODE_SESSION_ID), --cwd DIR (기본 현재 디렉터리), --json
--json 결과의 status가 ok·unchanged·repaired면 종료 코드 0, 그 밖(error·needs_target·needs_approval·
conflict·partial)은 1이다.

"실행 폴더"는 claude를 띄운 폴더다. 세션 트랜스크립트는 ~/.claude/projects/<실행 폴더를 -로 바꾼 이름>/ 에 쌓인다.
체크포인트는 <workdir>/.baton/checkpoints/ 에, 세션별 바인딩은 레지스트리 옆 .pulls/ 에 둔다.
"""
import os
import sys
import time

HOME = os.path.expanduser("~")
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
PROJECTS_ROOT = os.path.join(CONFIG_DIR, "projects")
REGISTRY = os.path.expanduser(os.environ.get("HANDOFF_REGISTRY") or os.path.join(HOME, ".claude", "handoffs", "projects.json"))
# 상대 경로면 명령을 띄운 폴더마다 다른 레지스트리가 된다 — 명령은 거부하고 훅은 조용히 끝낸다
REGISTRY_RELATIVE = not os.path.isabs(REGISTRY)
STATE_DIR = os.path.dirname(REGISTRY)
PENDING_DIR = os.path.join(STATE_DIR, ".pending-titles")
BINDING_DIR = os.path.join(STATE_DIR, ".pulls")
PENDING_TTL = 7 * 24 * 3600
BINDING_TTL = 30 * 24 * 3600  # Claude Code가 트랜스크립트를 지우는 기본 주기(cleanupPeriodDays)와 같다


def valid_sid(s):
    return isinstance(s, str) and 8 <= len(s) <= 64 and all(c.isascii() and (c.isalnum() or c == "-") for c in s)


def sweep_pending(sid=None):
    """만료된 세션 이름 예약을 지우고, sid의 예약이 남아 있는지 돌려준다."""
    try:
        names = os.listdir(PENDING_DIR)
    except OSError:
        return False
    now = time.time()
    own = False
    for name in names:
        path = os.path.join(PENDING_DIR, name)
        try:
            if now - os.path.getmtime(path) > PENDING_TTL:
                os.remove(path)
                continue
        except OSError:
            continue
        if name == sid and os.path.isfile(path):
            own = True
    return own


def quick_sid(text):
    """훅 입력에서 session_id만 꺼낸다. json·re를 import하지 않으려고 문자열로 찾는다."""
    i = text.find('"session_id"')
    if i < 0:
        return None
    start = text.find('"', text.find(":", i) + 1)
    end = text.find('"', start + 1)
    value = text[start + 1:end] if 0 <= start < end else None
    return value if valid_sid(value) else None


# 훅은 입력마다 돈다. /handon이 아니고 이 세션의 예약도 없으면 나머지 import 전에 끝낸다.
HOOK_STDIN = None
if __name__ == "__main__" and sys.argv[1:] == ["hook"]:
    try:
        HOOK_STDIN = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        HOOK_STDIN = ""
    if REGISTRY_RELATIVE:
        sys.exit(0)
    if "handon" not in HOOK_STDIN:
        try:
            _waiting = bool(os.listdir(PENDING_DIR))
        except OSError:
            _waiting = False
        if not _waiting or not sweep_pending(quick_sid(HOOK_STDIN)):
            sys.exit(0)

import argparse  # noqa: E402
import contextlib  # noqa: E402
import datetime  # noqa: E402
import difflib  # noqa: E402
import fcntl  # noqa: E402
import glob  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402,F811
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

DOC_DEFAULT = os.path.join("docs", "HANDOFF.md")
RESERVED = {"list", "ls", "목록"}
SUCCESS = {"ok", "unchanged", "repaired"}
LIST_FIELDS = ("aliases", "setup", "verify", "warnings")
CLEARABLE = ("one_line", "plan_doc") + LIST_FIELDS
KEY_RE = re.compile(r"[^\s/\\:]{1,64}")  # fullmatch로만 쓴다 — $는 끝의 줄바꿈 앞에서도 맞는다
SLUG_RE = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣._-]{0,63}")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CP_RE = re.compile(r"^C(\d+)$")
FIXED_START_RE = re.compile(r"<!--\s*baton:fixed\s*-->")
FIXED_END_RE = re.compile(r"<!--\s*/baton:fixed\s*-->")
# /handon, /handon myapp, /baton:handon myapp — 인자는 첫 줄 나머지. 다른 플러그인의 handon에는 반응하지 않는다
SLASH_RE = re.compile(r"^\s*/(?:baton:)?handon(?![\w-])(?:[ \t]+([^\r\n]*))?")
TAG_RE = re.compile(r"<command-name>/?(?:baton:)?handon</command-name>")
ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)


class Fail(Exception):
    """명령을 끝내는 결과. status가 SUCCESS에 없으면 종료 코드 1."""

    def __init__(self, status, message, code=None, **extra):
        super().__init__(message)
        self.result = {"status": status}
        if code:
            self.result["error"] = code
        self.result["message"] = message
        self.result.update(extra)


def error(code, message, **extra):
    return Fail("error", message, code, **extra)


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- 경로·트랜스크립트 ----------

def norm(p):
    return os.path.realpath(os.path.expanduser(p)) if isinstance(p, str) and p else None


def encode_dir(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def inside(path, root):
    return bool(path and root) and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def find_transcript(sid):
    if not valid_sid(sid):
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
                if isinstance(cwd, str) and cwd:
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


def current_context(sid=None, cwd=None, transcript=None, want_title=True):
    sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    hinted_dir = norm(os.path.dirname(transcript)) if transcript else None
    if not (transcript and os.path.isfile(transcript)):
        transcript = find_transcript(sid)
    cwd = norm(cwd) or norm(os.getcwd())
    if transcript:
        project_dir = norm(os.path.dirname(transcript))
        launch = first_cwd(transcript) or cwd
    else:
        # 첫 입력 전이라 트랜스크립트가 아직 없을 때 — 훅이 준 경로가 있으면 그 폴더
        launch = cwd
        project_dir = hinted_dir or norm(os.path.join(PROJECTS_ROOT, encode_dir(launch)))
    return {
        "session_id": sid,
        "transcript": transcript,
        "project_dir": project_dir,
        "launch_cwd": launch,
        "title": last_title(transcript) if (want_title and transcript) else None,
        "pending_title": read_pending(sid),
    }


def transcript_available(transcript_dir, session):
    return bool(transcript_dir and valid_sid(session)
                and os.path.isfile(os.path.join(transcript_dir, session + ".jsonl")))


# ---------- 파일 쓰기·잠금 ----------

def atomic_write(path, data, mode=0o644):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".baton-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def dump_json(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def registry_target():
    return os.path.realpath(REGISTRY)  # 심링크면 링크가 아니라 대상을 바꾼다


@contextlib.contextmanager
def registry_lock():
    """모든 쓰기는 이 잠금 하나로 직렬화한다 — 읽기부터 교체까지."""
    path = registry_target() + ".lock"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise error("lock_failed", "레지스트리 잠금을 얻지 못했다 (%s): %s" % (path, e))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ---------- 레지스트리 ----------

def registry_shape_ok(data):
    """파일이 있으면 projects 객체가 있어야 한다. 키가 빠진 {}를 빈 레지스트리로 받아들이면
    다음 쓰기가 마지막 정상 백업을 그 {}로 바꾼다."""
    return isinstance(data, dict) and isinstance(data.get("projects"), dict)


def backup_info():
    path = registry_target() + ".bak"
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            valid = registry_shape_ok(json.loads(f.read().decode("utf-8")))
    except (OSError, ValueError):
        valid = False
    return {"path": path, "valid": valid}


def load_registry():
    try:
        with open(REGISTRY, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {"projects": {}}  # 처음 쓰는 사람: 등록 전에도 list·resolve가 동작한다
    except OSError as e:
        raise error("registry_unreadable", "레지스트리를 읽지 못했다 (%s): %s" % (REGISTRY, e))
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        data, why = None, str(e)
    else:
        why = "최상위가 {\"projects\": {...}} 형식이 아니다"
    if not registry_shape_ok(data):
        backup = backup_info()
        hint = (" — %s 은 읽을 수 있다. 사용자에게 확인한 뒤 복구한다" % backup["path"]) if backup and backup["valid"] else ""
        raise error("registry_invalid", "레지스트리가 깨졌다 (%s): %s%s" % (REGISTRY, why, hint), backup=backup)
    return data


def write_registry(reg):
    """백업과 본문 모두 임시 파일을 쓴 뒤 교체한다. 제자리에서 고치지 않으므로
    읽기 전용이 된 .bak 때문에 이후 쓰기가 막히지 않는다."""
    target = registry_target()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    mode = 0o600
    old = read_bytes(target)
    if old is not None:
        mode = stat.S_IMODE(os.stat(target).st_mode) | stat.S_IWUSR
        atomic_write(target + ".bak", old, mode)
    atomic_write(target, dump_json(reg), mode)


def list_field(entry, field):
    """목록 필드. 0.3.0에서 손으로 쓴 문자열 하나도 한 항목짜리 목록으로 읽는다."""
    v = entry.get(field) if isinstance(entry, dict) else None
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def aliases_of(entry):
    return {a.casefold() for a in list_field(entry, "aliases") if a.strip()}


def rel_inside(rel):
    """workdir 안을 가리키는 상대 경로인가."""
    if not (isinstance(rel, str) and rel) or "\0" in rel:
        return False
    rel = os.path.normpath(rel)
    return not (os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep))


def entry_problems(entry):
    if not isinstance(entry, dict):
        return ["엔트리가 객체가 아니다"]
    problems = []
    workdir = entry.get("workdir")
    if not (isinstance(workdir, str) and workdir):
        problems.append("workdir 없음 또는 문자열이 아님")
    elif not os.path.isabs(os.path.expanduser(workdir)):
        problems.append("workdir가 절대 경로가 아님: %s" % workdir)
    if not (isinstance(entry.get("handoff_doc"), str) and entry.get("handoff_doc")):
        problems.append("handoff_doc 없음 또는 문자열이 아님")
    elif not rel_inside(entry["handoff_doc"]):
        problems.append("handoff_doc이 workdir 안의 상대 경로가 아님: %s" % entry["handoff_doc"])
    if entry.get("plan_doc") is not None and not (isinstance(entry["plan_doc"], str)
                                                  and (not entry["plan_doc"] or rel_inside(entry["plan_doc"]))):
        problems.append("plan_doc이 workdir 안의 상대 경로가 아님: %r" % (entry["plan_doc"],))
    for field in LIST_FIELDS:
        v = entry.get(field)
        if v is not None and not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))):
            problems.append("%s가 문자열 목록이 아님" % field)
    for field in ("one_line", "last_session_id", "transcript_dir", "latest_checkpoint", "updated"):
        v = entry.get(field)
        if v is not None and not isinstance(v, str):
            problems.append("%s가 문자열이 아님" % field)
    return problems


def key_slug(key):
    """체크포인트 폴더 이름. 키가 파일 이름으로 안전하지 않으면 해시로 바꾼다."""
    if SLUG_RE.fullmatch(key):
        return key
    return "k-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Project:
    def __init__(self, key, entry):
        self.key = key
        self.entry = entry
        self.workdir = norm(entry["workdir"])
        self.doc_rel = os.path.normpath(entry["handoff_doc"])
        self.doc = os.path.join(self.workdir, self.doc_rel)
        self.plan_rel = os.path.normpath(entry["plan_doc"]) if entry.get("plan_doc") else None
        self.plan = os.path.join(self.workdir, self.plan_rel) if self.plan_rel else None
        self.baton = os.path.join(self.workdir, ".baton")
        self.cp_root = os.path.join(self.baton, "checkpoints")
        # 한 폴더에 키를 여럿 등록해도(모노레포) 이력이 섞이지 않게 키마다 따로 둔다
        self.cp_dir = os.path.join(self.cp_root, key_slug(key))
        self.index_path = os.path.join(self.cp_dir, "index.json")


def get_project(reg, key):
    entry = reg["projects"][key]
    problems = entry_problems(entry)
    if problems:
        raise error("invalid_entry", "%s 엔트리가 잘못됐다: %s" % (key, "; ".join(problems)), key=key, issues=problems)
    return Project(key, entry)


def lookup(name, projects):
    """이름 → (키, 후보, 모호함). 정확한 키(대소문자 무시) → 유일한 별칭.
    별칭이 여럿과 겹치거나 단어가 남으면 고르지 않고 후보로만 돌려준다."""
    words = (name or "").split()
    if not words:
        return None, [], False
    joined = " ".join(words)
    if joined in projects:
        return joined, [], False
    full = joined.casefold()
    folded = sorted(k for k in projects if k.casefold() == full)
    if len(folded) == 1:
        return folded[0], [], False
    if folded:
        return None, folded, True  # 대소문자만 다른 키가 여럿이면 고르지 않는다
    by_alias = sorted(k for k, e in projects.items() if full in aliases_of(e))
    if len(by_alias) == 1:
        return by_alias[0], [], False
    if by_alias:
        return None, by_alias, True
    if len(words) > 1:
        first = words[0].casefold()
        near = sorted(k for k, e in projects.items() if first == k.casefold() or first in aliases_of(e))
        return None, near, False
    return None, [], False


def transcript_dirs(entry):
    dirs = {norm(entry.get("transcript_dir"))}
    last = find_transcript(entry.get("last_session_id"))
    if last:
        dirs.add(norm(os.path.dirname(last)))
    dirs.discard(None)
    return dirs


def folder_candidates(projects, ctx):
    """실행 폴더로 고르기 → (키 또는 None, 후보, 근거).
    실행 폴더를 포함하는 가장 깊은 workdir가 1순위, 트랜스크립트 폴더 일치가 그다음."""
    launch = ctx["launch_cwd"]
    found = []
    for key, entry in projects.items():
        if entry_problems(entry):
            continue
        workdir = norm(entry["workdir"])
        if inside(launch, workdir):
            found.append((len(workdir), key))
    if found:
        found.sort(key=lambda t: (-t[0], t[1]))
        deepest = found[0][0]
        best = [k for d, k in found if d == deepest]
        ordered = [k for _, k in found]
        return (best[0] if len(best) == 1 else None), ordered, "workdir"
    here = ctx["project_dir"]
    by_transcript = sorted(k for k, e in projects.items() if not entry_problems(e) and here in transcript_dirs(e))
    if len(by_transcript) == 1:
        return by_transcript[0], by_transcript, "transcript"
    return None, by_transcript, ("transcript" if by_transcript else "none")


def check_names(projects, key, aliases, exclude=None):
    keys = {k.casefold() for k in projects if k != exclude}
    taken = set()
    for k, e in projects.items():
        if k != exclude:
            taken |= aliases_of(e)
    if key is not None:
        if key.casefold() in keys:
            raise error("key_exists", "이미 등록된 키: %s" % key)
        if key.casefold() in taken:
            raise error("alias_conflict", "다른 프로젝트의 별칭과 겹친다: %s" % key)
    for alias in aliases:
        c = alias.casefold()
        if c in RESERVED:
            raise error("reserved_key", "예약어는 별칭으로 쓸 수 없다: %s" % alias)
        if c in keys or c in taken:
            raise error("alias_conflict", "다른 프로젝트의 키·별칭과 겹친다: %s" % alias)


def clean_list(values):
    out = []
    for v in values or []:
        v = v.strip()
        if v and v not in out:
            out.append(v)
    return out


def check_rel(workdir, rel, missing_code, label):
    if not rel_inside(rel):
        raise error("invalid_path", "%s는 workdir 안의 상대 경로여야 한다: %s" % (label, rel))
    rel = os.path.normpath(rel)
    if not os.path.isfile(os.path.join(workdir, rel)):
        raise error(missing_code, "%s가 없다: %s" % (label, os.path.join(workdir, rel)))
    return rel


# ---------- 세션 이름 예약 ----------

def pending_path(sid):
    return os.path.join(PENDING_DIR, sid) if valid_sid(sid) else None


def read_pending(sid):
    p = pending_path(sid)
    if not p:
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip() or None
    except OSError:
        return None


def clear_pending(sid):
    p = pending_path(sid)
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def pop_pending(sid):
    """내용이 비었어도 파일은 지운다 — 남으면 이 세션의 훅이 빠른 경로를 못 탄다."""
    title = read_pending(sid)
    clear_pending(sid)
    return title


def write_pending(sid, title):
    os.makedirs(PENDING_DIR, exist_ok=True)
    sweep_pending()
    with open(pending_path(sid), "w", encoding="utf-8") as f:
        f.write(title + "\n")


# ---------- 세션 바인딩 ----------

def binding_path(sid):
    return os.path.join(BINDING_DIR, sid + ".json") if valid_sid(sid) else None


def read_binding(sid):
    p = binding_path(sid)
    if not p:
        return {}
    try:
        with open(p, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    bases = data.get("bases") if isinstance(data.get("bases"), dict) else {}
    data["bases"] = {k: v for k, v in bases.items() if isinstance(v, dict)}
    return data


def base_of(meta):
    """바인딩에 남기는 기준. ID만으로는 .baton이 지워진 뒤 다시 매긴 같은 번호와 구별하지 못한다."""
    return {"id": meta["id"], "hash": meta.get("doc_hash")} if meta else {"id": None, "hash": None}


def write_binding(sid, key, base):
    """이 세션이 마지막으로 pull·push한 프로젝트와 그때의 체크포인트. 실패해도 명령은 계속한다."""
    p = binding_path(sid)
    if not p:
        return False
    data = read_binding(sid)
    bases = data.get("bases") or {}
    bases[key] = base
    data = {"session_id": sid, "current": key, "bases": bases, "updated": now_iso()}
    try:
        os.makedirs(BINDING_DIR, exist_ok=True)
        now = time.time()
        for old in glob.glob(os.path.join(BINDING_DIR, "*.json")):
            try:
                if now - os.path.getmtime(old) > BINDING_TTL:
                    os.remove(old)
            except OSError:
                pass
        atomic_write(p, dump_json(data), 0o600)
        return True
    except OSError:
        return False


# ---------- 체크포인트 ----------

def sha256(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def exact_text(data):
    """비교용. UTF-8이 아닌 바이트도 잃지 않는다(surrogateescape)."""
    return data.decode("utf-8", "surrogateescape") if data is not None else None


def shown(text):
    """출력용. 깨진 바이트는 대체 문자로 바꾼다."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace") if text is not None else None


META_TEXT = ("parent", "at", "session_id", "transcript_dir", "branch", "head", "doc", "doc_hash",
             "plan_doc", "plan_hash", "fixed_hash")


def check_baton_dirs(proj):
    """.baton 아래가 심링크면 다른 곳을 읽고 쓰게 된다 — 거부한다."""
    for path in (proj.baton, proj.cp_root, proj.cp_dir):
        if os.path.islink(path):
            raise error("baton_dir_symlink", "심링크는 따라가지 않는다: %s" % path)


def load_index(proj):
    check_baton_dirs(proj)
    raw = read_bytes(proj.index_path)
    if raw is None:
        return []

    def bad(why):
        return error("checkpoint_index_invalid", "체크포인트 목록이 잘못됐다 (%s): %s" % (proj.index_path, why))

    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise bad(e)
    if not isinstance(data, dict) or not isinstance(data.get("checkpoints"), list):
        raise bad("형식이 아니다")
    if data.get("key") is not None and data.get("key") != proj.key:
        raise error("checkpoint_index_mismatch", "%s의 체크포인트 목록이 아니다 (%s): %s"
                    % (proj.key, data.get("key"), proj.index_path))
    cps = data["checkpoints"]
    for meta in cps:
        if not (isinstance(meta, dict) and isinstance(meta.get("id"), str) and CP_RE.match(meta["id"])):
            raise bad("체크포인트 ID가 잘못됐다: %r" % (meta.get("id") if isinstance(meta, dict) else meta,))
        for field in ("doc_file", "plan_file"):
            v = meta.get(field)
            if v is not None and not (isinstance(v, str) and SAFE_NAME_RE.fullmatch(v)):
                raise bad("%s %s가 파일 이름이 아니다: %r" % (meta["id"], field, v))
        if not meta.get("doc_file"):
            raise bad("%s에 문서 사본 이름이 없다" % meta["id"])
        for field in META_TEXT:
            if meta.get(field) is not None and not isinstance(meta[field], str):
                raise bad("%s %s가 문자열이 아니다" % (meta["id"], field))
        if not isinstance(meta.get("plan_changed", False), bool):
            raise bad("%s plan_changed가 참/거짓이 아니다" % meta["id"])
    return cps


def ensure_baton_dir(proj):
    check_baton_dirs(proj)
    os.makedirs(proj.cp_dir, exist_ok=True)
    check_baton_dirs(proj)
    ignore = os.path.join(proj.baton, ".gitignore")
    try:
        fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except FileExistsError:
        return  # 이미 있으면(심링크여도) 건드리지 않는다
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("*\n")


def next_checkpoint_id(proj, cps, registry_latest):
    """목록, 폴더, 레지스트리가 기억하는 최신 번호 중 가장 큰 것 다음. .baton이 지워져도 번호를 다시 쓰지 않는다."""
    n = 0
    names = [c["id"] for c in cps] + ([registry_latest] if registry_latest else [])
    try:
        names += os.listdir(proj.cp_dir)  # 목록에 오르기 전에 끊긴 폴더와도 겹치지 않게
    except OSError:
        pass
    for name in names:
        m = CP_RE.match(name)
        if m:
            n = max(n, int(m.group(1)))
    return "C%04d" % (n + 1)


def stored_name(prefix, rel):
    ext = os.path.splitext(rel)[1] if rel else ""
    return prefix + (ext if SAFE_NAME_RE.fullmatch("x" + ext) else "")


def find_checkpoint(proj, cps, cid):
    for meta in cps:
        if meta["id"] == cid:
            return meta
    raise error("unknown_checkpoint", "%s에 체크포인트 %s가 없다" % (proj.key, cid),
                checkpoints=[c["id"] for c in cps])


def checkpoint_bytes(proj, meta):
    """사본을 읽고 저장할 때의 해시와 대조한다. 사본을 고쳐서 고정층 비교를 속이지 못하게 한다."""
    folder = os.path.join(proj.cp_dir, meta["id"])

    def read(name, want, label):
        path = os.path.join(folder, name)
        if os.path.islink(folder) or os.path.islink(path):
            raise error("baton_dir_symlink", "심링크는 따라가지 않는다: %s" % path)
        data = read_bytes(path)
        if data is None:
            raise error("checkpoint_missing", "체크포인트 %s의 %s 사본이 없다: %s" % (meta["id"], label, path))
        if want and sha256(data) != want:
            raise error("checkpoint_tampered", "체크포인트 %s의 %s 사본이 저장 때와 다르다: %s" % (meta["id"], label, path))
        return data

    doc = read(meta["doc_file"], meta.get("doc_hash"), "문서")
    plan = read(meta["plan_file"], meta.get("plan_hash"), "계획") if meta.get("plan_file") else None
    return doc, plan


def fixed_blocks(text):
    """<!-- baton:fixed --> … <!-- /baton:fixed --> 사이의 내용. 짝이 맞지 않으면 거부한다."""
    blocks, pos = [], 0
    while True:
        start = FIXED_START_RE.search(text, pos)
        end = FIXED_END_RE.search(text, pos)
        if not start:
            if end:
                raise error("fixed_marker_unbalanced", "고정층 닫는 표시만 있다 (%d행)" % line_no(text, end.start()))
            return blocks
        if end and end.start() < start.start():
            raise error("fixed_marker_unbalanced", "고정층 닫는 표시가 여는 표시보다 먼저 나온다 (%d행)"
                        % line_no(text, end.start()))
        end = FIXED_END_RE.search(text, start.end())
        if not end:
            raise error("fixed_marker_unbalanced", "고정층 여는 표시를 닫지 않았다 (%d행)" % line_no(text, start.start()))
        inner = text[start.end():end.start()]
        if FIXED_START_RE.search(inner):
            raise error("fixed_marker_unbalanced", "고정층 표시가 겹쳐 있다 (%d행)" % line_no(text, start.start()))
        blocks.append(inner.strip("\n"))
        pos = end.end()


def line_no(text, offset):
    return text.count("\n", 0, offset) + 1


def fixed_view(doc_text, plan_text, plan_rel):
    """고정층 = 문서의 고정 블록 전부 + 계획 파일 전체. 블록의 위치는 따지지 않는다."""
    parts = []
    blocks = fixed_blocks(doc_text)
    if blocks:
        parts.append("\n\n".join(blocks))
    if plan_text is not None:
        parts.append("=== 계획 파일 %s ===\n%s" % (plan_rel, plan_text.rstrip("\n")))
    return "\n\n".join(parts) + ("\n" if parts else "")


def udiff(a, b, a_name, b_name):
    a, b = shown(a) or "", shown(b) or ""
    a = a if (not a or a.endswith("\n")) else a + "\n"
    b = b if (not b or b.endswith("\n")) else b + "\n"
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), a_name, b_name))


def git_info(workdir):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")

    def run(*args):
        try:
            r = subprocess.run(["git", "-C", workdir] + list(args), capture_output=True,
                               encoding="utf-8", errors="replace", timeout=5, env=env)
        except (OSError, subprocess.SubprocessError):
            return None
        return (r.stdout.strip() or None) if r.returncode == 0 else None

    if run("rev-parse", "--is-inside-work-tree") != "true":
        return None, None
    return run("symbolic-ref", "--quiet", "--short", "HEAD"), run("rev-parse", "--verify", "--quiet", "HEAD")


def write_file_synced(path, data):
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def write_checkpoint(proj, cps, fields, doc_bytes, plan_bytes):
    """사본을 임시 폴더에 다 쓴 뒤 이름을 바꾸고, 그다음 목록에 올린다. 이전 체크포인트는 건드리지 않는다."""
    ensure_baton_dir(proj)
    now = time.time()
    for old in glob.glob(os.path.join(proj.cp_dir, ".tmp-*")):  # 끊긴 이전 시도
        try:
            if now - os.path.getmtime(old) > 3600:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    cid = next_checkpoint_id(proj, cps, proj.entry.get("latest_checkpoint"))
    meta = {"id": cid}
    meta.update(fields)
    meta["doc_file"] = stored_name("handoff", proj.doc_rel)
    meta["plan_file"] = stored_name("plan", proj.plan_rel) if plan_bytes is not None else None
    tmp = tempfile.mkdtemp(dir=proj.cp_dir, prefix=".tmp-")
    try:
        write_file_synced(os.path.join(tmp, meta["doc_file"]), doc_bytes)
        if plan_bytes is not None:
            write_file_synced(os.path.join(tmp, meta["plan_file"]), plan_bytes)
        os.rename(tmp, os.path.join(proj.cp_dir, cid))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    atomic_write(proj.index_path, dump_json({"format": 1, "key": proj.key, "checkpoints": cps + [meta]}))
    return meta


def history_notice(proj, cps):
    """레지스트리는 체크포인트를 기억하는데 목록이 비었다 — .baton이 지워졌을 가능성."""
    if not cps and proj.entry.get("latest_checkpoint"):
        return ["체크포인트 이력이 없다 — 레지스트리는 %s까지 기억한다. %s 이 지워졌을 수 있다"
                % (proj.entry["latest_checkpoint"], proj.baton)]
    return []


# ---------- 목록 출력 ----------

def project_status(key, entry, ctx):
    """목록 한 줄의 재료. 세 가지를 따로 본다 — 문서가 있는가, 원 대화(트랜스크립트)가 남았는가,
    이 실행 폴더의 프로젝트인가. resume 가능 여부는 원 대화가 남았는가와 같다(ID resume은 폴더를 가리지 않는다)."""
    problems = entry_problems(entry)
    if problems:
        return {"key": key, "where": "elsewhere", "this_folder": False, "launch_inside": False,
                "registered_here": False, "updated": "", "one_line": "", "workdir": None, "handoff_doc": None,
                "doc_exists": False, "last_session_id": None, "transcript_exists": False, "latest_checkpoint": None,
                "resumable": False, "run_from": None, "issues": problems}
    workdir = norm(entry["workdir"])
    doc = os.path.join(workdir, entry["handoff_doc"])
    reg_dir = norm(entry.get("transcript_dir"))
    last = entry.get("last_session_id")
    last_path = find_transcript(last)
    last_dir = norm(os.path.dirname(last_path)) if last_path else None
    here_dir = ctx["project_dir"]
    launch_inside = inside(ctx["launch_cwd"], workdir)
    this_folder = launch_inside or here_dir in (reg_dir, last_dir)

    issues = []
    if not os.path.isdir(workdir):
        issues.append("workdir 없음: %s" % workdir)
    elif not os.path.isfile(doc):
        issues.append("핸드오프 문서 없음: %s" % doc)
    if last and not last_path:
        issues.append("마지막 세션 %s 트랜스크립트 없음 (Claude Code가 정리했을 수 있다)" % last[:8])
    if last_dir and reg_dir and last_dir != reg_dir:
        issues.append("transcript_dir 불일치 — 등록 %s / 마지막 세션 실제 %s"
                      % (os.path.basename(reg_dir), os.path.basename(last_dir)))

    return {
        "key": key,
        "where": "here" if this_folder else "elsewhere",
        "this_folder": this_folder,
        "launch_inside": launch_inside,
        "registered_here": reg_dir == here_dir,
        "updated": entry.get("updated") or "",
        "one_line": entry.get("one_line") or "",
        "workdir": workdir,
        "handoff_doc": doc,
        "doc_exists": os.path.isfile(doc),
        "last_session_id": last,
        "transcript_exists": bool(last_path),
        "latest_checkpoint": entry.get("latest_checkpoint"),
        "resumable": bool(last_path),
        "run_from": first_cwd(last_path) if last_path else None,
        "issues": issues,
    }


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


def mark(flag):
    return "✓" if flag else "✗"


def print_list(r):
    ctx, states = r["context"], r["projects"]
    print("실행 폴더  %s" % ctx["launch_cwd"])
    print("현재 세션  %s · 묶인 프로젝트 %s" % (describe_session(ctx), r.get("bound") or "없음"))
    if not states:
        print("\n등록된 프로젝트가 없다 (%s)" % REGISTRY)
        return
    width = max(len(s["key"]) for s in states)
    pad = " " * width
    for here, head in ((True, "이 폴더의 프로젝트"), (False, "다른 폴더의 프로젝트")):
        items = sorted((s for s in states if s["this_folder"] == here), key=lambda s: s["updated"], reverse=True)
        if not items:
            continue
        print("\n%s (%d)" % (head, len(items)))
        for s in items:
            print("  %-*s  %-5s  %-16s  문서 %s  원 대화 %s" % (
                width, s["key"], s["latest_checkpoint"] or "C----", (s["updated"] or "-")[:16].replace("T", " "),
                mark(s["doc_exists"]), mark(s["transcript_exists"]) if s["last_session_id"] else "-"))
            if s["one_line"]:
                print("  %s  %s" % (pad, short(s["one_line"], 90)))
            if not here and s["workdir"]:
                print("  %s  작업 폴더 %s" % (pad, s["workdir"]))
            if s["transcript_exists"]:
                print("  %s  원 대화 cd %s && claude --resume %s" % (pad, s["run_from"] or s["workdir"], s["last_session_id"]))
    flagged = [s for s in states if s["issues"]]
    if flagged:
        print("\n⚠ 점검")
        for s in flagged:
            for issue in s["issues"]:
                print("  %-*s  %s" % (width, s["key"], issue))


# ---------- 명령 ----------

def project_by_name(name, projects):
    key, cands, ambiguous = lookup(name, projects)
    if key:
        return key
    if ambiguous:
        raise Fail("needs_target", "'%s'에 맞는 프로젝트가 여럿이다 — 키로 고른다: %s" % (name, ", ".join(cands)),
                   candidates=cands)
    raise error("unknown_project", "등록되지 않은 이름: %s" % name, candidates=cands)


def cmd_register(a):
    key = a.key
    if key.strip().casefold() in RESERVED:
        raise error("reserved_key", "예약어는 키로 쓸 수 없다: %s" % key)
    if not KEY_RE.fullmatch(key) or key.startswith(("-", ".")):
        raise error("invalid_key", "키는 공백·/·\\·: 없이 64자 이하로 쓴다: %r" % key)
    workdir = norm(a.workdir)
    if not (workdir and os.path.isdir(workdir)):
        raise error("workdir_missing", "workdir가 없다: %s" % a.workdir)
    doc_rel = check_rel(workdir, a.doc or DOC_DEFAULT, "doc_missing", "핸드오프 문서")
    plan_rel = check_rel(workdir, a.plan_doc, "plan_doc_missing", "계획 파일") if a.plan_doc else None
    aliases = [x for x in clean_list(a.alias) if x.casefold() != key.casefold()]
    entry = {"aliases": aliases, "workdir": workdir, "handoff_doc": doc_rel}
    if plan_rel:
        entry["plan_doc"] = plan_rel
    entry.update({
        "one_line": (a.one_line or "").strip(),
        "setup": clean_list(a.setup),
        "verify": clean_list(a.verify),
        "warnings": clean_list(a.warning),
        "registered": now_iso(),
    })
    with registry_lock():
        reg = load_registry()
        check_names(reg["projects"], key, aliases)
        reg["projects"][key] = entry
        try:
            write_registry(reg)
        except OSError as e:
            raise error("registry_write_failed", "레지스트리를 쓰지 못했다: %s" % e)
    return {"status": "ok", "key": key, "entry": entry,
            "message": "%s 등록 — %s (%s)" % (key, workdir, doc_rel)}


def cmd_set(a):
    """준 필드만 바꾼다. 바꾼 뒤에 검증하므로, 손으로 잘못 고친 엔트리도 set으로 고칠 수 있다."""
    with registry_lock():
        reg = load_registry()
        projects = reg["projects"]
        key = project_by_name(a.key, projects)
        entry = projects[key]
        if not isinstance(entry, dict):
            raise error("invalid_entry", "%s 엔트리가 객체가 아니다 — 레지스트리에서 지우고 다시 등록한다" % key, key=key)
        workdir = norm(entry.get("workdir")) if isinstance(entry.get("workdir"), str) else None
        changes = {}
        for field in a.clear or []:
            changes[field] = [] if field in LIST_FIELDS else None
        if a.one_line is not None:
            changes["one_line"] = a.one_line.strip()
        if a.plan_doc is not None:
            if not (workdir and os.path.isdir(workdir)):
                raise error("invalid_entry", "%s의 workdir가 없어 계획 파일을 확인할 수 없다" % key, key=key)
            changes["plan_doc"] = check_rel(workdir, a.plan_doc, "plan_doc_missing", "계획 파일")
        for field, values in (("aliases", a.alias), ("setup", a.setup), ("verify", a.verify), ("warnings", a.warning)):
            if values is not None:
                changes[field] = clean_list(values)
        if not changes:
            raise error("nothing_to_set", "고칠 필드를 주지 않았다")
        if changes.get("aliases"):
            check_names(projects, None, changes["aliases"], exclude=key)
        updated = dict(entry)
        for field, value in changes.items():
            if value is None:
                updated.pop(field, None)
            else:
                updated[field] = value
        problems = entry_problems(updated)
        if problems:
            raise error("invalid_entry", "고친 뒤에도 %s 엔트리가 잘못됐다: %s" % (key, "; ".join(problems)),
                        key=key, issues=problems)
        projects[key] = updated
        try:
            write_registry(reg)
        except OSError as e:
            raise error("registry_write_failed", "레지스트리를 쓰지 못했다: %s" % e)
    return {"status": "ok", "key": key, "changed": sorted(changes),
            "message": "%s: %s 갱신" % (key, ", ".join(sorted(changes)))}


def cmd_pull(a):
    ctx = current_context(a.session_id, a.cwd, want_title=False)
    sid = ctx["session_id"] if valid_sid(ctx["session_id"]) else None
    reg = load_registry()
    projects = reg["projects"]
    name = " ".join(a.name).strip()
    if name:
        key, by = project_by_name(name, projects), "name"
    else:
        key, cands, by = folder_candidates(projects, ctx)
        if not key:
            raise Fail("needs_target", "실행 폴더(%s)로 프로젝트를 정하지 못했다 — 키를 준다" % ctx["launch_cwd"],
                       candidates=cands)
    proj = get_project(reg, key)
    cps = load_index(proj)
    latest = cps[-1] if cps else None
    branch, head = git_info(proj.workdir)
    bound = write_binding(sid, key, base_of(latest))
    entry = proj.entry
    status = project_status(key, entry, ctx)
    result = {
        "status": "ok", "key": key, "by": by,
        "workdir": proj.workdir, "handoff_doc": proj.doc, "plan_doc": proj.plan,
        "doc_exists": os.path.isfile(proj.doc),
        "latest": latest["id"] if latest else None,
        "latest_at": latest.get("at") if latest else None,
        "latest_branch": latest.get("branch") if latest else None,
        "latest_head": latest.get("head") if latest else None,
        "branch": branch, "head": head,
        "one_line": entry.get("one_line") or "",
        "setup": list_field(entry, "setup"),
        "verify": list_field(entry, "verify"),
        "warnings": list_field(entry, "warnings"),
        "last_session_id": entry.get("last_session_id"),
        "resumable": status["resumable"],
        "bound": bound,
        "issues": status["issues"],
        "notices": history_notice(proj, cps),
    }
    head_note = ""
    if latest and latest.get("head") and head and latest["head"] != head:
        head_note = " · HEAD가 %s 이후 바뀜 (%s → %s)" % (latest["id"], latest["head"][:8], head[:8])
    result["message"] = "%s%s · 문서 %s · 최신 체크포인트 %s%s%s" % (
        key, " (실행 폴더 기준)" if by != "name" else "", proj.doc, result["latest"] or "없음", head_note,
        "".join("\n⚠ " + n for n in result["notices"]))
    return result


def cmd_push(a):
    ctx = current_context(a.session_id, a.cwd, want_title=False)
    sid = ctx["session_id"] if valid_sid(ctx["session_id"]) else None
    name = " ".join(a.name).strip()
    with registry_lock():
        reg = load_registry()
        projects = reg["projects"]
        binding = read_binding(sid)
        if name:
            key = project_by_name(name, projects)
        else:
            key = binding.get("current")
            if key not in projects:
                _, cands, _ = folder_candidates(projects, ctx)
                raise Fail("needs_target", "이 세션은 아직 어느 프로젝트에도 묶이지 않았다 — 키를 주거나 새로 등록한다",
                           candidates=cands)
        proj = get_project(reg, key)
        doc_bytes = read_bytes(proj.doc)
        if doc_bytes is None:
            raise error("doc_missing", "핸드오프 문서가 없다: %s" % proj.doc)
        plan_bytes = None
        if proj.plan:
            plan_bytes = read_bytes(proj.plan)
            if plan_bytes is None:
                raise error("plan_doc_missing", "계획 파일이 없다: %s" % proj.plan)
        doc_text, plan_text = exact_text(doc_bytes), exact_text(plan_bytes)
        fixed_now = fixed_view(doc_text, plan_text, proj.plan_rel)
        doc_hash, plan_hash = sha256(doc_bytes), sha256(plan_bytes)

        cps = load_index(proj)
        notices = history_notice(proj, cps)
        latest = cps[-1] if cps else None
        latest_id = latest["id"] if latest else None
        new_meta = None
        if latest and latest.get("doc_hash") == doc_hash and latest.get("plan_hash") == plan_hash:
            checkpoint_bytes(proj, latest)  # 사본이 온전한지 확인한다
            result = {"status": "unchanged", "key": key, "checkpoint": latest_id,
                      "parent": latest.get("parent"), "plan_changed": False}
        else:
            base = (binding.get("bases") or {}).get(key)
            mine = bool(sid and latest and latest.get("session_id") == sid)
            if base is not None and (base.get("id"), base.get("hash")) != (latest_id, latest and latest.get("doc_hash")) \
                    and not mine:
                latest_text = exact_text(checkpoint_bytes(proj, latest)[0]) if latest else ""
                if latest:
                    why = "이 세션이 읽은 뒤(%s) 다른 세션이 %s를 저장했다" % (base.get("id") or "체크포인트 없음", latest_id)
                else:
                    why = "이 세션이 읽은 뒤(%s) 체크포인트 이력이 사라졌다" % (base.get("id") or "체크포인트 없음")
                raise Fail("conflict", why + " — diff를 보고 합친 뒤 pull하고 다시 push한다",
                           key=key, base=base.get("id"), latest=latest_id, notices=notices,
                           diff=udiff(latest_text, doc_text, latest_id or "(없음)", "현재 문서"))
            if latest:
                old_doc, old_plan = checkpoint_bytes(proj, latest)
                parent_fixed = fixed_view(exact_text(old_doc), exact_text(old_plan), latest.get("plan_doc"))
            else:
                parent_fixed = ""
            plan_changed = fixed_now != parent_fixed
            if plan_changed and not a.approve_plan_change:
                raise Fail("needs_approval",
                           "고정층(목표·하지 않을 것·결정·계획)이 바뀌었다 — 사용자 승인 뒤 --approve-plan-change로 다시 실행한다",
                           key=key, parent=latest_id, notices=notices,
                           fixed_diff=udiff(parent_fixed, fixed_now, (latest_id or "(없음)") + " 고정층", "현재 고정층"))
            branch, head = git_info(proj.workdir)
            transcript_dir = ctx["project_dir"] if ctx["transcript"] else None
            new_meta = write_checkpoint(proj, cps, {
                "parent": latest_id, "at": now_iso(), "session_id": sid, "transcript_dir": transcript_dir,
                "branch": branch, "head": head, "doc": proj.doc_rel, "doc_hash": doc_hash,
                "plan_doc": proj.plan_rel if plan_bytes is not None else None, "plan_hash": plan_hash,
                "fixed_hash": sha256(fixed_now.encode("utf-8", "surrogateescape")),
                "plan_changed": plan_changed,
            }, doc_bytes, plan_bytes)
            result = {"status": "ok", "key": key, "checkpoint": new_meta["id"], "parent": latest_id,
                      "plan_changed": plan_changed}
        result["notices"] = notices

        cid = result["checkpoint"]
        entry = proj.entry
        registry_before = entry.get("latest_checkpoint")
        changes = {}
        if registry_before != cid:
            changes["latest_checkpoint"] = cid
        if new_meta:
            changes["updated"] = new_meta["at"]
            if ctx["transcript"]:
                changes["last_session_id"] = sid
                changes["transcript_dir"] = ctx["project_dir"]
        write_error = None
        if changes:
            entry.update(changes)
            try:
                write_registry(reg)
            except OSError as e:
                write_error = str(e)
        write_binding(sid, key, base_of(new_meta or latest))

    if write_error:
        raise Fail("partial", "%s 저장됨 / 색인은 %s / 같은 명령을 다시 실행하면 색인만 고친다 (%s)"
                   % (cid, registry_before or "없음", write_error),
                   key=key, checkpoint=cid, registry_latest=registry_before)
    if result["status"] == "unchanged" and changes:
        result["status"] = "repaired"
    words = {"ok": "저장", "unchanged": "바뀐 것 없음", "repaired": "색인 복구"}
    result["message"] = "%s: %s %s%s%s%s" % (
        key, cid, words[result["status"]],
        " (부모 %s)" % result["parent"] if result["status"] == "ok" and result["parent"] else "",
        " · 고정층 변경 승인됨" if result.get("plan_changed") else "",
        "".join("\n⚠ " + n for n in notices))
    return result


def cmd_log(a):
    reg = load_registry()
    key = project_by_name(a.key, reg["projects"])
    proj = get_project(reg, key)
    cps = load_index(proj)
    checkpoints = []
    for meta in cps:
        item = dict(meta)
        item["transcript_available"] = transcript_available(meta.get("transcript_dir"), meta.get("session_id"))
        checkpoints.append(item)
    return {"status": "ok", "key": key, "latest": cps[-1]["id"] if cps else None,
            "registry_latest": proj.entry.get("latest_checkpoint"), "checkpoints": checkpoints,
            "notices": history_notice(proj, cps)}


def print_log(r):
    print("%s  최신 %s · 색인 %s" % (r["key"], r["latest"] or "없음", r["registry_latest"] or "없음"))
    for n in r.get("notices") or []:
        print("⚠ " + n)
    for c in r["checkpoints"]:
        session = (c.get("session_id") or "?")[:8]
        talk = "원 대화 있음" if c["transcript_available"] else "원 대화 없음"
        git = "%s@%s" % (c.get("branch") or "-", (c.get("head") or "-")[:8])
        print("  %s  %s  %s  %s  %s%s" % (c["id"], (c.get("at") or "")[:16], session, talk, git,
                                          "  고정층 변경" if c.get("plan_changed") else ""))


def cmd_show(a):
    reg = load_registry()
    key = project_by_name(a.key, reg["projects"])
    proj = get_project(reg, key)
    cps = load_index(proj)
    meta = find_checkpoint(proj, cps, a.id)
    doc, plan = checkpoint_bytes(proj, meta)
    if a.against:
        other = find_checkpoint(proj, cps, a.against)
        odoc, oplan = checkpoint_bytes(proj, other)
        diff = udiff(exact_text(doc), exact_text(odoc), meta["id"], other["id"])
        if plan is not None or oplan is not None:
            diff += udiff(exact_text(plan) or "", exact_text(oplan) or "", meta["id"] + " 계획", other["id"] + " 계획")
        if not a.json:
            sys.stdout.write(diff)
            return None
        return {"status": "ok", "key": key, "id": meta["id"], "against": other["id"], "diff": diff}
    if not a.json:
        sys.stdout.flush()
        sys.stdout.buffer.write(doc)
        return None
    return {"status": "ok", "key": key, "id": meta["id"], "meta": meta,
            "doc": shown(exact_text(doc)), "plan": shown(exact_text(plan))}


def cmd_list(a):
    ctx = current_context(a.session_id, a.cwd)
    states = [project_status(k, e, ctx) for k, e in load_registry()["projects"].items()]
    sid = ctx["session_id"] if valid_sid(ctx["session_id"]) else None
    return {"status": "ok", "context": ctx, "bound": read_binding(sid).get("current") if sid else None,
            "projects": states}


def cmd_resolve(a):
    ctx = current_context(a.session_id, a.cwd, want_title=False)
    projects = load_registry()["projects"]
    name = " ".join(a.name).strip()
    if name:
        key, cands, ambiguous = lookup(name, projects)
        r = {"status": "ok", "match": key, "by": "name", "candidates": cands}
        if not key:
            r["reason"] = ("이름이 여러 프로젝트와 겹친다: %s" if ambiguous else "등록되지 않은 이름: %s") % name
        return r
    key, cands, by = folder_candidates(projects, ctx)
    return {"status": "ok", "match": key, "by": by, "candidates": cands}


def cmd_current(a):
    """현재 세션. bound는 이 세션이 묶인 프로젝트 — push 대상을 문서를 고치기 전에 알 수 있게 읽기만 한다."""
    ctx = current_context(a.session_id, a.cwd)
    r = dict(ctx)
    r["status"] = "ok"
    sid = ctx["session_id"] if valid_sid(ctx["session_id"]) else None
    r["bound"] = read_binding(sid).get("current") if sid else None
    return r


def print_current(r):
    print("세션       %s" % describe_session(r))
    print("세션 ID    %s" % (r["session_id"] or "?"))
    print("실행 폴더  %s" % r["launch_cwd"])
    print("트랜스크립트 폴더  %s" % r["project_dir"])
    print("묶인 프로젝트  %s" % (r.get("bound") or "없음 — pull 또는 push 하면 묶인다"))


def cmd_title(a):
    name = " ".join(a.name).strip()
    if not name:
        raise error("empty_title", "이름이 비었다")
    ctx = current_context(a.session_id, a.cwd)
    sid = ctx["session_id"]
    if not valid_sid(sid):
        raise error("no_session", "세션 ID를 알 수 없다 — /rename %s 로 직접 붙인다" % name)
    if ctx["title"] == name:
        clear_pending(sid)  # 예전에 대기시킨 다른 이름이 덮어쓰지 않게
        return {"status": "ok", "title": name, "applied": True,
                "message": "세션 이름: %s (설정됨 · %s)" % (name, sid[:8])}
    write_pending(sid, name)
    return {"status": "ok", "title": name, "applied": False,
            "message": "세션 이름: %s (다음 입력 때 적용 · 지금 %s · %s)" % (name, ctx["title"] or "이름 없음", sid[:8])}


def cmd_hook(_a):
    try:
        data = json.loads(HOOK_STDIN if HOOK_STDIN is not None else sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(data, dict) or data.get("hook_event_name", "UserPromptSubmit") != "UserPromptSubmit":
        return 0
    sid = data.get("session_id") if valid_sid(data.get("session_id")) else None
    prompt = data.get("prompt") or ""
    sweep_pending()

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
    if hit and not (first and first[0].casefold() in RESERVED):
        try:
            ctx = current_context(sid, data.get("cwd"), data.get("transcript_path"), want_title=False)
            projects = load_registry()["projects"]
            if arg and arg.strip():
                key, cands, _ = lookup(arg, projects)
                title = key or (cands[0] if len(cands) == 1 else None)  # 제목은 표시값이라 첫 단어 추정을 허용한다
            else:
                title = folder_candidates(projects, ctx)[0]
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


# ---------- 진입점 ----------

def print_generic(r):
    ok = r.get("status") in SUCCESS
    out = sys.stdout if ok else sys.stderr
    if r.get("message"):
        print(r["message"], file=out)
    if r.get("candidates") is not None and not ok:
        print("후보: %s" % (", ".join(r["candidates"]) or "없음"), file=out)
    for field in ("fixed_diff", "diff"):
        if r.get(field):
            print(r[field], file=out, end="" if r[field].endswith("\n") else "\n")


TEXT = {"list": print_list, "log": print_log, "current": print_current}


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    """잘못된 인자도 --json이면 JSON 오류로 돌려주도록 예외로 바꾼다."""

    def error(self, message):
        raise UsageError("%s\n%s" % (message, self.format_usage().strip()))


def build_parser():
    common = Parser(add_help=False)
    common.add_argument("--session-id")
    common.add_argument("--cwd")
    common.add_argument("--json", action="store_true")
    p = Parser(prog="handoff.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("register", parents=[common])
    sp.add_argument("key")
    sp.add_argument("--workdir", required=True)
    sp.add_argument("--doc")
    sp.add_argument("--plan-doc")
    sp.add_argument("--alias", action="append")
    sp.add_argument("--one-line")
    sp.add_argument("--setup", action="append")
    sp.add_argument("--verify", action="append")
    sp.add_argument("--warning", action="append")

    sp = sub.add_parser("set", parents=[common])
    sp.add_argument("key")
    sp.add_argument("--one-line")
    sp.add_argument("--plan-doc")
    sp.add_argument("--alias", action="append")
    sp.add_argument("--setup", action="append")
    sp.add_argument("--verify", action="append")
    sp.add_argument("--warning", action="append")
    sp.add_argument("--clear", action="append", choices=CLEARABLE)

    sp = sub.add_parser("pull", parents=[common])
    sp.add_argument("name", nargs="*")
    sp = sub.add_parser("push", parents=[common])
    sp.add_argument("name", nargs="*")
    sp.add_argument("--approve-plan-change", action="store_true")
    sp = sub.add_parser("log", parents=[common])
    sp.add_argument("key")
    sp = sub.add_parser("show", parents=[common])
    sp.add_argument("key")
    sp.add_argument("id")
    sp.add_argument("--against")
    sub.add_parser("list", parents=[common])
    sp = sub.add_parser("resolve", parents=[common])
    sp.add_argument("name", nargs="*")
    sub.add_parser("current", parents=[common])
    sp = sub.add_parser("title", parents=[common])
    sp.add_argument("name", nargs="+")
    sub.add_parser("hook")
    return p


HANDLERS = {"register": cmd_register, "set": cmd_set, "pull": cmd_pull, "push": cmd_push, "log": cmd_log,
            "show": cmd_show, "list": cmd_list, "resolve": cmd_resolve, "current": cmd_current, "title": cmd_title}


def emit(a, result):
    if a.json or a.cmd == "resolve":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=None if a.cmd == "resolve" else 2)
        print()
    elif result.get("status") in SUCCESS and a.cmd in TEXT:
        TEXT[a.cmd](result)
    else:
        print_generic(result)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    try:
        a = p.parse_args(argv)
    except UsageError as e:
        if "--json" in argv:
            json.dump({"status": "error", "error": "usage", "message": str(e)}, sys.stdout, ensure_ascii=False)
            print()
            return 1
        print(str(e), file=sys.stderr)
        return 2
    if a.cmd == "hook":
        try:
            return cmd_hook(a)  # 훅은 무슨 일이 있어도 프롬프트를 막지 않는다
        except Exception:
            return 0
    if a.cmd not in HANDLERS:
        p.print_help()
        return 2
    # traceback 대신 진단을 낸다. BATON_DEBUG=1이면 그대로 올린다
    try:
        if REGISTRY_RELATIVE:
            raise error("registry_path_relative", "HANDOFF_REGISTRY는 절대 경로여야 한다: %s" % REGISTRY)
        result = HANDLERS[a.cmd](a)
    except Fail as f:
        result = f.result
    except Exception as e:
        if os.environ.get("BATON_DEBUG"):
            raise
        result = {"status": "error", "error": "internal", "message": "%s: %s" % (type(e).__name__, e)}
    if result is None:
        return 0
    try:
        emit(a, result)
    except Exception as e:
        if os.environ.get("BATON_DEBUG"):
            raise
        result = {"status": "error", "error": "internal", "message": "출력 중 오류 — %s: %s" % (type(e).__name__, e)}
        print_generic(result)
    return 0 if result.get("status") in SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main())
