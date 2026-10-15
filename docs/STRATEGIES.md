# 18 个示例的差异、研究来源与限制

这些是原创编写的可运行教学基线，演示论文思想如何通过统一接口接入；没有复制第三方实现、模型权重或论文实验数据。
不声称复现任何论文的收益、训练规模或全部假设。任务允许模拟行情，三个数据包由固定种子生成，包含明显周期结构，
用于可重复工程验证，不用于比较投资价值。

每个策略有独立声明和入口；共享的是特征、模型算法和运行契约。下面的差异是算法或决策机制差异，
不是把同一个策略改六组参数充数。

| 目录 | 类别 / 粒度 | 数据与决策差异 | 输出 |
|---|---|---|---|
| rule_sma | 规则 / 日 | 收盘价的快慢均线交叉 | 目标库存 |
| rule_rsi | 规则 / 日 | 上涨/下跌幅度构造 RSI，超买超卖触发，中性区持仓 | 市价差量 |
| rule_breakout | 规则 / 分钟 | 历史 high/low 通道突破与退出 | 目标库存 |
| rule_vwap | 规则 / 分钟 | 成交量加权价格的均值回归 | 目标库存 |
| rule_l1_quote | 规则 / tick | L1 价格/数量，库存上限，逐事件撤旧单再挂限价 | 限价+撤单 |
| rule_l2_depth | 规则 / tick | 多层 bid/ask 深度失衡 | 目标库存 |
| sl_ridge | 监督 / 日 | 下一可执行时段收益；L2 正则线性回归 | 目标库存 |
| sl_logistic | 监督 / 分钟 | return/volume 特征；逻辑损失与 sigmoid 梯度 | 目标库存 |
| sl_knn | 监督 / 分钟 | momentum/range 特征；五近邻投票 | 目标库存 |
| sl_naive_bayes | 监督 / tick | L1 失衡/价差；分类型高斯均值方差与先验 | 目标库存 |
| sl_stump | 监督 / tick | L2 深度与逐笔成交方向；最小平方损失分裂树桩 | 目标库存 |
| sl_perceptron | 监督 / 日 | momentum/range/volume；仅错误分类时在线更新 | 目标库存 |
| rl_q_learning | RL / 日 | momentum×库存状态，离策略 max-Q bootstrap | 0/4/8 目标库存 |
| rl_sarsa | RL / 分钟 | momentum×库存状态，实际下一探索动作 bootstrap | 同上 |
| rl_expected_sarsa | RL / tick | L1 失衡×库存，epsilon-greedy 期望 bootstrap | 同上 |
| rl_double_q | RL / tick | L2 深度×库存，双 Q 表分离动作选择与估值 | 同上 |
| rl_monte_carlo | RL / 日 | return×库存，整回合折扣回报的 every-visit 均值 | 同上 |
| rl_actor_critic | RL / 分钟 | momentum×库存，softmax actor 与 TD value critic | 同上 |

## 训练是否真实发生

规则型走训练入口但明确 `fitted=false`。监督学习用训练区间有界历史构建特征，标签取下一可执行时段收益；
岭回归/逻辑回归/感知机更新权重；KNN 存训练样本；NB 估类条件分布；树桩搜索分裂。
Naive Bayes 缺少任一类别、树桩没有可分裂特征、训练行数不足都会明确失败。
监督策略需要历史窗口 8，至少 18 条训练事件形成足够标签。

六个 RL 算法各训练 40 回合，使用种子 1729 的独立 PRNG，9 个离散状态（3 档市场特征 × 3 档库存）、3 个动作。
奖励使用下一可执行价格到再下一可执行价格的收益百分比，减库存调整成本 `0.1 × |Δaction|`。
终止状态不 bootstrap；每回合重置为零库存。模型记录 episodes、updates、episode_rewards、Q/偏好表、动作空间与奖励定义。
训练时不使用回测区间。训练环境采用简化单位库存奖励；tick 使用 mid 计算奖励，实际回测使用 bid/ask、手续费和滑点，
所以训练回报不是回测净值。该环境只支持单标的，多标的训练明确拒绝。

## 概念参考

- Brock, Lakonishok & LeBaron (1992), *Simple Technical Trading Rules and the Stochastic Properties of Stock Returns*, DOI: [10.1111/j.1540-6261.1992.tb04681.x](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x)。均线/突破的历史研究背景，非逐表复现。
- J. Welles Wilder (1978), *New Concepts in Technical Trading Systems*。RSI 的概念来源；本例是窗口和而非 Wilder 递推平滑版本。
- Hoerl & Kennard (1970), *Ridge Regression: Biased Estimation for Nonorthogonal Problems*, DOI: [10.1080/00401706.1970.10488634](https://doi.org/10.1080/00401706.1970.10488634)。本例用有界梯度更新实现小型正则回归。
- Cover & Hart (1967), *Nearest Neighbor Pattern Classification*, DOI: [10.1109/TIT.1967.1053964](https://doi.org/10.1109/TIT.1967.1053964)。KNN 概念背景。
- Rosenblatt (1958), *The Perceptron*, DOI: [10.1037/h0042519](https://doi.org/10.1037/h0042519)。错误驱动更新。
- Sutton & Barto (2018), *Reinforcement Learning: An Introduction*, [公开作者页面](http://incompleteideas.net/book/the-book-2nd.html)。MC、TD、SARSA、Q-learning、actor-critic 方法背景。
- van Hasselt (2010), *Double Q-learning*, [NeurIPS 论文页](https://proceedings.neurips.cc/paper/2010/hash/091d584fced301b442654dd8c23b3fc9-Abstract.html)。双估值器方法背景。

其他 VWAP、L1/L2 失衡、逻辑回归、Gaussian NB 和树桩采用常见定义，由本项目独立实现。
引用只作为概念来源，不随仓库分发受版权保护的书籍或论文全文。

## 样例数据

`data/{tick,1m,1d}.json` 各 240 个事件、symbol=SIM；UTC 时间从 2024-01-01 后第一时间步开始。
分钟/日线有 OHLCV，tick 有 L1/L2/逐笔成交；模拟数据不代表交易所真实逐笔事件协议。
原始生成逻辑见 `psrc/data.py`，可用 `python scripts/build_examples.py` 再生成。
数据无需账户、网络、API key 或外部授权。
