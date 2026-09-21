#!/bin/sh
set -eu
candidate=/workspace/candidate
test -f "$candidate/knowledge_service/backend.py"
test -f "$candidate/knowledge_service/server.py"
python - <<'PY'
from pathlib import Path
candidate = Path('/workspace/candidate/knowledge_service')
assert any(candidate.glob('*.py')), 'candidate contains no Python implementation'
print('development structural verifier passed')
PY
