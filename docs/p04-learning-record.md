# P04 实现记录：入口一致性、模型流与取消

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现和阶段验证完成，个人理解待你讲解验收。阅读：[架构递进](p04-progressive-guide.md) → [设计详解](p04-progressive-explanations.md)。

## 选择与实施

沿用 P03 共享异步事件源，不复制四份循环。新增 LLM 门面 4 个目标方法（think、文本同步流、文本异步流、工具异步流），适配器新增 9 个目标方法（Base 回退/能力、OpenAI 原生流、Anthropic/Gemini 文本流）。新增方法逐定义 AST 对照一致，仅门面的预算装饰器留 P18；P01 中文注释和非流式逻辑保留。

P03 已有 SimpleAgent、Runtime、生命周期和 StreamEvent 结构承接四入口、deadline 和 finally，本阶段无需重写。协议碎片归适配器，循环与状态归 Runtime，提交与公开事件归 SimpleAgent，显示/传输由宿主消费。

没有增加依赖；环境仍使用固定 pip 清单。没有调用真实模型。

## 验证与范围

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**186 passed，4 skipped，无 deselected**。结果见[p04-test-results.log](p04-test-results.log)。4 项跳过分别为现有真实服务用例 1 项，以及原异步生命周期测试文件中明确无条件 skip 的真实模型/钩子用例 3 项。本阶段另有离线集成用例实际验证回调，不把这些 skip 视为通过。

- `test_llm_streaming.py` 原文件完整保留。
- `test_async_lifecycle.py` 保留全部定义和断言，仅移除未使用且属于 P05 的 ReActAgent 顶层导入；P05 恢复。
- runtime_edges、recovery_edges、provider_boundaries 三份阶段文件分别提取目标原文件 10、5、3 个定义（含辅助定义），逐定义 AST 对照一致。
- 新增 `test_p04_entry_lifecycle.py`：四入口真实写入恰好一次、最终消息不重复、异步宿主拒绝同步桥接、会话并发保护、钩子独立超时/错误、模型中途取消与复用、同步工具取消后晚到副作用、Unicode SSE 分片。
- P03 暂缓的 14 项原生流/think/usage 尾包断言全部接回，没有继续使用阶段排除。

第一次联合运行时，提取 provider 测试遗漏 Mock/SimpleNamespace 导入，出现测试 NameError；补齐原测试导入后通过，没有改变原断言或生产行为。

## 已验证与未声称的能力

OpenAI 原生工具流拼接 index/name/arguments，并保留空 choices 尾包 usage；其他适配器的工具流是完整响应回退。文本回退线程使用容量 8 的队列；StreamBuffer 是丢旧保新的事件缓存，不提供阻塞式背压。

提前关闭传播到上游异步流并释放活动标记；模型时限不计消费者停顿；钩子失败不覆盖业务结果；私有推理字段进入下一模型请求而不进入公开文本。工具已成功后的取消保留回执，完成事件处关闭保留已提交历史。

同步工具测试明确复现取消 await 后线程才完成写入，历史仍为 unknown。当前不声称取消可回滚外部副作用，也不把本阶段回执验证代替 P09 的存盘/重启组合验证。

SSE 测试用真实 Agent 事件并按 7 字节分片重组；解析器仅在测试中模拟消费者，没有向框架添加新前端。完整 Web 案例在 P09/P19 检查。

## Git 与后续

P01 `96295a9`、P02 `a87c7e9`、P03 `02f3cc1` 已推送至 `origin/main`，并通过远程引用确认 P03 的完整提交号为 `02f3cc1cbd7cafbf4890013c338687c9f0989b1b`。本阶段代码和文档也按授权提交并推送。

P05 将接入三种策略和 Agent 工厂，扩展到四策略×四入口共同契约；不会将当前 SimpleAgent 验证直接算作四策略完成。
