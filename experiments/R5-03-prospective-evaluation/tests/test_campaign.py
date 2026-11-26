"""Phase 2 driver: evaluation-opening guard, fail-closed preconditions, frozen schedule, blame-rule reruns, pause,
interruption, terminal failures, one-shot analysis and a monitor without outcomes. No runtime is started:
host_conformance.run_launch is replaced by a fake that writes synthetic traces; prompts are fake strings."""
from __future__ import annotations

import argparse, copy, gzip, hashlib, io, json, os, sys, tempfile, unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import HERE, MS, g1_templates, launch_trace, load   # noqa: E402

C = load("campaign", HERE / "campaign.py")
AN = load("analysis", HERE / "analysis.py")
SENTINEL = "SECRET-EVAL-PROMPT-TEXT"
PROMPTS = {p: f"{SENTINEL}-{p}" for p in ("code-eval-1", "nl-eval-1", "math-eval-1", "json-eval-1")}
TRACE_EV = {"B": dict(waits=(500, 100, 100)), "C0_I": dict(waits=(500, 100, 100)),
            "C_I": dict(waits=(200, 100, 100), deferred_complete=(2000, 5000))}


class Fake:
    """Stands in for host_conformance.run_launch. faults: {(key, attempt): fault}; fault in error, crash, kill, kill_after_trace,
    violation, digest, abort_marker, wrong_arm."""

    def __init__(self, effect=0.05, faults=None, pause_at=None, out=None):
        self.calls, self.effect, self.faults, self.pause_at, self.out = [], effect, dict(faults or {}), pause_at, out
        self.tmpl = g1_templates()
        self.attempts = {}

    def __call__(self, dslot, runtime, model, store, out, prompt):
        arm = {("A", False): "A", ("A", True): "B", ("C0", True): "C0_I", ("C", False): "C", ("C", True): "C_I"}[(dslot["arm"], dslot["traced"])]
        tag = dslot["step"].rsplit("-a", 1)[0].rsplit("-", 1)[1]
        camp = dslot["step"].split("-b")[0]
        key = str(dslot["seq"]) if tag == "c" else (f"warmup:{camp}" if tag == "w" else f"warmup:{camp}:before-{tag[1:]}")
        att = int(dslot["step"].rsplit("-a", 1)[1])
        self.calls.append((key, att, arm))
        fault = self.faults.get((key, att))
        if self.pause_at and key == self.pause_at and att == 1 and self.out:
            (self.out / C.PAUSE).write_text("", encoding="utf-8")
        if fault == "kill":
            raise KeyboardInterrupt
        if fault == "kill_after_timing":
            (out / "launches").mkdir(parents=True, exist_ok=True)
            p = Path(C.stderr_path(out, dslot)); p.write_text("slot print_timing: eval time = 1 ms\n", encoding="utf-8")
            raise KeyboardInterrupt
        (out / "launches").mkdir(parents=True, exist_ok=True)
        se = out / "launches" / f"{dslot['seq']:03d}-{dslot['step']}.stderr.log"
        se.write_text("argine: abort: GGML_ASSERT failed\n" if fault == "abort_marker" else "ok\n", encoding="utf-8")
        rec = copy.deepcopy(self.tmpl[arm]); rec.update(dslot)
        if dslot["traced"]:
            tr = out / "traces" / f"{dslot['seq']:03d}-{dslot['step']}.trace"; tr.parent.mkdir(parents=True, exist_ok=True)
            tr.write_text(launch_trace(C.ARM_WAIT[arm], **TRACE_EV[arm]), encoding="utf-8")
            rec["trace"] = load("host_conformance", HERE.parents[0] / "R5-01-ets-implementation" / "host_conformance.py").reduce_trace(tr)
            if fault == "kill_after_trace":
                raise KeyboardInterrupt
        else:
            rec.pop("trace", None)
        base = 280.0 if dslot["budget"] == "1.5" else 150.0
        v = base * (1 - self.effect) if arm == "C" and dslot["budget"] == "1.5" else base
        rec["server_timings"] = dict(rec["server_timings"], predicted_ms=v * 512, predicted_n=512)
        rec["r5_counters"] = dict(rec["r5_counters"], budget={"1.5": 1610612736, "4": 4294967296}[dslot["budget"]])
        rec["token_digest"] = hashlib.sha256(prompt.encode()).hexdigest()
        rec["stderr_log"], rec["error"] = str(se), None
        if fault == "error":                                                    # a crash before any output: no timings, no tokens
            rec["error"] = "RuntimeError: runtime exited early: 1"; rec["n_tokens"] = None; rec["server_timings"] = None
        if fault == "violation":
            rec["r5_counters"]["violations"] = 1
        if fault == "digest":
            rec["token_digest"] = "0" * 64
        if fault == "wrong_arm":
            rec["r5_counters"]["expert_wait"] = "layer_touch"
        return rec


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.out = self.root / "p2"; self.out.mkdir()
        self.rt = self.root / "llama-server.exe"; self.rt.write_bytes(b"runtime")
        self.rid = {"readiness_sha256": "r", "campaign_lf_sha256": "c", "analysis_lf_sha256": "a", "mechanism_lf_sha256": "m", "protocol_lf_sha256": "p"}
        self.frozen = {"binaries_sha256": {"llama-server.exe": hashlib.sha256(b"runtime").hexdigest()},
                       "model": {"sha256": "m"}, "store": {"sha256": "s"}}
        self.patches = [mock.patch.object(C, "marker_path", return_value=self.root / "r5" / "PHASE2_CAMPAIGN.json"),
                        mock.patch.object(C, "opening_path", return_value=self.root / "r5" / "EVALUATION_OPENING.json"),
                        mock.patch.object(C, "frozen", return_value=self.frozen),
                        mock.patch.object(C, "readiness_identity", return_value=self.rid),
                        mock.patch.object(C, "windows_events_since", return_value=["System: Kernel-Power 41"])]   # host corroboration
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def ns(self, **kw):
        return argparse.Namespace(model="M.gguf", store="M.psxs", out=str(self.out), rerun_infrastructure=kw.get("cause"), evidence=kw.get("evidence"),
                                  json=False)

    def run_campaign(self, fake, args=None, ac=True, rid=None):
        pre = {"ident": dict({"runtime_sha256": "x", "opening_sha256": "o"}, **(rid or self.rid)), "runtime": self.rt, "prompts": PROMPTS}
        H = C.driver()
        fake.out = self.out
        with ExitStack() as es:
            es.enter_context(mock.patch.object(C, "preconditions", return_value=pre))
            es.enter_context(mock.patch.object(C, "readiness_identity", return_value=self.rid))
            es.enter_context(mock.patch.object(C, "check_model_store", return_value={"model_sha256": "m", "store_sha256": "s"}))
            es.enter_context(mock.patch.object(C, "check_runtime", return_value="x"))
            es.enter_context(mock.patch.object(C, "host_record", return_value={"ac_power": ac}))
            es.enter_context(mock.patch.object(C, "power_on_ac", return_value=ac))
            es.enter_context(mock.patch.object(C.time, "sleep"))
            es.enter_context(mock.patch.object(H, "run_launch", side_effect=fake))
            so = es.enter_context(redirect_stdout(io.StringIO()))
            try:
                rc = C.cmd_run(args or self.ns())
            except KeyboardInterrupt:
                rc = "killed"
        return rc, so.getvalue()

    def result(self):
        return json.loads((self.out / C.RESULT).read_text(encoding="utf-8"))

    def evidence(self):
        e = self.root / "eventlog.txt"; e.write_text("Event 41 Kernel-Power", encoding="utf-8"); return str(e)


class Schedule(unittest.TestCase):
    def test_plan_is_the_frozen_schedule(self):
        p = C.plan()
        sc = json.loads((C.P0_DIR / "SCHEDULE.json").read_text(encoding="utf-8"))
        self.assertEqual(len(p), 196)
        self.assertEqual({k: sum(1 for s in p if s["campaign"] == k) for k in sc["campaign_order"]},
                         {"mechanism_1.5GiB": 36, "timing_1.5GiB": 80, "timing_4GiB": 80})
        self.assertEqual(sum(1 for s in p if s["traced"]), 36)
        for camp in sc["campaign_order"]:
            for b in sc["campaigns"][camp]["blocks"]:
                got = [s for s in p if s["campaign"] == camp and s["block"] == b["block"]]
                self.assertEqual([s["arm"] for s in got], b["order"]); self.assertTrue(all(s["prompt"] == b["prompt"] for s in got))
        self.assertTrue(all(s["traced"] == (s["arm"] in ("B", "C0_I", "C_I")) for s in p))
        self.assertTrue(all(s["budget"] == ("4" if s["campaign"] == "timing_4GiB" else "1.5") for s in p))
        self.assertEqual([C.campaign_warmup(c)["arm"] for c in sc["campaign_order"]], ["C0_I", "A", "A"])

    def test_arm_environments(self):
        self.assertEqual(C.ARM_WAIT, {"A": "layer", "B": "layer", "C": "kind", "C0_I": "layer_touch", "C_I": "kind"})
        H = C.driver()
        for arm, d in C.ARM_DRIVER.items():
            self.assertEqual(H.ARMS[d], C.ARM_WAIT[arm])

    def test_no_os_kill_and_no_prompt_output(self):
        src = (HERE / "campaign.py").read_text(encoding="utf-8")
        self.assertNotIn("os.kill(", src)
        self.assertNotIn("print(prompt", src); self.assertNotIn('"prompt_text"', src)


class Opening(Base):
    def test_authorize_requires_exact_statement_and_readiness_and_is_one_shot(self):
        a = lambda st, rd: argparse.Namespace(statement=st, readiness=rd)
        with ExitStack() as es:
            es.enter_context(mock.patch.object(C, "readiness_identity", return_value=self.rid))
            ev = es.enter_context(mock.patch.object(C, "eval_prompts"))
            es.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(C.cmd_authorize(a("yes", "r")), C.EXIT_PRECONDITION)
            self.assertEqual(C.cmd_authorize(a(C.OPENING_STATEMENT, "other")), C.EXIT_PRECONDITION)
            self.assertFalse(C.opening_path().exists())
            self.assertEqual(C.cmd_authorize(a(C.OPENING_STATEMENT, "r")), 0)
            self.assertEqual(C.cmd_authorize(a(C.OPENING_STATEMENT, "r")), C.EXIT_PRECONDITION)
            ev.assert_not_called()
        rec = C.opening_record(self.rid)
        self.assertEqual(rec["readiness_sha256"], "r")
        with self.assertRaises(SystemExit):
            C.opening_record(dict(self.rid, campaign_lf_sha256="changed"))     # no change of the driver after opening

    def test_run_without_opening_reads_no_prompt_and_launches_nothing(self):
        fake = Fake()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(C, "readiness_identity", return_value=self.rid))
            es.enter_context(mock.patch.object(C, "check_runtime", return_value="x"))
            ev = es.enter_context(mock.patch.object(C, "eval_prompts"))
            es.enter_context(mock.patch.object(C.driver(), "run_launch", side_effect=fake))
            es.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(C.cmd_run(self.ns()), C.EXIT_PRECONDITION)
            ev.assert_not_called()
        self.assertEqual(fake.calls, [])
        self.assertFalse(C.marker_path().exists())

    def test_eval_loader_checks_hashes_and_needs_an_opening(self):
        corpus = self.root / "corpus"; corpus.mkdir()
        ev_meta = []
        for f, cat, pid in (("coding", "coding", "code-eval-1"), ("natural-language", "natural-language", "nl-eval-1"),
                            ("reasoning-math", "reasoning-math", "math-eval-1"), ("structured-json", "structured-json", "json-eval-1")):
            obj = {"id": pid, "prompt": PROMPTS[pid], "segment": "x"}
            (corpus / f"{f}.json").write_text(json.dumps({"splits": {"dev": [], "eval": [obj]}}), encoding="utf-8")
            ev_meta.append({"id": pid, "category": cat, "prompt_object_sha256": hashlib.sha256(C.canon(obj)).hexdigest()})
        w = {"evaluation": {"prompts": ev_meta}, "development": {"prompts": [{"id": "code-dev-1"}]}}
        got = C.eval_prompts({"statement": C.OPENING_STATEMENT}, corpus, w)
        self.assertEqual(got, PROMPTS)
        with self.assertRaises(SystemExit):
            C.eval_prompts({}, corpus, w)
        w2 = copy.deepcopy(w); w2["evaluation"]["prompts"][0]["prompt_object_sha256"] = "0" * 64
        with self.assertRaises(SystemExit):
            C.eval_prompts({"statement": C.OPENING_STATEMENT}, corpus, w2)


class Campaign(Base):
    def test_full_campaign_one_shot_analysis_and_no_prompt_text_written(self):
        calls = []
        real = AN.classify
        def watch(recs, mech, *a, **k):
            calls.append(sum(1 for r in C.records(self.out) if r.get("type") == "launch")); return real(recs, mech, *a, **k)
        fake = Fake(effect=0.05)
        with mock.patch.object(AN, "classify", side_effect=watch):
            rc, so = self.run_campaign(fake)
        self.assertEqual(rc, 0, so[-2000:])
        self.assertEqual(calls, [199], "the analysis runs once, after 196 counted launches and 3 warmups")
        self.assertEqual([c for c in fake.calls if c[0].startswith("warmup")], [("warmup:mechanism_1.5GiB", 1, "C0_I"),
                                                                              ("warmup:timing_1.5GiB", 1, "A"), ("warmup:timing_4GiB", 1, "A")])
        res = self.result()
        self.assertEqual(res["classification"], "R5_POSITIVE_RUNTIME_IMPROVEMENT", res.get("gates"))
        self.assertEqual(len(list((self.out / "traces").glob("*.trace.gz"))), 36)
        self.assertFalse(list((self.out / "traces").glob("*.trace")))
        for f in self.out.rglob("*"):
            if f.is_file() and f.suffix != ".gz":
                self.assertNotIn(SENTINEL, f.read_text(encoding="utf-8", errors="replace"), f)
        self.assertNotIn(SENTINEL, so)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.analyze(self.out), C.EXIT_PRECONDITION)              # one-shot
            with mock.patch.object(C, "readiness_identity", return_value=self.rid):
                self.assertEqual(C.cmd_verify(self.ns()), 0)
        self.assertFalse((self.out / C.RUN_STATE).exists())

    def test_status_reports_progress_only(self):
        fake = Fake(faults={("40", 1): "error"})
        self.run_campaign(fake)
        with redirect_stdout(io.StringIO()) as so:
            C.cmd_status(self.ns())
        s = so.getvalue()
        self.assertIn("counted valid 39/196", s); self.assertIn("mechanism 36/36", s); self.assertIn("failed: ['40']", s)
        for word in ("ms", "classification", "R5_", "gain", "H ="):
            self.assertNotIn(word, s.replace("timing", ""))

    def test_running_slot_is_not_reported_as_failed(self):
        C.append(self.out, {"type": "attempt_start", "key": "5", "attempt": 1, "unix": 0})
        (self.out / C.RUN_STATE).write_text(json.dumps({"pid": os.getpid(), "phase": "post-processing", "key": "5", "updated_unix": 0}), encoding="utf-8")
        d = C.status_dict(self.out)
        self.assertEqual((d["running"], d["running_phase"]), ("5", "post-processing"))
        self.assertEqual(d["failed_slots"], []); self.assertEqual(d["interrupted_attempts_counting_as_failures"], [])
        (self.out / C.RUN_STATE).unlink()                                         # the run died: now it is an interruption
        d = C.status_dict(self.out)
        self.assertEqual(d["running"], None); self.assertEqual(d["interrupted_attempts_counting_as_failures"], ["5"])

    def test_pause_and_resume_with_a_warmup(self):
        fake = Fake(pause_at="50")
        rc, so = self.run_campaign(fake)
        self.assertEqual(rc, 0); self.assertIn("PAUSED", so); self.assertFalse((self.out / C.RESULT).exists())
        self.assertEqual(C.status_dict(self.out)["counted_valid"], 50)          # the running slot finishes, then the run pauses
        fake2 = Fake()
        rc, so = self.run_campaign(fake2)
        self.assertEqual(rc, 0, so[-1500:])
        nxt = C.plan()[50]
        self.assertEqual(fake2.calls[0], ("warmup:timing_1.5GiB:before-51", 1, nxt["arm"]))   # resume warmup with the next slot's arm
        self.assertEqual(fake2.calls[1][0], "51")
        self.assertIn(self.result()["classification"], AN.LABELS)

    def test_interrupted_before_any_outcome_needs_a_corroborated_cause(self):
        a = next(x for x in C.plan() if x["arm"] == "A")
        rc, _ = self.run_campaign(Fake(faults={(str(a["seq"]), 1): "kill"}))
        self.assertEqual(rc, "killed")
        rc, so = self.run_campaign(Fake())
        self.assertEqual(rc, C.EXIT_PRECONDITION); self.assertIn("rerun only with", so)
        with mock.patch.object(C, "windows_events_since", return_value=[]):
            rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, C.EXIT_PRECONDITION); self.assertIn("not corroborated", so)
        fake = Fake()
        rc, so = self.run_campaign(fake, self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, 0, so[-1500:])
        self.assertEqual(fake.calls[0][0], f"warmup:{a['campaign']}:before-{a['seq']}"); self.assertEqual(fake.calls[1][:2], (str(a["seq"]), 2))
        self.assertEqual(len(self.result()["reruns"]["reruns_authorized"]), 1)

    def test_interrupted_after_the_trace_is_never_rerun(self):
        rc, _ = self.run_campaign(Fake(faults={("3", 1): "kill_after_trace"}))     # seq 3 is C_I; the trace was handed over
        self.assertEqual(rc, "killed")
        for cause in ("power", "operator-interruption", "windows-update-or-defender"):
            rc, so = self.run_campaign(Fake(), self.ns(cause=cause, evidence=self.evidence()))
            self.assertEqual(rc, C.EXIT_PRECONDITION); self.assertIn("outcome information", so)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure=None, evidence=None)), 0)
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")

    def test_interruption_during_post_processing_completes_the_saved_result(self):
        real = C.check_model_store
        n = []
        def flaky(a):
            n.append(1)
            if len(n) == 4:                                                    # during the post-processing of the third launch
                raise KeyboardInterrupt
            return {"model_sha256": "m", "store_sha256": "s"}
        H = C.driver()
        fake = Fake(pause_at="4")
        with mock.patch.object(C, "check_model_store", side_effect=flaky):
            with ExitStack() as es:
                pre = {"ident": dict({"runtime_sha256": "x", "opening_sha256": "o"}, **self.rid), "runtime": self.rt, "prompts": PROMPTS}
                es.enter_context(mock.patch.object(C, "preconditions", return_value=pre))
                es.enter_context(mock.patch.object(C, "check_runtime", return_value="x"))
                es.enter_context(mock.patch.object(C, "host_record", return_value={"ac_power": True}))
                es.enter_context(mock.patch.object(C, "power_on_ac", return_value=True))
                es.enter_context(mock.patch.object(C.time, "sleep"))
                es.enter_context(mock.patch.object(H, "run_launch", side_effect=fake))
                es.enter_context(redirect_stdout(io.StringIO()))
                with self.assertRaises(KeyboardInterrupt):
                    C.cmd_run(self.ns())
        d = C.status_dict(self.out)
        self.assertEqual(d["failed_slots"], []); self.assertEqual(d["interrupted_attempts_counting_as_failures"], [])
        self.assertEqual(len(d["saved_results_pending_post_processing"]), 1)
        fake2 = Fake(pause_at="6")
        rc, so = self.run_campaign(fake2)                                          # no cause needed, nothing rerun
        self.assertEqual(rc, 0, so); self.assertIn("completing the post-processing", so)
        self.assertNotIn(d["saved_results_pending_post_processing"][0], [c[0] for c in fake2.calls])
        recs = C.records(self.out)
        self.assertTrue(any(r.get("type") == "launch" and r.get("post_processing_resumed") for r in recs))
        self.assertFalse(any(r.get("type") == "rerun_authorized" for r in recs))

    def test_correctness_failure_of_C_is_terminal_and_never_rerun(self):
        c = next(s for s in C.plan() if s["arm"] == "C" and s["campaign"] == "timing_1.5GiB")
        rc, so = self.run_campaign(Fake(faults={(str(c["seq"]), 1): "violation"}))
        self.assertEqual(rc, C.EXIT_TERMINAL, so)
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, C.EXIT_TERMINAL); self.assertIn("terminal", so)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.analyze(self.out), 0)
        self.assertEqual(self.result()["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")
        self.assertFalse(self.result()["endpoints_computed"])

    def test_abort_marker_and_c0_digest_are_correctness_failures(self):
        rc, so = self.run_campaign(Fake(faults={("1", 1): "abort_marker"}))   # seq 1 is C0_I
        self.assertEqual(rc, C.EXIT_TERMINAL, so)

    def test_digest_of_C0_I_checked_when_the_B_reference_arrives(self):
        # warmup C0_I and counted C0_I agree with each other but not with B, which comes next: caught when B arrives
        rc, so = self.run_campaign(Fake(faults={("warmup:mechanism_1.5GiB", 1): "digest", ("1", 1): "digest"}))
        self.assertEqual(rc, C.EXIT_TERMINAL, so); self.assertIn("earlier C/C_I/C0_I launches", so)

    def test_digest_of_C0_I_differing_from_its_warmup_is_caught_at_once(self):
        rc, so = self.run_campaign(Fake(faults={("1", 1): "digest"}))
        self.assertEqual(rc, C.EXIT_TERMINAL, so); self.assertIn("C0_I token digest differs", so)

    def test_A_or_B_integrity_failure_is_terminal_invalid(self):
        b = next(s for s in C.plan() if s["arm"] == "B" and s["block"] == 2)
        rc, so = self.run_campaign(Fake(faults={(str(b["seq"]), 1): "violation"}))
        self.assertEqual(rc, C.EXIT_TERMINAL, so)
        with redirect_stdout(io.StringIO()):
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_INVALID")

    def test_crash_during_trace_compression_does_not_destroy_the_campaign(self):
        real = C.os.replace
        hit = []
        def flaky(src, dst):
            if str(dst).endswith(".trace.gz") and not hit:
                hit.append(dst); raise KeyboardInterrupt                         # power loss between gzip and rename
            return real(src, dst)
        with mock.patch.object(C.os, "replace", side_effect=flaky):
            rc, _ = self.run_campaign(Fake())
        self.assertEqual(rc, "killed")
        self.assertTrue(list((self.out / "traces").glob("*.partial")))
        rc, so = self.run_campaign(Fake())                                          # the saved result is completed, nothing rerun
        self.assertEqual(rc, 0, so[-1500:]); self.assertIn("completing the post-processing", so)
        res = self.result()
        self.assertIn(res["classification"], AN.LABELS); self.assertTrue(res["endpoints_computed"])
        self.assertEqual(len(res["reruns"]["reruns_authorized"]), 0)

    def test_a_rerun_warmup_is_resolved_and_does_not_block_later_resumes(self):
        rc, _ = self.run_campaign(Fake(faults={("warmup:mechanism_1.5GiB", 1): "error"}))
        self.assertEqual(rc, C.EXIT_LAUNCH_INVALID)
        rc, so = self.run_campaign(Fake(pause_at="5"), self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, 0, so); self.assertIn("PAUSED", so)
        rc, so = self.run_campaign(Fake(pause_at="9"))                          # plain resume: no cause needed
        self.assertEqual(rc, 0, so); self.assertIn("PAUSED", so)

    def test_running_slot_with_a_stale_heartbeat_key_is_still_running(self):
        C.append(self.out, {"type": "attempt_start", "key": "6", "attempt": 1, "unix": 0})
        (self.out / C.RUN_STATE).write_text(json.dumps({"pid": os.getpid(), "phase": "pre-launch checks", "key": "5", "updated_unix": 0}),
                                            encoding="utf-8")
        d = C.status_dict(self.out)
        self.assertEqual(d["running"], "6"); self.assertEqual(d["interrupted_attempts_counting_as_failures"], []); self.assertEqual(d["failed_slots"], [])

    def test_operator_interruption_refused_once_the_timings_were_printed(self):
        a = next(s for s in C.plan() if s["arm"] == "A")
        rc, _ = self.run_campaign(Fake(faults={(str(a["seq"]), 1): "kill_after_timing"}))
        self.assertEqual(rc, "killed")
        for cause in ("operator-interruption", "power", "disk-full", "windows-update-or-defender"):
            rc, so = self.run_campaign(Fake(), self.ns(cause=cause, evidence=self.evidence()))   # even with a host event
            self.assertEqual(rc, C.EXIT_PRECONDITION, cause); self.assertIn("outcome information", so)

    def test_operator_interruption_before_any_result_is_accepted_and_reported(self):
        a = next(s for s in C.plan() if s["arm"] == "A")
        rc, _ = self.run_campaign(Fake(faults={(str(a["seq"]), 1): "kill"}))
        rc, so = self.run_campaign(Fake(pause_at=str(a["seq"] + 1)), self.ns(cause="operator-interruption", evidence=self.evidence()))
        self.assertEqual(rc, 0, so)
        recs = C.records(self.out)
        ra = [r for r in recs if r.get("type") == "rerun_authorized"]
        self.assertEqual(len(ra), 1); self.assertTrue(list((self.out / "evidence").iterdir()))

    def test_wrong_arm_echo_of_C_is_blocked(self):
        c = next(s for s in C.plan() if s["arm"] == "C")
        rc, _ = self.run_campaign(Fake(faults={(str(c["seq"]), 1): "wrong_arm"}))
        self.assertEqual(rc, C.EXIT_TERMINAL)
        with redirect_stdout(io.StringIO()):
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")

    def test_second_failure_of_a_C_slot_is_blocked_unless_documented(self):
        c = next(s for s in C.plan() if s["arm"] == "C")
        k = str(c["seq"])
        self.run_campaign(Fake(faults={(k, 1): "error"}))
        rc, _ = self.run_campaign(Fake(faults={(k, 2): "error"}), self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, C.EXIT_ABORTED)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.analyze(self.out), C.EXIT_PRECONDITION)          # the operator cannot pick INVALID by running analyze
            with mock.patch.object(C, "windows_events_since", return_value=[]):
                self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure="windows-update-or-defender",
                                                                   evidence=self.evidence())), C.EXIT_PRECONDITION)
            self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure=None, evidence=None)), 0)
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")

    def test_finalize_an_interruption_without_a_stop(self):
        a = next(s for s in C.plan() if s["arm"] == "A")
        rc, _ = self.run_campaign(Fake(faults={(str(a["seq"]), 1): "kill"}))
        self.assertFalse((self.out / C.STOPPED).exists())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure=None, evidence=None)), 0)
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_INVALID")

    def test_every_cause_needs_host_corroboration(self):
        c = next(x for x in C.plan() if x["arm"] == "C")
        rc, _ = self.run_campaign(Fake(faults={(str(c["seq"]), 1): "kill"}))      # no outcome produced
        with mock.patch.object(C, "windows_events_since", return_value=[]):
            for cause in ("power", "windows-update-or-defender", "disk-full"):
                with self.subTest(cause=cause):
                    rc, so = self.run_campaign(Fake(), self.ns(cause=cause, evidence=self.evidence()))
                    self.assertEqual(rc, C.EXIT_PRECONDITION, so); self.assertIn("not corroborated", so)
        with mock.patch.object(C, "windows_events_since", return_value=["System: WindowsUpdateClient"]):
            rc, so = self.run_campaign(Fake(pause_at=str(c["seq"] + 1)), self.ns(cause="windows-update-or-defender", evidence=self.evidence()))
        self.assertEqual(rc, 0, so)

    def test_analysis_input_failure_can_be_finalized_as_invalid(self):
        with mock.patch.object(C, "analyze", return_value=0):
            self.run_campaign(Fake())
        gz = sorted((self.out / "traces").glob("*.trace.gz"))[0]; gz.write_bytes(gzip.compress(b"x"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.analyze(self.out), C.EXIT_TELEMETRY)
            self.assertFalse((self.out / C.RESULT).exists())
            self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure=None, evidence=None)), 0)
            self.assertEqual(C.analyze(self.out), 0)
        self.assertEqual(self.result()["classification"], "R5_INVALID")

    def test_heartbeat_failure_never_ends_a_launch(self):
        with mock.patch.object(C, "write_atomic", side_effect=PermissionError("sharing violation")):
            rc, so = self.run_campaign(Fake(pause_at="3"))
        self.assertEqual(rc, 0, so); self.assertEqual(C.status_dict(self.out)["counted_valid"], 3)

    def test_torn_last_log_line_is_repaired(self):
        self.run_campaign(Fake(pause_at="3"))
        with open(self.out / C.LOG, "ab") as f:
            f.write(b'{"type": "launch", "key": "4", "trunc')
        self.assertTrue(C.records(self.out))                                    # readable despite the torn line
        rc, so = self.run_campaign(Fake(pause_at="6"))
        self.assertEqual(rc, 0, so); self.assertIn("torn last log line", so)
        self.assertTrue(list(self.out.glob("PHASE2_LOG.torn.*.txt")))
        self.assertTrue(any(r.get("type") == "log_repaired" for r in C.records(self.out)))

    def test_concurrent_run_refused(self):
        (self.out / C.RUN_STATE).write_text(json.dumps({"pid": os.getpid(), "phase": "launch", "key": "3", "updated_unix": C.time.time()}),
                                            encoding="utf-8")
        fake = Fake()
        rc, so = self.run_campaign(fake)
        self.assertEqual(rc, C.EXIT_PRECONDITION); self.assertIn("already live", so); self.assertEqual(fake.calls, [])
        self.assertTrue((self.out / C.RUN_STATE).exists())                       # the live run's lock is not removed

    def test_analysis_refusals_have_no_side_effect(self):
        self.run_campaign(Fake(pause_at="4"))
        before = sorted(p.name for p in self.out.iterdir())
        with redirect_stdout(io.StringIO()), mock.patch.object(C, "readiness_identity", side_effect=SystemExit("readiness")):
            self.assertEqual(C.analyze(self.out), C.EXIT_PRECONDITION)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), before)

    def test_wrong_arm_echo_is_terminal_invalid(self):
        a = next(s for s in C.plan() if s["arm"] == "A")
        rc, _ = self.run_campaign(Fake(faults={(str(a["seq"]), 1): "wrong_arm"}))
        self.assertEqual(rc, C.EXIT_TERMINAL)
        with redirect_stdout(io.StringIO()):
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_INVALID")

    def test_ordinary_failure_one_rerun_then_abort(self):
        a = next(s for s in C.plan() if s["arm"] == "A")
        k = str(a["seq"])
        rc, _ = self.run_campaign(Fake(faults={(k, 1): "error"}))
        self.assertEqual(rc, C.EXIT_LAUNCH_INVALID)
        rc, so = self.run_campaign(Fake(faults={(k, 2): "error"}), self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, C.EXIT_ABORTED, so)
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=self.evidence()))
        self.assertEqual(rc, C.EXIT_ABORTED)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure=None, evidence=None)), 0)
            C.analyze(self.out)
        self.assertEqual(self.result()["classification"], "R5_INVALID")

    def test_finalize_attributes_an_unexplained_C_failure_to_the_intervention(self):
        c = next(s for s in C.plan() if s["arm"] == "C")
        rc, _ = self.run_campaign(Fake(faults={(str(c["seq"]), 1): "error"}))
        self.assertEqual(rc, C.EXIT_LAUNCH_INVALID)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(C.analyze(self.out), C.EXIT_PRECONDITION)           # not terminal yet: no analysis
            self.assertEqual(C.cmd_finalize(argparse.Namespace(out=str(self.out), infrastructure=None, evidence=None)), 0)
            with mock.patch.object(C, "readiness_identity", return_value=self.rid):
                self.assertEqual(C.analyze(self.out), 0)
        self.assertEqual(self.result()["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")

    def test_incomplete_analysis_refused_without_side_effects(self):
        self.run_campaign(Fake(pause_at="10"))
        with redirect_stdout(io.StringIO()), mock.patch.object(C, "readiness_identity", return_value=self.rid):
            self.assertEqual(C.analyze(self.out), C.EXIT_PRECONDITION)
        self.assertFalse((self.out / C.STOPPED).exists()); self.assertFalse((self.out / C.RESULT).exists())

    def test_driver_change_after_start_blocks_the_analysis(self):
        with mock.patch.object(C, "analyze", return_value=0):
            self.run_campaign(Fake())
        with redirect_stdout(io.StringIO()), mock.patch.object(C, "readiness_identity", return_value=dict(self.rid, analysis_lf_sha256="x")):
            self.assertEqual(C.analyze(self.out), C.EXIT_PRECONDITION)
        self.assertFalse((self.out / C.RESULT).exists())

    def test_battery_or_unknown_power_stops_before_launching(self):
        for ac in (None, False):
            with self.subTest(ac=ac):
                fake = Fake()
                rc, _ = self.run_campaign(fake, ac=ac)
                self.assertEqual(rc, C.EXIT_PRECONDITION); self.assertEqual(fake.calls, [])
                (self.out / C.STOPPED).unlink()


class Validity(unittest.TestCase):
    def test_launch_problems_each_condition(self):
        tm = g1_templates()
        with mock.patch.object(C, "frozen", return_value={"binaries_sha256": {"llama-server.exe": "rt"}}):
            def rec(arm="A", **kw):
                r = copy.deepcopy(tm[arm]); r.update(arm=arm, budget="1.5", traced=arm in C.TRACED, counted=True, runtime_sha256="rt",
                                                    host_before={"ac_power": True}, host_after={"ac_power": True}, model_store_after={"ok": True})
                if arm in C.TRACED:
                    r["trace"] = {"present": True, "telemetry": {"valid": True}, "kept": "x.gz"}
                else:
                    r.pop("trace", None)
                r.update(kw); return r
            for arm in ("A", "B", "C", "C0_I", "C_I"):
                self.assertEqual(C.launch_problems(rec(arm)), [], arm)
            muts = {"error": dict(error="x"), "tokens": dict(n_tokens=511), "timing": dict(server_timings={}),
                    "shutdown": dict(shutdown={"mode": "forced"}), "ac": dict(host_after={"ac_power": None}),
                    "model": dict(model_store_after={"ok": False}), "runtime": dict(runtime_sha256="other")}
            for name, kw in muts.items():
                self.assertTrue(C.launch_problems(rec("A", **kw)), name)
            r = rec("C_I"); r["trace"]["telemetry"] = {"valid": False, "reason": "x"}
            self.assertTrue(C.launch_problems(r))
            r = rec("A"); r["trace"] = {"present": True}
            self.assertTrue(C.launch_problems(r))                               # an untraced arm must not be traced


if __name__ == "__main__":
    unittest.main()
