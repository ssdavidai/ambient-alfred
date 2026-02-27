#!/usr/bin/env python3
"""Entry point for the Ambient Alfred audio receiver."""

import uvicorn

from receiver.config import RECEIVER_HOST, RECEIVER_PORT

if __name__ == "__main__":
    uvicorn.run(
        "receiver.server:app",
        host=RECEIVER_HOST,
        port=RECEIVER_PORT,
        log_level="info",
    )
