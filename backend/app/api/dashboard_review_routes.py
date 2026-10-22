"""GET /api/paper/dashboard-review and /api/paper/ledger-view — display snapshots only."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.services.dashboard_review import build_dashboard_review

router = APIRouter(tags=["paper-dashboard"])


@router.get("/paper/dashboard-review")
async def paper_dashboard_review() -> dict:
    from app.services import paper_trade

    return build_dashboard_review(engine=paper_trade._engine)


@router.get("/paper/ledger-view")
async def paper_ledger_view() -> dict:
    """Evidence-tagged paper ledger (owner D187). One READ ONLY SELECT; tags are never stored."""
    from app.services import paper_ledger_view as view

    try:
        rows = await view.read_ledger_rows()
    except Exception as exc:  # noqa: BLE001
        return {
            "read_only": True,
            "available": False,
            "error": f"ledger read failed: {type(exc).__name__}: {str(exc)[:200]}",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "rows": [],
            "books": [],
        }
    out = view.build_ledger_view(rows)
    out["available"] = True
    return out
