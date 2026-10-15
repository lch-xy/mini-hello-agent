# P18 设计详解：恢复执行权、限制调用权、维护数据生命周期

对应[架构递进](p18-progressive-guide.md)。当前实现没有引入外部任务平台或迁移插件，沿用固定目标的 SQLite、普通 handler 和模型门面。

<a id="p18-a1"></a>
## A1：从进程崩溃提出最小持久调度需求

| 方案 | 适用场景 | 当前取舍 |
| --- | --- | --- |
| 内存 asyncio.create_task | 同一事件循环的短工作 | 退出即失去待办状态 |
| SQLite/WAL 队列与租约 | 单机多进程、可恢复且规模有限 | 当前采用，事务表达唯一当前持有者 |
| 外部消息系统与 worker 平台 | 多机调度、吞吐与运维需求 | 本目标没有这项部署需求，不额外引入 |

队列记录执行尝试，SessionStore 记录聊天，GraphStorage 记录合法构建缓存。进程退出后可能已经产生外部副作用但尚未确认队列结果，所以采用至少一次投递；不能把重复领取视为框架能自动消除所有副作用。

<a id="p18-a2"></a>
## A2：把输入快照、操作身份与执行代码分开

Queue 保存 task 名称和规范化 JSON payload/result/progress，检查 UTF-8 字节上限与非有限值；大产物应传路径或标识。handlers 是宿主已注册的函数白名单，claim 只领取该 worker 可处理的 task，不执行载荷中的任意函数。

幂等键绑定 namespace 内 task 与 payload；相同返回原 Job，换 payload 必须拒绝，max_attempts/delay 不用于重新定义同一操作。namespace 是宿主配置范围，不承担登录认证。提交幂等只避免重复生成 Job；context.idempotency_key 可帮助外部 handler 建立业务幂等，但不会自动让数据库外的写入恰好一次。

<a id="p18-a3"></a>
## A3：数据库事务拥有租约裁决权

claim 用 BEGIN IMMEDIATE 串行化选择/更新；queued 到期或 running 租约过期且未耗尽可领，新 token 使旧持有者失效。_owned 同时验证 running、token 与 deadline；续租、结果、失败、进度在写事务内检查并更新。最终尝试过期会转 failed，取消 queued/running 会清 token。

这是当前持有权校验，不是给外部系统的单调 fencing 序号。只在队列数据库内保证旧 owner 不能提交；外部模型、文件、图数据库没有自动加入同一事务。checkpoint 之后仍可能发生取消，不能将“检查了一次”夸大为跨库发布原子性。handler 需在合适边界再次检查，并用幂等或外部协议管理副作用。

<a id="p18-a4"></a>
## A4：用独立续租协程观察所有权，用 slot 限制并发

Worker 同时等待 work 与 renew；续租异常优先处理，失败不会装作成功。普通异常按 attempts 指数退避并封顶 3600 秒，LeaseLost 不再提交失败记录，外部取消让租约自然过期。finally 取消等待任务并收拢协程；同步 handler 的线程可能继续运行。

优雅 stop 让 slot 停止下一次轮询，已经领取的工作正常结束。progress 是持久化提示，重试如何从该位置恢复由 handler 决定；不是框架保存任意 Python 栈。后台示例在每次资料幂等写入后记录 offset，故写入与进度之间中断也可安全重复导入。

<a id="p18-a5"></a>
## A5：调用准入集中在模型门面，避免各策略重复计数

RunBudget 的锁控制调用 reserve 和响应统计；ContextVar.scope 在任务上下文中选预算，优先于模型默认 budget，退出还原之前上下文。asyncio.to_thread 继承当前上下文；原始线程池或新进程不能被一概认为自动继承。

invoke/invoke_with_tools 由 budgeted 装饰，ainvoke 转入已经装饰的同步入口，避免双计数；流使用相应同步/异步装饰器。这样主/摘要/Task/GraphRAG 只要通过通用 LLM 边界即可共享，而无须修改每个策略。自定义模型或直接 Embedding API 不经这里，不能声称全部外部成本自动受控。GraphRAG 修复 JSON 的重试再次走 invoke，预算耗尽直接传播，不降级为另一条不受限请求。

<a id="p18-a6"></a>
## A6：统计保守性来自响应后可见性

max_calls 限制逻辑入口准入，SDK 内部重试仍属于同一次；入口校验/请求失败也可能占用准入并记录未知 usage。max_tokens 比较已报告累计量与 unknown 标志，无法事先准确预留本次或并发请求的远端费用。unknown 会阻止后续受 Token 限制调用，不伪造零成本。

流在实际迭代时准入，正常耗尽后记录最终统计；提前 close/aclose 走 finally，关闭源并记录未知量，即使某些片段已输出。时间从 RunBudget 创建起计，在准入、非流返回和流片段处检查，不能打断卡住的同步 SDK。外部服务 timeout 与任务预算职责不同。新增同步/异步完整与中断四案例真实验证门面装饰链，向量或模型响应依然是协议夹具。

<a id="p18-a7"></a>
## A7：维护保留防止旧写者复活的版本状态

backup_database 以只读源连接执行 SQLite backup，不覆盖已有目标，验证完整性，失败删除不完整目标。migrate_database 要求宿主先显式备份，再在立即事务中逐版本执行受信任 execute/executemany 回调；不允许回调 commit/executescript 破坏原子边界，失败回滚。Schema guard 拒绝不认识的新版本，画像业务 Schema 迁移另由应用设计。

Memory purge 只删当前 user/task 的记录并递增 revision；Profile purge_history 删除内容历史后保留 revision+1 的空 forgotten 标记，拒绝旧 revision 重新写入。Qdrant purge 只删本 namespace 派生向量与清单，SQLite 原文不删；Graph prune 保留 active，按选项清缓存，不递归删外部资产。维护前停止相关写入，各处清理不是分布式事务，也不是磁盘安全擦除。

<a id="p18-a8"></a>
## A8：恢复已有组件的组合，而非另写一套恢复示例系统

完整原后台测试实际使用多连接竞争、真实子进程退出、可控过期时间、续租与迟到拒绝、有限重试、并发上限和停止；完整维护测试检查备份、迁移回滚与版本、范围清理和模型预算。原 GraphRAG JobWorker 案例恢复一条成功抽取缓存后完成真实构建；原语义索引 LeaseLost 案例在发布前中断并保留原 manifest。

[background_jobs_demo.py](../examples/runtime/background_jobs_demo.py) 默认离线，实际导入与保存进度；[semantic_memory_demo.py](../examples/memory/semantic_memory_demo.py) 要显式模型和维度，真实权重实验未在本阶段擅自下载或运行。P19 再统一执行原并发质量脚本和所有发行检查。个人验收要能区分数据库提交保证、协作式取消与外部系统幂等，而非只复述状态名。
