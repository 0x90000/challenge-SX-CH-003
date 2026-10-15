# Paper Strategy Runtime Contract 1.0

## 版本和实现边界

`contract_version` 当前精确支持 `1.0`，未知版本报 `VERSION_UNSUPPORTED`。
破坏字段含义、时间语义或订单语义时升主版本；兼容新增能力先声明，再实现适配器。
v1 是单币种、现金现货、多头账户；不自动模拟保证金、借券、A 股 T+1 或交易所涨跌停。
能力不足必须由新适配器显式拒绝，不能用本现货模型假装支持。

机器可读语法见 `schemas/*.schema.json`；跨字段、时序、能力和资金约束由 `psrc/contract.py`
与 `psrc/engine.py` 检查。宿主输入受 16 MiB JSON 字节上限约束，拒绝重复键、NaN 和 Infinity。
JSON 采用 UTF-8；canonical hash 使用 ASCII 转义、键排序、无额外空格的 JSON。

## 策略目录与声明

每个策略目录必须有普通文件 `manifest.json`、`strategy.py`。不执行安装脚本、不动态寻找 entry point。

```json
{
  "contract_version": "1.0",
  "id": "rule_sma",
  "kind": "rule",
  "granularity": "1d",
  "fields": ["close"],
  "symbols": ["SIM"],
  "lookback": 8,
  "actions": ["target"],
  "description": "Fast/slow moving average crossover",
  "reference": "Original educational adaptation; docs/STRATEGIES.md",
  "config": {"seed": 1729}
}
```

`kind` 为 `rule / supervised / rl`；`granularity` 为 `tick / 1m / 1d`；
`lookback` 为 1..1024；`symbols`、`fields`、`actions` 非空、无重复。
未知字段、未知动作、额外声明键立即失败。配置默认空对象，不擅自修改策略参数。
`reference` 说明研究来源与简化范围，不能省略或将引用冒充精确复现。

## 市场事件

行情包：`{granularity, provenance, rows}`。每行必有 `timestamp`（UTC 整数毫秒）和 `symbol`。
全局时间非递减；每个 symbol 时间严格递增。不同标的可同时间，文件顺序稳定处理；同标的同时间的多条 tick
在 v1 中拒绝，需要上游明确合并或在下一契约版本引入 sequence，不能丢弃重复事件。

| 字段 | 类型 / 含义 |
|---|---|
| open / high / low / close | 有限正数，满足 OHLC 包络关系 |
| volume | 非负数，报价币/基础币单位由数据来源声明，此例为基础单位 |
| bid / ask | 有限正数，bid ≤ ask |
| bid_size / ask_size | 非负基础资产数量 |
| bids / asks | 1..100 层 `[price, size]`；bid 严格降序、ask 严格升序；双侧不得交叉 |
| trade_price / trade_size | 正成交价 / 非负成交量 |

若同时提供 L1 与 L2，其最优价格必须一致。声明所需字段缺失报 `FIELD_MISSING`。
bar 的时间代表**已经完整观测的收盘时刻**，对齐 UTC 分钟或 UTC 日边界；允许真实休市缺口，绝不自动补行。
tick 不强制均匀间隔。分钟与日线撮合额外要求 OHLC；tick 撮合额外要求 bid/ask。
这些撮合字段属于引擎能力要求，只有策略声明的字段会投影到策略历史窗口。

## 生命周期及入口

```python
def initialize(config: dict) -> dict: ...
def train(rows: list[dict], config: dict) -> dict: ...
def infer(history: list[dict], account: dict, model: dict, state: dict) -> dict: ...
```

以上是规范类型说明；受限策略文件中入口不写类型注解。`initialize` 返回 JSON 状态；
`train` 返回非空 JSON 模型；规则型无需拟合，但也经过同一入口并输出显式 `fitted: false` 元数据。
`infer` 返回 `{orders: [...], state: {...}, diagnostics: {...}}`。
每次最多 32 个订单，空列表表示有意不下单；不能用异常替代空列表，也不能把异常变成空列表。
单次运行累计推理响应上限 16 MiB，超出以 RESOURCE_LIMIT 失败，已记录日志仍可审计。

生命周期：验证 → 初始化训练 → 训练 → 宿主保存 → 结束训练进程 → 宿主加载校验 →
新进程初始化推理 → 逐事件推理/回测 → 完成，任一步异常 → 失败报告 → 释放工作进程。
保存/加载由可信宿主实现，策略不获得文件句柄。
`api` 是受限环境提供的数学/策略 helper 字典，具体支持函数见 `psrc/learning.py:API`；没有 I/O、网络或账户修改能力。

`run` 按全局时间分割训练/评估，默认 60%/40%，同一 timestamp 不跨分割。
评估按标的分别预热 `lookback` 条，窗口只含该标的过去和当前已观测数据。
未完成预热不调用策略；不足以产生任何推理，或某标的始终不足预热，会明确失败。
训练使用单独进程；模型跨进程传递为 JSON；每次调用的模型、状态、账户与行情均是 JSON 副本。

## 账户和订单

账户：`{cash, positions: {symbol: quantity}, equity, open_orders}`。策略快照用有限 JSON 数字，
可信账本内部用 Decimal；最终报告的资金、价格、手续费、数量用十进制字符串，保留精度，
**不保证小数末尾零的个数**。单位为单一报价币及基础资产单位。

| type | 必填字段 | 含义 |
|---|---|---|
| market | symbol, side=buy/sell, quantity>0 | 下一该标的事件市价成交 |
| limit | 上述字段及 price>0 | 后续行情触价才成交，价格不可劣于限价 |
| target | symbol, quantity≥0 | 转为当前库存与目标库存之间的市价差量，已达目标则不产生订单 |
| cancel | symbol, order_id | 显式撤掉仍开放的同标的订单，未知/已关闭订单报错 |

数量须为 lot_size 整数倍；限价须为 tick_size 整数倍。禁止做空、超持仓上限、超可用现金，
挂单占用库存或估计现金。目标持仓与同标的未完成挂单并存会报错，避免含混的重复加仓。
订单按列表顺序接收，不承诺整个批次事务回滚；失败报告保留已发生事实，后续不继续执行。

状态：open → filled / cancelled / expired。没有假成交，也不在期末偷偷强制平仓。
当前事件先撮合之前订单，再更新账户和估值，最后策略观察当前事件并提交新订单。
市价 bar 用下一根 open 加方向性滑点；tick 用下一条 ask/bid 加滑点；手续费为成交金额 × fee_rate。
bar 限价触及 low/high 后，用 next open 和 limit 中更优者；tick 只按可见对手价触价。
限价不额外加滑点。所有订单最多全量成交一次。期末剩余挂单过期并注明 `end_of_data`。
成交 timestamp 是用于撮合的行情事件时间（bar 为收盘标签），不是重建的盘中成交时间。
现金缺口（如开盘跳空）会报 `BACKTEST_FAILED`，不会临时降低数量或融资。

最小引擎明确采用全量成交，无队列、部分成交、成交量参与率或市场冲击模型。
因此报告是接口验证证据；容量、MBO、真实交易所限制需专用适配器。

## 模型与审计

`model.json`：契约版本、策略 ID、源代码+声明哈希、SDK 算法哈希、配置哈希、训练数据哈希、
训练结束时刻、训练行数、payload、payload 哈希和整个 envelope 哈希。
拒绝缺文件、改坏 payload、不匹配策略/配置/SDK，以及训练结束不早于推理输入的模型。
哈希用于完整性与血缘检查，**不是数字签名**；有能力重写整个模型的人可重算哈希，来源真实性仍须通过提交包哈希及可信保管保证。
不接受 pickle/joblib，不执行模型反序列化代码。采用同目录临时文件+fsync+replace 原子保存。

兼容 policy 必须含 `enabled, field_aliases, symbol_map, reason`。转换仅支持字段重命名和一对一标的映射，
禁止覆盖现有字段、映射碰撞、时间/标的身份字段重命名。审计含转换实现版本、依据、可关闭标记、
完整 policy、逐行变更、影响行数和输入/输出哈希。禁用时仍记审计，再由严格校验检查原始数据。
不支持隐式单位转换、粒度变换、缺字段填零、OHLC 构造或 L2→MBO 推断。

## 错误对象

```json
{"code":"FIELD_MISSING","stage":"validation","message":"Required market fields are missing","details":{"row":0,"fields":["close"]},"retryable":false}
```

| 错误码 | 含义 |
|---|---|
| SCHEMA_INVALID / VERSION_UNSUPPORTED | 结构或版本错误 |
| FIELD_MISSING / FIELD_UNKNOWN | 数据字段缺失或未知 |
| GRANULARITY_MISMATCH / SYMBOL_UNMAPPED / DATA_INVALID | 粒度、标的或数据不合法 |
| DATA_LEAKAGE | 模型训练时间与推理数据重叠 |
| MODEL_MISSING / MODEL_INVALID / MODEL_INCOMPATIBLE | 缺模型、损坏或血缘不匹配 |
| ACTION_INVALID / ORDER_INVALID | 动作 schema 或实际交易限制违规 |
| INITIALIZATION_FAILED / TRAINING_FAILED / INFERENCE_FAILED / BACKTEST_FAILED | 生命周期执行异常 |
| STRATEGY_LOAD_FAILED / SANDBOX_VIOLATION | 策略不合法或危险能力请求 |
| SANDBOX_TIMEOUT / RESOURCE_LIMIT / SANDBOX_CRASH / SANDBOX_UNAVAILABLE | 时间、资源、进程或隔离能力故障 |
| COMPATIBILITY_INVALID / COMPATIBILITY_CONFLICT | 转换配置非法或数据冲突 |
| INPUT_MISSING / IO_FAILED / OUTPUT_EXISTS / RUNTIME_FAILED | 输入、文件、证据目录或未分类宿主故障 |

报告包括原始策略声明、所需字段、实际输入、训练区间和哈希、模型产物、逐次推理、订单/成交/净值、
手续费、回撤、P&L、是否无成交、兼容审计、沙箱限制、生命周期及失败原因。
失败时 status=failed，backtest 不呈现成功汇总，保留已有日志和 partial_account。
空订单可以是正常策略行为，`no_trades=true` 明示。没有异常自动重试或降级。
若输出目录不可写或已含证据，CLI 返回机器错误和非零退出码；不可能承诺在不可写目录里生成报告。

## 分阶段运行

准备两个按时间不重叠的同 schema 行情文件，训练集须覆盖声明字段：

```bash
python -m psrc train --strategy examples/sl_ridge --data train.json --output artifacts/train
python -m psrc infer --strategy examples/sl_ridge --data future.json --model artifacts/train/model.json --output artifacts/infer
python -m psrc backtest --strategy examples/sl_ridge --data future.json --model artifacts/train/model.json --output artifacts/backtest
```

`infer` 对 future.json 最后一个事件的完整 lookback 窗口执行一次决策，账户为初始现金与零持仓，
不提交订单；有状态逐事件推理使用 `backtest` 或外部适配器。
可选 `--config configs/default.json`、`--compatibility configs/compatibility.example.json`。
