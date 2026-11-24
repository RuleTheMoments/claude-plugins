"""헤드리스 Claude Code 세션 하나를 한 프로세스로 몬다. 턴마다 stdin에 한 줄을 쓰고 result가 올 때까지 읽는다.

unittest가 아니다(`unittest discover`에 잡히지 않는다). 실제 모델을 부르므로 토큰을 쓴다.
live_flow.py와 compact_canary.py가 쓴다.

한 프로세스로 모는 이유: `claude -p --resume`으로 턴마다 프로세스를 새로 띄우면 시작할 때마다
CLAUDE.md를 디스크에서 다시 읽는다. compact가 무엇을 다시 넣는지 재려면 프로세스가 하나여야 한다.
"""
import json
import os
import queue
import subprocess
import threading
import time


def claude_version():
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class Turn:
    def __init__(self, text):
        self.text = text
        self.events = []
        self.result = None     # 마지막 응답 글
        self.status = None     # result 이벤트의 subtype, 또는 EOF·TIMEOUT
        self.cost = None

    def _blocks(self, role, kind):
        for e in self.events:
            if e.get("type") != role:
                continue
            content = (e.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == kind:
                    yield b

    @property
    def tool_uses(self):
        return [{"name": b.get("name"), "input": b.get("input") or {}} for b in self._blocks("assistant", "tool_use")]

    @property
    def bash(self):
        """이 턴에 모델이 부른 셸 명령 원문."""
        return [u["input"].get("command") or "" for u in self.tool_uses if u["name"] == "Bash"]

    @property
    def tool_errors(self):
        out = []
        for b in self._blocks("user", "tool_result"):
            if b.get("is_error"):
                c = b.get("content")
                out.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
        return out

    @property
    def tool_outputs(self):
        out = []
        for b in self._blocks("user", "tool_result"):
            c = b.get("content")
            if isinstance(c, list):
                c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
            out.append(c if isinstance(c, str) else "")
        return out

    @property
    def said(self):
        """이 턴에 모델이 쓴 글 전부(도구 호출 사이의 글 포함)."""
        return "\n".join(b.get("text") or "" for b in self._blocks("assistant", "text"))

    @property
    def compaction(self):
        for e in self.events:
            if e.get("subtype") == "compact_boundary":
                return e.get("compact_metadata") or {}
        return None


class Session:
    def __init__(self, cwd, model="sonnet", budget=2.0, tools=None, args=(), env=None, timeout=900):
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--model", model, "--max-budget-usd", str(budget)]
        if tools is not None:
            cmd += ["--tools", tools]
        cmd += list(args)
        e = dict(os.environ)
        e.pop("CLAUDE_CODE_SESSION_ID", None)  # 이 스크립트를 띄운 세션의 ID를 물려주지 않는다
        e.update(env or {})
        self.timeout = timeout
        self.turns = []
        self.session_id = None
        self.proc = subprocess.Popen(cmd, cwd=cwd, env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        self._q = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)

    def send(self, text, timeout=None):
        turn = Turn(text)
        self.turns.append(turn)
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            turn.status = "EOF"
            return turn
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                line = self._q.get(timeout=5)
            except queue.Empty:
                continue
            if line is None:
                turn.status = "EOF"
                return turn
            try:
                event = json.loads(line)
            except ValueError:
                continue
            turn.events.append(event)
            if event.get("session_id"):
                self.session_id = event["session_id"]
            if event.get("type") == "result":
                turn.result = event.get("result") or ""
                turn.status = event.get("subtype")
                turn.cost = event.get("total_cost_usd")
                return turn
        turn.status = "TIMEOUT"
        return turn

    def close(self):
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        return self.proc.stderr.read()

    @property
    def cost(self):
        costs = [t.cost for t in self.turns if t.cost is not None]
        return costs[-1] if costs else 0.0  # total_cost_usd는 프로세스 누적값이다
