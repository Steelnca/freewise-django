
"""
In-process notification hub for live updates.

This is the MVP push layer:
- very fast
- simple to reason about
- easy to replace later with Redis pub/sub if you scale to many workers
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from queue import Queue
from typing import Any

from django.utils import timezone


@dataclass(frozen=True)
class NotificationPayload:
    id: int
    type: str
    title: str
    message: str
    link: str
    is_read: bool
    created_at: str


class NotificationHub:
    _lock = threading.Lock()
    _subscribers: dict[int, set[Queue]] = defaultdict(set)

    @classmethod
    def subscribe(cls, account_id: int) -> Queue:
        queue: Queue = Queue(maxsize=100)
        with cls._lock:
            cls._subscribers[account_id].add(queue)
        return queue

    @classmethod
    def unsubscribe(cls, account_id: int, queue: Queue) -> None:
        with cls._lock:
            subscribers = cls._subscribers.get(account_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                cls._subscribers.pop(account_id, None)

    @classmethod
    def publish(cls, account_id: int, payload: dict[str, Any]) -> None:
        with cls._lock:
            subscribers = list(cls._subscribers.get(account_id, set()))

        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except Exception:
                continue


def serialize_notification(notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "link": notification.link,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat(),
    }