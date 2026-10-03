"""Diagnostic ID generation: DIAG-YYYYMMDD-XXXXXXXX"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def new_diagnostic_id(when: datetime | None = None) -> str:
    ts = when or datetime.now(timezone.utc)
    day = ts.strftime("%Y%m%d")
    suffix = secrets.token_hex(4).upper()
    return f"DIAG-{day}-{suffix}"
