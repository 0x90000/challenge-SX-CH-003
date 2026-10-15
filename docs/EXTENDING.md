# 接入新策略、数据与执行引擎

## 新策略

创建一个新目录，写 `manifest.json` 与三个入口 `initialize / train / infer`，然后：

```bash
python -m psrc run --strategy path/to/new_strategy --data data/1m.json --output artifacts/new
```

不需要修改策略注册表；CLI 接收任意目录，suite 发现目录里的 manifest。
新的字段/粒度/动作不允许静默走已有定义，须先更新契约版本、schema、能力验证和一致性测试。
复杂模型训练可接独立后端，但仍须同样的输入输出、错误和模型血缘。

## 新数据供应商

保持原始数据和 provenance；如只是字段别名或 symbol 名称差异，提供显式 compatibility policy。
单位、交易时区、拆分复权、停牌、重复 tick 和粒度聚合均不是本兼容层的默认职责。
新增转换必须说明：输入版本、输出版本、转换依据、是否有损、影响行、前后哈希、可关闭开关、缺数据错误。
不能从 OHLCV 虚构真实 L2/MBO，不得在没有标签的情况下假造监督学习标签。

## 引擎适配器

可信侧实现 `psrc.engine.BacktestEngine` Protocol：

```python
class Adapter:
    def __init__(self, manifest, config): ...
    def on_event(self, event): ...
    def submit(self, orders, event): ...
    def snapshot(self): ...
    def finish(self): ...
```

通过 Python API 注入 `run(..., engine_factory=Adapter)`，保持策略无关。
`tests/test_runtime.py` 中的故障适配器证明替换边界生效并能生成 BACKTEST_FAILED。
参考引擎是确定性 SpotEngine；本项目未声称已实际接入某个第三方交易平台。

接 Backtrader、事件回测器或实盘网关时，首先声明并验证能力：支持的资产、交易日历、时间戳、
订单类型、价格/数量精度、结算货币、做空/融资/T+1、手续费/滑点模型，以及订单状态映射。
构造函数必须在开始训练前或执行前拒绝不支持的能力，不能把目标仓位偷偷转成无法实现的成交。
需补充以下同一组一致性测试：

- 接收/拒绝/部分成交/撤单/过期的状态机和可追踪 engine_order_id。
- 无未来数据；同事件不能依策略刚观察的价格倒填成交。
- 资金和持仓对账、费用、公司行动和多币种换算。
- 实盘幂等 client_order_id、重连查询恢复、部分成交、取消确认、风险限额和 kill switch。
- 不可用时明确失败，不从真实引擎悄悄切回模拟引擎。

这些是后续扩展契约，不在本次最小回测中虚构已实现。
