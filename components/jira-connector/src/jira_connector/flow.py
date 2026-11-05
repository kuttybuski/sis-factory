from typing import Any

from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from sis_contracts.namespaces import DEFAULT, Namespaces
from sis_contracts.workflow import ORCHESTRATOR_QUEUE, WORKFLOW, TaskInput, workflow_id


class TemporalFlow:
    """Starts and signals ticket workflows, each in its team's Temporal namespace."""

    def __init__(self, namespaces: Namespaces) -> None:
        self.namespaces = namespaces
        self.known: set[str] = {DEFAULT}  # every team namespace, refreshed each poll; running() counts across all of them

    async def start(self, key: str, inp: TaskInput) -> bool:
        client = await self.namespaces.ready(inp.namespace or DEFAULT)  # a team added a moment ago may not have its namespace yet
        try:
            await client.start_workflow(WORKFLOW, inp, id=workflow_id(key), task_queue=ORCHESTRATOR_QUEUE, id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                                        id_conflict_policy=WorkflowIDConflictPolicy.FAIL)
            return True
        except WorkflowAlreadyStartedError:
            return False

    async def signal(self, key: str, name: str, arg: Any, namespace: str = DEFAULT) -> bool:
        # A ticket started before its team had its own namespace is still running in default.
        for ns in dict.fromkeys((namespace or DEFAULT, DEFAULT)):
            handle = (await self.namespaces.client(ns)).get_workflow_handle(workflow_id(key))
            try:
                if (await handle.describe()).status != WorkflowExecutionStatus.RUNNING:
                    continue
                await handle.signal(name, arg)
                return True
            except RPCError:
                continue
        return False

    async def running(self) -> int:
        total = 0
        for ns in self.known:
            try:
                client = await self.namespaces.client(ns)
                total += sum([1 async for _ in client.list_workflows(f'WorkflowType="{WORKFLOW}" AND ExecutionStatus="Running"')])
            except RPCError:
                pass  # a namespace not registered yet has nothing running
        return total
