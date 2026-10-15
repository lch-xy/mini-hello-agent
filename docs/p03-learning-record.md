# P03 实现记录：共享 Runtime 与原生工具循环

> P19 路径归并：阶段提取已并入完整原测试，复盘命令已更新；历史结果仍表示当时阶段范围，当前重跑数量可能不同。完整对应见[测试归并表](p19-test-merge-map.md)。

日期：2026-10-15。目标快照：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

P03 实现及阶段检查完成；架构理解仍由你参与验收。阅读顺序：[架构递进](p03-progressive-guide.md) → [设计详解](p03-progressive-explanations.md) → 离线例子与本记录。

## 需求与架构选择

采用共享异步内部事件源。SimpleAgent 管输入、历史提交与运行生命周期，Runtime 管模型/工具顺序和停止条件，P01 管模型协议，P02 管实际工具执行。工作消息、长期历史和 RunResult 分开保存，原生工具请求与回执使用 ID 配对并跨轮重放。

先完成一次真实函数执行与两次脚本模型请求，再扩展到坏参数、重复 ID、空响应、截断、上限和错误传播。四入口共用的基础结构在此落地，真实 SDK 流式与取消矩阵在 P04 验收。

## 实现范围与来源

以下 19 个模块与固定参考快照逐文件核对，源码字节一致：

- core：agent、runtime、lifecycle、streaming、components、session_store。
- agents：simple_agent。
- context：包入口、builder、history、truncator、token_counter、providers、types、text。
- observability：包入口、trace_logger。
- skills：包入口、loader。

Agent 的真实构造路径依赖历史、计数、截断与装配；组件模块直接引用 SessionStore 和 SkillLoader，Agent 构造导入 TraceLogger。为保持目标结构，这些依赖先落地；P06/P07/P09/P10 的专项行为尚未验收，模块存在不等于对应阶段完成。

`core/budget.py` 当前仅保留目标的 BudgetExceeded 异常类型；共享预算准入、计量与装饰器在 P18 接通。包导出当前增加 SimpleAgent、AgentComponents、DEFAULT_COMPONENT，不提前导入未来模块。P01 模型执行代码保持原阶段实现。

## 验证证据

- 原始 `test_tool_policy.py` 完整恢复，包含 Agent 默认最小行为。
- runtime_contract、recovery_edges、input_errors 三份阶段测试从目标综合测试提取当前适用函数和辅助定义；逐定义 AST 比较确认装饰器及函数/类实现未改动。
- 新增纵向集成测试：同步/异步各验证真实函数副作用、两次模型请求、usage 累加、消息配对、历史与 RunResult；坏 JSON/非对象参数变成可反馈观察。
- P03 阶段：**23 passed**。
- P01–P03 联合：**142 passed，1 skipped，14 deselected**，输出见[p03-test-results.log](p03-test-results.log)。skipped 为真实模型服务；14 项是尚待 P04 实现的原生流式/think 路径。
- [离线例子](../examples/p03_tool_loop.py)运行成功：结果为 5；模型调用 2 次、工具调用 1 次；历史为 user→assistant→tool→assistant。例子显式注入字符计数器，无编码资源下载或模型网络请求。

阶段命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_recovery_edges.py tests/test_runtime_contract.py tests/test_recovery_edges.py tests/test_runtime_contract.py \
  tests/test_all_agents.py tests/test_p03_vertical_slice.py tests/test_tool_policy.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m examples.p03_tool_loop
```

联合检查采用精确 node ID 排除，不再用 `-k 'not stream'`，因为它会误排 P03 的 nonstream 覆写测试和脚本流错误传播测试。当前排除项如下；P04 应全部接回：

```text
tests/test_llm_message_contract.py::test_foreign_message_objects_fail_before_adapter[think]
tests/test_llm_message_contract.py::test_foreign_message_objects_fail_before_adapter[stream_invoke]
tests/test_llm_message_contract.py::test_foreign_message_objects_fail_before_adapter[astream_invoke]
tests/test_llm_message_contract.py::test_provider_fields_pass_through_without_mutation[think]
tests/test_llm_message_contract.py::test_provider_fields_pass_through_without_mutation[stream_invoke]
tests/test_llm_message_contract.py::test_provider_fields_pass_through_without_mutation[astream_invoke]
tests/test_llm_message_contract.py::test_public_stream_keeps_usage_with_empty_choices_footer[think]
tests/test_llm_message_contract.py::test_public_stream_keeps_usage_with_empty_choices_footer[stream_invoke]
tests/test_llm_message_contract.py::test_public_stream_keeps_usage_with_empty_choices_footer[astream_invoke]
tests/test_p01_public_hardening.py::test_instance_options_are_overridden_at_every_entry[stream_invoke]
tests/test_p01_public_hardening.py::test_instance_options_are_overridden_at_every_entry[astream_invoke]
tests/test_p01_public_hardening.py::test_instance_options_are_overridden_at_every_entry[astream_invoke_with_tools]
tests/test_usage_details.py::test_empty_footer_keeps_usage_details
tests/test_usage_details.py::test_async_empty_footer_keeps_usage_details
```

## 已确认的边界

重复/空工具 ID 在执行前失败；坏参数可交回模型；截断不执行未完成工具意图；空响应抛错；工具迭代上限撤下工具作最后总结但状态仍为 max_iterations。返回文本不自动等于任务完成。

工具已经开始执行却未确认结果时，只能记录结果未知，不能声称副作用已撤销。资源所有权仍属于宿主；一个 Agent 的可变历史不能由并发运行共同推进。完整取消、超时和流关闭在 P04 单独检查。

代码提交后按你的要求推送到已配置的 `origin/main`。下一阶段是 P04，不把本阶段的绿色结果作为整个框架完成证据。
