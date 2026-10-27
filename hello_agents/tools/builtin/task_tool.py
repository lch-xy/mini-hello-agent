"""Task 工具 - 子代理调用工具

允许主 Agent 启动子代理处理子任务，实现上下文隔离。
"""

from typing import Dict, Any, List, Optional, Callable, TYPE_CHECKING
from ..base import Tool, ToolParameter
from ...core.agent import Agent
from ...core.llm import HelloAgentsLLM
from ...core.config import Config
from ..response import ToolResponse
from ..errors import ToolErrorCode
from ..tool_filter import ToolFilter, ReadOnlyFilter, FullAccessFilter, CustomFilter

if TYPE_CHECKING:
    from ...tools.registry import ToolRegistry


class TaskTool(Tool):
    """子代理工具

    允许主 Agent 启动隔离的子代理来处理子任务。

    特性：
    - 支持任意 Agent 类型（react/reflection/plan/simple）
    - 上下文隔离（子代理有独立历史）
    - 工具过滤（控制子代理可用工具）
    - 摘要返回（避免污染主上下文）
    - 可选轻量模型（节省成本）
    """

    def __init__(
        self,
        agent_factory: Callable[[str], Agent],
        tool_registry: Optional["ToolRegistry"] = None,
        config: Optional[Config] = None,
    ):
        """初始化 TaskTool

        Args:
            agent_factory: Agent 工厂函数，接受 agent_type 返回 Agent 实例
            tool_registry: 工具注册表（传递给子代理）
            config: 配置对象
        """
        super().__init__(
            name="Task",
            description="启动子代理处理特定的子任务，使用隔离的上下文。适用于：探索代码库、规划任务、实现功能等需要独立上下文的场景。",
            expandable=False,
        )
        self.agent_factory = agent_factory
        self.tool_registry = tool_registry
        self.config = config or Config()

    def get_parameters(self) -> List[ToolParameter]:
        """获取 Task 工具参数规格说明

        定义子任务内容（必填）、Agent 类型、工具过滤策略以及最大步数覆盖参数。
        """
        return [
            ToolParameter(
                name="task",
                type="string",
                description="子任务的详细描述，告诉子代理具体要做什么",
                required=True,
            ),
            ToolParameter(
                name="agent_type",
                type="string",
                description="子代理类型：react（推理行动）、reflection（反思）、plan（规划）、simple（简单对话）",
                required=False,
                default="react",
                json_schema={
                    "type": "string",
                    "enum": ["react", "reflection", "plan", "simple"],
                },
            ),
            ToolParameter(
                name="tool_filter",
                type="string",
                description="工具过滤策略：readonly（只读工具）、full（完全访问）、none（无过滤）",
                required=False,
                default="none",
                json_schema={"type": "string", "enum": ["readonly", "full", "none"]},
            ),
            ToolParameter(
                name="max_steps",
                type="integer",
                description="最大步数限制（覆盖默认配置）",
                required=False,
                json_schema={"type": "integer", "minimum": 1},
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行子代理任务并聚合返回结果

        流程：
        1. 严格参数校验（task、agent_type、tool_filter、max_steps 类型与值合法性）；
        2. 先实例化 tool_filter 并调用 agent_factory 构建独立子代理；
        3. 调用子代理的 run_as_subagent 进入隔离上下文运行；
        4. 捕获执行耗时并根据 success 标识封装为 ToolResponse（成功或局部完成）。

        Args:
            parameters: 工具参数字典

        Returns:
            ToolResponse: 标准化工具响应
        """
        import time

        start_time = time.time()

        # 1. 结构与参数基础校验
        if not isinstance(parameters, dict):
            return ToolResponse.error(ToolErrorCode.INVALID_PARAM, "工具参数必须是对象")
        task = parameters.get("task", "")
        agent_type = parameters.get("agent_type", "react")
        tool_filter_type = parameters.get("tool_filter", "none")
        max_steps = parameters.get("max_steps")

        # 校验 task 必须为非空白字符串
        if not isinstance(task, str) or not task.strip():
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message="参数 'task' 不能为空"
            )

        # 校验 agent_type 合法枚举
        if not isinstance(agent_type, str) or agent_type.lower() not in {
            "react",
            "reflection",
            "plan",
            "simple",
        }:
            return ToolResponse.error(
                ToolErrorCode.INVALID_PARAM, "不支持的 agent_type"
            )
        # 校验 tool_filter 参数类型
        if not isinstance(tool_filter_type, str):
            return ToolResponse.error(
                ToolErrorCode.INVALID_PARAM, "tool_filter 必须是字符串"
            )
        # 校验 max_steps 必须为正整数
        if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
            return ToolResponse.error(
                ToolErrorCode.INVALID_PARAM, "max_steps 必须是正整数"
            )
        agent_type = agent_type.lower()
        tool_filter_type = tool_filter_type.lower()

        try:
            # 2. 在工厂产生副作用前检查过滤策略，未知策略拒绝执行而不降级为全开
            tool_filter = self._create_tool_filter(tool_filter_type)
            # 通过工厂创建目标类型的子代理实例
            subagent = self.agent_factory(agent_type)

            # 3. 运行子代理（以隔离上下文模式执行）
            print(f"\n[SubAgent-{agent_type}] 开始执行: {task[:50]}...")

            result = subagent.run_as_subagent(
                task=task,
                tool_filter=tool_filter,
                return_summary=True,
                max_steps_override=max_steps,
            )

            # 4. 计算耗时统计
            elapsed_ms = int((time.time() - start_time) * 1000)

            # 5. 组装标准 ToolResponse 响应返回宿主
            if result["success"]:
                print(
                    f"[SubAgent-{agent_type}] 完成 ({result['metadata']['steps']} 步, {result['metadata']['duration_seconds']}秒)"
                )

                return ToolResponse.success(
                    text=f"[SubAgent-{agent_type}] 任务完成\n\n{result['summary']}",
                    data={"agent_type": agent_type, "task": task, **result["metadata"]},
                    stats={"time_ms": elapsed_ms},
                )
            else:
                print(
                    f"[SubAgent-{agent_type}] 未完成: {result['metadata'].get('error', '未知错误')}"
                )

                # 任务未完全完成时返回 partial 状态
                return ToolResponse.partial(
                    text=f"[SubAgent-{agent_type}] 任务未完全完成\n\n{result['summary']}",
                    data={"agent_type": agent_type, "task": task, **result["metadata"]},
                    stats={"time_ms": elapsed_ms},
                )

        except ValueError as e:
            # 捕获参数或配置异常（如非法策略类型）
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"子代理参数或配置无效: {str(e)}",
            )

        except Exception as e:
            # 捕获未知执行期异常
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR, message=f"子代理执行失败: {str(e)}"
            )

    def _create_tool_filter(self, filter_type: str) -> Optional[ToolFilter]:
        """根据策略字符串创建对应的工具过滤器实例

        Args:
            filter_type: 过滤器类型名称（'readonly', 'full', 'none'）

        Returns:
            ToolFilter 实例或 None（表示无过滤）

        Raises:
            ValueError: 传入不支持的策略类型时抛出
        """
        if filter_type == "readonly":
            return ReadOnlyFilter()
        elif filter_type == "full":
            return FullAccessFilter()
        elif filter_type == "none":
            return None
        else:
            raise ValueError(
                f"不支持的 tool_filter: {filter_type}。支持 readonly、full、none"
            )
