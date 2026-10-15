# P15 实现记录：作用域记忆、语义召回与严格画像

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

阶段实现与离线契约验证完成，个人理解待讲解验收。配套：[架构递进](p15-progressive-guide.md)、[设计详解](p15-progressive-explanations.md)。

新增 memory 四文件、MemoryTool，恢复完整 runtime_features 与画像示例；连同完整 retrieval_memory、context_pipeline、cloud_retrieval、profile_memory 测试，共 12 个完整文件逐字节匹配固定目标。当前语义记忆三项测试和 Runtime 示例测试定义与原文件 AST 一致；后台 LeaseLost 案例留待 P18，原文档片段执行留待 P19。

全套结果：**681 passed，12 skipped，无 deselected，进程退出 0**，见[p15-test-results.log](p15-test-results.log)。P06 的真实 Memory 上下文组合、P14 的完整 cloud_memory 协议均已补齐。实际执行画像离线示例成功，重开为 5 公里、另一用户无画像；pip check 无破损依赖。

本机直接 pytest 的断言同样通过，但退出时 ONNX Runtime 1.30.0 遥测线程触发 C++ recursive_mutex 异常；系统崩溃栈明确落在 onnxruntime_pybind11_state.so 的 Events/HttpResponseDecoder。P15 专项 48 passed 且正常退出。最终全套在测试启动前调用 SDK 的 disable_telemetry_events 后正常完成；未修改框架、依赖版本或掩盖退出码。最终复核命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import onnxruntime; onnxruntime.disable_telemetry_events(); import pytest; raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests']))"
```

验证范围：真实 SQLite 的 user/task 隔离、已知 ID 越范围拒绝、事务修订与幂等撤回；真实本地 Qdrant 的过期拒绝/重开/范围 namespace；严格画像类型和默认值拒绝不落库、并发 CAS 只有一方获胜、date/tuple/enum 严格 JSON patch、字段来源与 forget 审计。

新增边界实验核对空查询读取最新近期记录，非空语义查询仍需显式同步；MemoryTool 映射 CONFLICT；撤回回执仍含内容，读取旧记录需 include_inactive=True。完整 Runtime 示例验证工具配对、每轮记忆刷新、持久化和中断恢复，脚本模型仅验证流程。

source 是来源声明，Schema 有效不等于事实已核实；撤回/forget 不擦除已发历史与审计行。文件已具备导出/purge 接口，但其维护版本屏障和后台租约中断到 P18 组合验收。未调用真实对话/嵌入 API、未下载模型权重，不声称通过语义质量评估。

当前文件由 git 1650f26 引入；历史对照比较旧 MemoryManager 与当前状态所有权，不把旧实现直接套进 main。本阶段提交并推送 origin/main，下一阶段 P16。
