# P08 实现记录：文件工具与精确副作用

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现与阶段验证完成，个人理解待讲解验收。阅读：[架构递进](p08-progressive-guide.md)、[详解](p08-progressive-explanations.md)。

## 实现与选择

新增 `tools/builtin/file_tools.py`，与固定目标逐字节一致；在 tools 和 builtin 两处入口增加目标已有的 ReadTool、WriteTool、EditTool、MultiEditTool 导出。没有改写 Runtime/Registry 或增加新依赖。

按四种修改意图划分工具，复用原子写/独占备份助手。写入前可选 mtime 比较；批改在顺序更新的内存副本中全部验证，再备份和原子替换。权限、路径行为和并发协调保持目标原边界。

## 验证证据

- 原 `test_file_tools.py` 完整恢复，与固定目标逐字节一致。
- 从 tool_contract_fixes 提取外部路径、固定临时文件保护/快速备份、replace 失败、并发临时文件、顺序批改 5 个原测试定义；逐定义 AST 一致，含参数化共 9 场景。
- 新增 Runtime 的 Read→携 mtime Edit→答案纵向场景，验证真实中文文件、备份、字节回执和工具 ID。
- 新增读取缓存不自动强制写锁、Policy 在任何文件副作用前拒绝、原子替换保留模式位验证。所有真实写入仅在测试临时目录。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**332 passed，12 skipped，无 deselected**；输出见[p08-test-results.log](p08-test-results.log)。跳过范围与 P07 相同，没有真实模型请求。

## 能力边界

file_mtime_ms 只有显式传入才比较；读取缓存不自动加锁。毫秒 mtime 不是哈希，检查与替换之间仍存在竞争窗口；独占临时文件和原子替换不保证写者串行化。

MultiEdit 保证单文件批次先准备再提交，不是多文件事务。replace 失败时原文保留，备份可能已创建；finally 只清理本操作 mkstemp 名称。

路径允许外部绝对路径，working_dir 是相对基准而非沙箱。Registry Policy 与操作系统权限独立；直接 run 不经过 Registry Policy。当前测试证明允许范围内的操作和正常回执，不声称跨宿主权限策略一致。

Read 的 offset/limit 只限制返回行范围，当前仍先读取整个文件；mtime/大小针对完整文件。UTF-8 字节统计与字符数分开。

## 历史与后续

已查看本地 `git diff 9ff239a 1650f26 -- hello_agents/tools/builtin/file_tools.py`：目标由固定 .tmp/直接编辑落盘改为共用独占原子写，由时间戳单独命名备份改为独占名称，MultiEdit 改为顺序内存验证，并加入外部备份路径展示。复刻保持最终行为，不推测作者动机。

P07 `5d4d601` 已推送；P08 同样提交并推送 origin/main。下一阶段 P09 保存/恢复完整工具历史与读取缓存，并接入真实追踪和 Web 事件示例。
