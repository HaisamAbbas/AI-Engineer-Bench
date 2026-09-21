"""Maintainer-only RAG-05 held-out fixture.

Real corpus: every document is a verbatim excerpt of the upstream
sqlite-utils README at the pinned release this task vendors (release 3.38,
commit 9d7da06). Two lines that embed Markdown links are used with the link
syntax stripped; the stripped text is noted below and in the task's
provenance.json. This is real third-party documentation content, not
synthetic fixture text.
"""

from __future__ import annotations

import random

FIXTURE_VERSION = "rag05-heldout/v1"

# Real README line (verbatim, link-stripped from
# "Check out the [full library documentation](...python-api.html) for everything
# else you can do with the Python library.").
REPLACEMENT_TEXT = (
    "Check out the full library documentation for everything else you can do "
    "with the Python library."
)

# Real README bullet description (verbatim): the db-to-sqlite entry.
RECREATION_TEXT = "CLI tool for exporting a MySQL or PostgreSQL database as a SQLite file"

# Real README lines (verbatim where link-free; plugins/datasette entries are
# the link-stripped bullet text).
DOCUMENTS: dict[str, dict[str, object]] = {
    "cli-csv-insert": {
        "version": 1,
        "text": "$ sqlite-utils insert dogs.db dogs dogs.csv --csv",
        "metadata": {"group": "cli"},
    },
    "cli-tables-counts": {
        "version": 1,
        "text": "$ sqlite-utils tables dogs.db --counts",
        "metadata": {"group": "cli"},
    },
    "memory-command": {
        "version": 1,
        "text": "You can import JSON data into a new database table like this:",
        "metadata": {"group": "cli"},
    },
    "library-import": {
        "version": 1,
        "text": "You can also `import sqlite_utils` and use it as a Python library like this:",
        "metadata": {"group": "python-api"},
    },
    "insert-all": {
        "version": 1,
        "text": "This line creates a \"dogs\" table if one does not already exist:",
        "metadata": {"group": "python-api"},
    },
    "plugins-feature": {
        # Upstream: "- [Install plugins](https://sqlite-utils.datasette.io/en/stable/plugins.html)
        # to add custom SQL functions and additional features" (link syntax stripped).
        "version": 1,
        "text": "Install plugins to add custom SQL functions and additional features",
        "metadata": {"group": "python-api"},
    },
    "dogsheep-analytics": {
        "version": 1,
        "text": "A family of tools for personal analytics, built on top of `sqlite-utils`",
        "metadata": {"group": "ecosystem"},
    },
    "datasette-explore": {
        # Upstream: "* [Datasette](https://datasette.io/): A tool for exploring and
        # publishing data" (verbatim description text).
        "version": 1,
        "text": "Datasette: A tool for exploring and publishing data",
        "metadata": {"group": "ecosystem"},
    },
}

# The document that is updated to version 2 during evaluation.
UPDATED_DOCUMENT = "library-import"
# The document that is deleted and then recreated at a higher version.
DELETED_DOCUMENT = "plugins-feature"
# The document that must remain untouched and searchable throughout.
CONTROL_DOCUMENT = "datasette-explore"


def generate(seed: int = 5309) -> dict[str, object]:
    """Seed-varying fixture: the group tags are per-seed labels and the
    ingestion order is shuffled. A candidate that hardcodes evaluator queries
    or canned hits cannot know the per-seed group labels, so shortcut answers
    fail; a genuine repair is label-agnostic and passes at any seed."""
    randomizer = random.Random(seed)
    suffix = f"{seed % 997:03x}"
    documents: dict[str, dict[str, object]] = {}
    for document_id, item in DOCUMENTS.items():
        metadata = dict(item["metadata"])  # type: ignore[arg-type]
        metadata["group"] = f"{metadata['group']}-{suffix}"  # type: ignore[index]
        documents[document_id] = {"version": item["version"], "text": item["text"], "metadata": metadata}
    ingestion_order = list(documents)
    randomizer.shuffle(ingestion_order)
    return {
        "fixture_version": FIXTURE_VERSION,
        "seed": seed,
        "documents": documents,
        "ingestion_order": ingestion_order,
        "replacement_text": REPLACEMENT_TEXT,
        "recreation_text": RECREATION_TEXT,
    }
