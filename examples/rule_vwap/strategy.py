def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return {"kind": "rule", "fitted": False, "observations": len(rows)}


def infer(history, account, model, state):
    total = sum(r["volume"] for r in history)
    if total == 0:
        raise ValueError("VWAP is undefined with zero volume")
    vwap = sum(r["close"] * r["volume"] for r in history) / total
    signal = history[-1]["close"] / vwap - 1
    orders = api["target"](6 if signal < -0.005 else 0, history)
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
