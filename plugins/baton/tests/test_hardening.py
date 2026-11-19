"""P1 코드 리뷰에서 나온 결함의 회귀 테스트.

한 폴더에 키 여럿, 사본 위조, 목록 경로 이탈, UTF-8이 아닌 고정층, 지워진 .baton,
대소문자만 다른 키, 손상 엔트리 복구, 심링크, 사용법 오류, 경로 검증.
"""
import glob
import json
import os
import shutil

from support import DOC_REL, FIXED, FIXED_END, FIXED_START, S1, S2, BatonCase, make_doc, sid

NON_GOAL = "- 기존 파일 형식을 바꾸지 않는다"


class HardeningCase(BatonCase):
    def stored(self, workdir, checkpoint, pattern="handoff*"):
        hits = glob.glob(os.path.join(workdir, ".baton", "checkpoints", "*", checkpoint, pattern))
        self.assertEqual(len(hits), 1, hits)
        return hits[0]

    def index_path(self, workdir):
        hits = glob.glob(os.path.join(workdir, ".baton", "checkpoints", "*", "index.json"))
        self.assertEqual(len(hits), 1, hits)
        return hits[0]


class SharedWorkdirTest(HardeningCase):
    def test_two_keys_in_one_workdir_keep_separate_histories(self):
        w = self.mkdir("work", "mono")
        self.write_doc(w, make_doc(FIXED), "docs/api.md")
        self.write_doc(w, make_doc(FIXED), "docs/web.md")
        self.register("api", w, "--doc", "docs/api.md")
        self.register("web", w, "--doc", "docs/web.md")
        for key in ("api", "web"):
            self.assertStatus(*self.push(key, "--approve-plan-change", sid=S1, cwd=w), "ok")
        self.assertEqual(self.checkpoint_ids("api"), ["C0001"])
        self.assertEqual(self.checkpoint_ids("web"), ["C0001"])

        self.write_doc(w, make_doc(FIXED.replace(NON_GOAL + "\n", "")), "docs/api.md")
        p, d = self.push("api", sid=S1, cwd=w)
        self.assertStatus(p, d, "needs_approval")
        self.assertIn(NON_GOAL, d["fixed_diff"])
        self.assertEqual(self.checkpoint_ids("web"), ["C0001"])


class StoredCopyTest(HardeningCase):
    def setUp(self):
        super().setUp()
        self.w = self.make_project("alpha", make_doc(FIXED))
        self.register("alpha", self.w)
        self.assertStatus(*self.push("alpha", "--approve-plan-change", sid=S1, cwd=self.w), "ok")

    def test_tampered_copy_cannot_launder_a_fixed_change(self):
        dropped = make_doc(FIXED.replace(NON_GOAL + "\n", ""))
        with open(self.stored(self.w, "C0001"), "w", encoding="utf-8") as f:
            f.write(dropped)  # 사본을 바꿔 고정층 비교를 속이려 한다
        self.write_doc(self.w, make_doc(FIXED.replace(NON_GOAL + "\n", ""), "- 바뀜"))
        p, d = self.push("alpha", sid=S1, cwd=self.w)
        self.assertError(p, d, "checkpoint_tampered")
        p = self.run_h("show", "alpha", "C0001")
        self.assertNotEqual(p.returncode, 0)
        self.assertNoTraceback(p)

    def test_index_paths_stay_inside_the_checkpoint(self):
        outside = os.path.join(self.root, "secret.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("SECRET\n")
        path = self.index_path(self.w)
        with open(path, encoding="utf-8") as f:
            index = json.load(f)
        index["checkpoints"][0]["doc_file"] = outside
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f)
        p, d = self.jh("show", "alpha", "C0001")
        self.assertError(p, d, "checkpoint_index_invalid")
        self.assertNotIn("SECRET", p.stdout)

    def test_odd_index_values_never_traceback_in_text_mode(self):
        path = self.index_path(self.w)
        with open(path, encoding="utf-8") as f:
            index = json.load(f)
        index["checkpoints"][0]["at"] = 123
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f)
        p = self.run_h("log", "alpha")
        self.assertEqual(p.returncode, 1)
        self.assertNoTraceback(p)


class EncodingTest(HardeningCase):
    def test_non_utf8_change_in_fixed_layer_needs_approval(self):
        w = self.mkdir("work", "legacy")
        path = os.path.join(w, DOC_REL)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:  # 0xA3, 0xA5는 UTF-8로 읽을 수 없는 바이트다
            f.write(b"# h\n" + FIXED_START.encode() + b"\n- price \xa3\n" + FIXED_END.encode() + b"\n")
        self.register("legacy", w)
        self.assertStatus(*self.push("legacy", "--approve-plan-change", sid=S1, cwd=w), "ok")
        with open(path, "wb") as f:
            f.write(b"# h\n" + FIXED_START.encode() + b"\n- price \xa5\n" + FIXED_END.encode() + b"\n")
        p, d = self.push("legacy", sid=S1, cwd=w)
        self.assertStatus(p, d, "needs_approval")


class WipedHistoryTest(HardeningCase):
    def test_wiped_baton_folder_never_reuses_ids_or_hides_the_other_push(self):
        w = self.make_project("alpha", make_doc(FIXED))
        self.register("alpha", w)
        self.assertStatus(*self.push("alpha", "--approve-plan-change", sid=S1, cwd=w), "ok")
        self.pull("alpha", sid=S2, cwd=w)  # S2의 기준은 지워지기 전의 C0001
        shutil.rmtree(os.path.join(w, ".baton"))  # 예: git clean -fdx

        self.write_doc(w, make_doc(FIXED, "- 지워진 뒤 S1"))
        p, d = self.push("alpha", sid=S1, cwd=w)
        self.assertStatus(p, d, "conflict")
        self.assertNotIn("None", d["message"])
        d = self.pull("alpha", sid=S1, cwd=w)
        self.assertIsNone(d["latest"])
        self.assertTrue(d["notices"], "이력이 사라진 것을 알리지 않았다")
        p, d = self.push("alpha", "--approve-plan-change", sid=S1, cwd=w)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["checkpoint"], "C0002", "지워지기 전의 번호를 다시 썼다")

        self.write_doc(w, make_doc(FIXED, "- S2"))
        self.assertStatus(*self.push("alpha", sid=S2, cwd=w), "conflict")


class NameTest(HardeningCase):
    def test_exact_case_wins_and_case_only_duplicates_are_ambiguous(self):
        wa, wb = self.make_project("upper"), self.make_project("lower")
        self.write_legacy_registry({
            "MyApp": {"workdir": wa, "handoff_doc": DOC_REL},
            "myapp": {"workdir": wb, "handoff_doc": DOC_REL},
        })
        p, d = self.push("myapp", sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["key"], "myapp")
        p, d = self.push("MYAPP", sid=S1)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(sorted(d["candidates"]), ["MyApp", "myapp"])

    def test_key_with_trailing_newline_is_invalid(self):
        w = self.make_project("alpha")
        p, d = self.jh("register", "b\n", "--workdir", w, sid=S1)
        self.assertError(p, d, "invalid_key")


class LegacyEntryTest(HardeningCase):
    def test_set_repairs_a_broken_entry_and_strings_read_as_lists(self):
        w = self.make_project("alpha")
        self.write_legacy_registry({"alpha": {"workdir": w, "handoff_doc": DOC_REL, "verify": 5,
                                              "setup": "true setup"}})
        p, d = self.jh("pull", "alpha", sid=S1)
        self.assertError(p, d, "invalid_entry")
        self.assertStatus(*self.jh("set", "alpha", "--verify", "true verify", sid=S1), "ok")
        d = self.pull("alpha", sid=S1)
        self.assertEqual(d["setup"], ["true setup"])
        self.assertEqual(d["verify"], ["true verify"])

    def test_paths_must_stay_inside_an_absolute_workdir(self):
        w = self.make_project("alpha")
        outside = self.mkdir("outside")
        self.write_doc(outside, make_doc())
        self.write_legacy_registry({
            "rel": {"workdir": ".", "handoff_doc": DOC_REL},
            "abs": {"workdir": w, "handoff_doc": os.path.join(outside, DOC_REL)},
            "up": {"workdir": w, "handoff_doc": "../outside/" + DOC_REL},
        })
        for key in ("rel", "abs", "up"):
            with self.subTest(key=key):
                p, d = self.push(key, sid=S1, cwd=w)
                self.assertError(p, d, "invalid_entry")
        self.assertFalse(os.path.exists(os.path.join(w, ".baton")))
        self.assertFalse(os.path.exists(os.path.join(outside, ".baton")))


class SymlinkTest(HardeningCase):
    def test_symlinked_baton_folder_is_refused(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        elsewhere = self.mkdir("elsewhere")
        os.symlink(elsewhere, os.path.join(w, ".baton"))
        p, d = self.push("alpha", sid=S1, cwd=w)
        self.assertError(p, d, "baton_dir_symlink")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_dangling_gitignore_link_is_not_followed(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        os.makedirs(os.path.join(w, ".baton"))
        target = os.path.join(self.root, "global-ignore")
        os.symlink(target, os.path.join(w, ".baton", ".gitignore"))
        self.assertStatus(*self.push("alpha", sid=S1, cwd=w), "ok")
        self.assertFalse(os.path.exists(target), "링크를 따라가 다른 파일을 만들었다")


class EnvironmentTest(HardeningCase):
    def test_usage_errors_are_json_with_json_flag(self):
        p = self.run_h("push", "--json", "--bogus", sid=S1)
        d = self.parse(p)
        self.assertEqual(p.returncode, 1)
        self.assertEqual((d["status"], d["error"]), ("error", "usage"))

    def test_relative_registry_path_is_refused(self):
        w = self.make_project("alpha")
        env = {"HANDOFF_REGISTRY": "reg/projects.json"}
        p, d = self.jh("pull", "alpha", sid=S1, cwd=w, env=env)
        self.assertError(p, d, "registry_path_relative")
        self.assertFalse(os.path.exists(os.path.join(w, "reg")))
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": S1, "prompt": "/handon alpha"})
        p = self.run_h("hook", stdin=payload, cwd=w, env=env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_pending_folder_named_after_the_session_is_not_a_title(self):
        w = self.make_project("alpha")
        self.write_legacy_registry({"alpha": {"workdir": w, "handoff_doc": DOC_REL}})
        os.makedirs(os.path.join(self.reg_dir, ".pending-titles", sid(1)))
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": sid(1), "prompt": "hello"})
        p = self.run_h("hook", stdin=payload, cwd=w)
        self.assertEqual((p.returncode, p.stdout), (0, ""))
