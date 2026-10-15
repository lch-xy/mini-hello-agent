# P07 实现记录：显式装配与默认最小行为

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

核心协议专项完成，真实可选工具/子任务组合待后续。阅读：[架构递进](p07-progressive-guide.md)、[详解](p07-progressive-explanations.md)。个人理解仍待讲解验收。

## 实现现状与选择

components、Agent 基类与四种 Agent 六个源码文件再次与固定目标比较，字节一致。组件装配于 P03、策略透传于 P05 落地，本阶段不重写或重构；新增交付为装配的架构说明和行为证据。

采用最小 Protocol、构造注入、哨兵三态与集中默认装配。组件校验在默认目录创建前，实例状态和生命周期属于宿主；没有全局服务定位器或依赖注入容器。

## 验证证据

从目标 test_agent_components 提取 11 个原定义（辅助对象与 6 个测试定义），AST 对照一致；从 public_hardening 提取主/注入模型选择原测试，AST 一致。

新增 test_p07_assembly_boundaries：两种可选组件×两种开关×三态共 12 场景，使用真实 SessionStore/SkillLoader 的假值子类验证身份；默认组件独立、运行不关闭借用模型且不创建隐式目录；明确功能开关不覆盖同名已有函数；仅改独立模型名称在构造时拒绝。

最后一个新用例最初预期取模型时拒绝，实际目标在构造阶段就抛错；修正测试期待位置与正文说明后通过，未修改生产行为。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**302 passed，12 skipped，无 deselected**；输出见[p07-test-results.log](p07-test-results.log)。跳过范围与 P06 相同，没有真实模型请求。

## 尚待恢复的完整组合

目标原 test_agent_components 的以下 3 项测试需真实可选功能或子代理作用域，暂不提取；在 P09/P10/P11 对应实现后恢复完整文件：

- default_assembly_matches_existing_config_and_tools：真实 Skill/Task/TodoWrite/DevLog 默认装配。
- optional_instances_override_disabled_config_and_really_work：注入 Skill/Session 的实际工具读取、保存/恢复。
- history_replace_and_subtask_restore_keep_counter_consistent：子任务运行与父历史/计数恢复。

当前三态验证覆盖真实组件构造与禁用协议，不声称已覆盖上述完整执行集成。检查方法可调用也不等于完整行为符合协议；显式共享宿主状态仍需宿主协调。

P06 `38d616c` 已推送，本阶段同样提交并推送到 origin/main。下一阶段 P08 接入四个文件工具及精确副作用验证。
