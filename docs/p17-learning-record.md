# P17 实现记录：证据图、真实 Leiden 与全局归约

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

阶段实现与离线契约验证完成；个人理解待讲解验收。配套：[架构递进](p17-progressive-guide.md)、[设计详解](p17-progressive-explanations.md)。

新增七个 graph 模块、GraphRAGTool 和 graphrag_demo，九个文件逐字节匹配固定目标。原 test_graphrag 除后台 JobWorker 组合外保留完整定义、参数化和 fixture，AST 匹配；该最后组合测试在 P18 恢复。新增一个真实管线的归约不收敛实验。

requirements/p17.txt 引用 P14，将 igraph==1.0.0、leidenalg==0.12.0、texttable==1.7.0 按目标 uv.lock 固定，安装后 pip check 无破损依赖。Leiden 测试实际调用 find_partition，不以模拟分组替代。

完整回归：**714 passed，12 skipped，无 deselected，退出 0**，见[p17-test-results.log](p17-test-results.log)。使用 P15 已记录的本机 ONNX 遥测关闭启动方式：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import onnxruntime; onnxruntime.disable_telemetry_events(); import pytest; raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests']))"
```

已实际验证真实 SQLite、Leiden 父子层级与 seed、真实本地 Qdrant 语义实体种子、严格抽取/引用校验、同名别名歧义、原文字符区间、重开/缓存/更新失效、竞争发布、取消、坏端点/quote/引用不发布、预算拒绝、无实体材料、空报告与 no_answer、Registry 组合。

新增非收敛实验让 map 返回三个各自可装包的大 claim，reduce 原样返回；两轮共六次 reduce 后抛 GraphBudgetError，不削减输入。索引仍可 inspect。独立离线旅行案例实际构建 8 chunks、9 entities、13 relations、7 communities/reports；重复构建复用 generation，全局 answered 且来源覆盖 complete。案例向量/生成均为明确预设，不宣称是公平检索性能基准。

设计边界：关系保留方向，但社区与局部扩展用无向投影；直接别名种子不受全局 seed_limit 硬截断。CAS 发布只保护图代次竞争，不是跨库事务；缓存合法不代表模型语义正确。coverage.complete 仅表示报告处理与来源引用覆盖。外部模型超时、任务租约和共享预算在 P18 组合；未调用真实模型或下载权重。

当前相关模块由 git 1650f26 引入。P16 `693fd69` 已推送，本阶段继续提交并推送 origin/main，下一阶段 P18。
