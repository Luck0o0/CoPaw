# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
"""send_to_minio tool — upload file to MinIO, notify BladeX to deliver to WeCom (spec S2).

Reads channel meta (bot_code/chat_id/chat_type) from agent_context
(get_current_channel_meta). Env vars:
    WECOM_OUTBOUND_MINIO_ENDPOINT  (default 127.0.0.1:9000)
    WECOM_OUTBOUND_MINIO_ACCESS_KEY (default bladexadmin)
    WECOM_OUTBOUND_MINIO_SECRET_KEY (default bladexadmin)
    WECOM_OUTBOUND_MINIO_BUCKET     (default wecom-outbound)
    WECOM_OUTBOUND_BLADEX_URL       (default http://127.0.0.1:80)
    WECOM_OUTBOUND_TOKEN            (shared secret for header auth)
"""

import io
import os
import uuid
from datetime import date
from pathlib import Path

import httpx
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
from minio import Minio

from qwenpaw.app.agent_context import get_current_channel_meta


def _minio_client():
    """Build Minio client from env vars (lazy, single-use per call)."""
    endpoint = os.getenv("WECOM_OUTBOUND_MINIO_ENDPOINT", "127.0.0.1:9000")
    secure = False
    if endpoint.startswith(("http://", "https://")):
        secure = endpoint.startswith("https://")
        endpoint = endpoint.split("://", 1)[1]
    return Minio(
        endpoint,
        access_key=os.getenv("WECOM_OUTBOUND_MINIO_ACCESS_KEY", "bladexadmin"),
        secret_key=os.getenv("WECOM_OUTBOUND_MINIO_SECRET_KEY", "bladexadmin"),
        secure=secure,
    )


async def _post_bladex(payload: dict) -> httpx.Response:
    """POST to BladeX outbound endpoint with auth header."""
    base = os.getenv("WECOM_OUTBOUND_BLADEX_URL", "http://127.0.0.1:80")
    base = base.rstrip("/")  # avoid double-slash
    token = os.getenv("WECOM_OUTBOUND_TOKEN", "")
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await client.post(
            f"{base}/blade-ai/hiclaw/wecom/outbound/send",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-BladeX-Outbound-Token": token,
            },
        )


def _error(msg: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"error: {msg}")])


def _ok(object_key: str, media_id: str) -> ToolResponse:
    return ToolResponse(
        content=[TextBlock(type="text", text=f"ok: object_key={object_key} media_id={media_id}")]
    )


async def send_to_minio(file_path: str) -> ToolResponse:
    """Upload file to MinIO and notify BladeX to send to WeCom user.

    Precondition: current channel meta must contain bot_code/chat_id/chat_type
    (set by console.py from BladeX-transmitted metadata).

    Args:
        file_path (str): Path to the local file to upload.

    Returns:
        ToolResponse: Status message.
    """
    # 1. Validate channel meta
    meta = get_current_channel_meta()
    bot_code = meta.get("bot_code")
    chat_id = meta.get("chat_id")
    chat_type = meta.get("chat_type", 1)
    if not bot_code or not chat_id:
        return _error("missing channel meta: bot_code/chat_id (not a wecom session?)")

    # 2. Validate local file
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return _error(f"file not found: {file_path}")
    size = p.stat().st_size
    if size < 5 or size > 50 * 1024 * 1024:
        return _error(f"file size {size}B out of range [5B, 50MB]")

    # 3. Upload to MinIO
    bucket = os.getenv("WECOM_OUTBOUND_MINIO_BUCKET", "wecom-outbound")
    today = date.today().isoformat()
    object_key = f"{bot_code}/{chat_id}/{today}/{uuid.uuid4().hex}_{p.name}"
    try:
        client = _minio_client()
        with open(p, "rb") as f:
            data = f.read()
        client.put_object(
            bucket_name=bucket,
            object_name=object_key,
            data=io.BytesIO(data),
            length=size,
        )
    except Exception as e:
        return _error(f"minio upload failed: {e}")

    # 4. Notify BladeX
    payload = {
        "objectKey": object_key,
        "fileName": p.name,
        "chatId": chat_id,
        "chatType": chat_type,
        "botCode": bot_code,
    }
    try:
        resp = await _post_bladex(payload)
    except Exception as e:
        return _error(f"bladex post failed: {e}; file at {object_key}, retry idempotent")

    if resp.status_code != 200:
        return _error(f"bladex returned {resp.status_code}")

    body = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
    if not body.get("success", True):
        return _error(f"bladex error: {body.get('error', 'unknown')}")

    media_id = (body.get("data") or {}).get("media_id", "unknown")
    return _ok(object_key, media_id)
