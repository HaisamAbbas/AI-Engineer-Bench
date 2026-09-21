#!/bin/sh
set -eu

reward=0
if python /tests/harness.py > /logs/verifier/harness.out 2>&1; then
  reward=1
fi

printf '%s\n' "$reward" > /logs/verifier/reward.txt
printf '{"checked_outside_agent_environment":true,"harness":"rag05-trusted-evaluator"}\n' > /logs/verifier/evidence.json
