# P19 测试归并表

阶段提取已由完整原文件覆盖；删除前逐一定义 AST 比较一致。新增独立变化实验仍保留，历史阶段结果不改写。

| 阶段提取 | 当前完整原测试 |
| --- | --- |
| <a id="test_p01_data_contract"></a>test_p01_data_contract.py | [test_context_engineering.py](../tests/test_context_engineering.py)、[test_dependency_contract.py](../tests/test_dependency_contract.py)、[test_pydantic_v2_serialization.py](../tests/test_pydantic_v2_serialization.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p01_public_hardening"></a>test_p01_public_hardening.py | [test_public_hardening.py](../tests/test_public_hardening.py) |
| <a id="test_p02_tool_contract"></a>test_p02_tool_contract.py | [test_tool_contract_fixes.py](../tests/test_tool_contract_fixes.py) |
| <a id="test_p02_tool_policy"></a>test_p02_tool_policy.py | [test_tool_policy.py](../tests/test_tool_policy.py) |
| <a id="test_p03_input_errors"></a>test_p03_input_errors.py | [test_all_agents.py](../tests/test_all_agents.py) |
| <a id="test_p03_recovery_edges"></a>test_p03_recovery_edges.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p03_runtime_contract"></a>test_p03_runtime_contract.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p04_provider_boundaries"></a>test_p04_provider_boundaries.py | [test_public_hardening.py](../tests/test_public_hardening.py) |
| <a id="test_p04_recovery_edges"></a>test_p04_recovery_edges.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p04_runtime_edges"></a>test_p04_runtime_edges.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p05_planner_compatibility"></a>test_p05_planner_compatibility.py | [test_public_hardening.py](../tests/test_public_hardening.py) |
| <a id="test_p05_recovery_edges"></a>test_p05_recovery_edges.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p05_shared_contract"></a>test_p05_shared_contract.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p06_context_pipeline"></a>test_p06_context_pipeline.py | [test_context_pipeline.py](../tests/test_context_pipeline.py) |
| <a id="test_p06_utf8_truncation"></a>test_p06_utf8_truncation.py | [test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p07_agent_components"></a>test_p07_agent_components.py | [test_agent_components.py](../tests/test_agent_components.py) |
| <a id="test_p07_model_injection"></a>test_p07_model_injection.py | [test_public_hardening.py](../tests/test_public_hardening.py) |
| <a id="test_p08_file_contract"></a>test_p08_file_contract.py | [test_tool_contract_fixes.py](../tests/test_tool_contract_fixes.py) |
| <a id="test_p09_trace_privacy"></a>test_p09_trace_privacy.py | [test_public_hardening.py](../tests/test_public_hardening.py) |
| <a id="test_p09_trace_statistics"></a>test_p09_trace_statistics.py | [test_recovery_edges.py](../tests/test_recovery_edges.py)、[test_runtime_contract.py](../tests/test_runtime_contract.py) |
| <a id="test_p09_web_example"></a>test_p09_web_example.py | [test_current_examples.py](../tests/test_current_examples.py) |
| <a id="test_p10_component_integration"></a>test_p10_component_integration.py | [test_agent_components.py](../tests/test_agent_components.py) |
| <a id="test_p10_persistence_contract"></a>test_p10_persistence_contract.py | [test_current_examples.py](../tests/test_current_examples.py) |
| <a id="test_p10_skill_contract"></a>test_p10_skill_contract.py | [test_tool_contract_fixes.py](../tests/test_tool_contract_fixes.py) |
| <a id="test_p11_task_contract"></a>test_p11_task_contract.py | [test_tool_contract_fixes.py](../tests/test_tool_contract_fixes.py) |
| <a id="test_p13_context_integration"></a>test_p13_context_integration.py | [test_context_pipeline.py](../tests/test_context_pipeline.py) |
| <a id="test_p13_document_reread"></a>test_p13_document_reread.py | [test_retrieval_composition.py](../tests/test_retrieval_composition.py) |
| <a id="test_p13_rag_store"></a>test_p13_rag_store.py | [test_retrieval_memory.py](../tests/test_retrieval_memory.py) |
| <a id="test_p14_cloud_contract"></a>test_p14_cloud_contract.py | [test_cloud_retrieval.py](../tests/test_cloud_retrieval.py) |
| <a id="test_p14_pipeline_contract"></a>test_p14_pipeline_contract.py | [test_retrieval_composition.py](../tests/test_retrieval_composition.py) |
| <a id="test_p14_ranking_contract"></a>test_p14_ranking_contract.py | [test_maintenance_budget_evaluation.py](../tests/test_maintenance_budget_evaluation.py) |
| <a id="test_p15_runtime_features"></a>test_p15_runtime_features.py | [test_runtime_features_example.py](../tests/test_runtime_features_example.py) |
| <a id="test_p15_semantic_memory"></a>test_p15_semantic_memory.py | [test_semantic_memory.py](../tests/test_semantic_memory.py) |
| <a id="test_p17_graphrag"></a>test_p17_graphrag.py | [test_graphrag.py](../tests/test_graphrag.py) |
