"""Import-time environment leak probe (codex-audit finding 4, second review round).

The module's TOP-LEVEL code (lines that run when the module is first imported) snapshots
os.environ. If the evaluator module were imported while the spawn child still carried the
worker's full environment - the bug being tested - this snapshot would contain the planted
secrets, and the verify phase would see them even though the entrypoint scrubbed os.environ
before invoking `evaluate`. With evaluator modules delivered as identity strings and imported
AFTER the scrub, this snapshot is empty of everything but the allowlisted/env-scrubbed state.
"""

import os

_IMPORT_TIME_ENV = dict(os.environ)


def evaluate(candidate_path, stop=None):
    """Report the import-time environment snapshot (not the runtime one) alongside the live
    runtime environment, so the parent can assert the import-time code never observed worker
    secrets regardless of what the runtime scrub did."""
    return {
        "pass": True,
        "import_time_db_url": _IMPORT_TIME_ENV.get("AIEB_DATABASE_URL", "<absent>"),
        "import_time_build_token": _IMPORT_TIME_ENV.get("CI_BUILD_TOKEN", "<absent>"),
        "runtime_db_url": os.environ.get("AIEB_DATABASE_URL", "<absent>"),
        "runtime_build_token": os.environ.get("CI_BUILD_TOKEN", "<absent>"),
        "import_time_secret_substr_count": sum(
            1 for key in _IMPORT_TIME_ENV for marker in ("PASSWORD", "SECRET", "TOKEN", "API_KEY") if marker in key.upper()
        ),
    }