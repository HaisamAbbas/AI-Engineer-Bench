# Upstream attribution

This task repository vendors real upstream code, unmodified:

- `sqlite_utils/` — [sqlite-utils](https://github.com/simonw/sqlite-utils)
  release **3.38**, pinned commit `9d7da06`, Apache-2.0 license.
- `sqlite_fts4/` — [sqlite-fts4](https://github.com/simonw/sqlite-fts4)
  release **1.0.3**, pinned commit `f86b449`, Apache-2.0 license (a direct
  runtime dependency of sqlite-utils' `db.py`).
- `click/`, `pluggy/`, `dateutil/`, `six.py` — the subset of sqlite-utils'
  declared runtime dependency closure that its library import path actually
  needs, vendored so the service runs offline with no package index:
  [click](https://github.com/pallets/click) 8.5.0 (BSD-3-Clause),
  [pluggy](https://github.com/pytest-dev/pluggy) (MIT),
  [python-dateutil](https://github.com/dateutil/dateutil) 2.9.0.post0
  (dual BSD-3-Clause / Apache-2.0), and
  [six](https://github.com/benjaminp/six) (MIT).

`knowledge_service/` (the HTTP wrapper and the deliberately broken backend) and
`dev_tests/` were written for this task. The held-out evaluation corpus consists
of verbatim excerpts of the upstream README at the pinned commit; see the task's
`provenance.json`.

Do not modify the vendored packages as part of a repair; the expected repair is
behavioral and lives in `knowledge_service/`.

