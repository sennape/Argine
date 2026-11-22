"""Contract tests for Research 5 Phase 0 (ETS). They read only the frozen contract and repository records; nothing is launched."""
from __future__ import annotations

import collections, copy, importlib.util, itertools, json, re, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r5_phase0", HERE / "phase0.py"); P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)  # type: ignore
J = lambda n: json.loads((HERE / n).read_text(encoding="utf-8"))


class Freeze(unittest.TestCase):
    def test_verify_clean(self):
        self.assertEqual(P.verify(), [])

    def test_state_locked_and_no_outcome(self):
        fz = J("FREEZE.json")
        self.assertEqual((fz["state"], fz["eval_state"], fz["implementation_state"]), ("R5_ETS_PHASE0_FROZEN", "LOCKED", "NOT_STARTED"))
        self.assertFalse(fz["outcome_metrics_present"]); self.assertFalse(fz["runtime_launched"])
        self.assertEqual(P.outcome_files(), [])

    def test_digest_detects_tampering(self):
        files = dict(J("FREEZE.json")["files_lf_sha256"])
        d0 = P.contract_digest(files)
        files["DECISION_RULES.json"] = "0" * 64
        self.assertNotEqual(d0, P.contract_digest(files))

    def test_every_contract_file_hashed(self):
        self.assertEqual(sorted(J("FREEZE.json")["files_lf_sha256"]), sorted(P.CONTRACT_JSON + P.CONTRACT_MD))


class CausalNotPredictive(unittest.TestCase):
    def setUp(self): self.c = J("COMPARATORS.json")

    def test_scheduler_has_no_prediction_and_no_parameter(self):
        cdef = self.c["arms"]["C"]
        self.assertEqual(cdef["parameters"], "none")
        self.assertNotRegex(cdef["definition"] + cdef["uses_only"], re.compile(r"predict|lookahead|next layer's experts|experts of the next layer|future|oracle", re.I))
        self.assertEqual(self.c["common_environment"]["ARGINE_PREFETCH"], "0")
        self.assertTrue(any("predict" in x for x in self.c["excluded"]))
        self.assertFalse(self.c["stronger_causal_scheduler_D"]["included"])
        self.assertFalse(self.c["oracle_E"]["implementable"])

    def test_timing_arms_untraced_and_traced_arms_never_timed(self):
        arms = self.c["arms"]
        for a in ("A", "C"):
            self.assertEqual(arms[a]["ARGINE_RESEARCH_TRACE"], "<unset>"); self.assertIn("timing", arms[a]["used_in"])
        for a in ("B", "C0_I", "C_I"):
            self.assertEqual(arms[a]["ARGINE_RESEARCH_TRACE"], "1"); self.assertNotIn("timing", arms[a]["used_in"])
        self.assertEqual({arms[a]["ARGINE_EXPERT_WAIT"] for a in arms}, {"layer", "layer_touch", "kind"})

    def test_audit_anchors_and_verdict(self):
        self.assertEqual(P.anchors_present(), [])
        au = J("RUNTIME_AUDIT.json")
        self.assertEqual(au["verdict"], "FEASIBLE_AFTER_PREREQUISITE_IMPLEMENTATION")
        self.assertEqual(len(au["findings"]), 12)


class Workloads(unittest.TestCase):
    def setUp(self): self.w = J("WORKLOADS.json")

    def test_dev_eval_disjoint_and_hashed(self):
        dev = {p["id"]: p["prompt_object_sha256"] for p in self.w["development"]["prompts"]}
        ev = {p["id"]: p["prompt_object_sha256"] for p in self.w["evaluation"]["prompts"]}
        self.assertEqual(len(dev), 4); self.assertEqual(len(ev), 4)
        self.assertFalse(set(dev) & set(ev)); self.assertFalse(set(dev.values()) & set(ev.values()))
        self.assertTrue(all(i.endswith("-dev-1") for i in dev)); self.assertTrue(all(i.endswith("-eval-1") for i in ev))

    def test_prompt_hashes_match_corpus(self):
        for split, key in (("dev", "development"), ("eval", "evaluation")):
            for p in self.w[key]["prompts"]:
                self.assertEqual(P.prompt_sha(p["category"], p["id"], split), p["prompt_object_sha256"])

    def test_eval_prompts_not_referenced_by_any_tracked_result(self):
        for p in self.w["evaluation"]["prompts"]:
            self.assertEqual(p["tracked_references_at_source_head"], [P.WL_FILES[p["category"]]])

    def test_launch_settings(self):
        l = self.w["launch"]
        self.assertEqual((l["n_predict"], l["ignore_eos"], l["temperature"], l["top_k"], l["threads"]), (512, True, 0, 1, 12))
        self.assertEqual((self.w["budgets"]["primary"]["bytes"], self.w["budgets"]["secondary"]["bytes"]), (1610612736, 4294967296))


class Schedule(unittest.TestCase):
    def setUp(self): self.s = J("SCHEDULE.json")

    def test_regenerates_identically(self):
        self.assertEqual(self.s, json.loads(P.dumps(P.schedule())))

    def test_counts(self):
        self.assertEqual(self.s["launch_counts"], {"mechanism_1.5GiB": 36, "timing_1.5GiB": 80, "timing_4GiB": 80})

    def test_timing_blocks_palindromic_balanced(self):
        for k in ("timing_1.5GiB", "timing_4GiB"):
            bl = self.s["campaigns"][k]["blocks"]
            for b in bl:
                o = b["order"]; self.assertEqual(o, o[::-1]); self.assertEqual(sorted(o), ["A", "A", "C", "C"])
            self.assertEqual(collections.Counter(b["order"][0] for b in bl), {"A": 10, "C": 10})
            self.assertEqual(set(collections.Counter(b["prompt"] for b in bl).values()), {5})

    def test_mechanism_blocks_balanced(self):
        bl = self.s["campaigns"]["mechanism_1.5GiB"]["blocks"]
        c = collections.Counter(tuple(b["order"]) for b in bl)
        self.assertEqual(set(c), set(itertools.permutations(["B", "C0_I", "C_I"]))); self.assertEqual(set(c.values()), {2})
        self.assertEqual(set(collections.Counter(b["prompt"] for b in bl).values()), {3})

    def test_only_evaluation_prompts_in_phase2(self):
        ev = {p["id"] for p in J("WORKLOADS.json")["evaluation"]["prompts"]}
        for c in self.s["campaigns"].values():
            self.assertTrue({b["prompt"] for b in c["blocks"]} <= ev)


class DecisionRules(unittest.TestCase):
    def setUp(self): self.d = J("DECISION_RULES.json")

    def test_thresholds(self):
        self.assertEqual(self.d["thresholds"], {"minimum_effect_of_interest": 0.02, "headroom_gate": 0.04, "no_harm_4GiB": 0.02, "scheduling_purity": 0.01,
                                                "hidden_deferred_bytes_floor": 0.25, "mechanism_attribution_share": 0.5})

    def test_required_distinctions_and_single_positive(self):
        labels = [r[1] for r in self.d["classification_order"]]
        for need in ("R5_POSITIVE_RUNTIME_IMPROVEMENT", "R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN", "R5_NO_MEANINGFUL_OVERLAP_HEADROOM", "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED"):
            self.assertIn(need, labels)
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual([l for l in labels if "POSITIVE" in l], ["R5_POSITIVE_RUNTIME_IMPROVEMENT"])

    def test_positive_requires_all_gates_and_precedence(self):
        rows = dict((v, k) for k, v in self.d["classification_order"])
        self.assertEqual(rows["R5_POSITIVE_RUNTIME_IMPROVEMENT"], "G3 and G4 and G5 and G6")
        labels = [r[1] for r in self.d["classification_order"]]
        for blocker in ("R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED", "R5_NO_MEANINGFUL_OVERLAP_HEADROOM", "R5_INVALID", "R5_CONFOUNDED_NOT_PURE_SCHEDULING"):
            self.assertLess(labels.index(blocker), labels.index("R5_POSITIVE_RUNTIME_IMPROVEMENT"))

    def test_mechanism_gate_uses_wait_against_bookkeeping_control(self):
        g4 = self.d["gates"]["G4_mechanism"]
        self.assertIn("exposed_wait_ms_per_token(C_I) - exposed_wait_ms_per_token(C0_I)", g4); self.assertIn(">= 0.25", g4)

    def test_one_shot_headroom_terminal_invalid_no_rescue(self):
        self.assertIn("one-shot", self.d["gates"]["G2_headroom"]); self.assertIn("not recomputed", self.d["gates"]["G2_headroom"])
        self.assertIn("final", self.d["blame_rules"]["terminal"]); self.assertIn("never rerun", self.d["blame_rules"]["reruns"])
        self.assertTrue(any("partial success" in x for x in self.d["anti_rescue"]))
        self.assertIn("R5_POSITIVE_RUNTIME_IMPROVEMENT", self.d["hopi_gate"])

    def test_primary_endpoint(self):
        e = J("ENDPOINTS.json")["primary"]
        self.assertEqual(e["name"], "decode_wall_ms_per_token"); self.assertEqual(e["arms"], "C vs A (both untraced)"); self.assertIn("df = 19", e["interval"])


class Provenance(unittest.TestCase):
    def test_values_read_from_frozen_artifacts(self):
        v = {x["id"]: x["value"] for x in J("PROVENANCE.json")["values"]}
        self.assertEqual(v["r4_classification"], "R4_CFDC_CAUSAL_STATE_INSUFFICIENT")
        self.assertEqual(v["r3_classification"], "R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME")
        self.assertEqual(v["r2_12_classification"], "R2_12_FAIL")
        cl = json.loads((P.REPO / "experiments/R3-04-closure/CLOSURE.json").read_text())
        self.assertEqual(v["r3_04_1p5_block_sd_ms"], cl["primary_1.5GiB"]["P_minus_A_ms_per_token"]["sd"])

    def test_research_1_to_4_unchanged(self):
        self.assertEqual(P.frozen_unchanged(), [])


if __name__ == "__main__":
    unittest.main()
