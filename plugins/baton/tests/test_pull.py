"""pull: 읽기 전용, 대상 확정, 기준 체크포인트 기록.

리뷰: Codex H3·2차 High 4(읽기와 실행이 같은 동작), Claude H3(복원이 드리프트 보고보다 먼저),
계획 §5 G4(실행 경계). 계획 P1.
"""
import os

from support import S1, S2, BatonCase


class PullTest(BatonCase):
    def test_pull_runs_no_project_commands_and_changes_nothing(self):
        w = self.make_project("alpha")
        marker = os.path.join(self.root, "commands-ran")  # 어느 폴더에서 실행돼도 같은 곳에 흔적이 남는다
        setup, verify = "echo setup >> '%s'" % marker, "echo verify >> '%s'" % marker
        self.register("alpha", w, "--setup", setup, "--verify", verify)
        self.assertFalse(os.path.exists(marker), "register가 프로젝트 명령을 실행했다")
        self.make_transcript(S1, w)
        registry_before = self.read_bytes(self.registry)
        work_before = self.snapshot(self.work)
        for args in (("alpha",), ()):
            with self.subTest(args=args):
                d = self.pull(*args, sid=S1, cwd=w)
                self.assertEqual(d["key"], "alpha")
                self.assertEqual(d["setup"], [setup])
                self.assertEqual(d["verify"], [verify])
        self.assertFalse(os.path.exists(marker), "pull이 프로젝트 명령을 실행했다")
        self.assertEqual(self.snapshot(self.work), work_before, "pull이 작업 폴더를 바꿨다")
        self.assertEqual(self.read_bytes(self.registry), registry_before, "pull이 레지스트리를 바꿨다")

    def test_pull_reports_the_latest_checkpoint(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.assertStatus(*self.push("alpha", sid=S1, cwd=w), "ok")
        self.assertEqual(self.pull("alpha", sid=S2, cwd=w)["latest"], "C0001")

    def test_pull_refuses_unknown_or_unresolvable_targets(self):
        self.register("alpha", self.make_project("alpha"))
        p, d = self.jh("pull", "nope", sid=S1)
        self.assertError(p, d, "unknown_project")
        self.make_transcript(S1, self.work)
        p, d = self.jh("pull", sid=S1, cwd=self.work)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(d["candidates"], [])
        self.assertFalse(os.path.exists(os.path.join(self.work, "alpha", ".baton")))
