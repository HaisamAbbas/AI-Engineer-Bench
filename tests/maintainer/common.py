"""Shared maintainer-only HTTP process harnesses for development task admission."""
from __future__ import annotations
import json, os, socket, subprocess, sys, time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return int(sock.getsockname()[1])

def request(base: str, method: str, path: str, body: object | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(base + path, data=data, method=method, headers={"Content-Type":"application/json"}), timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"error": body.decode("utf-8", errors="replace")}

class CandidateProcess:
    def __init__(self, repo: Path, module: str, env: dict[str, str] | None = None) -> None:
        self.port = free_port(); self.base = f"http://127.0.0.1:{self.port}"
        environment = dict(os.environ); environment.update(env or {})
        self.process = subprocess.Popen([sys.executable, "-m", module, "--port", str(self.port)], cwd=repo, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    def __enter__(self) -> "CandidateProcess":
        for _ in range(50):
            if self.process.poll() is not None:
                message = self.process.stderr.read() if self.process.stderr else ""
                if self.process.stderr: self.process.stderr.close()
                raise RuntimeError(f"candidate process stopped: {message}")
            try:
                if request(self.base, "GET", "/health")[0] == 200: return self
            except URLError: time.sleep(.04)
        raise RuntimeError("candidate process did not become ready")
    def __exit__(self, *args: object) -> None:
        self.process.terminate()
        try: self.process.wait(timeout=3)
        except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)
        if self.process.stderr: self.process.stderr.close()
        # Windows can briefly retain the subprocess current-directory handle
        # after wait(); callers must be able to remove fresh allocations.
        time.sleep(.08)
