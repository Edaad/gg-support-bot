"""Turn ORM rows, pydantic models and dataclasses into JSON-safe structures."""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable

BINARY_PLACEHOLDER = "<binary omitted>"


def cents_to_usd(cents: int | None) -> float | None:
    if cents is None:
        return None
    return round(int(cents) / 100.0, 2)


def _orm_columns(obj: Any) -> dict[str, Any] | None:
    try:
        state = sa_inspect(obj)
    except NoInspectionAvailable:
        return None
    mapper = getattr(state, "mapper", None)
    if mapper is None:
        return None
    return {attr.key: getattr(obj, attr.key) for attr in mapper.column_attrs}


def to_jsonable(obj: Any) -> Any:
    """Recursively convert to JSON-compatible values.

    Decimal -> float (2 dp), datetime -> ISO-8601 (naive treated as UTC, ``Z``),
    bytes -> placeholder, ORM rows -> their columns only (no relationships).
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Decimal):
        return float(round(obj, 2))
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            return obj.isoformat() + "Z"
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return BINARY_PLACEHOLDER
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)
        }
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    columns = _orm_columns(obj)
    if columns is not None:
        return to_jsonable(columns)
    return str(obj)
