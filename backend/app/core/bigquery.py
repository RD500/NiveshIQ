import logging
import time
import math
from typing import List, Dict, Any, Optional
import duckdb

from app.core.config import settings

logger = logging.getLogger(__name__)

_bq_client = None
_use_bq = False


if settings.BIGQUERY_PROJECT_ID:
    try:
        from google.cloud import bigquery
        _bq_client = bigquery.Client(project=settings.BIGQUERY_PROJECT_ID)
        _use_bq = True
        logger.info(f"BigQuery Client initialized for project: {settings.BIGQUERY_PROJECT_ID}")
    except Exception as e:
        logger.warning(f"BigQuery client initialization failed: {e}. Falling back to DuckDB engine.")
        _use_bq = False


class DuckDBTickStorage:
    
    def __init__(self, db_path: str = ":memory:"):
        self.conn = duckdb.connect(db_path)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ticks (
                timestamp DOUBLE,
                symbol VARCHAR,
                price DOUBLE,
                volume DOUBLE,
                bid DOUBLE,
                ask DOUBLE,
                high DOUBLE,
                low DOUBLE,
                vwap DOUBLE,
                sequence_id BIGINT
            );
            CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks (symbol, timestamp);
        """)
        logger.info("DuckDB tick storage schema initialized.")

    def insert_ticks(self, ticks: List[Dict[str, Any]]):
        if not ticks:
            return
        
        insert_data = []
        for t in ticks:
            insert_data.append((
                float(t.get("timestamp", time.time())),
                str(t.get("symbol", "")),
                float(t.get("price", 0.0)),
                float(t.get("volume", 0.0)),
                float(t.get("bid", 0.0)),
                float(t.get("ask", 0.0)),
                float(t.get("high", 0.0)),
                float(t.get("low", 0.0)),
                float(t.get("vwap", 0.0)),
                int(t.get("sequence_id", 0))
            ))
            
        self.conn.executemany("""
            INSERT INTO ticks (timestamp, symbol, price, volume, bid, ask, high, low, vwap, sequence_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_data)

    def get_tick_history(self, symbol: str, limit: int = 100, start_time: Optional[float] = None) -> List[Dict[str, Any]]:
        if start_time:
            query = """
                SELECT timestamp, symbol, price, volume, bid, ask, high, low, vwap, sequence_id
                FROM ticks
                WHERE symbol = ? AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            res = self.conn.execute(query, [symbol.upper(), start_time, limit]).fetchall()
        else:
            query = """
                SELECT timestamp, symbol, price, volume, bid, ask, high, low, vwap, sequence_id
                FROM ticks
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            res = self.conn.execute(query, [symbol.upper(), limit]).fetchall()

        cols = ["timestamp", "symbol", "price", "volume", "bid", "ask", "high", "low", "vwap", "sequence_id"]
        return [dict(zip(cols, row)) for row in res]

    def calculate_vwap(self, symbol: str, window_seconds: int = 3600) -> float:
        cutoff = time.time() - window_seconds
        query = """
            SELECT SUM(price * volume) / NULLIF(SUM(volume), 0) as vwap
            FROM ticks
            WHERE symbol = ? AND timestamp >= ?
        """
        res = self.conn.execute(query, [symbol.upper(), cutoff]).fetchone()
        if res and res[0] is not None:
            return float(res[0])
        return 0.0

    def calculate_volume_zscore(self, symbol: str, window_seconds: int = 3600) -> float:
        """
        Calculates Volume Z-score comparing recent volume against rolling standard deviation.
        """
        cutoff = time.time() - window_seconds
        query = """
            SELECT volume FROM ticks
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 100
        """
        rows = self.conn.execute(query, [symbol.upper(), cutoff]).fetchall()
        if not rows or len(rows) < 3:
            return 0.0
        
        volumes = [r[0] for r in rows]
        latest_vol = volumes[0]
        mean_vol = sum(volumes) / len(volumes)
        variance = sum((x - mean_vol) ** 2 for x in volumes) / len(volumes)
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return 0.0

        z_score = (latest_vol - mean_vol) / std_dev
        # Clamp Z-Score to range [-5.0, 5.0]
        return round(max(-5.0, min(5.0, z_score)), 2)

    def get_symbol_analytics(self, symbol: str, window_seconds: int = 3600) -> Dict[str, Any]:
        cutoff = time.time() - window_seconds
        query = """
            SELECT 
                COUNT(*) as count,
                AVG(price) as avg_price,
                MIN(price) as min_price,
                MAX(price) as max_price,
                SUM(volume) as total_volume,
                SUM(price * volume) / NULLIF(SUM(volume), 0) as vwap,
                STDDEV(price) as price_stddev
            FROM ticks
            WHERE symbol = ? AND timestamp >= ?
        """
        res = self.conn.execute(query, [symbol.upper(), cutoff]).fetchone()
        if not res or res[0] == 0:
            return {
                "symbol": symbol.upper(),
                "count": 0,
                "avg_price": 0.0,
                "min_price": 0.0,
                "max_price": 0.0,
                "total_volume": 0.0,
                "vwap": 0.0,
                "price_stddev": 0.0,
                "z_score": 0.0
            }
        
        z_score = self.calculate_volume_zscore(symbol, window_seconds)
        return {
            "symbol": symbol.upper(),
            "count": res[0],
            "avg_price": round(res[1] or 0.0, 2),
            "min_price": round(res[2] or 0.0, 2),
            "max_price": round(res[3] or 0.0, 2),
            "total_volume": round(res[4] or 0.0, 2),
            "vwap": round(res[5] or 0.0, 2),
            "price_stddev": round(res[6] or 0.0, 4),
            "z_score": z_score
        }



duckdb_storage = DuckDBTickStorage(db_path=settings.DUCKDB_PATH)


class BigQueryTickStorageClient:
    
    def __init__(self):
        self.bq_client = _bq_client
        self.use_bq = _use_bq
        self.fallback = duckdb_storage

    def insert_ticks(self, ticks: List[Dict[str, Any]]):
       
        self.fallback.insert_ticks(ticks)

        if self.use_bq and self.bq_client:
            try:
                table_id = f"{settings.BIGQUERY_PROJECT_ID}.{settings.BIGQUERY_DATASET}.{settings.BIGQUERY_TABLE}"
                errors = self.bq_client.insert_rows_json(table_id, ticks)
                if errors:
                    logger.error(f"BigQuery insert errors: {errors}")
            except Exception as e:
                logger.error(f"BigQuery write failed: {e}")

    def get_tick_history(self, symbol: str, limit: int = 100, start_time: Optional[float] = None) -> List[Dict[str, Any]]:
        return self.fallback.get_tick_history(symbol, limit, start_time)

    def calculate_vwap(self, symbol: str, window_seconds: int = 3600) -> float:
        return self.fallback.calculate_vwap(symbol, window_seconds)

    def calculate_volume_zscore(self, symbol: str, window_seconds: int = 3600) -> float:
        return self.fallback.calculate_volume_zscore(symbol, window_seconds)

    def get_symbol_analytics(self, symbol: str, window_seconds: int = 3600) -> Dict[str, Any]:
        return self.fallback.get_symbol_analytics(symbol, window_seconds)


tick_storage = BigQueryTickStorageClient()
