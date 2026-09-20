#!/bin/sh
set -eu

reward=1
test -f /workspace/submission/model_track_summary.json || reward=0
grep -q '"submitted": true' /workspace/submission/model_track_summary.json || reward=0
test "$(cat /workspace/submission/hello-from-model-track.txt)" = "hello from the ENG-023 model-track reference loop" || reward=0

printf '%s\n' "$reward" > /logs/verifier/reward.txt
printf '{"checked_outside_agent_environment":true}\n' > /logs/verifier/evidence.json
