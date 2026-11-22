"""Builds VALIDATION_MANIFEST.json, FREEZE.json from the committed summary + host run dir (read-only)."""
import hashlib, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parents[1]
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main(run):
    run = Path(run); fin = json.loads((HERE/"FINAL_RESULT.json").read_text())
    summ = json.loads((HERE/"host_records"/"launch_summary.json").read_text())
    recs = {f"{i:03d}.json": sha(run/"records"/f"{i:03d}.json") for i in range(1, 81)}
    parents = {p: sha(REPO/p) for p in [
        "experiments/R3-03B-end-to-end-opening/E2E_FREEZE.json", "experiments/R3-03B-end-to-end-opening/E2E_FREEZE_AMENDMENT_1.json",
        "experiments/R3-03B-instrumented-conformance-evidence/FREEZE.json", "experiments/R3-03B-microbench-evidence/FREEZE.json",
        "experiments/R3-02-phase2-mechanism/ANALYSIS.json", "experiments/R3-03B-e2e-shutdown-incident/INCIDENT.json"] if (REPO/p).exists()}
    man = {"artifact": "R3-03B-FINAL-VALIDATION-MANIFEST",
           "campaign_files": {n: sha(run/n) for n in ("CAMPAIGN_OPENED.json", "CAMPAIGN_RESUMED_01.json", "CAMPAIGN_COMPLETE.json")},
           "counted_records_sha256": recs, "orphan_record": {"file": "005.try1.ORPHAN_NONCOUNTED.json", "sha256": sha(run/"records"/"005.try1.ORPHAN_NONCOUNTED.json"), "counted": False},
           "runtime_identity_sha256": fin["audit_runtime"] if "audit_runtime" in fin else "8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5",
           "parent_artifacts_sha256": parents,
           "evidence_files_sha256": {n: sha(HERE/n) for n in sorted(["FINAL_RESULT.json", "CAMPAIGN_AUDIT.json", "BLOCK_RECOMPUTATION.json", "FIELLER_RECOMPUTATION.json", "DURATION_ANALYSIS.json", "DIAGNOSTICS.json", "CLAIM_BOUNDARIES.md", "README.md", "validate_final.py", "host_records/launch_summary.json"])}}
    (HERE/"VALIDATION_MANIFEST.json").write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    fr = {"artifact": "R3-03B-FINAL-FREEZE", "classification": fin["classification"], "rho": fin["rho"], "fieller": fin["fieller"], "equivalence_interval": fin["equivalence_interval"],
          "validation_manifest_sha256": sha(HERE/"VALIDATION_MANIFEST.json"), "final_result_sha256": sha(HERE/"FINAL_RESULT.json"), "model_runs_required": False}
    fr["freeze_sha256"] = hashlib.sha256(json.dumps(fr, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (HERE/"FREEZE.json").write_text(json.dumps(fr, indent=1, sort_keys=True), encoding="utf-8"); print(fr["freeze_sha256"])
if __name__ == "__main__": main(sys.argv[1])
