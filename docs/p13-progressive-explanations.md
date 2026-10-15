# P13 设计详解：证据身份、版本与轻量检索

配套[架构正文](p13-progressive-guide.md)。原文存储是权威数据，检索与未来向量索引是可替换的读取方式。

<a id="p13-a1"></a>
## A1：先有证据，再讨论召回算法

RetrievalResult 保存 chunk/document/source/version/区间/content/score，DocumentResult 保存完整版本与 active。to_context_packet 把来源信息带到组装器，不要求 Agent 认识 SQLite 表。

如果检索结果只有文本，就无法稳定核对旧引用、识别资料更新或支持未来索引回查。本阶段先固定这种契约，后续召回模型变化不改变证据身份。

<a id="p13-a2"></a>
## A2：连接短寿命，事务明确

每次操作打开连接，guard_schema 后在事务上下文里执行，finally 关闭。写入用 BEGIN IMMEDIATE，读 snapshot 用一致事务。RAGStore 明确拒绝 :memory:，因为它不维护一个永久连接来承载内存库。

storage.py 随 guard_schema 整体落地，备份/迁移函数留 P18 专项验收；没有因此启用外部迁移工具。pipeline 模块因 SearchBackend 协议一并提供，重排行为留 P14。

<a id="p13-a3"></a>
## A3：不可变版本允许稳定审计

document_id 默认由 source 的 SHA-256 截取生成，宿主也可显式指定。digest 检查同版本原文，来源也必须一致；同版本相同导入直接返回，不重新切片或激活。

新版本先撤下旧 active，再写文档/片段并提升 revision。namespace 是持久 UUID，用于区分资料库；version 是宿主标签；revision 是当前集合变化计数。v10 后导入 v2，当前即 v2，不能借字符串排序猜时间。

<a id="p13-a4"></a>
## A4：字符区间比重写片段更易核对

按字符保留原始空白与 emoji，区间使用 Python 字符串 [start,end)。chunk_id 由文档/版本/区间哈希形成，不因检索排序改变。它不是字节偏移，也不自动按语义句子截断。

同版本不同 chunk_size 的重复导入仍幂等；想改变证据切片需要明确新版本/身份，不能悄悄重写旧 chunk。这个约定为 P14 外部索引和后续关系证据提供稳定基础。

<a id="p13-a5"></a>
## A5：BM25 的统计单位是当前片段

每次扫描当前片段，用 lexical_terms 构造词频、片段长度和文档频次；这里频次统计单位是 chunk，不是原始文档。k1=1.2、b=0.75，查询词项去重，得分相同按 ID 稳定排序。

中文使用单字/相邻双字，英文按词；这是词法启发式，不是语义向量。无重叠返回空能明确表达没有证据，不能为了回答强行返回一段最高但无关内容。代价是每次扫描/分词，目标定位小型教学资料库。

<a id="p13-a6"></a>
## A6：当前可用与历史可核对不冲突

deactivate_document 仅撤下 active，实际改变才提升 revision；旧原文和片段仍保存。search 面向当前集合，read_chunk/显式 read_document 面向确切证据，行为必须不同。

snapshot 在同一事务读 revision 和行，避免外部索引把不同时间的版本号与片段组合在一起。P14 还需在索引发布前再检查 revision；这里的一次一致快照不是整个索引构建过程的全局锁。

<a id="p13-a7"></a>
## A7：检索策略与权威回读分离

RAGTool 接受可替换 search_backend，但 read/read_document 仍用 RAGStore。未来重排或向量后端负责候选排序，不能创造或改写权威原文。固定 Provider 与工具式查询只是调用时机/控制者不同。

本阶段恢复真实 RAG 工具往返和自定义 Assembler/Provider 的原用例；Memory Provider 组合仍待 P15。导入没有暴露成模型工具，资料范围由宿主选择 Store 与后端。

<a id="p13-a8"></a>
## A8：成功示例之外验证身份不漂移

新进程读取相同 namespace/revision/片段，旧版本重新导入不复活，撤下重复调用不加 revision，非法导入不破坏当前集合，构成后续索引一致性的基础证据。

讲解验收：为何旧证据允许读、却不允许当前检索命中？source/version/revision/namespace 分别管什么？切片参数改变为何不重写同版本？没有向量如何 RAG？向量索引与原文库谁是权威？
