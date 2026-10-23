"""Assemble one agent from a Registry role and the four harness layers.

Layer 1 owns the lifecycle (``AgentRunner``); Layer 2 context/memory policy and the
Layer 3 tool orchestrator are read from that runner so a role never runs with a
different policy than the one checkpointed. Layer 4 connectors are an optional,
currently interface-only extension point.
"""

from dataclasses import dataclass
from typing import Any

from raac.harness.layer1_lifecycle import AgentRunner
from raac.registry.models import LoadedRole


@dataclass(frozen=True)
class Agent:
    role: LoadedRole
    runner: AgentRunner
    connectors: tuple[Any, ...] = ()

    @classmethod
    def from_registry(cls, role_id: str, *, runner: AgentRunner, connectors=()):
        """Resolve ``role_id`` (ID or display name) in the runner's own Registry."""
        return cls(runner.registry.get(role_id), runner, tuple(connectors))

    @property
    def layer1(self):
        return self.runner

    @property
    def layer2(self):
        return {"context": self.runner.context_policy, "memory": self.runner.memory_policy}

    @property
    def layer3(self):
        return self.runner.tools

    @property
    def layer4(self):
        return self.connectors

    async def run(self, request: dict):
        """Run one lifecycle activation for this role; Layer 1 enforces its guards."""
        return await self.runner(self.role.spec.role, request)

    async def resume(self, invocation_id: str, *, approval=None):
        return await self.runner.resume(invocation_id, approval=approval)
