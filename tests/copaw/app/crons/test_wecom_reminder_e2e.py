# -*- coding: utf-8 -*-
"""E2E tests for WeCom reminder feature.

Note: The "parsing" of user input like "3分钟后提醒我喝水" or "明天8点"
is done by the LLM when using the skill, NOT by Python parsing functions.
These tests verify the model construction and integration between models.
"""
import pytest
from datetime import datetime, timezone, timedelta


def test_schedule_spec_at_type_with_absolute_time():
    """Test ScheduleSpec with absolute time (e.g., tomorrow 8am as run_at)."""
    from src.copaw.app.crons.models import ScheduleSpec

    # Simulate LLM parsing "明天8点" -> run_at = tomorrow 8am UTC
    # 2026-04-02 08:00:00 UTC
    run_at = datetime(2026, 4, 2, 8, 0, 0, tzinfo=timezone.utc)
    spec = ScheduleSpec(type="at", run_at=run_at)

    assert spec.type == "at"
    assert spec.run_at == run_at
    assert spec.run_at.hour == 8


def test_schedule_spec_interval_type():
    """Test ScheduleSpec with interval type (e.g., '1小时后')."""
    from src.copaw.app.crons.models import ScheduleSpec

    spec = ScheduleSpec(type="interval", interval_seconds=3600)
    assert spec.type == "interval"
    assert spec.interval_seconds == 3600


def test_reminder_job_model_construction():
    """Test constructing a reminder CronJobSpec with at schedule."""
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
        text="该喝水了！",
        dispatch=dispatch
    )

    assert spec.name == "喝水提醒"
    assert spec.task_type == "text"
    assert spec.text == "该喝水了！"
    assert spec.schedule.type == "at"
    assert spec.dispatch.channel == "wecom"


def test_interval_job_model_construction():
    """Test constructing an interval-based recurring job."""
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
        text="该喝水了！",
        dispatch=dispatch
    )

    assert spec.schedule.type == "interval"
    assert spec.schedule.interval_seconds == 3600


def test_cron_job_model_construction():
    """Test constructing a cron-based recurring job."""
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


def test_schedule_spec_with_relative_interval():
    """Test ScheduleSpec for relative time (e.g., '3分钟后' -> run_at)."""
    from src.copaw.app.crons.models import ScheduleSpec

    now = datetime.now(timezone.utc)
    # LLM would parse "3分钟后" -> run_at = now + 3 minutes
    delta = timedelta(minutes=3)
    run_at = now + delta

    spec = ScheduleSpec(type="at", run_at=run_at)
    assert spec.type == "at"
    # Verify it's approximately 3 minutes from "now"
    diff = (spec.run_at - now).total_seconds()
    assert 179 <= diff <= 181  # 3 minutes +/- 1 second tolerance
