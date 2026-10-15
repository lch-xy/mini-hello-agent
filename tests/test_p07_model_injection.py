from unittest.mock import Mock
from hello_agents import SimpleAgent, AgentComponents

def test_injected_or_main_model_for_summary_and_subagent():
    main, other = Mock(model='main'), Mock(model='other')
    agent = SimpleAgent('test', main)
    assert agent._get_summary_llm() is main
    assert agent._create_light_llm() is main
    agent = SimpleAgent('test', main, components=AgentComponents(summary_llm=other, subagent_llm=other))
    assert agent._get_summary_llm() is other
    assert agent._create_light_llm() is other

