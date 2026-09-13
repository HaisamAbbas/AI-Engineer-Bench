FROM python:3.12.10-slim-bookworm@sha256:da14110a3ac1a4ca7a0e400447329d6208220d3211f172e0f7dffed7e1d1f89f

WORKDIR /workspace
COPY app.py /opt/aieb-fixture/app.py
RUN printf 'editable fixture repository\n' > /workspace/README.txt
CMD ["sleep", "infinity"]
