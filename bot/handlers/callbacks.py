"""Inline keyboard callback handlers for GTOmniVid quality selection."""

import logging
import uuid
from aiogram import Router
from aiogram.types import CallbackQuery

from bot.keyboards.format_menu import FormatCallbackData, get_cached_formats
from core.queue import ConcurrencyController, JobRequest
from core.quota import QuotaExceededError, QuotaLedger

logger = logging.getLogger(__name__)

router = Router(name="callbacks_router")


@router.callback_query(FormatCallbackData.filter())
async def handle_format_selection(
    callback: CallbackQuery,
    callback_data: FormatCallbackData,
    concurrency_controller: ConcurrencyController,
    quota_ledger: QuotaLedger
) -> None:
    """Dispatches user selection to the concurrency FIFO queue."""
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id
    message_id = callback.message.message_id if callback.message else 0

    # 1. Handle Cancellation
    if callback_data.action == "cancel" or callback_data.idx == -1:
        if callback.message:
            await callback.message.edit_text("❌ <b>Media selection cancelled.</b>", parse_mode="HTML")
        await callback.answer("Cancelled")
        return

    # 2. Retrieve format from in-memory cache
    cached_entry = get_cached_formats(callback_data.key)
    if not cached_entry:
        await callback.answer("⚠️ Session expired. Please send the link again.", show_alert=True)
        return

    formats, webpage_url, media_title = cached_entry
    idx = callback_data.idx

    if idx < 0 or idx >= len(formats):
        await callback.answer("⚠️ Invalid format selection.", show_alert=True)
        return

    selected_format = formats[idx]

    # 3. Check User Quota
    can_download = await quota_ledger.can_user_download(user_id)
    if not can_download:
        raise QuotaExceededError("User daily download quota exceeded.")

    # 4. Construct JobRequest
    job_id = str(uuid.uuid4())
    job_request = JobRequest(
        job_id=job_id,
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        selected_format=selected_format,
        webpage_url=webpage_url,
        title=media_title
    )

    # 5. Enqueue into Semaphore(1) Concurrency Controller
    await concurrency_controller.enqueue(job_request)

    # 6. Inform user of their queue position
    queue_pos = concurrency_controller.get_queue_position(job_id)
    if queue_pos == 0:
        status_text = "⚡ <b>Starting download and processing...</b>"
    else:
        wait_seconds = queue_pos * 15
        status_text = (
            f"⏳ <b>You are in queue at position #{queue_pos}</b>\n"
            f"Estimated wait: <i>~{wait_seconds} seconds</i>."
        )

    if callback.message:
        await callback.message.edit_text(status_text, parse_mode="HTML")

    await callback.answer()
