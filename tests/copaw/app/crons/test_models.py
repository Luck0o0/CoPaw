# -*- coding: utf-8 -*-
import pytest
from datetime import datetime, timezone
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

def test_schedule_spec_cron_type():
    """cron type should work as before."""
    from copaw.app.crons.models import ScheduleSpec
    spec = ScheduleSpec(type="cron", cron="0 9 * * *", timezone="UTC")
    assert spec.type == "cron"
    assert spec.cron == "0 9 * * *"

def test_schedule_spec_at_type():
    """at type should store run_at datetime."""
    from copaw.app.crons.models import ScheduleSpec
    run_at = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    spec = ScheduleSpec(type="at", run_at=run_at)
    assert spec.type == "at"
    assert spec.run_at == run_at

def test_schedule_spec_interval_type():
    """interval type should store interval in seconds."""
    from copaw.app.crons.models import ScheduleSpec
    spec = ScheduleSpec(type="interval", interval_seconds=3600)
    assert spec.type == "interval"
    assert spec.interval_seconds == 3600