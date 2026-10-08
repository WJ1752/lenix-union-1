"""只起 webui 的 HTTP 面（不启动 bot runtime），用于本地验收面板/卡片的渲染端点"""
import os, sys, uvicorn
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import webui
uvicorn.run(webui.app, host="127.0.0.1", port=8911, log_level="warning")
