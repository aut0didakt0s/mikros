"""Server configuration — HOST and PORT from env vars."""

import os

HOST = os.environ.get("FASTMCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("FASTMCP_PORT", "8000"))
