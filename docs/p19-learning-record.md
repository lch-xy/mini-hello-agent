# P19 最终记录：完整复刻、离线验收与差异

日期：2026-10-15。固定参考：HelloAgents `V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

**P01～P19 开发与本地验收完成，按阶段提交并推送至个人 origin/main。P00 实验按你的要求免验；个人理解仍需你讲解验收，不因测试通过自动完成。**

开发仓库：`mini-hello-agent`，远程 [lch-xy/mini-hello-agent](https://github.com/lch-xy/mini-hello-agent)。导航：[总方案](replication-study-plan.md)、[进度](development-progress.md)、[P19 架构递进](p19-progressive-guide.md)、[设计详解](p19-progressive-explanations.md)。

## 范围与一致性

| 范围 | 最终结果与证据 |
| --- | --- |
| 框架 | 81 个 Python 文件＋py.typed 齐全；76 文件逐字节相同，6 个 P01 注释模块可执行 AST 对齐，见[逐文件表](p19-framework-audit.json) |
| 公开导出 | 目标全部包初始化、版本、Python 提示与公开接口已恢复 |
| 原测试 | 45 个模块＋conftest 齐全；除 context_engineering 增加临时目录 fixture 外逐字节一致，全部原定义 AST 保留 |
| 阶段测试 | 删除 34 个已由完整原文件覆盖的精确提取，保留 18 个独立变化实验，见[归并表](p19-test-merge-map.md) |
| 示例 | 42 个 Python 文件＋README/HTML，共 44 文件，逐字节相同 |
| 原指南 | 32 篇逐字节相同；另保留 P00～P19 学习资料、阶段日志和总方案 |
| 技能资源 | 17 目录、131 个目标文件逐字节相同；未执行其中外部脚本、未将它们装入核心依赖 |
| 安装/发行 | 原 pyproject/setup/MANIFEST/uv.lock/导出/脚本/CI 保留；pip 全功能安装和源码外 wheel 安装通过 |

P01 保留阅读注释的模块为 config、exceptions、llm、llm_adapters、llm_response、message；去除说明字符串、忽略模块导入组织后，其可执行结构与目标一致。没有用省略实现或 stub 替代目标模块。

README 只增加复刻来源与学习导航；.gitignore 补学习日志例外与本地状态忽略；学习文档修复阶段测试归并后的路径和本机源码链接。P00 的本机历史产物只保留路径说明，未额外上传 reference workspace。参考仓库源码与其总方案未修改。

## 实测质量结果

| 检查 | 结果 |
| --- | --- |
| 完整原测试＋独立实验 | **700 passed、12 skipped、无 deselected，进程退出 0**，见[p19-test-results.log](p19-test-results.log) |
| 分支覆盖率 | **85.16%**，目标门槛 82%；6330 statements / 1974 branches，见[完整报告](p19-coverage-report.txt) |
| Ruff | 目标 E9/F63/F7/F82，hello_agents/tests/examples/scripts 全部通过 |
| 多进程原脚本 | 200 jobs / 4 workers，200 唯一完成，真实退出重领 1 次，其他 namespace 未变化，见[报告](p19-concurrency-report.json) |
| 固定公开依赖扫描 | 原 check_security，82 项、0 已知漏洞、无跳过、退出 0，见[报告](p19-security-report.json) |
| pip check | 全功能环境无破损依赖 |
| 锁/导出 | 临时 uv 0.12.23 离线 lock --check 成功，原 sync_requirements.py --check 成功；环境安装仍走 pip |
| 发行 | 实际 sdist→解包重建 wheel→新环境最小安装，-I 在源码外导入并执行真实 RAG/Memory/JobQueue 成功；详见[报告](p19-distribution-report.json) |

最终全部原测试已运行，12 skips 为 9 个需显式启用的真实服务用例与 3 个原文件中的无条件真实配置跳过；没有因 SDK 缺失跳过 MCP、Qdrant、Leiden、Anthropic 或 Gemini。

原 current_examples 在独立目录运行全部可离线公开案例，包含自定义工具 TTL、异步重试/取消、格式化语义与持久化；原 runtime_features_example 执行七篇指南的 Python 片段。实际 SSE/HTTP 断连、MCP stdio/HTTP、Qdrant 本地、Leiden 与 SQLite 流程也在完整测试中运行。

本机 ONNX Runtime 遥测退出问题见 P15。最终覆盖率启动脚本先关闭遥测，显式加入仓库根目录供 examples 使用，并断言框架导入实际来自复刻仓库；启动路径错误已经修正，最终退出码为 0。独立 wheel 检查另用 -I 和新 venv，排除了源码路径或 editable 安装的帮助。当前临时发行审计使用 setuptools 84.0.0/wheel 0.48.0，符合目标构建约束；quality 为 Ruff 0.16.10、coverage 7.16.2、pip-audit 2.10.1。

## 保留的目标边界

- Web 包装器在 Runtime ERROR 后追加通用错误，原 ERROR 的异常详情仍可能先发出；P09 记录了实际结果，未擅自修复目标协议。
- Registry.fork 共享 Tool 实例，ReadTool 自身的 registry 回指可写入父读取缓存；子 Agent 构造也可能按继承开关重新装配部分可选工具，见 P11。
- 会话证据与队列租约不提供外部副作用恰好一次；同步线程取消后仍可能继续；checkpoint 不提供跨系统事务。
- 撤回/forget 不清除已发历史；purge/backup/图资产维护范围各自有限；migrate_database 需调用者先显式备份。
- RunBudget 是逻辑准入与已报告用量限制，SDK 内部重试和已准入调用可能使远端费用超预期；直接外部客户端不自动受控。
- 根 skills 不在目标 wheel 中；核心安装的可选 SDK 缺失提示与包导入边界已验证。学习证据日志/JSON 以 Git 仓库为准，原 MANIFEST 不把全部这些证据打入 sdist。

## 明确未验证的部分

未调用真实对话/嵌入服务、未下载 FastEmbed 模型权重、未验证真实 Qdrant 云账号与语义质量。实际模型用例仍须显式配置并允许调用后运行。未将单机 macOS/Python 3.13.5 结果当作 Windows/Linux/Python 3.12 的全矩阵结果；远程 CI 配置已保存，远程执行状态不在本地通过结论中。

未发布到 PyPI 或部署服务；已授权交付是代码、学习资料、本地发行验证、Git 提交与推送。依赖扫描只代表本次固定公开运行依赖查询结果，不保证未来无漏洞或覆盖所有外部工具。

## 继续学习的入口

每个 P01～P19 都有 A1～A8 主文、设计详解与记录；总方案保留历史与完整文件索引。下一次可用“按 Pxx 的 A1 开始，先让我给出方案与取舍，再对照实现”进行讲解验收。你能看懂源码只是第一层；能为需求变化划分状态、边界、代价与验证方式，才是本方案要求的架构理解。
