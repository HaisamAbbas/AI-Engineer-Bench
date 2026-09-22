from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from aieb_core.models import SubmissionPolicy
from aieb_runner.artifacts import FilesystemArtifactStore
from aieb_runner.backends.base import CandidateArtifacts
from aieb_runner.backends.normalization import HarborResultError, normalize_harbor_result

ROOT = Path(__file__).resolve().parents[1]


def _temporary_directory() -> tempfile.TemporaryDirectory[str]:
    root = ROOT / ".cache" / "test-tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class HarborNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        # Some managed Windows runners deny nested create/delete in process-owned
        # directories.  Skip this filesystem integration test there rather than
        # misreporting a Harbor normalization failure.
        probe_root = ROOT / ".cache" / "test-tmp"
        probe: Path | None = None
        try:
            probe_root.mkdir(parents=True, exist_ok=True)
            probe = Path(tempfile.mkdtemp(prefix=f"normalization-probe-{uuid.uuid4().hex}-", dir=probe_root))
            child = probe / "child"
            child.mkdir()
            child.rmdir()
            probe.rmdir()
        except (OSError, PermissionError) as exc:
            self.skipTest(f"filesystem cannot create nested test directories: {exc}")

    def test_candidate_is_collected_from_overlaid_harbor_artifact(self) -> None:
        # `include=("*",)`, not `("**",)`: SubmissionPolicy.validate_paths
        # (aieb_core/models.py) rejects a bare "**" (or a "**/"-prefixed
        # path) as not task-specific - first exercised now that this whole
        # suite runs against real PostgreSQL. "*" matches the single
        # top-level file these fixtures write and is unaffected by that rule.
        with _temporary_directory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            frozen.mkdir()
            (frozen / "service.py").write_text("old\n", encoding="utf-8")
            trial = root / "trial"
            candidate = trial / "artifacts" / "candidate"
            candidate.mkdir(parents=True)
            (candidate / "service.py").write_text("new\n", encoding="utf-8")
            result = trial / "result.json"
            result.write_text(json.dumps({"verifier_result": {"rewards": {"reward": 1}}}), encoding="utf-8")
            artifacts = CandidateArtifacts(trial, result, trial / "artifacts" / "manifest.json", ())
            normalized = normalize_harbor_result(
                artifacts=artifacts,
                frozen_source=frozen,
                submission=SubmissionPolicy(include=("*",), protected=(), max_artifact_bytes=1024),
                base_revision_digest="a" * 64,
                store=FilesystemArtifactStore(root / "store"),
                access_scope="attempt-1",
            )
            self.assertEqual(normalized.reward, 1.0)
            self.assertEqual(normalized.candidate.manifest.files[0].path, "service.py")

    def test_missing_candidate_artifact_fails_closed(self) -> None:
        with _temporary_directory() as tmp:
            root = Path(tmp)
            trial = root / "trial"
            trial.mkdir()
            result = trial / "result.json"
            result.write_text("{}", encoding="utf-8")
            with self.assertRaises(HarborResultError):
                normalize_harbor_result(
                    artifacts=CandidateArtifacts(trial, result, trial / "manifest.json", ()),
                    frozen_source=root,
                    submission=SubmissionPolicy(include=("*",), protected=(), max_artifact_bytes=1024),
                    base_revision_digest="a" * 64,
                    store=FilesystemArtifactStore(root / "store"),
                    access_scope="attempt-1",
                )


if __name__ == "__main__":
    unittest.main()
