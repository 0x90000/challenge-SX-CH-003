"""Small deterministic teaching algorithms, shared by independently declared strategies."""

import math
import random


def price(row):
    if "close" in row:
        return row["close"]
    return (row["bid"] + row["ask"]) / 2


def features(history, names):
    row = history[-1]
    p = price(row)
    result = []
    for name in names:
        if name == "return":
            value = p / price(history[-2]) - 1 if len(history) > 1 else 0
            value *= 100
        elif name == "momentum":
            value = (p / price(history[0]) - 1) * 20
        elif name == "range":
            value = (row["high"] - row["low"]) / p * 100
        elif name == "volume":
            average = sum(r["volume"] for r in history) / len(history)
            value = row["volume"] / max(average, 1e-12) - 1
        elif name == "imbalance":
            value = (row["bid_size"] - row["ask_size"]) / max(
                row["bid_size"] + row["ask_size"], 1e-12
            )
        elif name == "depth":
            bid = sum(level[1] for level in row["bids"])
            ask = sum(level[1] for level in row["asks"])
            value = (bid - ask) / max(bid + ask, 1e-12)
        elif name == "spread":
            value = (row["ask"] - row["bid"]) / p * 100
        elif name == "flow":
            value = (row["trade_price"] / p - 1) * row["trade_size"]
        else:
            raise ValueError(f"Unknown feature: {name}")
        result.append(value)
    return result


def target(quantity, history):
    return [{"type": "target", "symbol": history[-1]["symbol"], "quantity": quantity}]


def supervised(rows, algorithm, names):
    x, y = [], []
    for symbol in sorted({r["symbol"] for r in rows}):
        events = [r for r in rows if r["symbol"] == symbol]
        for i in range(7, len(events) - 2):
            x.append(features(events[i - 7 : i + 1], names))
            # Learn the return between executable next-event prices, not the current close.
            first = events[i + 1]
            second = events[i + 2]
            a = first["open"] if "open" in first else price(first)
            b = second["open"] if "open" in second else price(second)
            y.append((b / a - 1) * 100)
    if len(x) < 8:
        raise ValueError("Need at least 18 training events per available symbol")
    labels = [int(v > 0) for v in y]
    width = len(names)
    model = {"algorithm": algorithm, "features": names, "samples": len(x)}
    if algorithm in ("ridge", "logistic", "perceptron"):
        weights = [0.0] * (width + 1)
        for epoch in range(100):
            for row, value, label in zip(x, y, labels):
                vector = [1.0, *row]
                score = sum(a * b for a, b in zip(weights, vector))
                if algorithm == "ridge":
                    error = max(-5, min(5, value - score))
                elif algorithm == "logistic":
                    error = label - 1 / (1 + math.exp(-max(-30, min(30, score))))
                else:
                    error = label - int(score >= 0)
                rate = 0.02 / (1 + epoch * 0.05)
                weights = [
                    w + rate * (error * v - (0.01 * w if j else 0))
                    for j, (w, v) in enumerate(zip(weights, vector))
                ]
        model["weights"] = weights
    elif algorithm == "knn":
        model.update(x=x[-128:], y=labels[-128:], neighbors=5)
    elif algorithm == "naive_bayes":
        classes = []
        for label in (0, 1):
            group = [row for row, lab in zip(x, labels) if lab == label]
            if not group:
                raise ValueError("Gaussian NB requires both classes in training data")
            mean = [sum(row[j] for row in group) / len(group) for j in range(width)]
            var = [
                max(1e-6, sum((row[j] - mean[j]) ** 2 for row in group) / len(group))
                for j in range(width)
            ]
            classes.append({"prior": len(group) / len(x), "mean": mean, "variance": var})
        model["classes"] = classes
    elif algorithm == "stump":
        candidates = []
        for j in range(width):
            values = sorted({row[j] for row in x})
            for threshold in values[:: max(1, len(values) // 16)]:
                left = [lab for row, lab in zip(x, labels) if row[j] <= threshold]
                right = [lab for row, lab in zip(x, labels) if row[j] > threshold]
                if not left or not right:
                    continue
                a, b = sum(left) / len(left), sum(right) / len(right)
                loss = sum((v - a) ** 2 for v in left) + sum((v - b) ** 2 for v in right)
                candidates.append((loss, j, threshold, a, b))
        if not candidates:
            raise ValueError("No nonconstant feature for a decision stump")
        loss, j, threshold, left, right = min(candidates)
        model.update(feature=j, threshold=threshold, left=left, right=right, loss=loss)
    else:
        raise ValueError(f"Unknown supervised algorithm: {algorithm}")
    return model


def predict(history, model):
    vector = features(history, model["features"])
    algorithm = model["algorithm"]
    if algorithm in ("ridge", "logistic", "perceptron"):
        score = sum(w * v for w, v in zip(model["weights"], [1.0, *vector]))
        return score > 0
    if algorithm == "knn":
        distances = sorted(
            (sum((a - b) ** 2 for a, b in zip(vector, row)), i) for i, row in enumerate(model["x"])
        )
        return (
            sum(model["y"][i] for _, i in distances[: model["neighbors"]]) > model["neighbors"] / 2
        )
    if algorithm == "naive_bayes":
        scores = [
            math.log(c["prior"])
            - 0.5
            * sum(
                math.log(v) + (a - m) ** 2 / v for a, m, v in zip(vector, c["mean"], c["variance"])
            )
            for c in model["classes"]
        ]
        return scores[1] > scores[0]
    if algorithm == "stump":
        probability = (
            model["left"] if vector[model["feature"]] <= model["threshold"] else model["right"]
        )
        return probability > 0.5
    raise ValueError(f"Unknown model algorithm: {algorithm}")


def market_state(history, feature):
    value = features(history, [feature])[0]
    return 0 if value < -0.05 else 2 if value > 0.05 else 1


def _best(values):
    return max(range(len(values)), key=lambda a: values[a])


def _probabilities(values):
    peak = max(values)
    exp = [math.exp(v - peak) for v in values]
    return [v / sum(exp) for v in exp]


def reinforcement(rows, algorithm, feature, seed):
    if len({r["symbol"] for r in rows}) != 1 or len(rows) < 20:
        raise ValueError("This teaching RL environment requires one symbol and >=20 events")
    if algorithm not in {
        "q_learning",
        "sarsa",
        "expected_sarsa",
        "double_q",
        "monte_carlo",
        "actor_critic",
    }:
        raise ValueError("Unknown reinforcement algorithm")
    rng = random.Random(seed)
    q = [[0.0] * 3 for _ in range(9)]
    second = [[0.0] * 3 for _ in range(9)]
    values = [0.0] * 9
    counts = [[0] * 3 for _ in range(9)]
    states = [market_state(rows[max(0, i - 7) : i + 1], feature) for i in range(len(rows))]
    alpha, gamma, epsilon = 0.15, 0.9, 0.2
    rewards = []
    updates = 0

    def choose(state):
        if algorithm == "actor_critic":
            return rng.choices(range(3), weights=_probabilities(q[state]))[0]
        if rng.random() < epsilon:
            return rng.randrange(3)
        return _best([a + b for a, b in zip(q[state], second[state])])

    for episode in range(40):
        position = 0
        state = states[7] * 3 + position
        action = choose(state)
        trajectory, total = [], 0.0
        for i in range(7, len(rows) - 2):
            a = rows[i + 1]["open"] if "open" in rows[i + 1] else price(rows[i + 1])
            b = rows[i + 2]["open"] if "open" in rows[i + 2] else price(rows[i + 2])
            reward = action * (b / a - 1) * 100 - abs(action - position) * 0.1
            terminal = i == len(rows) - 3
            next_state = states[i + 1] * 3 + action
            next_action = choose(next_state)
            bootstrap = 0 if terminal else 1
            if algorithm == "q_learning":
                q[state][action] += alpha * (
                    reward + bootstrap * gamma * max(q[next_state]) - q[state][action]
                )
            elif algorithm == "sarsa":
                q[state][action] += alpha * (
                    reward + bootstrap * gamma * q[next_state][next_action] - q[state][action]
                )
            elif algorithm == "expected_sarsa":
                expectation = (1 - epsilon) * max(q[next_state]) + epsilon * sum(q[next_state]) / 3
                q[state][action] += alpha * (
                    reward + bootstrap * gamma * expectation - q[state][action]
                )
            elif algorithm == "double_q":
                one, two = (q, second) if rng.random() < 0.5 else (second, q)
                estimate = two[next_state][_best(one[next_state])]
                one[state][action] += alpha * (
                    reward + bootstrap * gamma * estimate - one[state][action]
                )
            elif algorithm == "actor_critic":
                error = reward + bootstrap * gamma * values[next_state] - values[state]
                values[state] += alpha * error
                probabilities = _probabilities(q[state])
                for act in range(3):
                    q[state][act] += 0.05 * error * (int(act == action) - probabilities[act])
            trajectory.append((state, action, reward))
            updates += 1
            total += reward
            position, state, action = action, next_state, next_action
        if algorithm == "monte_carlo":
            gain = 0
            for state, action, reward in reversed(trajectory):
                gain = reward + gamma * gain
                counts[state][action] += 1
                q[state][action] += (gain - q[state][action]) / counts[state][action]
        rewards.append(total)
    if algorithm == "double_q":
        q = [[a + b for a, b in zip(row, other)] for row, other in zip(q, second)]
    return {
        "algorithm": algorithm,
        "feature": feature,
        "q": q,
        "seed": seed,
        "episodes": 40,
        "updates": updates,
        "episode_rewards": rewards,
        "action_quantities": [0, 4, 8],
        "reward_cost": 0.1,
        "reward": "next executable price return in percent minus turnover cost",
        "environment": "single-symbol inventory MDP, no queue/impact, reset flat",
    }


def policy(history, account, model):
    quantity = account["positions"][history[-1]["symbol"]]
    if quantity not in model["action_quantities"]:
        raise ValueError("Inventory outside trained RL action space")
    inventory = model["action_quantities"].index(quantity)
    state = market_state(history, model["feature"]) * 3 + inventory
    return model["action_quantities"][_best(model["q"][state])]


API = {
    "price": price,
    "features": features,
    "target": target,
    "supervised": supervised,
    "predict": predict,
    "reinforcement": reinforcement,
    "policy": policy,
}
