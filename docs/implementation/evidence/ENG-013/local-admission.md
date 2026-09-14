# ENG-013 local development admission evidence

Date: 2026-09-14. This is deterministic local fixture evidence only; none of these revisions has independent human review or official admission.

The twelve catalogued public tasks span twelve separately named synthetic application families (`knowledge-service-a` through `assistant-service-h`), exceeding the six-project development diversity floor. Each has public ticket/contract/provenance, a broken baseline, reference, alternative, shortcut candidate, external maintainer evaluator, and ten fresh reference resets.

The RAG-02/03/04, EXT-01/03/04, and TOOL-03/04 admission scripts were run locally. In every observed matrix the baseline and shortcut failed, reference and alternative passed, and all ten reference resets passed. TOOL-02's corrected external-ledger implementation has a verified passing reference plus ten resets; its complete post-fix baseline/alternative/shortcut matrix remains a CI/local rerun gate. Existing RAG-01, EXT-02, and TOOL-01 retain their prior recorded admission evidence.

`aieb task validate` and `aieb task verify` resolve every catalog task through the explicit trusted runtime registry; campaign run/inspect/report use the same fresh-replay pipeline and task-specific evaluator. The CI task-admission workflow runs all admission test modules.

The development release manifest is deliberately not created: the specification permits admitted-only releases, while every task is marked `pending-independent-review`. Creating a frozen release now would misrepresent validation as admission.
