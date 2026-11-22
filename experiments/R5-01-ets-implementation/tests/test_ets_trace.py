"""Tests of the R5 trace analysis on synthetic records (no runtime involved)."""
from __future__ import annotations

import importlib.util, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
import sys
spec = importlib.util.spec_from_file_location("ets_trace", HERE / "ets_trace.py"); A = importlib.util.module_from_spec(spec); sys.modules["ets_trace"] = A; spec.loader.exec_module(A)  # type: ignore


def T(graph, layer, kind, tid, first, hin, ws, we, hout, mode=2, n=0, b=0, nd=0, bd=0, pend=0, mask=7):
    return "\t".join(map(str, ["T", graph, layer, kind, tid, mode, first, hin, ws, we, hout, mask, n, b, nd, bd, pend]))


def I(req, tid, layer, expert, kind, nbytes, issue, done, ws, we, flags=1):
    return "\t".join(map(str, ["I", req, tid, layer, expert, kind, nbytes, issue, done, ws, we, 1, flags]))


def R(graph, layer, n_tokens):
    return "\t".join(map(str, ["R", graph, 0, layer, n_tokens, 8] + [0] * 8))


C = ["C\tets_schema\tr5-ets-v1", "C\texpert_wait\tkind"]


def two_thread_layer(graph=1, layer=0, base=1_000_000):
    """One decode layer event, 2 threads. Gate node: thread 0 waits 100, thread 1 waits 900 (critical = 900, mean = 500).
    Up and down reads issued at the gate node; down completes before the down node's earliest wait (hidden), up does not."""
    b = base
    lines = [R(graph, layer, 1)]
    # gate node: hook [b, b+2000] (t0) and [b+50, b+2100] (t1)
    lines += [T(graph, layer, 0, 0, 1, b, b + 500, b + 600, b + 2000, n=1, b=100), T(graph, layer, 0, 1, 1, b + 50, b + 500, b + 1400, b + 2100, n=1, b=100)]
    # up node: [b+3000, b+3500], waits up to b+3400
    lines += [T(graph, layer, 1, 0, 0, b + 3000, b + 3100, b + 3400, b + 3500), T(graph, layer, 1, 1, 0, b + 3010, b + 3100, b + 3200, b + 3500)]
    # down node: [b+4000, b+4300], earliest wait start b+4100
    lines += [T(graph, layer, 2, 0, 0, b + 4000, b + 4100, b + 4150, b + 4300), T(graph, layer, 2, 1, 0, b + 4020, b + 4120, b + 4160, b + 4300)]
    # reads issued inside the gate node window
    lines += [I(1, 0, layer, 3, 0, 100, b + 100, b + 1300, b + 500, b + 1400),   # gate (first kind)
              I(2, 0, layer, 3, 1, 200, b + 110, b + 3300, b + 3100, b + 3400),  # up: completes after its wait began -> exposed
              I(3, 1, layer, 3, 2, 400, b + 120, b + 3900, b + 4100, b + 4150)]  # down: completes before b+4100 -> hidden
    return lines


class CriticalPath(unittest.TestCase):
    def test_wait_is_max_over_threads_not_mean(self):
        log = A.parse_lines(C + two_thread_layer())
        nd = A.nodes(log)
        self.assertEqual(nd[(1, 0, 0)].wait_max, 900)                 # max, not the mean (500)
        self.assertEqual(nd[(1, 0, 0)].in_min, 1_000_000)
        self.assertEqual(nd[(1, 0, 0)].out_max, 1_002_100)

    def test_metrics(self):
        m = A.metrics_from_log(A.parse_lines(C + two_thread_layer()), decode_ms_per_token=1.0)
        self.assertEqual(m["decode_tokens"], 1)
        store = (2100) + (500) + (300)
        self.assertAlmostEqual(m["exposed_store_ms_per_token"], store / 1e6)
        self.assertAlmostEqual(m["exposed_wait_ms_per_token"], (900 + 300 + 50) / 1e6)
        self.assertAlmostEqual(m["hidden_deferred_bytes_fraction"], 400 / 600)
        self.assertEqual(m["deferred_read_bytes_per_token"], 600)
        self.assertAlmostEqual(m["non_hook_ms_per_token"], 1.0 - store / 1e6)

    def test_read_completed_inside_issue_is_never_hidden(self):
        lines = [l.replace("\t400\t", "\t400\t") for l in two_thread_layer()]
        # rewrite the down read with the SYNC_IN_ISSUE flag (1 | 16)
        lines[-1] = I(3, 1, 0, 3, 2, 400, 1_000_120, 1_000_121, 1_004_100, 1_004_150, flags=17)
        m = A.metrics_from_log(A.parse_lines(C + lines))
        self.assertEqual(m["hidden_deferred_bytes_fraction"], 0.0)

    def test_headroom_bound(self):
        # baseline-like event: first node waits for everything; tail of later kinds = c_all - c_first, clipped to the
        # first node's wait interval; window = start of last node - end of first node
        b = 5_000_000
        lines = [R(2, 0, 1),
                 T(2, 0, 0, 0, 1, b, b + 100, b + 1000, b + 1100, mode=0),
                 T(2, 0, 1, 0, 0, b + 1500, 0, 0, b + 1600, mode=0),
                 T(2, 0, 2, 0, 0, b + 1700, 0, 0, b + 1800, mode=0),
                 I(1, 0, 0, 1, 0, 10, b + 10, b + 300, b + 100, b + 1000),
                 I(2, 0, 0, 1, 1, 10, b + 20, b + 700, b + 100, b + 1000),
                 I(3, 0, 0, 1, 2, 10, b + 30, b + 950, b + 100, b + 1000)]
        m = A.metrics_from_log(A.parse_lines(C + lines))
        # tail = 950 - 300 = 650; window = 1700 - 1100 = 600 -> H = 600
        self.assertAlmostEqual(m["H_ms_per_token"], 600 / 1e6)

    def test_prefill_graphs_excluded(self):
        lines = two_thread_layer(graph=1) + [R(0, 0, 37)] + [T(0, 0, 0, 0, 1, 10, 20, 30, 40)]
        m = A.metrics_from_log(A.parse_lines(C + lines))
        self.assertEqual(m["decode_tokens"], 1)


class Structure(unittest.TestCase):
    def test_complete_trace(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.trace")
            Path(p).write_text("\n".join(C + two_thread_layer()) + "\n")
            s = A.structure(p)
        self.assertTrue(all(s["checks"].values()), s["checks"])
        self.assertNotIn("exposed_wait_ms_per_token", s)            # structure never reports a timing metric
        self.assertFalse(any("ms" in k for k in s))

    def test_incoherent_and_incomplete(self):
        import tempfile, os
        bad = C + two_thread_layer() + [I(9, 0, 7, 1, 1, 5, 100, 50, 10, 20), "T\t1\t2"]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.trace")
            Path(p).write_text("\n".join(bad) + "\n")
            s = A.structure(p)
        self.assertFalse(s["checks"]["I_records_coherent"])
        self.assertFalse(s["checks"]["no_unparseable_records"])
        self.assertFalse(s["checks"]["every_read_attributed_to_an_event"])


if __name__ == "__main__":
    unittest.main()
