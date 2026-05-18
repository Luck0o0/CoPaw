# -*- coding: utf-8 -*-
"""Tests for cron CLI payload construction."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from qwenpaw.cli.cron_cmd import cron_group


def test_cron_create_uses_forwarded_dispatch_context(monkeypatch):
    """Worker-created cron jobs should dispatch to the frontend user."""
    dispatch_context = {
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
    monkeypatch.setenv(
        "QWENPAW_CRON_DISPATCH_CONTEXT",
        json.dumps(dispatch_context),
    )

    with patch("qwenpaw.cli.cron_cmd.client") as mock_client:
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "job-1"}
        mock_response.raise_for_status = MagicMock()
        http_client = mock_client.return_value.__enter__.return_value
        http_client.post.return_value = mock_response

        result = CliRunner().invoke(
            cron_group,
            [
                "create",
                "--type",
                "text",
                "--schedule-type",
                "scheduled",
                "--run-at",
                "2026-05-15T23:27:00+08:00",
                "--name",
                "睡觉提醒",
                "--agent-id",
                "reminder",
                "--channel",
                "wecom",
                "--target-user",
                "LiuKang",
                "--target-session",
                "LiuKang",
                "--text",
                "该睡觉了",
            ],
        )

    assert result.exit_code == 0
    payload = http_client.post.call_args.kwargs["json"]
    assert payload["dispatch"]["channel"] == "bladex"
    assert payload["dispatch"]["target"] == {
        "user_id": "blade:1123598821738675201",
        "session_id": "wecom:LiuKang",
    }
    assert payload["dispatch"]["meta"] == dispatch_context["meta"]
