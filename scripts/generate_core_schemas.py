"""Generate checked-in JSON Schema artifacts for the versioned core contracts."""

from __future__ import annotations

import json
from pathlib import Path

from aieb_core.models import (
    Attempt, BudgetProfile, CampaignDraft, CandidateManifest, Cohort, EntrantRevision,
    EvaluationPlan, EvaluationResult, EventEnvelope, ProtocolRevision, PublicationManifest,
    ResolvedCampaign, TaskRevision, Trial,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "implementation" / "evidence" / "ENG-002" / "schemas"
MODELS = (TaskRevision, EntrantRevision, ProtocolRevision, BudgetProfile, Cohort, CampaignDraft,
          ResolvedCampaign, Trial, Attempt, CandidateManifest, EvaluationPlan, EvaluationResult,
          EventEnvelope, PublicationManifest)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        path = OUT / f"{model.__name__}.schema.json"
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
