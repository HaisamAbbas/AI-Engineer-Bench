# V2-GAP-009 authorized model execution readiness

The model loop and provider adapters are implemented and fake Docker smoke is
available, but no real provider call is authorized or executed here. The
readiness audit is read-only:

```text
PYTHONPATH=packages/aieb-core/src \
python scripts/audit_model_track_authorization.py \
  --output docs/implementation/evidence/V2-GAP-009/model-track-readiness.json
```

It verifies the pinned reference loop, system prompt, tool schemas, separate
model-track cohort, concrete model identities, protocol digest, unsupported
control disclosure, credential presence, positive spend cap, authorization
record, real usage/model-identity evidence, and real provider-call evidence.
Credential values are never printed and the command never contacts a provider.

Current report: `ready_for_real_execution: false`. The plan is explicitly
`prepared-not-authorized`; it has no authorization record, credential, concrete
model, spend cap, protocol digest, or real provider-call evidence. The fake
provider remains a test double and cannot satisfy this gate.

Evidence references are now fail-closed: a readiness report can count only a
repository-local `evidence://path#sha256` file whose bytes exist and hash to
the declared digest. `private://` references remain explicitly unverifiable
to this local audit rather than being accepted because they are non-empty.
The checked-in plan has no such evidence and remains blocked.
