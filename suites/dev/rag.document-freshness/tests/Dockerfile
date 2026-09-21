FROM python:3.12.10-slim-bookworm@sha256:da14110a3ac1a4ca7a0e400447329d6208220d3211f172e0f7dffed7e1d1f89f
WORKDIR /tests
COPY test.sh /tests/test.sh
RUN chmod +x /tests/test.sh
CMD ["/tests/test.sh"]
