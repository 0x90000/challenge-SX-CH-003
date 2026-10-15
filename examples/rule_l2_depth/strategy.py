def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return {"kind": "rule", "fitted": False, "observations": len(rows)}


def infer(history, account, model, state):
    signal = api["features"](history, ["depth"])[0]
    orders = api["target"](8 if signal > 0.1 else 0, history)
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
