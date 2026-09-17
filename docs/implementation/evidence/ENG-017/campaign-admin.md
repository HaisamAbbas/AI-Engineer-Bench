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
- No saved draft GET projection: draft editing requires the complete source manifest.
- Invalid-attempt inspection is read-only; it does not record a review decision.
- No new browser/independent human acceptance is claimed.
- ENG-015 and ENG-016 remain COMPLETE; this ticket remains IN_PROGRESS until
  these scope/acceptance limitations are reviewed.

Verification results are consolidated in `../ENG-018/prompt14-verification.md`.
