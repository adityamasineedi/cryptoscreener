# Strategy plans

How each strategy in the bot is planned: timeframe, entry, exit, and coins. This is a map of the code. It is not a claim that a strategy is profitable, and it does not change any rule.

Source of the 28 combination plans: `backend/app/research/bos_combinations.py`.  
Shared stop and target: `docs/LONG_STRATEGY.md` and `backend/app/signals/config.py` (`min_rr` default **2.0**).  
Paper coins and stake: `backend/app/research/passed_cap_book.py` and `backend/app/research/v1_production.py`.

## Shared plan

Most combinations do not define their own stop or target. They only choose which gates must pass. After a gate passes, the same engines build the trade.

| Piece | Plan |
|---|---|
| Setup candle | A **closed** candle. The signal is known at that close. |
| Usual setup timeframe | **1h** for the paper books. Research can run the same gates on 15m or 4h. |
| Higher timeframe | **4h and 1h**, and only when that combination requires HTF alignment. |
| 15m | Used as confirmation on the v2 and COMBO_03 transition plans. Not the v1 long setup. |
| Long entry | Setup trend is bullish (higher high and higher low) and a bullish break of structure is confirmed, plus whatever extra gates that combination lists. |
| Short entry | The mirror: bearish trend (lower high and lower low) and a bearish break of structure, plus that combination's extra gates. |
| Stop | Structural swing on the protective side of the entry, then an ATR buffer (default 0.2 × ATR). A long stop must sit below the entry. A short stop must sit above it. No stop means no trade. |
| Take profit | **TP1** is the first structural level at least **2R** away. If none exists, TP1 is 2R. Paper exits at the **stop** or at **TP1**. |
| Size | Risk dollars ÷ distance from entry to stop. |
| One position | The passed paper book allows one open position per coin, whichever strategy got there first. |

## Coins

A combination does not name its coins. The book that runs it does.

**Paper universe (27 coins).** Large caps risk **2%** ($20 on a $1,000 account). Mid caps risk **1%** ($10). A trade against the BTC daily trend is halved.

| Group | Coins | Risk |
|---|---|---|
| Large | BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, AVAX, LINK, DOT, BCH, LTC, TON | 2% |
| Mid | NEAR, ATOM, APT, ARB, OP, SUI, INJ, AAVE, UNI, FIL, ETC, ICP, RENDER | 1% |

**Which side may open.** BTC's daily trend picks the side for the passed book. Bearish allows the short rows below. Bullish allows the one long row. Neutral allows none.

**COMBO_02 v1 long watcher** uses the same 27 coins, all at **2%**, and only takes longs. It does not use the daily-trend switch. Its own 4h and 1h alignment has to be bullish.

## Passed paper book

Armed for paper. Still not a live-exchange order.

| Strategy | Timeframe | Entry | Exit | Coins | When it may open |
|---|---|---|---|---|---|
| COMBO_02_V2 | 1h, with 4h and 15m | Regime router. Trend: 1h break of structure and a soft 4h gate (4h may be neutral). Range: liquidity sweep plus a 15m confirm. Reversal: sweep or change of character, plus 15m confirm. | Shared stop / TP1 | 27-coin paper universe | Short while BTC daily is bearish |
| COMBO_02_V2_1B | 1h, 15m required in chop | Same as v2, except a choppy range must have a real 15m confirmation. | Shared stop / TP1 | same | Short while daily is bearish |
| COMBO_02_V2_1_A | 1h, 15m | Same as v2, except choppy markets wait. No range trade in chop. Same plan as the id `COMBO_02_V2_1A`. | Shared stop / TP1 | same | Short while daily is bearish |
| COMBO_02 | 1h setup, 4h+1h hard gate | Break of structure, setup trend agrees, and 4h plus 1h structure agree. HTF candles must already be closed. | Shared stop / TP1 | same for this short row; the separate v1 watcher (below) is long-only | Short while daily is bearish |
| COMBO_02_CLOSED_HTF | 1h setup, 4h+1h | Same gates as COMBO_02, including fully closed HTF candles. Stored as its own id so it is not labeled v1. | Shared stop / TP1 | same | Short while daily is bearish |
| COMBO_03_TRANSITION | 1h shift, 15m confirm | Eligible regime (chop, range, high-volatility range, or transition), liquidity sweep and rejection, then a 1h structure shift, then a real 15m confirmation. | Shared stop / TP1 | same | Short while daily is bearish |
| COMBO_03_TRANSITION_B | 1h | Same sweep, rejection, and 1h shift. 15m confirmation is not required. A long is skipped while the setup trend is bearish. | Shared stop / TP1 | same | Short while daily is bearish. **Long only while daily is bullish.** |
| COMBO_01 | 1h | Break of structure only. No trend gate and no higher-timeframe gate. | Shared stop / TP1 | same | Short while daily is bearish |
| COMBO_02_LOCAL | 1h | Break of structure plus setup-timeframe trend. No 4h gate. Research comparison against COMBO_02. | Shared stop / TP1 | same | Short while daily is bearish |

## COMBO_02 v1 long watcher

This is the frozen long book (`v1-combo02-long-htf`). The watcher is on. It opens only when that coin's own gates pass.

| | Plan |
|---|---|
| Strategy | COMBO_02, long only |
| Timeframe | 1h setup. 4h and 1h must both be bullish. Optional 4h swing books for BTC and ETH exist and are off. 15m is research and is off. |
| Entry | Bullish structure, confirmed bullish break of structure, 4h+1h aligned on fully closed candles. |
| Exit | Stop or TP1 (at least 2R). |
| Coins | The same 27, each at 2%. The original evidence set was BTC, ETH, and SOL. The other 24 were added for paper watch. |
| Also off | BTC/ETH/SOL 15m books. |

## Research combinations (not armed)

Same shared stop and TP1 unless the row says otherwise. No fixed coin list: backtests choose the symbols. They are not in the passed paper book.

| Strategy | Timeframe | Entry beyond the shared plan |
|---|---|---|
| COMBO_02_V2_1A | 1h, 15m | Duplicate id of COMBO_02_V2_1_A (choppy markets wait). |
| COMBO_03 | 1h | Break of structure, trend agrees, pullback confirmed. |
| COMBO_03_TRANSITION_C | 1h, 15m | Transition baseline plus a displacement impulse on the structure-shift bar. |
| COMBO_04 | 1h | Break of structure, trend, impulse, and pullback. |
| COMBO_04_HTF | 1h, 4h+1h | COMBO_04 plus the hard 4h+1h gate. Planned as the short reverse of the v1 long. |
| COMBO_05 | 1h | COMBO_04 plus relative volume. |
| COMBO_06 | 1h | Break of structure, trend, pullback, and a supply/demand zone. |
| COMBO_07 | 1h | COMBO_05 plus a supply/demand zone. |
| COMBO_08 | 1h | COMBO_07 plus reward at least 2R. |

### Supply / demand research (10)

These do not use the COMBO_01–08 gate stack. Planned structure, from `backend/app/research/sd_structure/variants.py`:

| Piece | Plan |
|---|---|
| Director | **4h or 1d**. No entry against a clear higher-timeframe trend. |
| Trigger | **15m or 5m** change of character. |
| Entry | Body close, not a wick. Inside-bar swings are ignored. Swings inside an active trend range are ignored. |
| Hold | At most **48** trigger bars (12 hours on 15m, 4 hours on 5m). |
| Coins | Not fixed. Research only. Not a live signal. |

| Strategy | Entry |
|---|---|
| COMBO_SD_A | Zone touch, then change of character. No break of structure. No volume filter. Stop at the pre-signal extreme plus an ATR buffer. |
| COMBO_SD_B | Zone, change of character, then break of structure after a protective swing. No volume filter. |
| COMBO_SD_C12 | SD_B plus breakout relative volume at least 1.2. |
| COMBO_SD_C | SD_B plus breakout relative volume at least 1.5. |
| COMBO_SD_C20 | SD_B plus breakout relative volume at least 2.0. |
| COMBO_SD_C_FRESH | SD_C, and the zone is fresh: at most one touch, and not broken. |
| COMBO_SD_D | SD_C, and entry only after a retest of the broken level holds. |
| COMBO_SD_E | Zone and change of character, plus price/volume divergence on the last two swings. No break of structure. |
| COMBO_SD_F | Zone and break of structure, no change of character, plus a wider range than the previous 20 bars and relative volume at least 1.5. |
| COMBO_SD_LIQ | SD_B only after an equal-high or equal-low sweep (within 0.2%) wicks through and closes back inside, before the change of character. |

## Other named books

These are designed outside the 28 combinations. None of them is armed as a passed-paper strategy.

| Book | Timeframe | Entry | Exit | Coins |
|---|---|---|---|---|
| COMBO_02 v1.1 risk overlay | Same as COMBO_02 v1 | Same long gates, then portfolio limits: cluster heat, at most 2 open positions, daily and symbol loss halts. | Shared stop / TP1 | BTC, ETH, SOL in the overlay design |
| COMBO_02 short research, short-entry research, pullback-rejection research | 1h research | Short-only experiments. `paper_eligible` is false. | Research exits | Research universe |
| Path B (experimental) | 15m setup, 4h and 1h must not conflict | Path A plus impulse, pullback, and TP1 at least 2R. | Shared stop / TP1 | Screener symbols. Auto entry is off. |
| Intraday BOS / zones | 1h direction, 15m zones, 5m setup, 1m execution | Separate research book. | Its own research exits | Not the v1 coin list |
| Five 1m scalp templates | 1m, with 5m and 15m context | BOS retest, sweep reclaim, sweep plus change of character, VWAP pullback, compression retest. | Research replay | Research script only |
| CANDLE12 v2 | **1h, BTC only, long** | Candle-1 bullish break of structure that closes up. Next candle fills on a wick through Candle-1 high, plus 0.02% slippage. | Structural stop (Candle-1 low minus the ATR buffer). Nearest target. Same-bar stop and target counts as the stop. | BTCUSDT. Frozen research. Not paper. |
| CANDLE12 v3 | 1h BTC long in the sealed OOS-2 paper runner | Separate frozen execution test. Outcomes stay sealed until the scheduled read. | That contract's stop and target | BTCUSDT only |
