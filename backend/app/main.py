import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.redis import redis_manager
from app.engine.simulator import simulator
from app.engine.ws_manager import ws_manager
from app.api.v1.router import api_v1_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("smart_market")


async def redis_pubsub_listener():
    try:
        pubsub = await redis_manager.subscribe("ticks:all")
        if hasattr(pubsub, "listen"):
            async for msg in pubsub.listen():
                if msg and isinstance(msg, dict) and msg.get("type") == "message":
                    raw_data = msg.get("data")
                    try:
                        ticks = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                        if isinstance(ticks, list):
                            for tick in ticks:
                                await ws_manager.broadcast_tick(tick)
                        elif isinstance(ticks, dict):
                            await ws_manager.broadcast_tick(ticks)
                    except Exception as parse_err:
                        logger.debug(f"PubSub parse error: {parse_err}")
    except asyncio.CancelledError:
        logger.info("PubSub listener cancelled.")
    except Exception as e:
        logger.error(f"Error in Redis PubSub listener: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION}...")
    
    await redis_manager.init_redis()

    sim_task = asyncio.create_task(simulator.run_simulation_loop())
    ws_hb_task = asyncio.create_task(ws_manager.run_heartbeat_loop())
    pubsub_task = asyncio.create_task(redis_pubsub_listener())

    yield

    logger.info("Shutting down background workers...")
    simulator.stop()
    sim_task.cancel()
    ws_hb_task.cancel()
    pubsub_task.cancel()
    await redis_manager.close()
    logger.info("Shutdown complete.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Production High-Throughput Real-Time Smart Market Watchlist API",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix=settings.API_V1_STR)

from app.api.v1.websocket import router as ws_root_router
app.include_router(ws_root_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "redis_fallback_active": redis_manager.is_fallback
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
