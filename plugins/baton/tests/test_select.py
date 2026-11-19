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
    """결정 2: 키가 비면 이 세션의 바인딩으로, 없으면 쓰지 않고 묻는다."""

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

    def test_no_key_follows_pull_binding_not_folder_guess(self):
        wa, wb = make_h2a(self)
        alpha_before = self.snapshot(wa)
        self.pull("beta", sid=S1, cwd=wb)
        self.write_doc(wb, make_doc(status="- beta 작업"))
        p, d = self.push(sid=S1, cwd=wb)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["key"], "beta")
        self.assertEqual(d["checkpoint"], "C0001")
        self.assertEqual(self.checkpoint_ids("alpha"), [])
        self.assertEqual(self.snapshot(wa), alpha_before)

    def test_no_key_follows_previous_push(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.assertStatus(*self.push("alpha", sid=S1, cwd=w), "ok")
        self.write_doc(w, make_doc(status="- 두 번째"))
        p, d = self.push(sid=S1, cwd=w)
        self.assertStatus(p, d, "ok")
        self.assertEqual((d["key"], d["checkpoint"]), ("alpha", "C0002"))

    def test_most_recent_explicit_command_rebinds(self):
        self.register("alpha", self.make_project("alpha"))
        wb = self.make_project("beta")
        self.register("beta", wb)
        self.pull("alpha", sid=S1)
        self.pull("beta", sid=S1)
        self.write_doc(wb, make_doc(status="- beta"))
        p, d = self.push(sid=S1)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["key"], "beta")

    def test_pull_chosen_by_folder_also_binds(self):
        """/handon 다음 /handoff를 둘 다 키 없이 쓰면, pull이 고른 프로젝트에 쓴다."""
        wa, wb = make_h2a(self)
        self.assertEqual(self.pull(sid=S1, cwd=wb)["key"], "beta")
        self.write_doc(wb, make_doc(status="- beta 작업"))
        p, d = self.push(sid=S1, cwd=wb)
        self.assertStatus(p, d, "ok")
        self.assertEqual(d["key"], "beta")

    def test_binding_belongs_to_one_session(self):
        w = self.make_project("alpha")
        self.register("alpha", w)
        self.pull("alpha", sid=S1)
        self.write_doc(w, make_doc(status="- 바뀜"))
        p, d = self.push(sid=S2, cwd=self.work)
        self.assertStatus(p, d, "needs_target")
        self.assertEqual(self.checkpoint_ids("alpha"), [])


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
