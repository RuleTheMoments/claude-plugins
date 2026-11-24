"""레지스트리 쓰기 일원화와 손상 대응.

리뷰: Codex H1(동시 저장 손실)·M2(레지스트리 경로 불일치)·M3(손상 엔트리), Claude M3(JSON 문법 오류),
Codex 2차(심링크 설정 경로, 예약어 키). 계획 P1.
"""
import os

from support import DOC_REL, S1, BatonCase, make_doc, sid


class RegisterTest(BatonCase):
    def test_creates_registry_file_when_missing(self):
        w = self.make_project("alpha")
        self.assertFalse(os.path.exists(self.registry))
        self.register("alpha", w)
        self.assertTrue(os.path.isfile(self.registry))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")),
                         "HANDOFF_REGISTRY가 있는데 기본 경로에 썼다")
        d = self.pull("alpha", sid=S1)
        self.assertEqual(d["key"], "alpha")
        self.assertEqual(d["workdir"], w)
        self.assertEqual(d["handoff_doc"], os.path.join(w, DOC_REL))
        self.assertIsNone(d["plan_doc"])
        self.assertIsNone(d["latest"])

    def test_stores_every_given_field(self):
        w = self.make_project("alpha", rel="HANDOFF.md")
        self.write_doc(w, "S1\n", "PLAN.md")
        self.register("alpha", w, "--doc", "HANDOFF.md", "--plan-doc", "PLAN.md",
                      "--alias", "알파", "--alias", "a1", "--one-line", "S1부터",
                      "--setup", "true setup", "--verify", "true verify", "--warning", "미커밋 있음")
        d = self.pull("alpha", sid=S1)
        self.assertEqual(d["handoff_doc"], os.path.join(w, "HANDOFF.md"))
        self.assertEqual(d["plan_doc"], os.path.join(w, "PLAN.md"))
        self.assertEqual(d["one_line"], "S1부터")
        self.assertEqual(d["setup"], ["true setup"])
        self.assertEqual(d["verify"], ["true verify"])
        self.assertEqual(d["warnings"], ["미커밋 있음"])
        self.assertEqual(self.jh("resolve", "알파")[1]["match"], "alpha")

    def test_rejects_reserved_keys(self):
        w = self.make_project("alpha")
        for key in ("list", "LS", "목록"):
            with self.subTest(key=key):
                p, d = self.jh("register", key, "--workdir", w, sid=S1)
                self.assertError(p, d, "reserved_key")
        self.assertFalse(os.path.exists(self.registry))

    def test_rejects_invalid_keys(self):
        w = self.make_project("alpha")
        for key in ("my app", "a/b", ""):
            with self.subTest(key=key):
                p, d = self.jh("register", key, "--workdir", w, sid=S1)
                self.assertError(p, d, "invalid_key")
        self.assertFalse(os.path.exists(self.registry))

    def test_rejects_key_and_alias_conflicts(self):
        self.register("alpha", self.make_project("alpha"), "--alias", "a1")
        before = self.read_bytes(self.registry)
        wb = self.make_project("beta")
        cases = [
            (["Alpha"], "key_exists"),               # 대소문자만 다른 키
            (["beta", "--alias", "ALPHA"], "alias_conflict"),  # 별칭이 기존 키와 겹침
            (["beta", "--alias", "a1"], "alias_conflict"),     # 별칭이 기존 별칭과 겹침
            (["a1"], "alias_conflict"),              # 키가 기존 별칭과 겹침
        ]
        for args, code in cases:
            with self.subTest(args=args):
                p, d = self.jh("register", args[0], "--workdir", wb, *args[1:], sid=S1)
                self.assertError(p, d, code)
        self.assertEqual(self.read_bytes(self.registry), before)

    def test_requires_existing_workdir_and_doc(self):
        p, d = self.jh("register", "alpha", "--workdir", os.path.join(self.root, "missing"), sid=S1)
        self.assertError(p, d, "workdir_missing")
        bare = self.mkdir("work", "bare")
        p, d = self.jh("register", "alpha", "--workdir", bare, sid=S1)
        self.assertError(p, d, "doc_missing")
        self.assertFalse(os.path.exists(self.registry))


class SetTest(BatonCase):
    def setUp(self):
        super().setUp()
        self.w = self.make_project("alpha")
        self.register("alpha", self.w, "--setup", "true setup", "--verify", "true verify",
                      "--one-line", "처음")

    def test_changes_only_named_fields(self):
        p, d = self.jh("set", "alpha", "--one-line", "S2 진행", "--warning", "w1", "--warning", "w2", sid=S1)
        self.assertStatus(p, d, "ok")
        d = self.pull("alpha", sid=S1)
        self.assertEqual(d["one_line"], "S2 진행")
        self.assertEqual(d["warnings"], ["w1", "w2"])
        self.assertEqual(d["setup"], ["true setup"])
        self.assertEqual(d["verify"], ["true verify"])

        p, d = self.jh("set", "alpha", "--clear", "warnings", sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(self.pull("alpha", sid=S1)["warnings"], [])

    def test_alias_changes_keep_names_unique(self):
        self.register("beta", self.make_project("beta"), "--alias", "b1")
        before = self.read_bytes(self.registry)
        for alias, code in (("beta", "alias_conflict"), ("B1", "alias_conflict"), ("목록", "reserved_key")):
            with self.subTest(alias=alias):
                p, d = self.jh("set", "alpha", "--alias", alias, sid=S1)
                self.assertError(p, d, code)
        self.assertEqual(self.read_bytes(self.registry), before)
        self.assertStatus(*self.jh("set", "alpha", "--alias", "알파", sid=S1), "ok")
        self.assertEqual(self.jh("resolve", "알파")[1]["match"], "alpha")

    def test_rejects_unknown_project_and_missing_plan_doc(self):
        p, d = self.jh("set", "nope", "--one-line", "x", sid=S1)
        self.assertError(p, d, "unknown_project")
        p, d = self.jh("set", "alpha", "--plan-doc", "nope.md", sid=S1)
        self.assertError(p, d, "plan_doc_missing")


class ConcurrentWriteTest(BatonCase):
    def test_parallel_register_set_push_keep_every_write(self):
        """등록·필드 수정·push가 동시에 돌아도 어느 쓰기도 조용히 사라지지 않는다."""
        n = 6
        olds = []
        for i in range(n):
            w = self.make_project("old%d" % i)
            self.register("old%d" % i, w)
            self.write_doc(w, make_doc(status="- 진행 %d" % i))
            olds.append(w)
        news = [self.make_project("new%d" % i) for i in range(n)]

        procs = []
        for i in range(n):
            procs.append(self.spawn("register", "new%d" % i, "--workdir", news[i], sid=sid(100 + i)))
            procs.append(self.spawn("set", "old%d" % i, "--one-line", "갱신 %d" % i, sid=sid(200 + i)))
            procs.append(self.spawn("push", "old%d" % i, sid=sid(300 + i)))
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, "%s\n%s\n%s" % (proc.args[3:], out, err))

        for i in range(n):
            self.pull("new%d" % i, sid=S1)
            self.assertEqual(self.pull("old%d" % i, sid=S1)["one_line"], "갱신 %d" % i)
            lg = self.log("old%d" % i)
            self.assertEqual(lg["latest"], "C0001")
            self.assertEqual(lg["registry_latest"], "C0001")


class LoadRobustnessTest(BatonCase):
    def test_syntax_error_is_diagnosed_and_never_overwritten(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        p, d = self.jh("set", "alpha", "--one-line", "x", sid=S1)  # 두 번째 쓰기 → 유효한 .bak
        self.assertStatus(p, d, "ok")
        good = self.read_bytes(self.registry).decode("utf-8").rstrip()
        bad = good[:-1] + ",}\n"  # 모델이 손으로 고치다 남긴 쉼표 하나
        with open(self.registry, "w", encoding="utf-8") as f:
            f.write(bad)
        wb = self.make_project("beta")

        for args in (["list"], ["resolve", "alpha"], ["pull", "alpha"], ["push", "alpha"],
                     ["set", "alpha", "--one-line", "y"], ["register", "beta", "--workdir", wb], ["log", "alpha"]):
            with self.subTest(args=args):
                p = self.run_h(*args, sid=S1, cwd=w)
                self.assertNoTraceback(p)
                self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.read_bytes(self.registry).decode("utf-8"), bad, "손상된 레지스트리를 덮어썼다")

        p, d = self.jh("list", sid=S1)
        self.assertError(p, d, "registry_invalid")
        self.assertTrue(d["backup"]["valid"], d)

    def test_existing_file_without_projects_is_an_error_and_backup_survives(self):
        """본문이 {}인데 빈 레지스트리로 받아들이면, 다음 쓰기가 마지막 정상 백업을 {}로 바꾼다."""
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.assertStatus(*self.jh("set", "alpha", "--one-line", "x", sid=S1), "ok")  # 두 번째 쓰기 → 유효한 .bak
        backup = self.read_bytes(self.registry + ".bak")
        self.assertIn(b"alpha", backup)
        wb = self.make_project("beta")
        for body in ("{}\n", "[]\n", '{"projects": []}\n'):
            with self.subTest(body=body):
                with open(self.registry, "w", encoding="utf-8") as f:
                    f.write(body)
                p, d = self.jh("list", sid=S1)
                self.assertError(p, d, "registry_invalid")
                self.assertTrue(d["backup"]["valid"], d)
                p, d = self.jh("register", "beta", "--workdir", wb)
                self.assertError(p, d, "registry_invalid")
                self.assertEqual(self.read_bytes(self.registry).decode("utf-8"), body, "손상된 본문을 덮어썼다")
                self.assertEqual(self.read_bytes(self.registry + ".bak"), backup, "정상 백업을 바꿨다")

    def test_empty_projects_object_is_a_valid_registry(self):
        os.makedirs(self.reg_dir, exist_ok=True)
        with open(self.registry, "w", encoding="utf-8") as f:
            f.write('{"projects": {}}\n')
        p, d = self.jh("list", sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["projects"], [])
        self.register("alpha", self.make_project("alpha"))
        self.assertEqual([s["key"] for s in self.jh("list", sid=S1)[1]["projects"]], ["alpha"])

    def test_one_bad_entry_does_not_break_others(self):
        wa = self.make_project("alpha")
        wd = self.make_project("delta")
        self.write_legacy_registry({
            "alpha": {"workdir": wa, "handoff_doc": DOC_REL},
            "gamma": {"workdir": 123, "handoff_doc": "x"},
            "delta": {"workdir": wd},
            "epsilon": "깨진 엔트리",
        })
        p, d = self.jh("list", sid=S1)
        self.assertStatus(p, d, "ok")
        states = {s["key"]: s for s in d["projects"]}
        self.assertEqual(states["alpha"]["issues"], [])
        for key in ("gamma", "delta", "epsilon"):
            with self.subTest(key=key):
                self.assertIn(key, states)
                self.assertTrue(states[key]["issues"], states[key])
                p, d = self.push(key, sid=S1)
                self.assertError(p, d, "invalid_entry")

    def test_symlinked_config_dir_has_no_false_mismatch(self):
        link = os.path.join(self.root, "cfglink")
        os.symlink(self.cfg, link)
        env = {"CLAUDE_CONFIG_DIR": link}
        w = self.make_project("alpha")
        self.make_transcript(S1, w)
        self.register("alpha", w)
        p, d = self.push("alpha", sid=S1, cwd=w, env=env)
        self.assertStatus(p, d, "ok")
        p, d = self.jh("list", sid=S1, cwd=w, env=env)
        self.assertStatus(p, d, "ok")
        states = {s["key"]: s for s in d["projects"]}
        self.assertEqual(states["alpha"]["issues"], [])
        # push가 이 세션의 트랜스크립트 폴더를 기록했으므로 이 폴더에서 이어받을 수 있다
        self.assertTrue(states["alpha"]["registered_here"], states["alpha"])
        self.assertTrue(states["alpha"]["resumable"], states["alpha"])
