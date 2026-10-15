def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return {"kind": "rule", "fitted": False, "observations": len(rows)}


def infer(history, account, model, state):
    prices = [r["close"] for r in history]
    fast = sum(prices[-3:]) / 3
    slow = sum(prices) / len(prices)
    signal = fast > slow
    orders = api["target"](8 if signal else 0, history)
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
