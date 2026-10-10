import asyncio

from backend.supervision import TaskSupervisor


def test_unexpected_worker_exit_is_fatal_and_visible():
    async def run():
        supervisor = TaskSupervisor()

        async def failed():
            raise RuntimeError("failure")

        supervisor.spawn("worker", failed())
        await asyncio.wait_for(supervisor.fatal.wait(), 1)
        assert supervisor.failed == {"worker":"RuntimeError"}
        await supervisor.close()

    asyncio.run(run())


def test_operator_cancellation_is_not_a_task_failure():
    async def run():
        supervisor = TaskSupervisor()
        supervisor.spawn("worker", asyncio.Event().wait(), group="processing")
        await supervisor.cancel_group("processing")
        assert not supervisor.fatal.is_set()
        assert not supervisor.tasks

    asyncio.run(run())
