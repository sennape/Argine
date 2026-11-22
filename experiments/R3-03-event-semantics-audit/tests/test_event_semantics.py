"""R3 event-semantics audit tests: deterministic, offline, NO model, no trace files.

Real traces stay outside git; these tests exercise the audit logic on synthetic
streams, validate the simulation against the frozen R2 reference predictor, and
pin the source-level facts the audit relies on.
"""

from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PHASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import r3_event_semantics_audit as A  # noqa: E402


def _row(step, layer, requested, protected=(), miss=0, hit=0, evicted=0, bol=3059712):
    return {"step": step, "layer": layer, "policy": "predicted_protect_sham",
            "bytes_of_layer": bol, "requested": list(requested), "hit": hit, "miss": miss,
            "protected": list(protected), "evicted": evicted}


def _triplicated_rows(sets_by_pass, n_layers=2, with_protected=True):
    """Build runtime-style rows: 3 calls per (pass, layer); protected_before
    computed by the same predictor so the stream is self-consistent."""
    sims = {l: A.DecayedTransitionSim() for l in range(n_layers)}
    last = {l: () for l in range(n_layers)}
    rows, step = [], 0
    for p, per_layer in enumerate(sets_by_pass):
        for l in range(n_layers):
            for _ in range(3):
                rows.append(_row(step, l, per_layer[l], last[l] if with_protected else ()))
                last[l] = sims[l].observe(per_layer[l])
                step += 1
    return rows


def _rand_sets(rng, n_pass, n_layers=2, k=8):
    return [[sorted(rng.sample(range(128), k)) for _ in range(n_layers)] for _ in range(n_pass)]


class ExtractTests(unittest.TestCase):
    def test_extract_prefix_and_skip_completion(self):
        line = (b'{"step":3,"policy":"predicted_protect_sham","layer":1,"bytes_of_layer":10,'
                b'"requested_experts":[1,2],"hit_experts":[1],"miss_experts":[2],'
                b'"protected_before":[5,6],"resident_before":[{"layer":1,"expert":1}],'
                b'"evicted":[{"layer":0,"expert":9},{"layer":0,"expert":8}],"resident_after":[]}\n')
        done = b'{"step":3,"complete":1,"cache_resident_bytes_post_service":1}\n'
        got = list(A.extract_rows([line, done]))
        self.assertEqual(len(got), 1)
        r = got[0]
        self.assertEqual((r["step"], r["layer"], r["requested"], r["hit"], r["miss"],
                          r["protected"], r["evicted"]), (3, 1, [1, 2], 1, 1, [5, 6], 2))


class GroupingTests(unittest.TestCase):
    def test_triplets_and_pass_numbering(self):
        rows = _triplicated_rows(_rand_sets(random.Random(1), 4))
        groups = A.build_layer_groups(rows)
        self.assertEqual(len(groups), 4 * 2)
        self.assertTrue(all(len(g["rows"]) == 3 for g in groups))
        self.assertEqual([g["pass"] for g in groups], [0, 0, 1, 1, 2, 2, 3, 3])

    def test_event_counts_actual_over_semantic_is_three(self):
        rows = _triplicated_rows(_rand_sets(random.Random(2), 5))
        groups = A.build_layer_groups(rows)
        ec = A.event_counts(rows, groups, n_generated=4, n_layers=2)
        self.assertEqual(ec["policy_rows"], 30)
        self.assertEqual(ec["semantic_events_if_one_per_layer_per_pass"], 10)
        self.assertEqual(ec["actual_over_semantic"], 3.0)
        self.assertEqual(ec["calls_per_layer_per_decode_pass"], 3.0)
        self.assertTrue(ec["policy_step_contiguous"])

    def test_pass_kind_multi_token(self):
        sets = _rand_sets(random.Random(3), 3)
        sets[1] = [sorted(random.Random(4).sample(range(128), 15)) for _ in range(2)]
        groups = A.build_layer_groups(_triplicated_rows(sets))
        self.assertEqual(A.pass_kinds(groups), {0: "decode", 1: "multi_token", 2: "decode"})


class IdentityTests(unittest.TestCase):
    def _groups(self, a, b, c):
        rows = [_row(0, 0, a), _row(1, 0, b), _row(2, 0, c)]
        return A.build_layer_groups(rows)

    def test_identical_partial_disjoint(self):
        same = list(range(8))
        r = A.triplet_identity(self._groups(same, same, same))
        self.assertEqual(r["all"]["identical"], 1.0)
        self.assertIsNone(r["first_mismatch"])
        part = A.triplet_identity(self._groups(same, same, [0, 1, 2, 3, 4, 5, 6, 99]))
        self.assertEqual(part["all"]["partially_different"], 1.0)
        self.assertEqual(part["first_mismatch"]["steps"], [0, 1, 2])
        dis = A.triplet_identity(self._groups(range(8), range(8, 16), range(16, 24)))
        self.assertEqual(dis["all"]["fully_different"], 1.0)


class PredictorEquivalenceTests(unittest.TestCase):
    def test_sim_is_bit_identical_to_frozen_r2_reference(self):
        from argine.research2.r2_10.causal_walk import CausalContext
        from argine.research2.r2_10.predictors import DecayedTransitionPredictor
        rng = random.Random(7)
        ref = DecayedTransitionPredictor(128, 0.99)
        sim = A.DecayedTransitionSim(128, 0.99, 8)
        hist = []
        for i in range(120):
            s = tuple(sorted(rng.sample(range(32), 8)))     # small universe -> many ties/overlaps
            hist.append((i, s))
            ctx = CausalContext(position=i, token_index=i, history=tuple(hist), k=8)
            self.assertEqual(tuple(sorted(ref.predict(ctx, k=8))), sim.observe(s), msg=f"i={i}")

    def test_conformance_check_detects_a_divergent_stream(self):
        rows = _triplicated_rows(_rand_sets(random.Random(5), 6))
        res = A.simulate(A.build_layer_groups(rows))
        self.assertEqual(res["runtime_stream_conformance_vs_logged_protected_before"]["fraction"], 1.0)
        rows[10]["protected"] = [0, 1, 2, 3, 4, 5, 6, 7]
        bad = A.simulate(A.build_layer_groups(rows))
        self.assertLess(bad["runtime_stream_conformance_vs_logged_protected_before"]["fraction"], 1.0)
        self.assertIsNotNone(bad["runtime_stream_conformance_vs_logged_protected_before"]["first_mismatch"])


class TriplicationStateEffectTests(unittest.TestCase):
    def test_history_and_decay_and_updates(self):
        rows = _triplicated_rows(_rand_sets(random.Random(11), 30))
        res = A.simulate(A.build_layer_groups(rows))
        st = res["state_effect"]
        self.assertEqual(st["history_ratio"], 3.0)
        self.assertGreaterEqual(st["transition_update_ratio"], 3.0)
        self.assertAlmostEqual(st["effective_decay_per_token_R3"], 0.99 ** 3)
        self.assertAlmostEqual(st["effective_time_constant_tokens_R2"], 100.0, places=6)
        self.assertLess(st["effective_time_constant_tokens_R3"], 34.0)

    def test_triplication_changes_the_consulted_protected_set(self):
        rng = random.Random(21)
        stream = [sorted(rng.sample(range(128), 8)) for _ in range(60)]
        r2, r3 = A.DecayedTransitionSim(), A.DecayedTransitionSim()
        differ = 0
        ov2 = ov3 = 0
        for s in stream:
            p2 = r2.observe(s)
            for _ in range(3):
                p3 = r3.observe(s)
            differ += int(p2 != p3)
            ov2 += len(set(p2) & set(s))
            ov3 += len(set(p3) & set(s))
        self.assertGreater(differ, len(stream) // 2)       # consulted sets mostly differ
        self.assertGreater(ov3, ov2)                       # R3 leans toward the set just seen

    def test_miss_by_call_position_and_hit_rate_inflation(self):
        rows = [_row(0, 0, range(8), miss=8, hit=0, evicted=8),
                _row(1, 0, range(8), miss=2, hit=6, evicted=2),
                _row(2, 0, range(8), miss=0, hit=8)]
        m = A.miss_by_call_position(A.build_layer_groups(rows))
        self.assertEqual(m["misses_by_call_position"], [8, 2, 0])
        self.assertAlmostEqual(m["fraction_of_misses_at_calls_2_and_3"], 0.2)
        self.assertGreater(m["logical_hit_rate_as_logged_all_calls"],
                           m["logical_hit_rate_first_call_only"])


class R2EventDefinitionTests(unittest.TestCase):
    def test_r2_trace_yields_one_event_per_step_and_layer_with_union(self):
        from argine.research.trace import Trace, TraceMeta, Step
        meta = TraceMeta(model="m", n_experts=128, top_k=8, layers=[0, 1], source="synthetic")
        steps = [
            Step(graph=0, n_tokens=2, routes={0: [(1, 2, 3), (3, 4, 5)], 1: [(7, 8, 9), (7, 8, 10)]}),
            Step(graph=1, n_tokens=1, routes={0: [(6, 7, 8)], 1: [(1, 2, 3)]}),
        ]
        tr = Trace(meta=meta, steps=steps)
        ev = list(tr.events())
        self.assertEqual([(i, l) for i, l, _ in ev], [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertEqual(ev[0][2], (1, 2, 3, 4, 5))      # union across the tokens of one step
        # gate / up / down of one layer are NOT separate R2 events
        self.assertEqual(len(ev), len(steps) * len(meta.layers))


class SourceLevelRootCauseTests(unittest.TestCase):
    """Pin the source facts the audit's root-cause statement rests on."""

    @classmethod
    def setUpClass(cls):
        p = REPO / "patches"
        cls.experts = (p / "argine-experts.cpp").read_text(encoding="utf-8", errors="replace")
        cls.store = (p / "argine-store.cpp").read_text(encoding="utf-8", errors="replace")
        cls.policy = (p / "argine-retention-policy.cpp").read_text(encoding="utf-8", errors="replace")

    def test_all_three_expert_tensor_kinds_reach_the_hook(self):
        for kind in ("ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"):
            needle = f'strstr(nome, ".{kind}.weight")'
            self.assertTrue(needle in self.experts, msg=f"identifica() does not recognise {kind}")
        self.assertTrue("op->op != GGML_OP_MUL_MAT_ID" in self.experts)

    def test_router_trace_dedups_per_graph_layer_but_policy_hook_does_not(self):
        guard = "c.arx_route_graph != c.arx_graph || c.arx_route_layer != (int64_t) id.layer"
        self.assertTrue(guard in self.experts, msg="R2-side router logger no longer dedups per (graph, layer)")
        marker = "} else if (psx_cache_has_retention_policy(c.cache)) {"
        self.assertEqual(self.experts.count(marker), 1)
        branch = self.experts[self.experts.index(marker):]
        branch = branch[:branch.index("        } else {\n            // La seconda")]
        self.assertTrue("psx_cache_ensure_many(" in branch)
        self.assertTrue("if (ith == 0) {" in branch)
        self.assertFalse("arx_route_graph" in branch,
                         msg="policy branch unexpectedly keyed by (graph, layer)")

    def test_each_ensure_many_call_is_one_policy_event(self):
        self.assertEqual(self.store.count("c->policy->access(step, layer"), 1)
        self.assertEqual(self.store.count("c->policy_step++"), 1)
        self.assertTrue("return servi_evento_con_politica(c, s, n, regions, offsets, layers, experts, kinds)"
                        in self.store)

    def test_every_access_advances_history_and_prediction(self):
        self.assertEqual(self.policy.count("after_event(step, layer, served);"), 1)
        body = self.policy[self.policy.index("void PredictedProtectPolicy::after_event"):]
        for needle in ("pred.history.push_back(served_sorted)",
                       "predicted_next_[layer] = predict_for_layer(layer)",
                       "predictor_decisions_++"):
            self.assertTrue(needle in body, msg=needle)


class RecordedAuditConsistencyTests(unittest.TestCase):
    def test_recorded_audit_matches_structural_facts(self):
        path = PHASE / "AUDIT.json"
        if not path.exists():
            self.skipTest("AUDIT.json not yet generated")
        audit = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(audit["classification"], "R2_R3_EVENT_SEMANTICS_NOT_EQUIVALENT")
        for b in ("1.5GiB", "4GiB", "8GiB"):
            ec = audit["event_counts"][b]
            self.assertEqual(ec["policy_rows"], 74016)
            self.assertEqual(ec["layer_groups"] * 3, ec["policy_rows"])
            self.assertEqual(ec["forward_passes"] * 48, ec["layer_groups"])
            self.assertEqual(ec["actual_over_semantic"], 3.0)
            self.assertEqual(audit["set_identity"][b]["all"]["identical"], 1.0)
            conf = audit["simulation"][b]["runtime_stream_conformance_vs_logged_protected_before"]
            self.assertEqual(conf["fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()
