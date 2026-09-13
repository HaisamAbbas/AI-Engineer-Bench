"""Development entrypoint for repository integrity and toolchain checks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SPEC_HASHES = {
    "AI-Engineer-Bench-Architecture-v0.1.md": (
        "1acd59c8be999ae750d864415a5eb41bba441e19e0a499e6984c3b98261b1017"
    ),
    "AI-Engineer-Bench-Implementation-Spec-v1.0.md": (
        "8f1556ac93e2f38af926923e6d13b33e452e9f969528cfcb89e6cfb7d6a2150c"
    ),
}
REQUIRED_DOCS = (
    "README.md",
    "docs/implementation/STATUS.md",
    "docs/implementation/DECISIONS.md",
    "docs/implementation/SESSION_HANDOFF.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command_version(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = (completed.stdout or completed.stderr).strip()
    return output.splitlines()[0] if output else f"exit {completed.returncode}"


def doctor() -> int:
    toolchain = json.loads((ROOT / "toolchain.json").read_text(encoding="utf-8"))
    expected_python = toolchain["python"]["version"]
    current_python = platform.python_version()
    observations = {
        "Python": current_python,
        "uv": command_version(["uv", "--version"]),
        "Git": command_version(["git", "--version"]),
        "Docker": command_version(["docker", "--version"]),
        "Docker Compose": command_version(["docker", "compose", "version"]),
        "Node (deferred)": command_version(["node", "--version"]),
        "pnpm (deferred)": command_version(["pnpm", "--version"]),
    }
    try:
        observations["Harbor"] = importlib.metadata.version("harbor")
    except importlib.metadata.PackageNotFoundError:
        observations["Harbor"] = None
    print("AI Engineer Bench development environment")
    for name, version in observations.items():
        print(f"  {name}: {version or 'not found'}")
    failures: list[str] = []
    if current_python != expected_python:
        failures.append(
            f"Python {expected_python} is required for this bootstrap; "
            f"found {current_python}"
        )
    expected_uv = f"uv {toolchain['uv']['version']}"
    if not (observations["uv"] or "").startswith(expected_uv):
        failures.append(
            f"uv {toolchain['uv']['version']} is required for this bootstrap; "
            f"found {observations['uv'] or 'not found'}"
        )
    if observations["Git"] is None:
        failures.append("Git is required for repository work; executable not found")
    expected_harbor = toolchain["harbor"]["version"]
    if observations["Harbor"] != expected_harbor:
        failures.append(
            f"Harbor {expected_harbor} is required for the execution foundation; "
            f"found {observations['Harbor'] or 'not found'}"
        )
    for failure in failures:
        print(
            f"error: {failure}",
            file=sys.stderr,
        )
    return 1 if failures else 0


def verify_repository() -> list[str]:
    errors: list[str] = []
    for name, expected_hash in EXPECTED_SPEC_HASHES.items():
        copied = ROOT / "docs" / "specs" / name
        if not copied.is_file():
            errors.append(f"missing specification copy: {copied.relative_to(ROOT)}")
        elif sha256_file(copied) != expected_hash:
            errors.append(f"specification copy changed: {copied.relative_to(ROOT)}")

    for relative_path in REQUIRED_DOCS:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing required document: {relative_path}")

    status_path = ROOT / "docs" / "implementation" / "STATUS.md"
    if status_path.is_file():
        status = status_path.read_text(encoding="utf-8")
        found = {int(value) for value in re.findall(r"ENG-(\d{3})", status)}
        expected = set(range(1, 25))
        if found != expected:
            missing = sorted(expected - found)
            extra = sorted(found - expected)
            errors.append(f"ticket ledger mismatch; missing={missing}, extra={extra}")
    return errors


def run_tests() -> int:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def check() -> int:
    errors = verify_repository()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    test_status = run_tests()
    if test_status:
        return test_status
    print("Repository checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "check", "test"))
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "test":
        return run_tests()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
