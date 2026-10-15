# P05 实现记录：四策略共用执行契约

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现和阶段检查完成；个人架构理解待你讲解验收。[正文](p05-progressive-guide.md)按八步展开，[详解](p05-progressive-explanations.md)解释控制与代价。

## 实现与选择

新增 react_agent、reflection_agent、plan_solve_agent、factory 四个模块，恢复目标 agents 包入口。以上五个文件与固定目标逐文件比较，源码字节一致。三种策略借用 P03/P04 生命周期和 turn_events；未复制四份入口或新建插件/策略注册框架。

根包增加三种 Agent 导出；工厂保持在目标的 hello_agents.agents 包，不额外增加根导出。default_subagent_factory 因文件整体落地先存在，子代理专项留 P11。

ReAct 采用内部控制工具与纯业务批次有限并发；Reflection 组织执行、评审和改进；PlanSolve 验证结构化计划后逐步调用共享工具循环。Planner/Executor 负责计划协议和步骤输入，工厂只负责构造选择。

## 验证证据

完整恢复原 `test_all_agents.py` 和 `test_async_lifecycle.py`。阶段文件 shared_contract、recovery_edges、planner_compatibility 分别从目标综合测试提取 10、8、1 个定义，逐定义 AST 对照确认函数/类/装饰器与断言未改动。

新增策略集成用例验证：控制混批串行与 Finish 后不再写入、保留名称、反思中英停止与每任务重置、零次反思、非 completed 停止、改进事件与 usage、坏计划在副作用前失败、步骤上下文和系统角色保留、独立 Executor 借用资源、工厂参数传播。

最终联合命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**233 passed，10 skipped，无 deselected**。完整输出见[p05-test-results.log](p05-test-results.log)。其中四策略×四入口 16 个原始场景实际执行工具并检查回执。10 项跳过为 7 个显式 live 场景及原生命周期文件 3 项无条件 skip；没有调用真实服务或将跳过视为通过。

公开导出对齐目标后再次联合检查通过；没有新增依赖。旧阶段提取文件暂保留学习证据，最终 P19 以完整原始测试和完整范围审计验收，不以重复测试总数证明覆盖。

## 需要理解的边界

Runtime 仍与策略的 _parallel_tools、_builtin_tools、处理钩子协作，不能称为完全零耦合。控制工具不等于普通业务工具；Thought 的简短行动说明不等于服务商私有推理字段。

Reflection Memory 是本任务轨迹，非长期记忆。无需改进采用子串启发式，不保证语义判定。计划已生成不代表执行已完成；调用次数、规划步骤数、工具请求回执数与实际业务副作用次数不同。

并发成功回执即时保存；失败后取消等待任务不保证已经执行的同步线程停止。继承 P04 的 unknown 边界，不自动重试副作用。

## 历史与下一阶段

本地查看 `5a19188` 的实际 diff：旧 ReAct 从 tc.function.name/arguments 改成框架 ToolCall 的 tc.name/arguments。当前策略始终消费统一 DTO。`1650f26` 提供当前共享执行实现；不从提交标题补写作者动机。

P04 `8233346` 已推送；P05 同样按授权提交并推送 origin/main。下一阶段 P06 验证历史、计数、摘要、截断和按次上下文组装；它们因 P03 构造依赖已先提供真实模块，后续将逐项核查而非重复重写。
