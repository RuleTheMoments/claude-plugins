"""프로젝트 선택과 세션 바인딩, 훅 범위.

리뷰: Codex H2(선택이 약한 단서를 우선)·M4(다른 네임스페이스, 만료 예약), Claude H2(다른 프로젝트 문서 오염),
결정 2(키가 비면 이 세션의 바인딩, 없으면 묻는다). 계획 P1.
"""
import json
import os
import time

from support import DOC_REL, S1, S2, BatonCase, make_doc, sid


class NameMatchTest(BatonCase):
    def test_exact_key_beats_alias(self):
        self.write_legacy_registry({
            "alpha": {"workdir": self.make_project("alpha"), "handoff_doc": DOC_REL, "aliases": ["beta"]},
            "beta": {"workdir": self.make_project("beta"), "handoff_doc": DOC_REL},
        })
        self.assertEqual(self.jh("resolve", "beta")[1]["match"], "beta")

    def test_shared_alias_is_ambiguous(self):
        self.write_legacy_registry({
            "alpha": {"workdir": self.make_project("alpha"), "handoff_doc": DOC_REL, "aliases": ["app"]},
            "gamma": {"workdir": self.make_project("gamma"), "handoff_doc": DOC_REL, "aliases": ["app"]},
        })
        d = self.jh("resolve", "app")[1]
        self.assertIsNone(d["match"])
        self.assertEqual(sorted(d["candidates"]), ["alpha", "gamma"])

    def test_extra_words_suggest_but_do_not_select(self):
        self.write_legacy_registry({"alpha": {"workdir": self.make_project("alpha"), "handoff_doc": DOC_REL}})
        d = self.jh("resolve", "alpha", "아무말")[1]
        self.assertIsNone(d["match"])
        self.assertIn("alpha", d["candidates"])


def make_h2a(case):
    """beta 폴더에서 띄운 세션 S1. alpha의 지난 세션도 beta 폴더에서, beta의 지난 세션은 beta/sub에서 띄웠다."""
    wa = case.make_project("alpha")
    wb = case.make_project("beta")
    sub = case.mkdir("work", "beta", "sub")
    a_last, b_last = sid(11), sid(12)
    case.make_transcript(a_last, wb)
    case.make_transcript(b_last, sub)
    case.make_transcript(S1, wb)
    case.write_legacy_registry({
        "alpha": {"workdir": wa, "handoff_doc": DOC_REL, "last_session_id": a_last,
                  "transcript_dir": case.transcript_dir_for(wb)},
        "beta": {"workdir": wb, "handoff_doc": DOC_REL, "last_session_id": b_last,
                 "transcript_dir": case.transcript_dir_for(sub)},
    })
    return wa, wb


class LaunchFolderTest(BatonCase):
    def test_no_name_prefers_workdir_over_transcript_folder(self):
        wa, wb = make_h2a(self)
        self.assertEqual(self.jh("resolve", sid=S1, cwd=wb)[1]["match"], "beta")
        self.assertEqual(self.pull(sid=S1, cwd=wb)["key"], "beta")

    def test_nested_workdirs_pick_the_deepest(self):
        mono = self.mkdir("work", "mono")
        pkg = self.mkdir("work", "mono", "pkg")
        src = self.mkdir("work", "mono", "pkg", "src")
        for w in (mono, pkg):
            self.write_doc(w, make_doc())
        self.make_transcript(S1, src)
        self.write_legacy_registry({
            "mono": {"workdir": mono, "handoff_doc": DOC_REL},
            "pkg": {"workdir": pkg, "handoff_doc": DOC_REL},
        })
        self.assertEqual(self.jh("resolve", sid=S1, cwd=src)[1]["match"], "pkg")


class PushTargetTest(BatonCase):
    """쓰기 대상. 헬퍼의 push는 키를 받아야만 쓴다. 키 없는 /handoff의 대상은 current가 정해 준다 —
    이 세션의 바인딩과 실행 폴더의 프로젝트가 같은 키일 때만. 어긋나면 정해 주지 않고, 스킬이 묻는다."""

    def current(self, session, cwd=None):
        p, d = self.jh("current", sid=session, cwd=cwd)
        self.assertStatus(p, d, "ok")
        return d

    def test_no_key_without_binding_asks_and_writes_nothing(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.make_transcript(S1, w)
        self.write_doc(w, make_doc(status="- 바뀜"))
        p, d = self.push(sid=S1, cwd=w)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(d["candidates"], ["alpha"])
        self.assertEqual(self.checkpoint_ids("alpha"), [])
        self.assertFalse(os.path.exists(os.path.join(w, ".baton")))

    def test_no_key_outside_every_workdir_has_no_candidates(self):
        self.register("alpha", self.make_project("alpha"))
        self.make_transcript(S1, self.work)
        p, d = self.push(sid=S1, cwd=self.work)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(d["candidates"], [])

    def test_no_key_never_writes_even_when_bound(self):
        """바인딩은 후보일 뿐이다. 헬퍼는 키 없이 쓰지 않는다."""
        wa, wb = make_h2a(self)
        self.pull("beta", sid=S1, cwd=wb)
        self.write_doc(wb, make_doc(status="- beta 작업"))
        before = self.snapshot(self.work)
        p, d = self.push(sid=S1, cwd=wb)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(d["bound"], "beta")
        self.assertEqual(self.snapshot(self.work), before)

        p, d = self.push("beta", sid=S1, cwd=wb)
        self.assertStatus(p, d, "ok")
        self.assertEqual((d["key"], d["checkpoint"]), ("beta", "C0001"))
        self.assertEqual(self.checkpoint_ids("alpha"), [])

    def test_target_is_named_only_when_binding_and_folder_agree(self):
        wa = self.make_project("alpha")
        self.register("alpha", wa)
        self.register("beta", self.make_project("beta"))
        self.make_transcript(S1, wa)  # S1은 alpha 폴더에서 띄운 세션
        d = self.current(S1)
        self.assertEqual((d["bound"], d["folder"], d["push_target"]), (None, "alpha", None))  # 아직 묶이지 않았다
        self.pull("alpha", sid=S1)
        d = self.current(S1)
        self.assertEqual((d["bound"], d["folder"], d["push_target"]), ("alpha", "alpha", "alpha"))
        self.pull("beta", sid=S1)  # 다른 프로젝트를 읽었다 — 실행 폴더와 어긋난다
        d = self.current(S1)
        self.assertEqual((d["bound"], d["folder"], d["push_target"]), ("beta", "alpha", None))
        self.assertIn("alpha", d["push_target_note"])
        self.assertIn("beta", d["push_target_note"])

    def test_folder_outside_every_workdir_backs_a_target_only_after_a_save_from_there(self):
        """상위 폴더에서 띄운 세션. 그 폴더에서 저장한 적이 있는 프로젝트가 하나뿐이면 폴더도 그 프로젝트를 가리킨다."""
        wa = self.make_project("alpha")
        self.register("alpha", wa)
        self.make_transcript(S2, self.work)
        self.pull("alpha", sid=S2)
        d = self.current(S2)
        self.assertEqual((d["bound"], d["folder"], d["push_target"]), ("alpha", None, None))
        self.write_doc(wa, make_doc(status="- 첫 저장"))
        self.assertStatus(*self.push("alpha", sid=S2), "ok")  # 키를 밝힌 저장
        d = self.current(S2)
        self.assertEqual((d["bound"], d["folder"], d["push_target"]), ("alpha", "alpha", "alpha"))

    def test_most_recent_pull_rebinds(self):
        self.register("alpha", self.make_project("alpha"))
        self.register("beta", self.make_project("beta"))
        self.pull("alpha", sid=S1)
        self.pull("beta", sid=S1)
        self.assertEqual(self.current(S1)["bound"], "beta")

    def test_pull_chosen_by_folder_also_binds(self):
        """/handon 다음 /handoff를 둘 다 키 없이 쓰면, pull이 고른 프로젝트가 대상이다."""
        wa, wb = make_h2a(self)
        self.assertEqual(self.pull(sid=S1, cwd=wb)["key"], "beta")
        d = self.current(S1, cwd=wb)
        self.assertEqual((d["bound"], d["push_target"]), ("beta", "beta"))

    def test_current_shows_the_binding_without_changing_it(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.assertIsNone(self.current(S1)["bound"])
        self.pull("alpha", sid=S1)
        self.assertEqual(self.current(S1)["bound"], "alpha")
        self.assertIsNone(self.current(S2)["bound"])
        # current는 기준을 바꾸지 않는다 — 그 사이 다른 세션의 push는 여전히 충돌로 잡힌다
        self.write_doc(w, make_doc(status="- S2"))
        self.assertStatus(*self.push("alpha", sid=S2), "ok")
        self.current(S1)
        self.write_doc(w, make_doc(status="- S1"))
        self.assertStatus(*self.push("alpha", sid=S1), "conflict")

    def test_binding_belongs_to_one_session(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.pull("alpha", sid=S1)
        self.write_doc(w, make_doc(status="- 바뀜"))
        p, d = self.push(sid=S2, cwd=self.work)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(self.checkpoint_ids("alpha"), [])

    def test_failed_rebind_is_reported_and_never_redirects_a_write(self):
        """alpha에 묶인 세션이 beta를 읽었는데 바인딩을 쓰지 못했다. 키 없는 저장이 alpha로 가면 안 된다."""
        wa = self.make_project("alpha")
        wb = self.make_project("beta")
        self.register("alpha", wa)
        self.register("beta", wb)
        self.make_transcript(S1, wa)
        self.pull("alpha", sid=S1)
        pulls = os.path.join(self.reg_dir, ".pulls")
        os.chmod(pulls, 0o500)
        d = self.pull("beta", sid=S1)  # 문서는 읽힌다
        self.assertFalse(d["bound"])
        self.assertTrue(any("묶지 못했다" in n for n in d["notices"]), d["notices"])

        cur = self.current(S1)
        self.assertEqual(cur["bound"], "alpha")  # 낡은 바인딩이 남아 있다
        self.assertFalse(cur["binding_writable"])
        self.assertIsNone(cur["push_target"])    # 그래서 대상을 정해 주지 않는다
        self.write_doc(wb, make_doc(status="- beta 작업"))
        before = self.snapshot(self.work)
        self.assertStatus(*self.push(sid=S1, cwd=wa), "needs_target")
        self.assertEqual(self.snapshot(self.work), before)

        p, d = self.push("beta", sid=S1, cwd=wa)  # 키를 밝히면 그 프로젝트에만 쓴다
        self.assertStatus(p, d, "ok")             # 사본은 저장됐다 — 다시 저장하라고 하지 않는다
        self.assertEqual(d["checkpoint"], "C0001")
        self.assertFalse(d["bound"])
        self.assertTrue(any("묶지 못했다" in n for n in d["notices"]), d["notices"])
        self.assertEqual(self.checkpoint_ids("alpha"), [])
        os.chmod(pulls, 0o700)


class PushByNameTest(BatonCase):
    """G2: 이름으로 push할 때도 엉뚱한 프로젝트에 쓰지 않는다."""

    def setUp(self):
        super().setUp()
        self.wa = self.make_project("alpha")
        self.wb = self.make_project("beta")
        self.wg = self.make_project("gamma")
        self.write_legacy_registry({
            "alpha": {"workdir": self.wa, "handoff_doc": DOC_REL, "aliases": ["beta", "app", "알파"]},
            "beta": {"workdir": self.wb, "handoff_doc": DOC_REL},
            "gamma": {"workdir": self.wg, "handoff_doc": DOC_REL, "aliases": ["app"]},
        })
        for w in (self.wa, self.wb, self.wg):
            self.write_doc(w, make_doc(status="- 바뀜"))

    def test_exact_key_beats_alias(self):
        p, d = self.push("beta", sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["key"], "beta")
        self.assertEqual(self.checkpoint_ids("alpha"), [])

    def test_shared_alias_writes_nowhere(self):
        p, d = self.push("app", sid=S1)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(sorted(d["candidates"]), ["alpha", "gamma"])
        for w in (self.wa, self.wg):
            self.assertFalse(os.path.exists(os.path.join(w, ".baton")))

    def test_unique_alias_and_extra_words(self):
        p, d = self.push("알파", sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["key"], "alpha")
        p, d = self.push("gamma 지금", sid=S2)
        self.assertError(p, d, "unknown_project")
        self.assertEqual(self.checkpoint_ids("gamma"), [])


class HookTest(BatonCase):
    def setUp(self):
        super().setUp()
        self.w = self.make_project("alpha")
        self.write_legacy_registry({"alpha": {"workdir": self.w, "handoff_doc": DOC_REL}})

    def hook(self, prompt, session=S1):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": self.w, "prompt": prompt}
        p = self.run_h("hook", stdin=json.dumps(payload, ensure_ascii=False), cwd=self.w)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not p.stdout.strip():
            return None
        return json.loads(p.stdout)["hookSpecificOutput"].get("sessionTitle")

    def test_titles_baton_and_bare_command(self):
        for prompt in ("/baton:handon alpha", "/handon alpha", "/baton:handon alpha 테스트부터 봐줘",
                       "<command-name>/baton:handon</command-name>\n<command-args>alpha</command-args>"):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.hook(prompt), "alpha")

    def test_ignores_other_namespaces_and_plain_text(self):
        for prompt in ("/other:handon alpha",
                       "<command-name>/other:handon</command-name>\n<command-args>alpha</command-args>",
                       "handon alpha 해줘"):
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.hook(prompt))

    def test_purges_expired_pending_titles(self):
        """오래된 예약 하나가 모든 세션의 빠른 경로를 막지 않게 한다."""
        pending = os.path.join(self.reg_dir, ".pending-titles")
        os.makedirs(pending, exist_ok=True)
        stale, fresh = os.path.join(pending, sid(77)), os.path.join(pending, sid(78))
        for path in (stale, fresh):
            with open(path, "w", encoding="utf-8") as f:
                f.write("alpha\n")
        old = time.time() - 8 * 24 * 3600
        os.utime(stale, (old, old))
        self.assertIsNone(self.hook("hello"))
        self.assertFalse(os.path.exists(stale), "만료된 예약이 남았다")
        self.assertTrue(os.path.exists(fresh), "만료되지 않은 다른 세션의 예약을 지웠다")
