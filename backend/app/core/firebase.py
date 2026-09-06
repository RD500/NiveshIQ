import logging
import time
from typing import Dict, Any, Optional
import jwt
from app.core.config import settings

logger = logging.getLogger(__name__)

_firebase_app = None
_firebase_initialized = False

try:
    import firebase_admin
    from firebase_admin import credentials, auth
    
    if settings.FIREBASE_CREDENTIALS_PATH and os.path.exists(settings.FIREBASE_CREDENTIALS_PATH):
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        _firebase_app = firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        logger.info("Firebase Admin initialized with certificate credentials file.")
    elif settings.FIREBASE_PROJECT_ID:
        _firebase_app = firebase_admin.initialize_app(options={"projectId": settings.FIREBASE_PROJECT_ID})
        _firebase_initialized = True
        logger.info(f"Firebase Admin initialized with project ID: {settings.FIREBASE_PROJECT_ID}")
    else:
        logger.info("Firebase credentials not configured. Operating in local Dev Auth Fallback mode.")
except Exception as e:
    logger.warning(f"Firebase Admin SDK initialization skipped/failed: {e}. Falling back to dev JWT.")
    _firebase_initialized = False


def create_dev_token(user_id: str = "dev_trader_01", email: str = "trader@smartmarket.io", name: str = "Senior Trader") -> str:
    """
    Generates a signed JWT dev token usable for testing and fallback authentication.
    """
    now = int(time.time())
    payload = {
        "sub": user_id,
        "uid": user_id,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + (settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        "iss": "smart-market-dev-auth",
        "auth_provider": "dev_fallback"
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_dev_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decodes and validates local dev JWT token.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except Exception as e:
        logger.debug(f"Failed to decode dev token: {e}")
        return None


def verify_auth_token(token: str) -> Dict[str, Any]:
    """
    Verifies token either via Firebase Admin SDK or via local dev JWT fallback.
    Returns decoded user info dict or raises ValueError if invalid.
    """
    if not token or token.strip() == "":
        raise ValueError("Empty authentication token")

    # Sanitize token (remove 'Bearer ' prefix if present)
    if token.startswith("Bearer "):
        token = token[7:]

    # 1. Try Firebase if initialized
    if _firebase_initialized:
        try:
            decoded_firebase = auth.verify_id_token(token)
            return {
                "uid": decoded_firebase.get("uid"),
                "email": decoded_firebase.get("email", ""),
                "name": decoded_firebase.get("name", "Trader"),
                "auth_provider": "firebase"
            }
        except Exception as fb_err:
            logger.debug(f"Firebase token verification failed: {fb_err}. Trying dev token fallback.")

    # 2. Try Dev Token validation
    dev_payload = decode_dev_token(token)
    if dev_payload:
        return {
            "uid": dev_payload.get("uid", dev_payload.get("sub", "dev_trader")),
            "email": dev_payload.get("email", "dev@smartmarket.io"),
            "name": dev_payload.get("name", "Dev Trader"),
            "auth_provider": dev_payload.get("auth_provider", "dev")
        }

    # 3. Allow special mock token strings for easy dev/testing
    if token == "mock-dev-token" or token == "dev":
        return {
            "uid": "dev_trader_01",
            "email": "dev@smartmarket.io",
            "name": "Dev Trader",
            "auth_provider": "mock"
        }

    raise ValueError("Invalid authentication token")
