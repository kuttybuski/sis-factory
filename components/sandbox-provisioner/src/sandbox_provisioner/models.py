import re
import time
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


class SandboxSpec(BaseModel):
    task_id: str
    image: str
    env: dict[str, str] = Field(default_factory=dict)
    memory_mb: int = Field(1024, ge=128, le=8192)
    cpus: float = Field(1.0, gt=0, le=4)
    pids: int = Field(256, ge=16, le=2048)
    workspace_mb: int = Field(2048, ge=64, le=16384)
    ttl_seconds: int = Field(14400, ge=60, le=86400)  # a sandbox older than this is destroyed even if nothing asked for it

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, v: str) -> str:
        if not TASK_ID.match(v):
            raise ValueError("task_id may only contain letters, digits, '.', '_' and '-'")
        return v

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        bad = [k for k in v if not re.fullmatch(r"SIS_[A-Z0-9_]{1,60}", k)]
        if bad:
            raise ValueError(f"only SIS_* variables may be set in a sandbox, not {bad}")  # no LD_PRELOAD, PATH, proxies...
        return v


class SandboxInfo(BaseModel):
    task_id: str
    name: str
    backend: str
    status: str
    created_at: float
    ttl_seconds: int

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) > self.created_at + self.ttl_seconds


class Backend(Protocol):
    name: str

    async def create(self, spec: SandboxSpec) -> SandboxInfo: ...
    async def delete(self, task_id: str) -> bool: ...
    async def get(self, task_id: str) -> SandboxInfo | None: ...
    async def logs(self, task_id: str, tail: int) -> list[str] | None: ...
    async def list(self) -> list[SandboxInfo]: ...
