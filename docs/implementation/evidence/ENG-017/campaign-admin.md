# ENG-017 — staging campaign administration

Date: 2026-09-17. Branch: `prompt-14-eng017-eng018`.

Implemented draft/create/freeze, exact matrix preview, estimated budget reservation,
start/pause/resume/cancel, completion classification, progress, and invalid-attempt
inspection. Pausing prevents new claims; cancellation retains evidence and drains
leased work. Reservations are estimated accounting, not provider holds.

The web admin and progress pages consume authoritative API responses, refetch after
writes, require cancellation/freeze acknowledgements, and never edit frozen plans.
Draft updates carry If-Match; errors retain server request IDs.

## Acceptance limitations

- No campaign-list API: the UI opens known IDs or creates a draft.
- No new browser/independent human acceptance is claimed.
- ENG-015 and ENG-016 remain COMPLETE; this ticket remains IN_PROGRESS until
  these scope/acceptance limitations are reviewed.

## Gap closure update (2026-09-17, branch `prompt-14-gap-closure`, PR #3)

Two earlier limitations are closed on the stacked gap-closure branch:

- Saved-draft retrieval: campaign state reads now return the saved,
  manifest-validated draft for draft campaigns, and the admin editor prefills
  from it. If-Match revision-controlled saves are unchanged. Backend assertion
  added to `test_patch_with_stale_if_match_is_412`.
- Invalidity review: `POST /v1/campaigns/{campaign_id}/invalid-attempts/{attempt_id}/reviews`
  records append-only, idempotency-keyed, rationale-required decisions
  (reviewer/administrator; operator requests are 403). Recorded outcomes are
  never mutated. Regression: `tests/test_invalidity_review.py`.

Verification for both is consolidated in `../ENG-018/prompt14-verification.md`
and PR #3.

Verification results are consolidated in `../ENG-018/prompt14-verification.md`.
