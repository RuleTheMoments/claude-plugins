"""push: 체크포인트, 고정층 보호, 충돌, 복구.

리뷰: Claude §2-1(압축된 컨텍스트로 문서 재작성)·§2-4(같은 문서에 두 push)·§2-5(브랜치·HEAD),
Codex 2차 High 1·3·5, 계획 §5 G1(방향 보존)·G2(정확한 쓰기)·G3(복구). 계획 P1.
"""
import os
import re
import subprocess

from support import FIXED, FIXED_START, S1, S2, BatonCase, make_doc, sid

NON_GOAL = "- 기존 파일 형식을 바꾸지 않는다"


class PushCase(BatonCase):
    def setUp(self):
        super().setUp()
        self.w = self.make_project("alpha", make_doc(FIXED))
        self.register("alpha", self.w)
        self.make_transcript(S1, self.w)

    def push_ok(self, *flags, session=S1, status="ok"):
        p, d = self.push("alpha", *flags, sid=session, cwd=self.w)
        self.assertStatus(p, d, status)
        return d

    def reviewed(self, session=S1):
        """고정층이 바뀐 저장. 먼저 diff와 검토값을 받고, 사용자가 저장하기로 하면 그 값으로 다시 부른다."""
        p, d = self.push("alpha", sid=session, cwd=self.w)
        self.assertStatus(p, d, "needs_review")
        return self.push_ok("--review", d["review"], session=session)

    def approve_first(self, session=S1):
        d = self.reviewed(session)
        self.assertEqual(d["checkpoint"], "C0001")
        return d


class FixedLayerTest(PushCase):
    """G1: 방향이 바뀐 저장은 diff를 거쳐야만 사본이 되고, 저장된 변경은 이전 내용과 함께 남는다."""

    def test_first_fixed_layer_needs_review(self):
        p, d = self.push("alpha", sid=S1, cwd=self.w)
        self.assertStatus(p, d, "needs_review")
        self.assertIn(NON_GOAL, d["fixed_diff"])
        self.assertEqual(self.checkpoint_ids("alpha"), [])
        self.assertTrue(self.approve_first()["plan_changed"])

    def test_volatile_change_needs_no_approval(self):
        self.approve_first()
        moved = "# 핸드오프\n\n## 메모\n\n- 고정층 위에 새 절\n\n" + make_doc(FIXED, "- S1 진행 중").split("\n", 2)[2]
        self.write_doc(self.w, moved)
        d = self.push_ok()
        self.assertEqual(d["checkpoint"], "C0002")
        self.assertFalse(d["plan_changed"])
        self.assertFalse(self.log("alpha")["checkpoints"][1]["plan_changed"])

    def test_dropped_non_goal_is_refused_then_kept_in_history(self):
        self.approve_first()
        dropped = make_doc(FIXED.replace(NON_GOAL + "\n", ""), "- S1 끝, UI 시작")
        self.write_doc(self.w, dropped)
        p, d = self.push("alpha", sid=S1, cwd=self.w)
        self.assertStatus(p, d, "needs_review")
        self.assertIn(NON_GOAL, d["fixed_diff"])
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])
        self.assertEqual(self.read_doc(self.w), dropped, "헬퍼가 작업 문서를 고쳤다")

        d = self.push_ok("--review", d["review"])
        self.assertEqual(d["checkpoint"], "C0002")
        self.assertTrue(d["plan_changed"])
        self.assertIn(NON_GOAL, self.show("alpha", "C0001"))
        self.assertNotIn(NON_GOAL, self.show("alpha", "C0002"))

        # 승인한 변경이 새 기준이다. 다음 일반 수정에 다시 승인을 요구하지 않는다
        self.write_doc(self.w, make_doc(FIXED.replace(NON_GOAL + "\n", ""), "- S2 시작"))
        d = self.push_ok()
        self.assertEqual(d["checkpoint"], "C0003")
        self.assertFalse(d["plan_changed"])

    def test_reordered_steps_need_approval(self):
        self.approve_first()
        swapped = FIXED.replace("- S2 저장 기능 (S1 통과 뒤)\n- S3 UI (S2 완료 뒤)",
                                "- S3 UI (S2 완료 뒤)\n- S2 저장 기능 (S1 통과 뒤)")
        self.write_doc(self.w, make_doc(swapped))
        self.assertStatus(*self.push("alpha", sid=S1, cwd=self.w), "needs_review")

    def test_removing_the_markers_counts_as_a_change(self):
        self.approve_first()
        self.write_doc(self.w, make_doc(None, "- 고정층을 통째로 지움"))
        p, d = self.push("alpha", sid=S1, cwd=self.w)
        self.assertStatus(p, d, "needs_review")
        self.assertIn(NON_GOAL, d["fixed_diff"])

    def test_unbalanced_marker_is_refused(self):
        self.write_doc(self.w, "# 핸드오프\n\n" + FIXED_START + "\n- 닫는 표시 없음\n")
        p, d = self.push("alpha", "--review", "0" * 16, sid=S1, cwd=self.w)
        self.assertError(p, d, "fixed_marker_unbalanced")
        self.assertEqual(self.checkpoint_ids("alpha"), [])

    def test_legacy_doc_without_markers_pushes_directly(self):
        wb = self.make_project("beta")
        self.register("beta", wb)
        p, d = self.push("beta", sid=S1, cwd=wb)
        self.assertStatus(p, d, "ok")
        self.assertFalse(d["plan_changed"])

    def test_plan_doc_is_part_of_the_fixed_layer(self):
        self.write_doc(self.w, "S1\nS2\n", "PLAN.md")
        self.assertStatus(*self.jh("set", "alpha", "--plan-doc", "PLAN.md", sid=S1), "ok")
        self.approve_first()
        p, d = self.jh("show", "alpha", "C0001", sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["plan"], "S1\nS2\n")

        self.write_doc(self.w, "S2\nS1\n", "PLAN.md")
        p, d = self.push("alpha", sid=S1, cwd=self.w)
        self.assertStatus(p, d, "needs_review")
        self.assertRegex(d["fixed_diff"], re.compile(r"^[-+]S[12]$", re.M))  # 문맥 줄이 아니라 바뀐 줄


class ReviewTest(PushCase):
    """검토값: 보여 준 변경과 저장되는 내용이 같을 때만 사본을 만든다.
    사람이 승인했다는 증명이 아니다 — 프로젝트·부모 체크포인트·후보 고정층이 그대로인지 확인할 뿐이다.

    리뷰: Codex PR H2(B의 diff를 본 뒤 C가 저장됨), Claude 평가 R5(첫 호출부터 플래그), 최종 작업안 4.
    """

    def change_goal(self, word, status="- 시작 전"):
        self.write_doc(self.w, make_doc(FIXED.replace("호환되는", word), status))

    def ask(self, *flags, key="alpha", cwd=None):
        p, d = self.push(key, *flags, sid=S1, cwd=cwd or self.w)
        self.assertStatus(p, d, "needs_review")
        return d

    def test_changed_after_the_diff_needs_a_new_review(self):
        self.approve_first()
        self.change_goal("B안")
        first = self.ask()
        self.assertIn("B안", first["fixed_diff"])
        self.change_goal("C안")  # 사용자가 B의 diff를 보는 사이 문서가 C가 됐다
        d = self.ask("--review", first["review"])
        self.assertIn("C안", d["fixed_diff"])
        self.assertNotEqual(d["review"], first["review"])
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])
        d = self.push_ok("--review", d["review"])
        self.assertEqual(d["checkpoint"], "C0002")
        self.assertIn("C안", self.show("alpha", "C0002"))

    def test_no_value_or_a_made_up_value_cannot_save(self):
        self.approve_first()
        self.change_goal("B안")
        for flags in ([], ["--review", "0" * 16], ["--review", ""]):
            with self.subTest(flags=flags):
                self.ask(*flags)
                self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])
        p, d = self.push("alpha", "--approve-plan-change", sid=S1, cwd=self.w)  # 0.4 초안의 플래그
        self.assertError(p, d, "approve_flag_removed")
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])

    def test_first_fixed_layer_cannot_be_saved_in_one_call(self):
        self.ask("--review", "0" * 16)
        self.assertEqual(self.checkpoint_ids("alpha"), [])

    def test_review_is_bound_to_the_parent_checkpoint(self):
        self.approve_first()
        self.change_goal("B안")
        token = self.ask()["review"]
        self.write_doc(self.w, make_doc(FIXED, "- S1 진행"))  # 고정층은 그대로 두고 진행만 저장 → 부모가 C0002가 된다
        self.assertEqual(self.push_ok()["checkpoint"], "C0002")
        self.change_goal("B안", "- S1 진행")
        d = self.ask("--review", token)
        self.assertNotEqual(d["review"], token)
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001", "C0002"])

    def test_review_is_bound_to_the_project(self):
        self.approve_first()
        wb = self.make_project("beta", make_doc(FIXED))
        self.register("beta", wb)
        self.assertStatus(*self.save("beta", sid=S1, cwd=wb), "ok")
        self.change_goal("B안")
        self.write_doc(wb, self.read_doc(self.w))  # 두 프로젝트의 문서가 글자까지 같다
        token = self.ask()["review"]
        d = self.ask("--review", token, key="beta", cwd=wb)
        self.assertNotEqual(d["review"], token)
        self.assertEqual(self.checkpoint_ids("beta"), ["C0001"])

    def test_same_review_twice_keeps_one_copy(self):
        self.approve_first()
        self.change_goal("B안")
        token = self.ask()["review"]
        self.assertEqual(self.push_ok("--review", token)["checkpoint"], "C0002")
        d = self.push_ok("--review", token, status="unchanged")
        self.assertEqual(d["checkpoint"], "C0002")
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001", "C0002"])

    def test_plan_file_at_another_path_is_not_unchanged(self):
        self.write_doc(self.w, "S1\nS2\n", "PLAN.md")
        self.assertStatus(*self.jh("set", "alpha", "--plan-doc", "PLAN.md", sid=S1), "ok")
        self.approve_first()
        self.write_doc(self.w, "S1\nS2\n", os.path.join("docs", "PLAN2.md"))  # 내용은 같고 경로만 다르다
        self.assertStatus(*self.jh("set", "alpha", "--plan-doc", os.path.join("docs", "PLAN2.md"), sid=S1), "ok")
        d = self.ask()
        self.assertIn("PLAN.md", d["fixed_diff"])
        self.assertIn("PLAN2.md", d["fixed_diff"])
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])

    def test_conflict_on_the_plan_file_alone_shows_a_diff(self):
        self.write_doc(self.w, "S1\nS2\n", "PLAN.md")
        self.assertStatus(*self.jh("set", "alpha", "--plan-doc", "PLAN.md", sid=S1), "ok")
        self.approve_first()
        self.pull("alpha", sid=S2, cwd=self.w)
        self.write_doc(self.w, "S1\nS2\nS3\n", "PLAN.md")
        self.assertEqual(self.reviewed()["checkpoint"], "C0002")  # S1이 계획 파일만 바꿔 저장했다
        self.write_doc(self.w, "S1\nS9\n", "PLAN.md")
        p, d = self.push("alpha", sid=S2, cwd=self.w)
        self.assertStatus(p, d, "conflict")
        self.assertIn("S9", d["diff"])


class ConflictTest(PushCase):
    """G2: 같은 문서에 두 세션이 push해도 조용히 덮이지 않는다."""

    def test_push_after_another_sessions_push_conflicts(self):
        self.approve_first()
        self.pull("alpha", sid=S1, cwd=self.w)
        self.pull("alpha", sid=S2, cwd=self.w)
        v_a = make_doc(FIXED, "- A의 기록")
        self.write_doc(self.w, v_a)
        self.assertEqual(self.push_ok()["checkpoint"], "C0002")

        v_b = make_doc(FIXED, "- B의 기록")
        self.write_doc(self.w, v_b)  # B가 A의 기록을 모르고 덮었다
        p, d = self.push("alpha", sid=S2, cwd=self.w)
        self.assertStatus(p, d, "conflict")
        self.assertEqual((d["base"], d["latest"]), ("C0001", "C0002"))
        self.assertIn("A의 기록", d["diff"])
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001", "C0002"])
        self.assertEqual(self.show("alpha", "C0002"), v_a)  # A의 내용은 체크포인트에
        self.assertEqual(self.read_doc(self.w), v_b)        # B의 내용은 작업 문서에 그대로

        self.assertEqual(self.pull("alpha", sid=S2, cwd=self.w)["latest"], "C0002")
        self.write_doc(self.w, make_doc(FIXED, "- A의 기록\n- B의 기록"))
        d = self.push_ok(session=S2)
        self.assertEqual((d["checkpoint"], d["parent"]), ("C0003", "C0002"))

    def test_pull_before_any_checkpoint_still_detects_the_other_push(self):
        """0.3.0에서 넘어온 엔트리는 체크포인트 없이 시작한다. 그 상태에서 두 세션이 pull해도 덮이지 않는다."""
        wb = self.make_project("beta")
        self.register("beta", wb)
        self.assertIsNone(self.pull("beta", sid=S1, cwd=wb)["latest"])
        self.assertIsNone(self.pull("beta", sid=S2, cwd=wb)["latest"])
        self.write_doc(wb, make_doc(status="- A"))
        p, d = self.push("beta", sid=S1, cwd=wb)
        self.assertStatus(p, d, "ok")
        self.write_doc(wb, make_doc(status="- B"))
        p, d = self.push("beta", sid=S2, cwd=wb)
        self.assertStatus(p, d, "conflict")
        self.assertEqual((d["base"], d["latest"]), (None, "C0001"))
        self.assertEqual(self.checkpoint_ids("beta"), ["C0001"])

    def test_lost_binding_does_not_turn_own_push_into_conflict(self):
        """바인딩을 못 써도, 최신 체크포인트를 이 세션이 썼다면 충돌이 아니다."""
        self.approve_first()
        self.write_doc(self.w, make_doc(FIXED, "- 두 번째"))
        pulls = os.path.join(self.reg_dir, ".pulls")
        os.chmod(pulls, 0o555)
        try:
            self.assertEqual(self.push_ok()["checkpoint"], "C0002")
        finally:
            os.chmod(pulls, 0o700)
        self.write_doc(self.w, make_doc(FIXED, "- 세 번째"))
        self.assertEqual(self.push_ok()["checkpoint"], "C0003")

    def test_explicit_push_without_pull_builds_on_latest(self):
        self.approve_first()
        self.write_doc(self.w, make_doc(FIXED, "- pull 없이 이어 씀"))
        d = self.push_ok(session=sid(50))
        self.assertEqual((d["checkpoint"], d["parent"]), ("C0002", "C0001"))

    def test_concurrent_pushes_of_one_project_get_unique_ids(self):
        self.approve_first()
        self.write_doc(self.w, make_doc(FIXED, "- 동시에 저장"))
        procs = [self.spawn("push", "alpha", sid=sid(400 + i), cwd=self.w) for i in range(5)]
        statuses = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, "%s\n%s" % (out, err))
            statuses.append(self.parse(subprocess.CompletedProcess(proc.args, 0, out, err))["status"])
        self.assertEqual(sorted(statuses), ["ok"] + ["unchanged"] * 4)
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001", "C0002"])


class HistoryTest(PushCase):
    """G3: 저장 시점마다 그때의 내용을 다시 읽을 수 있다."""

    def test_two_pushes_in_one_session_and_day_keep_both(self):
        v1 = make_doc(FIXED)
        self.approve_first()
        v2 = make_doc(FIXED, "- S1 통과 12/12")
        self.write_doc(self.w, v2)
        self.assertEqual(self.push_ok()["checkpoint"], "C0002")
        self.assertEqual(self.show("alpha", "C0001"), v1)
        self.assertEqual(self.show("alpha", "C0002"), v2)
        diff = self.run_h("show", "alpha", "C0001", "--against", "C0002").stdout
        self.assertIn("-- 시작 전", diff)
        self.assertIn("+- S1 통과 12/12", diff)

    def test_identical_retry_creates_no_checkpoint(self):
        self.approve_first()
        d = self.push_ok(status="unchanged")
        self.assertEqual(d["checkpoint"], "C0001")
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001"])

    def test_history_survives_transcript_deletion(self):
        self.approve_first()
        cp = self.log("alpha")["checkpoints"][0]
        self.assertEqual(cp["session_id"], S1)
        self.assertEqual(cp["transcript_dir"], self.transcript_dir_for(self.w))
        self.assertTrue(cp["transcript_available"])

        os.remove(os.path.join(self.transcript_dir_for(self.w), S1 + ".jsonl"))
        cp = self.log("alpha")["checkpoints"][0]
        self.assertFalse(cp["transcript_available"])
        self.assertEqual(self.show("alpha", "C0001"), make_doc(FIXED))

    def test_push_works_without_any_transcript(self):
        wb = self.make_project("beta")
        self.register("beta", wb)
        p, d = self.push("beta", sid=sid(9), cwd=wb)
        self.assertStatus(p, d, "ok")
        cp = self.log("beta")["checkpoints"][0]
        self.assertIsNone(cp["transcript_dir"])
        self.assertFalse(cp["transcript_available"])

    def test_non_git_workdir_records_no_branch_or_head(self):
        self.approve_first()
        cp = self.log("alpha")["checkpoints"][0]
        self.assertIsNone(cp["branch"])
        self.assertIsNone(cp["head"])

    def test_git_workdir_records_branch_and_head(self):
        wg = self.make_project("gitted", make_doc(), git=True, commit=True)
        self.register("gitted", wg)
        self.assertStatus(*self.push("gitted", sid=S1, cwd=wg), "ok")
        cp = self.log("gitted")["checkpoints"][0]
        self.assertEqual(cp["branch"], "main")
        self.assertEqual(cp["head"], self.git(wg, "rev-parse", "HEAD"))

    def test_unborn_git_repo_has_no_head(self):
        wg = self.make_project("fresh", make_doc(), git=True)
        self.register("fresh", wg)
        self.assertStatus(*self.push("fresh", sid=S1, cwd=wg), "ok")
        self.assertIsNone(self.log("fresh")["checkpoints"][0]["head"])

    def test_baton_folder_ignores_itself(self):
        wg = self.make_project("gitted", make_doc(), git=True, commit=True)
        self.register("gitted", wg)
        self.assertStatus(*self.push("gitted", sid=S1, cwd=wg), "ok")
        with open(os.path.join(wg, ".baton", ".gitignore"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "*\n")
        status = self.git(wg, "status", "--porcelain", "--untracked-files=all")
        self.assertNotIn(".baton", status)

    def test_registry_failure_is_partial_and_retry_repairs(self):
        if os.geteuid() == 0:
            self.skipTest("root는 읽기 전용 폴더에도 쓸 수 있다")
        self.approve_first()
        v2 = make_doc(FIXED, "- 색인 실패 직전")
        self.write_doc(self.w, v2)
        mode = os.stat(self.registry).st_mode
        os.chmod(self.registry, 0o444)  # 제자리 쓰기도, 임시 파일 교체도 막는다
        os.chmod(self.reg_dir, 0o555)
        try:
            p, d = self.push("alpha", sid=S1, cwd=self.w)
        finally:
            os.chmod(self.reg_dir, 0o700)
            os.chmod(self.registry, mode)
        self.assertStatus(p, d, "partial")
        self.assertEqual((d["checkpoint"], d["registry_latest"]), ("C0002", "C0001"))
        lg = self.log("alpha")
        self.assertEqual((lg["latest"], lg["registry_latest"]), ("C0002", "C0001"))
        self.assertEqual(self.show("alpha", "C0002"), v2)

        p, d = self.push("alpha", sid=S1, cwd=self.w)
        self.assertStatus(p, d, "unchanged", "repaired")
        self.assertEqual(d["checkpoint"], "C0002")
        lg = self.log("alpha")
        self.assertEqual(lg["registry_latest"], "C0002")
        self.assertEqual(self.checkpoint_ids("alpha"), ["C0001", "C0002"])
