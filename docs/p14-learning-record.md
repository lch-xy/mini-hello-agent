# P14 实现记录：向量索引、融合、重排与评测

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现与阶段契约验证完成；真实模型质量/云账号连通性不作为通过项。阅读：[架构递进](p14-progressive-guide.md)、[设计详解](p14-progressive-explanations.md)。个人理解待讲解验收。

## 实现与来源

新增 embeddings、qdrant、hybrid、rerank、evaluation 五个模块，逐字节与固定目标一致；P13 先行 pipeline 再核对一致。阶段包导出目标新增接口，关系/GraphRAG 仍在后续。

复制目标 retrieval 包示例入口与 qdrant_retrieval_demo，逐字节一致。该实验要求显式模型和维度，当前未调用真实嵌入 API 或下载模型权重。

requirements/p14.txt 引用 P12，按目标 uv.lock 的当前平台依赖闭包固定新增 SDK 库，包括 qdrant-client 1.19.1、fastembed 0.8.1、numpy 2.5.3、onnxruntime 1.30.0。全部新固定项核对安装成功，pip check 无破损依赖。FastEmbed SDK 可导入且 passage_embed/query_embed 入口存在，没有创建真实权重模型。

## 验证证据

原 test_qdrant_retrieval.py 完整恢复，逐字节一致；使用真实本地 Qdrant/持久化与明确的 FixtureEmbedding，验证协议而非模型质量。

cloud_contract、pipeline_contract、ranking_contract 分别提取目标原文件 4、3、3 个定义，AST 一致。Cloud 使用真实 SDK HTTP 序列化与 MockTransport/本地 Qdrant 夹具，不访问真实云端；Memory 路径到 P15 恢复完整 cloud_retrieval。

新增 index_boundaries 验证缺/重复 Embedding input index、重排输入限额与输出截断、混合后端间 revision 变化、旧代回收失败仍保留已发布新代次可用。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**633 passed，12 skipped，无 deselected**；输出见[p14-test-results.log](p14-test-results.log)。Qdrant 原测试没有因缺 SDK 而跳过；既有 skip 范围与 P13 相同。

已实际验证：本地索引重开、未变化不重复编码、版本更新/撤下拒绝旧索引、错误向量/失败上传/编码中变更不发布半品、缺原文拒绝、namespace/借入 client 所有权、RRF、候选完整排列与已知指标。

## 设计边界

SQLite 是权威证据，Qdrant 是派生索引；generation/manifest 控制可见性，查询再次回读原文并校验 active/version/revision。实例锁不替代跨进程单写入协调；发布后旧代清理失败仅警告，不回滚新清单。

RRF 融合排名而非原始分数量纲，重排只能返回全部原候选排列，不能改写来源、内容或召回分数。无正相关标注指标为 None，不能冒充零分。

向量夹具、HTTP 模拟和脚本 LLM 只证明实现契约；未验收 FastEmbed 权重后的真实语义效果、实际 OpenAI Embedding 服务或真实 Qdrant 云账号。自带显式模型实验保留供后续独立学习。

本地 git 显示这些当前模块由 1650f26 引入；按依赖架构递进复刻，不从标题补写动机。P13 `4ae8a32` 已推送，本阶段继续提交并推送 origin/main；下一阶段 P15 补记忆、语义召回、画像与真实组合。
