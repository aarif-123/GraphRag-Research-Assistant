import os
import sys

import uvicorn

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
os.environ["PYTHONUNBUFFERED"] = "1"

print("Starting server...", flush=True)

uvicorn.run(
    "app:app",
    host="0.0.0.0",
    port=8000,
    log_level="info",
    reload=True,
    workers=1,
)
