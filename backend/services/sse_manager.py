"""Server-Sent Events manager with Redis pub/sub for cross-worker broadcast.

Events: new_matches, search_complete, job_update
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

SSE_CHANNEL_PREFIX = "sse:"
SSE_BROADCAST_CHANNEL = "sse:__broadcast__"


class SSEManager:
    """Manages SSE connections per user with Redis pub/sub for multi-worker support.

    Each worker maintains local asyncio.Queue instances per connection.
    Redis pub/sub bridges events across workers.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        queue_maxsize: int = 100,
    ):
        self._redis = redis_client
        self._queue_maxsize = queue_maxsize
        self._connections: dict[uuid.UUID, set[asyncio.Queue]] = defaultdict(set)
        self._pubsub: aioredis.client.PubSub | None = None
        self._listener_task: asyncio.Task | None = None
        self._dropped_events: int = 0

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the Redis pub/sub listener. Call during FastAPI lifespan startup."""
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe(f"{SSE_CHANNEL_PREFIX}*")
        self._listener_task = asyncio.create_task(
            self._listen(), name="sse-redis-listener"
        )
        logger.info("SSEManager started with Redis pub/sub")

    async def stop(self) -> None:
        """Stop the listener and clean up. Call during FastAPI lifespan shutdown."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.punsubscribe()
            await self._pubsub.aclose()
        self._connections.clear()
        logger.info("SSEManager stopped (dropped events: %d)", self._dropped_events)

    # --- Connection management ---

    async def subscribe(self, user_id: uuid.UUID) -> asyncio.Queue:
        """Create a new bounded SSE subscription for a user."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._connections[user_id].add(queue)
        return queue

    def unsubscribe(self, user_id: uuid.UUID, queue: asyncio.Queue) -> None:
        """Remove a subscription when the client disconnects."""
        queues = self._connections.get(user_id)
        if queues:
            queues.discard(queue)
            if not queues:
                del self._connections[user_id]

    # --- Broadcasting (publish to Redis) ---

    async def broadcast_to_user(
        self, user_id: uuid.UUID, event_type: str, data: dict
    ) -> None:
        """Publish an event for a specific user via Redis."""
        message = json.dumps({"event": event_type, "data": data})
        channel = f"{SSE_CHANNEL_PREFIX}{user_id}"
        await self._redis.publish(channel, message)

    async def broadcast_to_all(self, event_type: str, data: dict) -> None:
        """Publish a broadcast event to all connected users via Redis."""
        message = json.dumps({"event": event_type, "data": data})
        await self._redis.publish(SSE_BROADCAST_CHANNEL, message)

    # --- Local fanout ---

    def _fanout_to_local(self, user_id: uuid.UUID, message: dict) -> int:
        """Deliver a message to all local queues for a user. Returns count delivered."""
        queues = self._connections.get(user_id)
        if not queues:
            return 0

        sent = 0
        for queue in list(queues):
            try:
                queue.put_nowait(message)
                sent += 1
            except asyncio.QueueFull:
                # Drop oldest, enqueue new (TD-01 + TD-12)
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(message)
                    sent += 1
                except asyncio.QueueFull:
                    pass
                self._dropped_events += 1
                logger.warning(
                    "SSE queue overflow for user %s — dropped oldest event "
                    "(total drops: %d)",
                    user_id,
                    self._dropped_events,
                )
        return sent

    def _fanout_to_all_local(self, message: dict) -> int:
        """Deliver a broadcast message to all local connections."""
        total = 0
        for user_id in list(self._connections):
            total += self._fanout_to_local(user_id, message)
        return total

    # --- Redis listener ---

    async def _listen(self) -> None:
        """Background task: consume Redis pub/sub and fan out to local queues."""
        try:
            async for raw_message in self._pubsub.listen():
                if raw_message["type"] != "pmessage":
                    continue

                channel = raw_message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()

                try:
                    message = json.loads(raw_message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                if channel == SSE_BROADCAST_CHANNEL:
                    self._fanout_to_all_local(message)
                elif channel.startswith(SSE_CHANNEL_PREFIX):
                    uid_str = channel[len(SSE_CHANNEL_PREFIX) :]
                    try:
                        user_id = uuid.UUID(uid_str)
                    except ValueError:
                        continue
                    self._fanout_to_local(user_id, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SSE Redis listener crashed")

    # --- Monitoring ---

    def get_active_connections(self) -> dict:
        """Return count of active connections per user (for monitoring)."""
        return {
            str(uid): len(queues) for uid, queues in self._connections.items() if queues
        }

    @property
    def dropped_events(self) -> int:
        """Total number of events dropped due to queue overflow."""
        return self._dropped_events

    @staticmethod
    def format_sse(event_type: str, data: dict) -> str:
        """Format a message as an SSE text stream."""
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
