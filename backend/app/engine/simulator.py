import asyncio
import random
import time
import logging
from typing import Dict, Any, List, Optional

from app.core.config import settings
from app.core.redis import redis_manager
from app.core.bigquery import tick_storage
from app.engine.anomaly import anomaly_engine

logger = logging.getLogger(__name__)


# Default Tickers & Baseline Configuration
DEFAULT_TICKERS = {
    "AAPL": {"base_price": 225.50, "volatility": 0.0008, "base_volume": 1200},
    "NVDA": {"base_price": 128.40, "volatility": 0.0018, "base_volume": 3500},
    "TSLA": {"base_price": 214.20, "volatility": 0.0022, "base_volume": 2800},
    "GOOGL": {"base_price": 178.60, "volatility": 0.0007, "base_volume": 1500},
    "AMZN": {"base_price": 186.50, "volatility": 0.0009, "base_volume": 1800},
    "MSFT": {"base_price": 448.10, "volatility": 0.0006, "base_volume": 1100},
    "BTC-USD": {"base_price": 64200.00, "volatility": 0.0025, "base_volume": 15}
}


class MarketTickSimulator:
    """
    Real-Time Realistic Market Tick Generator.
    Emits continuous price updates, volume Z-scores, and anomaly events.
    """
    def __init__(self):
        self._sequence_id: int = 1000
        self._running: bool = False
        self._state: Dict[str, Dict[str, Any]] = {}
        self._init_ticker_states()

    def _init_ticker_states(self):
        now = time.time()
        for symbol, cfg in DEFAULT_TICKERS.items():
            base = cfg["base_price"]
            self._state[symbol] = {
                "symbol": symbol,
                "price": base,
                "open_price": base,
                "high": base * 1.002,
                "low": base * 0.998,
                "bid": round(base - 0.02, 2 if base < 1000 else 1),
                "ask": round(base + 0.02, 2 if base < 1000 else 1),
                "last_price": base,
                "accumulated_volume": 50000.0,
                "accumulated_pv": 50000.0 * base,
                "vwap": base,
                "volatility": cfg["volatility"],
                "base_volume": cfg["base_volume"],
                "attention_score": 15.0,
                "tier": "NORMAL",
                "last_update": now
            }

    def get_latest_quotes(self) -> Dict[str, Dict[str, Any]]:
        return self._state.copy()

    def get_symbol_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._state.get(symbol.upper())

    def _generate_tick_for_symbol(self, symbol: str) -> Dict[str, Any]:
        state = self._state[symbol]
        self._sequence_id += 1
        now = time.time()

        # Random Walk / Geometric Brownian step
        volatility = state["volatility"]
        base_vol = state["base_volume"]

        # 5% chance of sudden volume burst / anomaly event
        is_spike = random.random() < 0.05
        vol_multiplier = random.uniform(3.5, 7.0) if is_spike else random.uniform(0.5, 1.8)

        # Price step percentage
        price_step_pct = random.gauss(0.00005 if is_spike else 0.0, volatility * (2.5 if is_spike else 1.0))
        new_price = max(0.01, state["price"] * (1 + price_step_pct))

        # Precision handling
        decimals = 2 if new_price < 1000 else 1
        new_price = round(new_price, decimals)

        # Spread
        spread = round(max(0.01, new_price * 0.0002), 2)
        bid = round(new_price - (spread / 2), decimals)
        ask = round(new_price + (spread / 2), decimals)

        # Volume for this tick
        tick_volume = round(base_vol * vol_multiplier, 2)

        # High/Low update
        high = max(state["high"], new_price)
        low = min(state["low"], new_price)

        # VWAP calculation
        state["accumulated_volume"] += tick_volume
        state["accumulated_pv"] += new_price * tick_volume
        vwap = round(state["accumulated_pv"] / max(1.0, state["accumulated_volume"]), decimals)

        # Velocity pct over last step
        velocity_pct = ((new_price - state["price"]) / state["price"]) * 100.0

        # Update in-memory state
        state["last_price"] = state["price"]
        state["price"] = new_price
        state["bid"] = bid
        state["ask"] = ask
        state["high"] = high
        state["low"] = low
        state["vwap"] = vwap
        state["last_update"] = now

        # Compute analytics & Attention score
        z_score = tick_storage.calculate_volume_zscore(symbol, window_seconds=1800)
        
        # Check breakout
        breakout = (new_price >= state["high"] and is_spike) or (new_price <= state["low"] and is_spike)
        
        att_res = anomaly_engine.calculate_attention_score(
            volume_z_score=z_score,
            price_velocity_pct=velocity_pct,
            volatility_ratio=1.8 if is_spike else 1.0,
            breakout_flag=breakout
        )
        
        state["attention_score"] = att_res["attention_score"]
        state["tier"] = att_res["tier"]

        tick_payload = {
            "sequence_id": self._sequence_id,
            "timestamp": now,
            "symbol": symbol,
            "price": new_price,
            "change": round(new_price - state["open_price"], decimals),
            "change_pct": round(((new_price - state["open_price"]) / state["open_price"]) * 100.0, 2),
            "volume": tick_volume,
            "bid": bid,
            "ask": ask,
            "high": high,
            "low": low,
            "vwap": vwap,
            "z_score": z_score,
            "attention_score": att_res["attention_score"],
            "tier": att_res["tier"],
            "is_anomaly": att_res["attention_score"] >= 70.0 or breakout
        }

        return tick_payload

    async def run_simulation_loop(self):
        self._running = True
        logger.info("Market Tick Simulator loop started.")

        while self._running:
            try:
                ticks_to_store = []
                for symbol in DEFAULT_TICKERS.keys():
                    tick = self._generate_tick_for_symbol(symbol)
                    ticks_to_store.append(tick)

                    # Update quote in Redis hash
                    await redis_manager.hset("quotes", symbol, tick)

                    # Publish individual symbol tick channel
                    await redis_manager.publish(f"ticks:{symbol}", tick)

                # Batch insert to tick storage
                tick_storage.insert_ticks(ticks_to_store)

                # Broadcast all ticks channel
                await redis_manager.publish("ticks:all", ticks_to_store)

            except Exception as e:
                logger.error(f"Error in simulation loop: {e}")

            await asyncio.sleep(settings.TICK_INTERVAL_SECONDS)

    def trigger_anomaly(self, symbol: str, price_jump_pct: float = 3.5, volume_multiplier: float = 8.0) -> Dict[str, Any]:
        """
        Manually triggers a massive anomaly event for testing real-time dynamic reordering.
        """
        if symbol not in self._state:
            return {}
        
        state = self._state[symbol]
        self._sequence_id += 1
        now = time.time()
        
        new_price = round(state["price"] * (1 + (price_jump_pct / 100.0)), 2 if state["price"] < 1000 else 1)
        tick_volume = round(state["base_volume"] * volume_multiplier, 2)
        
        state["last_price"] = state["price"]
        state["price"] = new_price
        state["high"] = max(state["high"], new_price)
        state["accumulated_volume"] += tick_volume
        state["accumulated_pv"] += new_price * tick_volume
        state["vwap"] = round(state["accumulated_pv"] / max(1.0, state["accumulated_volume"]), 2)
        state["last_update"] = now
        
        att_res = anomaly_engine.calculate_attention_score(
            volume_z_score=4.5,
            price_velocity_pct=price_jump_pct,
            volatility_ratio=2.5,
            breakout_flag=True
        )
        
        state["attention_score"] = att_res["attention_score"]
        state["tier"] = att_res["tier"]
        
        tick_payload = {
            "sequence_id": self._sequence_id,
            "timestamp": now,
            "symbol": symbol,
            "price": new_price,
            "change": round(new_price - state["open_price"], 2),
            "change_pct": round(((new_price - state["open_price"]) / state["open_price"]) * 100.0, 2),
            "volume": tick_volume,
            "bid": round(new_price - 0.02, 2),
            "ask": round(new_price + 0.02, 2),
            "high": state["high"],
            "low": state["low"],
            "vwap": state["vwap"],
            "z_score": 4.5,
            "attention_score": att_res["attention_score"],
            "tier": att_res["tier"],
            "anomaly_flags": ["⚡ VOLUME SPIKE", "🚀 BREAKOUT HIGH"],
            "factor_breakdown": {
                "volume_z_score": 4.5,
                "price_velocity_pct": price_jump_pct,
                "volatility_ratio": 2.5,
                "breakout_flag": True,
                "components": att_res["components"]
            },
            "is_anomaly": True
        }
        
        asyncio.create_task(redis_manager.hset("quotes", symbol, tick_payload))
        asyncio.create_task(redis_manager.publish(f"ticks:{symbol}", tick_payload))
        asyncio.create_task(redis_manager.publish("ticks:all", tick_payload))
        tick_storage.insert_ticks([tick_payload])
        
        return tick_payload

    def stop(self):
        self._running = False
        logger.info("Market Tick Simulator loop stopped.")



simulator = MarketTickSimulator()
