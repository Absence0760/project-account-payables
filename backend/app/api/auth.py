"""Auth endpoints — login, token refresh, current user."""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import create_access_token, decode_token, get_current_user
from app.database import get_control_db
from app.models.user import User
from app.redis import block_token
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.utils.passwords import PasswordError, validate_password_complexity

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_control_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not pwd_context.verify(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(user.id, user.organization_id)
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(authorization: str = Header()):
    """Revoke the current token by adding it to the Redis blocklist."""
    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)
    jti = payload.get("jti")
    if jti:
        exp = payload.get("exp", 0)
        # Block for the remaining lifetime of the token
        import time

        ttl = max(int(exp - time.time()), 1)
        await block_token(jti, ttl)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        organization_id=str(user.organization_id),
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        roles=[r.name for r in user.roles],
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return _user_response(user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    if body.full_name is not None:
        user.full_name = body.full_name

    if body.password is not None:
        if not body.current_password:
            raise HTTPException(
                status_code=400, detail="Current password is required to set a new password"
            )
        if not user.hashed_password or not pwd_context.verify(
            body.current_password, user.hashed_password
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        user.hashed_password = pwd_context.hash(body.password)

    await db.commit()
    return _user_response(user)


@router.post("/change-password", response_model=UserResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Change password for the authenticated user.

    Used by the first-login forced-change flow (`must_change_password=true`)
    and by any user voluntarily rotating their password. Clearing the flag
    happens regardless — successfully setting a password means the temp
    credential is no longer in play.
    """
    if not user.hashed_password or not pwd_context.verify(
        body.current_password, user.hashed_password
    ):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        validate_password_complexity(body.new_password)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user.hashed_password = pwd_context.hash(body.new_password)
    user.must_change_password = False
    await db.commit()
    return _user_response(user)
