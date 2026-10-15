def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return api["supervised"](rows, "ridge", ["return", "momentum"])


def infer(history, account, model, state):
    signal = api["predict"](history, model)
    orders = api["target"](8 if signal else 0, history)
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
