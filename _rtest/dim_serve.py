"""本地验证用：只起 webui 的 HTTP 服务（不启 QQ bot、不起 TLS 口），端口 8907 避免撞用户的 8900"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn  # noqa: E402
import webui  # noqa: E402

uvicorn.run(webui.app, host="127.0.0.1", port=8907, log_level="warning")
