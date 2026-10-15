# P09 实现记录：会话、追踪与 Web SSE

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

阶段实现及验证完成，目标现存 Web 错误透传问题保持并记录。阅读：[架构递进](p09-progressive-guide.md)、[详解](p09-progressive-explanations.md)。个人理解待讲解验收。

## 实现现状和新增内容

SessionStore 与 observability 包/TraceLogger 已于 P03 因构造依赖落地，本阶段逐字节核对固定目标一致，未重写。新增完整目标 Web 四文件和 examples/_support.py，逐字节一致。

_support 依赖组合示例的 ScriptedLLM/response，但完整组合示例还引用 P13/P15 的 RAG/Memory。本阶段在目标路径 examples/agents/runtime_features.py 仅提取这两个原定义，AST 完全一致，作为离线协议共享地基；后续对应阶段恢复完整组合示例，当前不声称组合示例开发完成。

requirements/p09.txt 引用 p01 清单，新增 Web 依赖全部来自目标 uv.lock：annotated-doc 0.0.5、click 8.5.0、fastapi 0.142.2、opentelemetry-api 1.45.0、starlette 1.7.0、uvicorn 0.54.0。pip 初次解析传递依赖得到 OpenTelemetry 1.45.1，发现后补全固定清单并调整为目标 1.45.0。最终版本逐项核对，pip check 无破损依赖。

## 验证证据

- 完整恢复 session_persistence、observability、trace_integration、recovery_edges 四个原测试文件，逐字节一致。recovery_edges 的全部依赖现已具备，因此以完整原文件替换了尚未提交的 P09 提取文件。
- trace_privacy、trace_statistics、web_example 阶段文件分别提取原综合文件 3、4、1 个定义，AST 完全一致；后者实际验证四策略与连续两次 Web 请求。
- 新增非法 metadata/cache 不替换旧状态、函数 Schema 哈希顺序无关/描述变化、Web 成功和失败后关闭所持模型。
- 新增随机回环 TCP 端口的真实 Uvicorn/HTTPX SSE 断连测试，收到文本片段后关闭连接，Provider 源与请求模型收到关闭，Agent 状态 cancelled，运行标记释放；服务器与连接自动清理。
- 四策略中断保存/恢复原用例实际执行写入，新 Agent 读取完整回执继续，场景中不重复写入；自动保存含当前统计。

最终联合命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**397 passed，12 skipped，无 deselected**；完整输出见[p09-test-results.log](p09-test-results.log)。跳过范围与 P08 相同，没有真实模型或外部服务调用。HTTP 验证仅本机随机回环端口；未将浏览器外观手工检查列为通过。

## 已复现的目标现存问题

以合成 RuntimeError('synthetic-upstream-detail') 请求 Web SSE，观察到两个 ERROR：Agent 原始 data.error 先透传，外层统一 data.message 后发。原始 Web 文件虽捕获异常，不能收回已经发送的原始错误事件；不能宣称所有浏览器错误都经过受控提示。

复刻保持固定目标行为，没有擅自修复原实现。验证成功/失败关闭模型与终态不等于验证所有错误隐私。该问题在架构正文 A7 和详解 A7 说明，后续若独立改进应作为明确偏离目标的任务。

## 边界与后续

会话快照不含 SDK 连接/工具实例，也不是恢复旧协程指令位置；宿主重建依赖，恢复交互、元数据与缓存。确认回执不等于跨运行自动 exactly-once；未知结果仍需外部核实。自动保存失败被捕获，不保证每轮已成功落盘。

HTML raw_response 隐藏不删除 JSONL 已脱敏事件；sanitize 和自定义 sanitizer 按实际开关运行。Logger 是同步文件观察者，I/O 失败可能传播，不是隔离日志服务。

P08 `235beee` 已推送。本阶段继续提交并推送 origin/main；P10 实现 TodoWrite、DevLog 与 SkillTool，P11 再补齐 P07 完整真实工具/子任务装配。完整 RAG/Memory 组合仍待 P13/P15/P19。
