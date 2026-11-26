"""Deterministic synthetic-trace tests of the Phase 1b headroom bound H (headroom.py) and the G2 decision."""
from __future__ import annotations

import gzip, importlib.util, sys, tempfile, unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("headroom", HERE / "headroom.py")
HR = importlib.util.module_from_spec(spec); sys.modules["headroom"] = HR; spec.loader.exec_module(HR)  # type: ignore

NT = HR.EXPECTED_THREADS
MS = 1_000_000


class Trace:
    """Builds argine-raw lines: one decode graph per add_event call unless graph is given."""

    def __init__(self, arm="layer", schema="r5-ets-v1", tid_base=0):
        self.lines = [f"C\texpert_wait\t{arm}", f"C\tets_schema\t{schema}"]
        self.req = 0
        self.tid_base = tid_base          # arx_thread_id is a process-wide first-come counter: ids need not start at 0

    def graph(self, g, n_tokens=1):
        self.lines.append(f"R\t{g}\t0\t0\t{n_tokens}\t8")

    def node(self, g, layer, kind, first, t_in, t_out, waits, mode=0, threads=NT, nowait=False):
        """waits: {thread index: (wait_start, wait_end)}; every other thread waits briefly right after entering (as in a
        real B trace, where every thread calls the wait), unless nowait."""
        for i in range(threads):
            ws, we = waits.get(i, (0, 0) if nowait else (t_in + 1, t_in + 2))
            self.lines.append("\t".join(map(str, ["T", g, layer, kind, self.tid_base + i, mode, first, t_in, ws, we, t_out, 7, 0, 0, 0, 0, 0])))

    def read(self, layer, kind, issue, complete, flags=1, tid=None, nbytes=4096, ok=1):
        self.req += 1
        tid = self.tid_base + (self.req % NT if tid is None else tid)        # reads owned by different threads
        self.lines.append("\t".join(map(str, ["I", self.req, tid, layer, 3, kind, nbytes, issue, complete, 0, 0, ok, flags])))

    def event(self, g, layer, t0, wait, gap, reads, extra_waits=None, node_len=None):
        """A 3-node layer event starting at t0. First node: hook t0..t0+node_len with thread 0 waiting `wait`
        (ending at the hook end); W = `gap` between the first node's end and the last (third) node's start.
        reads: list of (kind, issue, complete, flags) with times relative to t0."""
        nl = node_len or (wait[1] + 1)
        waits = {0: (t0 + wait[0], t0 + wait[1])}
        for tid, (a, b) in (extra_waits or {}).items():
            waits[tid] = (t0 + a, t0 + b)
        self.node(g, layer, 0, 1, t0, t0 + nl, waits)
        mid = t0 + nl + gap // 2
        self.node(g, layer, 1, 0, mid, mid + 20, {})
        self.node(g, layer, 2, 0, t0 + nl + gap, t0 + nl + gap + 20, {})
        for kind, iss, comp, flags in reads:
            self.read(layer, kind, t0 + iss, t0 + comp, flags)

    def h(self, expected_tokens=None):
        return HR.launch_h(self.lines, expected_tokens)


def one_event(wait, gap, reads, **kw):
    t = Trace(); t.graph(1); t.event(1, 0, 1000, wait, gap, reads, **kw); return t


class BoundedGate(unittest.TestCase):
    def test_bounded_gate_read_uses_its_issue_time(self):
        # reviewer counterexample: gate finished at 200 (exact) -> 400; recorded only at a late poll (flag 2, 590): the read
        # finished somewhere in [issue 10, 590], so the optimistic end (issue) is used and H is not shrunk
        exact = one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)]).h().h_ns
        late = one_event((100, 600), 10 * MS, [(0, 10, 590, 2), (2, 10, 600, 1)]).h().h_ns
        self.assertEqual(exact, 400); self.assertEqual(late, 500)       # clipped at the wait start 100
        self.assertGreaterEqual(late, exact)

    def test_wait_end_without_wait_start_is_incoherent(self):
        t = Trace(); t.graph(1); t.event(1, 0, 1000, (100, 600), 10 * MS, [(0, 10, 200, 1)])
        f = t.lines[3].split("\t"); f[8], f[9] = "0", str(int(f[7]) + 5); t.lines[3] = "\t".join(f)
        with self.assertRaises(HR.TelemetryError):
            t.h()


class Estimator(unittest.TestCase):
    def test_zero_headroom_first_kind_is_last(self):
        # the wait ends when the gate (first-kind) read arrives: nothing to hide
        t = one_event((100, 600), 10 * MS, [(0, 10, 600, 1), (1, 10, 300, 1), (2, 10, 200, 1)])
        self.assertEqual(t.h().h_ns, 0)

    def test_positive_headroom_tail_limited(self):
        # gate ready at 200, last deferred read at 600, wait 100..600, W = 10 ms: H = 400
        t = one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (1, 10, 500, 1), (2, 10, 600, 1)])
        r = t.h(); self.assertEqual(r.h_ns, 400); self.assertEqual(r.events_positive, 1)

    def test_positive_headroom_gap_limited(self):
        t = one_event((100, 600), 150, [(0, 10, 200, 1), (2, 10, 600, 1)])
        self.assertEqual(t.h().h_ns, 150)              # min(T_tail 400, W 150)

    def test_overlapping_deferred_reads_counted_once(self):
        # three overlapping deferred reads: only the latest completion bounds the tail; no double counting
        t = one_event((100, 700), 10 * MS, [(0, 10, 200, 1), (1, 10, 650, 1), (1, 20, 640, 1), (2, 30, 700, 1)])
        self.assertEqual(t.h().h_ns, 500)

    def test_simultaneous_reads_same_issue_time(self):
        t = one_event((100, 500), 10 * MS, [(0, 50, 300, 1), (1, 50, 500, 1), (2, 50, 500, 1)])
        self.assertEqual(t.h().h_ns, 200)

    def test_synchronous_completion_never_extends_the_tail(self):
        # a deferred read completed inside its issuing call (flag 16) at 590 is not hideable: tail ends at 400
        t = one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (1, 10, 400, 1), (2, 590, 590, 1 | 16)])
        r = t.h(); self.assertEqual(r.h_ns, 200); self.assertEqual(r.not_overlapped_reads, 1)

    def test_synchronous_and_fallback_reads_never_extend_the_tail(self):
        for flags in (1 | 4, 1 | 4 | 8, 1 | 16):
            t = one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (1, 10, 400, 1), (2, 590, 590, flags)])
            r = t.h(); self.assertEqual(r.h_ns, 200, flags); self.assertEqual(r.not_overlapped_reads, 1)

    def test_synchronous_first_kind_read_still_marks_readiness(self):
        t = one_event((100, 600), 10 * MS, [(0, 300, 300, 1 | 16), (2, 10, 600, 1)])
        self.assertEqual(t.h().h_ns, 300)               # first-kind ready at 300

    def test_already_overlapped_reads_before_the_wait(self):
        # every read completed before the wait started: read/read overlap only, no hideable stall
        t = one_event((400, 410), 10 * MS, [(0, 10, 200, 1), (1, 10, 300, 1), (2, 10, 390, 1)])
        self.assertEqual(t.h().h_ns, 0)

    def test_late_completion_clipped_to_the_wait(self):
        # a completion stamped after the wait end (bound) is clipped at the wait end
        t = one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 900, 2)])
        r = t.h(); self.assertEqual(r.h_ns, 400); self.assertEqual(r.bounded_completion_reads, 1)

    def test_critical_interval_is_the_wait_that_ends_last(self):
        # independent review counterexample: thread 0 waits 100..700 (the longest wait), thread 5 waits 450..1000 and
        # leaves last; first-kind data ready at 650, last read at 1000. The kind arm could save 1000 - max(650, 450) = 350.
        # The longest-wait interval would give 50 (an under-count); the last-ending wait gives 350.
        t = one_event((100, 700), 10 * MS, [(0, 10, 650, 1), (2, 10, 1000, 1)], extra_waits={5: (450, 1000)}, node_len=1001)
        r = t.h(); self.assertEqual(r.h_ns, 350); self.assertLessEqual(r.h_ns, r.first_wait_ns)

    def test_union_of_waits_is_not_used(self):
        # thread 0 waits 100..150, thread 5 waits 300..600 (ends last): the union 100..600 would give 480; the
        # critical interval 300..600 gives 300
        t = one_event((100, 150), 10 * MS, [(0, 10, 120, 1), (2, 10, 600, 1)], extra_waits={5: (300, 600)}, node_len=601)
        self.assertEqual(t.h().h_ns, 300)

    def test_h_never_exceeds_the_first_node_wait(self):
        t = one_event((100, 120), 10 * MS, [(0, 10, 101, 1), (2, 10, 120, 1)], extra_waits={3: (50, 60)}, node_len=200)
        r = t.h(); self.assertLessEqual(r.h_ns, r.first_wait_ns)

    def test_no_reads_or_no_wait(self):
        self.assertEqual(one_event((100, 200), 10 * MS, []).h().h_ns, 0)
        t = Trace(); t.graph(1)
        for k, (a, b) in enumerate(((0, 50), (60, 70), (80, 90))):
            t.node(1, 0, k, int(k == 0), a, b, {}, nowait=True)
        self.assertEqual(t.h().h_ns, 0)

    def test_thread_ids_need_not_start_at_zero(self):
        t = Trace(tid_base=1); t.graph(1); t.event(1, 0, 1000, (100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)])
        self.assertEqual(t.h().h_ns, 400)

    def test_validate_returns_no_headroom(self):
        v = HR.validate(one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)]).lines)
        self.assertEqual(v["valid"], True); self.assertFalse(any("h" == k[:1].lower() for k in v))

    def test_prefill_graph_excluded_and_tokens_counted(self):
        t = Trace(); t.graph(0, n_tokens=40); t.graph(1); t.graph(2)
        t.event(0, 0, 1000, (100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)])          # prefill: not counted
        t.event(1, 0, 50 * MS, (100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)])
        t.event(2, 0, 90 * MS, (100, 600), 10 * MS, [(0, 10, 500, 1), (2, 10, 600, 1)])
        r = t.h(expected_tokens=2)
        self.assertEqual((r.decode_tokens, r.events, r.h_ns), (2, 2, 400 + 100))

    def test_reads_of_later_nodes_are_not_first_node_reads(self):
        t = Trace(); t.graph(1); t.event(1, 0, 1000, (100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)])
        t.read(0, 2, 1000 + 601 + 5 * MS + 5, 1000 + 601 + 5 * MS + 100)  # issued inside the middle node window
        self.assertEqual(t.h().h_ns, 400)


class Malformed(unittest.TestCase):
    def good(self):
        return one_event((100, 600), 10 * MS, [(0, 10, 200, 1), (2, 10, 600, 1)])

    def test_each_defect_fails_closed(self):
        cases = {}
        t = self.good(); t.lines.append("T\t1\t2\tx"); cases["unparseable record"] = t
        t = self.good(); t.lines[1] = "C\tets_schema\tother"; cases["wrong schema"] = t
        t = self.good(); t.lines[0] = "C\texpert_wait\tkind"; cases["not the B arm"] = t
        t = self.good(); t.lines = [l for l in t.lines if not (l.startswith("T\t1\t0\t0\t5\t"))]; cases["missing thread"] = t
        t = self.good(); t.lines = [l.replace("T\t1\t0\t0\t5\t", "T\t1\t0\t0\t4\t") for l in t.lines]; cases["duplicate thread id"] = t
        t = self.good(); t.lines = [l.replace("T\t1\t0\t2\t5\t", "T\t1\t0\t2\t99\t") for l in t.lines]; cases["thread-id set differs"] = t
        t = self.good(); t.read(0, 1, 1100, 1200, tid=77); cases["read by an unknown thread"] = t
        t = self.good(); t.read(0, 1, 10 ** 12, 10 ** 12 + 5); cases["unattributable read"] = t
        t = self.good(); t.read(0, 1, 1100, 1050); cases["completion before issue"] = t
        t = self.good(); t.read(0, 1, 1100, 1200, ok=0); cases["failed read"] = t
        t = self.good(); t.lines = [l for l in t.lines if not l.startswith("R\t")]; cases["no decode graph"] = t
        t = Trace(); t.graph(1); t.node(1, 0, 0, 1, 0, 50, {}); cases["single-node event"] = t
        t = Trace(); t.graph(1); t.node(1, 0, 0, 0, 0, 50, {}); t.node(1, 0, 1, 0, 60, 70, {}); cases["no first node"] = t
        t = self.good(); t.lines.append("\t".join(map(str, ["T", 1, 0, 0, 0, 0, 1, 500, 400, 450, 300, 0, 0, 0, 0, 0, 0]))); cases["incoherent T"] = t
        for name, t in cases.items():
            with self.assertRaises(HR.TelemetryError, msg=name):
                t.h()
            with self.assertRaises(HR.TelemetryError, msg="validate: " + name):
                HR.validate(t.lines)
        with self.assertRaises(HR.TelemetryError):
            self.good().h(expected_tokens=511)                   # decode-graph count differs from the launch

    def test_truncated_or_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.trace.gz"
            data = gzip.compress("\n".join(self.good().lines).encode())
            p.write_bytes(data[: len(data) // 2])
            with self.assertRaises(HR.TelemetryError):
                HR.read_lines(p)
            q = Path(d) / "u.trace"; q.write_bytes(b"C\tets_schema\t\xff\xfe\n")
            with self.assertRaises(HR.TelemetryError):
                HR.read_lines(q)
            ok = Path(d) / "ok.trace.gz"; ok.write_bytes(gzip.compress("\n".join(self.good().lines).encode()))
            self.assertEqual(HR.launch_h(HR.read_lines(ok)).h_ns, 400)


def fake(h_ns, tokens=511):
    return HR.LaunchH(decode_tokens=tokens, h_ns=h_ns, events=1, events_positive=1, tail_ns=h_ns, gap_ns=h_ns, first_wait_ns=h_ns,
                      first_node_reads=1, not_overlapped_reads=0, bounded_completion_reads=0)


class Decision(unittest.TestCase):
    def test_exact_four_percent_boundary(self):
        # decode: 511 steps of 290 ms -> predicted_ms = 148190 per launch; H exactly 4% of the 8 launches' decode time
        pm = 290.0 * 511
        h_tot = int(Fraction(4, 100) * Fraction(pm) * 8 * 1_000_000)
        per = [h_tot // 8] * 8; per[0] += h_tot - sum(per)
        timing = [(pm, 512)] * 8
        d = HR.decide([fake(x) for x in per], timing)
        self.assertEqual(Fraction(d["H_exact"]), Fraction(1, 25)); self.assertEqual(d["gate_G2"], "PASS")
        self.assertEqual(d["label"], "R5_G2_HEADROOM_GATE_PASS")
        per[0] -= 1
        d = HR.decide([fake(x) for x in per], timing)
        self.assertLess(Fraction(d["H_exact"]), Fraction(1, 25)); self.assertEqual(d["gate_G2"], "FAIL")
        self.assertEqual(d["label"], "R5_NO_MEANINGFUL_OVERLAP_HEADROOM")

    def test_same_decode_steps_in_numerator_and_denominator(self):
        # H per decode step 11.6 ms, decode 290 ms per decode step (predicted_ms over 511 steps) -> exactly 4%
        d = HR.decide([fake(int(11.6 * MS * 511))] * 8, [(290.0 * 511, 512)] * 8)
        self.assertAlmostEqual(d["H"], 0.04, places=12)
        self.assertAlmostEqual(d["decode_ms_per_decode_step_mean"], 290.0)
        with self.assertRaises(HR.TelemetryError):
            HR.decide([fake(1, tokens=512)] * 8, [(1.0, 512)] * 8)            # decode graphs must be predicted_n - 1

    def test_zero_and_large(self):
        timing = [(150_000.0, 512)] * 8
        self.assertEqual(HR.decide([fake(0)] * 8, timing)["gate_G2"], "FAIL")
        self.assertEqual(HR.decide([fake(10 ** 12)] * 8, timing)["gate_G2"], "PASS")

    def test_pooled_aggregation_and_per_launch_report(self):
        timing = [(100.0 * 511, 512)] * 4 + [(300.0 * 511, 512)] * 4       # mean decode 200 ms per step
        hs = [2 * MS * 511] * 4 + [6 * MS * 511] * 4                         # pooled H 4 ms per step
        d = HR.decide([fake(x) for x in hs], timing)
        self.assertAlmostEqual(d["H"], 0.02); self.assertAlmostEqual(d["H_ms_per_decode_step"], 4.0)
        self.assertEqual(len(d["per_launch_H_fraction"]), 8)

    def test_exactly_eight_launches_and_valid_timings(self):
        with self.assertRaises(HR.TelemetryError):
            HR.decide([fake(1)] * 7, [(1.0, 512)] * 7)
        with self.assertRaises(HR.TelemetryError):
            HR.decide([fake(1)] * 8, [(0.0, 512)] + [(1.0, 512)] * 7)

    def test_threshold_is_the_frozen_value(self):
        import json
        dr = json.loads((HR.REPO / "experiments" / "R5-00-exposed-transfer-stall" / "DECISION_RULES.json").read_text(encoding="utf-8"))
        self.assertEqual(Fraction(dr["thresholds"]["headroom_gate"]).limit_denominator(1000), HR.HEADROOM_GATE)
        self.assertIn(HR.LABEL_FAIL, json.dumps(dr["gates"]["G2_headroom"]))


if __name__ == "__main__":
    unittest.main()
