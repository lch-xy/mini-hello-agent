# P06 实现记录：历史、预览与单次模型输入

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

核心机制专项检查完成，真实检索/记忆存储组合待对应阶段。阅读：[架构递进](p06-progressive-guide.md)、[设计详解](p06-progressive-explanations.md)。理解仍需你讲解验收。

## 代码现状与职责选择

context 的包入口及 builder、history、truncator、token_counter、providers、types、text，以及 Agent、SimpleAgent 十个文件已于 P03 因真实构造依赖落地。本阶段再次与固定目标逐文件比较，源码字节一致，无需改写已有实现。

P06 的新增交付是架构递进资料和专项契约验收。HistoryManager 管保存/压缩，TokenCounter 估算历史，Truncator 管长工具结果，Builder 管单次输入，Provider 按请求收集资料；保持现有 Protocol 和组合方式，不合并三种限制。

## 验证来源与结果

- `test_smart_summary.py` 原文件完整恢复。
- `test_context_engineering.py` 保留全部原定义/断言，额外增加模块级 autouse fixture，把原测试相对输出目录限制在每个测试的 tmp_path，避免删除或污染仓库同名目录。原 4 个测试类 AST 完全一致。
- 从 `test_context_pipeline.py` 提取 CapturingLLM、config 和当前 4 个测试定义；其中四入口参数化实际执行 4 个路径。6 个定义 AST 一致。
- 从原 runtime_contract 提取 UTF-8 head/tail/head_tail 原测试，定义 AST 一致。
- 新增核心边界：原生工具字段参与计数/缓存、BudgetExceeded 不降级、工具后按请求刷新 Provider、词项/MMR 不采信外部 relevance_score、instructions 仍是 user 参考且不拆配对、最少轮次无法压缩时基础超限明确失败。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**272 passed，12 skipped，无 deselected**；输出见[p06-test-results.log](p06-test-results.log)。新增两项 skip 为智能摘要与压缩真实服务场景，总 skip 包含 9 项 live 和原生命周期 3 项无条件 skip。没有真实模型请求。

## 实际能力与后续组合

本阶段真实函数修改本地 Provider 来源，证明第二次模型请求不沿用旧快照，且参考资料不偷偷写入长期历史。真实 RAGStore、MemoryStore、对应工具及自定义 Assembler/真实检索 Provider 组合在 P13/P15 恢复完整 context_pipeline 原文件；原文件中的工具熔断/异步组合也将在完整回归恢复。

智能摘要普通异常允许回退，BudgetExceeded 继续传播；当前只验证异常边界，P18 才接通共享预算准入和计量。统计摘要不保留全部旧事实，智能摘要同样有损；不能声称无限历史或精确计费。

本地历史显示 `9ff239a`、`1650f26` 等提交演进上下文模块；本阶段以固定目标的实际实现及断言为准，不据提交标题补写语义。P05 `81d613c` 已推送，本阶段也按授权提交并推送。

下一阶段 P07 先验显式组件的基本协议和三态；真实可选功能仍在 P09/P10/P11 补齐组合。
