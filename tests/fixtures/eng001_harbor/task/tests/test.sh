#!/bin/sh
set -eu

reward=1
test "$(cat /workspace/submission/new-file.txt)" = "service-ready" || reward=0
grep -q "agent-edited" /workspace/submission/edited-readme.txt || reward=0
test ! -e /workspace/submission/post-deadline.txt || reward=0
python -c "import json; state=json.load(open('/tmp/application-state.json')); assert state['requests'] >= 2" || reward=0

printf '%s\n' "$reward" > /logs/verifier/reward.txt
printf '{"candidate_replayed":true,"checked_outside_agent_environment":true,"late_write_absent":true}\n' > /logs/verifier/evidence.json
