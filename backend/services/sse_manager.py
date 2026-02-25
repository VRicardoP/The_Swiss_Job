"""Server-Sent Events manager with user-scoped channels.

Events: new_matches, search_complete, job_update
"""

import asyncio
import json
import uuid
from collections import defaultdict


class SSEManager:
    """Manages SSE connections per user. Each user can have multiple active connections."""

    def __init__(self):
        self._connections: dict[uuid.UUID, set[asyncio.Queue]] = defaultdict(set)

    async def subscribe(self, user_id: uuid.UUID) -> asyncio.Queue:
        """Create a new SSE subscription for a user. Returns a queue to read events from."""
        queue: asyncio.Queue = asyncio.Queue()
        self._connections[user_id].add(queue)
        return queue

    def unsubscribe(self, user_id: uuid.UUID, queue: asyncio.Queue) -> None:
        """Remove a subscription when the client disconnects."""
        queues = self._connections.get(user_id)
        if queues:
            queues.discard(queue)
            if not queues:
                del self._connections[user_id]

    async def broadcast_to_user(
        self, user_id: uuid.UUID, event_type: str, data: dict
    ) -> int:
        """Send an event to all active connections for a user.

        Returns the number of connections that received the event.
        """
        queues = self._connections.get(user_id)
        if not queues:
            return 0

        message = {"event": event_type, "data": data}
        sent = 0
        for queue in list(queues):
            try:
                queue.put_nowait(message)
                sent += 1
            except asyncio.QueueFull:
                # Drop message for slow consumers
                pass
        return sent

    async def broadcast_to_all(self, event_type: str, data: dict) -> int:
        """Send an event to all connected users. Returns total connections notified."""
        total = 0
        for user_id in list(self._connections.keys()):
            total += await self.broadcast_to_user(user_id, event_type, data)
        return total

    def get_active_connections(self) -> dict:
        """Return count of active connections per user (for monitoring)."""
        return {
            str(uid): len(queues)
            for uid, queues in self._connections.items()
            if queues
        }

    @staticmethod
    def format_sse(event_type: str, data: dict) -> str:
        """Format a message as an SSE text stream."""
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# Singleton instance
sse_manager = SSEManager()
