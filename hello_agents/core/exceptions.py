"""框架异常的继承层次，便于调用者选择统一捕获或按模块捕获。

注意实际抛出位置：P01 适配器目前包装为 HelloAgentsException，参数边界还会
直接抛 TypeError、ValueError、RuntimeError；并非所有错误都已归入下面的子类。
"""

class HelloAgentsException(Exception):
    """HelloAgents基础异常类"""
    # 捕获此基类也能捕获继承它的所有模块异常。
    pass

class LLMException(HelloAgentsException):
    """LLM相关异常"""
    # 先定义分类契约；现有适配器并未统一改用此子类。
    pass

class AgentException(HelloAgentsException):
    """Agent相关异常"""
    # Agent 执行层将在后续阶段接入。
    pass

class ConfigException(HelloAgentsException):
    """配置相关异常"""
    # Config 的 Pydantic 校验错误仍按其原有异常类型抛出。
    pass

class ToolException(HelloAgentsException):
    """工具相关异常"""
    # 工具执行层将在后续阶段接入；模型提出工具请求本身不等于工具执行失败。
    pass
