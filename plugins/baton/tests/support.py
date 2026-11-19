"""baton 헬퍼(scripts/handoff.py) 테스트 공용 도구.

실행 (플러그인 폴더에서, 훅과 같은 macOS 기본 Python 3.9로):

    cd plugins/baton && /usr/bin/python3 -m unittest discover -s tests -v

헬퍼는 실제 사용처럼 하위 프로세스로 부른다. HOME, CLAUDE_CONFIG_DIR, HANDOFF_REGISTRY, git 설정은
모두 테스트마다 만든 임시 폴더를 가리킨다. 사용자의 레지스트리·세션·git 설정은 읽지도 쓰지도 않는다.

여기서 쓰는 명령·옵션·`--json` 결과의 `status`·종료 코드는
docs/baton_improvement_plan_claude.md 의 "헬퍼 계약"과 같다.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HELPER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts", "handoff.py"))
# 훅(hooks/hooks.json)과 같은 Python으로 헬퍼를 부른다 — 3.9 호환을 러너와 무관하게 확인한다
PYTHON = "/usr/bin/python3" if os.access("/usr/bin/python3", os.X_OK) else sys.executable

FIXED_START = "<!-- baton:fixed -->"
FIXED_END = "<!-- /baton:fixed -->"
DOC_REL = os.path.join("docs", "HANDOFF.md")

# 종료 코드 0으로 끝나는 status. 나머지(error, needs_target, needs_approval, conflict, partial)는 1.
SUCCESS = ("ok", "unchanged", "repaired")


def sid(n):
    """SID_RE(^[0-9A-Za-z-]{8,64}$)에 맞는 가짜 세션 ID."""
    return "%08d-aaaa-4000-8000-%012d" % (n, n)


S1, S2, S3 = sid(1), sid(2), sid(3)

# 승인된 방향의 예: 목표, 하지 않을 것, 단계 순서
FIXED = """## 목표

- 기존 형식과 호환되는 저장 기능

## 하지 않을 것

- 기존 파일 형식을 바꾸지 않는다

## 계획

- S1 형식 호환성 시험
- S2 저장 기능 (S1 통과 뒤)
- S3 UI (S2 완료 뒤)"""


def make_doc(fixed=None, status="- 시작 전"):
    """핸드오프 문서. fixed가 있으면 고정층 표시 사이에 넣는다."""
    text = "# 핸드오프\n\n"
    if fixed is not None:
        text += FIXED_START + "\n" + fixed.strip("\n") + "\n" + FIXED_END + "\n\n"
    text += "## 상태\n\n" + status.strip("\n") + "\n"
    return text


def encode_dir(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


class BatonCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="baton-test-"))
        self.addCleanup(self._cleanup)
        self.home = self.mkdir("home")
        self.cfg = self.mkdir("cfg")
        self.projects_root = self.mkdir("cfg", "projects")
        self.reg_dir = os.path.join(self.root, "reg")  # 일부 테스트는 레지스트리가 없는 상태에서 시작한다
        self.registry = os.path.join(self.reg_dir, "projects.json")
        self.work = self.mkdir("work")
        gitconfig = os.path.join(self.root, "gitconfig")
        with open(gitconfig, "w", encoding="utf-8") as f:
            f.write("[user]\n\tname = baton-test\n\temail = baton-test@example.invalid\n"
                    "[init]\n\tdefaultBranch = main\n[core]\n\thooksPath = /dev/null\n"
                    "[commit]\n\tgpgsign = false\n")
        self.env = {
            "PATH": os.environ.get("PATH") or "/usr/bin:/bin",
            "HOME": self.home,
            "CLAUDE_CONFIG_DIR": self.cfg,
            "HANDOFF_REGISTRY": self.registry,
            "GIT_CONFIG_GLOBAL": gitconfig,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": self.root,  # 임시 폴더가 다른 git 저장소 안에 있어도 그 저장소를 보지 않는다
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
        }
        if os.environ.get("TMPDIR"):
            self.env["TMPDIR"] = os.environ["TMPDIR"]

    def _cleanup(self):
        for d, _, _ in os.walk(self.root):
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------- 파일 ----------

    def mkdir(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def write_doc(self, workdir, text, rel=DOC_REL):
        path = os.path.join(workdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return path

    def read_doc(self, workdir, rel=DOC_REL):
        with open(os.path.join(workdir, rel), encoding="utf-8", newline="") as f:
            return f.read()

    def read_bytes(self, path):
        with open(path, "rb") as f:
            return f.read()

    def snapshot(self, top):
        """top 아래 모든 폴더와 파일의 {상대 경로: 내용(폴더는 None)}."""
        tree = {}
        for d, dirs, files in os.walk(top):
            for name in dirs:
                tree[os.path.relpath(os.path.join(d, name), top) + os.sep] = None
            for name in files:
                path = os.path.join(d, name)
                tree[os.path.relpath(path, top)] = self.read_bytes(path)
        return tree

    def make_project(self, key, text=None, rel=DOC_REL, git=False, commit=False):
        workdir = self.mkdir("work", key)
        self.write_doc(workdir, make_doc() if text is None else text, rel)
        if git:
            self.git(workdir, "init", "-q")
            if commit:
                self.git(workdir, "add", "-A")
                self.git(workdir, "commit", "-q", "-m", "init")
        return workdir

    def transcript_dir_for(self, launch_dir):
        return os.path.join(self.projects_root, encode_dir(launch_dir))

    def make_transcript(self, session, launch_dir):
        """Claude Code가 세션을 띄운 폴더 기준으로 쌓는 트랜스크립트를 흉내 낸다."""
        d = self.transcript_dir_for(launch_dir)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, session + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "cwd": launch_dir, "sessionId": session}) + "\n")
        return path

    def write_legacy_registry(self, projects):
        """0.3.0 형식 레지스트리를 직접 쓴다 — 이미 쓰고 있던 사용자의 데이터."""
        os.makedirs(self.reg_dir, exist_ok=True)
        with open(self.registry, "w", encoding="utf-8") as f:
            json.dump({"projects": projects}, f, ensure_ascii=False, indent=2)

    def git(self, cwd, *args):
        return subprocess.run(["git"] + list(args), cwd=cwd, env=self.env, check=True,
                              capture_output=True, encoding="utf-8").stdout.strip()

    # ---------- 헬퍼 실행 ----------

    def _cmd_env(self, args, session, env):
        e = dict(self.env)
        if session:
            e["CLAUDE_CODE_SESSION_ID"] = session
        if env:
            e.update(env)
        return [PYTHON, "-I", HELPER] + [str(a) for a in args], e

    def run_h(self, *args, sid=None, cwd=None, stdin=None, env=None):
        cmd, e = self._cmd_env(args, sid, env)
        return subprocess.run(cmd, cwd=cwd or self.work, env=e, input=stdin, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)

    def spawn(self, *args, sid=None, cwd=None):
        """동시 실행용. --json을 붙인다."""
        cmd, e = self._cmd_env(list(args) + ["--json"], sid, None)
        return subprocess.Popen(cmd, cwd=cwd or self.work, env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8", errors="replace")

    def parse(self, p):
        try:
            return json.loads(p.stdout)
        except ValueError:
            self.fail("JSON 출력이 아니다 (종료 코드 %s)\n--- stdout\n%s\n--- stderr\n%s"
                      % (p.returncode, p.stdout, p.stderr))

    def jh(self, *args, **kw):
        """--json을 붙여 실행하고 (프로세스, 파싱한 결과)를 돌려준다. resolve는 원래 JSON이다."""
        args = list(args)
        if args[0] != "resolve":
            args.append("--json")
        p = self.run_h(*args, **kw)
        return p, self.parse(p)

    def assertStatus(self, p, d, *statuses):
        self.assertIn(d.get("status"), statuses, d)
        want = 0 if d.get("status") in SUCCESS else 1
        self.assertEqual(p.returncode, want, "status %s의 종료 코드\n%s" % (d.get("status"), p.stderr))
        self.assertNoTraceback(p)

    def assertError(self, p, d, code):
        self.assertStatus(p, d, "error")
        self.assertEqual(d.get("error"), code, d)

    def assertNoTraceback(self, p):
        self.assertNotIn("Traceback", p.stderr)
        self.assertNotIn("Traceback", p.stdout)

    # ---------- 자주 쓰는 명령 ----------

    def register(self, key, workdir, *extra, sid=None):
        # 등록은 세션과 무관하다. 기본으로 세션 ID 없이 부른다
        p, d = self.jh("register", key, "--workdir", workdir, *extra, sid=sid)
        self.assertStatus(p, d, "ok")
        return d

    def push(self, key=None, *flags, sid, cwd=None, env=None):
        args = ["push"] + ([key] if key else []) + list(flags)
        return self.jh(*args, sid=sid, cwd=cwd, env=env)

    def pull(self, key=None, *, sid, cwd=None):
        args = ["pull"] + ([key] if key else [])
        p, d = self.jh(*args, sid=sid, cwd=cwd)
        self.assertStatus(p, d, "ok")
        return d

    def log(self, key):
        p, d = self.jh("log", key)
        self.assertStatus(p, d, "ok")
        return d

    def checkpoint_ids(self, key):
        return [c["id"] for c in self.log(key)["checkpoints"]]

    def show(self, key, checkpoint):
        p = self.run_h("show", key, checkpoint)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout
