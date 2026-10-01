from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from calliope.auth import current_user
from calliope.config import settings
from calliope.events.bus import event_bus

router = APIRouter()


@router.get("")
async def events_stream(request: Request):
    async def generator():
        # Atomic subscribe+snapshot: no event lands in both the backlog and
        # the queue (duplicates), and none is lost between them.
        q, snapshot = await event_bus.subscribe(backlog=20)
        try:
            for event in snapshot:
                if settings.auth_enabled and not await run_in_threadpool(current_user, request):
                    return
                yield event_bus.format_sse(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    if settings.auth_enabled and not await run_in_threadpool(current_user, request):
                        break
                    yield event_bus.format_sse(event)
                except asyncio.TimeoutError:
                    if settings.auth_enabled and not await run_in_threadpool(current_user, request):
                        break
                    yield ": keepalive\n\n"
        finally:
            await event_bus.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
