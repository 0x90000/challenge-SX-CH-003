# 策略执行安全边界

## 威胁模型

策略源码、声明、模型与行情可能出错或包含恶意内容。宿主 Python、项目运行时、基础镜像、
配置和执行者提供的路径视为可信。目标是阻断策略读写宿主文件、网络、启动进程、反射访问解释器对象，
并对无限循环、超大输出和大内存分配设置上限。不宣称抵抗未知 CPython/OS 漏洞或受控宿主管理员。

## 实施措施

1. 策略只在独立解释器运行，使用同版本基础 Python 的 `-I -S`，空临时工作目录，环境仅保留 Windows 启动必需变量；不继承 API key、代理或 PYTHONPATH。
   直接使用基础解释器可避免 Windows venv 启动器偷偷再创建子进程，且不加载虚拟环境第三方包。
2. 受限 Python：仅函数定义/数学导入可出现在模块顶层；禁 class、with、async/yield、global/nonlocal、私有名字；
   属性访问采用明确白名单。拒绝 open/eval/exec/getattr/globals 等。`math` 是唯一可导入模块。
3. builtins 白名单，没有文件、网络、反射或动态代码入口；可信 `api` 只暴露数学/模型算法，不能获取宿主权限。
4. 工作进程审计钩子拒绝文件打开、socket、exec/spawn、ctypes 动态库及常见文件写操作，作为第二道限制。
5. 每调用默认 5 秒墙钟超时、200 万 trace 步预算；超时杀进程，不重试、不以宿主直接执行代替。
6. Linux/Unix 使用 RLIMIT_AS=256 MiB、RLIMIT_CPU=60 秒（整个进程）、RLIMIT_FSIZE=0；
   Windows 使用 Job Object 限单进程内存 256 MiB、最多 1 个进程、关闭 Job 时杀进程。
   Windows 无法建立 Job Object 时返回 `SANDBOX_UNAVAILABLE`，不降级执行。
7. IPC 只用有界 JSON，不用 pickle。拒绝非有限数、重复 key；请求/响应单条上限 16 MiB，
   单次运行累计推理响应也限 16 MiB，避免诊断/状态输出无限累积；策略源码按 128,000 字节上限读取。
8. 训练和推理使用不同进程，避免训练阶段变量泄漏到回测。策略不直接访问完整评估集。

限制范围有意小于完整 Python：不能直接 import numpy/pandas/torch，也不能加载任意第三方 pickle 权重。
需要大型模型时，按 `EXTENDING.md` 设计独立容器后端及签名数据产物，不能随便扩大本地白名单。

## OS 容器增强

README 提供非 root、禁网络、只读文件系统、无 capabilities、no-new-privileges、CPU/内存/PID 限额的命令。
仅 `/tmp` 和结果卷可写。不要把宿主 home、SSH 目录、Docker socket、云凭据挂载到容器。
Docker 仍共享宿主内核；多租户敌对代码应采用独立 VM / microVM 或专门沙箱，不把本地原型直接开放为服务。

## 已验证与待验证

本机实际验证范围见 `VALIDATION.md`。安全测试通过真实工作进程执行允许数学函数、超时、trace 预算、
大内存、训练/推理异常、进程终止，并对文件访问、网络导入、反射、格式字符串反射与动态执行尝试做阻断验证。
API helper 逻辑在父进程另有数值单元测试。父进程覆盖率不能统计 `-I -S` 子进程内部行数，
其黑盒测试的行为证据须与覆盖率报告一起阅读。

没有第三方渗透审计，未知解释器漏洞、宿主失陷和完整 OS 沙箱强度不在本地测试证明范围内。
