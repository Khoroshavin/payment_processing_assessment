"""API key entity.

Currently a plain domain model validated against static configuration; the
natural extension is an SQLAlchemy-backed table (id, name, key_hash,
is_active, created_at) with the same fields.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ApiKey:
    id: str
    name: str
    key_hash: str
    is_active: bool = True
    created_at: datetime | None = None
