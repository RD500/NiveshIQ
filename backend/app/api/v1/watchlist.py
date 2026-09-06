import json
import time
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from app.api.v1.auth import get_current_user
from app.core.redis import redis_manager
from app.engine.simulator import simulator
from app.engine.anomaly import anomaly_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["Watchlist"])


DEFAULT_USER_WATCHLIST = ["AAPL", "NVDA", "TSLA", "GOOGL", "AMZN", "MSFT", "BTC-USD"]


class AddSymbolRequest(BaseModel):
    symbol: str = Field(..., description="Stock or Crypto symbol, e.g., AAPL, NVDA, BTC-USD")


class SaveSnapshotRequest(BaseModel):
    snapshot_data: Dict[str, Dict[str, Any]] = Field(..., description="User session snapshot map by symbol")


async def get_user_watchlist_symbols(user_id: str) -> List[str]:
    key = f"watchlist:{user_id}"
    raw = await redis_manager.get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    
    await redis_manager.set(key, DEFAULT_USER_WATCHLIST)
    return DEFAULT_USER_WATCHLIST.copy()


async def set_user_watchlist_symbols(user_id: str, symbols: List[str]):
    key = f"watchlist:{user_id}"
    await redis_manager.set(key, symbols)


@router.get("")
async def get_watchlist(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Returns user's watchlist items enriched with real-time quotes, VWAP, and Attention Scores.
    """
    user_id = current_user["uid"]
    symbols = await get_user_watchlist_symbols(user_id)
    all_quotes = simulator.get_latest_quotes()

    items = []
    for sym in symbols:
        sym_upper = sym.upper()
        quote = all_quotes.get(sym_upper)
        if quote:
            items.append(quote)
        else:
           
            items.append({
                "symbol": sym_upper,
                "price": 100.0,
                "change": 0.0,
                "change_pct": 0.0,
                "attention_score": 10.0,
                "tier": "NORMAL"
            })

    return {
        "user_id": user_id,
        "count": len(items),
        "watchlist": items
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_symbol_to_watchlist(
    req: AddSymbolRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    user_id = current_user["uid"]
    symbol = req.symbol.upper().strip()
    
    symbols = await get_user_watchlist_symbols(user_id)
    if symbol not in symbols:
        symbols.append(symbol)
        await set_user_watchlist_symbols(user_id, symbols)
        
    return {
        "message": f"Symbol {symbol} added to watchlist",
        "watchlist": symbols
    }


@router.delete("/{symbol}")
async def remove_symbol_from_watchlist(
    symbol: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    
    user_id = current_user["uid"]
    sym_upper = symbol.upper().strip()

    symbols = await get_user_watchlist_symbols(user_id)
    if sym_upper in symbols:
        symbols.remove(sym_upper)
        await set_user_watchlist_symbols(user_id, symbols)

    return {
        "message": f"Symbol {sym_upper} removed from watchlist",
        "watchlist": symbols
    }


@router.get("/snapshot")
async def get_session_snapshot(current_user: Dict[str, Any] = Depends(get_current_user)):
   
    user_id = current_user["uid"]
    key = f"snapshot:{user_id}"
    raw = await redis_manager.get(key)
    if raw:
        try:
            snapshot = json.loads(raw)
            return {"user_id": user_id, "snapshot": snapshot}
        except Exception:
            pass

   
    current_quotes = simulator.get_latest_quotes()
    return {
        "user_id": user_id,
        "snapshot": {
            "timestamp": time.time(),
            "quotes": current_quotes
        }
    }


@router.post("/snapshot")
async def save_session_snapshot(
    req: SaveSnapshotRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
   
    user_id = current_user["uid"]
    key = f"snapshot:{user_id}"
    payload = {
        "timestamp": time.time(),
        "quotes": req.snapshot_data
    }
    await redis_manager.set(key, payload)
    return {"message": "Session snapshot saved successfully", "timestamp": payload["timestamp"]}


@router.get("/digest")
@router.get("/delta-digest")
async def get_delta_digest(
    since_timestamp: Optional[float] = Query(None, description="Optional Unix timestamp to calculate changes since"),
    current_user: Dict[str, Any] = Depends(get_current_user)
):

    
    user_id = current_user["uid"]
    key = f"snapshot:{user_id}"
    raw_snap = await redis_manager.get(key)
    
    previous_quotes = {}
    last_ts = since_timestamp

    if raw_snap:
        try:
            snap_obj = json.loads(raw_snap)
            previous_quotes = snap_obj.get("quotes", {})
            if not last_ts:
                last_ts = snap_obj.get("timestamp")
        except Exception:
            pass

    current_quotes = simulator.get_latest_quotes()

    digest = anomaly_engine.generate_delta_digest(
        previous_snapshot=previous_quotes,
        current_quotes=current_quotes,
        since_timestamp=last_ts
    )

    return digest
