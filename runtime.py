"""AgentCore Runtime entry point. Launches the same FastAPI app on 8080."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pilot.api import app          # noqa: E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
