from __future__ import annotations

from typing import Any

from app.config import Settings


def list_presets(settings: Settings) -> list[dict[str, Any]]:
    raw = (settings.screener_config.get("presets") or {})
    out: list[dict[str, Any]] = []
    for key, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        out.append(
            {
                "id": cfg.get("id") or key,
                "label": cfg.get("label") or key,
                "description": cfg.get("description") or "",
                "filters": list(cfg.get("filters") or []),
            }
        )
    return out


def get_preset(settings: Settings, preset_id: str) -> dict[str, Any] | None:
    for p in list_presets(settings):
        if p["id"] == preset_id:
            return p
    return None
