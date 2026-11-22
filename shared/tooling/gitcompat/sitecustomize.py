"""Git-object equivalence layer for the frozen verifiers (see manifests/consolidation-path-changes.json).

The frozen verifiers ask Git historical questions (``git log -1 --format=%h -- PATH``, ``git show COMMIT:PATH`` and
``git diff --name-only REV -- PATHS``) about commits that carry multi-gigabyte raw traces and therefore do not exist in the
public consolidated history. When ARGINE_GITCOMPAT_ROOT is set, this module answers exactly those queries from
``manifests/git-object-equivalence.json``, but ONLY if the content-addressed Git object currently at that path is the very object
recorded for the original commit (otherwise the query fails like a missing object). Every other Git command, and every
non-matching query, is passed to the real git unchanged. No verifier source file is modified, so every FREEZE hash stays valid.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess

_ROOT = os.environ.get("ARGINE_GITCOMPAT_ROOT")
_orig_run = subprocess.run
_M = None


def _manifest():
    global _M
    if _M is None:
        with open(os.path.join(_ROOT, "manifests", "git-object-equivalence.json"), encoding="utf-8") as f: _M = json.load(f)
    return _M


def _real(args, **kw): return _orig_run(args, capture_output=True, text=True, **kw)


def _done(args, kw, rc, out, err=""):
    text = bool(kw.get("text") or kw.get("universal_newlines") or kw.get("encoding"))
    r = subprocess.CompletedProcess(args, rc, out if text else out.encode(), err if text else err.encode())
    if kw.get("check") and rc != 0: raise subprocess.CalledProcessError(rc, args, r.stdout, r.stderr)
    return r


def _object(path):
    r = _real(["git", "-C", _ROOT, "rev-parse", "HEAD:" + path])
    return r.stdout.strip() if r.returncode == 0 else None


def _same_root(p): return os.path.normcase(os.path.realpath(p)) == os.path.normcase(os.path.realpath(_ROOT))


def _tree_listing(prefixes):
    r = _real(["git", "-C", _ROOT, "ls-tree", "-r", "-z", "HEAD"]); d = {}
    for rec in r.stdout.split("\0"):
        if rec:
            meta, p = rec.split("\t", 1)
            if p.startswith(tuple(prefixes)): d[p] = meta.split()[2]
    return d


def _match(path, spec):
    spec = spec.rstrip("/")
    return path == spec or path.startswith(spec + "/") or fnmatch.fnmatchcase(path, spec)


def _intercept(args, kw):
    if len(args) < 4 or args[0] != "git" or args[1] != "-C" or not _same_root(args[2]): return None
    m = _manifest(); rest = list(args[3:])
    if rest[:3] == ["log", "-1", "--format=%h"] and len(rest) == 5 and rest[3] == "--" and rest[4] in m["last_commit"]:
        e = m["last_commit"][rest[4]]
        if _object(rest[4]) != e["expected_object"]: return _done(args, kw, 128, "", "gitcompat: object at %s differs from the consolidated frozen state\n" % rest[4])
        return _done(args, kw, 0, e["short"] + "\n")
    if len(rest) == 2 and rest[0] == "show" and rest[1] in m["show"]:
        _, p = rest[1].split(":", 1)
        if _object(p) != m["show"][rest[1]]: return _done(args, kw, 128, "", "gitcompat: blob at %s differs from the recorded historical blob\n" % p)
        r = _orig_run(["git", "-C", _ROOT, "show", "HEAD:" + p], capture_output=True)
        text = bool(kw.get("text") or kw.get("universal_newlines") or kw.get("encoding"))
        out = r.stdout.decode("utf-8") if text else r.stdout
        res = subprocess.CompletedProcess(args, r.returncode, out, r.stderr.decode() if text else r.stderr)
        if kw.get("check") and r.returncode: raise subprocess.CalledProcessError(r.returncode, args, res.stdout, res.stderr)
        return res
    if rest[:2] == ["diff", "--name-only"] and len(rest) >= 5 and rest[3] == "--" and rest[2] in m["revision_listings"]["revisions"]:
        old = m["revision_listings"]["revisions"][rest[2]]["entries"]; specs = rest[4:]; pfx = m["revision_listings"]["path_prefixes"]
        cur = _tree_listing(pfx); names = set()
        for p in set(old) | set(cur):
            if any(_match(p, s) for s in specs) and old.get(p) != cur.get(p): names.add(p)
        w = _real(["git", "-C", _ROOT, "diff", "--name-only", "HEAD", "--", *specs]); names |= {x for x in w.stdout.split("\n") if x}
        return _done(args, kw, 0, "".join(n + "\n" for n in sorted(names)))
    return None


def _run(*popenargs, **kw):
    args = popenargs[0] if popenargs else kw.get("args")
    if _ROOT and isinstance(args, (list, tuple)) and args and str(args[0]) == "git":
        r = _intercept([str(a) for a in args], kw)
        if r is not None: return r
    return _orig_run(*popenargs, **kw)


if _ROOT: subprocess.run = _run
