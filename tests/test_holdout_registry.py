"""V2-GAP-005 private holdout boundary tests that require no database."""
from __future__ import annotations

import sys
import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from aieb_api import holdouts  # noqa: E402
from aieb_api.errors import ApiError  # noqa: E402
from aieb_api import holdout_storage  # noqa: E402
from aieb_api.holdout_storage import DirectoryPrivateHoldoutStore, HoldoutCapability, HoldoutStorageError  # noqa: E402
from aieb_api.idempotency import required_idempotency_key  # noqa: E402
from aieb_api.models import HoldoutAccessAuditRow, HoldoutManifestRow, HoldoutReviewRow  # noqa: E402

H64 = "a" * 64


def test_public_or_local_storage_uri_is_rejected() -> None:
    with pytest.raises(ApiError):
        holdouts.validate_private_uri("https://example.test/fixture.json")
    with pytest.raises(ApiError):
        holdouts.validate_private_uri("file:///tmp/fixture.json")


def test_private_uri_and_manifest_identity_are_digest_bound() -> None:
    uri = holdouts.validate_private_uri("private://official/rag-1/object")
    first = holdouts.manifest_identity(
        task_revision_id=uuid4(), storage_uri=uri, object_digest=H64,
        object_length=42, protocol_version="protocol/v1",
        access_scope="campaign:one", overlap_review_digest=H64,
    )
    second = holdouts.manifest_identity(
        task_revision_id=uuid4(), storage_uri=uri, object_digest=H64,
        object_length=42, protocol_version="protocol/v1",
        access_scope="campaign:one", overlap_review_digest=H64,
    )
    assert first != second


def test_holdout_review_kinds_are_explicit() -> None:
    assert holdouts._REVIEW_KINDS == {"overlap", "storage", "isolation", "manifest"}  # noqa: SLF001


def test_latest_reject_supersedes_an_older_approval() -> None:
    latest = holdouts._latest_reviews([  # noqa: SLF001
        SimpleNamespace(review_kind="overlap", decision="reject"),
        SimpleNamespace(review_kind="overlap", decision="approve"),
    ])
    assert latest["overlap"].decision == "reject"


def test_directory_adapter_rejects_traversal() -> None:
    root = ROOT / ".cache" / "test-tmp" / f"holdout-{uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=False)
    except PermissionError as exc:
        pytest.skip(f"host cannot create a writable holdout test directory: {exc}")
    payload = b"private fixture"
    digest = hashlib.sha256(payload).hexdigest()
    try:
        (root / "fixture.json").write_bytes(payload)
        store = DirectoryPrivateHoldoutStore(root)
        capability = HoldoutCapability(uuid4(), digest, len(payload), "scope")
        assert store.read(capability, object_key="fixture.json") == payload
        for key in ("../fixture.json", "nested/../fixture.json", "/fixture.json", "C:/outside.json", "nested\\fixture.json"):
            with pytest.raises(HoldoutStorageError):
                store.read(capability, object_key=key)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_directory_adapter_verifies_digest_and_length() -> None:
    root = ROOT / ".cache" / "test-tmp" / f"holdout-{uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=False)
    except PermissionError as exc:
        pytest.skip(f"host cannot create a writable holdout test directory: {exc}")
    payload = b"private fixture"
    try:
        (root / "fixture.json").write_bytes(payload)
        store = DirectoryPrivateHoldoutStore(root)
        capability = HoldoutCapability(uuid4(), "0" * 64, len(payload), "scope")
        with pytest.raises(HoldoutStorageError, match="digest"):
            store.read(capability, object_key="fixture.json")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_directory_adapter_rejects_symlinked_objects() -> None:
    root = ROOT / ".cache" / "test-tmp" / f"holdout-{uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=False)
    except PermissionError as exc:
        pytest.skip(f"host cannot create a writable holdout test directory: {exc}")
    payload = b"private fixture"
    digest = hashlib.sha256(payload).hexdigest()
    try:
        target = root / "target.json"
        target.write_bytes(payload)
        link = root / "link.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"host does not permit symlink test setup: {exc}")
        store = DirectoryPrivateHoldoutStore(root)
        capability = HoldoutCapability(uuid4(), digest, len(payload), "scope")
        with pytest.raises(HoldoutStorageError, match="symlink"):
            store.read(capability, object_key="link.json")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_directory_adapter_rejects_intermediate_symlink_components() -> None:
    root = ROOT / ".cache" / "test-tmp" / f"holdout-{uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=False)
    except PermissionError as exc:
        pytest.skip(f"host cannot create a writable holdout test directory: {exc}")
    payload = b"private fixture"
    digest = hashlib.sha256(payload).hexdigest()
    try:
        target = root / "target"
        target.mkdir()
        (target / "fixture.json").write_bytes(payload)
        link = root / "alias"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"host does not permit symlink test setup: {exc}")
        store = DirectoryPrivateHoldoutStore(root)
        capability = HoldoutCapability(uuid4(), digest, len(payload), "scope")
        with pytest.raises(HoldoutStorageError, match="symlink"):
            store.read(capability, object_key="alias/fixture.json")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_failed_access_audit_failure_does_not_replace_original_storage_error(monkeypatch) -> None:
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(holdout_storage.db, "session_factory", lambda: BrokenFactory())
    # Failed reads preserve their provider exception even if the independent
    # audit transaction cannot be opened.
    holdout_storage._persist_access_audit(success=False, holdout_id=uuid4())  # noqa: SLF001


def test_successful_access_fails_closed_when_audit_cannot_be_persisted(monkeypatch) -> None:
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(holdout_storage.db, "session_factory", lambda: BrokenFactory())
    with pytest.raises(HoldoutStorageError, match="audit"):
        holdout_storage._persist_access_audit(success=True, holdout_id=uuid4())  # noqa: SLF001


def test_idempotency_header_is_trimmed_and_bounded() -> None:
    assert required_idempotency_key("  holdout-key  ") == "holdout-key"
    with pytest.raises(ApiError):
        required_idempotency_key("   ")
    with pytest.raises(ApiError):
        required_idempotency_key("x" * 129)


def test_holdout_orm_metadata_contains_migration_identity_constraints() -> None:
    def names(model) -> set[str]:
        return {constraint.name for constraint in model.__table__.constraints if constraint.name}

    assert {
        "ck_holdout_manifest_identity_digest",
        "ck_holdout_manifest_protocol_scope",
    } <= names(HoldoutManifestRow)
    assert {
        "ck_holdout_review_evidence_digest",
        "ck_holdout_review_reason",
        "ck_holdout_review_report_size",
    } <= names(HoldoutReviewRow)
    assert {
        "ck_holdout_access_actor",
        "ck_holdout_access_digest",
    } <= names(HoldoutAccessAuditRow)
