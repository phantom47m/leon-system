"""
Neural Bridge — WebSocket communication layer between Left and Right Brain.

Left Brain runs BridgeServer (accepts connection from Right Brain).
Right Brain runs BridgeClient (connects to Left Brain).
Both use JSON envelopes with token auth, heartbeat, and request-response.
"""

import asyncio
import json
import logging
import os
import ssl
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from aiohttp import web, WSMsgType, ClientSession, WSServerHandshakeError

logger = logging.getLogger("leon.bridge")

# ── Message Types ────────────────────────────────────────
MSG_AUTH = "auth"
MSG_HEARTBEAT = "heartbeat"
MSG_TASK_DISPATCH = "task_dispatch"
MSG_TASK_STATUS = "task_status"
MSG_TASK_RESULT = "task_result"
MSG_MEMORY_SYNC = "memory_sync"
MSG_STATUS_REQUEST = "status_request"
MSG_STATUS_RESPONSE = "status_response"


# ── Bridge Message ───────────────────────────────────────

@dataclass
class BridgeMessage:
    """JSON envelope for all bridge communication."""
    type: str
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "BridgeMessage":
        data = json.loads(raw)
        return cls(
            type=data["type"],
            payload=data.get("payload", {}),
            id=data.get("id", uuid.uuid4().hex[:12]),
            timestamp=data.get("timestamp", time.time()),
        )


# ── Bridge Server (Left Brain) ──────────────────────────

class BridgeServer:
    """
    Runs on the Left Brain. Accepts one WebSocket connection from
    the Right Brain, authenticates it, then exchanges messages.
    """

    def __init__(self, config: dict):
        self.host = config.get("host", "0.0.0.0")
        self.port = config.get("port", 9100)
        self.token = os.environ.get("LEON_BRIDGE_TOKEN") or config.get("token", "")
        self.cert_path = config.get("cert_path", "")
        self.key_path = config.get("key_path", "")

        self._handlers: dict[str, Callable] = {}
        self._ws: Optional[web.WebSocketResponse] = None
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None and not self._ws.closed

    def on(self, msg_type: str, handler: Callable[..., Coroutine]):
        """Register a handler for a message type."""
        self._handlers[msg_type] = handler

    async def start(self):
        """Start the WebSocket server."""
        self._app = web.Application()
        self._app.router.add_get("/bridge", self._ws_handler)

        ssl_ctx = None
        if self.cert_path and self.key_path and Path(self.cert_path).exists():
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(self.cert_path, self.key_path)
            logger.info("Bridge TLS enabled")

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port, ssl_context=ssl_ctx)
        await site.start()
        logger.info(f"Bridge server listening on {self.host}:{self.port}")

    async def stop(self):
        """Shut down the bridge server."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._runner:
            await self._runner.cleanup()
        self._connected = False
        logger.info("Bridge server stopped")

    async def send(self, msg: BridgeMessage):
        """Send a message to the Right Brain (fire-and-forget)."""
        if not self.connected:
            logger.warning("Bridge send failed — not connected")
            return
        try:
            await self._ws.send_str(msg.to_json())
        except Exception as e:
            logger.error(f"Bridge send error: {e}")
            self._connected = False

    async def send_and_wait(self, msg: BridgeMessage, timeout: float = 30.0) -> Optional[BridgeMessage]:
        """Send a message and wait for a response with matching id."""
        if not self.connected:
            return None
        future = asyncio.get_event_loop().create_future()
        self._pending[msg.id] = future
        await self.send(msg)
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Bridge request timed out: {msg.type} ({msg.id})")
            self._pending.pop(msg.id, None)
            return None

    # ── Internal ─────────────────────────────────────────

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        # Auth: first message must be an auth message with correct token
        if self.token:
            try:
                first = await asyncio.wait_for(ws.receive(), timeout=10)
                if first.type != WSMsgType.TEXT:
                    await ws.close(code=4001, message=b"Expected auth message")
                    return ws
                auth_msg = BridgeMessage.from_json(first.data)
                if auth_msg.type != MSG_AUTH or auth_msg.payload.get("token") != self.token:
                    logger.warning("Bridge auth failed — bad token")
                    await ws.close(code=4003, message=b"Auth failed")
                    return ws
            except asyncio.TimeoutError:
                await ws.close(code=4002, message=b"Auth timeout")
                return ws

        # Connected
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = ws
        self._connected = True
        logger.info("Right Brain connected to bridge")

        # Send auth ack
        await ws.send_str(BridgeMessage(type=MSG_AUTH, payload={"status": "ok"}).to_json())

        # Start heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            async for raw_msg in ws:
                if raw_msg.type == WSMsgType.TEXT:
                    await self._handle_message(raw_msg.data)
                elif raw_msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except Exception as e:
            logger.error(f"Bridge handler error: {e}")
        finally:
            self._connected = False
            self._ws = None
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            logger.info("Right Brain disconnected from bridge")

        return ws

    async def _handle_message(self, raw: str):
        try:
            msg = BridgeMessage.from_json(raw)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Bridge bad message: {e}")
            return

        # Check if this is a response to a pending request
        if msg.id in self._pending:
            self._pending.pop(msg.id).set_result(msg)
            return

        # Heartbeat
        if msg.type == MSG_HEARTBEAT:
            return

        # Dispatch to registered handler
        handler = self._handlers.get(msg.type)
        if handler:
            try:
                await handler(msg)
            except Exception as e:
                logger.error(f"Bridge handler error for {msg.type}: {e}")
        else:
            logger.debug(f"No handler for bridge message type: {msg.type}")

    async def _heartbeat_loop(self):
        try:
            while self.connected:
                await self.send(BridgeMessage(type=MSG_HEARTBEAT))
                await asyncio.sleep(20)
        except asyncio.CancelledError:
            pass


# ── Bridge Client (Right Brain) ─────────────────────────

class BridgeClient:
    """
    Runs on the Right Brain. Connects to the Left Brain's BridgeServer,
    authenticates, and exchanges messages. Auto-reconnects on disconnect.
    """

    def __init__(self, config: dict):
        self.server_url = config.get("server_url", "wss://localhost:9100/bridge")
        self.token = os.environ.get("LEON_BRIDGE_TOKEN") or config.get("token", "")
        self.cert_path = config.get("cert_path", "")

        self._handlers: dict[str, Callable] = {}
        self._ws = None
        self._session: Optional[ClientSession] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._connected = False
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._connect_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None and not self._ws.closed

    def on(self, msg_type: str, handler: Callable[..., Coroutine]):
        """Register a handler for a message type."""
        self._handlers[msg_type] = handler

    async def start(self):
        """Start the client connection loop."""
        self._running = True
        self._session = ClientSession()
        self._connect_task = asyncio.create_task(self._connect_loop())
        logger.info(f"Bridge client starting — target: {self.server_url}")

    async def stop(self):
        """Disconnect and shut down."""
        self._running = False
        if self._connect_task:
            self._connect_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        self._connected = False
        logger.info("Bridge client stopped")

    async def send(self, msg: BridgeMessage):
        """Send a message to the Left Brain."""
        if not self.connected:
            logger.debug("Bridge client send skipped — not connected")
            return
        try:
            await self._ws.send_str(msg.to_json())
        except Exception as e:
            logger.error(f"Bridge client send error: {e}")
            self._connected = False

    async def send_and_wait(self, msg: BridgeMessage, timeout: float = 30.0) -> Optional[BridgeMessage]:
        """Send and wait for response with matching id."""
        if not self.connected:
            return None
        future = asyncio.get_event_loop().create_future()
        self._pending[msg.id] = future
        await self.send(msg)
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Bridge client request timed out: {msg.type} ({msg.id})")
            self._pending.pop(msg.id, None)
            return None

    # ── Internal ─────────────────────────────────────────

    async def _connect_loop(self):
        """Auto-reconnect loop with exponential backoff."""
        while self._running:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Bridge connection failed: {e}")

            if not self._running:
                break

            # Backoff
            logger.info(f"Bridge reconnecting in {self._reconnect_delay:.0f}s...")
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _connect_once(self):
        """Establish a single connection, authenticate, then listen."""
        ssl_ctx = None
        if self.server_url.startswith("wss://"):
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if self.cert_path and Path(self.cert_path).exists():
                ssl_ctx.load_verify_locations(self.cert_path)
            else:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        logger.info(f"Connecting to Left Brain at {self.server_url}")
        async with self._session.ws_connect(self.server_url, ssl=ssl_ctx, heartbeat=20) as ws:
            self._ws = ws

            # Authenticate
            if self.token:
                auth = BridgeMessage(type=MSG_AUTH, payload={"token": self.token})
                await ws.send_str(auth.to_json())

                # Wait for auth ack
                ack_raw = await asyncio.wait_for(ws.receive(), timeout=10)
                if ack_raw.type != WSMsgType.TEXT:
                    logger.error("Bridge auth failed — no text response")
                    return
                ack = BridgeMessage.from_json(ack_raw.data)
                if ack.type != MSG_AUTH or ack.payload.get("status") != "ok":
                    logger.error("Bridge auth rejected")
                    return

            self._connected = True
            self._reconnect_delay = 1.0  # Reset backoff
            logger.info("Connected to Left Brain")

            # Listen loop
            async for raw_msg in ws:
                if raw_msg.type == WSMsgType.TEXT:
                    await self._handle_message(raw_msg.data)
                elif raw_msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break

        # Connection closed
        self._connected = False
        self._ws = None
        logger.info("Disconnected from Left Brain")

    async def _handle_message(self, raw: str):
        try:
            msg = BridgeMessage.from_json(raw)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Bridge client bad message: {e}")
            return

        # Check pending responses
        if msg.id in self._pending:
            self._pending.pop(msg.id).set_result(msg)
            return

        # Heartbeat — no-op
        if msg.type == MSG_HEARTBEAT:
            return

        # Dispatch
        handler = self._handlers.get(msg.type)
        if handler:
            try:
                await handler(msg)
            except Exception as e:
                logger.error(f"Bridge client handler error for {msg.type}: {e}")
        else:
            logger.debug(f"No handler for bridge message type: {msg.type}")
