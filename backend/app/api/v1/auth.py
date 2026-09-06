from fastapi import APIRouter, HTTPException, Depends, Header, status
from pydantic import BaseModel, EmailStr
from typing import Dict, Any, Optional

from app.core.firebase import create_dev_token, verify_auth_token

router = APIRouter(prefix="/auth", tags=["Authentication"])


class DevTokenRequest(BaseModel):
    user_id: str = "dev_trader_01"
    email: str = "trader@smartmarket.io"
    name: str = "Senior Trader"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    name: str


async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """
    FastAPI dependency for verifying token from Authorization header.
    """
    if not authorization:
        
        return {
            "uid": "dev_trader_01",
            "email": "dev@smartmarket.io",
            "name": "Dev Trader",
            "auth_provider": "dev_fallback"
        }

    try:
        user_info = verify_auth_token(authorization)
        return user_info
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(err),
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/token", response_model=TokenResponse)
async def generate_token(req: DevTokenRequest):
    """
    Generates a valid signed token for dev testing or fallback auth.
    """
    token = create_dev_token(user_id=req.user_id, email=req.email, name=req.name)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=req.user_id,
        email=req.email,
        name=req.name
    )


@router.get("/verify")
async def verify_token_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Verifies user authentication status.
    """
    return {
        "status": "authenticated",
        "user": current_user
    }
