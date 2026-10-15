# P11 实现记录：Task、过滤与父状态恢复

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现与阶段验证完成；个人理解待讲解验收。阅读：[架构递进](p11-progressive-guide.md)、[详解](p11-progressive-explanations.md)。

## 实现与来源

新增 subagent_scope、tool_filter、task_tool 三个模块，与固定目标逐字节一致；已于 P05 落地的 factory 再核对一致。tools/builtin 包入口增加目标 Task 和 Filter 导出，没有新增依赖或重构运行器。

Task 使用已有 Registry 与共享运行机制。默认工厂负责依赖构造，作用域负责指定父状态保存/恢复，过滤器负责名称选择。模型与实际工具实例仍属于宿主资源。

## 验证证据

完整恢复 subagent_mechanism、tool_filter、agent_state_boundaries、agent_components、runtime_contract 五份原始测试，逐字节一致。P07 剩余真实 Task 默认装配及父子状态恢复组合全部接回；P03–P05 的综合 Runtime 断言也已完整恢复。

从 tool_contract_fixes 提取 Task 参数拒绝、证据只读名单、显式过滤保留 3 个原定义，AST 一致。新增真实嵌套 Task 测试：父/子实际 Runtime、实际 Read 文件、主/注入模型开关、宿主模型不自动关闭、父历史只合入 Task 回执；另验默认工厂可按继承 Config 重新装配可选工具。

最终命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**563 passed，12 skipped，无 deselected**；输出见[p11-test-results.log](p11-test-results.log)。跳过范围与 P10 相同；全部嵌套模型使用脚本响应，真实文件只在临时目录，回环 HTTP 一同通过。

## 已验证的隔离边界

- 正常、异常、KeyboardInterrupt、摘要/metadata 后处理失败恢复指定父状态；非法过滤和步数先失败；子会话不覆盖父自动保存。
- fork 复制目录和缓存字典，但共享工具、Policy、breaker；工具自身状态及外部文件不回滚。
- 实际 ReadTool 注入父 Registry 时，子读取仍通过共享实例的反向引用更新父缓存。测试明确观察到子缓存字典身份独立、父缓存却更新；保持目标行为，不声称完整对象图隔离。
- 默认工厂初始排除 Task/Skill/TodoWrite/DevLog，但只关闭递归子代理和自动保存；继承其他开关后，构造可能重新注册可选工具。真实 TodoWrite 例子已验证。
- _STATE 是白名单，不包含任意自定义属性和全部策略局部状态。history 是原 Message 引用列表，metadata 则复制；这不是全对象深拷贝或进程沙箱。
- success 按实际完成状态判断，兼容无 last_run 旧对象；步骤/Token metadata 仍为历史估算。共享模型身份与关闭责任已验，共享 RunBudget 的准入/计量留 P18。

## 历史与后续

已查看本地 factory 的 `9ff239a → 1650f26` diff：Config 复制、初始目录 fork、Simple Registry 传播和步数设置被加入。文档按最终实际构造顺序解释，未从标题推测动机。

P10 `28063c1` 已推送，本阶段继续提交并推送 origin/main。下一阶段 P12 接通 MCP stdio/Streamable HTTP，沿用当前工具与连接所有权规则。
