"""Shared generic/utility schemas used across resource-specific modules."""
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detail: str
