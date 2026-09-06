import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Query, HTTPException, status

from app.engine.simulator import simulator, DEFAULT_TICKERS
from app.core.bigquery import tick_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["Market Data"])


SYMBOL_SEARCH_INDEX = {
    "AAPL": {"symbol": "AAPL", "name": "Apple Inc.", "asset_type": "Equity", "exchange": "NASDAQ"},
    "NVDA": {"symbol": "NVDA", "name": "NVIDIA Corporation", "asset_type": "Equity", "exchange": "NASDAQ"},
    "TSLA": {"symbol": "TSLA", "name": "Tesla, Inc.", "asset_type": "Equity", "exchange": "NASDAQ"},
    "GOOGL": {"symbol": "GOOGL", "name": "Alphabet Inc.", "asset_type": "Equity", "exchange": "NASDAQ"},
    "AMZN": {"symbol": "AMZN", "name": "Amazon.com, Inc.", "asset_type": "Equity", "exchange": "NASDAQ"},
    "MSFT": {"symbol": "MSFT", "name": "Microsoft Corporation", "asset_type": "Equity", "exchange": "NASDAQ"},
    "BTC-USD": {"symbol": "BTC-USD", "name": "Bitcoin USD", "asset_type": "Crypto", "exchange": "COINBASE"}
}


@router.get("/quotes")
async def get_all_quotes():
   
    quotes = simulator.get_latest_quotes()
    return {
        "count": len(quotes),
        "quotes": list(quotes.values())
    }


@router.get("/quote/{symbol}")
async def get_symbol_quote(symbol: str):
   
    sym_upper = symbol.upper().strip()
    quote = simulator.get_symbol_quote(sym_upper)
    if not quote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol '{sym_upper}' not found in active market engine."
        )

    analytics = tick_storage.get_symbol_analytics(sym_upper, window_seconds=3600)
    
    return {
        "quote": quote,
        "analytics": analytics
    }


@router.get("/history/{symbol}")
@router.get("/ticks/{symbol}")
async def get_tick_history(
    symbol: str,
    limit: int = Query(100, ge=1, le=1000, description="Max number of historical ticks to return"),
    start_time: Optional[float] = Query(None, description="Optional start unix timestamp filter")
):
    
    sym_upper = symbol.upper().strip()
    ticks = tick_storage.get_tick_history(sym_upper, limit=limit, start_time=start_time)
    
    return {
        "symbol": sym_upper,
        "count": len(ticks),
        "ticks": ticks
    }



@router.get("/search")
async def search_symbols(q: str = Query("", description="Query prefix or keyword, e.g. AAPL, Apple")):
    
    query = q.strip().upper()
    if not query:
        return {"results": list(SYMBOL_SEARCH_INDEX.values())}

    matches = []
    for sym, data in SYMBOL_SEARCH_INDEX.items():
        if query in sym or query in data["name"].upper():
            matches.append(data)

    return {
        "query": q,
        "count": len(matches),
        "results": matches
    }


@router.post("/simulate-anomaly")
async def trigger_simulated_anomaly(
    symbol: str = Query("BTC-USD", description="Symbol to trigger anomaly for"),
    jump_pct: float = Query(5.0, description="Percentage price surge"),
    vol_mult: float = Query(10.0, description="Volume multiplier")
):
    
    sym_upper = symbol.upper().strip()
    tick = simulator.trigger_anomaly(sym_upper, price_jump_pct=jump_pct, volume_multiplier=vol_mult)
    if not tick:
        raise HTTPException(status_code=404, detail=f"Symbol '{sym_upper}' not found.")
    
    return {
        "status": "anomaly_triggered",
        "symbol": sym_upper,
        "attention_score": tick.get("attention_score"),
        "tier": tick.get("tier"),
        "price": tick.get("price"),
        "tick": tick
    }
