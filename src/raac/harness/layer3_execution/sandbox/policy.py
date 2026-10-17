"""Execution limits."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SandboxPolicy:
    timeout: int = 600
    nano_cpus: int = 2_000_000_000
    mem_limit: str = "4g"
    pids_limit: int = 256

    def container_options(self):
        return {
            "network_mode": "none",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "user": "1000:1000",
            "nano_cpus": self.nano_cpus,
            "mem_limit": self.mem_limit,
            "pids_limit": self.pids_limit,
        }

    def snapshot(self):
        return {**asdict(self), **self.container_options()}
