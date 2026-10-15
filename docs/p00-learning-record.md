# P00 学习记录：固定基线与独立环境

日期：2026-10-14。学习方案：[replication-study-plan.md](/Users/lincunhao/Documents/github/HelloAgents/docs/replication-study-plan.md:395)。

状态：P00 已按你的要求验收通过。技术基线有实测证据；理解练习按你在 2026-10-14 的明确要求跳过，直接进入 P01。

## 1. 固定复刻终点

- 参考仓库：`/Users/lincunhao/Documents/github/HelloAgents`。
- 复刻仓库：`/Users/lincunhao/Documents/github/mini-hello-agent`；该仓库原有 README、Git main 分支和 origin 远程保持原状。
- 目标分支：main，标签 V1.0.1。
- 完整参考提交：`c597a7ca46ea9969e5b21d9e130549ab69626ff9`。
- 仓库不是浅克隆；当前 HEAD 的祖先含 75 个提交，所有本地引用可达 85 个提交。
- 13 个标签：`V0.1.1`, `V0.2.0`, `V0.2.1`, `V0.2.2`, `V0.2.3`, `V0.2.4`, `V0.2.5`, `V0.2.6`, `V0.2.7`, `V0.2.8`, `V0.2.9`, `V1.0.0`, `V1.0.1`。
- origin/main 指向目标提交；origin/feature/retrieval-memory-context 的 1650f26 已进入目标历史。
- origin/learn_version 的 3927c6d 和 origin/feature-branch-1 的 3f6c608 不属于当前 HEAD 的祖先。它们用于对照，不能把各分支日志行当作同一连续版本。
- 本阶段未 fetch、更换分支、提交或推送。

目的：以后所有源码/行为对照都能回答“对照的是哪个固定版本”，避免学习途中目标发生变化。

## 2. 环境与依赖

| 项目 | 参考环境 | 复刻环境 |
| --- | --- | --- |
| Python | 3.13.5 / arm64 | 3.13.5 / arm64 |
| 环境路径 | `/Users/lincunhao/Documents/github/HelloAgents/.venv` | `/Users/lincunhao/Documents/github/mini-hello-agent/.venv` |
| 依赖方式 | pip + 原仓库 requirements/test.txt | pip + requirements/p00.txt |
| 固定第三方依赖 | 当前平台 34 条全部匹配 | 当前平台 34 条全部匹配 |
| pip check | 通过 | 通过 |
| hello_agents 导入 | `/Users/lincunhao/Documents/github/HelloAgents/hello_agents/__init__.py` | `/Users/lincunhao/Documents/github/mini-hello-agent/hello_agents/__init__.py` |
| 原框架 distribution | editable 安装 hello-agents 1.0.1 | 未安装 |
| 复刻实现 | 原版源码 | 0 字节的空 __init__.py |

采用你选择的 pip 路线，没有安装 uv。全局 Conda 环境原有的 OpenAI/Pydantic 等版本不符合基线，两个新环境各自隔离。

复刻 `requirements/p00.txt` 保留原仓库 test 导出的全部固定第三方版本和平台条件，排除了 `-e .`，因此不会把原框架安装进复刻环境。将来建立自己的项目配置后，再安装自己的包。Windows 条件的 colorama 在当前 macOS 不安装，所以清单含 35 条声明，当前实际校验 34 条。

三类依赖：核心用于基本运行；extras 按功能增加 SDK/服务；quality 用于发行检查。本阶段只准备核心和测试，不提前安装 Qdrant、GraphRAG、MCP 或模型服务 SDK。

三个文件角色：pyproject.toml 声明允许范围；uv.lock 固定解析选择；requirements 是锁文件的 pip 导出。已有清单本次没有修改。

## 3. 参考案例：观察一次工具往返

案例源码：[runtime_features.py](/Users/lincunhao/Documents/github/HelloAgents/examples/agents/runtime_features.py:208)。

实际主要路径：

```text
用户输入
  → SimpleAgent.run
  → _events：创建运行状态、工作消息
  → _run_loop / runtime.turn_events：控制模型与工具循环
  → ScriptedLLM 第一次响应：提出 route_distance 请求
  → ToolRegistry → RouteTool：真实执行并产生 ToolResponse
  → runtime 把 tool 回执加入模型消息
  → ScriptedLLM 第二次响应：返回公开答案
  → _events 提交历史；宿主显式保存会话
```

ScriptedLLM 按预设顺序返回响应，并捕获实际模型输入。SQLite、工具、上下文、流事件和会话恢复都是真实执行。这个实验验证运行协议，尚不验证真实模型的规划或回答质量。

已保存会话中的四条原生消息：

1. user：比较两条线路的步行距离。
2. assistant：请求 route_distance，ID 为 route-1，legs 为 museum 和 lake。
3. tool：回执 ID 为 route-1，museum=3 km，lake=7 km，来源 fixture://routes/v1。
4. assistant：教学线路中，博物馆线为三公里。

第一次模型响应提供要执行的操作；工具回执是模型后续需要消费的新输入；第二次模型响应才得到最终文本。因此实际 model_calls=2，tool_calls=1，status=completed。

## 4. 观察完成与失败边界

| 项目 | 实测结果 | 含义 |
| --- | --- | --- |
| 正常循环 | completed，模型 2 次，工具 1 次 | 工具请求/回执/回答闭环完成 |
| 工具迭代上限 | max_iterations | 最后总结不代表任务已验证完成 |
| 模型输出截断 | output_limit | 与正常完成不同 |
| 模拟服务异常 | failed | 异常不能伪装空文本成功 |
| 取消后的运行 | cancelled | 运行提前关闭 |
| 保存后续跑 | completed | 已确认回执被重放 |
| 本次写入 | 1 次；恢复工具调用 0 次 | 恢复未重新执行已确认写入 |
| 旧记忆 | superseded | 修订产生替代记录 |
| 当前记忆 | retracted | 撤回后退出有效召回 |
| 其他用户记录 | 0 | Store 的用户范围过滤生效 |

这些细节将在后续阶段实现。P00 先掌握：文本回答、运行状态、工具回执是不同证据。

## 5. 测试环境与历史控制权

[tests/conftest.py](/Users/lincunhao/Documents/github/HelloAgents/tests/conftest.py:1) 默认跳过 live 测试；普通测试阻止外部网络，loopback 允许本地协议实验。真实模型调用需要显式 --run-live 和服务配置。

当前已运行：

```bash
.venv/bin/python -m pytest -q tests/test_tool_response_protocol.py tests/test_runtime_features_example.py::test_runtime_features_persists_pairs_and_exposes_outcomes
```

结果：21 passed。只验证本阶段这组参考测试，不代表全仓测试全部通过，也不代表你的空复刻包已经拥有这些功能。

早期 e86a0dc 的 HelloAgents/core/graph.py 构建并编译 LangGraph 图，Executor 调用 app.invoke/astream_events；真正执行节点循环的是 LangGraph。当前 main 的循环由本地 runtime.turn_events 控制，SDK 只执行模型协议调用。旧版本中已删除的功能用于历史理解，不加入 v1.0.1 的复刻 API。

## 6. 复盘命令

参考环境，从参考仓库执行；不带 --workspace 的案例使用临时目录：

```bash
cd /Users/lincunhao/Documents/github/HelloAgents
source .venv/bin/activate
python -c "import sys, hello_agents; print(sys.executable); print(hello_agents.__file__)"
python -X utf8 -m examples.agents.runtime_features
```

复刻环境，在另一终端执行：

```bash
cd /Users/lincunhao/Documents/github/mini-hello-agent
source .venv/bin/activate
python -c "import sys, hello_agents; print(sys.executable); print(hello_agents.__file__)"
python -m pip check
```

目的：命令在哪个目录执行、使用哪个 Python、实际导入哪个包，都有明确证据。后续测试要确认导入的是复刻路径。

## 7. 实际证据

- [结果 JSON](/Users/lincunhao/Documents/github/HelloAgents/workspace/reference-runtime/result.json)。
- [示例标准输出](/Users/lincunhao/Documents/github/HelloAgents/workspace/reference-runtime/stdout.log)。
- [基线测试输出](/Users/lincunhao/Documents/github/HelloAgents/workspace/reference-runtime/baseline-tests.log)。
- [完整工具往返会话](/Users/lincunhao/Documents/github/HelloAgents/workspace/reference-runtime/sessions/travel-protocol.json)。
- [中断写入会话](/Users/lincunhao/Documents/github/HelloAgents/workspace/reference-runtime/sessions/interrupted-write.json)。
- [已确认的教学写入](/Users/lincunhao/Documents/github/HelloAgents/workspace/reference-runtime/confirmed-note.txt)。

## 8. 理解练习（按你的要求跳过）

1. 用箭头描述这次案例的执行路径，指出模型/工具循环的控制者；解释为什么是两次模型调用、一次工具调用。
2. 解释两个独立环境的作用，以及复刻环境为何排除原框架的 editable 安装；写出检查当前 hello_agents 实际来源的命令。
3. 解释 ScriptedLLM 案例能证明什么、不能证明什么，以及为什么历史 0.2.x 的全部功能不直接并入当前复刻终点。

P00 技术验收通过；你选择跳过理解练习并进入 P01。上述题目保留供以后复盘，不阻止后续阶段。
