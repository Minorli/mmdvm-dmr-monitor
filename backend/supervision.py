"""Application task supervision: unexpected exits are visible and restartable."""

import asyncio
import logging

log = logging.getLogger(__name__)


class TaskSupervisor:
    def __init__(self):
        self.tasks: dict[str, tuple[str, asyncio.Task]] = {}
        self.failed: dict[str, str] = {}
        self.fatal = asyncio.Event()

    def spawn(self, name: str, coroutine, group: str = "application") -> asyncio.Task:
        if name in self.tasks:
            coroutine.close()
            raise RuntimeError(f"Task already running: {name}")
        task = asyncio.create_task(coroutine, name=name)
        self.tasks[name] = (group, task)
        task.add_done_callback(lambda finished: self._finished(name, finished))
        return task

    def _finished(self, name: str, task: asyncio.Task):
        if name not in self.tasks:
            return
        if task.cancelled():
            reason = "unexpected cancellation"
        else:
            error = task.exception()
            reason = type(error).__name__ if error else "unexpected exit"
        self.failed[name] = reason
        log.error("Background task %s failed: %s", name, reason)
        self.fatal.set()

    async def cancel_group(self, group: str):
        selected = [(name, task) for name, (task_group, task) in self.tasks.items() if task_group == group]
        for name, task in selected:
            self.tasks.pop(name, None)
            task.cancel()
        await asyncio.gather(*(task for _, task in selected), return_exceptions=True)

    async def close(self):
        for group in {group for group, _ in self.tasks.values()}:
            await self.cancel_group(group)
