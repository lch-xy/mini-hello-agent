# P15 设计详解：沿着变化划分状态所有权

对应[架构递进](p15-progressive-guide.md)。建议每节先画出当前边界，再说明新增需求迫使哪个边界变化。

<a id="p15-a1"></a>
## A1：由生命周期确定状态归属

| 状态 | 负责回答 | 所有权与生命周期 |
| --- | --- | --- |
| 历史 | 这一轮说过、调用过什么 | Agent/SessionStore；保留角色和工具调用配对 |
| MemoryRecord | 这个任务当前认可哪些带来源记忆 | MemoryStore；user/task，显式修订和撤回 |
| ProfileSnapshot | 跨会话哪些稳定字段当前有效 | ProfileStore；user/namespace，严格业务 Schema、版本和字段来源 |

将三者统一成一个万能表需要每个调用者解释状态，反而降低内聚。这里各 Store 集中自己的不变量，Provider 只将当前可见结果转为统一 ContextPacket。相同用户偏好可出现在两处，但应用必须明确保存策略；框架没有自动双写协调事务。

<a id="p15-a2"></a>
## A2：范围是构造依赖，而非可变查询条件

可选方案包括每个动作传身份、每用户一个物理数据库、同库中宿主绑定范围。目标选第三种：连接可共用文件，每条 SQL 都按绑定身份限定，包括 get/revise/retract 已知 ID。UUID namespace 在范围内持久化，另一个用户变更不会使本用户索引失效。

MemoryTool 是动作/错误协议的适配器，Store 拥有事务和范围约束，模型只提供业务参数。它不能单凭 prompt 文字切换身份。不过宿主把错误 Store 注册给错误用户仍会泄露；对象边界不是账号认证系统。注册前的授权由应用决定。

<a id="p15-a3"></a>
## A3：状态机与事务先于搜索能力

最小闭环先实现 add/读取当前记录，然后增加两种变化：revise 必须检查原记录 active，将其标记 superseded，追加 active 新记录；retract 将当前记录改为 retracted。二者用立即事务避免半状态，revision 只随成功变化增加，重复 retract 不重复增加。五种 kind 区分偏好、事实、经历、推断、步骤，但不会自动核实 source。

这是带修订链的持久化状态，并非完整事件溯源系统。撤回只阻止未来 Store/Provider 召回；retract 的 ToolResponse 仍带原内容，历史请求、回执和模型已收到的上下文不会倒带删除。P18 才验证维护接口的实际清理范围。

<a id="p15-a4"></a>
## A4：适配现有协议，把一致性集中在一处

另写 MemoryVectorStore 会重复范围、代次、失败发布和回读校验。_MemoryDocuments 使用 Adapter：snapshot 提供当前 active 记忆，read_chunk/read_document 回读同一权威记录，namespace 加 memory: 前缀，Memory ID 保持原身份。QdrantSearch 管发布与失效，HybridSearch 组合词项与向量，SemanticMemorySearch 最后再核对范围 revision。

非空查询需要当前有效索引，变更后由宿主显式 sync。空查询在语义层仍直接读取近期有效记录，不需要向量；这是入口已声明的不同语义，不能概括为“所有搜索都等同步”。MemoryTool 将过期转换为 CONFLICT；Provider 的异常交给调用宿主处理，不在此擅自同步或跨范围降级。

<a id="p15-a5"></a>
## A5：把业务字段的严格性放在写入边界

ProfileModel 使用 strict=True、extra=forbid、validate_default=True。应用定义 walking_km 等字段，Store 检查子类继续保留这些约束；Schema hash 标识完整 JSON Schema，改模型后显式拒绝读取原语义数据，不自动猜迁移。

尺寸限制按 UTF-8 的数据与 sources 合计。数据有效、来源存在、Schema 稳定与现实事实正确是不同层面的保证。画像不需要复用自由文本记忆的语义索引；这使其写入验证内聚，也允许 Provider 只依赖当前快照。

<a id="p15-a6"></a>
## A6：乐观并发控制表达“我修改的是哪一版”

读 revision=1 后两个写者分别改 5 和 6，后写覆盖会丢更新。expected_revision 是调用者的前提条件，事务中比较当前版本；不相同抛 ProfileConflict。应用重读并作业务决定，而不是自动重复覆盖。

patch 先用严格 JSON 校验恢复 date/tuple/enum 等 Python 类型，再合并顶层字段并重验。嵌套对象整体替换；不隐含递归合并、字段删除或多表事务。未改字段保留来源，新增默认值也有来源。每次写入新增快照；forget 新增 forgotten 墓碑，get 默认忽略它，history 仍可审计。外层 frozen dataclass 也不意味着内部 dict 深度不可变。

<a id="p15-a7"></a>
## A7：查询、组装、运行各自只拥有自己的变化

Store 管范围与事务；可替换 SearchBackend 管召回；Provider 每次返回当前包；Builder 管预算、选择和消息结构；Runtime 在下一次模型请求前重新收集。这是协议注入与组合，Provider 可理解为查询适配器，不需要为每一层套工厂或复杂模式。

required 由宿主选定；Profile 默认 required，Memory 默认可选。必需包放不下会预算失败，而不是偷偷删掉约束。宿主仍需显式装配 Builder/Provider，单独创建 Store 不会自动接入 Agent。完整离线纵向案例见 [runtime_features.py](../examples/agents/runtime_features.py)；画像重开案例见 [profile_memory_demo.py](../examples/memory/profile_memory_demo.py)。

<a id="p15-a8"></a>
## A8：验证变化路径，而非只验证 CRUD 能调用

保留原 retrieval_memory/context_pipeline/cloud_retrieval/profile_memory 的完整断言，语义记忆当前三个原测试验证范围、更新/撤回与真实 Qdrant 重开。新增边界实验同时展示“近期读当前、非空查询拒绝过期、撤回回执仍含原文”。完整 Runtime 示例证明这条纵向链能与工具配对和会话恢复组合。

租约丢失发生在后台调度边界，故完整 semantic_memory 的 LeaseLost 案例在 P18 接入，不制造临时后台替身。真实 Embedding 权重与模型效果未由确定性向量证明。

历史对照：当前文件由 1650f26 引入，旧 0cbd4d2/de9cf50 的 MemoryManager 是早期路线。比较“状态由谁持有、修订依据放哪里、身份在哪绑定”，不要把旧类型名单直接套进当前 main。讲解验收需要你说明选择及代价，测试通过不代替个人理解。
