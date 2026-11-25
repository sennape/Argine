"""Tests of the llama-research-v5 recovery identity rule in host_conformance.py (no runtime is launched).

The driver accepts the G1 baseline-fidelity comparator only as (a) the historical raw SHA-256 of llama-research-v5 or
(b) the ONE reconstruction frozen in V5-RECOVERY.json. These tests use small synthetic PE32+ images for the rule itself
and check the real manifest against the identity pinned in the driver.
"""
from __future__ import annotations

import copy, hashlib, importlib.util, json, struct, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host_conformance", HERE / "host_conformance.py")
H = importlib.util.module_from_spec(spec); sys.modules["host_conformance"] = H; spec.loader.exec_module(H)  # type: ignore
MANIFEST = json.loads(H.V5_RECOVERY.read_text(encoding="utf-8"))
PE = 0x80
TDS, CKS = PE + 8, PE + 24 + 64        # COFF TimeDateStamp, PE32+ optional-header CheckSum


def synthetic_pe(seed: int = 1, size: int = 4096) -> bytearray:
    b = bytearray(hashlib.sha256(str(seed).encode()).digest() * (size // 32))
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, PE)
    b[PE:PE + 4] = b"PE\0\0"
    struct.pack_into("<H", b, PE + 24, 0x20B)
    struct.pack_into("<I", b, TDS, 1795470517)
    struct.pack_into("<I", b, CKS, 19816510)
    return b


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        p = self.d / name; p.write_bytes(bytes(data)); return p

    def accepted_setup(self):
        """A synthetic binary plus a manifest and expected identity that accept exactly it (same rule as the real pair)."""
        exe = self.write("llama-server.exe", synthetic_pe())
        exp = dict(H.V5_RECONSTRUCTED, reconstructed_raw_sha256=H.sha256_file(exe), reconstructed_pe_normalized_sha256=H.pe_normalized_sha256(exe))
        m = copy.deepcopy(MANIFEST)
        m["accepted_reconstruction"].update({k: exp[k] for k in ("reconstructed_raw_sha256", "reconstructed_pe_normalized_sha256")})
        man = self.d / "V5-RECOVERY.json"; man.write_text(json.dumps(m), encoding="utf-8")
        return exe, man, exp, m


class RealManifest(unittest.TestCase):
    def test_manifest_matches_the_pinned_identity(self):
        acc, bc = MANIFEST["accepted_reconstruction"], MANIFEST["build_contract"]
        self.assertEqual(MANIFEST["verdict"], "R5_V5_DETERMINISTIC_RECONSTRUCTION_ACCEPTED")
        self.assertEqual(MANIFEST["review"]["conclusion"], "ACCEPT")
        self.assertEqual(MANIFEST["historical"]["historical_raw_sha256"], H.V5_SHA256)
        self.assertEqual(acc["reconstructed_raw_sha256"], H.V5_RECONSTRUCTED["reconstructed_raw_sha256"])
        self.assertEqual(acc["reconstructed_pe_normalized_sha256"], H.V5_RECONSTRUCTED["reconstructed_pe_normalized_sha256"])
        self.assertEqual(MANIFEST["build_contract_sha256"], H.contract_sha256(bc))
        self.assertEqual(MANIFEST["build_contract_sha256"], H.V5_RECONSTRUCTED["build_contract_sha256"])
        for k in ("source_commit", "source_patches_tree", "llama_cpp_commit", "compiler"):
            self.assertEqual(bc[k], H.V5_RECONSTRUCTED[k])

    def test_historical_sha_is_kept_distinct(self):
        self.assertEqual(H.V5_SHA256, "65f445c42ac145765735984582691964181dd4dd8019f4519edac1fa315df26c")
        self.assertNotEqual(H.V5_SHA256, H.V5_RECONSTRUCTED["reconstructed_raw_sha256"])
        self.assertFalse(MANIFEST["historical_exact_hash_comparison"]["matches"])

    def test_clean_builds_differ_only_in_pe_metadata(self):
        a = MANIFEST["binary_difference_analysis"]
        self.assertFalse(a["substantive_bytes_differ"])
        for name, v in a["per_binary"].items():
            self.assertTrue(v["only_pe_metadata"], name)
            self.assertEqual(sorted(set(v["fields"].values())), ["CheckSum", "TimeDateStamp"], name)
        b1, b2 = MANIFEST["clean_builds"]["build1"], MANIFEST["clean_builds"]["build2"]
        for name in b1:
            self.assertEqual(b1[name]["pe_normalized_sha256"], b2[name]["pe_normalized_sha256"], name)
            self.assertNotEqual(b1[name]["raw_sha256"], b2[name]["raw_sha256"], name)
            self.assertEqual(b1[name]["size"], b2[name]["size"], name)
            self.assertEqual(b1[name]["dll_imports"], b2[name]["dll_imports"], name)
        self.assertEqual(b1["llama-server.exe"]["raw_sha256"], H.V5_RECONSTRUCTED["reconstructed_raw_sha256"])

    def test_real_reconstructed_binary_if_present(self):
        p = H.REPO / "llama-research-v5-recovered" / "llama-server.exe"
        if not p.is_file():
            self.skipTest("reconstructed v5 binary not placed locally (not in Git)")
        r = H.v5_identity(p)
        self.assertTrue(r["accepted"], r); self.assertEqual(r["kind"], "reconstructed")


class IdentityRule(Base):
    def test_historical_sha_still_accepted(self):
        exe = self.write("llama-server.exe", synthetic_pe(7))
        with mock.patch.object(H, "sha256_file", side_effect=lambda p: H.V5_SHA256):
            r = H.v5_identity(exe, manifest=self.d / "absent.json")
        self.assertTrue(r["accepted"]); self.assertEqual(r["kind"], "historical")

    def test_the_one_reconstructed_identity_is_accepted(self):
        exe, man, exp, _ = self.accepted_setup()
        r = H.v5_identity(exe, manifest=man, expected=exp)
        self.assertTrue(r["accepted"], r); self.assertEqual(r["kind"], "reconstructed")
        self.assertTrue(all(r["checks"].values()))

    def test_other_binary_rejected(self):
        _, man, exp, _ = self.accepted_setup()
        other = self.write("other.exe", synthetic_pe(2))
        r = H.v5_identity(other, manifest=man, expected=exp)
        self.assertFalse(r["accepted"])
        self.assertFalse(H.v5_identity(other)["accepted"])        # and against the real pinned identity

    def test_substantive_byte_change_rejected(self):
        exe, man, exp, _ = self.accepted_setup()
        for off in (0x40, PE + 4, PE + 24 + 2, CKS + 4, 2048, 4095):
            b = bytearray(exe.read_bytes()); b[off] ^= 0x01
            p = self.write(f"mut{off}.exe", b)
            self.assertFalse(H.v5_identity(p, manifest=man, expected=exp)["accepted"], off)
            self.assertNotEqual(H.pe_normalized_sha256(p), exp["reconstructed_pe_normalized_sha256"], off)

    def test_timestamp_only_rebuild_rejected(self):
        """Another clean rebuild has the same normalized hash but a different raw hash: it is not the frozen identity."""
        exe, man, exp, _ = self.accepted_setup()
        b = bytearray(exe.read_bytes()); struct.pack_into("<I", b, TDS, 1795470915); struct.pack_into("<I", b, CKS, 19816908)
        p = self.write("rebuild.exe", b)
        self.assertEqual(H.pe_normalized_sha256(p), exp["reconstructed_pe_normalized_sha256"])
        r = H.v5_identity(p, manifest=man, expected=exp)
        self.assertFalse(r["accepted"])

    def test_normalization_only_masks_the_two_approved_fields(self):
        base = synthetic_pe(3)
        ref = H.pe_normalized_sha256(self.write("ref.exe", base))
        approved = set(range(TDS, TDS + 4)) | set(range(CKS, CKS + 4))
        for off in range(0, 512):
            b = bytearray(base); b[off] ^= 0xFF
            if b[0x3C:0x40] != base[0x3C:0x40] or b[PE:PE + 4] != base[PE:PE + 4]:
                continue          # breaks the PE header itself: the normalizer refuses (asserts), checked below
            n = H.pe_normalized_sha256(self.write("f.exe", b))
            if off in approved:
                self.assertEqual(n, ref, off)
            else:
                self.assertNotEqual(n, ref, off)
        b = bytearray(base); b[PE] = 0x00
        with self.assertRaises(AssertionError):
            H.pe_normalized_sha256(self.write("bad.exe", b))

    def test_manifest_disagreement_rejected(self):
        exe, man, exp, m = self.accepted_setup()
        cases = {
            "verdict": lambda d: d.update(verdict="R5_V5_REFERENCE_BLOCKED"),
            "review": lambda d: d["review"].update(conclusion="REJECT"),
            "historical": lambda d: d["historical"].update(historical_raw_sha256="0" * 64),
            "raw": lambda d: d["accepted_reconstruction"].update(reconstructed_raw_sha256="1" * 64),
            "norm": lambda d: d["accepted_reconstruction"].update(reconstructed_pe_normalized_sha256="2" * 64),
            "source": lambda d: d["build_contract"].update(source_commit="0" * 40),
            "tree": lambda d: d["build_contract"].update(source_patches_tree="0" * 40),
            "llama": lambda d: d["build_contract"].update(llama_cpp_commit="0" * 40),
            "compiler": lambda d: d["build_contract"].update(compiler="x86_64-w64-mingw32-g++-posix (GCC) 13-posix"),
            "flags": lambda d: d["build_contract"]["cmake_flags"].append("-DGGML_OPENMP=ON"),
            "contract_digest": lambda d: d.update(build_contract_sha256="3" * 64),
        }
        for name, mut in cases.items():
            d = copy.deepcopy(m); mut(d)
            p = self.d / f"m-{name}.json"; p.write_text(json.dumps(d), encoding="utf-8")
            self.assertFalse(H.v5_identity(exe, manifest=p, expected=exp)["accepted"], name)
        self.assertFalse(H.v5_identity(exe, manifest=self.d / "missing.json", expected=exp)["accepted"])

    def test_manifest_cannot_admit_a_new_identity(self):
        """Editing the manifest to another binary does not help: the driver's pinned identity must agree too."""
        other = self.write("other.exe", synthetic_pe(9))
        d = copy.deepcopy(MANIFEST)
        d["accepted_reconstruction"].update(reconstructed_raw_sha256=H.sha256_file(other), reconstructed_pe_normalized_sha256=H.pe_normalized_sha256(other))
        p = self.d / "edited.json"; p.write_text(json.dumps(d), encoding="utf-8")
        self.assertFalse(H.v5_identity(other, manifest=p)["accepted"])

    def test_locate_v5_ignores_unaccepted_binaries(self):
        (self.d / "a").mkdir(); self.write("a/llama-server.exe", synthetic_pe(4))
        self.assertIsNone(H.locate_v5(self.d))


class Records(Base):
    def test_evaluate_records_the_v5_reference(self):
        ident = {"v5_identity": {"accepted": True, "kind": "reconstructed", "raw_sha256": "r", "pe_normalized_sha256": "n"}}
        (self.d / "IDENTITY.json").write_text(json.dumps(ident), encoding="utf-8")
        H.main(["evaluate", "--out", str(self.d)])
        res = json.loads((self.d / "G1_HOST_RESULT.json").read_text(encoding="utf-8"))
        self.assertEqual(res["v5_reference"], {"accepted": True, "kind": "reconstructed", "raw_sha256": "r", "pe_normalized_sha256": "n",
                                               "historical_raw_sha256": H.V5_SHA256})


if __name__ == "__main__":
    unittest.main()
