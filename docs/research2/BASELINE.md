# Research Part 2 — baseline

- **Frozen Argine2 commit:** `406763361a49a2c7ab6cbb811655dbdc51e77f9b`
  ("Document public snapshot provenance", `master`).
- **No Git tag exists** in Argine2 marking a frozen/release commit.

## Evidence used to identify it

1. `git tag -l` in Argine2 returns nothing: no explicit frozen/release tag
   (evidence tier 1) exists.
2. Argine2's `CITATION.cff` records `version: "0.2.0"` and
   `date-released: "2026-10-17"`. That metadata was added two commits before
   HEAD (`707c088`, "Bind release metadata to public repository") and HEAD
   itself (`4067633`, "Document public snapshot provenance") is dated the
   same day and is the tip of the only branch — i.e. HEAD is the commit the
   repository's own metadata documents as the `0.2.0` release (evidence
   tier 2: an explicit commit documented as the frozen baseline).
3. Argine2's `README.md` Roadmap section states the publication experiment
   campaign is complete, with no `RUNNING` or `PLANNED` experiments in the
   registry — consistent with, though not on its own sufficient for,
   treating this commit as frozen.
4. Argine2 has a single branch (`master`) and no tags, so there is no
   second, equally plausible candidate commit; the ambiguity check in
   R2-M0.1's task did not need to fire.

## Argine2 working-tree state at inspection time

Argine2's HEAD was unchanged throughout (`406763361a49a2c7ab6cbb811655dbdc51e77f9b`)
but its working tree was found to be **dirty**: ~1112 tracked files show as
modified, due to a CRLF/LF line-ending mismatch between the committed blobs
and the on-disk files (e.g. `.bat` files on disk use CRLF while the
committed blob is LF-only). Every affected file's mtime examined predates
this task by weeks (around 2026-10-03 to 2026-10-11, vs. the task running on
2026-11-07), confirming this is pre-existing drift, not something this task
changed. This task never wrote to Argine2: Argine3 was created with `git
clone` of Argine2's `.git` object database only, which is unaffected by
working-tree file content, and no command run against Argine2 in this task
was anything other than a read (`git status/log/show`, `cat`, `grep`,
`find`, `stat`, `du`, `wc`). The frozen baseline is identified from
committed history and tracked metadata (points 1-4 above), neither of which
depends on the working tree's clean/dirty state.
