# Runtime environment

The development fixture is a standard-library HTTP service launched with
`python -m knowledge_service.server --port <port>` from the task repository
root, which also vendors the real `sqlite_utils/` (sqlite-utils 3.38) and
`sqlite_fts4/` packages it imports. SQLite FTS5 is required and is available
in CPython's bundled sqlite3 on both Linux and Windows. The trusted admission
harness supplies a local external write-ledger URL via `AIEB_LEDGER_URL`.
No model provider, network corpus, or secret is required.
