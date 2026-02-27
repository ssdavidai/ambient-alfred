"""Notification dispatch via OpenClaw Gateway."""

import logging

import httpx

from pipeline.config import (
    GATEWAY_TOKEN,
    GATEWAY_URL,
    NOTIFICATION_CHANNEL,
    NOTIFICATION_CHANNEL_TYPE,
)

logger = logging.getLogger(__name__)


def notify(message: str, channel: str | None = None, channel_type: str | None = None) -> bool:
    """Send a notification via OpenClaw Gateway.

    Returns True on success, False on failure.
    """
    target = channel or NOTIFICATION_CHANNEL
    ch_type = channel_type or NOTIFICATION_CHANNEL_TYPE

    if not target:
        logger.debug("No notification channel configured, skipping")
        return False

    if not GATEWAY_URL or not GATEWAY_TOKEN:
        logger.debug("Gateway not configured, skipping notification")
        return False

    try:
        httpx.post(
            f"{GATEWAY_URL}/tools/invoke",
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "tool": "message",
                "args": {
                    "action": "send",
                    "channel": ch_type,
                    "target": target,
                    "message": message,
                },
            },
            timeout=15,
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to send notification: {e}")
        return False


def spawn_subagent(task: str, agent_id: str, model: str | None = None) -> str:
    """Spawn an OpenClaw subagent via Gateway to handle a command.

    Returns "dispatched" on success, error string on failure.
    """
    if not GATEWAY_URL or not GATEWAY_TOKEN:
        return "error: Gateway not configured"

    args = {
        "agentId": agent_id,
        "mode": "run",
        "task": task,
        "thread": False,
        "cleanup": "delete",
        "runTimeoutSeconds": 120,
    }
    if model:
        args["model"] = model

    try:
        resp = httpx.post(
            f"{GATEWAY_URL}/tools/invoke",
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"tool": "sessions_spawn", "args": args},
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            return "dispatched"
        return f"failed: {data.get('error', 'unknown')}"
    except Exception as e:
        return f"error: {e}"
