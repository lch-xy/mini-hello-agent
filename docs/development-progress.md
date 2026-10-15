# 连续复刻开发进度

目标：完整复刻固定 `V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`，按 P01 的架构决策方式逐阶段说明并实现，不将部分兼容实现当作全项目完成。

开发仓库：`mini-hello-agent`。参考仓库：同级 `HelloAgents`。总范围以参考仓库 `docs/replication-study-plan.md` 为准。

每阶段保持：需求边界→方案选择→数据契约→职责/模式→纵向实现→扩展→装配/生命周期→变化验证。阶段代码和文档验收后提交到 Git，并推送到已配置的 origin/main；理解验收由你参与，不因自动化检查而自动通过。

| 阶段 | 开发范围 | 当前实现证据 |
| --- | --- | --- |
| P00 | 可重复基线 | 环境与版本已核查；实验按你的要求免验，见阶段记录 |
| P01 | 消息、响应、三协议模型层 | `96295a9`；48 项阶段检查通过；架构学习资料已保存 |
| P02 | 工具、Schema、注册、策略与熔断 | `a87c7e9`；71 项阶段检查；联合回归 119 passed；见[P02 记录](p02-learning-record.md) |
| P03 | Function Calling 最小 Runtime | `02f3cc1`；23 项阶段检查；联合 142 passed；见[P03 记录](p03-learning-record.md) |
| P04 | 四入口、生命周期、流式与取消 | `8233346`；全部当前测试 186 passed / 4 skipped；无阶段排除；见[P04 记录](p04-learning-record.md) |
| P05 | ReAct、Reflection、PlanSolve 与工厂 | `81d613c`；四策略×四入口及专属边界；联合 233 passed / 10 skipped；见[P05 记录](p05-learning-record.md) |
| P06 | 历史、计数、摘要、截断与上下文 | `38d616c`；核心专项及联合 272 passed / 12 skipped；真实 RAG/Memory 组合待 P13/P15；见[P06 记录](p06-learning-record.md) |
| P07 | 显式组件装配与最小默认行为 | `5d4d601`；核心协议/三态已验，完整原组件组合已于 P11 补齐；见[P07 记录](p07-learning-record.md)、[P11 记录](p11-learning-record.md) |
| P08 | 文件工具与精确副作用 | `235beee`；四工具及真实 Runtime 集成；联合 332 passed / 12 skipped；见[P08 记录](p08-learning-record.md) |
| P09 | 会话、追踪与 Web 事件 | `6585d70`；完整恢复边界与真实 HTTP 断连；联合 397 passed / 12 skipped；保留目标错误透传问题，见[P09 记录](p09-learning-record.md) |
| P10 | TodoWrite、DevLog、Skills | `28063c1`；三工具、17 目录/131 资源文件；联合 463 passed / 12 skipped；见[P10 记录](p10-learning-record.md) |
| P11 | 子代理、过滤与父状态恢复 | 完整原组件/Runtime 测试、真实嵌套 Task；联合 563 passed / 12 skipped；见[P11 记录](p11-learning-record.md) |
| P12 | MCP 传输桥接 | 待开发 |
| P13 | 版本化资料库与 BM25 RAG | 待开发 |
| P14 | Embedding、Qdrant、混合召回与评测 | 待开发 |
| P15 | 记忆、语义召回与画像 | 待开发 |
| P16 | 原文证据关系遍历 | 待开发 |
| P17 | GraphRAG | 待开发 |
| P18 | 后台任务、模型预算、数据维护 | 待开发 |
| P19 | 全仓、组合案例、独立发行 | 待开发 |

后续阶段恢复综合测试原文件，包含当前按阶段提取的原始断言。最终必须检查完整公开接口、资源、依赖/发行、组合行为和阶段范围，不能仅用当前小范围绿色结果宣称完成。

当前下一步：P12。P03 因构造依赖先提供部分上下文、会话、追踪和技能模块，不代表后续专项阶段已验收。真实模型尚未调用；已验证范围以各阶段记录和日志为准。
