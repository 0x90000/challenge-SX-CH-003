def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return {"kind": "rule", "fitted": False, "observations": len(rows)}


def infer(history, account, model, state):
    upper = max(r["high"] for r in history[:-1])
    lower = min(r["low"] for r in history[:-1])
    current = history[-1]
    held = account["positions"][current["symbol"]]
    signal = current["close"] > upper
    desired = 8 if signal else 0 if current["close"] < lower else held
    orders = api["target"](desired, history)
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
