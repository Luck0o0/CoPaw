# -*- coding: utf-8 -*-
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

def test_cron_manager_build_trigger_at():
    """_build_trigger should return DateTrigger for at type."""
    from copaw.app.crons.manager import CronManager
    from copaw.app.crons.models import CronJobSpec, ScheduleSpec, DispatchSpec, DispatchTarget, CronJobRequest

    # Create minimal spec for at trigger
    run_at = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    schedule = ScheduleSpec(type="at", run_at=run_at)
    dispatch = DispatchSpec(
        channel="wecom",
        target=DispatchTarget(user_id="test", session_id="test")
    )
    request = CronJobRequest(input="test input")
    spec = CronJobSpec(
        id="test-job",
        name="Test Job",
        schedule=schedule,
        dispatch=dispatch,
        request=request
    )

    # Mock repo and runner
    mock_repo = MagicMock()
    mock_runner = MagicMock()
    mock_channel_manager = MagicMock()
    mgr = CronManager(repo=mock_repo, runner=mock_runner, channel_manager=mock_channel_manager)

    trigger = mgr._build_trigger(spec)
    assert isinstance(trigger, DateTrigger)

def test_cron_manager_build_trigger_interval():
    """_build_trigger should return IntervalTrigger for interval type."""
    from copaw.app.crons.manager import CronManager
    from copaw.app.crons.models import CronJobSpec, ScheduleSpec, DispatchSpec, DispatchTarget, CronJobRequest

    schedule = ScheduleSpec(type="interval", interval_seconds=3600)
    dispatch = DispatchSpec(
        channel="wecom",
        target=DispatchTarget(user_id="test", session_id="test")
    )
    request = CronJobRequest(input="test input")
    spec = CronJobSpec(
        id="test-job",
        name="Test Job",
        schedule=schedule,
        dispatch=dispatch,
        request=request
    )

    mock_repo = MagicMock()
    mock_runner = MagicMock()
    mock_channel_manager = MagicMock()
    mgr = CronManager(repo=mock_repo, runner=mock_runner, channel_manager=mock_channel_manager)

    trigger = mgr._build_trigger(spec)
    assert isinstance(trigger, IntervalTrigger)

def test_cron_manager_build_trigger_cron():
    """_build_trigger should return CronTrigger for cron type."""
    from copaw.app.crons.manager import CronManager
    from copaw.app.crons.models import CronJobSpec, ScheduleSpec, DispatchSpec, DispatchTarget, CronJobRequest

    schedule = ScheduleSpec(type="cron", cron="0 9 * * *", timezone="UTC")
    dispatch = DispatchSpec(
        channel="wecom",
        target=DispatchTarget(user_id="test", session_id="test")
    )
    request = CronJobRequest(input="test input")
    spec = CronJobSpec(
        id="test-job",
        name="Test Job",
        schedule=schedule,
        dispatch=dispatch,
        request=request
    )

    mock_repo = MagicMock()
    mock_runner = MagicMock()
    mock_channel_manager = MagicMock()
    mgr = CronManager(repo=mock_repo, runner=mock_runner, channel_manager=mock_channel_manager)

    trigger = mgr._build_trigger(spec)
    assert isinstance(trigger, CronTrigger)