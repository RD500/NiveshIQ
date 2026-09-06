from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.watchlist import router as watchlist_router
from app.api.v1.market import router as market_router
from app.api.v1.websocket import router as websocket_router

api_v1_router = APIRouter()

api_v1_router.include_router(auth_router)
api_v1_router.include_router(watchlist_router)
api_v1_router.include_router(market_router)
api_v1_router.include_router(websocket_router)
