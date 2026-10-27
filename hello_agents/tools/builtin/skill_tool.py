"""Skill Tool - 技能工具

允许 Agent 按需加载领域知识。

特性：
- 渐进式披露：仅在需要时加载完整技能
- 缓存友好：作为 tool_result 注入，不修改 system_prompt
- 资源提示：自动列出可用的脚本、文档、示例等
- 参数替换：支持 $ARGUMENTS 占位符

使用示例：
    >>> from hello_agents.skills import SkillLoader
    >>> from hello_agents.tools.builtin.skill_tool import SkillTool
    >>> loader = SkillLoader(skills_dir=Path("skills"))
    >>> tool = SkillTool(skill_loader=loader)
    >>> # Agent 调用
    >>> response = tool.run({"skill": "pdf"})
"""

from typing import Dict, Any, List
from ..base import Tool, ToolParameter
from ...skills.loader import SkillLoader
from ..response import ToolResponse
from ..errors import ToolErrorCode


class SkillTool(Tool):
    """
    技能工具

    允许模型按需加载领域知识。
    """

    def __init__(self, skill_loader: SkillLoader):
        """初始化技能工具

        Args:
            skill_loader: 技能加载器实例
        """
        # 生成动态描述
        descriptions = skill_loader.get_descriptions()

        super().__init__(
            name="Skill",
            description=f"""加载技能获取专业知识。

可用技能：
{descriptions}

何时使用：
- 任务明确匹配某个技能描述时，立即使用
- 开始领域特定工作之前
- 需要模型不具备的专业知识时

注意：加载技能后，请严格遵循技能说明来完成用户任务。""",
            expandable=False
        )
        self.skill_loader = skill_loader

    def get_parameters(self) -> List[ToolParameter]:
        """获取技能工具的参数定义

        包含技能名称（必填）和可选参数（用于占位符替换）。
        """
        return [
            ToolParameter(
                name="skill",
                type="string",
                description="要加载的技能名称",
                required=True
            ),
            ToolParameter(
                name="args",
                type="string",
                description="可选参数，将替换 SKILL.md 中的 $ARGUMENTS 占位符",
                required=False,
                default=""
            )
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行技能加载

        根据技能名称从 SkillLoader 中获取技能主体，执行参数替换并生成资源提示。
        返回内容采用 <skill-loaded> 标签包裹，作为 tool_result 返回以保持缓存友好性。

        Args:
            parameters: 包含 skill（技能名）和可选 args（替换参数）的参数字典

        Returns:
            ToolResponse: 包含完整技能内容的标准化响应
        """
        skill_name = parameters.get("skill", "")
        args = parameters.get("args", "")

        # 参数校验：技能名称不能为空
        if not skill_name:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="必须指定技能名称",
                context={"params_input": parameters}
            )

        try:
            # 按需加载技能对象（若有缓存则优先使用缓存）
            skill = self.skill_loader.get_skill(skill_name)

            # 技能不存在时返回错误并列出所有可用技能
            if not skill:
                available = ", ".join(self.skill_loader.list_skills())
                return ToolResponse.error(
                    code=ToolErrorCode.NOT_FOUND,
                    message=f"技能 '{skill_name}' 不存在。可用技能：{available}",
                    context={"params_input": parameters, "available_skills": self.skill_loader.list_skills()}
                )

            # 动态替换技能文档中的 $ARGUMENTS 占位符
            content = skill.body.replace("$ARGUMENTS", args)

            # 扫描并列出技能附属目录中的可用资源（脚本、文档、资产等）
            resources_hint = self._get_resources_hint(skill)

            # 构造完整技能内容（采用 <skill-loaded> 结构化标记注入，不污染 system_prompt）
            full_content = f"""<skill-loaded name="{skill_name}">
{content}
{resources_hint}
</skill-loaded>

✅ 技能已加载：{skill.name}
📝 描述：{skill.description}

请严格遵循上述技能说明来完成用户任务。"""

            return ToolResponse.success(
                text=full_content,
                data={
                    "name": skill.name,
                    "description": skill.description,
                    "loaded": True,
                    "token_estimate": len(full_content),
                    "has_resources": bool(resources_hint)
                }
            )

        except Exception as e:
            # 捕获未知内部异常并返回 INTERNAL_ERROR 错误响应
            return ToolResponse.error(
                code=ToolErrorCode.INTERNAL_ERROR,
                message=f"加载技能失败：{str(e)}",
                context={"params_input": parameters, "error": str(e)}
            )

    def _get_resources_hint(self, skill) -> str:
        """生成资源提示文本

        扫描技能附属目录（scripts/references/assets/examples）下的文件列表。
        注意：此处仅作文件信息展示与发现提示，严格不自动执行任何脚本。

        Args:
            skill: Skill 对象

        Returns:
            格式化的资源提示文本；若无资源则返回空字符串
        """
        resources = []

        # 扫描四大资源子目录并生成人类可读的提示标签
        for folder, label in [
            ("scripts", "脚本"),
            ("references", "参考文档"),
            ("assets", "资源"),
            ("examples", "示例")
        ]:
            folder_path = skill.dir / folder
            if folder_path.exists():
                files = list(folder_path.glob("*"))
                if files:
                    file_list = ", ".join(f.name for f in files[:5])  # 最多显示 5 个文件名称
                    if len(files) > 5:
                        file_list += f" 等 {len(files)} 个文件"
                    resources.append(f"  - {label}：{file_list}")

        if not resources:
            return ""

        return "\n\n**可用资源**：\n" + "\n".join(resources)

