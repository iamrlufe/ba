from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class LoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MeResponse(UserRead):
    """Distinct name for OpenAPI docs on GET /api/auth/me; same shape as UserRead."""
