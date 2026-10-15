"""Deterministic synthetic markets. No downloaded or restricted market data."""

import math
import random

from .contract import GRANULARITIES


def synthetic(granularity, count=240, seed=1729):
    rng = random.Random(seed)
    step = GRANULARITIES[granularity] or 1000
    rows = []
    previous = 100.0
    for i in range(count):
        center = 100 + 6 * math.sin(i / 9) + 2 * math.sin(i / 3) + i * 0.015
        close = round(center + rng.uniform(-0.1, 0.1), 2)
        opening = previous
        bid, ask = round(close - 0.02, 2), round(close + 0.02, 2)
        demand = int(100 + 70 * math.cos(i / 9))
        supply = 200 - demand
        row = {
            "timestamp": 1_704_067_200_000 + (i + 1) * step,
            "symbol": "SIM",
            "open": opening,
            "high": round(max(opening, close) + 0.15, 2),
            "low": round(min(opening, close) - 0.15, 2),
            "close": close,
            "volume": 1000 + (i * 31) % 800,
            "bid": bid,
            "ask": ask,
            "bid_size": demand,
            "ask_size": supply,
            "bids": [[bid, demand], [round(bid - 0.01, 2), demand + 20]],
            "asks": [[ask, supply], [round(ask + 0.01, 2), supply + 20]],
            "trade_price": ask if i % 3 else bid,
            "trade_size": 1 + i % 20,
        }
        if granularity == "tick":
            for name in ("open", "high", "low", "close", "volume"):
                del row[name]
        rows.append(row)
        previous = close
    return {
        "granularity": granularity,
        "rows": rows,
        "provenance": f"Original synthetic fixture; seed={seed}; generator=psrc.data/1.0; no real market claims",
    }
