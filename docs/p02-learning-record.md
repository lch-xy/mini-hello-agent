# P02 实现记录：工具执行边界

日期：2026-10-15。目标：`HelloAgents V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

当前状态：P02 实现与阶段检查完成；你的架构理解尚未独立验收。入口为[架构递进正文](p02-progressive-guide.md)，选择依据与模式解释见[详解](p02-progressive-explanations.md)。

## 需求与选择

P01 给出工具意图，P02 提供名称查找、实际执行和统一结果。选择“注册表＋Tool 对象＋函数/action 适配”，保留三种接入约定；宿主策略与熔断作为执行前的不同规则，业务实现保持独立。

本阶段不实现模型循环、文件副作用、子代理或远端 MCP。它们在后续阶段接入同一工具契约。

## 代码范围与来源

以下七个模块按固定参考快照重建，已逐文件比较，源码字节一致：

- [base.py](../hello_agents/tools/base.py)：Tool、参数、Schema 与 action 适配。
- [response.py](../hello_agents/tools/response.py)：统一回执。
- [errors.py](../hello_agents/tools/errors.py)：错误码。
- [registry.py](../hello_agents/tools/registry.py)：目录、调度、策略与 fork。
- [permissions.py](../hello_agents/tools/permissions.py)：宿主许可决策。
- [circuit_breaker.py](../hello_agents/tools/circuit_breaker.py)：每工具熔断。
- [calculator.py](../hello_agents/tools/builtin/calculator.py)：确定性的纵向业务工具。

阶段性包导出只引用当前已实现模块；根入口新增目标公开的 ToolRegistry、global_registry、CalculatorTool、calculate。已有 P01 执行代码没有改动。

Schema 校验、展开注册原子性、async 调用等沿用目标行为，没有额外重构或引入新依赖。当前 pip 环境已有所需基础库。

## 验证证据

1. 原工具回执、自定义工具、熔断、Pydantic v2 序列化四份测试直接保留。
2. 从 `test_tool_contract_fixes.py` 选取当前阶段 14 个测试函数与 2 个辅助类；从 `test_tool_policy.py` 选取 3 个测试函数。逐定义 AST 对照确认原函数、装饰器和辅助类未改动，仅缩小顶层依赖。
3. P02 阶段执行：**71 passed**。
4. P01＋P02 联合回归：**119 passed，1 skipped，14 deselected**。
5. skipped 为真实服务；deselected 为 P04 的流式/think 相关路径，包括明确指定的两项空尾包 usage 测试。所有原断言保留。

联合回归命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests \
  -k 'not stream and not think' \
  --deselect=tests/test_usage_details.py::test_empty_footer_keeps_usage_details \
  --deselect=tests/test_usage_details.py::test_async_empty_footer_keeps_usage_details
```

首次联合选择误纳入这两项名称未含 stream 的 P04 尾包用例，因流式方法尚未接入而失败；随后按阶段范围明确排除。该调整不表示流式已通过验证。完整输出见[p02-test-results.log](p02-test-results.log)。

## 架构边界与后续

Policy 拒绝不会执行工具，也不累计服务熔断失败；fork 独立目录与缓存，但共享工具对象、Policy 和 breaker。Schema 描述不等于全局严格参数验证；普通函数仍接收单 input；action 使用方法签名。这些是复刻契约，不在本阶段改变。

`52b2597` 对工具序列化有 Pydantic v2 调整；`1650f26` 包含当前注册、Schema、策略等最终语义。按本地 diff 解释变化，不依据提交标题补写事故或作者动机。

下一步 P03 将统一模型请求、工具调度、回执保存与再请求组织成最小 Runtime。P02 作为独立阶段提交，后续开发以该边界为基础。
