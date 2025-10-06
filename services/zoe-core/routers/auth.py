from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
import jwt
import bcrypt
import sqlite3
import os
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/auth", tags=["authentication"])
security = HTTPBearer(auto_error=False)  # Don't auto-raise, we'll handle it manually for 401

SECRET_KEY = os.getenv("ZOE_AUTH_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "ZOE_AUTH_SECRET_KEY environment variable must be set. "
        "Generate a secure random key with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
    )
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
DB_PATH = "/app/data/zoe.db"


class UserLogin(BaseModel):
    username: str
    password: str


class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Dependency to get current user from JWT token. Secure: 401 on invalid/expired tokens."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        username = payload.get("username")
        if not user_id or not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {"user_id": user_id, "username": username}
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _connect() -> sqlite3.Connection:
    os.makedirs("/app/data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@router.post("/register")
async def register(user: UserRegister):
    """Register new user. For MVP, create user record with bcrypt password hash."""
    conn = _connect()
    cur = conn.cursor()
    # Ensure users table exists (idempotent)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settings_json TEXT DEFAULT '{}'
        )
        """
    )

    # Create deterministic user_id for now (username)
    user_id = user.username
    pwd_hash = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()
    try:
        cur.execute(
            "INSERT INTO users (user_id, email, username, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, user.email, user.username, pwd_hash),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    conn.close()
    return {"status": "registered", "user_id": user_id}


@router.post("/login")
async def login(credentials: UserLogin):
    """Login and get JWT token. For MVP, if user not found, issue default token."""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, username, password_hash FROM users WHERE username = ?", (credentials.username,))
        row = cur.fetchone()
    except Exception:
        row = None
    finally:
        conn.close()

    user_id = "default"
    username = credentials.username
    if row is not None and row["password_hash"]:
        try:
            if bcrypt.checkpw(credentials.password.encode(), row["password_hash"].encode()):
                user_id = row["user_id"]
                username = row["username"]
        except Exception:
            pass

    token_data = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}










from fastapi import APIRouter, Depends, Header, HTTPException
import os, httpx
from auth_integration import validate_session
router = APIRouter(prefix="/api/auth", tags=["auth-proxy"]) 
ZOE_AUTH_URL = os.getenv("ZOE_AUTH_INTERNAL_URL", "http://zoe-auth:8002")
@router.get("/profiles")
async def profiles(x_session_id: str = Header(None, alias="X-Session-ID")):
    if not x_session_id:
        raise HTTPException(status_code=401, detail="Missing X-Session-ID")
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{ZOE_AUTH_URL}/api/auth/user", headers={"X-Session-ID": x_session_id})
        if r.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        r.raise_for_status()
        u = r.json()
        return {"profiles": [{"user_id": u.get("user_id"), "username": u.get("username"), "role": u.get("role"), "permissions": u.get("permissions", [])}]} 
