"""Screener trading-tool context and the manual trade journal (owner D188, logged D208/D215).

Display and journal only: nothing here opens, sizes or closes a paper or real trade, and nothing reads
or writes exchange keys. The paper engine, the watchers and the frozen CANDLE12 V3 OOS-2 files are not
imported for writing; the BTC regime is read through the live passed-book clock (read-only).
"""
