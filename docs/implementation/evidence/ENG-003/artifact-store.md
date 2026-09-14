# ENG-003 local artifact store

Date: 2026-09-14  
Scope: local filesystem candidate artifacts only. No hosted bucket, API, task admission, benchmark execution, or score is implemented here.

## Layout

The filesystem implementation is rooted at a caller-selected directory:

```text
<root>/
  blobs/sha256/<first-two-hex>/<full-sha256>  immutable content-addressed bytes
  references/<uuid>.json                      access-controlled blob references
  tmp/                                         unpublished atomic-write staging
```

Blobs deduplicate only bytes. Access is granted through an `ArtifactReference`, which records visibility and an access scope. A restricted reference requires its matching scope; a public reference is an explicitly separate export reference. Reading a digest directly is not part of the store interface, so identical private/public bytes do not imply permission to discover or read another artifact.

## Collection and replay invariants

- Collection walks the frozen source and contestant workspace with filesystem metadata; it never runs candidate code and does not use `git diff`.
- Only changed paths allowed by the existing `SubmissionPolicy` are collected. New/untracked, modified, and deleted files are represented in the existing `CandidateManifest`.
- Any protected or non-allowed change rejects the whole collection. It is never silently omitted.
- Absolute/traversal paths, symlinks, hardlinks, devices, sockets, and non-regular workspace entries reject collection. Tar extraction separately rejects traversal, links, non-regular members, malformed archives, excess file counts, and excess declared expanded bytes.
- Content reads verify length and SHA-256. Interrupted writes stay in `tmp/` and are never published as blobs. Cleanup deletes only explicitly nominated blobs after checking every persisted reference.
- Reconstruction copies the frozen source without executing it, applies verified manifest bytes into an empty destination, and verifies the final full-tree hash.

## Evidence and commands

`tests/test_candidate_artifacts.py` covers EX-01 additions/deletions/replay (including a Unicode path), traversal/link escape rejection, protected edits, hardlinks, malformed/oversized archives, byte/file limits, missing/corrupted blobs, interrupted writes, reference-scoped reads, deduplication, and safe referenced cleanup.

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\python.exe -m unittest tests.test_candidate_artifacts -v
uv lock --check
./dev.ps1 check
```

Final result: all 22 repository tests passed, with the separate optional Docker compatibility test skipped by default.
