import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uvicorn, webui
uvicorn.run(webui.app, host="127.0.0.1", port=8911, log_level="warning")
