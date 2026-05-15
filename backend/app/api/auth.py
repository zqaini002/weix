from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext

from app.config import get_config
from app.models.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_token(data: dict, secret: str, expires_minutes: int = 1440) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    return jwt.encode(to_encode, secret, algorithm="HS256")


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    config = get_config()
    try:
        payload = jwt.decode(credentials.credentials, config.admin.get("jwt_secret", ""), algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    config = get_config()
    admin_cfg = config.admin
    stored_password = admin_cfg.get("password", "")
    jwt_secret = admin_cfg.get("jwt_secret", "weix-secret-key")

    if req.username != admin_cfg.get("username"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # 检查是否是 bcrypt hash（$2b$ 开头）
    if stored_password.startswith("$2b$") or stored_password.startswith("$2a$"):
        if not pwd_context.verify(req.password, stored_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    else:
        # 明文密码直接比较（首次使用）
        if req.password != stored_password:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_token({"sub": req.username}, jwt_secret)
    return TokenResponse(access_token=token)
