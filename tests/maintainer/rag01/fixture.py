"""Deterministic maintainer-only held-out RAG-01 fixture generator."""

from __future__ import annotations

import random


FIXTURE_VERSION = "rag01-heldout/v1"


def generate(seed: int = 4107) -> dict[str, object]:
    randomizer = random.Random(seed)
    tokens = ["amber", "bravo", "cedar"]
    randomizer.shuffle(tokens)
    return {
        "fixture_version": FIXTURE_VERSION,
        "seed": seed,
        "documents": {
            "amber": {"version": 1, "text": f"{tokens[0]} original archive", "metadata": {"group": "updates"}},
            "bravo": {"version": 1, "text": f"{tokens[1]} removable archive", "metadata": {"group": "deletes"}},
            "cedar": {"version": 1, "text": f"{tokens[2]} unaffected archive", "metadata": {"group": "control"}},
        },
        "replacement_text": "violet replacement fact",
        "recreation_text": "bravo higher recreation fact",
    }
