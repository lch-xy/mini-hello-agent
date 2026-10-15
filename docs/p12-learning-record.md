# P12 实现记录：真实 MCP 传输与工具桥接

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现与阶段验证完成，个人理解待讲解验收。阅读：[架构递进](p12-progressive-guide.md)、[设计详解](p12-progressive-explanations.md)。

## 实现与依赖

新增 tools/mcp.py、examples/tools/mcp_server.py、mcp_agent_demo.py 与 test_mcp_integration.py，四文件逐字节与固定目标一致。tools 包导出目标 MCPConnection，没有改动 Runtime 或重新实现 MCP 传输。

requirements/p12.txt 引用 P09 清单，按 uv.lock 递归核对当前平台依赖闭包，新增 mcp 2.3.0、mcp-types 2.3.0、pyjwt 2.15.1、python-multipart 0.0.32、sse-starlette 3.5.0，并保留目标 pywin32 的 Windows 条件固定项。当前安装版本逐项核对，pip check 无破损依赖。未声称验证其他操作系统。

连接由宿主 async with 管理，MCPTool 保留原始 inputSchema 与 data.mcp，Registry 仍执行 Policy 和统一工具协议，Runtime 继续控制模型轮次。

## 验证证据

原 4 个 MCP 集成场景完整运行：真实 stdio 的 Schema/成功/错误/Policy/关闭，撞名与宿主替换保留，重连旧 fork 失效，以及真实回环 Streamable HTTP。没有因缺 SDK 而跳过。

新增目录协议替身验证重复 cursor/名称、允许列表缺项、导出名称、外部引用失败后关闭；本地 $defs/$ref 和多模态/structuredContent 完整保留；参数错误不触发远端；跨循环拒绝；超时不自动重试。

新增真实临时 stdio 服务器与 Agent 往返，传输 dict[str, list[int]] 嵌套参数，取得 structuredContent.total=6；错误嵌套参数被本地拦截，远端调用文件计数仍为 1。

最终命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**576 passed，12 skipped，无 deselected**；输出见[p12-test-results.log](p12-test-results.log)。跳过仍是此前 9 个 live 与 3 个原无条件 skip，无 MCP 跳过。

另运行目标 `python -m examples.tools.mcp_agent_demo` 成功：脚本模型通过真实 stdio 工具取得 fixture 回执，连接退出后 Registry 为空。所有服务器为本机子进程/随机回环 HTTP，正常与失败路径自动清理；没有真实模型或外部服务调用。

## 实际边界

连接绑定原事件循环；同步 run 拒绝，Task 子线程的新循环也不能借用当前连接。fork 共享工具不改变连接所有权。

关闭后直接注册项注销，宿主后来替换同名工具保留；其他 fork 可保留旧名字，但开放/循环/generation 检查阻止调用。重连新代次不会让旧能力复活。

桥接只自动导出工具，resources/prompts 仍由 client 访问；text 不代表全部结果，多模态和结构化字段保留在 data.mcp。Schema 合法不证明远端行为无副作用，超时不能推断写入未提交。

本地 git 显示该桥接由 `1650f26` 引入；本阶段以实际源码与原始传输测试为依据，不推测提交动机。

P11 `91458b5` 已推送，本阶段同样提交并推送 origin/main。下一阶段 P13 建立版本化文档、原文回读和 BM25 检索。
