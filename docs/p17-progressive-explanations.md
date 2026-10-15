# P17 设计详解：用明确发布边界连接有成本的生成步骤

对应[架构递进](p17-progressive-guide.md)。完整运行示例见 [graphrag_demo.py](../examples/retrieval/graphrag_demo.py)，默认预设模型响应，真实执行其余流程。

<a id="p17-a1"></a>
## A1：从需求选择三条流程，而非一个万能 search

| 路线 | 适合的问题 | 代价与当前选择 |
| --- | --- | --- |
| BM25/向量 RAG | 找匹配片段 | 简单；跨资料关系和整体概览由回答端承担 |
| P16 显式关系遍历 | 精确实体的方向和有限跳数 | 可解释；宿主维护关系，没有社区报告 |
| GraphRAG | 局部依赖追查与跨社区综合 | 构建、存储、模型成本更高；当前按需求增加 |

RAGStore 拥有原文与 revision；GraphStorage 拥有派生代次与成功缓存；GraphModel 拥有生成协议；GraphRAGIndex 编排构建与查询；GraphRAGTool 转换工具协议。局部查询读已发布索引返回原文，全局查询读取报告再调用模型。此分工让模型更换、原文更新、查询策略变化分别落在各自边界。

<a id="p17-a2"></a>
## A2：模型适配器与生成管线分担不同约束

GraphModel 仅要求稳定 model_id 和 generate(stage,payload,预算)；当前四阶段 extract/report/map/reduce 各有严格 Schema。HelloAgentsGraphModel 复用 HelloAgentsLLM.invoke，身份摘要包含模型/协议/base_url、调用参数、指令和 Schema。自定义模型必须自行正确版本化 model_id。

适配器管 JSON 解析、Schema、提示词开销和响应截断；管线再次验证结构、阶段预算、领域证据与引用。有限重试只针对 GraphIntegrityError 的格式/Schema 问题，反馈不回显无效字段原值；预算、截断、超时等不在此静默修补。不能因 prompt 声明“不信任原文”就宣称消除了注入或语义错误。

<a id="p17-a3"></a>
## A3：知识身份由原文与规范化契约决定

片段抽取先验证局部 key 唯一、关系端点已声明、名字或别名在 quote 中逐字出现、quote 是受长度限制的连续子串。全局实体 ID 来自 NFKC/casefold/空白规范化后的 kind/name/scope；有向关系按源、目标、谓词聚合。别名到多个实体的映射保留为 ambiguous_aliases，不擅自消歧。

Evidence ID 绑定 chunk、片段内首次匹配偏移与 quote；保存的 start/end 是完整文档字符偏移。若同一句多次出现，当前使用首次 index 定位，不夸大成模型精确定位到任意一次。引用校验保证位置与身份，不能证明模型所说的关系或 claim 在语义上成立。

<a id="p17-a4"></a>
## A4：聚类投影与关系事实分开

聚类将不同方向关系投影为无向边，权重累加关系 evidence 数量；自环不加入投影。真实 igraph/leidenalg 在父分组内部继续划分，resolution 按层翻倍，固定 seed 和迭代次数。原有有向关系仍保存在知识结构中，但局部邻域同样使用无向投影，这与 P16 可指定方向的 BFS 不同。

无实体 chunk 仍生成原文 evidence，并进入各层 unassigned 社区；否则全局流程会在抽取阶段静默遗漏资料。一个 evidence 可属于多个社区或层级，这是组织方式的重叠，不是分包重复错误。报告分包的“一次”指同一待分包列表中的每项恰好分配一次。

<a id="p17-a5"></a>
## A5：用代次发布隔离半成品，用缓存减少重做

构建顺序为 snapshot→逐片段校验抽取/缓存→assemble→Leiden→报告分包/校验/缓存→可选实体向量资产→检查 revision→GraphStorage.publish。构建报告记录规模、缓存命中、歧义与 generation。未变 revision/config 可直接复用活跃代次。

抽取键包含片段身份/版本/内容与模型和限制，报告键包含实际 payload、模型和输入输出预算；缓存命中仍走证据或引用校验。失败保留成功缓存但不发布未完代次。发布在立即事务中比较 expected_generation，防止后完成者覆盖先发布的新图。

这是派生数据的构建/发布边界，不是资料库与图数据库的分布式事务；资料 revision 在发布前及查询末检查。可选向量资产按 generation 分目录，失败遗留和历史资产清理由宿主管理，GraphStorage.prune 不会递归删除所有外部资源。checkpoint 只在检查点协作中断，外部模型请求仍需自身超时。

<a id="p17-a6"></a>
## A6：局部路径先找到种子，再选择有原文的邻域

直接别名匹配优先；没有直接命中时用词项重叠补充到 seed_limit；可选 Qdrant 实体召回再取并集。直接命中可包含多个歧义实体，seed_limit 不能被解释为所有种子的绝对数量上限。

无向邻域给实体距离，证据所在 chunk 得到 1/(1+距离) 的基础分，再加原文词项排名提升。输出裁到 limit，同时返回实体、相关关系与粗层报告；完整局部结构超预算则失败。借用 P14 既有 QdrantSearch，避免再次实现向量发布和版本校验。分数是排序信号，语义种子夹具也不证明实际模型召回质量。

<a id="p17-a7"></a>
## A7：全局归约优先保证输入处理可见

所选层全部报告逐一 map；claims 按 payload 预算装包，每项必须可容纳，reduce 后若仍多包则继续，超过 max_reduce_rounds 失败。单项超长、报告过多和输出截断同样明确失败，不删除难处理部分。每次生成的 citations 必须属于这一次输入允许的 evidence 集，最终原文回读校验版本和字符区间。

coverage.complete 表示该层报告均被 map，且每个源 chunk 至少在报告 claim 引用中有覆盖；它不要求所有最终 claim 覆盖全部原文，也不证明摘要保留了所有例外或答案正确。空 claims 返回 no_answer；有答案但源覆盖不完整为 answered_partial。新非收敛实验让 reduce 原样返回三个大 claim，验证六次调用后在两轮上限失败，而非把 claim 删到能装下一包。

<a id="p17-a8"></a>
## A8：装配与验证围绕所有权变化

GraphRAGTool 仅暴露 local/global，索引构建与原文写入留在宿主；global 虽不修改资料，仍有多次模型调用成本。当前原测试验证真实 Leiden 的 seed/父子分组、原文引用、真实本地 Qdrant 种子、重开与缓存、坏抽取/引用、资料变更、竞争发布、取消、预算、空材料/无答案和 Registry 组合。后台 JobWorker 的原测试在 P18 完整恢复。

当前七个 graph 模块与工具由 1650f26 引入；这是从一个大特性提交中按依赖拆出的学习阶段。设计使用协议注入、Adapter、生成流水线与 CAS 发布，不额外引入调度框架或万能工厂。真实模型效果、语义抽取质量和公平性能基准不由本次离线夹具证明。
