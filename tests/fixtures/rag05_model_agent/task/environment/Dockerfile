FROM python:3.12.10-slim-bookworm@sha256:da14110a3ac1a4ca7a0e400447329d6208220d3211f172e0f7dffed7e1d1f89f

# workspace.tar.gz is staged next to this Dockerfile by
# scripts/run_rag05_model_agent.py before launch; it contains the real
# (deliberately broken) task repository from suites/real/rag.corpus-index-drift.
WORKDIR /workspace
ADD workspace.tar.gz /workspace
RUN mkdir -p /workspace/submission
CMD ["sleep", "infinity"]
