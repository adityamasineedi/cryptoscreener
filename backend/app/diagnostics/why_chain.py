"""Dependency chains for 'Why is this broken?' drilldowns."""

from __future__ import annotations

from typing import Any

from app.diagnostics.constants import HealthStatus


# Static forensic chains — nodes are evaluated live against overview cards / coverage.
CHAINS: dict[str, dict[str, Any]] = {
    "ohlcv": {
        "dataset": "OHLCV",
        "nodes": [
            {"key": "binance_ws", "label": "Binance WebSocket"},
            {"key": "kline_parser", "label": "Kline Parser"},
            {"key": "normalizer", "label": "Normalizer"},
            {"key": "postgresql", "label": "PostgreSQL"},
            {"key": "coverage", "label": "Coverage"},
            {"key": "freshness", "label": "Freshness"},
            {"key": "frontend_api", "label": "Frontend API"},
        ],
    },
    "oi": {
        "dataset": "Open Interest",
        "nodes": [
            {"key": "binance_rest", "label": "Binance REST OI"},
            {"key": "oi_scheduler", "label": "OI Scheduler"},
            {"key": "postgresql", "label": "PostgreSQL"},
            {"key": "coverage", "label": "Coverage"},
            {"key": "freshness", "label": "Freshness"},
            {"key": "frontend_api", "label": "Frontend API"},
        ],
    },
    "liquidations": {
        "dataset": "Liquidations",
        "nodes": [
            {"key": "binance_ws", "label": "Binance Force Order WS"},
            {"key": "liq_parser", "label": "Liquidation Parser"},
            {"key": "liquidations", "label": "Liquidation Store"},
            {"key": "freshness", "label": "Freshness"},
            {"key": "frontend_api", "label": "Frontend API"},
        ],
    },
    "ticker": {
        "dataset": "Ticker",
        "nodes": [
            {"key": "binance_ws", "label": "Binance Ticker WS"},
            {"key": "market_store", "label": "Market Store"},
            {"key": "freshness", "label": "Freshness"},
            {"key": "frontend_api", "label": "Frontend API"},
        ],
    },
    "research": {
        "dataset": "Research Pipeline",
        "nodes": [
            {"key": "binance_rest", "label": "Binance REST"},
            {"key": "research_pipeline", "label": "Research Pipeline"},
            {"key": "postgresql", "label": "PostgreSQL"},
            {"key": "coverage", "label": "History Coverage"},
        ],
    },
}


def build_why_chain(
    dataset: str,
    *,
    cards_by_key: dict[str, dict[str, Any]],
    related_issues: list[dict[str, Any]] | None = None,
    evidence_by_key: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    key = dataset.strip().lower().replace(" ", "_")
    # Allow ohlcv_15m → ohlcv
    if key.startswith("ohlcv"):
        key = "ohlcv"
    chain = CHAINS.get(key)
    if chain is None:
        return {
            "dataset": dataset,
            "status": HealthStatus.UNKNOWN.value,
            "reason": f"No forensic chain defined for '{dataset}'",
            "nodes": [],
            "failure_node": None,
            "related_issues": related_issues or [],
        }

    evidence_by_key = evidence_by_key or {}
    nodes_out: list[dict[str, Any]] = []
    failure_node: dict[str, Any] | None = None
    for node in chain["nodes"]:
        card = cards_by_key.get(node["key"])
        evidence = evidence_by_key.get(node["key"])
        if evidence is not None:
            status = str(evidence.get("status") or HealthStatus.UNKNOWN.value)
            reason = evidence.get("reason")
            metrics = evidence.get("metrics") or {}
            last_checked = evidence.get("last_checked")
        elif card is not None:
            status = str(card.get("status") or HealthStatus.UNKNOWN.value)
            reason = card.get("reason")
            metrics = card.get("metrics") or {}
            last_checked = card.get("last_checked")
        else:
            # Synthetic nodes without a dedicated card
            status = HealthStatus.UNKNOWN.value
            reason = "No dedicated health probe for this stage yet"
            metrics = {}
            last_checked = None

        entry = {
            "key": node["key"],
            "label": node["label"],
            "status": status,
            "reason": reason,
            "last_checked": last_checked,
            "evidence": metrics,
            "metrics": metrics,
            "related_issue_id": None,
            "ok": status
            in (
                HealthStatus.HEALTHY.value,
                HealthStatus.WAITING.value,
                HealthStatus.DISABLED.value,
                "LIVE",
            ),
        }
        # Attach first matching open issue for this component
        if related_issues:
            for iss in related_issues:
                comp = (iss.get("component") or "").lower()
                if node["key"] in comp or comp in node["key"]:
                    entry["issue"] = {
                        "id": iss.get("id"),
                        "diagnostic_id": iss.get("diagnostic_id"),
                        "message": iss.get("message"),
                        "file": iss.get("file"),
                        "function": iss.get("function"),
                        "line": iss.get("line"),
                        "location": iss.get("location"),
                    }
                    entry["related_issue_id"] = iss.get("id") or iss.get("diagnostic_id")
                    break
        nodes_out.append(entry)
        if failure_node is None and status in (
            HealthStatus.ERROR.value,
            HealthStatus.UNAVAILABLE.value,
            HealthStatus.STALE.value,
            HealthStatus.WARNING.value,
            HealthStatus.DEGRADED.value,
            HealthStatus.MISSING.value,
            "CRITICAL",
        ):
            failure_node = entry

    overall = (
        failure_node["status"]
        if failure_node
        else HealthStatus.HEALTHY.value
    )
    # If all unknown, overall unknown
    if all(n["status"] == HealthStatus.UNKNOWN.value for n in nodes_out):
        overall = HealthStatus.UNKNOWN.value

    return {
        "dataset": chain["dataset"],
        "status": overall,
        "reason": (
            f"Failure at {failure_node['label']}: {failure_node.get('reason')}"
            if failure_node
            else "No failing node detected in measured chain"
        ),
        "nodes": nodes_out,
        "failure_node": failure_node,
        "related_issues": related_issues or [],
    }
