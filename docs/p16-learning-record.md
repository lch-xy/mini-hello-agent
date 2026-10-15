# P16 实现记录：带原文证据的关系遍历

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现与阶段契约验证完成，个人理解待讲解验收。配套：[架构递进](p16-progressive-guide.md)、[设计详解](p16-progressive-explanations.md)。

新增 relations.py、relation_tool.py，恢复完整 test_retrieval_composition.py，三个文件逐字节匹配固定目标；包导出增加 RelationStore/RelationResult/RelationTool。当前这些模块由 git 1650f26 引入。

完整回归 **693 passed，12 skipped，无 deselected，退出 0**，见[p16-test-results.log](p16-test-results.log)。沿用 P15 的测试启动关闭 ONNX 遥测方式：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import onnxruntime; onnxruntime.disable_telemetry_events(); import pytest; raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests']))"
```

原测试实际执行 SQLite 的有向环三跳、边去重和深度、入向与双向查询、limit、关系重开、quote 拒绝、资料换版后旧边退出、精确实体与参数范围，以及 Registry 标准回执。P14 管线完整组合断言同时恢复。

当前边由宿主导入；quote 子串检查只证明来源位置。RelationStore 查询 JOIN 当前 active 版本，历史原文仍能回读；不自动重抽取。BFS 结果是发现的边，不能视为已证实的因果链、相关性排名或全局 GraphRAG。无新增模型调用、依赖或独立图服务。

高内聚体现在关系验证/遍历集中于 Store；低耦合体现在工具只适配协议，Runtime 无需修改。没有额外 Repository、图数据库或自动 Provider。本阶段提交并推送 origin/main，下一阶段 P17。
