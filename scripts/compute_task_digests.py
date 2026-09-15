"""Replace placeholder repeated-digit digests in every suites/dev/*/task.yaml
with real SHA-256 digests of the actual on-disk content those fields name.

Review finding #2: every task.yaml's repository_digest/provenance_digest/
contract_digest/service_topology_digest/evaluator_digest was a repeated-digit
placeholder ("1111...1", "2222...2", ...) rather than a hash of anything -
they never changed no matter what the repo/provenance.json/contract/
environment doc/evaluator source actually contained, so they could not catch
drift and were not verifiable claims. Four of the five name content that
genuinely exists in this repo today and can be hashed for real:

  repository_digest        sha256 over the task's repo/ file tree
  provenance_digest         sha256 of provenance.json
  contract_digest            sha256 of contracts/application-api.md
  service_topology_digest   sha256 of environment/README.md
  evaluator_digest            sha256 of the trusted evaluator's own source file

`environment.official_image`'s sha256 is different in kind: it names a
container image this project has never built or pushed (the registry host,
registry.example, is itself a fictional placeholder, not a real one this
digest could ever verify against) - ENG-013's own scope is local fixture
validation, not a real image build/push pipeline. Computing a "real" hash of
an image that does not exist would not be more honest than the placeholder
it already is, so it is deliberately left unchanged here.

Run with no arguments; rewrites every suites/dev/*/task.yaml in place.
`aieb_cli.main._task_check` (see the digest-verification test in
tests/test_task_digests.py) can then confirm each rewritten value actually
matches the content it names, closing the gap a hand-edited or stale value
would otherwise reopen.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aieb_cli.main import TASK_RUNTIMES, hash_file, hash_tree  # noqa: E402


def evaluator_source_path(evaluator_module: str) -> Path:
    # "tests.maintainer.rag01.evaluator" -> tests/maintainer/rag01/evaluator.py
    return ROOT.joinpath(*evaluator_module.split(".")).with_suffix(".py")


def compute_digests(task_dir: Path, evaluator_module: str) -> dict[str, str]:
    return {
        "repository_digest": hash_tree(task_dir / "repo"),
        "provenance_digest": hash_file(task_dir / "provenance.json"),
        "contract_digest": hash_file(task_dir / "contracts" / "application-api.md"),
        "service_topology_digest": hash_file(task_dir / "environment" / "README.md"),
        "evaluator_digest": hash_file(evaluator_source_path(evaluator_module)),
    }


def rewrite(task_dir: Path, digests: dict[str, str]) -> bool:
    text = (task_dir / "task.yaml").read_text(encoding="utf-8")
    changed = False
    for field, value in digests.items():
        pattern = re.compile(rf'({field}:\s*"?)[0-9a-f]{{64}}("?)')
        new_text, count = pattern.subn(rf"\g<1>{value}\g<2>", text)
        if count == 0:
            raise SystemExit(f"{task_dir}: field {field} not found in task.yaml")
        changed = changed or new_text != text
        text = new_text
    (task_dir / "task.yaml").write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    suites = ROOT / "suites" / "dev"
    for task_id, runtime in TASK_RUNTIMES.items():
        task_dir = suites / task_id
        evaluator_module = runtime[1]
        digests = compute_digests(task_dir, evaluator_module)
        changed = rewrite(task_dir, digests)
        print(f"{task_id}: {'updated' if changed else 'unchanged'}")


if __name__ == "__main__":
    main()
