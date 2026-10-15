from psrc.data import synthetic
from psrc.learning import features, predict, reinforcement, supervised

from .helpers import Case


class LearningTests(Case):
    def test_supervised_algorithms_learn_both_directions(self):
        rows = synthetic("1d", 140)["rows"]
        for algorithm in ("ridge", "logistic", "perceptron", "knn", "naive_bayes", "stump"):
            with self.subTest(algorithm=algorithm):
                model = supervised(rows, algorithm, ["return", "momentum"])
                predictions = [predict(rows[i - 7 : i + 1], model) for i in range(8, 140)]
                self.assertIn(True, predictions)
                self.assertIn(False, predictions)
                self.assertGreater(model["samples"], 100)

    def test_rl_seed_determinism_and_real_updates(self):
        rows = synthetic("1d", 50)["rows"]
        for algorithm in (
            "q_learning",
            "sarsa",
            "expected_sarsa",
            "double_q",
            "monte_carlo",
            "actor_critic",
        ):
            with self.subTest(algorithm=algorithm):
                a = reinforcement(rows, algorithm, "momentum", 7)
                b = reinforcement(rows, algorithm, "momentum", 7)
                self.assertEqual(a, b)
                self.assertEqual(a["updates"], 40 * (50 - 9))
                self.assertTrue(any(v != 0 for row in a["q"] for v in row))

    def test_degenerate_training_data_fail_explicitly(self):
        rows = synthetic("1d", 30)["rows"]
        with self.assertRaises(ValueError):
            supervised(rows[:5], "ridge", ["return"])
        for row in rows:
            row["open"] = row["close"] = 100
        for algorithm in ("naive_bayes", "stump"):
            with self.assertRaises(ValueError):
                supervised(rows, algorithm, ["return"])
        with self.assertRaises(ValueError):
            features(rows, ["unknown"])
        with self.assertRaises(ValueError):
            reinforcement(rows[:5], "q_learning", "return", 0)
