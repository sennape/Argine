"""Independent verifier of the R3-04 Gate 1 (model-free shutdown probe) evidence. Reads only PROBE.json in this directory. No model, no network."""
import hashlib, json, statistics as st, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE_SHA256 = "4ed1abb9aaf91cfba4c0dff782c917aa13489166d970717ce4d0712949a880d2"
PROTOCOL_FREEZE_SHA256 = "a88359b52891f93ec49e67978d92c2eb3b73db588611bce46b3103b2978c1202"
PROTOCOL_COMMIT, TOOLING_COMMIT = "4a0aa2d", "6b22fdd"
CLASSES = ("SHUTDOWN_PROBE_SUPPORTS_HELPER", "SHUTDOWN_PROBE_INCONCLUSIVE", "SHUTDOWN_PROBE_REJECTS_HELPER")


def freeze_sha(obj):
    o = {k: v for k, v in obj.items() if k != "freeze_sha256"}
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recompute(probe):
    rows = probe["rows"]; out = {}
    for m in ("legacy", "own_console_helper"):
        r = [x for x in rows if x["method"] == m]
        v = [x["delivery_latency_s"] for x in r]
        out[m] = {"n": len(r), "failed": sum(1 for x in r if "error" in x or x.get("mode") != "graceful"), "median_s": st.median(v), "max_s": max(v), "min_s": min(v), "over_5s": sum(1 for x in v if x > 5.0),
                  "helper_returncodes": sorted({x["helper_returncode"] for x in r if x["helper_returncode"] is not None})}
    L, H = out["legacy"], out["own_console_helper"]
    out["ratios_descriptive"] = {"median_legacy_over_helper": L["median_s"] / H["median_s"], "max_legacy_over_helper": L["max_s"] / H["max_s"]}
    return out


def classify(s):
    """Descriptive, model-free H1 classification. Criteria stated after the host summary was reported; they are not a frozen hypothesis test."""
    L, H = s["legacy"], s["own_console_helper"]
    if H["failed"] > 0 or H["max_s"] > L["max_s"] or H["median_s"] > L["median_s"]:
        return "SHUTDOWN_PROBE_REJECTS_HELPER"
    if L["n"] == H["n"] == 20 and H["over_5s"] == 0 and H["max_s"] < L["median_s"] and L["over_5s"] > 0:
        return "SHUTDOWN_PROBE_SUPPORTS_HELPER"
    return "SHUTDOWN_PROBE_INCONCLUSIVE"


def verify():
    raw = (HERE / "PROBE.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PROBE_SHA256, "probe sha256"
    probe = json.loads(raw)
    assert probe["model_free"] is True and probe["trials"] == 20 and len(probe["rows"]) == 40
    assert all(x["mode"] == "graceful" and "error" not in x for x in probe["rows"])
    s = recompute(probe)
    rep = probe["summary"]
    for m in ("legacy", "own_console_helper"):
        for k in ("n", "failed", "median_s", "max_s", "over_5s"):
            assert rep[m][k] == s[m][k], (m, k)
    assert s["own_console_helper"]["helper_returncodes"] == [0]
    return s, classify(s)


if __name__ == "__main__":
    s, c = verify()
    fz = json.loads((HERE / "FREEZE.json").read_text())
    assert fz["freeze_sha256"] == freeze_sha(fz), "gate1 freeze sha"
    assert fz["classification"] == c and fz["statistics"] == json.loads(json.dumps(s))
    print("GATE1_VERIFIED", c, fz["freeze_sha256"]); sys.exit(0)
