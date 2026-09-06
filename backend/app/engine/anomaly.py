import time
import math
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


from app.engine.ml_model import xgb_market_model

class AnomalyEngine:
    """
    Smart Market Anomaly & Attention Score Calculation Engine.
    Powered by XGBoost Machine Learning Model + Factor Decomposition.
    """
    
    @staticmethod
    def calculate_attention_score(
        volume_z_score: float,
        price_velocity_pct: float,
        volatility_ratio: float = 1.0,
        breakout_flag: bool = False
    ) -> Dict[str, Any]:
        """
        Calculates Attention Score (0 - 100) using trained XGBoost Regressor.
        """
        # 1. XGBoost Model Prediction
        features = {
            "vol_z_score": volume_z_score,
            "price_velocity_1m": price_velocity_pct,
            "price_velocity_5m": price_velocity_pct * 1.2,
            "volatility_ratio": volatility_ratio,
            "vwap_distance_pct": price_velocity_pct * 0.5,
            "spread_pct": 0.02,
            "breakout_flag": breakout_flag
        }
        xgb_res = xgb_market_model.predict(features)
        total_score = xgb_res["predicted_score"]
        tier = xgb_res["tier"]

        # Component breakdown
        vol_score = min(40.0, max(0.0, volume_z_score * 13.33)) if volume_z_score > 0 else 0.0
        vel_score = min(30.0, abs(price_velocity_pct) * 30.0)
        volat_score = min(30.0, max(0.0, volatility_ratio - 1.0) * 30.0)

        return {
            "attention_score": total_score,
            "tier": tier,
            "model": "XGBoost_v1",
            "components": {
                "volume_score": round(vol_score, 1),
                "velocity_score": round(vel_score, 1),
                "volatility_score": round(volat_score, 1),
                "breakout_bonus": 10.0 if breakout_flag else 0.0
            }
        }


    @staticmethod
    def generate_delta_digest(
        previous_snapshot: Dict[str, Dict[str, Any]],
        current_quotes: Dict[str, Dict[str, Any]],
        since_timestamp: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Computes natural language Delta Digest comparing current session state 
        with previous snapshot / timestamp X.
        """
        highlights: List[str] = []
        structured_events: List[Dict[str, Any]] = []

        time_clause = ""
        if since_timestamp:
            elapsed_sec = int(time.time() - since_timestamp)
            if elapsed_sec < 60:
                time_clause = f"over the last {elapsed_sec} seconds"
            elif elapsed_sec < 3600:
                time_clause = f"over the last {elapsed_sec // 60} minutes"
            else:
                time_clause = f"since your last session ({elapsed_sec // 3600}h ago)"
        else:
            time_clause = "since your last snapshot"

        for symbol, curr in current_quotes.items():
            prev = previous_snapshot.get(symbol)
            if not prev:
                # New symbol added to watch
                highlights.append(f"• **{symbol}** was added to your active watchlist (current price: ${curr.get('price', 0):,.2f}).")
                structured_events.append({
                    "symbol": symbol,
                    "event_type": "NEW_SYMBOL",
                    "description": f"Added to watchlist at ${curr.get('price', 0):,.2f}"
                })
                continue

            prev_price = prev.get("price", curr.get("price", 0))
            curr_price = curr.get("price", 0)
            
            if prev_price > 0:
                pct_change = ((curr_price - prev_price) / prev_price) * 100.0
            else:
                pct_change = 0.0

            # 1. Level Breakouts (20-day high / low or intraday break)
            curr_high = curr.get("high", curr_price)
            curr_low = curr.get("low", curr_price)
            prev_high = prev.get("high", prev_price)
            prev_low = prev.get("low", prev_price)

            if curr_price > prev_high and prev_high > 0:
                msg = f"• **{symbol}** broke resistance high at ${prev_high:,.2f} (now **${curr_price:,.2f}**, +{pct_change:.2f}%)."
                highlights.append(msg)
                structured_events.append({
                    "symbol": symbol,
                    "event_type": "HIGH_BREAKOUT",
                    "description": f"Broke high at ${prev_high:,.2f} to ${curr_price:,.2f}",
                    "pct_change": round(pct_change, 2)
                })
            elif curr_price < prev_low and prev_low > 0:
                msg = f"• **{symbol}** dropped below support low at ${prev_low:,.2f} (now **${curr_price:,.2f}**, {pct_change:.2f}%)."
                highlights.append(msg)
                structured_events.append({
                    "symbol": symbol,
                    "event_type": "LOW_BREAKDOWN",
                    "description": f"Broke support at ${prev_low:,.2f} to ${curr_price:,.2f}",
                    "pct_change": round(pct_change, 2)
                })

            # 2. Significant Price Velocity Move (> 1.0%)
            elif abs(pct_change) >= 1.0:
                direction = "surged" if pct_change > 0 else "plunged"
                msg = f"• **{symbol}** {direction} **{pct_change:+.2f}%** from ${prev_price:,.2f} to ${curr_price:,.2f}."
                highlights.append(msg)
                structured_events.append({
                    "symbol": symbol,
                    "event_type": "PRICE_SURGE" if pct_change > 0 else "PRICE_DROP",
                    "description": f"{direction.capitalize()} {pct_change:+.2f}% to ${curr_price:,.2f}",
                    "pct_change": round(pct_change, 2)
                })

            # 3. Attention Score Jump
            prev_att = prev.get("attention_score", 0.0)
            curr_att = curr.get("attention_score", 0.0)
            att_diff = curr_att - prev_att
            if att_diff >= 25.0:
                msg = f"• **{symbol}** attention score spiked by **+{att_diff:.1f}** (now {curr_att:.1f}/100 - High Volume/Volatility)."
                highlights.append(msg)
                structured_events.append({
                    "symbol": symbol,
                    "event_type": "ATTENTION_SPIKE",
                    "description": f"Attention score jumped +{att_diff:.1f} to {curr_att:.1f}"
                })

            # 4. VWAP Cross
            vwap = curr.get("vwap", 0.0)
            if vwap > 0:
                if prev_price < vwap and curr_price >= vwap:
                    msg = f"• **{symbol}** crossed above VWAP (${vwap:,.2f}) to **${curr_price:,.2f}**."
                    highlights.append(msg)
                    structured_events.append({
                        "symbol": symbol,
                        "event_type": "VWAP_BULLISH_CROSS",
                        "description": f"Crossed above VWAP ${vwap:,.2f}"
                    })
                elif prev_price > vwap and curr_price <= vwap:
                    msg = f"• **{symbol}** crossed below VWAP (${vwap:,.2f}) to **${curr_price:,.2f}**."
                    highlights.append(msg)
                    structured_events.append({
                        "symbol": symbol,
                        "event_type": "VWAP_BEARISH_CROSS",
                        "description": f"Crossed below VWAP ${vwap:,.2f}"
                    })

        if not highlights:
            summary_text = f"Markets were relatively stable {time_clause}. No critical level breakouts or volume spikes detected across tracked symbols."
        else:
            summary_text = f"Key market developments {time_clause}:\n\n" + "\n".join(highlights)

        return {
            "timestamp": time.time(),
            "since_timestamp": since_timestamp,
            "summary_text": summary_text,
            "highlights_count": len(highlights),
            "events": structured_events
        }


anomaly_engine = AnomalyEngine()
