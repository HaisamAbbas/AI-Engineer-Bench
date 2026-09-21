# RAG-05 dev_tests

Run from the repository root:

```bash
python dev_tests/selfcheck.py
```

`selfcheck.py` exercises the published HTTP contract (see
`contracts/application-api.md`) over toy data inside one server process. It
covers update visibility, idempotent replay, version ordering, conflict,
deletion, recreation, and metadata filtering.

These checks are necessary but not sufficient: the trusted evaluator runs a
held-out corpus of real upstream text with additional checks (citation
currency, full-text matching quality, and incremental write scope) that are
maintainer-only. Do not modify `dev_tests/`.
