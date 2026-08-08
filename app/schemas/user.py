from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import UserRole


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    role: UserRole

    @field_validator("username")
    @classmethod
    def _username_no_whitespace(cls, v: str) -> str:
        if v != v.strip() or " " in v:
            raise ValueError("username must not contain whitespace")
        return v


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole
    is_active: bool
    telegram_user_id: int | None
    created_at: datetime
    updated_at: datetime
    # hashed_password intentionally excluded
    # telegram_bot_token_encrypted intentionally excluded -- encrypted secrets
    # are never echoed back in a response schema (see app.models.user.User).
