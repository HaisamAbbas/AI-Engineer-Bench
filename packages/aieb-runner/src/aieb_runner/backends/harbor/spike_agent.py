"""Deterministic fake installed agent for lifecycle contract testing only."""

from __future__ import annotations

import asyncio

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class DeterministicFixtureAgent(BaseInstalledAgent):
    """Exercise installed-agent plumbing without making any model/provider call."""

    @staticmethod
    def name() -> str:
        return "aieb-deterministic-fixture"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command="printf 'aieb-deterministic-fixture 1.0.0\\n' > /installed-agent/version",
        )

    def get_version_command(self) -> str:
        return "cat /installed-agent/version"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction, context
        command = (
            "mkdir -p /workspace/submission && "
            "python -c \"import urllib.request; "
            "body=urllib.request.urlopen('http://application:8080/health', timeout=3).read(); "
            "assert body == b'ready'\" && "
            "printf '\\nagent-edited\\n' >> /workspace/README.txt && "
            "cp /workspace/README.txt /workspace/submission/edited-readme.txt && "
            "printf 'service-ready\\n' > /workspace/submission/new-file.txt"
        )
        await self.exec_as_agent(environment, command=command, cwd="/workspace")
        await self.exec_as_agent(
            environment,
            command=(
                "nohup sh -c \"sleep 20; printf 'late-write\\n' > "
                "/workspace/submission/post-deadline.txt\" "
                ">/dev/null 2>&1 </dev/null & "
                "printf '%s\\n' $! > /workspace/submission/contestant.pid"
            ),
            cwd="/workspace",
        )
        await asyncio.sleep(30)
