def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return api["reinforcement"](rows, "q_learning", "momentum", config["seed"])


def infer(history, account, model, state):
    signal = api["policy"](history, account, model)
    orders = api["target"](signal, history)
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
