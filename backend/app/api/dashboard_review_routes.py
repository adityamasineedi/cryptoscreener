"""GET /api/paper/dashboard-review — display snapshot only."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.dashboard_review import build_dashboard_review

router = APIRouter(tags=["paper-dashboard"])


@router.get("/paper/dashboard-review")
async def paper_dashboard_review() -> dict:
    from app.services import paper_trade

    return build_dashboard_review(engine=paper_trade._engine)
