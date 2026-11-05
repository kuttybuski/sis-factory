"""How the gateway talks to the workflow: signals and queries over Temporal (tests swap in a fake with the same two methods).
Each task's workflow runs in its team's Temporal namespace, so every call names it."""

from typing import Any

from sif_contracts.namespaces import Namespaces


class TemporalOrchestrator:
    def __init__(self, address: str) -> None:
        self.namespaces = Namespaces(address)

    async def signal(self, workflow_id: str, name: str, arg: Any, namespace: str = "default") -> None:
        await (await self.namespaces.client(namespace)).get_workflow_handle(workflow_id).signal(name, arg)

    async def query(self, workflow_id: str, name: str, arg: Any = None, namespace: str = "default") -> Any:
        h = (await self.namespaces.client(namespace)).get_workflow_handle(workflow_id)
        return await (h.query(name, arg) if arg is not None else h.query(name))
