import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uvicorn, webui
if __name__ == "__main__":
    uvicorn.run(webui.app, host="127.0.0.1", port=8905, log_level="warning")
