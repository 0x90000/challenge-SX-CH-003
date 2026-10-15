"""Regenerate the 18 original strategy adapters and deterministic public fixtures."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psrc.codec import write_json  # noqa: E402
from psrc.data import synthetic  # noqa: E402

INITIALIZE = 'def initialize(config):\n    return {"calls": 0}\n\n\n'
RULE_TRAIN = (
    "def train(rows, config):\n"
    '    return {"kind": "rule", "fitted": False, "observations": len(rows)}\n\n\n'
)
FINISH = (
    '    state["calls"] += 1\n'
    '    return {"orders": orders, "state": state, "diagnostics": {"signal": signal}}\n'
)

RULES = [
    (
        "rule_sma",
        "1d",
        ["close"],
        ["target"],
        "Fast/slow moving average crossover",
        """    prices = [r["close"] for r in history]
    fast = sum(prices[-3:]) / 3
    slow = sum(prices) / len(prices)
    signal = fast > slow
    orders = api["target"](8 if signal else 0, history)
""",
    ),
    (
        "rule_rsi",
        "1d",
        ["close"],
        ["market"],
        "RSI contrarian entry with neutral-band holding",
        """    changes = [b["close"] - a["close"] for a, b in zip(history, history[1:])]
    gain = sum(max(v, 0) for v in changes)
    loss = sum(max(-v, 0) for v in changes)
    rsi = 100 * gain / (gain + loss) if gain + loss else 50
    symbol = history[-1]["symbol"]
    held = account["positions"][symbol]
    desired = 8 if rsi < 30 else 0 if rsi > 70 else held
    signal = rsi
    orders = []
    if desired != held:
        orders = [{"type": "market", "symbol": symbol, "side": "buy" if desired > held else "sell", "quantity": abs(desired - held)}]
""",
    ),
    (
        "rule_breakout",
        "1m",
        ["high", "low", "close"],
        ["target"],
        "Donchian breakout with channel exit",
        """    upper = max(r["high"] for r in history[:-1])
    lower = min(r["low"] for r in history[:-1])
    current = history[-1]
    held = account["positions"][current["symbol"]]
    signal = current["close"] > upper
    desired = 8 if signal else 0 if current["close"] < lower else held
    orders = api["target"](desired, history)
""",
    ),
    (
        "rule_vwap",
        "1m",
        ["close", "volume"],
        ["target"],
        "Rolling volume-weighted mean reversion",
        """    total = sum(r["volume"] for r in history)
    if total == 0:
        raise ValueError("VWAP is undefined with zero volume")
    vwap = sum(r["close"] * r["volume"] for r in history) / total
    signal = history[-1]["close"] / vwap - 1
    orders = api["target"](6 if signal < -0.005 else 0, history)
""",
    ),
    (
        "rule_l1_quote",
        "tick",
        ["bid", "ask", "bid_size", "ask_size"],
        ["limit", "cancel"],
        "Inventory-limited L1 passive quoting with explicit cancel/replace",
        """    row = history[-1]
    symbol = row["symbol"]
    held = account["positions"][symbol]
    orders = [{"type": "cancel", "symbol": symbol, "order_id": o["id"]} for o in account["open_orders"] if o["symbol"] == symbol]
    signal = row["bid_size"] - row["ask_size"]
    if held == 0 and signal > 0:
        orders.append({"type": "limit", "symbol": symbol, "side": "buy", "quantity": 4, "price": row["bid"]})
    elif held > 0:
        orders.append({"type": "limit", "symbol": symbol, "side": "sell", "quantity": held, "price": row["ask"]})
""",
    ),
    (
        "rule_l2_depth",
        "tick",
        ["bid", "ask", "bids", "asks"],
        ["target"],
        "Multi-level depth imbalance allocation",
        """    signal = api["features"](history, ["depth"])[0]
    orders = api["target"](8 if signal > 0.1 else 0, history)
""",
    ),
]

SUPERVISED = [
    (
        "sl_ridge",
        "1d",
        ["close"],
        "ridge",
        ["return", "momentum"],
        "L2-regularized linear return regression",
    ),
    (
        "sl_logistic",
        "1m",
        ["close", "volume"],
        "logistic",
        ["return", "volume"],
        "Logistic directional classification",
    ),
    (
        "sl_knn",
        "1m",
        ["open", "high", "low", "close"],
        "knn",
        ["momentum", "range"],
        "Five-neighbor classification on OHLC features",
    ),
    (
        "sl_naive_bayes",
        "tick",
        ["bid", "ask", "bid_size", "ask_size"],
        "naive_bayes",
        ["imbalance", "spread"],
        "Gaussian class-conditional order-book classifier",
    ),
    (
        "sl_stump",
        "tick",
        ["bid", "ask", "bids", "asks", "trade_price", "trade_size"],
        "stump",
        ["depth", "flow"],
        "Squared-error decision stump over depth/trade flow",
    ),
    (
        "sl_perceptron",
        "1d",
        ["open", "high", "low", "close", "volume"],
        "perceptron",
        ["momentum", "range", "volume"],
        "Online mistake-driven perceptron classifier",
    ),
]

RL = [
    (
        "rl_q_learning",
        "1d",
        ["close"],
        "q_learning",
        "momentum",
        "Off-policy maximum-bootstrap Q-learning",
    ),
    (
        "rl_sarsa",
        "1m",
        ["close", "volume"],
        "sarsa",
        "momentum",
        "On-policy SARSA with sampled next action",
    ),
    (
        "rl_expected_sarsa",
        "tick",
        ["bid", "ask", "bid_size", "ask_size"],
        "expected_sarsa",
        "imbalance",
        "Expected SARSA under epsilon-greedy policy",
    ),
    (
        "rl_double_q",
        "tick",
        ["bid", "ask", "bids", "asks"],
        "double_q",
        "depth",
        "Double Q-learning with separated selection/evaluation",
    ),
    (
        "rl_monte_carlo",
        "1d",
        ["close"],
        "monte_carlo",
        "return",
        "Every-visit Monte Carlo return averaging",
    ),
    (
        "rl_actor_critic",
        "1m",
        ["open", "high", "low", "close"],
        "actor_critic",
        "momentum",
        "Softmax actor and temporal-difference state-value critic",
    ),
]


def save(name, kind, granularity, fields, actions, description, source):
    directory = ROOT / "examples" / name
    directory.mkdir(parents=True, exist_ok=True)
    if kind != "rule" and granularity != "tick" and "open" not in fields:
        fields = ["open", *fields]
    write_json(
        directory / "manifest.json",
        {
            "contract_version": "1.0",
            "id": name,
            "kind": kind,
            "granularity": granularity,
            "fields": fields,
            "symbols": ["SIM"],
            "lookback": 8,
            "actions": actions,
            "description": description,
            "reference": "Original educational adaptation; algorithm sources and limits: docs/STRATEGIES.md",
            "config": {"seed": 1729},
        },
    )
    (directory / "strategy.py").write_text(INITIALIZE + source, encoding="utf-8", newline="\n")


def main():
    for name, granularity, fields, actions, description, body in RULES:
        source = RULE_TRAIN + "def infer(history, account, model, state):\n" + body + FINISH
        save(name, "rule", granularity, fields, actions, description, source)
    for name, granularity, fields, algorithm, features, description in SUPERVISED:
        source = (
            f'def train(rows, config):\n    return api["supervised"](rows, "{algorithm}", {features!r})\n\n\n'
            "def infer(history, account, model, state):\n"
            '    signal = api["predict"](history, model)\n'
            '    orders = api["target"](8 if signal else 0, history)\n' + FINISH
        )
        save(name, "supervised", granularity, fields, ["target"], description, source)
    for name, granularity, fields, algorithm, feature, description in RL:
        source = (
            f'def train(rows, config):\n    return api["reinforcement"](rows, "{algorithm}", "{feature}", config["seed"])\n\n\n'
            "def infer(history, account, model, state):\n"
            '    signal = api["policy"](history, account, model)\n'
            '    orders = api["target"](signal, history)\n' + FINISH
        )
        save(name, "rl", granularity, fields, ["target"], description, source)
    for granularity in ("tick", "1m", "1d"):
        write_json(ROOT / "data" / f"{granularity}.json", synthetic(granularity))


if __name__ == "__main__":
    main()
