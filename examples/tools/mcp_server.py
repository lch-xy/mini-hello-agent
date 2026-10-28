"""本地 MCP 测试与示例服务（基于官方 mcp.server.MCPServer）

提供旅行路线交通信息的测试夹具服务，支持两种传输模式：
1. stdio（标准输入输出）：供子进程管道模式运行；
2. streamable-http（基于 HTTP 的流式服务）：指定 --port 端口时启动。
"""

import argparse
from typing import Any
from mcp.server import MCPServer

# 初始化 FastMCP 测试服务实例
server = MCPServer("Travel fixture")


@server.tool(structured_output=True)
def route_info(place: str) -> dict[str, Any]:
    """返回示例数据集中目标景点的交通与用时信息

    Args:
        place: 目的地名称（如 "museum" 或 "lake"）

    Returns:
        包含目的地、耗时（分钟）、步行距离（公里）及来源标识的结构化字典

    Raises:
        ValueError: 传入未知目的地时抛出
    """
    routes = {
        "museum": {"minutes": 25, "walking_km": 1},
        "lake": {"minutes": 40, "walking_km": 4},
    }
    if place not in routes:
        raise ValueError("Unknown destination")
    return {"place": place, **routes[place], "source": "example-fixture"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="指定 HTTP 端口；不指定则默认以 stdio 模式运行")
    args = parser.parse_args()
    if args.port:
        # HTTP 流式服务端模式
        server.run("streamable-http", host="127.0.0.1", port=args.port)
    else:
        # 子进程标准输入输出模式
        server.run("stdio")
