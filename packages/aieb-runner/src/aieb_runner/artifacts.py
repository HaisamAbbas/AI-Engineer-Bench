"""Local content-addressed candidate artifacts with non-executing collection."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from aieb_core.canonical import content_hash
from aieb_core.models import CandidateFile, CandidateManifest, SubmissionPolicy


DEFAULT_MAX_FILES = 10_000


class ArtifactError(ValueError):
    """Base error for artifact storage and extraction failures."""


class ArtifactValidationError(ArtifactError):
    """Candidate filesystem data violates the frozen submission contract."""


class ArtifactIntegrityError(ArtifactError):
    """Stored bytes do not match the digest they claim to have."""


class ArtifactNotFound(ArtifactError):
    """A referenced blob is not present."""


class ArtifactAccessDenied(ArtifactError):
    """A principal attempted to read a blob through the wrong reference."""


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class ArtifactReference:
    id: UUID
    blob: BlobRef
    access_scope: str
    visibility: str


@dataclass(frozen=True)
class StoredCandidate:
    manifest: CandidateManifest
    file_references: tuple[tuple[str, ArtifactReference], ...]

    def reference_for(self, path: str) -> ArtifactReference:
        for candidate_path, reference in self.file_references:
            if candidate_path == path:
                return reference
        raise ArtifactNotFound(f"no stored reference for {path}")


class ArtifactStore(Protocol):
    def put_bytes(self, data: bytes) -> BlobRef: ...
    def create_reference(self, blob: BlobRef, *, access_scope: str, visibility: str = "restricted") -> ArtifactReference: ...
    def read(self, reference: ArtifactReference, *, principal_scope: str) -> bytes: ...
    def verify(self, blob: BlobRef) -> None: ...


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(path: str) -> str:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or ".." in pure.parts or any(part in ("", ".") for part in pure.parts):
        raise ArtifactValidationError(f"unsafe relative path: {path!r}")
    return pure.as_posix()


class FilesystemArtifactStore:
    """CAS bytes plus access-controlled references, rooted in one local directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._blobs = self.root / "blobs" / "sha256"
        self._refs = self.root / "references"
        self._tmp = self.root / "tmp"
        for directory in (self._blobs, self._refs, self._tmp):
            directory.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ArtifactValidationError("invalid blob digest")
        return self._blobs / digest[:2] / digest

    def _reference_path(self, reference_id: UUID) -> Path:
        return self._refs / f"{reference_id}.json"

    def put_bytes(self, data: bytes) -> BlobRef:
        digest = _sha256_bytes(data)
        blob = BlobRef(digest, len(data))
        destination = self._blob_path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.verify(blob)
            return blob
        descriptor, temporary_name = tempfile.mkstemp(prefix="blob-", dir=self._tmp)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if _sha256_bytes(temporary.read_bytes()) != digest:
                raise ArtifactIntegrityError("temporary blob digest mismatch")
            try:
                os.replace(temporary, destination)
            except FileExistsError:
                self.verify(blob)
            self.verify(blob)
            return blob
        finally:
            if temporary.exists():
                temporary.unlink()

    def create_reference(self, blob: BlobRef, *, access_scope: str, visibility: str = "restricted") -> ArtifactReference:
        if visibility not in {"restricted", "public"} or not access_scope:
            raise ArtifactValidationError("reference visibility and scope are required")
        self.verify(blob)
        reference = ArtifactReference(uuid4(), blob, access_scope, visibility)
        temporary = self._tmp / f"ref-{reference.id}.json"
        temporary.write_text(json.dumps({"id": str(reference.id), "sha256": blob.sha256, "byte_length": blob.byte_length, "access_scope": access_scope, "visibility": visibility}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self._reference_path(reference.id))
        return reference

    def _persisted_reference(self, reference: ArtifactReference) -> dict[str, object]:
        path = self._reference_path(reference.id)
        if not path.is_file():
            raise ArtifactNotFound(f"reference does not exist: {reference.id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError("reference metadata is malformed") from exc
        if value != {"id": str(reference.id), "sha256": reference.blob.sha256, "byte_length": reference.blob.byte_length, "access_scope": reference.access_scope, "visibility": reference.visibility}:
            raise ArtifactIntegrityError("reference metadata does not match supplied reference")
        return value

    def read(self, reference: ArtifactReference, *, principal_scope: str) -> bytes:
        self._persisted_reference(reference)
        if reference.visibility != "public" and reference.access_scope != principal_scope:
            raise ArtifactAccessDenied("artifact reference is not readable by this scope")
        path = self._blob_path(reference.blob.sha256)
        if not path.is_file():
            raise ArtifactNotFound(f"blob does not exist: {reference.blob.sha256}")
        data = path.read_bytes()
        if len(data) != reference.blob.byte_length or _sha256_bytes(data) != reference.blob.sha256:
            raise ArtifactIntegrityError("stored blob digest or length mismatch")
        return data

    def verify(self, blob: BlobRef) -> None:
        path = self._blob_path(blob.sha256)
        if not path.is_file():
            raise ArtifactNotFound(f"blob does not exist: {blob.sha256}")
        data = path.read_bytes()
        if len(data) != blob.byte_length or _sha256_bytes(data) != blob.sha256:
            raise ArtifactIntegrityError("stored blob digest or length mismatch")

    def delete_unreferenced(self, blobs: tuple[BlobRef, ...]) -> tuple[str, ...]:
        """Delete only explicitly nominated blobs that have no persisted reference."""
        referenced: set[str] = set()
        for reference_path in self._refs.glob("*.json"):
            try:
                referenced.add(str(json.loads(reference_path.read_text(encoding="utf-8"))["sha256"]))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ArtifactIntegrityError(f"cannot safely inspect reference {reference_path.name}") from exc
        deleted: list[str] = []
        for blob in blobs:
            if blob.sha256 in referenced:
                continue
            path = self._blob_path(blob.sha256)
            if path.is_file():
                self.verify(blob)
                path.unlink()
                deleted.append(blob.sha256)
        return tuple(deleted)


@dataclass(frozen=True)
class CollectionLimits:
    max_files: int = DEFAULT_MAX_FILES


def _walk_regular_files(root: Path, *, reject_hardlinks: bool = True) -> dict[str, tuple[Path, os.stat_result]]:
    if not root.is_dir():
        raise ArtifactValidationError(f"workspace is not a directory: {root}")
    files: dict[str, tuple[Path, os.stat_result]] = {}
    identities: dict[tuple[int, int], str] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                entry_path = Path(entry.path)
                relative = _safe_relative(entry_path.relative_to(root).as_posix())
                # DirEntry.stat() returns zero file identifiers in the managed
                # Windows sandbox; os.stat() provides the actual stable identity.
                metadata = os.stat(entry_path, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise ArtifactValidationError(f"symlink is forbidden: {relative}")
                if stat.S_ISDIR(metadata.st_mode):
                    stack.append(entry_path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise ArtifactValidationError(f"non-regular file is forbidden: {relative}")
                identity = (metadata.st_dev, metadata.st_ino)
                if reject_hardlinks and identity in identities:
                    raise ArtifactValidationError(f"hardlink is forbidden: {relative}")
                identities[identity] = relative
                files[relative] = (entry_path, metadata)
    return files


def _included(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _file_metadata(path: Path, metadata: os.stat_result) -> tuple[str, int, bool]:
    data = path.read_bytes()
    return _sha256_bytes(data), len(data), bool(metadata.st_mode & stat.S_IXUSR)


def _tree_hash(files: dict[str, tuple[Path, os.stat_result]]) -> str:
    entries = [{"path": path, "sha256": _file_metadata(file_path, metadata)[0], "executable": _file_metadata(file_path, metadata)[2]} for path, (file_path, metadata) in sorted(files.items())]
    return content_hash(entries)


def collect_candidate(*, frozen_source: Path, workspace: Path, submission: SubmissionPolicy, base_revision_digest: str, store: ArtifactStore, access_scope: str, limits: CollectionLimits = CollectionLimits()) -> StoredCandidate:
    """Collect changed allowed regular files without invoking any candidate program."""
    source_files = _walk_regular_files(frozen_source, reject_hardlinks=False)
    workspace_files = _walk_regular_files(workspace)
    changed = sorted(set(source_files) | set(workspace_files))
    candidate_files: list[CandidateFile] = []
    references: list[tuple[str, ArtifactReference]] = []
    total_bytes = 0
    for relative in changed:
        before = source_files.get(relative)
        after = workspace_files.get(relative)
        before_info = _file_metadata(*before) if before else None
        after_info = _file_metadata(*after) if after else None
        if before_info == after_info:
            continue
        if _included(relative, submission.protected):
            raise ArtifactValidationError(f"protected path changed: {relative}")
        if not _included(relative, submission.include):
            raise ArtifactValidationError(f"changed path is not allowed by submission policy: {relative}")
        if after is None:
            candidate_files.append(CandidateFile(path=relative, operation="delete"))
            continue
        digest, size, executable = after_info
        total_bytes += size
        if total_bytes > submission.max_artifact_bytes:
            raise ArtifactValidationError("candidate artifact exceeds byte limit")
        operation = "add" if before is None else "modify"
        blob = store.put_bytes(after[0].read_bytes())
        reference = store.create_reference(blob, access_scope=access_scope)
        candidate_files.append(CandidateFile(path=relative, operation=operation, sha256=digest, byte_length=size, executable=executable))
        references.append((relative, reference))
    if len(candidate_files) > limits.max_files:
        raise ArtifactValidationError("candidate artifact exceeds file-count limit")
    full_tree_hash = _tree_hash(workspace_files)
    identity = content_hash({"schema_version": "aieb.candidate/v1", "base_revision_digest": base_revision_digest, "full_tree_hash": full_tree_hash, "files": [item.model_dump(mode="json") for item in candidate_files]})
    manifest = CandidateManifest(schema_version="aieb.candidate/v1", id=uuid5(NAMESPACE_URL, f"aieb:candidate:{identity}"), base_revision_digest=base_revision_digest, full_tree_hash=full_tree_hash, files=tuple(candidate_files))
    return StoredCandidate(manifest, tuple(references))


def reconstruct_candidate(*, frozen_source: Path, destination: Path, stored: StoredCandidate, store: ArtifactStore, principal_scope: str) -> None:
    """Build a clean candidate tree from frozen source plus verified stored bytes."""
    if destination.exists() and any(destination.iterdir()):
        raise ArtifactValidationError("reconstruction destination must be empty")
    source_files = _walk_regular_files(frozen_source, reject_hardlinks=False)
    destination.mkdir(parents=True, exist_ok=True)
    for relative, (source_path, metadata) in source_files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        os.chmod(target, stat.S_IMODE(metadata.st_mode))
    for entry in stored.manifest.files:
        target = destination / _safe_relative(entry.path)
        if entry.operation == "delete":
            if target.exists():
                target.unlink()
            continue
        reference = stored.reference_for(entry.path)
        data = store.read(reference, principal_scope=principal_scope)
        if _sha256_bytes(data) != entry.sha256 or len(data) != entry.byte_length:
            raise ArtifactIntegrityError(f"reference bytes do not match manifest: {entry.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o755 if entry.executable else 0o644)
    if _tree_hash(_walk_regular_files(destination)) != stored.manifest.full_tree_hash:
        raise ArtifactIntegrityError("reconstructed tree hash does not match manifest")


def safe_extract_tar(*, archive: Path, destination: Path, max_files: int = DEFAULT_MAX_FILES, max_bytes: int = 52_428_800) -> None:
    """Extract only validated regular tar members; never follow archive links."""
    try:
        with tarfile.open(archive, mode="r:*") as bundle:
            members = bundle.getmembers()
            if len(members) > max_files:
                raise ArtifactValidationError("archive exceeds file-count limit")
            declared = 0
            for member in members:
                _safe_relative(member.name)
                if not member.isfile():
                    raise ArtifactValidationError(f"archive member is not a regular file: {member.name}")
                declared += member.size
                if declared > max_bytes:
                    raise ArtifactValidationError("archive exceeds expanded byte limit")
            destination.mkdir(parents=True, exist_ok=True)
            for member in members:
                source = bundle.extractfile(member)
                if source is None:
                    raise ArtifactValidationError(f"archive member has no content: {member.name}")
                target = destination / _safe_relative(member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except tarfile.TarError as exc:
        raise ArtifactValidationError("malformed archive") from exc
