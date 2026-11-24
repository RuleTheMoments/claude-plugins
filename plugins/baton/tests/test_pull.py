"""pull: 읽기 전용, 대상 확정, 기준 체크포인트 기록, 마지막 체크포인트와의 고정층 비교.

리뷰: Codex H3·2차 High 4(읽기와 실행이 같은 동작), Claude H3(복원이 드리프트 보고보다 먼저),
계획 §5 G4(실행 경계). 계획 P1.
"""
import glob
import os
import re
import shutil

from support import FIXED, S1, S2, BatonCase, make_doc

GOAL_A = "- 기존 형식과 호환되는 저장 기능"
GOAL_B = "- 새 형식으로 갈아타는 저장 기능"


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


class FixedStateTest(BatonCase):
    """pull은 작업 문서의 고정층을 마지막 체크포인트와 비교해 알린다. 어느 쪽이 맞는지 정하지 않고, 아무것도 고치지 않는다.

    리뷰: Codex PR H1(승인 대기 중의 초안을 다음 pull이 그대로 전달), 최종 작업안 3.
    """

    def setUp(self):
        super().setUp()
        self.w = self.make_project("alpha", make_doc(FIXED))
        self.register("alpha", self.w)
        self.make_transcript(S1, self.w)

    def saved(self):
        p, d = self.save("alpha", sid=S1, cwd=self.w)
        self.assertStatus(p, d, "ok")
        return d

    def test_no_checkpoint_means_no_base(self):
        d = self.pull("alpha", sid=S1, cwd=self.w)
        self.assertEqual(d["fixed_state"], "no_base")
        self.assertFalse(d.get("fixed_diff"))

    def test_unpushed_fixed_change_is_shown_as_a_difference(self):
        self.saved()
        self.assertEqual(self.pull("alpha", sid=S2, cwd=self.w)["fixed_state"], "same")
        self.write_doc(self.w, make_doc(FIXED.replace(GOAL_A, GOAL_B)))
        before = self.snapshot(self.work)
        d = self.pull("alpha", sid=S2, cwd=self.w)
        self.assertEqual((d["latest"], d["fixed_state"]), ("C0001", "differs"))
        self.assertIn("-" + GOAL_A, d["fixed_diff"])
        self.assertIn("+" + GOAL_B, d["fixed_diff"])
        self.assertTrue(any("C0001" in n for n in d["notices"]), d["notices"])
        self.assertEqual(self.snapshot(self.work), before, "pull이 작업 폴더를 바꿨다")
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])
        self.assertIn(GOAL_A, self.show("alpha", "C0001"))

    def test_volatile_change_is_not_a_fixed_difference(self):
        self.saved()
        self.assertFalse(self.pull("alpha", sid=S2, cwd=self.w)["doc_changed"])
        self.write_doc(self.w, make_doc(FIXED, "- S1 진행 중"))
        d = self.pull("alpha", sid=S2, cwd=self.w)
        self.assertEqual(d["fixed_state"], "same")
        self.assertTrue(d["doc_changed"])
        self.assertEqual(d["doc_bytes"], len(self.read_bytes(os.path.join(self.w, "docs", "HANDOFF.md"))))
        self.assertFalse(d.get("fixed_diff"))
        self.assertEqual(d["notices"], [])

    def test_plan_file_is_compared_too(self):
        self.write_doc(self.w, "S1\nS2\n", "PLAN.md")
        self.assertStatus(*self.jh("set", "alpha", "--plan-doc", "PLAN.md", sid=S1), "ok")
        self.saved()
        self.write_doc(self.w, "S2\nS1\n", "PLAN.md")
        d = self.pull("alpha", sid=S2, cwd=self.w)
        self.assertEqual(d["fixed_state"], "differs")
        self.assertRegex(d["fixed_diff"], re.compile(r"^[-+]S[12]$", re.M))

    def test_cannot_compare_is_said_not_guessed(self):
        self.write_doc(self.w, "S1\n", "PLAN.md")
        self.assertStatus(*self.jh("set", "alpha", "--plan-doc", "PLAN.md", sid=S1), "ok")
        self.saved()
        good_doc = self.read_doc(self.w)
        copy = glob.glob(os.path.join(self.w, ".baton", "checkpoints", "*", "C0001", "handoff*"))[0]
        good_copy = self.read_bytes(copy)

        def broken_copy():
            with open(copy, "ab") as f:
                f.write(b"tampered\n")

        def unreadable_copy():
            os.chmod(copy, 0)

        def broken_markers():
            self.write_doc(self.w, good_doc.replace("<!-- /baton:fixed -->", ""))

        def missing_plan():
            os.remove(os.path.join(self.w, "PLAN.md"))

        for name, breaker in (("copy", broken_copy), ("unreadable", unreadable_copy), ("markers", broken_markers),
                              ("plan", missing_plan)):
            with self.subTest(case=name):
                before = self.snapshot(self.work)
                breaker()
                try:
                    d = self.pull("alpha", sid=S2, cwd=self.w)  # 읽기는 실패하지 않는다
                    self.assertEqual(d["fixed_state"], "unavailable")
                    self.assertTrue(d["fixed_note"])
                    self.assertFalse(d.get("fixed_diff"))
                finally:  # 다음 경우를 위해 되돌린다
                    os.chmod(copy, 0o600)
                    with open(copy, "wb") as f:
                        f.write(good_copy)
                    self.write_doc(self.w, good_doc)
                    self.write_doc(self.w, "S1\n", "PLAN.md")
                self.assertEqual(self.snapshot(self.work), before, "pull이 작업 폴더에 무언가 남겼다")

    def test_wiped_history_is_no_base_with_a_notice(self):
        self.saved()
        shutil.rmtree(os.path.join(self.w, ".baton"))
        d = self.pull("alpha", sid=S2, cwd=self.w)
        self.assertEqual(d["fixed_state"], "no_base")
        self.assertTrue(any("이력" in n for n in d["notices"]), d["notices"])
