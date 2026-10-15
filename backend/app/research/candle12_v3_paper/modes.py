"""The two execution modes, and the rule that market data and fills come from one venue.

virtual_paper_mainnet  mainnet candles + mainnet funding + SimulatedBroker.
                       The only mode whose store can count toward OOS.
testnet_broker_check   testnet candles + testnet funding + BinanceTestnetBroker.
                       API and order-lifecycle validation only. Never OOS evidence,
                       never strategy validation, never compared with the engine
                       under the OOS reconciliation tolerances.

Mainnet candles with testnet fills (or the reverse) is refused with
MIXED_DATA_BROKER_MODE: a testnet fill is a fill on a different order book, so
reconciling it against mainnet bars measures nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

VIRTUAL_PAPER_MAINNET = "virtual_paper_mainnet"
TESTNET_BROKER_CHECK = "testnet_broker_check"
MODES = (VIRTUAL_PAPER_MAINNET, TESTNET_BROKER_CHECK)

MAINNET_DATA_BASE = "https://fapi.binance.com"
TESTNET_DATA_BASE = "https://demo-fapi.binance.com"
MAINNET_HOSTS = ("fapi.binance.com",)
TESTNET_HOSTS = ("demo-fapi.binance.com", "testnet.binancefuture.com")

MIXED_DATA_BROKER_MODE = "MIXED_DATA_BROKER_MODE"
UNKNOWN_MODE = "UNKNOWN_MODE"

SIMULATED = "SIMULATED"
TESTNET_BROKER = "BINANCE_USDM_TESTNET"

NON_OOS = "NON_OOS"
OOS_ELIGIBLE = "OOS_ELIGIBLE"


class ModeError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ModeSpec:
    mode: str
    store_mode: str
    data_base: str
    data_venue: str
    broker: str
    counts_toward_oos: bool
    strategy_validation: bool
    engine_comparison: bool  # may the store be reconciled against the engine under OOS tolerances


SPECS = {
    VIRTUAL_PAPER_MAINNET: ModeSpec(
        mode=VIRTUAL_PAPER_MAINNET, store_mode="VIRTUAL_PAPER_MAINNET", data_base=MAINNET_DATA_BASE,
        data_venue="MAINNET", broker=SIMULATED, counts_toward_oos=True, strategy_validation=True,
        engine_comparison=True,
    ),
    TESTNET_BROKER_CHECK: ModeSpec(
        mode=TESTNET_BROKER_CHECK, store_mode="TESTNET_BROKER_CHECK", data_base=TESTNET_DATA_BASE,
        data_venue="TESTNET", broker=TESTNET_BROKER, counts_toward_oos=False, strategy_validation=False,
        engine_comparison=False,
    ),
}


def spec(mode: str | None) -> ModeSpec:
    if mode not in SPECS:
        raise ModeError(UNKNOWN_MODE, f"--mode must be one of {', '.join(MODES)}; got {mode!r}")
    return SPECS[mode]


def venue_of(base_url: str) -> str:
    host = urlparse(base_url).hostname or ""
    if host in MAINNET_HOSTS:
        return "MAINNET"
    if host in TESTNET_HOSTS:
        return "TESTNET"
    return f"UNKNOWN:{host}"


def broker_venue(broker_name: str) -> str:
    return "TESTNET" if broker_name == TESTNET_BROKER else "VIRTUAL"


def check_pair(data_base: str, broker_name: str) -> None:
    """Candles and fills must come from the same venue.

    MAINNET data goes with the SIMULATED broker only. TESTNET data goes with
    the testnet broker only.
    """
    data = venue_of(data_base)
    fills = broker_venue(broker_name)
    ok = (data == "MAINNET" and fills == "VIRTUAL") or (data == "TESTNET" and fills == "TESTNET")
    if not ok:
        raise ModeError(MIXED_DATA_BROKER_MODE,
                        f"{data} market data with {broker_name} fills; candles and fills must come from one venue")


def resolve(mode: str | None, *, data_base: str | None = None, broker_name: str | None = None) -> ModeSpec:
    """Validate a requested mode, with optional overrides of its data host and broker."""
    s = spec(mode)
    check_pair(data_base or s.data_base, broker_name or s.broker)
    if (data_base and venue_of(data_base) != s.data_venue) or (broker_name and broker_name != s.broker):
        raise ModeError(MIXED_DATA_BROKER_MODE,
                        f"{s.mode} uses {s.data_venue} data with the {s.broker} broker")
    return s
