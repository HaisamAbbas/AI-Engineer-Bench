from __future__ import annotations

import unittest

from aieb_api.app import create_app
from aieb_api.schemas import TaskDraftCreateRequest


class TaskAuthoringContractTest(unittest.TestCase):
    def test_task_draft_route_is_private_and_registered(self) -> None:
        app = create_app()
        paths = [route.path for included in app.routes if hasattr(included, "original_router") for route in included.original_router.routes if hasattr(route, "path")]
        self.assertIn("/v1/maintainer/task-drafts", paths)
        self.assertIn("/v1/maintainer/task-drafts/authored", paths)
        self.assertIn("/v1/maintainer/task-drafts/mined-pr", paths)
        self.assertIn("/v1/maintainer/task-drafts/live-window", paths)

    def test_request_rejects_unbounded_or_malformed_digest(self) -> None:
        with self.assertRaises(ValueError):
            TaskDraftCreateRequest(manifest={}, ticket_text="ticket", evaluator_code_digest="bad", evaluator_contract_version="v2")


if __name__ == "__main__":
    unittest.main()
