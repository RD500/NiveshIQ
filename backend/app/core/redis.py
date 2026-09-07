import asyncio
import json
import logging
from typing import Dict, Any, Optional, Set, Callable, Awaitable
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


class InMemoryRedisManager:
    """
    Async In-Memory Redis Fallback Manager when live Redis server is unavailable.
    Provides key-value storage, hashes, and topic channel pub/sub pattern.
    """
    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        logger.info("Initialized In-Memory Redis Fallback Manager.")

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            val = self._store.get(key)
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            return str(val) if val is not None else None

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        async with self._lock:
            if isinstance(value, (dict, list)):
                self._store[key] = json.dumps(value)
            else:
                self._store[key] = str(value)
            return True

    async def hset(self, name: str, key: str, value: Any) -> int:
        async with self._lock:
            if name not in self._store or not isinstance(self._store[name], dict):
                self._store[name] = {}
            is_new = key not in self._store[name]
            self._store[name][key] = str(value) if not isinstance(value, (dict, list)) else json.dumps(value)
            return 1 if is_new else 0

    async def hget(self, name: str, key: str) -> Optional[str]:
        async with self._lock:
            hash_dict = self._store.get(name)
            if isinstance(hash_dict, dict):
                return hash_dict.get(key)
            return None

    async def hgetall(self, name: str) -> Dict[str, str]:
        async with self._lock:
            hash_dict = self._store.get(name)
            if isinstance(hash_dict, dict):
                return {k: str(v) for k, v in hash_dict.items()}
            return {}

    async def publish(self, channel: str, message: Any) -> int:
        msg_str = json.dumps(message) if isinstance(message, (dict, list)) else str(message)
        async with self._lock:
            queues = list(self._subscribers.get(channel, set()))

        count = 0
        for queue in queues:
            try:
                queue.put_nowait(msg_str)
                count += 1
            except asyncio.QueueFull:
                logger.warning(f"Subscriber queue full for channel: {channel}")
        return count

    async def subscribe(self, channel: str) -> "InMemoryPubSub":
        queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            if channel not in self._subscribers:
                self._subscribers[channel] = set()
            self._subscribers[channel].add(queue)
        return InMemoryPubSub(self, channel, queue)

    async def _unsubscribe(self, channel: str, queue: asyncio.Queue):
        async with self._lock:
            if channel in self._subscribers:
                self._subscribers[channel].discard(queue)
                if not self._subscribers[channel]:
                    del self._subscribers[channel]

    async def close(self):
        pass


class InMemoryPubSub:
    """PubSub handle wrapping queue for in-memory implementation."""
    def __init__(self, manager: InMemoryRedisManager, channel: str, queue: asyncio.Queue):
        self.manager = manager
        self.channel = channel
        self.queue = queue

    async def listen(self):
        while True:
            try:
                data = await self.queue.get()
                yield {"type": "message", "channel": self.channel, "data": data}
            except asyncio.CancelledError:
                break

    async def unsubscribe(self):
        await self.manager._unsubscribe(self.channel, self.queue)


class RedisClientWrapper:
    """
    Unified Redis Manager handling connection attempt to Redis server with seamless
    fallback to InMemoryRedisManager if connection fails.
    """
    def __init__(self):
        self.client: Optional[aioredis.Redis] = None
        self.fallback: Optional[InMemoryRedisManager] = None
        self.is_fallback: bool = False

    async def init_redis(self):
        try:
            r = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0
            )
            await r.ping()
            self.client = r
            self.is_fallback = False
            logger.info(f"Connected to Redis server at {settings.REDIS_URL}")
        except Exception as e:
            logger.warning(f"Could not connect to Redis server ({e}). Falling back to In-Memory Redis Client.")
            self.fallback = InMemoryRedisManager()
            self.is_fallback = True

    async def publish(self, channel: str, message: Any) -> int:
        payload = json.dumps(message) if isinstance(message, (dict, list)) else str(message)
        if self.is_fallback or not self.client:
            return await self.fallback.publish(channel, payload)
        try:
            return await self.client.publish(channel, payload)
        except Exception as e:
            logger.error(f"Redis publish error: {e}, using fallback")
            if not self.fallback:
                self.fallback = InMemoryRedisManager()
            self.is_fallback = True
            return await self.fallback.publish(channel, payload)

    async def subscribe(self, channel: str):
        if self.is_fallback or not self.client:
            return await self.fallback.subscribe(channel)
        try:
            pubsub = self.client.pubsub()
            await pubsub.subscribe(channel)
            return pubsub
        except Exception as e:
            logger.error(f"Redis subscribe error: {e}, using fallback")
            if not self.fallback:
                self.fallback = InMemoryRedisManager()
            self.is_fallback = True
            return await self.fallback.subscribe(channel)

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        payload = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if self.is_fallback or not self.client:
            return await self.fallback.set(key, payload, ex)
        try:
            return await self.client.set(key, payload, ex=ex)
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            if not self.fallback:
                self.fallback = InMemoryRedisManager()
            self.is_fallback = True
            return await self.fallback.set(key, payload, ex)

    async def get(self, key: str) -> Optional[str]:
        if self.is_fallback or not self.client:
            return await self.fallback.get(key)
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            if not self.fallback:
                self.fallback = InMemoryRedisManager()
            self.is_fallback = True
            return await self.fallback.get(key)

    async def hset(self, name: str, key: str, value: Any) -> int:
        payload = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if self.is_fallback or not self.client:
            return await self.fallback.hset(name, key, payload)
        try:
            return await self.client.hset(name, key, payload)
        except Exception as e:
            logger.error(f"Redis hset error: {e}")
            if not self.fallback:
                self.fallback = InMemoryRedisManager()
            self.is_fallback = True
            return await self.fallback.hset(name, key, payload)

    async def hgetall(self, name: str) -> Dict[str, str]:
        if self.is_fallback or not self.client:
            return await self.fallback.hgetall(name)
        try:
            return await self.client.hgetall(name)
        except Exception as e:
            logger.error(f"Redis hgetall error: {e}")
            if not self.fallback:
                self.fallback = InMemoryRedisManager()
            self.is_fallback = True
            return await self.fallback.hgetall(name)

    async def close(self):
        if self.client:
            await self.client.close()
        if self.fallback:
            await self.fallback.close()


redis_manager = RedisClientWrapper()
