FROM python:3.12.10-slim-bookworm@sha256:da14110a3ac1a4ca7a0e400447329d6208220d3211f172e0f7dffed7e1d1f89f

# The trusted RAG-05 evaluator snapshot (tests/rag05/) and harness.py are
# staged next to this Dockerfile by scripts/run_rag05_model_agent.py before
# launch; they are maintainer-owned trusted material, never candidate material.
COPY harness.py /tests/harness.py
COPY rag05/ /tests/rag05/
COPY test.sh /tests/test.sh
RUN chmod +x /tests/test.sh
CMD ["sleep", "infinity"]
