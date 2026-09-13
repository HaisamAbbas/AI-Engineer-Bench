from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "scripts" / "run_eng001_spike.py"


@unittest.skipUnless(
    os.environ.get("AIEB_RUN_HARBOR_INTEGRATION") == "1",
    "set AIEB_RUN_HARBOR_INTEGRATION=1 to run Docker compatibility evidence",
)
class HarborCompatibilityTest(unittest.TestCase):
    def test_timeout_collection_separate_replay_and_cleanup(self) -> None:
        env = dict(os.environ)
        completed = subprocess.run(
            [sys.executable, str(WORKER)],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout.splitlines()[-1])
        self.assertEqual(summary["timeout_exception"], "AgentTimeoutError")
        self.assertEqual(summary["reward"], 1.0)
        self.assertEqual(summary["verifier_environment_mode"], "separate")
        self.assertEqual(summary["new_file"], "service-ready\n")
        self.assertTrue(summary["edited_file_contains_marker"])
        self.assertTrue(summary["late_write_absent"])
        self.assertTrue(summary["cleanup_clean"])
