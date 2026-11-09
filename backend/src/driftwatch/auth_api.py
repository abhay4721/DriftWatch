"""First-admin setup, login, and user administration endpoints."""

import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from driftwatch.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from driftwatch.database import get_db
from driftwatch.db_models import AuditLog, User

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
_setup_lock = threading.Lock()
_attempt_lock = threading.Lock()
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_ATTEMPT_WINDOW_SECONDS = 60
_MAX_LOGIN_ATTEMPTS = 10


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if value.count("@") != 1 or "." not in value.rsplit("@", maxsplit=1)[-1]:
            raise ValueError("Enter a valid email address")
        return value


class UserCreate(Credentials):
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in {"admin", "operator", "viewer"}:
            raise ValueError("role must be admin, operator, or viewer")
        return value


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    is_active: bool


def _check_login_limit(request: Request) -> None:
    now = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    with _attempt_lock:
        for key, values in list(_login_attempts.items()):
            while values and now - values[0] > _ATTEMPT_WINDOW_SECONDS:
                values.popleft()
            if not values:
                _login_attempts.pop(key, None)
        attempts = _login_attempts[client_ip]
        if len(attempts) >= _MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "rate_limited",
                    "message": "Too many login attempts; retry shortly",
                },
                headers={"Retry-After": str(_ATTEMPT_WINDOW_SECONDS)},
            )
        attempts.append(now)
        if len(_login_attempts) > 2048:
            for key in list(_login_attempts)[: len(_login_attempts) - 2048]:
                _login_attempts.pop(key, None)


def _user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def _token(user: User) -> AccessToken:
    from driftwatch.config import settings

    return AccessToken(
        access_token=create_access_token(user), expires_in=settings.jwt_ttl_minutes * 60
    )


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"setup_required": _user_count(db) == 0}


@router.post("/setup", response_model=AccessToken, status_code=status.HTTP_201_CREATED)
def setup_first_admin(payload: Credentials, db: Session = Depends(get_db)) -> AccessToken:
    with _setup_lock:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(804160827)"))
        if _user_count(db) != 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "setup_complete",
                    "message": "Administrator setup is already complete",
                },
            )
        user = User(
            id=uuid.uuid4(),
            email=payload.email,
            password_hash=hash_password(payload.password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.add(
            AuditLog(
                actor_user_id=user.id,
                action="auth.admin_setup",
                entity_type="user",
                entity_id=user.id,
                details={"email": user.email},
            )
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "setup_complete",
                    "message": "Administrator setup is already complete",
                },
            ) from exc
        db.refresh(user)
        return _token(user)


@router.post("/token", response_model=AccessToken)
def login(payload: Credentials, request: Request, db: Session = Depends(get_db)) -> AccessToken:
    _check_login_limit(request)
    user = db.scalar(select(User).where(User.email == payload.email))
    if (
        user is None
        or not verify_password(payload.password, user.password_hash)
        or not user.is_active
    ):
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Email or password is incorrect"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token(user)


@router.get("/me", response_model=UserOut)
def who_am_i(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="auth.user_created",
            entity_type="user",
            entity_id=user.id,
            details={"email": user.email, "role": user.role},
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "conflict", "message": "A user with this email already exists"},
        ) from exc
    db.refresh(user)
    return user
