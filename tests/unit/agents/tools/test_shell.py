# -*- coding: utf-8 -*-
"""Tests for shell tool request-context propagation."""
from __future__ import annotations

import json

from qwenpaw.agents.tools import shell


def test_build_cron_dispatch_context_env_from_channel_meta(monkeypatch):
    monkeypatch.setattr(
        "qwenpaw.app.agent_context.get_current_channel_meta",
        lambda: {
            "channel_id": "bladex",
            "bot_code": "blade",
            "chat_id": "LiuKang",
            "user_id": "blade:1123598821738675201",
            "session_id": "wecom:LiuKang",
        },
    )

    result = shell._build_cron_dispatch_context_env()

    assert result is not None
    data = json.loads(result)
    assert data == {
        "channel": "bladex",
        "target_user": "blade:1123598821738675201",
        "target_session": "wecom:LiuKang",
        "meta": {
            "channel_id": "bladex",
            "bot_code": "blade",
            "chat_id": "LiuKang",
            "user_id": "blade:1123598821738675201",
            "session_id": "wecom:LiuKang",
        },
    }


def test_build_cron_dispatch_context_env_maps_blade_bot_code(monkeypatch):
    monkeypatch.setattr(
        "qwenpaw.app.agent_context.get_current_channel_meta",
        lambda: {
            "bot_code": "blade",
            "chat_id": "LiuKang",
            "user_id": "blade:1123598821738675201",
            "session_id": "wecom:LiuKang",
        },
    )

    result = shell._build_cron_dispatch_context_env()

    assert result is not None
    assert json.loads(result)["channel"] == "bladex"
