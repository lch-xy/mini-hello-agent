"""轻量级 SSE 客户端验证脚本。

使用 Python 标准库 `urllib` 实现，零第三方网络库依赖（无需 requests/httpx）。
使用方法：
1. 启动服务：`python -m examples.web.fastapi_sse_server`
2. 运行本脚本：`python -m examples.web.test_sse_client`
"""

import json
from urllib.request import Request, urlopen


def main():
    """向本地 SSE 接口发起 POST 请求并按行解析 Server-Sent Events 事件流。"""
    # 构建请求体，指定测试输入与智能体类型
    request = Request(
        "http://127.0.0.1:8000/agent/stream",
        data=json.dumps({"input": "你好", "agent_type": "simple"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    finished = False
    # 打开长连接流式读取响应
    with urlopen(request, timeout=120) as stream:
        for raw in stream:
            line = raw.decode("utf-8").strip()
            # 过滤并提取 SSE 的 data 行
            if line.startswith("data:"):
                event = json.loads(line[5:])
                print(event)
                # 若收到服务端错误事件，抛出异常
                if event["type"] == "error":
                    raise RuntimeError(event["data"])
                # 记录是否收到智能体正常完成事件
                finished |= event["type"] == "agent_finish"
    # 验证在流结束关闭前，必须收到终态 agent_finish 事件
    assert finished, "连接关闭前未收到完成事件"


if __name__ == "__main__":
    main()

