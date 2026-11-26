"""Phase 1b acquisition driver: fail-closed preconditions, development prompts only, blame-rule reruns, one-shot G2.
No runtime is started: host_conformance.run_launch is replaced by a fake that writes synthetic B traces."""
from __future__ import annotations

import argparse, copy, gzip, importlib.util, io, json, sys, tempfile, unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tests"))
spec = importlib.util.spec_from_file_location("acquire", HERE / "acquire.py")
A = importlib.util.module_from_spec(spec); sys.modules["acquire"] = A; spec.loader.exec_module(A)  # type: ignore
from test_headroom import Trace, MS   # noqa: E402  (synthetic B traces)

SEAL_LOG = A.SEAL / "host" / "DEV_LOG.jsonl"


def g1_b_record():
    """The canonical G1 traced B launch (seq 41), as a template of a valid B record."""
    r = [json.loads(l) for l in SEAL_LOG.read_text(encoding="utf-8").splitlines() if l.strip()][40]
    assert r["traced"] and r["arm"] == "A"
    return r


def synthetic_trace(tail_ns: int, n_decode: int = 511) -> str:
    t = Trace(); t.graph(0, n_tokens=40)
    t.event(0, 0, 1000, (100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)])
    for g in range(1, n_decode + 1):
        t.graph(g)
        t.event(g, 0, g * 100 * MS, (100, 100 + 100 + tail_ns), 50 * MS, [(0, 10, 200, 1), (2, 10, 200 + tail_ns, 1)])
    return "\n".join(t.lines) + "\n"


class Fake:
    """Stands in for host_conformance.run_launch: writes a synthetic trace, calls the (patched) reduce_trace, returns a B record."""

    def __init__(self, tail_ns=5 * MS, decode_ms=290.0, bad=()):
        self.calls, self.tail_ns, self.decode_ms, self.bad = [], tail_ns, decode_ms, list(bad)

    def __call__(self, slot, runtime, model, store, out, prompt):
        self.calls.append(slot["seq"])
        tr = out / "traces" / f"{slot['seq']:03d}-{len(self.calls)}-{slot['step']}.trace"; tr.parent.mkdir(parents=True, exist_ok=True)
        tr.write_text(synthetic_trace(self.tail_ns), encoding="utf-8")
        rec = copy.deepcopy(g1_b_record()); rec.update(slot)
        rec["server_timings"] = dict(rec["server_timings"], predicted_ms=self.decode_ms * 511, predicted_n=512)
        rec["trace"] = A.driver().reduce_trace(tr)
        if self.bad and self.bad[0] == slot["seq"]:
            self.bad.pop(0)
            rec["error"] = "RuntimeError: runtime exited early"
        return rec


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.out = self.root / "p1b"; self.out.mkdir()
        self.args = self.ns()
        self.marker = mock.patch.object(A, "marker_path", return_value=self.root / "marker" / "PHASE1B_CAMPAIGN.json"); self.marker.start()
        self.rid = {"readiness_sha256": "r", "headroom_py_lf_sha256": "h", "acquire_py_lf_sha256": "a", "protocol_lf_sha256": "p"}

    def tearDown(self):
        self.marker.stop(); self.tmp.cleanup()

    def ns(self, out=None, **kw):
        return argparse.Namespace(model="M.gguf", store="M.psxs", out=str(out or self.out), rerun_infrastructure=kw.get("cause"), evidence=kw.get("evidence"))

    def run_campaign(self, fake, args=None, ac=True):
        pre = {"ident": dict({"runtime_sha256": "r"}, **self.rid), "runtime": Path("rt.exe"), "prompts": {p: "x" for p in set(A.ORDER)}}
        H = A.driver()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(A, "preconditions", return_value=pre))
            es.enter_context(mock.patch.object(A, "readiness_identity", return_value=self.rid))
            es.enter_context(mock.patch.object(A, "check_model_store", return_value={"model_sha256": "m", "store_sha256": "s"}))
            es.enter_context(mock.patch.object(A, "host_record", return_value={"ac_power": ac}))
            es.enter_context(mock.patch.object(A, "power_on_ac", return_value=ac))
            es.enter_context(mock.patch.object(A.time, "sleep"))
            es.enter_context(mock.patch.object(H, "run_launch", side_effect=fake))
            so = es.enter_context(redirect_stdout(io.StringIO()))
            rc = A.cmd_run(args or self.args)
        return rc, so.getvalue()


class Schedule(unittest.TestCase):
    def test_plan_is_the_predeclared_schedule(self):
        p = A.plan()
        self.assertEqual(len(p), 9)
        self.assertEqual((p[0]["step"], p[0]["counted"], p[0]["prompt"]), ("warmup", False, "code-dev-1"))
        self.assertEqual([s["prompt"] for s in p[1:]], ["code-dev-1", "nl-dev-1", "math-dev-1", "json-dev-1", "json-dev-1", "math-dev-1", "nl-dev-1", "code-dev-1"])
        self.assertTrue(all(s["arm"] == "A" and s["traced"] and s["budget"] == "1.5" and s["runtime"] == "r5" for s in p))

    def test_protocol_file_matches_the_code(self):
        proto = json.loads((HERE / "PROTOCOL_1B.json").read_text(encoding="utf-8"))
        self.assertEqual(proto["schedule"]["counted_order"], A.ORDER)
        self.assertEqual(proto["decision"]["threshold"], 0.04)

    def test_infrastructure_causes_are_the_frozen_list(self):
        dr = json.loads((A.P0_DIR / "DECISION_RULES.json").read_text(encoding="utf-8"))
        self.assertEqual(len(dr["blame_rules"]["infrastructure_causes"]), len(A.INFRASTRUCTURE_CAUSES))


class EvaluationLocked(unittest.TestCase):
    def ev_ids(self):
        w = json.loads((A.P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))
        return [p["id"] for p in w["evaluation"]["prompts"]]

    def test_loader_returning_an_evaluation_prompt_is_refused(self):
        H = A.driver()
        with mock.patch.object(H, "dev_prompts", return_value=[{"id": self.ev_ids()[0], "prompt": "?"}]):
            with self.assertRaises(SystemExit):
                A.development_prompts()

    def test_schedule_with_an_evaluation_id_is_refused(self):
        with mock.patch.object(A, "ORDER", A.ORDER[:7] + [self.ev_ids()[0]]):
            with self.assertRaises(SystemExit):
                A.development_prompts()

    def test_no_evaluation_id_or_access_in_phase1b_sources(self):
        ev = self.ev_ids()
        for f in list(HERE.glob("*.py")) + list(HERE.glob("*.json")) + list(HERE.glob("*.md")):
            t = f.read_text(encoding="utf-8")
            self.assertFalse(any(i in t for i in ev), f)
        src = (HERE / "acquire.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('["evaluation"]'), 1)        # only the guard reads the evaluation ids (never prompts)
        self.assertNotIn("workloads", src.replace("WORKLOADS", ""))


class Preconditions(Base):
    def test_failed_precondition_launches_nothing(self):
        fake = Fake()
        with mock.patch.object(A, "preconditions", side_effect=SystemExit("no")), mock.patch.object(A.driver(), "run_launch", side_effect=fake), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(A.cmd_run(self.args), A.EXIT_PRECONDITION)
        self.assertEqual(fake.calls, [])
        self.assertFalse(A.marker_path().exists())

    def _pre(self, **over):
        good = A.frozen()["binaries_sha256"]["llama-server.exe"]
        with ExitStack() as es:
            es.enter_context(mock.patch.object(A, "readiness_identity", side_effect=over.get("rid", lambda: self.rid)))
            es.enter_context(mock.patch.object(A.Path, "is_file", return_value=True))
            es.enter_context(mock.patch.object(A, "sha256_file", return_value=over.get("sha", good)))
            es.enter_context(mock.patch.object(A, "development_prompts", return_value={}))
            return A.preconditions(self.args, self.out)

    def test_all_preconditions_hold(self):
        self.assertIn("ident", self._pre())

    def test_each_precondition_fails_closed(self):
        def unready():
            raise SystemExit("Phase 1b readiness does not verify: g1_seal: FREEZE self-hash")
        for name, over in (("readiness / G1 seal / phase 0", {"rid": unready}), ("other runtime", {"sha": "0" * 64})):
            with self.assertRaises(SystemExit, msg=name):
                self._pre(**over)
        (self.out / A.RESULT).write_text("{}", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._pre()

    def test_identity_change_in_the_same_directory_refused(self):
        self._pre()
        (self.out / "IDENTITY_1B.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._pre()

    def test_second_output_directory_refused(self):
        mk = A.marker_path(); mk.parent.mkdir(parents=True)
        mk.write_text(json.dumps({"out": str(self.root / "first")}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._pre()

    def test_model_store_checked(self):
        with mock.patch.object(A, "sha256_file", return_value="0" * 64):
            with self.assertRaises(SystemExit):
                A.check_model_store(self.args)
        imf = A.frozen()
        with mock.patch.object(A, "sha256_file", side_effect=[imf["model"]["sha256"], imf["store"]["sha256"]]):
            self.assertEqual(A.check_model_store(self.args)["model_sha256"], imf["model"]["sha256"])


class Campaign(Base):
    def test_full_campaign_computes_H_once_at_the_end(self):
        calls_before_h = []
        real = A.headroom().launch_h
        def watch(*a, **k):
            calls_before_h.append(sum(1 for r in A.records(self.out) if r.get("type") == "launch"))
            return real(*a, **k)
        fake = Fake(tail_ns=int(0.05 * 290 * MS))       # 5% of a 290 ms decode step
        with mock.patch.object(A.headroom(), "launch_h", side_effect=watch):
            rc, so = self.run_campaign(fake)
        self.assertEqual(rc, 0, so)
        self.assertEqual(fake.calls, list(range(0, 9)))
        self.assertEqual(set(calls_before_h), {9}, "H must be computed only after all 9 launches are recorded")
        res = json.loads((self.out / A.RESULT).read_text(encoding="utf-8"))
        self.assertEqual(res["gate_G2"], "PASS"); self.assertAlmostEqual(res["H"], 0.05, places=9)
        self.assertEqual(len(res["launches"]), 8)
        self.assertEqual(res["identity"]["readiness_sha256"], "r")
        self.assertFalse(any(p.suffix == ".trace" for p in (self.out / "traces").iterdir()))           # raw deleted
        self.assertEqual(len(list((self.out / "traces").glob("*.trace.gz"))), 8)                       # warmup not kept
        self.assertEqual(so.count("H = "), 1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(A.cmd_verify(self.args), 0)
        self.assertTrue(A.marker_path().exists())

    def test_small_headroom_fails_the_gate(self):
        rc, _ = self.run_campaign(Fake(tail_ns=int(0.01 * 290 * MS)))
        self.assertEqual(rc, 0)
        res = json.loads((self.out / A.RESULT).read_text(encoding="utf-8"))
        self.assertEqual((res["gate_G2"], res["label"]), ("FAIL", "R5_NO_MEANINGFUL_OVERLAP_HEADROOM"))

    def test_one_shot(self):
        self.run_campaign(Fake())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(A.analyze(self.out), A.EXIT_PRECONDITION)
        rc, so = self.run_campaign(Fake())
        self.assertNotEqual(rc, 0)

    def test_invalid_launch_stops_without_H_and_rerun_needs_a_listed_cause(self):
        fake = Fake(bad=[3])
        with mock.patch.object(A.headroom(), "launch_h", side_effect=AssertionError("H computed")):
            rc, _ = self.run_campaign(fake)
        self.assertEqual(rc, A.EXIT_LAUNCH_INVALID); self.assertEqual(fake.calls, [0, 1, 2, 3])
        st = json.loads((self.out / A.STOPPED).read_text(encoding="utf-8"))
        self.assertEqual((st["seq"], st["g2_computed"], st["aborted"]), (3, False, False))
        rc, so = self.run_campaign(Fake())                                   # plain rerun: refused
        self.assertEqual(rc, A.EXIT_PRECONDITION); self.assertIn("rerun only with", so)
        ev = self.root / "eventlog.txt"; ev.write_text("Event 41: Kernel-Power", encoding="utf-8")
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=str(self.root / "missing.txt")))
        self.assertEqual(rc, A.EXIT_PRECONDITION)                            # evidence must exist
        fake2 = Fake()
        rc, so = self.run_campaign(fake2, self.ns(cause="power", evidence=str(ev)))
        self.assertEqual(rc, 0, so)
        self.assertEqual(fake2.calls, [0, 3, 4, 5, 6, 7, 8])                # a fresh warmup, then the rest
        recs = A.records(self.out)
        self.assertTrue(any(r.get("type") == "rerun_authorized" and r["slots"] == [3] for r in recs))
        res = json.loads((self.out / A.RESULT).read_text(encoding="utf-8"))
        self.assertEqual([x["attempt"] for x in res["launches"]], [1, 1, 2, 1, 1, 1, 1, 1])

    def test_second_failure_of_a_slot_aborts(self):
        ev = self.root / "e.txt"; ev.write_text("x", encoding="utf-8")
        self.run_campaign(Fake(bad=[2]))
        rc, so = self.run_campaign(Fake(bad=[2]), self.ns(cause="operator-interruption", evidence=str(ev)))
        self.assertEqual(rc, A.EXIT_ABORTED, so)
        self.assertTrue(json.loads((self.out / A.STOPPED).read_text(encoding="utf-8"))["aborted"])
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=str(ev)))
        self.assertEqual(rc, A.EXIT_ABORTED); self.assertFalse((self.out / A.RESULT).exists())

    def test_interrupted_attempt_counts_as_a_failure(self):
        A.append(self.out, {"type": "attempt_start", "seq": 1, "attempt": 1, "unix": 0})       # crash before the launch record
        rc, so = self.run_campaign(Fake())
        self.assertEqual(rc, A.EXIT_PRECONDITION); self.assertIn("interrupted", so)

    def test_unknown_or_battery_power_stops(self):
        for ac in (None, False):
            with self.subTest(ac=ac):
                fake = Fake()
                rc, _ = self.run_campaign(fake, ac=ac)
                self.assertEqual(rc, A.EXIT_PRECONDITION); self.assertEqual(fake.calls, [])
                (self.out / A.STOPPED).unlink()

    def test_estimator_change_after_acquisition_blocks_the_gate(self):
        with mock.patch.object(A, "analyze", return_value=0):
            self.run_campaign(Fake())
        rid2 = dict(self.rid, headroom_py_lf_sha256="changed")
        with mock.patch.object(A, "readiness_identity", return_value=rid2), redirect_stdout(io.StringIO()):
            self.assertEqual(A.analyze(self.out), A.EXIT_PRECONDITION)
        self.assertFalse((self.out / A.RESULT).exists())

    def test_tampered_kept_trace_is_telemetry_failure(self):
        fake = Fake()
        real_keep = A.keep_trace_factory
        def tamper(counted, *rest):
            k = real_keep(counted, *rest)
            def keep(trace):
                info = k(trace)
                if counted and fake.calls[-1] == 4:
                    gz = trace.with_name(trace.name + ".gz"); gz.write_bytes(gzip.compress(b"C\tets_schema\tr5-ets-v1\n"))
                return info
            return keep
        with mock.patch.object(A, "keep_trace_factory", side_effect=tamper):
            rc, _ = self.run_campaign(fake)
        self.assertEqual(rc, A.EXIT_TELEMETRY); self.assertFalse((self.out / A.RESULT).exists())

    def test_malformed_telemetry_invalidates_the_launch_immediately(self):
        fake = Fake()
        real = synthetic_trace
        with mock.patch(f"{__name__}.synthetic_trace", side_effect=lambda tail: real(tail, 510 if fake.calls[-1] == 2 else 511)):
            rc, so = self.run_campaign(fake)
        self.assertEqual(rc, A.EXIT_LAUNCH_INVALID, so); self.assertEqual(fake.calls, [0, 1, 2])

    def test_attempts_never_overwrite_and_superseded_traces_are_reported(self):
        ev = self.root / "e.txt"; ev.write_text("x", encoding="utf-8")
        self.run_campaign(Fake(bad=[3]))                                    # attempt 1 of slot 3 is kept, then invalid
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=str(ev)))
        self.assertEqual(rc, 0, so)
        kept = sorted(p.name for p in (self.out / "traces").glob("003-*.trace.gz"))
        self.assertEqual(len(kept), 2, kept); self.assertTrue(any("-a1.trace" in k for k in kept) and any("-a2.trace" in k for k in kept))
        res = json.loads((self.out / A.RESULT).read_text(encoding="utf-8"))
        self.assertEqual([(x["seq"], x["attempt"], x["decides"]) for x in res["superseded_attempts"]], [(3, 1, False)])
        self.assertIn("h_ms", res["superseded_attempts"][0])

    def test_erased_superseded_trace_blocks_the_gate(self):
        ev = self.root / "e.txt"; ev.write_text("x", encoding="utf-8")
        self.run_campaign(Fake(bad=[3]))
        next(self.out.glob("traces/003-*-a1.trace.gz")).unlink()
        rc, _ = self.run_campaign(Fake(), self.ns(cause="power", evidence=str(ev)))
        self.assertEqual(rc, A.EXIT_TELEMETRY); self.assertFalse((self.out / A.RESULT).exists())

    def test_trace_hash_logged_before_post_launch_work(self):
        with mock.patch.object(A, "analyze", return_value=0):
            self.run_campaign(Fake())
        recs = A.records(self.out)
        for r in recs:
            if r.get("type") == "launch" and r["counted"]:
                i = recs.index(r)
                self.assertEqual(recs[i - 1]["type"], "trace_kept"); self.assertEqual(recs[i - 1]["seq"], r["seq"])

    def test_operator_interruption_after_kept_trace_is_not_a_rerun_cause(self):
        ev = self.root / "e.txt"; ev.write_text("x", encoding="utf-8")
        class Killed(Fake):                       # the process dies after the trace was kept, before the launch record
            def __call__(self, slot, *a):
                rec = super().__call__(slot, *a)
                if slot["seq"] == 2:
                    raise KeyboardInterrupt
                return rec
        with self.assertRaises(KeyboardInterrupt):
            self.run_campaign(Killed())
        rc, so = self.run_campaign(Fake(), self.ns(cause="operator-interruption", evidence=str(ev)))
        self.assertEqual(rc, A.EXIT_PRECONDITION); self.assertIn("not a rerun cause", so)
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=str(ev)))   # a documented power loss still may
        self.assertEqual(rc, 0, so)
        res = json.loads((self.out / A.RESULT).read_text(encoding="utf-8"))
        self.assertEqual([(x["seq"], x["attempt"]) for x in res["superseded_attempts"]], [(2, 1)])

    def test_interrupted_during_trace_reduction_is_reported_and_not_rerunnable_as_operator(self):
        ev = self.root / "e.txt"; ev.write_text("x", encoding="utf-8")
        real_keep = A.keep_trace_factory
        def dying(counted, *rest):
            k = real_keep(counted, *rest)
            def keep(trace):
                if counted and rest[1] == 5:
                    A.append(rest[0], {"type": "trace_received", "seq": 5, "attempt": rest[2], "raw": trace.name, "bytes": 1, "unix": 0})
                    raise KeyboardInterrupt                 # killed before the gzip / trace_kept: the raw trace stays
                return k(trace)
            return keep
        with mock.patch.object(A, "keep_trace_factory", side_effect=dying), self.assertRaises(KeyboardInterrupt):
            self.run_campaign(Fake())
        rc, so = self.run_campaign(Fake(), self.ns(cause="operator-interruption", evidence=str(ev)))
        self.assertEqual(rc, A.EXIT_PRECONDITION); self.assertIn("not a rerun cause", so)
        rc, so = self.run_campaign(Fake(), self.ns(cause="power", evidence=str(ev)))
        self.assertEqual(rc, 0, so)
        res = json.loads((self.out / A.RESULT).read_text(encoding="utf-8"))
        raw = [x for x in res["superseded_attempts"] if "raw_unkept" in x]
        self.assertEqual(len(raw), 1); self.assertIn("h_ms", raw[0]); self.assertFalse(raw[0]["decides"])

    def test_unaccounted_file_in_traces_blocks_the_gate(self):
        with mock.patch.object(A, "analyze", return_value=0):
            self.run_campaign(Fake())
        (self.out / "traces" / "extra.trace.gz").write_bytes(b"x")
        with mock.patch.object(A, "readiness_identity", return_value=self.rid), redirect_stdout(io.StringIO()):
            self.assertEqual(A.analyze(self.out), A.EXIT_TELEMETRY)
        self.assertFalse((self.out / A.RESULT).exists())

    def test_status_never_shows_H(self):
        self.run_campaign(Fake())
        with redirect_stdout(io.StringIO()) as so:
            A.cmd_status(self.args)
        self.assertIn("counted launches valid: 8/8", so.getvalue()); self.assertNotIn("H", so.getvalue().replace("G2", ""))


class Validation(unittest.TestCase):
    def test_g1_b_record_is_valid_except_kept_trace(self):
        r = g1_b_record(); r["counted"] = False
        self.assertEqual(A.launch_problems(r), [])
        r["counted"] = True
        self.assertEqual(A.launch_problems(r), ["counted trace not kept or not validated"])

    def test_each_invalidity(self):
        muts = {
            "error": lambda r: r.update(error="TimeoutError"),
            "tokens": lambda r: r.update(n_tokens=511),
            "timing": lambda r: r.update(server_timings={}),
            "shutdown": lambda r: r.update(shutdown={"mode": "forced"}),
            "arm": lambda r: r["r5_counters"].update(expert_wait="kind"),
            "prefetch": lambda r: r["r5_counters"].update(prefetch="2"),
            "violations": lambda r: r["r5_counters"].update(violations=1),
            "pending": lambda r: r["r5_counters"].update(pending_at_event_end=1),
            "deferred": lambda r: r["r5_counters"].update(deferred_reads=5),
            "budget": lambda r: r["r5_counters"].update(budget=4294967296),
            "structure": lambda r: r["trace"]["structure"]["checks"].update(no_unparseable_records=False),
            "decode graphs": lambda r: r["trace"]["structure"].update(decode_graphs=400),
            "telemetry": lambda r: r["trace"].update(telemetry={"valid": False, "reason": "x"}),
            "ac after": lambda r: r.update(host_after={"ac_power": None}),
            "model/store after": lambda r: r.update(model_store_after={"ok": False}),
        }
        for name, m in muts.items():
            r = g1_b_record(); r["counted"] = False; m(r)
            self.assertTrue(A.launch_problems(r), name)


if __name__ == "__main__":
    unittest.main()
