"""测试工具过滤器核心功能与策略模式

测试范围：
1. ReadOnlyFilter：验证只读工具白名单放行、写操作拦截与额外工具扩展；
2. FullAccessFilter：验证完全访问放行与高危终端工具黑名单拦截；
3. CustomFilter：验证自定义白名单模式与黑名单模式及非法模式参数拦截。
"""

import pytest
from hello_agents.tools.tool_filter import (
    ToolFilter,
    ReadOnlyFilter,
    FullAccessFilter,
    CustomFilter
)


class TestReadOnlyFilter:
    """测试只读工具过滤器（ReadOnlyFilter）"""
    
    def test_filter_readonly_tools(self):
        """测试批量过滤混合工具列表，只保留只读工具"""
        filter_obj = ReadOnlyFilter()
        
        all_tools = [
            "Read", "Write", "Edit", "LS", "Grep",
            "Bash", "MemoryTool", "SearchTool", "MemorySearchTool", "memory_search"
        ]
        
        filtered = filter_obj.filter(all_tools)
        
        # 验证只读工具放行
        assert "Read" in filtered
        assert "LS" in filtered
        assert "Grep" in filtered
        # 综合 MemoryTool 可写入、修订和撤回，因此不在只读列表；仅独立查询入口属于只读
        assert "MemorySearchTool" in filtered
        assert "memory_search" in filtered
        assert "MemoryTool" not in filtered
        assert "SearchTool" in filtered
        
        # 验证写入及命令执行工具被拦截
        assert "Write" not in filtered
        assert "Edit" not in filtered
        assert "Bash" not in filtered
    
    def test_is_allowed(self):
        """测试对单个工具名称的准入判定"""
        filter_obj = ReadOnlyFilter()
        
        assert filter_obj.is_allowed("Read") is True
        assert filter_obj.is_allowed("LS") is True
        assert filter_obj.is_allowed("Write") is False
        assert filter_obj.is_allowed("Bash") is False
    
    def test_additional_allowed(self):
        """测试通过 additional_allowed 动态扩展允许的自定义只读工具"""
        filter_obj = ReadOnlyFilter(additional_allowed=["CustomTool"])
        
        assert filter_obj.is_allowed("Read") is True
        assert filter_obj.is_allowed("CustomTool") is True
        assert filter_obj.is_allowed("Write") is False


class TestFullAccessFilter:
    """测试完全访问过滤器（FullAccessFilter）"""
    
    def test_filter_deny_dangerous_tools(self):
        """测试排除预设的 Bash/Terminal 等危险执行工具"""
        filter_obj = FullAccessFilter()
        
        all_tools = [
            "Read", "Write", "Edit", "LS", "Grep",
            "Bash", "Terminal", "MemoryTool"
        ]
        
        filtered = filter_obj.filter(all_tools)
        
        # 包含读写与通用工具
        assert "Read" in filtered
        assert "Write" in filtered
        assert "Edit" in filtered
        assert "LS" in filtered
        
        # 排除危险终端工具
        assert "Bash" not in filtered
        assert "Terminal" not in filtered
    
    def test_is_allowed(self):
        """测试单个工具是否被允许调用"""
        filter_obj = FullAccessFilter()
        
        assert filter_obj.is_allowed("Read") is True
        assert filter_obj.is_allowed("Write") is True
        assert filter_obj.is_allowed("Bash") is False
        assert filter_obj.is_allowed("Terminal") is False
    
    def test_additional_denied(self):
        """测试通过 additional_denied 额外禁止特定工具"""
        filter_obj = FullAccessFilter(additional_denied=["Write"])
        
        assert filter_obj.is_allowed("Read") is True
        assert filter_obj.is_allowed("Write") is False
        assert filter_obj.is_allowed("Bash") is False


class TestCustomFilter:
    """测试自定义过滤器（CustomFilter）"""
    
    def test_whitelist_mode(self):
        """测试白名单模式：仅允许 explicitly allowed 工具"""
        filter_obj = CustomFilter(
            allowed=["Read", "LS", "Grep"],
            mode="whitelist"
        )
        
        all_tools = ["Read", "Write", "LS", "Grep", "Bash"]
        filtered = filter_obj.filter(all_tools)
        
        assert filtered == ["Read", "LS", "Grep"]
    
    def test_blacklist_mode(self):
        """测试黑名单模式：拦截 explicitly denied 工具"""
        filter_obj = CustomFilter(
            denied=["Bash", "Terminal"],
            mode="blacklist"
        )
        
        all_tools = ["Read", "Write", "Bash", "Terminal", "LS"]
        filtered = filter_obj.filter(all_tools)
        
        assert "Read" in filtered
        assert "Write" in filtered
        assert "LS" in filtered
        assert "Bash" not in filtered
        assert "Terminal" not in filtered
    
    def test_is_allowed_whitelist(self):
        """测试白名单模式下的单个工具判定"""
        filter_obj = CustomFilter(
            allowed=["Read", "LS"],
            mode="whitelist"
        )
        
        assert filter_obj.is_allowed("Read") is True
        assert filter_obj.is_allowed("LS") is True
        assert filter_obj.is_allowed("Write") is False
    
    def test_is_allowed_blacklist(self):
        """测试黑名单模式下的单个工具判定"""
        filter_obj = CustomFilter(
            denied=["Bash"],
            mode="blacklist"
        )
        
        assert filter_obj.is_allowed("Read") is True
        assert filter_obj.is_allowed("Write") is True
        assert filter_obj.is_allowed("Bash") is False
    
    def test_invalid_mode(self):
        """测试传入无效的模式参数抛出 ValueError 异常"""
        with pytest.raises(ValueError, match="Invalid mode"):
            CustomFilter(mode="invalid")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

