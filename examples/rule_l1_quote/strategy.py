def initialize(config):
    return {"calls": 0}


def train(rows, config):
    return {"kind": "rule", "fitted": False, "observations": len(rows)}


def infer(history, account, model, state):
    row = history[-1]
    symbol = row["symbol"]
    held = account["positions"][symbol]
    orders = [
        {"type": "cancel", "symbol": symbol, "order_id": o["id"]}
        for o in account["open_orders"]
        if o["symbol"] == symbol
    ]
    signal = row["bid_size"] - row["ask_size"]
    if held == 0 and signal > 0:
        orders.append(
            {"type": "limit", "symbol": symbol, "side": "buy", "quantity": 4, "price": row["bid"]}
        )
    elif held > 0:
        orders.append(
            {
                "type": "limit",
                "symbol": symbol,
                "side": "sell",
                "quantity": held,
                "price": row["ask"],
            }
        )
    state["calls"] += 1
    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}
