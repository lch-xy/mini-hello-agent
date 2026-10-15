# P13 实现记录：版本化证据库与 BM25

日期：2026-10-15。固定目标：`V1.0.1 / c597a7ca46ea9969e5b21d9e130549ab69626ff9`。

实现与阶段验证完成，个人理解待讲解验收。阅读：[架构递进](p13-progressive-guide.md)、[设计详解](p13-progressive-explanations.md)。

## 实现与选择

新增 storage.py、retrieval/store.py、retrieval/pipeline.py、builtin/rag_tool.py 四文件，逐字节与固定目标一致；阶段 retrieval 包导出当前 Store/结果/检索契约，tools/builtin 增加目标 RAGTool。

storage 因 guard_schema 依赖先整体落地，备份/迁移函数待 P18 专项；pipeline 因 SearchBackend 协议先落地，重排与向量组合待 P14。没有新增依赖或向量服务，不重写 Agent/Runtime。

权威 SQLite 保存不可变原文版本和稳定片段身份；BM25 只检索当前 active，旧证据可以按确切 ID/版本回读。资料导入属于宿主，RAGTool 只暴露搜索/回读。

## 验证来源与结果

从原 retrieval_memory 提取 3 个当前 RAG 定义（含非法切片参数化），从 retrieval_composition 提取 source 与文档回读原测试，从 context_pipeline 提取 CapturingLLM/config 与真实 RAG 往返、自定义 Assembler/Provider 两原测试；分别 3、2、4 个定义 AST 一致。

新增 store_identity：namespace/revision/snapshot、旧版幂等且不重新激活、同版本不同切片参数不重建、v10→v2 按导入顺序、撤下重复调用、旧证据、新进程读取、BM25 同分稳定 ID/查询去重、未知 Schema/:memory: 拒绝。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

**590 passed，12 skipped，无 deselected**；输出见[p13-test-results.log](p13-test-results.log)。所有 SQLite 文件与进程实验使用临时目录；跳过范围与 P12 相同，未调用外部模型或检索服务。

## 实际边界

字符区间按 Python 字符串，不是 UTF-8 字节；同版本来源/内容不可变，幂等导入不会重切片或激活旧版。namespace 区分资料库，version 是宿主标签，revision 记录当前集合变更；三者不可混同。

BM25 每次扫描当前片段，适用于小型资料库；分数不是语义置信度，无词项重叠返回空。snapshot 保证一次一致读，不为整个外部索引构建加全局锁。

当前 RAG Provider/工具组合已补验 P06 的对应范围；Memory 撤回组合待 P15，完整 context_pipeline 原文件届时恢复。完整 retrieval_memory 待 P15，retrieval_composition 的关系路径待 P16。

本地 git 显示当前 Store/storage/RAGTool 由 1650f26 引入；教学按数据身份→持久化→版本→检索递进，而非推测历史作者动机。

P12 `387e530` 已推送，本阶段同样提交并推送 origin/main。下一阶段 P14 在这些证据身份之上接入可替换向量索引、混合召回与重排评测。
