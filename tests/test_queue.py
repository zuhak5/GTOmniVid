"""Unit tests for ConcurrencyController and FIFO Job Queue (core/queue.py)."""

import asyncio
import pytest
from extractors.base import FormatOption, FormatTier
from core.queue import ConcurrencyController, JobRequest, JobStatus


def _create_dummy_request(job_id: str, user_id: int) -> JobRequest:
    return JobRequest(
        job_id=job_id,
        user_id=user_id,
        chat_id=1000 + user_id,
        message_id=2000 + user_id,
        selected_format=FormatOption(
            format_id="136+140",
            tier=FormatTier.P720,
            resolution_label="720p HD",
            estimated_size_bytes=10 * 1024 * 1024
        ),
        webpage_url=f"https://youtube.com/watch?v={job_id}",
        title=f"Video {job_id}"
    )


@pytest.mark.asyncio
async def test_concurrency_controller_semaphore_1_and_fifo():
    """Verify strictly 1 active job runs at a time and execution is FIFO."""
    controller = ConcurrencyController(concurrency_limit=1)
    execution_order = []
    active_count = 0
    max_active = 0

    async def mock_handler(request: JobRequest, cancel_event: asyncio.Event):
        nonlocal active_count, max_active
        active_count += 1
        max_active = max(max_active, active_count)
        execution_order.append(f"start_{request.job_id}")
        await asyncio.sleep(0.05)  # Simulate processing
        execution_order.append(f"done_{request.job_id}")
        active_count -= 1
        return f"result_{request.job_id}"

    controller.set_handler(mock_handler)
    await controller.start()

    req1 = _create_dummy_request("job1", user_id=1)
    req2 = _create_dummy_request("job2", user_id=2)
    req3 = _create_dummy_request("job3", user_id=3)

    fut1 = await controller.enqueue(req1)
    fut2 = await controller.enqueue(req2)
    fut3 = await controller.enqueue(req3)

    # Position checks
    assert controller.get_queue_position("job1") in (0, 1)  # 0 if already popped into active
    assert controller.get_queue_position("job2") in (1, 2)
    assert controller.get_queue_position("job3") in (2, 3)

    res1 = await fut1
    res2 = await fut2
    res3 = await fut3

    assert res1 == "result_job1"
    assert res2 == "result_job2"
    assert res3 == "result_job3"

    # Verify concurrency never exceeded 1
    assert max_active == 1
    # Verify strict FIFO order
    assert execution_order == ["start_job1", "done_job1", "start_job2", "done_job2", "start_job3", "done_job3"]

    await controller.stop()


@pytest.mark.asyncio
async def test_job_cancellation():
    """Verify queued job can be cancelled before and during execution."""
    controller = ConcurrencyController(concurrency_limit=1)

    async def slow_handler(request: JobRequest, cancel_event: asyncio.Event):
        for _ in range(20):
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            await asyncio.sleep(0.02)
        return "finished"

    controller.set_handler(slow_handler)
    await controller.start()

    req1 = _create_dummy_request("active_job", user_id=1)
    req2 = _create_dummy_request("queued_job", user_id=2)

    fut1 = await controller.enqueue(req1)
    fut2 = await controller.enqueue(req2)

    # Cancel req2 while waiting in queue
    cancelled = controller.cancel_job("queued_job")
    assert cancelled is True

    with pytest.raises(asyncio.CancelledError):
        await fut2

    # Cancel req1 user-level active job
    cancelled_id = controller.cancel_user_active_job(user_id=1)
    assert cancelled_id == "active_job"

    with pytest.raises(asyncio.CancelledError):
        await fut1

    await controller.stop()
