import json
import uuid
import time
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status

from app.core.firebase import verify_auth_token
from app.engine.ws_manager import ws_manager
from app.engine.simulator import simulator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/watchlist")
async def websocket_watchlist_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None)
):
   
    if not client_id:
        client_id = f"client_{uuid.uuid4().hex[:8]}"

    
    user_id = "dev_trader_01"
    if token:
        try:
            user_info = verify_auth_token(token)
            user_id = user_info.get("uid", "dev_trader_01")
        except Exception as e:
            logger.warning(f"WebSocket auth warning: {e}. Defaulting to dev user.")

    await ws_manager.connect(websocket, client_id, user_id)

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                data = json.loads(raw_text)
                action = data.get("action", "").lower()

                if action == "ping":
                    await ws_manager.record_pong(client_id)
                    await ws_manager.send_personal_message(client_id, {
                        "type": "pong",
                        "timestamp": time.time()
                    })

                elif action == "pong":
                    await ws_manager.record_pong(client_id)

                elif action == "subscribe":
                    symbol = data.get("symbol", "all").upper()
                    subs = await ws_manager.subscribe(client_id, symbol)
                    await ws_manager.send_personal_message(client_id, {
                        "type": "subscribed",
                        "symbol": symbol,
                        "active_subscriptions": list(subs)
                    })

                elif action == "unsubscribe":
                    symbol = data.get("symbol", "").upper()
                    subs = await ws_manager.unsubscribe(client_id, symbol)
                    await ws_manager.send_personal_message(client_id, {
                        "type": "unsubscribed",
                        "symbol": symbol,
                        "active_subscriptions": list(subs)
                    })

                elif action == "snapshot":
                    quotes = list(simulator.get_latest_quotes().values())
                    await ws_manager.send_personal_message(client_id, {
                        "type": "snapshot",
                        "timestamp": time.time(),
                        "quotes": quotes
                    })

                else:
                    await ws_manager.send_personal_message(client_id, {
                        "type": "error",
                        "message": f"Unknown action '{action}'"
                    })

            except json.JSONDecodeError:
                await ws_manager.send_personal_message(client_id, {
                    "type": "error",
                    "message": "Invalid JSON format"
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected gracefully: {client_id}")
    except Exception as e:
        logger.error(f"WebSocket client connection error ({client_id}): {e}")
    finally:
        await ws_manager.disconnect(client_id)
