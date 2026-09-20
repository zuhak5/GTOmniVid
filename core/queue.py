"""Concurrency Controller & FIFO Job Queue for GTOmniVid.

Enforces strict Semaphore(1) execution to safeguard 1,024 MB physical RAM on GCP e2-micro.
Manages waiting FIFO queue, real-time queue position notifications, and cancellation tokens.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from config.settings import get_settings
from extractors.base import FormatOption

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Lifecycle status of a queued processing job."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class JobRequest:
    """Represents a validated media download & remux task request."""
    job_id: str
    user_id: int
    chat_id: int
    message_id: int
    selected_format: FormatOption
    webpage_url: str
    title: str


@dataclass
class QueuedJob:
    """Internal queue entry tracking future resolution and cancellation state."""
    request: JobRequest
    future: asyncio.Future
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    enqueued_at: float = field(default_factory=time.monotonic)
    status: JobStatus = JobStatus.QUEUED


class ConcurrencyController:
    """Manages serial job execution and waiting queue with live position reporting."""

    def __init__(self, concurrency_limit: Optional[int] = None) -> None:
        settings = get_settings()
        limit = concurrency_limit or settings.CONCURRENCY_LIMIT
        self.semaphore = asyncio.Semaphore(limit)
        self.queue: asyncio.Queue[QueuedJob] = asyncio.Queue()
        self._waiting_list: List[QueuedJob] = []
        self._active_job: Optional[QueuedJob] = None
        self._all_jobs: Dict[str, QueuedJob] = {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._handler: Optional[Callable[[JobRequest, asyncio.Event], Awaitable[Any]]] = None

    def set_handler(self, handler: Callable[[JobRequest, asyncio.Event], Awaitable[Any]]) -> None:
        """Registers the media pipeline worker handler."""
        self._handler = handler

    async def start(self) -> None:
        """Starts the FIFO queue worker loop."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="gtomnivid-queue-worker")
        logger.info("ConcurrencyController started (Semaphore=1).")

    async def stop(self) -> None:
        """Stops the worker and cancels all pending jobs."""
        if not self._running:
            return
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        # Cancel remaining queued jobs
        for job in list(self._waiting_list):
            job.status = JobStatus.CANCELLED
            job.cancel_event.set()
            if not job.future.done():
                job.future.cancel()
        self._waiting_list.clear()
        logger.info("ConcurrencyController stopped.")

    async def enqueue(self, request: JobRequest) -> asyncio.Future:
        """Enqueues a job and returns an awaitable Future."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        queued_job = QueuedJob(request=request, future=future)

        self._all_jobs[request.job_id] = queued_job
        self._waiting_list.append(queued_job)
        await self.queue.put(queued_job)

        logger.info(
            "Job enqueued: %s (user=%d, format=%s, queue_pos=%d)",
            request.job_id,
            request.user_id,
            request.selected_format.format_id,
            len(self._waiting_list)
        )
        return future

    def get_queue_position(self, job_id: str) -> int:
        """Returns 1-based queue position (1 means next in line), or 0 if currently processing."""
        if self._active_job and self._active_job.request.job_id == job_id:
            return 0

        for idx, job in enumerate(self._waiting_list, start=1):
            if job.request.job_id == job_id:
                return idx
        return -1

    def cancel_job(self, job_id: str) -> bool:
        """Cancels a job if it is queued or currently running."""
        job = self._all_jobs.get(job_id)
        if not job:
            return False

        logger.info("Cancelling job %s (current status: %s)", job_id, job.status)
        job.status = JobStatus.CANCELLED
        job.cancel_event.set()

        if job in self._waiting_list:
            self._waiting_list.remove(job)

        if not job.future.done():
            job.future.cancel()

        return True

    def cancel_user_active_job(self, user_id: int) -> Optional[str]:
        """Cancels any queued or processing job belonging to the specified user."""
        # Check waiting queue
        for job in list(self._waiting_list):
            if job.request.user_id == user_id:
                job_id = job.request.job_id
                self.cancel_job(job_id)
                return job_id

        # Check currently active job
        if self._active_job and self._active_job.request.user_id == user_id:
            job_id = self._active_job.request.job_id
            self.cancel_job(job_id)
            return job_id

        return None

    @property
    def waiting_count(self) -> int:
        """Returns number of jobs currently waiting in the FIFO queue."""
        return len(self._waiting_list)

    @property
    def is_busy(self) -> bool:
        """Returns True if a job is currently executing."""
        return self._active_job is not None

    async def _worker_loop(self) -> None:
        """Serial worker loop processing jobs through the semaphore."""
        while self._running:
            try:
                job = await self.queue.get()
                if job in self._waiting_list:
                    self._waiting_list.remove(job)

                if job.cancel_event.is_set() or job.status == JobStatus.CANCELLED:
                    self._all_jobs.pop(job.request.job_id, None)
                    self.queue.task_done()
                    continue

                async with self.semaphore:
                    self._active_job = job
                    job.status = JobStatus.PROCESSING
                    logger.info("Started processing job: %s", job.request.job_id)

                    try:
                        if self._handler:
                            result = await self._handler(job.request, job.cancel_event)
                            job.status = JobStatus.COMPLETED
                            if not job.future.done():
                                job.future.set_result(result)
                        else:
                            raise RuntimeError("No worker handler registered with ConcurrencyController.")
                    except asyncio.CancelledError:
                        job.status = JobStatus.CANCELLED
                        if not job.future.done():
                            job.future.cancel()
                        logger.info("Job cancelled during execution: %s", job.request.job_id)
                    except Exception as err:
                        job.status = JobStatus.FAILED
                        if not job.future.done():
                            job.future.set_exception(err)
                        logger.exception("Job %s failed: %s", job.request.job_id, err)
                    finally:
                        self._all_jobs.pop(job.request.job_id, None)
                        self._active_job = None
                        self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.exception("Unexpected error in worker loop: %s", err)
