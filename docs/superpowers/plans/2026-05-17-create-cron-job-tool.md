# Create Cron Job Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured `create_cron_job` agent tool that creates worker-owned cron jobs without shelling out, while preserving existing CLI behavior.

**Architecture:** Extract shared pure helpers for channel dispatch normalization and cron payload construction, then add a small cron service for id generation and persistence. Wire the current workspace `CronManager` into agent execution with a paired ContextVar context manager, then expose a thin agent tool that builds a `CronJobSpec` and persists it through the shared service.

**Why not just call the HTTP API from the tool?** The minimal approach would be a single file that POSTs to `/cron/jobs`. That works and is lower-touch. This plan intentionally goes wider because the existing creation path is already split across three places that disagree — CLI builder, shell env shim, and CLI env consumer. Adding a fourth without consolidating would lock in the duplication. Two of the three are collapsed in this PR (CLI builder → spec_builder, shell dispatch → normalize.py); the env-shim path is explicitly deferred to a follow-up. The 18-file touch surface is the cost of deleting more duplication than we add.

**Why shell out is a problem:** Today the agent runs `qwenpaw cron create` through `execute_shell_command`. The LLM must produce a correctly-quoted shell command with ~15 flags; the frontend dispatch context is smuggled in via the `QWENPAW_CRON_DISPATCH_CONTEXT` env var; errors from the subprocess surface as unstructured text. Breakdowns are silent: a mis-quoted `--target-user` silently sends the reminder to the wrong person, and a missing env var drops dispatch entirely. The structured tool replaces this with typed parameters, ContextVar-based dispatch resolution, and predictable error paths.

**Creation-only is a deliberate v1 scope decision.** Management tools (list/pause/resume/delete) remain CLI-only in this phase. The agent will recommend CLI commands to users who need to manage existing jobs, just as it does today. Companion tools can follow if agent-side management demand materializes.

**Tech Stack:** Python 3.10+, Pydantic models in `src/qwenpaw/app/crons/models.py`, FastAPI router in `src/qwenpaw/app/crons/api.py`, AgentScope `ToolResponse`, pytest.

**Known scope limitations (v1):**
- **No agent-side management tools.** List/pause/resume/delete remain CLI-only. Agents recommend CLI commands to users for management, same as today.
- **No cron job authorization model.** Ownership is recorded (agent_id) but not enforced on lifecycle operations. This matches the existing CLI behavior (`qwenpaw cron delete` accepts any --agent-id). A formal auth model is future work.
- **No rate limiting.** The tool does not cap per-agent job counts or creation rates. An agent in a retry loop could create many jobs. Rate limiting is future work; for now the CronManager's storage backend is the only backstop.
- **CronManager ContextVar does not propagate across delegation.** If a top-level manager delegates cron creation to a worker running in a separate runner, `get_cron_manager_for_current_agent()` returns `None` because `active_cron_manager` is scoped to the calling runner's ContextVar. The skill instructs managers to delegate for worker-specific reminders; the recommended path works when the worker is invoked in-process within the same runner. Cross-runner cron creation is not supported in v1.
- **spec_builder and Pydantic have overlapping validation.** `build_cron_job_payload` validates input shape before the Pydantic `model_validate` step validates model integrity. This is deliberate: the builder catches user-facing errors early with readable messages for the LLM, while Pydantic is the backstop for schema integrity. The two layers must be kept in sync when model fields change; no automation for this exists yet.

---

## File Structure

- Create `src/qwenpaw/app/channels/normalize.py`: pure channel metadata to dispatch-target resolver.
- Create `tests/unit/app/channels/test_normalize.py`: tests for BladeX channel inference and incomplete metadata.
- Create `src/qwenpaw/app/crons/spec_builder.py`: pure cron payload builder and `CronSpecError`.
- Create `tests/unit/app/crons/test_spec_builder.py`: tests moved from CLI validation and payload shape.
- Modify `src/qwenpaw/cli/cron_cmd.py`: delegate inline payload construction to `spec_builder`; keep env shim.
- Modify `tests/unit/cli/test_cron_cmd.py`: keep CLI/env compatibility coverage. No new tests required unless the delegation wrappers change CLI observable behavior.
- Create `src/qwenpaw/app/crons/service.py`: shared id generation and persistence through an existing `CronManager`.
- Modify `src/qwenpaw/app/crons/api.py`: `POST /cron/jobs` delegates to service.
- Modify `src/qwenpaw/config/context.py`: add `current_cron_manager`, `get_current_cron_manager()`, and `active_cron_manager()`.
- Modify `src/qwenpaw/app/workspace/workspace.py`: pass `ws.cron_manager` into `AgentRunner`.
- Modify `src/qwenpaw/app/runner/runner.py`: accept `cron_manager` and wrap actual agent execution in `active_cron_manager`.
- Create `src/qwenpaw/agents/tools/cron.py`: new `create_cron_job` agent tool.
- Modify `src/qwenpaw/agents/tools/__init__.py`: export `create_cron_job`.
- Modify `src/qwenpaw/agents/react_agent.py`: register `create_cron_job` as a built-in hardcoded tool.
- Create `tests/unit/agents/tools/test_cron.py`: agent tool behavior tests.
- Modify `src/qwenpaw/agents/skills/cron-zh/SKILL.md` and `src/qwenpaw/agents/skills/cron-en/SKILL.md`: prefer the tool for creation.

## Task 1: Extract Channel Dispatch Normalization

**Files:**
- Create: `src/qwenpaw/app/channels/normalize.py`
- Create: `tests/unit/app/channels/test_normalize.py`
- Modify: `src/qwenpaw/agents/tools/shell.py`
- Test: `tests/unit/app/channels/test_normalize.py`
- Test: `tests/unit/agents/tools/test_shell.py`

- [ ] **Step 1: Write failing normalization tests**

Create `tests/unit/app/channels/test_normalize.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from qwenpaw.app.channels.normalize import (
    resolve_dispatch_from_channel_meta,
)


def test_resolve_dispatch_uses_explicit_channel_id():
    meta = {
        "channel_id": "bladex",
        "bot_code": "blade",
        "user_id": "blade:1123598821738675201",
        "session_id": "wecom:LiuKang",
        "chat_id": "LiuKang",
    }

    result = resolve_dispatch_from_channel_meta(meta)

    assert result == {
        "channel": "bladex",
        "target_user": "blade:1123598821738675201",
        "target_session": "wecom:LiuKang",
        "meta": meta,
    }


def test_resolve_dispatch_infers_bladex_from_blade_bot_code():
    meta = {
        "bot_code": "blade",
        "user_id": "blade:1123598821738675201",
        "session_id": "wecom:LiuKang",
    }

    result = resolve_dispatch_from_channel_meta(meta)

    assert result is not None
    assert result["channel"] == "bladex"
    assert result["target_user"] == "blade:1123598821738675201"
    assert result["target_session"] == "wecom:LiuKang"
    assert result["meta"] == meta


def test_resolve_dispatch_returns_none_when_target_missing():
    assert resolve_dispatch_from_channel_meta({"channel_id": "bladex"}) is None


def test_resolve_dispatch_returns_none_for_non_dict_input():
    assert resolve_dispatch_from_channel_meta(None) is None
    assert resolve_dispatch_from_channel_meta("bad") is None
```

- [ ] **Step 2: Run normalization tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/app/channels/test_normalize.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'qwenpaw.app.channels.normalize'`.

- [ ] **Step 3: Implement `normalize.py`**

Create `src/qwenpaw/app/channels/normalize.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def resolve_dispatch_from_channel_meta(
    meta: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve dispatch fields from frontend channel metadata.

    Returns a dict with channel, target_user, target_session, and meta when
    all required fields are present. Returns None for missing or invalid
    metadata so callers can decide whether to error or fall back.
    """
    if not isinstance(meta, Mapping):
        return None

    channel = _clean_string(meta.get("channel_id"))
    if channel is None and _clean_string(meta.get("bot_code")) == "blade":
        channel = "bladex"

    target_user = _clean_string(meta.get("user_id"))
    target_session = _clean_string(meta.get("session_id"))
    if not (channel and target_user and target_session):
        return None

    return {
        "channel": channel,
        "target_user": target_user,
        "target_session": target_session,
        "meta": dict(meta),
    }
```

- [ ] **Step 4: Refactor shell env builder to use the helper**

Modify `src/qwenpaw/agents/tools/shell.py`.

Replace the body of `_build_cron_dispatch_context_env()` with:

```python
def _build_cron_dispatch_context_env() -> Optional[str]:
    """Serialize frontend dispatch context for child qwenpaw cron commands."""
    from ...app.agent_context import get_current_channel_meta
    from ...app.channels.normalize import resolve_dispatch_from_channel_meta

    context = resolve_dispatch_from_channel_meta(get_current_channel_meta())
    if context is None:
        return None
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"))
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/app/channels/test_normalize.py \
  tests/unit/agents/tools/test_shell.py \
  -q
```

Expected: PASS. Existing pytest config warnings are acceptable.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/qwenpaw/app/channels/normalize.py \
  src/qwenpaw/agents/tools/shell.py \
  tests/unit/app/channels/test_normalize.py
git commit -m "refactor(cron): share dispatch context normalization"
```

## Task 2: Extract Cron Spec Builder

**Files:**
- Create: `src/qwenpaw/app/crons/spec_builder.py`
- Create: `tests/unit/app/crons/test_spec_builder.py`
- Modify: `src/qwenpaw/cli/cron_cmd.py`
- Modify: `tests/unit/cli/test_cron_cmd.py`

- [ ] **Step 1: Write failing spec builder tests**

Create `tests/unit/app/crons/test_spec_builder.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from qwenpaw.app.crons.spec_builder import (
    CronSpecError,
    build_cron_job_payload,
)


def _base_kwargs(**overrides):
    data = {
        "task_type": "text",
        "schedule_type": "scheduled",
        "name": "睡觉提醒",
        "cron": None,
        "run_at": "2026-05-15T23:27:00+08:00",
        "repeat_every_days": None,
        "repeat_end_type": None,
        "repeat_until": None,
        "repeat_count": None,
        "channel": "bladex",
        "target_user": "blade:1123598821738675201",
        "target_session": "wecom:LiuKang",
        "text": "该睡觉了",
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "mode": "final",
        "save_result_to_inbox": None,
        "share_session": True,
        "timeout_seconds": 120,
    }
    data.update(overrides)
    return data


def test_build_text_scheduled_payload():
    payload = build_cron_job_payload(**_base_kwargs())

    assert payload["id"] == ""
    assert payload["name"] == "睡觉提醒"
    assert payload["task_type"] == "text"
    assert payload["text"] == "该睡觉了"
    assert payload["schedule"] == {
        "type": "once",
        "run_at": "2026-05-15T23:27:00+08:00",
        "timezone": "Asia/Shanghai",
    }
    assert payload["dispatch"] == {
        "type": "channel",
        "channel": "bladex",
        "target": {
            "user_id": "blade:1123598821738675201",
            "session_id": "wecom:LiuKang",
        },
        "mode": "final",
        "meta": {},
    }
    assert payload["runtime"]["timeout_seconds"] == 120
    assert payload["runtime"]["share_session"] is True
    assert "save_result_to_inbox" not in payload


def test_build_agent_cron_payload():
    payload = build_cron_job_payload(
        **_base_kwargs(
            task_type="agent",
            schedule_type="cron",
            cron="0 9 * * *",
            run_at=None,
            text="总结今天的任务",
            timeout_seconds=600,
        )
    )

    assert payload["task_type"] == "agent"
    assert payload["schedule"] == {
        "type": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }
    assert payload["request"]["input"][0]["content"] == [
        {"type": "text", "text": "总结今天的任务"},
    ]
    assert payload["runtime"]["timeout_seconds"] == 600


def test_cron_schedule_requires_cron_expression():
    with pytest.raises(CronSpecError, match="--cron is required"):
        build_cron_job_payload(
            **_base_kwargs(schedule_type="cron", cron=None, run_at=None)
        )


def test_scheduled_schedule_requires_run_at():
    with pytest.raises(CronSpecError, match="--run-at is required"):
        build_cron_job_payload(**_base_kwargs(run_at=None))


def test_repeat_options_only_supported_for_scheduled():
    with pytest.raises(CronSpecError, match="--repeat-\\* options"):
        build_cron_job_payload(
            **_base_kwargs(
                schedule_type="cron",
                cron="0 9 * * *",
                run_at=None,
                repeat_every_days=1,
            )
        )


def test_unsupported_task_type_raises_cron_spec_error():
    with pytest.raises(CronSpecError, match="Unsupported task type"):
        build_cron_job_payload(**_base_kwargs(task_type="bad"))


def test_dispatch_meta_is_copied_into_payload():
    meta = {
        "channel_id": "bladex",
        "bot_code": "blade",
        "chat_id": "LiuKang",
    }

    payload = build_cron_job_payload(**_base_kwargs(dispatch_meta=meta))

    assert payload["dispatch"]["meta"] == meta
    # Caller must not be able to mutate payload through the original dict.
    meta["chat_id"] = "Mutated"
    assert payload["dispatch"]["meta"]["chat_id"] == "LiuKang"


def test_dispatch_meta_defaults_to_empty():
    payload = build_cron_job_payload(**_base_kwargs())

    assert payload["dispatch"]["meta"] == {}
```

- [ ] **Step 2: Run spec builder tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/app/crons/test_spec_builder.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'qwenpaw.app.crons.spec_builder'`.

- [ ] **Step 3: Implement `spec_builder.py`**

Create `src/qwenpaw/app/crons/spec_builder.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional


class CronSpecError(ValueError):
    """Raised when cron job construction arguments are invalid."""


def _validate_and_apply_scheduled_repeat(
    schedule: dict,
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
) -> None:
    if repeat_end_type and repeat_every_days is None:
        raise CronSpecError("--repeat-end-type requires --repeat-every-days")
    if repeat_until and (
        repeat_end_type != "until" or repeat_every_days is None
    ):
        raise CronSpecError(
            "--repeat-until requires --repeat-every-days and "
            "--repeat-end-type until",
        )
    if repeat_count is not None and (
        repeat_end_type != "count" or repeat_every_days is None
    ):
        raise CronSpecError(
            "--repeat-count requires --repeat-every-days and "
            "--repeat-end-type count",
        )
    if repeat_every_days is None:
        return

    schedule["repeat_every_days"] = repeat_every_days
    end_type = repeat_end_type or "never"
    schedule["repeat_end_type"] = end_type
    if end_type == "until":
        if not (repeat_until and repeat_until.strip()):
            raise CronSpecError(
                "--repeat-until is required when --repeat-end-type is 'until'",
            )
        schedule["repeat_until"] = repeat_until.strip()
    elif end_type == "count":
        if repeat_count is None:
            raise CronSpecError(
                "--repeat-count is required when --repeat-end-type is 'count'",
            )
        schedule["repeat_count"] = repeat_count


def build_cron_schedule_payload(
    *,
    schedule_type: str,
    cron: str | None,
    run_at: str | None,
    timezone: str,
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
) -> dict:
    if schedule_type == "scheduled":
        if not (run_at and run_at.strip()):
            raise CronSpecError(
                "--run-at is required when schedule type is 'scheduled'",
            )
        schedule = {
            "type": "once",
            "run_at": run_at.strip(),
            "timezone": timezone,
        }
        _validate_and_apply_scheduled_repeat(
            schedule=schedule,
            repeat_every_days=repeat_every_days,
            repeat_end_type=repeat_end_type,
            repeat_until=repeat_until,
            repeat_count=repeat_count,
        )
        return schedule

    if schedule_type != "cron":
        raise CronSpecError(f"Unsupported schedule type: {schedule_type}")
    if not (cron and cron.strip()):
        raise CronSpecError("--cron is required when schedule type is 'cron'")
    if (
        repeat_every_days is not None
        or repeat_end_type is not None
        or repeat_until is not None
        or repeat_count is not None
    ):
        raise CronSpecError(
            "--repeat-* options are only supported when "
            "--schedule-type is 'scheduled'",
        )
    return {"type": "cron", "cron": cron.strip(), "timezone": timezone}


def build_cron_job_payload(
    *,
    task_type: str,
    schedule_type: str,
    name: str,
    cron: str | None,
    run_at: str | None,
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
    channel: str,
    target_user: str,
    target_session: str,
    text: Optional[str],
    timezone: str,
    enabled: bool,
    mode: str,
    save_result_to_inbox: Optional[bool] = None,
    share_session: bool = True,
    timeout_seconds: int = 120,
    dispatch_meta: Optional[dict] = None,
) -> dict:
    """Build CronJobSpec JSON payload from structured arguments.

    ``dispatch_meta`` is copied into ``dispatch.meta``. Pass the frontend
    channel metadata here when known; CLI callers without channel context
    can leave it as ``None``.
    """
    if not (name and name.strip()):
        raise CronSpecError("--name is required")
    if not (channel and channel.strip()):
        raise CronSpecError("--channel is required")
    if not (target_user and target_user.strip()):
        raise CronSpecError("--target-user is required")
    if not (target_session and target_session.strip()):
        raise CronSpecError("--target-session is required")

    schedule = build_cron_schedule_payload(
        schedule_type=schedule_type,
        cron=cron,
        run_at=run_at,
        timezone=timezone,
        repeat_every_days=repeat_every_days,
        repeat_end_type=repeat_end_type,
        repeat_until=repeat_until,
        repeat_count=repeat_count,
    )
    dispatch = {
        "type": "channel",
        "channel": channel.strip(),
        "target": {
            "user_id": target_user.strip(),
            "session_id": target_session.strip(),
        },
        "mode": mode,
        "meta": dict(dispatch_meta) if dispatch_meta else {},
    }
    runtime = {
        "share_session": share_session,
        "max_concurrency": 1,
        "timeout_seconds": timeout_seconds,
        "misfire_grace_seconds": 60,
    }
    if task_type == "text":
        if not (text and text.strip()):
            raise CronSpecError("--text is required when task type is 'text'")
        payload = {
            "id": "",
            "name": name.strip(),
            "enabled": enabled,
            "schedule": schedule,
            "task_type": "text",
            "text": text.strip(),
            "dispatch": dispatch,
            "runtime": runtime,
            "meta": {},
        }
        if save_result_to_inbox is not None:
            payload["save_result_to_inbox"] = save_result_to_inbox
        return payload
    if task_type == "agent":
        if not (text and text.strip()):
            raise CronSpecError(
                "--text is required when task type is 'agent' "
                "(the question/prompt sent to the agent)",
            )
        payload = {
            "id": "",
            "name": name.strip(),
            "enabled": enabled,
            "schedule": schedule,
            "task_type": "agent",
            "request": {
                "input": [
                    {
                        "role": "user",
                        "type": "message",
                        "content": [{"type": "text", "text": text.strip()}],
                    },
                ],
            },
            "dispatch": dispatch,
            "runtime": runtime,
            "meta": {},
        }
        if save_result_to_inbox is not None:
            payload["save_result_to_inbox"] = save_result_to_inbox
        return payload
    raise CronSpecError(f"Unsupported task type: {task_type}")
```

- [ ] **Step 4: Update CLI imports**

Modify `src/qwenpaw/cli/cron_cmd.py`.

Add after existing imports:

```python
from ..app.crons.spec_builder import (
    CronSpecError,
    build_cron_job_payload,
    build_cron_schedule_payload,
)
```

- [ ] **Step 5: Replace CLI schedule/spec helpers with wrappers**

In `src/qwenpaw/cli/cron_cmd.py`, replace `_validate_and_apply_scheduled_repeat`, `_build_schedule_from_cli`, and `_build_spec_from_cli` with:

```python
def _build_schedule_from_cli(
    schedule_type: str,
    cron: str,
    run_at: Optional[str],
    timezone: str,
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
) -> dict:
    try:
        return build_cron_schedule_payload(
            schedule_type=schedule_type,
            cron=cron,
            run_at=run_at,
            timezone=timezone,
            repeat_every_days=repeat_every_days,
            repeat_end_type=repeat_end_type,
            repeat_until=repeat_until,
            repeat_count=repeat_count,
        )
    except CronSpecError as e:
        raise click.UsageError(str(e)) from e


def _build_spec_from_cli(
    task_type: str,
    schedule_type: str,
    name: str,
    cron: str,
    run_at: Optional[str],
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
    channel: str,
    target_user: str,
    target_session: str,
    text: Optional[str],
    timezone: str,
    enabled: bool,
    mode: str,
    save_result_to_inbox: Optional[bool] = None,
    share_session: bool = True,
    timeout_seconds: int = 120,
) -> dict:
    try:
        return build_cron_job_payload(
            task_type=task_type,
            schedule_type=schedule_type,
            name=name,
            cron=cron,
            run_at=run_at,
            repeat_every_days=repeat_every_days,
            repeat_end_type=repeat_end_type,
            repeat_until=repeat_until,
            repeat_count=repeat_count,
            channel=channel,
            target_user=target_user,
            target_session=target_session,
            text=text,
            timezone=timezone,
            enabled=enabled,
            mode=mode,
            save_result_to_inbox=save_result_to_inbox,
            share_session=share_session,
            timeout_seconds=timeout_seconds,
        )
    except CronSpecError as e:
        raise click.UsageError(str(e)) from e
```

- [ ] **Step 6: Run builder and CLI tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/app/crons/test_spec_builder.py \
  tests/unit/cli/test_cron_cmd.py \
  -q
```

Expected: PASS. Existing pytest config warnings are acceptable.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/qwenpaw/app/crons/spec_builder.py \
  src/qwenpaw/cli/cron_cmd.py \
  tests/unit/app/crons/test_spec_builder.py \
  tests/unit/cli/test_cron_cmd.py
git commit -m "refactor(cron): share job payload builder"
```

## Task 3: Add Shared Cron Creation Service

**Files:**
- Create: `src/qwenpaw/app/crons/service.py`
- Modify: `src/qwenpaw/app/crons/api.py`
- Create: `tests/unit/app/crons/test_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/unit/app/crons/test_service.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from qwenpaw.app.crons.models import CronJobSpec
from qwenpaw.app.crons.service import create_cron_job_via_manager


class FakeCronManager:
    def __init__(self) -> None:
        self.created = None

    async def create_or_replace_job(self, spec: CronJobSpec) -> None:
        self.created = spec


def _spec() -> CronJobSpec:
    return CronJobSpec.model_validate(
        {
            "id": "",
            "name": "睡觉提醒",
            "enabled": True,
            "schedule": {
                "type": "once",
                "run_at": "2026-05-15T23:27:00+08:00",
                "timezone": "Asia/Shanghai",
            },
            "task_type": "text",
            "text": "该睡觉了",
            "dispatch": {
                "type": "channel",
                "channel": "bladex",
                "target": {
                    "user_id": "blade:1123598821738675201",
                    "session_id": "wecom:LiuKang",
                },
                "mode": "final",
                "meta": {},
            },
            "runtime": {
                "share_session": True,
                "max_concurrency": 1,
                "timeout_seconds": 120,
                "misfire_grace_seconds": 60,
            },
            "meta": {},
        },
    )


async def test_create_cron_job_via_manager_generates_id_and_persists():
    manager = FakeCronManager()

    created = await create_cron_job_via_manager(manager, _spec())

    assert created.id
    assert created.name == "睡觉提醒"
    assert manager.created == created
```

- [ ] **Step 2: Run service test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/app/crons/test_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'qwenpaw.app.crons.service'`.

- [ ] **Step 3: Implement service**

Create `src/qwenpaw/app/crons/service.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from .models import CronJobSpec

if TYPE_CHECKING:
    from .manager import CronManager


async def create_cron_job_via_manager(
    manager: "CronManager",
    spec: CronJobSpec,
) -> CronJobSpec:
    """Create a cron job through an already-resolved CronManager."""
    job_id = str(uuid.uuid4())
    created = spec.model_copy(update={"id": job_id})
    await manager.create_or_replace_job(created)
    return created


async def get_cron_manager_for_current_agent() -> "CronManager":
    """Resolve CronManager for the active agent context."""
    from ...config.context import get_current_cron_manager

    manager = get_current_cron_manager()
    if manager is None:
        raise RuntimeError(
            "CronManager is not available in the current agent context.",
        )
    return manager
```

- [ ] **Step 4: Update API router creation endpoint**

Modify `src/qwenpaw/app/crons/api.py`.

Remove the `import uuid` line.

Add after model imports:

```python
from .service import create_cron_job_via_manager
```

Replace the `create_job()` body with:

```python
@router.post("/jobs", response_model=CronJobSpec)
async def create_job(
    spec: CronJobSpec,
    mgr: CronManager = Depends(get_cron_manager),
):
    return await create_cron_job_via_manager(mgr, spec)
```

- [ ] **Step 5: Run service tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/app/crons/test_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Run existing cron CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/cli/test_cron_cmd.py -q
```

Expected: PASS. Existing pytest config warnings are acceptable.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/qwenpaw/app/crons/service.py \
  src/qwenpaw/app/crons/api.py \
  tests/unit/app/crons/test_service.py
git commit -m "refactor(cron): share job creation service"
```

## Task 4: Wire CronManager Context Into Agent Execution

**Files:**
- Modify: `src/qwenpaw/config/context.py`
- Modify: `src/qwenpaw/app/workspace/workspace.py`
- Modify: `src/qwenpaw/app/runner/runner.py`
- Create: `tests/unit/config/test_context_cron_manager.py`

- [ ] **Step 1: Write failing ContextVar test**

Create `tests/unit/config/test_context_cron_manager.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect

from qwenpaw.config.context import (
    active_cron_manager,
    get_current_cron_manager,
)


class DummyCronManager:
    pass


def test_active_cron_manager_sets_and_resets():
    manager = DummyCronManager()

    assert get_current_cron_manager() is None
    with active_cron_manager(manager):
        assert get_current_cron_manager() is manager
    assert get_current_cron_manager() is None


def test_active_cron_manager_resets_on_exception():
    manager = DummyCronManager()

    try:
        with active_cron_manager(manager):
            assert get_current_cron_manager() is manager
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    # Exception path must still reset; otherwise stale state leaks.
    assert get_current_cron_manager() is None


def test_agent_runner_query_handler_wraps_execution_in_active_cron_manager():
    """Regression guard: AgentRunner.query_handler must wrap agent
    execution in ``active_cron_manager(self._cron_manager)``. Removing the
    wrapper would allow a stale CronManager to leak across requests served
    by the same long-lived runner instance.

    This test intentionally uses ``inspect.getsource`` + substring match
    rather than a behavioral test. A behavioral test would require a real
    or mock CronManager and a minimal runner execution path — higher setup
    cost but immune to reformatting. The source-inspection approach is
    chosen as a pragmatic tradeoff: it catches deletion of the wrapper
    with near-zero setup, at the cost of breaking on reformatting or
    variable renames. If the runner's execution block is restructured,
    update this assertion to match — do not delete it.
    """
    from qwenpaw.app.runner import runner as runner_module

    source = inspect.getsource(runner_module.AgentRunner.query_handler)
    assert "active_cron_manager(self._cron_manager)" in source, (
        "AgentRunner.query_handler no longer binds CronManager via "
        "active_cron_manager. See plan task 4 step 6."
    )
```

- [ ] **Step 2: Run ContextVar test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/config/test_context_cron_manager.py -q
```

Expected: FAIL with `ImportError` for `active_cron_manager` or `get_current_cron_manager`.

- [ ] **Step 3: Add ContextVar accessors**

Modify `src/qwenpaw/config/context.py`.

Add imports:

```python
from contextlib import contextmanager
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from qwenpaw.app.crons.manager import CronManager
```

Add after existing context variables:

```python
current_cron_manager: ContextVar["CronManager | None"] = ContextVar(
    "current_cron_manager",
    default=None,
)


def get_current_cron_manager() -> "CronManager | None":
    """Get the current agent's CronManager from context."""
    return current_cron_manager.get()


@contextmanager
def active_cron_manager(mgr: "CronManager | None") -> Iterator[None]:
    """Bind a CronManager for one agent execution and reset on exit."""
    token = current_cron_manager.set(mgr)
    try:
        yield
    finally:
        current_cron_manager.reset(token)
```

- [ ] **Step 4: Extend `AgentRunner.__init__`**

Modify `src/qwenpaw/app/runner/runner.py`.

In the `TYPE_CHECKING` block, add:

```python
from ..crons.manager import CronManager
```

Update `AgentRunner.__init__` to accept:

```python
cron_manager: "CronManager | None" = None,
```

Store it in the initializer:

```python
self._cron_manager = cron_manager
```

- [ ] **Step 5: Pass CronManager from Workspace via post_init**

Modify `src/qwenpaw/app/workspace/workspace.py`.

The `ws.cron_manager` property returns `self._service_manager.services.get("cron_manager")` (`workspace.py:110-112`). At runner init time (priority 10), the CronManager service (priority 40) has not yet been created, so `ws.cron_manager` is `None`. Use the **existing post_init pattern** (established at `workspace.py:182-186` for memory_manager and context_manager) on the CronManager's ServiceDescriptor instead of adding `cron_manager` to the runner's init_args:

```python
# On the cron_manager ServiceDescriptor (near workspace.py:289),
# add a post_init that sets the runner reference after CronManager is created:
sm.register(
    ServiceDescriptor(
        name="cron_manager",
        ...
        post_init=lambda ws, cm: setattr(
            ws._service_manager.services["runner"],
            "_cron_manager",
            cm,
        ),
    ),
)
```

Remove any `"cron_manager": ws.cron_manager` that was added to the runner's `init_args` in Step 4 — the runner only needs `self._cron_manager = None` from `__init__` default.

- [ ] **Step 6: Wrap agent execution with `active_cron_manager`**

Modify `src/qwenpaw/app/runner/runner.py`.

Add import near the mission execution block or at file top:

```python
from ...config.context import active_cron_manager
```

Wrap the mission and standard execution block with:

```python
with active_cron_manager(self._cron_manager):
    if mission_info is not None:
        from ...agents.mission.mission_runner import (
            run_mission_phase1,
            run_mission_phase2,
        )

        phase = mission_info["mission_phase"]
        loop_dir = Path(mission_info["loop_dir"])
        max_iters = mission_info.get("max_iterations", 20)

        if phase == 1:
            async for msg, last in run_mission_phase1(
                agent=agent,
                msgs=msgs,
                loop_dir=loop_dir,
                max_iterations=max_iters,
                agent_id=self.agent_id,
            ):
                yield msg, last
        else:
            async for msg, last in run_mission_phase2(
                agent=agent,
                msgs=msgs,
                loop_dir=loop_dir,
                max_iterations=max_iters,
                agent_id=self.agent_id,
            ):
                yield msg, last
    else:
        async for msg, last in _stream_printing_messages_interruptible(
            agents=[agent],
            coroutine_task=agent(msgs),
        ):
            yield msg, last
```

Keep the existing surrounding `try/except/finally` structure intact.

- [ ] **Step 7: Run ContextVar test**

Run:

```bash
.venv/bin/python -m pytest tests/unit/config/test_context_cron_manager.py -q
```

Expected: PASS.

- [ ] **Step 8: Run existing runner-adjacent tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/agents/tools/test_agent_management.py -q
```

Expected: PASS. Existing pytest config warnings are acceptable.

- [ ] **Step 9: Commit**

Run:

```bash
git add src/qwenpaw/config/context.py \
  src/qwenpaw/app/workspace/workspace.py \
  src/qwenpaw/app/runner/runner.py \
  tests/unit/config/test_context_cron_manager.py
git commit -m "feat(cron): bind CronManager to agent execution context"
```

## Task 5: Add `create_cron_job` Agent Tool

**Files:**
- Create: `src/qwenpaw/agents/tools/cron.py`
- Create: `tests/unit/agents/tools/test_cron.py`

- [ ] **Step 1: Write failing agent tool tests**

Create `tests/unit/agents/tools/test_cron.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from qwenpaw.agents.tools import cron as cron_tool
from qwenpaw.config.context import active_cron_manager


def _text_from_response(response) -> str:
    return response.content[0].get("text", "")


class FakeCronManager:
    def __init__(self) -> None:
        self.created = None

    async def create_or_replace_job(self, spec) -> None:
        self.created = spec


_FULL_META = {
    "channel_id": "bladex",
    "bot_code": "blade",
    "user_id": "blade:1123598821738675201",
    "session_id": "wecom:LiuKang",
}


def _patch_channel_meta(monkeypatch, meta: dict) -> None:
    monkeypatch.setattr(
        "qwenpaw.app.agent_context.get_current_channel_meta",
        lambda: meta,
    )


def _patch_calling_agent(monkeypatch, agent_id: str = "reminder") -> None:
    monkeypatch.setattr(
        "qwenpaw.agents.tools.agent_management.resolve_calling_agent_id",
        lambda from_agent=None: agent_id,
    )


async def test_create_cron_job_uses_channel_meta(monkeypatch):
    manager = FakeCronManager()
    _patch_channel_meta(monkeypatch, _FULL_META)
    _patch_calling_agent(monkeypatch)

    with active_cron_manager(manager):
        response = await cron_tool.create_cron_job(
            task_type="text",
            schedule_type="scheduled",
            name="睡觉提醒",
            text="该睡觉了",
            run_at="2026-05-15T23:27:00+08:00",
        )

    text = _text_from_response(response)
    assert "Created cron job" in text
    assert manager.created is not None
    assert manager.created.dispatch.channel == "bladex"
    assert manager.created.dispatch.target.user_id == (
        "blade:1123598821738675201"
    )
    assert manager.created.dispatch.target.session_id == "wecom:LiuKang"
    assert manager.created.dispatch.meta["bot_code"] == "blade"
    # Timezone is hardcoded to Beijing; verify it lands in the payload.
    assert manager.created.schedule.timezone == "Asia/Shanghai"


async def test_create_cron_job_errors_when_channel_meta_incomplete(monkeypatch):
    manager = FakeCronManager()
    _patch_channel_meta(monkeypatch, {"channel_id": "bladex"})
    _patch_calling_agent(monkeypatch)

    with active_cron_manager(manager):
        response = await cron_tool.create_cron_job(
            task_type="text",
            schedule_type="scheduled",
            name="睡觉提醒",
            text="该睡觉了",
            run_at="2026-05-15T23:27:00+08:00",
        )

    assert "Cannot resolve cron dispatch target" in _text_from_response(response)
    assert manager.created is None


async def test_create_cron_job_errors_when_cron_missing(monkeypatch):
    manager = FakeCronManager()
    _patch_channel_meta(monkeypatch, _FULL_META)
    _patch_calling_agent(monkeypatch)

    with active_cron_manager(manager):
        response = await cron_tool.create_cron_job(
            task_type="text",
            schedule_type="cron",
            name="每日提醒",
            text="早上好",
        )

    assert "--cron is required" in _text_from_response(response)
    assert manager.created is None


async def test_create_cron_job_errors_when_manager_unavailable(monkeypatch):
    # Note: no active_cron_manager() — default ContextVar value is None,
    # which is what the runner-less code path (e.g. `qwenpaw task run`)
    # actually produces in production.
    _patch_channel_meta(monkeypatch, _FULL_META)
    _patch_calling_agent(monkeypatch)

    response = await cron_tool.create_cron_job(
        task_type="text",
        schedule_type="scheduled",
        name="睡觉提醒",
        text="该睡觉了",
        run_at="2026-05-15T23:27:00+08:00",
    )

    assert "CronManager is not available" in _text_from_response(response)
```

- [ ] **Step 2: Run agent tool tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/agents/tools/test_cron.py -q
```

Expected: FAIL with `ImportError` for `qwenpaw.agents.tools.cron` or missing `create_cron_job`.

- [ ] **Step 3: Implement agent tool**

Create `src/qwenpaw/agents/tools/cron.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ...app.channels.normalize import resolve_dispatch_from_channel_meta
from ...app.crons.models import CronJobSpec
from ...app.crons.service import (
    create_cron_job_via_manager,
    get_cron_manager_for_current_agent,
)
from ...app.crons.spec_builder import CronSpecError, build_cron_job_payload

logger = logging.getLogger(__name__)


def _text_response(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _error_response(text: str) -> ToolResponse:
    return _text_response(f"ERROR: {text}")


# Reminders surface in WeCom which is China-only. Freezing timezone avoids
# the LLM proposing a different one and breaking dispatch semantics. This is
# a known WeCom coupling; if the tool is later used for non-WeCom channels,
# the timezone should become configurable (per-channel or per-agent config).
CRON_TOOL_TIMEZONE = "Asia/Shanghai"


async def create_cron_job(
    *,
    task_type: str,
    schedule_type: str,
    name: str,
    text: str,
    cron: str | None = None,
    run_at: str | None = None,
    timeout_seconds: int = 120,
) -> ToolResponse:
    """Create a cron job for the current agent and frontend dispatch target."""
    from ...app.agent_context import get_current_channel_meta
    from .agent_management import resolve_calling_agent_id

    dispatch = resolve_dispatch_from_channel_meta(get_current_channel_meta())
    if dispatch is None:
        return _error_response(
            "Cannot resolve cron dispatch target from current channel metadata. "
            "Ask the user to retry from a supported frontend channel.",
        )

    try:
        payload = build_cron_job_payload(
            task_type=task_type,
            schedule_type=schedule_type,
            name=name,
            cron=cron,
            run_at=run_at,
            repeat_every_days=None,
            repeat_end_type=None,
            repeat_until=None,
            repeat_count=None,
            channel=dispatch["channel"],
            target_user=dispatch["target_user"],
            target_session=dispatch["target_session"],
            text=text,
            timezone=CRON_TOOL_TIMEZONE,
            enabled=True,
            mode="final",
            save_result_to_inbox=None,
            share_session=True,
            timeout_seconds=timeout_seconds,
            dispatch_meta=dict(dispatch["meta"]),
        )
    except CronSpecError as e:
        return _error_response(str(e))

    try:
        spec = CronJobSpec.model_validate(payload)
    except Exception as e:
        return _error_response(f"Invalid cron job specification: {e}")

    try:
        manager = await get_cron_manager_for_current_agent()
        created = await create_cron_job_via_manager(manager, spec)
    except Exception as e:
        logger.warning("create_cron_job failed", exc_info=True)
        return _error_response(str(e))

    owner = resolve_calling_agent_id(None) or "unknown"
    return _text_response(
        "Created cron job "
        f"{created.id} for agent {owner}. "
        f"Dispatch channel={created.dispatch.channel}, "
        f"user={created.dispatch.target.user_id}, "
        f"session={created.dispatch.target.session_id}.",
    )
```

- [ ] **Step 4: Run agent tool tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/agents/tools/test_cron.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/qwenpaw/agents/tools/cron.py \
  tests/unit/agents/tools/test_cron.py
git commit -m "feat(agents): add create cron job tool"
```

## Task 6: Register Tool And Update Skills

**Files:**
- Modify: `src/qwenpaw/agents/tools/__init__.py`
- Modify: `src/qwenpaw/agents/react_agent.py`
- Modify: `src/qwenpaw/agents/skills/cron-zh/SKILL.md`
- Modify: `src/qwenpaw/agents/skills/cron-en/SKILL.md`
- Modify: `tests/unit/agents/tools/test_cron.py`

- [ ] **Step 1: Add registration assertions**

Append to `tests/unit/agents/tools/test_cron.py`:

```python
def test_create_cron_job_is_exported():
    from qwenpaw.agents import tools

    assert "create_cron_job" in tools.__all__
    assert callable(tools.create_cron_job)
```

- [ ] **Step 2: Run export test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/agents/tools/test_cron.py::test_create_cron_job_is_exported \
  -q
```

Expected: FAIL because `create_cron_job` is not exported yet.

- [ ] **Step 3: Export tool**

Modify `src/qwenpaw/agents/tools/__init__.py`.

Add import:

```python
from .cron import create_cron_job
```

Add to `__all__`:

```python
"create_cron_job",
```

- [ ] **Step 4: Register as hardcoded built-in tool**

Modify `src/qwenpaw/agents/react_agent.py`.

In the `from .tools import (...)` block (`react_agent.py:40-44` as of writing), add `create_cron_job` to the imported names, preserving alphabetical order where the existing list does so:

```python
from .tools import (
    browser_use,
    ...
    chat_with_agent,
    check_agent_task,
    create_cron_job,   # NEW
    ...
)
```

In the `tool_functions` dict inside `_create_toolkit` (`react_agent.py:263-283` as of writing), add a new entry near the other agent-management entries:

```python
"create_cron_job": create_cron_job,
```

The dict is part of the hardcoded-builtin path; adding it there makes the tool default-enabled without needing per-agent plugin configuration. Plugin auto-discovery at `react_agent.py:289-302` is unaffected because the name will already be present in `tool_functions` before the discovery loop runs.

- [ ] **Step 5: Update Chinese cron skill**

Modify `src/qwenpaw/agents/skills/cron-zh/SKILL.md`.

Do **not** change the `description:` frontmatter field — the existing words `"使用 qwenpaw cron list/create/get/state/pause/resume/delete/run 管理任务"` serve as the skill's triggering keywords and must stay.

In the "硬规则" or equivalent section, add:

```markdown
### 创建任务使用 `create_cron_job` 工具

当可用工具里存在 `create_cron_job` 时，创建 cron job 必须使用该工具。不要通过 `execute_shell_command` 执行 `qwenpaw cron create`。

`create_cron_job` 会从当前前端 channel context 自动解析投递目标。不要手写或猜测企微用户 ID、session ID。

如果你是 top-level manager，而用户要创建 worker-specific reminder，先用 `submit_to_agent` / `chat_with_agent` 委派给对应 worker，再让 worker 调用 `create_cron_job`。

### 何时仍用 CLI 管理任务

以下 CLI 命令仅限人力运维使用（终端、CI、故障恢复），agent **不得**通过 `execute_shell_command` 调用：

- `qwenpaw cron list/get/state` — 查看任务状态
- `qwenpaw cron pause/resume` — 暂停/恢复执行
- `qwenpaw cron delete` — 永久删除任务
- `qwenpaw cron run` — 手动触发执行

人力运维仍可使用 `qwenpaw cron create` 在终端/脚本中创建任务，agent **不得**通过 shell 调用。
```

Keep existing CLI management examples for list/get/state/pause/resume/delete/run unchanged.

- [ ] **Step 6: Update English cron skill**

Modify `src/qwenpaw/agents/skills/cron-en/SKILL.md`.

Do **not** change the `description:` frontmatter field — the existing words `"Manage jobs with qwenpaw cron list/create/get/state/pause/resume/delete/run"` serve as the skill's triggering keywords and must stay.

Add a creation rule section:

```markdown
### Use `create_cron_job` for creation

When the `create_cron_job` tool is available, use it to create cron jobs. Do not call `execute_shell_command` just to run `qwenpaw cron create`.

`create_cron_job` resolves the dispatch target from the current frontend channel context. Do not hand-write or guess WeCom user IDs or session IDs.

If you are the top-level manager and the user asks for a worker-specific reminder, delegate to the worker with `submit_to_agent` / `chat_with_agent` first, then let the worker call `create_cron_job`.

### When CLI management is still appropriate

These CLI commands are for human operators (terminal, CI, recovery). Agents must **not** invoke them via `execute_shell_command`:

- `qwenpaw cron list/get/state` — inspect job state
- `qwenpaw cron pause/resume` — toggle execution
- `qwenpaw cron delete` — permanently remove a job
- `qwenpaw cron run` — trigger a one-off run

Human operators may still use `qwenpaw cron create` in terminal/scripts. Agents must not shell out for creation.
```

Keep existing CLI management examples for list/get/state/pause/resume/delete/run unchanged.

- [ ] **Step 7: Run export and cron tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/agents/tools/test_cron.py \
  tests/unit/agents/tools/test_agent_management.py \
  -q
```

Expected: PASS. Existing pytest config warnings are acceptable.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/qwenpaw/agents/tools/__init__.py \
  src/qwenpaw/agents/react_agent.py \
  src/qwenpaw/agents/skills/cron-zh/SKILL.md \
  src/qwenpaw/agents/skills/cron-en/SKILL.md \
  tests/unit/agents/tools/test_cron.py
git commit -m "feat(cron): register create cron job tool"
```

## Task 7: Focused Verification

**Files:**
- Verify only; no planned source edits.

- [ ] **Step 1: Run the focused test suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/app/channels/test_normalize.py \
  tests/unit/app/crons/test_spec_builder.py \
  tests/unit/app/crons/test_service.py \
  tests/unit/agents/tools/test_cron.py \
  tests/unit/agents/tools/test_shell.py \
  tests/unit/cli/test_cron_cmd.py \
  tests/unit/agents/tools/test_agent_management.py \
  tests/unit/config/test_context_cron_manager.py \
  -q
```

Expected: PASS. Existing pytest config warnings about asyncio options are acceptable.

- [ ] **Step 2: Check no conflict markers or accidental env shim deletion**

Run:

```bash
rg -n "^(<<<<<<<|=======|>>>>>>>)" src tests docs
```

Expected: no output.

Run:

```bash
rg -n "QWENPAW_CRON_DISPATCH_CONTEXT|_apply_forwarded_dispatch_context|_build_cron_dispatch_context_env" src/qwenpaw
```

Expected: still finds `QWENPAW_CRON_DISPATCH_CONTEXT`, `_apply_forwarded_dispatch_context`, and `_build_cron_dispatch_context_env`; this PR must not remove the env shim.

- [ ] **Step 3: Inspect git diff summary**

Run:

```bash
git diff --stat
```

Expected: only files from this plan plus pre-existing unrelated working-tree changes are shown. Do not revert unrelated changes.

- [ ] **Step 4: Handle verification-only fixes**

If verification reveals a failure, return to the task that owns the failed behavior, apply the smallest fix there, rerun that task's tests, and commit using that task's commit step. If verification passes without changes, do not create an empty commit.
