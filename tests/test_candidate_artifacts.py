from __future__ import annotations

import io
import os
import shutil
import stat
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aieb_core.models import SubmissionPolicy
from aieb_runner.artifacts import (
    ArtifactAccessDenied,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactValidationError,
    CollectionLimits,
    FilesystemArtifactStore,
    collect_candidate,
    reconstruct_candidate,
    safe_extract_tar,
)


BASE_DIGEST = "a" * 64
TEST_ROOT = Path(__file__).resolve().parents[1] / ".cache" / "eng003-tests"


class CandidateArtifactsTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_ROOT / f"case-{uuid4()}"
        self.root.mkdir()
        self.source = self.root / "source"
        self.workspace = self.root / "workspace"
        self.source.mkdir()
        self.workspace.mkdir()
        for root in (self.source, self.workspace):
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("original\n", encoding="utf-8")
            (root / "remove.txt").write_text("remove me\n", encoding="utf-8")
            (root / "dev_tests").mkdir()
            (root / "dev_tests" / "protected.py").write_text("trusted\n", encoding="utf-8")
        self.store = FilesystemArtifactStore(self.root / "store")
        self.policy = SubmissionPolicy(include=("src/**", "*.txt"), protected=("dev_tests/**",), max_artifact_bytes=1024)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def collect(self, **kwargs: object):
        return collect_candidate(frozen_source=self.source, workspace=self.workspace, submission=self.policy, base_revision_digest=BASE_DIGEST, store=self.store, access_scope="attempt-1", **kwargs)

    def test_ex01_preserves_add_modify_delete_and_unicode_on_clean_replay(self) -> None:
        (self.workspace / "src" / "app.py").write_text("changed\n", encoding="utf-8")
        (self.workspace / "remove.txt").unlink()
        (self.workspace / "src" / "résumé.txt").write_text("new\n", encoding="utf-8")
        stored = self.collect()
        self.assertEqual([(item.path, item.operation) for item in stored.manifest.files], [("remove.txt", "delete"), ("src/app.py", "modify"), ("src/résumé.txt", "add")])
        replay = self.root / "replay"
        reconstruct_candidate(frozen_source=self.source, destination=replay, stored=stored, store=self.store, principal_scope="attempt-1")
        self.assertEqual((replay / "src" / "app.py").read_text(encoding="utf-8"), "changed\n")
        self.assertEqual((replay / "src" / "résumé.txt").read_text(encoding="utf-8"), "new\n")
        self.assertFalse((replay / "remove.txt").exists())

    def test_rejects_protected_and_unallowed_changes(self) -> None:
        (self.workspace / "dev_tests" / "protected.py").write_text("edited\n", encoding="utf-8")
        with self.assertRaisesRegex(ArtifactValidationError, "protected"):
            self.collect()
        (self.workspace / "dev_tests" / "protected.py").write_text("trusted\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("not allowed\n", encoding="utf-8")
        with self.assertRaisesRegex(ArtifactValidationError, "not allowed"):
            self.collect()

    def test_rejects_symlink_escape(self) -> None:
        escape = self.workspace / "src" / "escape.txt"
        escape.write_text("outside\n", encoding="utf-8")
        original_stat = os.stat

        def lstat_with_escape(path: object, *args: object, **kwargs: object) -> os.stat_result:
            if Path(path) == escape:
                return os.stat_result((stat.S_IFLNK, 0, 0, 0, 0, 0, 0, 0, 0, 0))
            return original_stat(path, *args, **kwargs)

        with patch("aieb_runner.artifacts.os.stat", side_effect=lstat_with_escape):
            with self.assertRaisesRegex(ArtifactValidationError, "symlink"):
                self.collect()

    def test_rejects_hardlink(self) -> None:
        (self.workspace / "src" / "hardlink.py").write_text("plain\n", encoding="utf-8")
        os.link(self.workspace / "src" / "hardlink.py", self.workspace / "src" / "hardlink-copy.py")
        with self.assertRaisesRegex(ArtifactValidationError, "hardlink"):
            self.collect()

    def test_size_and_file_count_limits_reject_without_success(self) -> None:
        (self.workspace / "src" / "large.txt").write_bytes(b"x" * 20)
        restricted = SubmissionPolicy(include=("src/**",), protected=(), max_artifact_bytes=10)
        with self.assertRaisesRegex(ArtifactValidationError, "byte limit"):
            collect_candidate(frozen_source=self.source, workspace=self.workspace, submission=restricted, base_revision_digest=BASE_DIGEST, store=self.store, access_scope="attempt-1")
        (self.workspace / "src" / "large.txt").unlink()
        (self.workspace / "src" / "one.txt").write_text("1", encoding="utf-8")
        (self.workspace / "src" / "two.txt").write_text("2", encoding="utf-8")
        with self.assertRaisesRegex(ArtifactValidationError, "file-count"):
            self.collect(limits=CollectionLimits(max_files=1))

    def test_store_verifies_reads_dedup_permissions_and_safe_cleanup(self) -> None:
        blob = self.store.put_bytes(b"same")
        self.assertEqual(blob, self.store.put_bytes(b"same"))
        private = self.store.create_reference(blob, access_scope="private-a")
        public = self.store.create_reference(blob, access_scope="public-export", visibility="public")
        self.assertEqual(self.store.read(public, principal_scope="other"), b"same")
        with self.assertRaises(ArtifactAccessDenied):
            self.store.read(private, principal_scope="other")
        self.assertEqual(self.store.delete_unreferenced((blob,)), ())
        self.store._reference_path(private.id).unlink()
        self.store._reference_path(public.id).unlink()
        self.assertEqual(self.store.delete_unreferenced((blob,)), (blob.sha256,))

    def test_missing_and_corrupted_blobs_fail_verified_read(self) -> None:
        blob = self.store.put_bytes(b"verified")
        reference = self.store.create_reference(blob, access_scope="attempt-1")
        self.store._blob_path(blob.sha256).write_bytes(b"corrupt")
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read(reference, principal_scope="attempt-1")
        self.store._blob_path(blob.sha256).unlink()
        with self.assertRaises(ArtifactNotFound):
            self.store.read(reference, principal_scope="attempt-1")

    def test_interrupted_blob_write_leaves_no_published_blob(self) -> None:
        data = b"interrupted"
        digest = __import__("hashlib").sha256(data).hexdigest()
        with patch("aieb_runner.artifacts.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.store.put_bytes(data)
        self.assertFalse(self.store._blob_path(digest).exists())
        self.assertEqual(list(self.store._tmp.glob("blob-*")), [])

    def test_safe_tar_rejects_traversal_links_malformed_and_expansion(self) -> None:
        malformed = self.root / "bad.tar"
        malformed.write_bytes(b"not a tar")
        with self.assertRaisesRegex(ArtifactValidationError, "malformed"):
            safe_extract_tar(archive=malformed, destination=self.root / "out")
        traversal = self.root / "traversal.tar"
        with tarfile.open(traversal, "w") as archive:
            info = tarfile.TarInfo("../escape.txt")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(ArtifactValidationError, "unsafe"):
            safe_extract_tar(archive=traversal, destination=self.root / "out")
        linked = self.root / "link.tar"
        with tarfile.open(linked, "w") as archive:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/outside"
            archive.addfile(info)
        with self.assertRaisesRegex(ArtifactValidationError, "not a regular"):
            safe_extract_tar(archive=linked, destination=self.root / "out")
        oversized = self.root / "large.tar"
        with tarfile.open(oversized, "w") as archive:
            info = tarfile.TarInfo("large.bin")
            info.size = 11
            archive.addfile(info, io.BytesIO(b"x" * 11))
        with self.assertRaisesRegex(ArtifactValidationError, "expanded"):
            safe_extract_tar(archive=oversized, destination=self.root / "out", max_bytes=10)

    def test_safe_tar_rejects_windows_drive_qualified_member_name(self) -> None:
        """"C:/outside/evil.txt" starts with neither "/" nor "..", so it is not
        caught by the ordinary traversal check, yet Path(destination) /
        "C:/outside/evil.txt" discards destination entirely on Windows."""
        drive = self.root / "drive.tar"
        with tarfile.open(drive, "w") as archive:
            info = tarfile.TarInfo("C:/outside/evil.txt")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(ArtifactValidationError, "unsafe"):
            safe_extract_tar(archive=drive, destination=self.root / "out")

    def test_safe_tar_rejects_escape_through_a_symlinked_existing_destination(self) -> None:
        """A member name alone can be a perfectly safe-looking relative path
        ("linked-dir/evil.txt") and still resolve outside destination if an
        already-existing destination (reused across calls, not freshly
        created) has a symlinked intermediate directory. No tar member can
        plant that symlink itself - every member here is required to be a
        regular file - so this models a dirty destination from a prior
        caller, not a self-contained archive attack.

        Creating a real symlink needs a privilege this sandboxed environment
        does not grant (confirmed: WinError 1314), so this simulates the
        resolved-outside-root condition the same way test_rejects_symlink_escape
        simulates its symlink - by patching resolution for the one path of
        interest - rather than skipping the check entirely."""
        outside_target = self.root / "outside-target" / "evil.txt"
        destination = self.root / "out2"
        destination.mkdir()
        escape = self.root / "escape.tar"
        with tarfile.open(escape, "w") as archive:
            info = tarfile.TarInfo("linked-dir/evil.txt")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        real_resolve = Path.resolve

        def resolve_with_symlink_escape(path: Path, *args: object, **kwargs: object) -> Path:
            resolved = real_resolve(path, *args, **kwargs)
            if resolved == (destination / "linked-dir" / "evil.txt").absolute():
                return outside_target
            return resolved

        with patch.object(Path, "resolve", resolve_with_symlink_escape):
            with self.assertRaisesRegex(ArtifactValidationError, "escaped"):
                safe_extract_tar(archive=escape, destination=destination)
        self.assertFalse(outside_target.exists())


if __name__ == "__main__":
    unittest.main()
