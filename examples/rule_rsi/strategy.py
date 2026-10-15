def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return {"kind": "rule", "fitted": False, "observations": len(rows)}


def infer(history, account, model, state):
    changes = [b["close"] - a["close"] for a, b in zip(history, history[1:])]
    gain = sum(max(v, 0) for v in changes)
    loss = sum(max(-v, 0) for v in changes)
    rsi = 100 * gain / (gain + loss) if gain + loss else 50
    symbol = history[-1]["symbol"]
    held = account["positions"][symbol]
    desired = 8 if rsi < 30 else 0 if rsi > 70 else held
    signal = rsi
    orders = []
    if desired != held:
        orders = [
            {
                "type": "market",
                "symbol": symbol,
                "side": "buy" if desired > held else "sell",
                "quantity": abs(desired - held),
            }
        ]
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
