"""Deterministic tests of the locale-independent compiler preflight (no benchmark, no timing, no model)."""
import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_check_cl_version as chk  # noqa: E402

COPY = "\r\nCopyright (C) Microsoft Corporation.  All rights reserved.\r\n"


class ClVersionTests(unittest.TestCase):
    def test_01_english_19_44_passes(self):
        self.assertEqual(chk.check("Microsoft (R) C/C++ Optimizing Compiler Version 19.44.35229 for x64" + COPY)[0], 0)

    def test_02_italian_19_44_passes(self):
        self.assertEqual(chk.check("Microsoft (R) C/C++ Optimizing Compiler versione 19.44.35229 per x64\r\nCopyright (C) Microsoft Corporation. Tutti i diritti riservati.\r\n")[0], 0)

    def test_03_wrong_families_fail_closed(self):
        for v in ("19.43.34808", "19.45.35000", "19.40.33807", "19.29.30159", "20.44.35229"):
            for tmpl in ("Compiler Version %s for x64", "Compiler versione %s per x64"):
                self.assertEqual(chk.check(tmpl % v)[0], 1, v)

    def test_04_missing_or_unparseable_fails_closed(self):
        for t in ("", "Microsoft compiler", "version 19.44 for x64", "19.4.1", "x119.44.35229y1"):
            self.assertEqual(chk.check(t)[0], 2, t)

    def test_05_ambiguous_two_versions_fail_closed(self):
        self.assertEqual(chk.check("Version 19.44.35229 and also 19.43.34808")[0], 2)

    def test_06_cli_exit_codes_and_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cl.txt"
            p.write_bytes("Compiler versione 19.44.35229 per x64\r\n".encode("cp1252"))
            run = lambda: subprocess.run([sys.executable, str(ROOT / "tools/r3_03b_check_cl_version.py"), str(p)], capture_output=True, text=True).returncode
            self.assertEqual(run(), 0)
            p.write_bytes(b"Compiler versione 19.43.34808 per x64\r\n"); self.assertEqual(run(), 1)
            p.write_bytes(b"\xff\xfe garbage"); self.assertEqual(run(), 2)

    def test_07_bat_uses_locale_independent_check_and_run2(self):
        bat = (ROOT / "tools/r3_03b_microbench.bat").read_text()
        self.assertNotIn("findstr", bat)
        self.assertIn("r3_03b_check_cl_version.py", bat)
        self.assertIn("R3-03B-microbench-run2", bat)
        self.assertNotIn("microbench-run1", bat)
        # scientific parameters unchanged
        self.assertIn("%%N 30 4294967296 0.99 64 0.99 8 128", bat)
        self.assertIn("/O2 /Ob2 /DNDEBUG /MT /EHsc /std:c++17 /arch:AVX2 /DNOMINMAX /W4", bat)
        self.assertIn("(1 2 3)", bat)

    def test_08_protocol_digest_unchanged(self):
        d = ROOT / "experiments/R3-03B-direct-cost-attribution"
        fz = json.loads((d / "PROTOCOL_FREEZE.json").read_text())
        self.assertEqual(hashlib.sha256((d / "PROTOCOL.json").read_bytes()).hexdigest(), fz["protocol_json_sha256"])
        self.assertEqual(fz["protocol_json_sha256"], "295e8e3f3e376248998baf3ea5fa845c8f3b793706ed3e1c7aae0d8b463b1ae5")
        self.assertEqual(fz["freeze_sha256"], "d0dd74510918d711c0ae6a8dd35f97c40d8cb3a573ff32f82955ebbe30686882")


if __name__ == "__main__":
    unittest.main()
