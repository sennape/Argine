"""R3-03B instrumented-runtime build verification (offline-testable; used by tools/r3_03b_build.bat).

  --stage pre  --repo <Argine4> --tree <new llama.cpp tree>
      overlay files in the new tree must equal the committed repo patches (CR-normalised); the argine sources that
      R3-03B does not touch must equal the repo patches too (CR-normalised).
  --stage post --new-exe <exe> --r3a-exe <exe> --r3a-sha <hex> --frozen-exe <exe> --frozen-sha <hex> --record <json>
      hashes, size, --version (no shell), proof that the R3-03A and frozen runtimes are unchanged and that the new
      identity differs from both.
Exit 0 = pass, 1 = fail. Never launches a model.
"""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

OVERLAY = ("argine-experts.cpp", "argine-store.cpp", "argine-store.h", "argine-retention-policy.cpp", "argine-retention-policy.h")
UNCHANGED = ("argine-experts.h", "argine-research.cpp", "argine-research.h")
CPU_DIR = Path("ggml") / "src" / "ggml-cpu"
EXPECT_COMMIT = "commit 254b177"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm(b):
    return b.replace(b"\r\n", b"\n")


def stage_pre(repo, tree):
    bad = []
    rows = {}
    for name in OVERLAY + UNCHANGED:
        a, b = Path(tree) / CPU_DIR / name, Path(repo) / "patches" / name
        if not a.is_file() or not b.is_file():
            bad.append("missing: %s" % name); continue
        same = norm(a.read_bytes()) == norm(b.read_bytes())
        rows[name] = {"tree_sha256_crlf_normalised": hashlib.sha256(norm(a.read_bytes())).hexdigest(), "equals_repo": same}
        if not same:
            bad.append("differs from repo patches: %s" % name)
    return {"stage": "pre", "ok": not bad, "failures": bad, "files": rows}


def stage_post(a):
    bad = []
    new, r3a, frozen = Path(a.new_exe), Path(a.r3a_exe), Path(a.frozen_exe)
    rec = {"stage": "post", "new_exe": str(new)}
    if not new.is_file():
        bad.append("new runtime missing")
    else:
        rec["new_sha256"] = sha256(new); rec["new_bytes"] = new.stat().st_size
        try:
            r = subprocess.run([str(new), "--version"], capture_output=True, text=True, timeout=120)
            rec["version_output"] = (r.stdout + r.stderr).strip()
            if EXPECT_COMMIT not in rec["version_output"]:
                bad.append("--version lacks %r" % EXPECT_COMMIT)
        except Exception as exc:  # noqa: BLE001 - recorded, fail closed
            bad.append("--version failed: %s" % exc)
    for label, exe, want in (("r3_03a", r3a, a.r3a_sha), ("frozen", frozen, a.frozen_sha)):
        got = sha256(exe) if exe.is_file() else None
        rec[label + "_sha256_now"] = got
        if got != want:
            bad.append("%s runtime changed or missing (expected %s, got %s)" % (label, want, got))
        if rec.get("new_sha256") and rec["new_sha256"] == want:
            bad.append("new runtime sha256 equals the %s runtime" % label)
    rec["ok"] = not bad; rec["failures"] = bad
    if a.record:
        Path(a.record).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=("pre", "post"))
    ap.add_argument("--repo"); ap.add_argument("--tree")
    ap.add_argument("--new-exe"); ap.add_argument("--r3a-exe"); ap.add_argument("--r3a-sha")
    ap.add_argument("--frozen-exe"); ap.add_argument("--frozen-sha"); ap.add_argument("--record")
    a = ap.parse_args(argv)
    res = stage_pre(a.repo, a.tree) if a.stage == "pre" else stage_post(a)
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
