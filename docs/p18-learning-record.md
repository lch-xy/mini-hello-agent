# P18 实现记录：后台恢复、共享预算与数据维护

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

阶段实现与离线契约验证完成，个人理解待讲解验收。配套：[架构递进](p18-progressive-guide.md)、[设计详解](p18-progressive-explanations.md)。

新增完整 background 四文件，补齐 budget 模块；恢复完整 background_jobs、maintenance_budget_evaluation、semantic_memory、graphrag 测试与后台/语义记忆示例，共 11 个完整文件逐字节匹配目标。LLM 最小接入预算参数、校验与装饰器，保留 P01 注释；去除文档字符串后的类可执行 AST 与目标一致，模块层仍保留 P01 的导入排序/去除无用导入差异。

完整回归：**770 passed，12 skipped，无 deselected，退出 0**，见[p18-test-results.log](p18-test-results.log)。当前仍保留早期按阶段提取的原测试作过渡，P19 全仓归并时去掉完整文件已覆盖的重复提取，不能将累计数量等同于独立覆盖。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import onnxruntime; onnxruntime.disable_telemetry_events(); import pytest; raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests']))"
```

实测包含：真实多连接竞争与进程退出后重领、有效租约续期、迟到续租/旧 owner 的进度与结果拒绝、取消、有限退避、worker 并发与优雅停止；实际 JobWorker 图构建失败重试复用一次成功抽取缓存，语义索引发布前 LeaseLost 保留旧 manifest。

模型预算验证线程安全准入、多个实例/工具 to_thread 共享、缺 usage 拒绝后续调用、时间耗尽与摘要不吞 BudgetExceeded。新增同步/异步流完整/中断四案例验证源关闭、已知/未知统计和后续准入，另验证 GraphRAG JSON 格式重试不能绕开 scope 调用限额。

维护验证包含作用域导出/purge、备份后原库继续变更与备份隔离、迁移失败回滚和未来版本拒绝、画像清理后旧 revision 拒绝重建、Qdrant namespace 清理不影响另一存储、Graph prune 保留 active。storage/Memory/Profile/向量/图维护方法从其先行阶段已有目标实现，本阶段恢复组合验证，未额外改变行为。

实际运行后台导入示例，第二次尝试 succeeded，progress imported=3/total=3，documents=3/revision=3。语义记忆示例已恢复完整源文件，但需要真实 FastEmbed 模型/维度，未下载权重。模型预算不是账单硬上限，协作取消不能强停线程，队列租约不提供跨外部系统恰好一次。

迁移调用者须先显式备份，migrate_database 本身不自动创建备份；没有引入 Flyway 或额外任务平台。当前模块由 1650f26 引入。P17 `ddc5821` 已推送，本阶段继续提交推送 origin/main，下一阶段 P19 做完整文件/测试/示例/发行与质量审计。
