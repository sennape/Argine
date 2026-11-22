"""Build CLOSURE.json (hashes of preserved frozen artifacts; no Session 1 content)."""
import hashlib, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
EXP = HERE.parent
PRESERVED = [
 "R3-02-phase2-mechanism/CLASSIFICATION.json","R3-02-phase2-mechanism/ANALYSIS.json","R3-02-phase2-mechanism/FREEZE.json",
 "R3-02G-precision-extension/PREREGISTRATION.json","R3-02G-precision-extension/FEASIBILITY.json",
 "R3-02G-precision-extension/PRECISION_SCALING.json","R3-02G-precision-extension/FREEZE.json",
 "R3-02H-multisession/GLOBAL_SCHEDULE.json","R3-02H-multisession/EXECUTION_CONTRACT.json",
 "R3-02H-multisession/SESSION_PLAN.json","R3-02H-multisession/FREEZE.json",
]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
seal = json.loads((HERE/"SESSION1_SEAL_MANIFEST.json").read_text())
c = {
 "artifact": "R3-02G-CLOSURE",
 "r3_02g_status": "R3_02G_TERMINATED_INCOMPLETE",
 "reason": "DESIGN_SUPERSEDED_BEFORE_UNBLINDING",
 "session1_status": "PROTOCOL_DEVIATION_NONCONFIRMATORY_SEALED",
 "statement": "This is a design supersession, NOT a failed scientific hypothesis.",
 "distinction": {
  "protocol_failure": "Session 1 ran without the 15 s/60 s cooldowns required by R3-02H; it is therefore a protocol deviation and nonconfirmatory.",
  "scientific_negative_result": "NONE. No R3-02G confirmatory result exists; none was computed or inspected.",
  "voluntary_design_supersession": "Independent review (STOP_R3_02G_AND_REDESIGN) and the R3 event-granularity audit (R2_R3_EVENT_SEMANTICS_NOT_EQUIVALENT) superseded the design before unblinding."},
 "cooldown_deviation_root_cause": "tools/r3_02g_session.py (R3-02H execution adapter) contains no cooldown/sleep logic, whereas tools/r3_02_acquire.py honours launch['cooldown_before_s'] (sleep, l.1179).",
 "session1_seal": {"files": seal["summary"]["files"], "root_sha256": seal["summary"]["root_sha256"],
   "manifest": "SESSION1_SEAL_MANIFEST.json", "content_hashes_only": True,
   "unblinded": False, "ab_result_computed": False},
 "successor_policy": "No R3-02G result (Session 1 included) is used by any successor experiment. Session 1 data stay sealed and may only be examined by a future explicitly approved exploratory analysis, never as confirmatory.",
 "r3_02_classification_unchanged": "MECHANISM_INSUFFICIENT",
 "preserved_artifact_sha256": {p: sha(EXP/p) for p in PRESERVED},
}
(HERE/"CLOSURE.json").write_text(json.dumps(c, indent=1, sort_keys=True)+"\n")
