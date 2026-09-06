import asyncio
import json
import time
import logging
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.config import settings

logger = logging.getLogger(__name__)


class ConnectionMetaData:
    def __init__(self, websocket: WebSocket, client_id: str, user_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.user_id = user_id
        self.connected_at = time.time()
        self.last_pong_time = time.time()
        self.subscriptions: Set[str] = {"all"}  # Default subscribe to all symbols


class WebSocketManager:
    """
    Production-grade WebSocket Manager featuring:
    - Dynamic room/channel subscriptions
    - Monotonically increasing sequence IDs per message broadcast
    - Periodic heartbeat ping-pong (15s ping, 10s pong timeout)
    - Automatic stale connection pruning
    """
    def __init__(self):
        self.active_connections: Dict[str, ConnectionMetaData] = {}
        self._lock = asyncio.Lock()
        self._sequence_id: int = 1

    def _next_sequence_id(self) -> int:
        self._sequence_id += 1
        return self._sequence_id

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str):
        await websocket.accept()
        async with self._lock:
            meta = ConnectionMetaData(websocket, client_id, user_id)
            self.active_connections[client_id] = meta
        logger.info(f"WebSocket client connected: {client_id} (User: {user_id}). Total active: {len(self.active_connections)}")

        # Send welcome connection frame with initial sequence_id
        welcome_payload = {
            "type": "connection_ack",
            "client_id": client_id,
            "user_id": user_id,
            "sequence_id": self._next_sequence_id(),
            "timestamp": time.time(),
            "ping_interval": settings.PING_INTERVAL_SECONDS
        }
        await self.send_personal_message(client_id, welcome_payload)

    async def disconnect(self, client_id: str):
        async with self._lock:
            if client_id in self.active_connections:
                meta = self.active_connections.pop(client_id)
                try:
                    if meta.websocket.client_state == WebSocketState.CONNECTED:
                        await meta.websocket.close()
                except Exception as e:
                    logger.debug(f"Error closing socket for {client_id}: {e}")
                logger.info(f"WebSocket client disconnected: {client_id}. Total active: {len(self.active_connections)}")

    async def subscribe(self, client_id: str, symbol: str) -> Set[str]:
        async with self._lock:
            if client_id in self.active_connections:
                self.active_connections[client_id].subscriptions.add(symbol.upper())
                return self.active_connections[client_id].subscriptions
        return set()

    async def unsubscribe(self, client_id: str, symbol: str) -> Set[str]:
        async with self._lock:
            if client_id in self.active_connections:
                self.active_connections[client_id].subscriptions.discard(symbol.upper())
                return self.active_connections[client_id].subscriptions
        return set()

    async def record_pong(self, client_id: str):
        async with self._lock:
            if client_id in self.active_connections:
                self.active_connections[client_id].last_pong_time = time.time()

    async def send_personal_message(self, client_id: str, message: Dict[str, Any]):
        meta = self.active_connections.get(client_id)
        if not meta:
            return
        
        if "sequence_id" not in message:
            message["sequence_id"] = self._next_sequence_id()

        try:
            if meta.websocket.client_state == WebSocketState.CONNECTED:
                await meta.websocket.send_json(message)
        except Exception as e:
            logger.warning(f"Error sending message to client {client_id}: {e}")
            await self.disconnect(client_id)

    async def broadcast_tick(self, tick_data: Dict[str, Any]):
        """
        Broadcasts market tick data to subscribed clients.
        """
        if not self.active_connections:
            return

        seq_id = self._next_sequence_id()
        payload = {
            "type": "tick",
            "sequence_id": seq_id,
            "data": tick_data
        }

        symbol = tick_data.get("symbol", "").upper()
        dead_clients = []

        async with self._lock:
            connections = list(self.active_connections.items())

        for client_id, meta in connections:
            if "all" in meta.subscriptions or symbol in meta.subscriptions:
                try:
                    if meta.websocket.client_state == WebSocketState.CONNECTED:
                        await meta.websocket.send_json(payload)
                    else:
                        dead_clients.append(client_id)
                except Exception as e:
                    logger.debug(f"Broadcast error to {client_id}: {e}")
                    dead_clients.append(client_id)

        for cid in dead_clients:
            await self.disconnect(cid)

    async def send_heartbeat_pings(self):
        """
        Sends ping frames to all connected clients.
        """
        now = time.time()
        ping_payload = {
            "type": "ping",
            "sequence_id": self._next_sequence_id(),
            "timestamp": now
        }

        async with self._lock:
            connections = list(self.active_connections.values())

        for meta in connections:
            try:
                if meta.websocket.client_state == WebSocketState.CONNECTED:
                    await meta.websocket.send_json(ping_payload)
            except Exception as e:
                logger.debug(f"Ping send failed for {meta.client_id}: {e}")

    async def prune_stale_connections(self):
        """
        Prunes clients that failed to respond with pong within (ping_interval + pong_timeout).
        """
        now = time.time()
        timeout_threshold = settings.PING_INTERVAL_SECONDS + settings.PONG_TIMEOUT_SECONDS
        stale_clients = []

        async with self._lock:
            for client_id, meta in self.active_connections.items():
                if now - meta.last_pong_time > timeout_threshold:
                    logger.warning(
                        f"Pruning stale client {client_id}: last pong was {now - meta.last_pong_time:.1f}s ago "
                        f"(threshold: {timeout_threshold}s)"
                    )
                    stale_clients.append(client_id)

        for cid in stale_clients:
            await self.disconnect(cid)

    async def run_heartbeat_loop(self):
        """
        Background worker executing ping heartbeats and stale socket pruning.
        """
        logger.info("WebSocket Heartbeat and Pruner loop started.")
        while True:
            try:
                await self.send_heartbeat_pings()
                await asyncio.sleep(settings.PING_INTERVAL_SECONDS)
                await self.prune_stale_connections()
            except asyncio.CancelledError:
                logger.info("Heartbeat loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket heartbeat loop: {e}")
                await asyncio.sleep(5.0)


ws_manager = WebSocketManager()
