"""Frozen mechanism metrics and strict per-arm telemetry checks on synthetic traces."""
from __future__ import annotations

import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import HERE, MS, Trace, launch_trace, load   # noqa: E402

M = load("mechanism", HERE / "mechanism.py")


def one(wait_mode="kind", n=1, **ev):
    t = Trace(wait_mode)
    for g in range(1, n + 1):
        t.graph(g); t.event(g, g * 10 * MS, **ev)
    return t


class Metrics(unittest.TestCase):
    def test_exposed_wait_and_store_follow_the_critical_path_rule(self):
        t = one(waits=(500, 100, 300))
        m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
        self.assertEqual(m.exposed_wait_ns, 500 + 100 + 300)                    # max over threads per node, summed over nodes
        self.assertEqual(m.exposed_store_ns, 3 * 1000)                          # (max hook_out - min hook_in) per node
        self.assertAlmostEqual(m.exposed_wait_ms_per_token, 900 / 1e6)

    def test_max_not_mean_or_sum_over_threads(self):
        t = Trace("kind"); t.graph(1)
        t.node(1, 0, 0, 1, 0, 1000, {0: (10, 410), 1: (10, 110), 5: (20, 220)})
        t.node(1, 0, 1, 0, 3000, 4000, {0: (3010, 3020)})
        t.read(0, 0, 5, 200)
        m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
        self.assertEqual(m.exposed_wait_ns, 400 + 10)

    def test_hidden_deferred_bytes(self):
        # node kind 1 starts at t0+3000, its wait at t0+3010; kind 2 node at t0+6000, wait at t0+6010
        t = one(deferred_complete=(2000, 7000))                                 # kind 1 hidden, kind 2 completes after its wait
        m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
        self.assertEqual((m.deferred_bytes, m.hidden_deferred_bytes), (8192, 4096))
        self.assertAlmostEqual(m.hidden_deferred_bytes_fraction, 0.5)

    def test_completion_exactly_at_the_earliest_wait_start_is_hidden(self):
        # every thread of a node starts waiting 1 ns after entering (fixture), so the earliest wait start is entry + 1
        t = one(deferred_complete=(3001, 6001))
        self.assertEqual(M.metrics(t.lines, "kind", expected_decode_tokens=1).hidden_deferred_bytes_fraction, 1.0)
        t = one(deferred_complete=(3002, 6002))                                 # 1 ns after the earliest wait start: not hidden
        self.assertEqual(M.metrics(t.lines, "kind", expected_decode_tokens=1).hidden_deferred_bytes_fraction, 0.0)

    def test_synchronous_reads_are_never_hidden(self):
        for f in (4, 8, 16, 4 | 1):
            t = one(deferred_complete=(2000, 2000), flags=(f, 1))
            m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
            self.assertEqual(m.hidden_deferred_bytes, 4096, f); self.assertEqual(m.not_hidden_sync_reads, 1)

    def test_bounded_completion_counts_only_if_the_bound_precedes_the_wait(self):
        t = one(deferred_complete=(2000, 6500), flags=(2, 2))
        m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
        self.assertEqual(m.hidden_deferred_bytes, 4096); self.assertEqual(m.bounded_deferred_reads, 2)

    def test_reads_of_the_issuing_kind_are_not_deferred(self):
        t = one(nbytes=(1000, 3000))
        m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
        self.assertEqual(m.deferred_bytes, 4000); self.assertEqual(m.deferred_reads, 2)

    def test_no_wait_at_the_consuming_node_uses_its_entry(self):
        t = one(deferred_complete=(3000, 6001), nowait_later=True)               # node 1 entry 3000, node 2 entry 6000
        m = M.metrics(t.lines, "kind", expected_decode_tokens=1)
        self.assertEqual(m.hidden_deferred_bytes, 4096)

    def test_no_deferred_bytes_counts_zero(self):
        t = Trace("kind"); t.graph(1)
        t.node(1, 0, 0, 1, 0, 1000, {0: (10, 20)}); t.node(1, 0, 1, 0, 3000, 4000, {0: (3010, 3020)})
        t.read(0, 0, 5, 200)
        self.assertEqual(M.metrics(t.lines, "kind", expected_decode_tokens=1).hidden_deferred_bytes_fraction, 0.0)

    def test_full_launch_shape(self):
        m = M.metrics(launch_trace("kind").splitlines(), "kind")
        self.assertEqual(m.decode_tokens, 511)


class Telemetry(unittest.TestCase):
    def test_valid_for_each_arm(self):
        for mode in ("layer", "layer_touch", "kind"):
            self.assertTrue(M.validate(launch_trace(mode).splitlines(), mode)["valid"])

    def test_wrong_arm_config_or_mode_refused(self):
        with self.assertRaises(M.TelemetryError):
            M.validate(launch_trace("kind").splitlines(), "layer_touch")
        t = Trace("kind", mode=1); t.graph(1); t.event(1, 10 * MS)
        with self.assertRaises(M.TelemetryError):
            M.validate(t.lines, "kind", expected_decode_tokens=1)

    def test_malformed_refused(self):
        bad = {
            "schema": Trace("kind", schema="v0"),
            "unparseable": None,
            "wrong decode count": None,
            "unattributable read": None,
            "missing thread": None,
            "missing consuming node": None,
        }
        t = bad["schema"]; t.graph(1); t.event(1, 10 * MS)
        with self.assertRaises(M.TelemetryError):
            M.validate(t.lines, "kind", expected_decode_tokens=1)
        t = one(); t.lines.append("T\tgarbage")
        with self.assertRaises(M.TelemetryError):
            M.validate(t.lines, "kind", expected_decode_tokens=1)
        with self.assertRaises(M.TelemetryError):
            M.validate(one(n=3).lines, "kind", expected_decode_tokens=1)
        t = one(); t.read(0, 1, 10 * MS + 2500, 10 * MS + 2600)                  # issued in the gap between nodes
        with self.assertRaises(M.TelemetryError):
            M.validate(t.lines, "kind", expected_decode_tokens=1)
        t = one(); t.lines = [l for l in t.lines if not (l.startswith("T") and l.split("\t")[4] == "3" and l.split("\t")[3] == "1")]
        with self.assertRaises(M.TelemetryError):
            M.validate(t.lines, "kind", expected_decode_tokens=1)
        t = Trace("kind"); t.graph(1)
        t.node(1, 0, 0, 1, 0, 1000, {0: (10, 20)}); t.node(1, 0, 1, 0, 3000, 4000, {0: (3010, 3020)})
        t.read(0, 2, 5, 200)                                                    # deferred kind 2 without a kind-2 node
        with self.assertRaises(M.TelemetryError):
            M.metrics(t.lines, "kind", expected_decode_tokens=1)

    def test_one_token_last_prefill_micro_batch_is_not_a_decode_graph(self):
        t = Trace("kind"); t.graph(0, n_tokens=512)
        for g in range(1, 4):                                                   # graph 1 = the prompt's last micro-batch (1 token)
            t.graph(g); t.event(g, g * 10 * MS)
        v = M.validate(t.lines, "kind", expected_decode_tokens=2)
        self.assertEqual(v["decode_graphs"], 2)
        self.assertEqual(M.metrics(t.lines, "kind", expected_decode_tokens=2).decode_tokens, 2)

    def test_validate_returns_no_metric(self):
        v = M.validate(launch_trace("kind").splitlines(), "kind")
        self.assertEqual(set(v), {"valid", "decode_graphs", "events", "reads"})


if __name__ == "__main__":
    unittest.main()
