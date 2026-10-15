# Paper Strategy Runtime Contract · SX-CH-003

面向 SX-CH-003 S 档要求的 Python 参考实现：把规则策略、监督学习策略和强化学习策略，
通过同一份可校验契约接入训练、模型保存/加载、推理和事件驱动回测。

**运行依赖只有 Python 3.11+ 标准库。** 自带 18 个独立策略目录、3 份确定性模拟行情、
结构化失败、显式兼容审计和受限策略执行。目标是可复现的工程契约，合成行情结果不代表真实收益。

## 一键验收

在仓库根目录执行：

```bash
python scripts/verify.py
```

顺序运行单元/集成/安全测试、18 策略全流程、10 个故障/兼容演示、10 万事件性能测量，
输出到新的 `artifacts/verify-时间戳/`。任一步失败，退出码非零。整个流程不访问网络。
想收集行/分支覆盖率与 JUnit 结果：

```bash
python -m pip install -r requirements-dev.txt
python scripts/verify.py --coverage --output artifacts/review
```

策略运行示例：

```bash
python -m psrc run --strategy examples/sl_ridge --data data/1d.json --output artifacts/ridge
python -m psrc suite --output artifacts/all-strategies
python -m unittest discover -v
```

`run` 默认按时间 60% 训练、40% 回测；回测有独立预热窗口。输出目录必须不存在或为空，防止覆盖证据。
`train`、`infer`、`backtest` 也可独立调用，后两者必须传 `--model`，且输入最早时间严格晚于模型训练结束时间。
详见 [契约](docs/CONTRACT.md) 的分阶段命令。CLI 的任何失败均返回非零退出码。

## 工程目录

```text
psrc/                  契约、兼容层、隔离执行、撮合、生命周期、学习算法、CLI
examples/<strategy>/   独立 manifest.json 和 strategy.py，共 18 个
data/                  tick / 1m / 1d 模拟行情，固定种子 1729
configs/               回测参数、显式兼容转换示例
schemas/               JSON Schema 2020-12 机器可读定义
tests/                 契约、账本、模型、泄漏、安全、算法与全策略测试
scripts/               一键验收、证据打包、故障演示、性能基准、资源再生成
docs/                  技术路线、规范、策略差异、沙箱、扩展、规则与来源
artifacts/             运行产物（不进版本控制）
```

设计依据与验收映射见 [技术路线](docs/PLAN.md)；接口细节见 [契约](docs/CONTRACT.md)；
18 个示例的差异和引用见 [策略说明](docs/STRATEGIES.md)。

## 关键行为

- 策略只能观察已发生且声明过的行情字段、有界历史窗口及账户副本。
- 信号在当前事件结束后产生，只能在**下一条该标的事件**成交。不允许同事件成交。
- Decimal 记现金、手续费和持仓；包含市价、限价、目标持仓、撤单、挂单和期末过期。
- 训练和推理使用不同进程；JSON 模型校验策略、SDK、配置、数据及载荷哈希，禁止 pickle。
- 缺字段、错粒度、未知标的、缺模型、非法订单、训练/回测失败均生成机器可读错误。
- 字段重命名与标的映射必须显式启用，记录依据、影响行和前后哈希；不自动重采样或补行情。
- 策略执行有导入/属性白名单、审计钩子、超时、指令预算与 256 MiB OS 内存限制。

## 隔离容器运行

```bash
docker build -t psrc:1.0 .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --memory 768m --cpus 2 \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --mount type=volume,source=psrc-results,target=/results psrc:1.0
```

镜像首次构建需要拉取基础镜像；执行阶段不访问网络。结果保存在命名卷 `psrc-results`。
重复运行须指定新的 `--output /results/<新目录>`。容器是额外隔离边界；不能把 Python AST 检查当成任意 Python 代码的万能沙箱。
宿主实现的限制、威胁模型与本机验证范围见 [安全说明](docs/SECURITY.md) 和 [验证记录](docs/VALIDATION.md)。

## 证据与提交

统一验收结果是 `verification.json`，其链接指向测试、18 策略汇总、故障矩阵、性能及源码哈希。
每个策略目录含 `model.json`、`inference.json`、`errors.json`、`report.json`。
[提交准备](docs/SUBMISSION.md) 提供官方要求与私密邮件模板。此仓库不自动发送邮件、创建 PR 或提交认证。
官方 S 档最终认定由主办方完成，本地测试不能代替认证。
