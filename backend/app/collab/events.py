import asyncio
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any


class RoomEventHub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, room_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=25)
        async with self._lock:
            self._rooms.setdefault(room_id, set()).add(queue)
        return queue

    async def unsubscribe(self, room_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            queues = self._rooms.get(room_id)
            if not queues:
                return
            queues.discard(queue)
            if not queues:
                self._rooms.pop(room_id, None)

    async def publish(
        self,
        room_id: str,
        event: str,
        *,
        actor_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        message = {
            "type": "room_event",
            "event": event,
            "room_id": room_id,
            "actor_id": actor_id,
            "payload": payload or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        async with self._lock:
            queues = list(self._rooms.get(room_id, set()))
        for queue in queues:
            _offer(queue, message)


def _offer(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(message)


@lru_cache
def get_room_event_hub() -> RoomEventHub:
    return RoomEventHub()
