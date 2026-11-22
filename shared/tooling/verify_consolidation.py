#!/usr/bin/env python3
"""Structure checks for the consolidated repository (also used on a fresh clone).

    python shared/tooling/verify_consolidation.py [--expect-head SHA] [--skip-hashes]

Checks: git status clean, branch main, optional expected HEAD, all legacy tags resolve to the recorded commits,
research/papers/manifests/root documents present, R4 classification unchanged, freeze/digest artifacts present,
raw-omission manifest present, every original file's committed blob SHA-256 equal to manifests/tracked-file-hashes.json."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_TAGS = {
    "legacy/argine2/main": "faa613d18645e58dbb3f5e11cdf4d526bea842aa",
    "legacy/argine2/v0.2.0": "8ed817dc05a7db5a7e7e22138fa4d75bfc6c5e17",
    "legacy/argine2/local-master": "406763361a49a2c7ab6cbb811655dbdc51e77f9b",
    "legacy/argine3/r2-main": "fda402f05876ed557e4282bc2ccfdec3b3a33957",
    "legacy/argine3/research2-paper-final": "ebd8e50bb616bb15ed286a4d8e0408ddc0cfbe70",
    "legacy/argine4/main": "66d2b8ebaa2ad3f26a42dfc0e310857409f99cbd",
    "legacy/argine4/research3-paper-v1": "bcc9bde72ac6a926dbcc21a06f34088c6ec7d1dc",
}
PATHS = [
    "README.md", "REPRODUCING.md", "CITATION.cff", "CONTRIBUTING.md", "research/README.md", "papers/README.md",
    *[f"research/research-{n}/README.md" for n in range(1, 5)], *[f"papers/research-{n}/README.md" for n in range(1, 5)],
    "manifests/legacy-repositories.json", "manifests/research-layout-map.json", "manifests/consolidation-path-changes.json",
    "manifests/raw-artifacts-omitted.json", "manifests/github-consolidation.json", "manifests/git-object-equivalence.json",
    "manifests/tracked-file-hashes.json",
    "experiments/R3-final-closure/FREEZE.json" if (ROOT / "experiments/R3-final-closure/FREEZE.json").exists() else "experiments/R3-final-closure/closure.py",
    "experiments/R4-00-causal-eviction-distillation/FREEZE.json", "experiments/R4-01-cfdc-training-validation/FREEZE.json",
    "experiments/R4-02-cfdc-prospective-eval/FREEZE.json", "paper/research3/research3.pdf", "paper/research2/manuscript.pdf",
]


def git(*a):
    r = subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def main() -> int:
    bad: list[str] = []
    ok = lambda c, m: (print(("ok   " if c else "FAIL ") + m), None if c else bad.append(m))
    rc, out = git("status", "--porcelain"); ok(rc == 0 and not out, "git status clean")
    rc, br = git("rev-parse", "--abbrev-ref", "HEAD"); ok(br == "main", f"branch is main ({br})")
    if "--expect-head" in sys.argv:
        exp = sys.argv[sys.argv.index("--expect-head") + 1]; rc, h = git("rev-parse", "HEAD"); ok(h.startswith(exp), f"HEAD {h[:12]} == expected {exp[:12]}")
    for tag, sha in LEGACY_TAGS.items():
        rc, h = git("rev-parse", f"refs/tags/{tag}^{{commit}}"); ok(h == sha, f"tag {tag} -> {sha[:10]}")
        rc, t = git("cat-file", "-t", f"refs/tags/{tag}"); ok(t == "tag", f"tag {tag} is annotated")
    for p in PATHS: ok((ROOT / p).exists(), f"exists {p}")
    cl = json.loads((ROOT / "experiments/R4-02-cfdc-prospective-eval/FINAL_CLASSIFICATION.json").read_text(encoding="utf-8"))
    ok(cl["classification"] == "R4_CFDC_CAUSAL_STATE_INSUFFICIENT" and cl["r4_cfdc_state"] == "R4_CFDC_CLOSED" and cl["eval_state"] == "CLOSED", "R4 classification unchanged")
    if "--skip-hashes" not in sys.argv:
        hs = json.loads((ROOT / "manifests/tracked-file-hashes.json").read_text(encoding="utf-8"))
        files = hs["sha256"]
        # Files intentionally changed by the consolidation (documented in manifests/consolidation-path-changes.json)
        # plus the one file whose mixed line endings in the original cannot be reproduced by a per-file attribute.
        changed = {"README.md", "CITATION.cff", "CONTRIBUTING.md", "REPRODUCING.md", ".gitattributes", "experiments/R3-02B-acquisition-driver/README.md"}
        # The manifest holds SHA-256 of Git blob content (LF-stored); compare against the committed blobs.
        todo = [p for p in files if p not in changed]
        proc = subprocess.run(["git", "cat-file", "--batch"], cwd=str(ROOT), input="".join(f"HEAD:{p}\n" for p in todo).encode("utf-8"), capture_output=True)
        out, mism, pos = proc.stdout, [], 0
        for p in todo:
            nl = out.index(b"\n", pos); head = out[pos:nl].split(); pos = nl + 1
            if len(head) != 3 or head[1] != b"blob":
                mism.append(p); continue
            size = int(head[2]); blob = out[pos:pos + size]; pos += size + 1
            if hashlib.sha256(blob).hexdigest() != files[p]:
                mism.append(p)
        ok(not mism, f"committed blob SHA-256 equals original ({len(files) - len(changed)} files checked, {len(changed)} documented exceptions; mismatches: {mism[:3]})")
    print("\nFAILED: %d" % len(bad) if bad else "\nALL CONSOLIDATION CHECKS PASSED")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
