"""Offline tests of the Gate 3 tooling with SYNTHETIC attestations only (never written to the real OPENING_FREEZE path). No model, no network."""
import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools")); sys.path.insert(0, str(HERE))
import r3_04_acquire as A  # noqa: E402
import r3_04_host_identity as HI  # noqa: E402
import r3_04_open_gate3 as G  # noqa: E402
import validate_opening as VO  # noqa: E402
V = A.V
PLAN = json.loads(HI.PLAN.read_text())
DRIVER_COMMIT = "a3482a7"


def good_attestation():
    f = {k: {"absolute_path": PLAN[k]["path"], "size_bytes": PLAN[k]["bytes"], "sha256": PLAN[k]["sha256"], "expected_sha256": PLAN[k]["sha256"], "expected_size_bytes": PLAN[k]["bytes"], "sha256_matches": True, "size_matches": True} for k in ("model", "store")}
    f["runtime"] = {"absolute_path": G.RUNTIME_PATH, "size_bytes": 1, "sha256": V.RUNTIME_SHA256, "expected_sha256": V.RUNTIME_SHA256, "expected_size_bytes": None, "sha256_matches": True, "size_matches": True}
    return {"artifact": HI.SCHEMA, "model_free": True, "llama_server_launched": False, "created_unix": 1.0, "files": f, "verdict": "IDENTITY_MATCHES_FROZEN_PLAN"}


class Opening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.t = Path(self.tmp.name); self.addCleanup(self.tmp.cleanup)
        self.p = mock.patch.object(A, "OPENING_FREEZE", self.t / "OPENING_FREEZE.json"); self.p.start(); self.addCleanup(self.p.stop)

    def test_01_host_identity_hashes_and_detects_mismatch(self):
        import hashlib
        m, s, r = (self.t / n for n in ("m.gguf", "s.psxs", "r.exe")); m.write_bytes(b"model"); s.write_bytes(b"store"); r.write_bytes(b"rt")
        plan = {"model": {"path": str(m), "bytes": 5, "sha256": hashlib.sha256(b"model").hexdigest()}, "store": {"path": str(s), "bytes": 5, "sha256": hashlib.sha256(b"store").hexdigest()}}
        with mock.patch.object(HI, "RUNTIME_SHA256", hashlib.sha256(b"rt").hexdigest()):
            att = HI.attest(m, s, r, plan)
        self.assertEqual(att["verdict"], "IDENTITY_MATCHES_FROZEN_PLAN"); self.assertTrue(att["model_free"]); self.assertFalse(att["llama_server_launched"])
        plan["store"]["sha256"] = "0" * 64
        with mock.patch.object(HI, "RUNTIME_SHA256", hashlib.sha256(b"rt").hexdigest()):
            self.assertEqual(HI.attest(m, s, r, plan)["verdict"], "IDENTITY_MISMATCH")
        with self.assertRaises(FileNotFoundError): HI.attest(self.t / "none", s, r, plan)

    def test_02_host_identity_never_launches_or_overwrites(self):
        src = (REPO / "tools" / "r3_04_host_identity.py").read_text()
        for bad in ("import subprocess", "import socket", "urllib", "shell=True", "os.system", "Popen"): self.assertNotIn(bad, src)
        out = self.t / "o.json"; out.write_text("{}")
        self.assertEqual(HI.main(["--runtime", "x", "--out", str(out)]), 1)

    def test_03_attestation_refusals(self):
        G.check_attestation(good_attestation(), PLAN)
        for mut in (lambda a: a.update(model_free=False), lambda a: a.update(llama_server_launched=True), lambda a: a.update(verdict="IDENTITY_MISMATCH"),
                    lambda a: a["files"]["model"].update(sha256="0" * 64), lambda a: a["files"]["store"].update(size_bytes=1), lambda a: a["files"]["runtime"].update(sha256="1" * 64),
                    lambda a: a["files"]["model"].update(absolute_path="D:\\other.gguf"), lambda a: a["files"]["store"].update(sha256_matches=False)):
            a = good_attestation(); mut(a)
            with self.assertRaises(SystemExit): G.check_attestation(a, PLAN)

    def test_04_build_binds_everything_and_validates(self):
        of = G.build(good_attestation(), DRIVER_COMMIT)
        self.assertEqual((of["physical_launches"], of["counted_launches"], of["warmups"]), (146, 144, 2)); self.assertEqual(of["blocks"], {"1.5": 18, "8": 6})
        self.assertEqual(of["protocol_freeze_sha256"], G.PROTOCOL_FREEZE_SHA256); self.assertEqual(of["amendment_freeze_sha256"], G.AMENDMENT_FREEZE_SHA256); self.assertEqual(of["gate2_freeze_sha256"], G.GATE2_FREEZE_SHA256)
        self.assertEqual(of["driver_sha256_lf"], V.sha_lf(G.DRIVER)); self.assertEqual(of["runtime_sha256"], V.RUNTIME_SHA256)
        self.assertFalse(of["campaign_authorized"]); self.assertEqual(of["shutdown_method_required"], "own_console_helper"); self.assertEqual(of["confidence_level"], 0.95)
        (self.t / "OPENING_FREEZE.json").write_text(json.dumps(of)); self.assertTrue(VO.validate()["ok"])
        self.assertEqual(A.load_opening_freeze(of["freeze_sha256"], of["protocol_freeze_sha256"], G.DRIVER)["amendment_freeze_sha256"], G.AMENDMENT_FREEZE_SHA256)      # driver accepts it
        self.assertFalse((self.t / "timing-output").exists())                                                                                                      # no campaign state created

    def test_05_validator_detects_tampering(self):
        of = G.build(good_attestation(), DRIVER_COMMIT)
        for k, v in (("driver_sha256_lf", "0" * 64), ("physical_launches", 183), ("amended_schedule_sha256", "1" * 64), ("campaign_authorized", True)):
            o = copy.deepcopy(of); o[k] = v; o["freeze_sha256"] = V.freeze_sha(o); (self.t / "OPENING_FREEZE.json").write_text(json.dumps(o))
            with self.assertRaises((AssertionError, SystemExit)): VO.validate()

    def test_06_refuses_wrong_driver_commit_existing_file_bad_attestation(self):
        with self.assertRaises(SystemExit): G.build(good_attestation(), "ec54b83")                       # not the last commit of the driver
        a = good_attestation(); a["files"]["model"]["sha256"] = "0" * 64
        with self.assertRaises(SystemExit): G.build(a, DRIVER_COMMIT)
        (self.t / "OPENING_FREEZE.json").write_text("{}")
        with self.assertRaises(SystemExit): G.build(good_attestation(), DRIVER_COMMIT)                    # never overwrites

    def test_07_real_opening_freeze_if_present_validates_and_is_not_authorization(self):
        self.p.stop(); of = REPO / "experiments" / "R3-04-net-runtime-value" / "OPENING_FREEZE.json"
        if of.exists():
            self.assertTrue(VO.validate(of)["ok"]); self.assertFalse(json.loads(of.read_text())["campaign_authorized"])
        self.p.start()


if __name__ == "__main__":
    unittest.main()
