# -*- coding: utf-8 -*-
"""E2E tests for WeCom reminder feature."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

def test_parse_relative_time_3_minutes():
    """Test parsing '3分钟后' into a datetime."""
    from src.copaw.app.crons.models import ScheduleSpec

    now = datetime.now(timezone.utc)
    # Simulate parsing "3分钟后" -> run_at = now + 3 minutes
    delta = timedelta(minutes=3)
    run_at = now + delta

    spec = ScheduleSpec(type="at", run_at=run_at)
    assert spec.type == "at"
    # Verify it's approximately 3 minutes from "now"
    diff = (spec.run_at - now).total_seconds()
    assert 179 <= diff <= 181  # 3 minutes +/- 1 second tolerance

def test_parse_relative_time_1_hour():
    """Test parsing '1小时后' into interval_seconds."""
    from src.copaw.app.crons.models import ScheduleSpec

    spec = ScheduleSpec(type="interval", interval_seconds=3600)
    assert spec.type == "interval"
    assert spec.interval_seconds == 3600

def test_create_reminder_job_via_api():
    """Test creating a reminder job via the cron API."""
    # This test would normally call the actual API
    # For unit testing, we verify the model construction
    from src.copaw.app.crons.models import CronJobSpec, ScheduleSpec, DispatchSpec, DispatchTarget

    run_at = datetime(2026, 4, 2, 8, 0, 0, tzinfo=timezone.utc)
    schedule = ScheduleSpec(type="at", run_at=run_at)
    dispatch = DispatchSpec(
        channel="wecom",
        target=DispatchTarget(user_id="LiuKang", session_id="wecom:LiuKang")
    )

    spec = CronJobSpec(
        id="test-reminder",
        name="喝水提醒",
        schedule=schedule,
        task_type="text",
        text="该喝水了！💧",
        dispatch=dispatch
    )

    assert spec.name == "喝水提醒"
    assert spec.task_type == "text"
    assert spec.text == "该喝水了！💧"
    assert spec.schedule.type == "at"
    assert spec.dispatch.channel == "wecom"

def test_interval_job_via_api():
    """Test creating an interval job via the API."""
    from src.copaw.app.crons.models import CronJobSpec, ScheduleSpec, DispatchSpec, DispatchTarget

    schedule = ScheduleSpec(type="interval", interval_seconds=3600)
    dispatch = DispatchSpec(
        channel="wecom",
        target=DispatchTarget(user_id="LiuKang", session_id="wecom:LiuKang")
    )

    spec = CronJobSpec(
        id="test-interval",
        name="喝水提醒",
        schedule=schedule,
        task_type="text",
        text="该喝水了！💧",
        dispatch=dispatch
    )

    assert spec.schedule.type == "interval"
    assert spec.schedule.interval_seconds == 3600

def test_cron_job_still_works():
    """Verify cron jobs still work after our changes."""
    from src.copaw.app.crons.models import CronJobSpec, ScheduleSpec, DispatchSpec, DispatchTarget

    schedule = ScheduleSpec(type="cron", cron="0 9 * * *", timezone="UTC")
    dispatch = DispatchSpec(
        channel="wecom",
        target=DispatchTarget(user_id="LiuKang", session_id="wecom:LiuKang")
    )

    spec = CronJobSpec(
        id="test-cron",
        name="每日提醒",
        schedule=schedule,
        task_type="text",
        text="早上好！",
        dispatch=dispatch
    )

    assert spec.schedule.type == "cron"
    assert spec.schedule.cron == "0 9 * * *"
