"""Chain/contract mapping for on-chain queries. Never invents mappings."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from app.config import load_yaml
@dataclass(frozen=True)

class AssetMeta:
    asset: str
    chain: str | None
    contract_address: str | None
    decimals: int | None
    provider_id: str | None
    symbols: tuple[str, ...]
    def is_queryable(self) -> bool:
        """Native L1 may omit contract; still needs a chain."""
        return bool(self.chain)

class AssetMetadataRegistry:
    def __init__(self) -> None:
        self._by_asset: dict[str, AssetMeta] = {}
        self._by_symbol: dict[str, AssetMeta] = {}
        self.reload()
    def reload(self) -> None:
        raw = load_yaml("assets.yaml")
        rows = list(raw.get("assets") or [])
        by_asset: dict[str, AssetMeta] = {}
        by_symbol: dict[str, AssetMeta] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            asset = str(row.get("asset") or "").upper()
            if not asset:
                continue
            symbols = tuple(
                str(s).upper() for s in (row.get("symbols") or []) if s
            )
            if not symbols:
                symbols = (f"{asset}USDT",)
            meta = AssetMeta(
                asset=asset,
                chain=(str(row["chain"]).lower() if row.get("chain") else None),
                contract_address=(
                    str(row["contract_address"])
                    if row.get("contract_address")
                    else None
                ),
                decimals=(
                    int(row["decimals"]) if row.get("decimals") is not None else None
                ),
                provider_id=(
                    str(row["provider_id"]) if row.get("provider_id") else None
                ),
                symbols=symbols,
            )
            by_asset[asset] = meta
            for sym in symbols:
                by_symbol[sym] = meta
        self._by_asset = by_asset
        self._by_symbol = by_symbol
    def get_for_symbol(self, symbol: str) -> AssetMeta | None:
        return self._by_symbol.get(symbol.upper())
    def get_for_asset(self, asset: str) -> AssetMeta | None:
        return self._by_asset.get(asset.upper())
    def as_dict(self, symbol: str) -> dict[str, Any] | None:
        meta = self.get_for_symbol(symbol)
        if meta is None:
            return None
        return {
            "asset": meta.asset,
            "chain": meta.chain,
            "contract_address": meta.contract_address,
            "decimals": meta.decimals,
            "provider_id": meta.provider_id,
            "symbols": list(meta.symbols),
        }
    def stats(self) -> dict[str, Any]:
        return {
            "mapped_assets": len(self._by_asset),
            "mapped_symbols": len(self._by_symbol),
        }
asset_registry = AssetMetadataRegistry()
