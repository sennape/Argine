"""Offline tests of the R3-03B instrumented-conformance evidence. No model, no timing."""
import copy, json, shutil, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import validate_evidence as ve  # noqa: E402


class T(unittest.TestCase):
    def test_01_committed_records_pass_every_check(self):
        r = ve.run_checks(HERE / "host_records")
        self.assertEqual([c["check"] for c in r if not c["ok"]], [])
        self.assertGreaterEqual(len(r), 70)

    def test_02_token_digest_matches_frozen_definition(self):
        import hashlib
        self.assertEqual(ve.token_digest([101, 2450, 7]), hashlib.sha256(b"argine-output-tokens-v1\n101\n2450\n7\n").hexdigest())

    def test_03_tampered_record_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "host_records"
            shutil.copytree(HERE / "host_records", d)
            p = d / "A-8GiB-ON.analysis.json"
            a = json.loads(p.read_text()); a["budget_violations"] = 1; p.write_text(json.dumps(a))
            bad = [c["check"] for c in ve.run_checks(d) if not c["ok"]]
            self.assertIn("manifest:A-8GiB-ON.analysis.json", bad)

    def test_04_scan_trace_detects_duplicates_and_budget_and_order(self):
        def row(p, l, b=10, bud=100):
            return ('{"step":0,"policy":"x","layer":%d,"policy_resident_bytes_after":%d,"budget_bytes":%d,"policy_params":{},'
                    '"semantic_event":{"schema":"r3-03a-v1","pass":%d,"layer":%d,"node_kind":0,"first_node":1}}\n' % (l, b, bud, p, l)).encode()
        comp = b'{"step":0,"complete":1,"cache_resident_bytes_post_service":1}\n'
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "g"; good.write_bytes(row(0, 0) + comp + row(0, 1) + comp)
            s = ve.scan_trace(good)
            self.assertEqual((s["unique_events"], s["budget_violations"], s["alternating"]), (2, 0, True))
            dup = Path(d) / "d"; dup.write_bytes(row(0, 0) + comp + row(0, 0) + comp)
            self.assertEqual(ve.scan_trace(dup)["unique_events"], 1)
            ov = Path(d) / "o"; ov.write_bytes(row(0, 0, 200) + comp)
            self.assertEqual(ve.scan_trace(ov)["budget_violations"], 1)
            na = Path(d) / "n"; na.write_bytes(row(0, 0) + row(0, 1) + comp)
            self.assertFalse(ve.scan_trace(na)["alternating"])

    def test_05_verification_record_agrees_with_manifest(self):
        man = json.loads((HERE / "EVIDENCE_MANIFEST.json").read_text())
        iv = json.loads((HERE / "INDEPENDENT_VERIFICATION.json").read_text())
        for t, h in iv["trace_sha256_rehash_on_mounted_files"].items():
            if t != "matches_driver_records":
                self.assertEqual(man["traces_not_committed"]["files"][t + ".trace.jsonl"]["sha256"], h)

    def test_06_prior_frozen_evidence_untouched(self):
        import hashlib
        fz = json.loads((ROOT / "experiments/R3-03B-microbench-evidence/FREEZE.json").read_text())
        self.assertEqual(fz["freeze_sha256"], "abb451010b04782a56c51420485908871e1a77e87dd18b22f75b5e0580dd7009")
        o = copy.deepcopy(fz); o.pop("freeze_sha256")
        self.assertEqual(hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), fz["freeze_sha256"])


if __name__ == "__main__":
    unittest.main()
