# P10 设计详解：声明状态、记录理由与按需知识

配套[架构正文](p10-progressive-guide.md)。这些工具可帮助模型组织工作，实际执行证据仍来自 Runtime 和业务回执。

<a id="p10-a1"></a>
## A1：三类信息的写入者与可信度不同

Todo/DevLog 由模型或宿主主动提交，描述的是声明状态与理由；Trace 由实际执行路径产生；Skill 是被加载的知识材料。把三者混成一个“记忆模块”会丢掉信息来源和生命周期。

当前通过 Tool/ToolResponse 统一接入，保持进度格式、日志格式、知识加载与运行控制各自内聚。它们仍可读写本地文件，但不会因此自动成为任务调度器。

<a id="p10-a2"></a>
## A2：声明式覆盖简化接口，也有保存语义

create/update 提交完整列表，未带回的旧事项不会自动保留；clear 将当前列表设为空并保存。TodoList 的一个 in_progress 约束不代表所有工具串行执行。源码对 clear 特殊处理，其他 action 字符串仍沿普通覆盖路径，不能声称未知 action 一律拒绝。

保存文件按秒时间戳命名，同秒更新可能覆盖同名快照，不同秒可留下多个历史文件；clear 不删除全部旧快照。load_todos 加载宿主指定文件，不能假设新建工具自动找到最新文件。

<a id="p10-a3"></a>
## A3：DevLog 的 session_id 决定持久化身份

DevLog 追加有类别的记录，标签按任意匹配过滤，limit 取最新条目。相同 session_id 对应同一文件并在构造时尝试恢复；不同会话可独立记录。summary 是本地统计和近期内容摘录，不需要模型调用。

Todo/DevLog 使用固定 .tmp 后 replace，不具备 P08/P09 独占临时名称和完整并发协调保证。它们先更新内存再持久化，I/O 失败时内存可能已变；不能把文件替换的原子性扩大为内存/磁盘共同事务。当前忠实保留该实现，不顺带重构。

<a id="p10-a4"></a>
## A4：渐进披露控制进入模型的内容

SkillLoader.metadata_cache 只存元数据，skills_cache 才存完整 Skill；磁盘扫描仍读取完整文本。它节省的是启动阶段长期缓存与模型可见内容，不承诺只做少量文件 I/O。

元数据由 SkillTool 构造时放入 description，成为工具 Schema 的一部分；Agent 构造没有把全部正文自动拼进 system_prompt。资源文件递归列举与 SkillTool 的顶层资源 hint 也不同，不应按一个列表理解全部资源。

<a id="p10-a5"></a>
## A5：统一校验减少时间差异

扫描和 get_skill 共用 _parse_metadata，避免 frontmatter 读取与正文使用规则不一致。get_skill 会检查名称是否在扫描后改变；缓存命中则返回已缓存正文，文件修改需要 reload 才刷新。

reload 清除元数据、正文和诊断，再重新扫描。已构造 SkillTool 的 description 是当时字符串快照，不会仅因 loader.reload 自动重算；需要区分加载器实时状态与模型可见工具描述的寿命。

<a id="p10-a6"></a>
## A6：知识回执维持原生消息关系

SkillTool 返回正文和资源提示，Runtime 将其放入 tool 消息，下一次模型请求使用相同 ID 配对。知识加载不直接改宿主系统消息，也不执行 scripts；模型若需要运行资源，还必须另行通过宿主提供的执行能力。

$ARGUMENTS 是字符串替换，token_estimate 实际 len(full_content)，不能当作精确 Token 成本。资源提示显示可用文件，不证明依赖安装或外部服务可用。

<a id="p10-a7"></a>
## A7：恢复真实可选组件，保持已有扩展点

components.register_default_tools 使用明确开关与实际注入对象。SkillLoader 有实例且允许自动注册时加入 Skill；TodoWrite/DevLog 使用各自配置和目录。已有同名定义保留，不覆盖宿主工具。

本阶段补验 P07 的假值 SessionStore 和 LoaderProxy：实际 Skill 返回正文，Session 真正保存/加载，历史计数保持。Task 与 subagent_scope 尚待 P11，因此完整 agent_components 原文件还有两项组合未接回。

<a id="p10-a8"></a>
## A8：资源复刻与资源执行分开验收

17 个目录、131 个跟踪文件逐字节保留；初始正文缓存为 0，17 个正文逐个读取成功，无 diagnostics。元数据名称不必等于目录名，如 Podcast Generate；模型按元数据名称调用 Skill。

技能资源含不同语言脚本与独立说明，核心框架只负责发现和加载。讲解验收：completed 是否证明副作用成功？Trace 与 DevLog 来源为何不同？渐进披露究竟延迟了什么？reload 后已有工具描述是否自动刷新？资源路径出现是否意味着脚本运行？
