"""Research 5 Phase 1a tests: implementation record, gate, frozen-state checks and (when g++ exists) the model-free
C++ conformance test of the exact scheduler code. No model is loaded and no evaluation prompt is touched."""
from __future__ import annotations

import importlib.util, json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r5_phase1a", HERE / "phase1a.py")
P = importlib.util.module_from_spec(spec); sys.modules["r5_phase1a"] = P; spec.loader.exec_module(P)  # type: ignore
J = lambda n: json.loads((HERE / n).read_text(encoding="utf-8"))


class Records(unittest.TestCase):
    def test_verify_clean(self):
        self.assertEqual(P.verify(), [])

    def test_gate_pass_and_host_pending(self):
        g = J("GATE.json")
        self.assertEqual(g["verdict"], "R5_PHASE1A_IMPLEMENTATION_GATE_PASS")
        self.assertTrue(all(g["criteria"].values()))
        self.assertEqual(g["host_G1"]["state"], "PENDING")
        self.assertEqual((g["evaluation"], g["headroom_gate_G2"]), ("LOCKED", "NOT RUN"))

    def test_three_frozen_arms_and_no_prediction(self):
        imp = J("IMPLEMENTATION.json")
        self.assertEqual(set(imp["arms"]), {"layer", "layer_touch", "kind"})
        self.assertIn("no prediction", imp["causal_information"])
        frozen_modes = {a["ARGINE_EXPERT_WAIT"] for a in json.loads((P.P0_DIR / "COMPARATORS.json").read_text(encoding="utf-8"))["arms"].values()}
        self.assertEqual(frozen_modes, set(imp["arms"]))

    def test_r5_diff_regenerates(self):
        self.assertEqual(P.lf((P.R5SRC / "R5_CHANGES.diff").read_bytes()).decode(), P.r5_diff())

    def test_baseline_code_untouched(self):
        """The R5 change is additive: the trace and identity changes remove no line; the hook loses exactly two lines,
        each rewritten to add the R5 branch in front of the unchanged baseline block; the store loses exactly one
        tracing-only line (inside `if (arx)`), rewritten to keep its flag and add ARX_FLAG_SYNC_IN_ISSUE."""
        removed, added = {}, {}
        cur = None
        for l in P.r5_diff().splitlines():
            if l.startswith("--- "):
                cur = l[4:].strip()
            elif l.startswith("+++ "):
                pass
            elif l.startswith("-"):
                removed.setdefault(cur, []).append(l[1:].strip())
            elif l.startswith("+"):
                added.setdefault(cur, []).append(l[1:].strip())
        self.assertEqual(set(removed), {"patches/argine-experts.cpp", "patches/argine-store.cpp"}, removed)
        self.assertEqual(removed["patches/argine-experts.cpp"], ["if (!pol_followup) {", "if (c.modo == LETTURA_ASINCRONA) {"])
        self.assertEqual(removed["patches/argine-store.cpp"], ["so.flags |= ARX_FLAG_COMPLETE_EXACT;"])
        self.assertIn("so.flags |= ARX_FLAG_COMPLETE_EXACT | ARX_FLAG_SYNC_IN_ISSUE;  // R5: paid by the issuer, never \"hidden\"",
                      added["patches/argine-store.cpp"])

    def test_eval_ids_absent(self):
        self.assertFalse([x for x in P.verify() if "evaluation prompt id" in x])


@unittest.skipUnless(shutil.which("g++"), "g++ not available (the Windows host runs argine-ets-test.exe in host_conformance.py)")
class ModelFreeCpp(unittest.TestCase):
    def test_conformance_binary(self):
        src, frozen = P.R5SRC, P.REPO / "patches"
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "etstest"
            cmd = ["g++", "-O1", "-std=c++17", "-pthread", "-I", str(src), "-I", str(frozen), str(src / "argine-store.cpp"), str(src / "argine-research.cpp"),
                   str(frozen / "argine-retention-policy.cpp"), str(src / "argine-ets-test.cpp"), "-o", str(exe)]
            subprocess.run(cmd, check=True, capture_output=True)
            r = subprocess.run([str(exe), d], capture_output=True, text=True, timeout=1200)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertRegex(r.stdout, r"RESULT: \d+ passed, 0 failed")


if __name__ == "__main__":
    unittest.main()
