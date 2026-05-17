# Create Cron Job Agent Tool Design

## Summary

Add a dedicated `create_cron_job` agent tool so manager and worker agents can create scheduled jobs through structured Python code instead of shelling out to `qwenpaw cron create`.

The immediate goal is to let a worker agent create a cron job that still dispatches reminders back to the original frontend WeCom user. The job should remain in the worker workspace by default, because the worker owns the scheduled task execution context; only the dispatch target should point back to the frontend user/session.

## Background

Today, cron creation is primarily driven by the cron skill, which instructs agents to call:

```bash
qwenpaw cron create --agent-id <agent_id> ...
```

The agent reaches that command through `execute_shell_command`. The CLI then builds a payload and posts it to `/cron/jobs`.

That path works, but it has several weaknesses for worker-created reminders:

- The LLM must produce a correct shell command with correct quoting and required flags.
- The worker creates the job under its own `--agent-id`, while the user-facing dispatch target can accidentally point to the worker-local session instead of the original frontend WeCom user.
- Passing frontend channel context through a shell child process requires environment-variable plumbing.
- The path is longer than necessary: agent tool -> shell -> CLI parser -> HTTP API.

## Goals

- Provide a first-class `create_cron_job` tool available to agents.
- Prefer structured parameters over shell command generation.
- Automatically use the current frontend channel context when creating dispatch targets.
- Keep cron jobs in the current worker workspace by default.
- Preserve the existing `qwenpaw cron` CLI for humans, tests, and troubleshooting.
- Keep this change focused on creation only; management commands remain CLI/API-only for now.

## Non-Goals

- Do not add `list/get/state/pause/resume/delete/run` agent tools in this phase.
- Do not redesign the cron API, scheduler, repository, or executor.
- Do not migrate existing jobs between workspaces.
- Do not change the frontend cron UI.
- Do not remove the CLI until the new tool has been verified in real worker flows.

## Recommended Approach

Create a new agent tool module:

```text
src/qwenpaw/agents/tools/cron.py
```

The module exposes one tool:

```python
async def create_cron_job(
    task_type: str,
    schedule_type: str,
    name: str,
    text: str,
    cron: str | None = None,
    run_at: str | None = None,
    repeat_every_days: int | None = None,
    repeat_end_type: str | None = None,
    repeat_until: str | None = None,
    repeat_count: int | None = None,
    channel: str | None = None,
    target_user: str | None = None,
    target_session: str | None = None,
    agent_id: str | None = None,
    timezone: str | None = None,
    timeout_seconds: int = 120,
    save_result_to_inbox: bool | None = None,
    share_session: bool = True,
) -> ToolResponse:
    ...
```

The tool builds a `CronJobSpec` using the same validation and defaults as the CLI, then creates the job through the existing `/cron/jobs` HTTP API. Using the HTTP API keeps behavior aligned with the CLI and avoids reaching directly into workspace internals from agent tool code.

## Shared Spec Builder

Move the CLI payload-building logic out of `src/qwenpaw/cli/cron_cmd.py` into a shared module:

```text
src/qwenpaw/app/crons/spec_builder.py
```

The shared module should expose a function that both CLI and tool can use, for example:

```python
def build_cron_job_payload(
    *,
    task_type: str,
    schedule_type: str,
    name: str,
    cron: str | None,
    run_at: str | None,
    repeat_every_days: int | None,
    repeat_end_type: str | None,
    repeat_until: str | None,
    repeat_count: int | None,
    channel: str,
    target_user: str,
    target_session: str,
    text: str | None,
    timezone: str,
    enabled: bool,
    mode: str,
    save_result_to_inbox: bool | None,
    share_session: bool,
    timeout_seconds: int,
) -> dict:
    ...
```

This keeps CLI and agent tool behavior from drifting. The existing CLI helper can either delegate to the new builder or be replaced by it.

## Context Resolution

The tool should read the current frontend context with:

```python
get_current_channel_meta()
```

Dispatch target resolution should use this priority:

1. Explicit tool arguments: `channel`, `target_user`, `target_session`.
2. Current channel metadata: `channel_id`, `user_id`, `session_id`.
3. Error response if the dispatch target is still incomplete.

When channel metadata indicates the BladeX WeCom bridge, normalize the channel the same way as the current compatibility fix:

```text
bot_code == "blade" -> channel "bladex"
```

The resolved dispatch should include a copy of the channel metadata:

```json
{
  "dispatch": {
    "channel": "bladex",
    "target": {
      "user_id": "blade:1123598821738675201",
      "session_id": "wecom:LiuKang"
    },
    "meta": {
      "channel_id": "bladex",
      "bot_code": "blade",
      "chat_id": "LiuKang",
      "user_id": "blade:1123598821738675201",
      "session_id": "wecom:LiuKang"
    }
  }
}
```

The tool should not ask the model to infer WeCom target IDs when the frontend context already provides them.

## Workspace And Agent Ownership

By default, the job should be created for the current agent, which in the manager-to-worker flow means the worker agent.

This preserves the current execution model:

- The worker owns the cron job.
- The worker has the skills and workspace state needed to execute the job.
- The dispatch target points to the original frontend user/session.

An explicit `agent_id` argument may be supported for advanced use, but the cron skill should discourage agents from overriding it unless the user explicitly asks.

## Data Flow

```text
frontend WeCom request
  -> manager agent
  -> worker agent via submit_to_agent/chat_with_agent
  -> create_cron_job(...)
  -> resolve current channel metadata
  -> build CronJobSpec payload
  -> POST /cron/jobs with X-Agent-Id: worker
  -> CronManager stores job in worker workspace
  -> scheduled trigger
  -> CronExecutor dispatches result to original WeCom target
```

## Error Handling

The tool should return a concise `ToolResponse` instead of exposing long tracebacks.

Required errors:

- Missing `name`.
- Missing `text`.
- Missing `cron` when `schedule_type == "cron"`.
- Missing `run_at` when `schedule_type == "scheduled"`.
- Missing dispatch target after explicit arguments and channel metadata are resolved.
- Invalid `task_type` or `schedule_type`.
- API failure from `/cron/jobs`, including status code and brief response text.

Invalid or partial channel metadata should not crash the tool. It should ignore non-string values and report a missing-target error if required fields cannot be resolved.

## Registration

Register the new tool in the same places as other built-in tools:

- `src/qwenpaw/agents/tools/__init__.py`
- `src/qwenpaw/agents/react_agent.py`
- default built-in tools configuration if this repo requires explicit defaults for new tools

The tool name should be stable and direct:

```text
create_cron_job
```

## Skill Updates

Update cron skills:

- `src/qwenpaw/agents/skills/cron-zh/SKILL.md`
- `src/qwenpaw/agents/skills/cron-en/SKILL.md`

New guidance:

- Agents should prefer `create_cron_job` for creating jobs.
- `qwenpaw cron` remains available for manual inspection, troubleshooting, and commands not exposed as tools.
- Agents should not use shell just to create a cron job when `create_cron_job` is available.
- The tool defaults to the current frontend dispatch target when available.

## Compatibility Strategy

Keep the current CLI path working.

During the transition, the existing environment-variable compatibility layer for shell-created cron jobs can remain. That avoids breaking any prompt or older skill path that still calls `qwenpaw cron create`.

After real worker flows verify `create_cron_job`, the shell/env compatibility logic can be revisited and possibly removed in a separate cleanup.

## Testing Plan

Add tests for the new tool:

```text
tests/unit/agents/tools/test_cron.py
```

Test cases:

- Creates a text scheduled job using channel metadata as dispatch target.
- Normalizes `bot_code == "blade"` to `channel == "bladex"`.
- Explicit dispatch arguments take priority over channel metadata.
- Returns a readable error when no complete dispatch target is available.
- Returns a readable error when `schedule_type == "cron"` and `cron` is missing.
- Returns a readable error when `schedule_type == "scheduled"` and `run_at` is missing.
- Sends `X-Agent-Id` for the current agent by default.
- Preserves `timeout_seconds`, `share_session`, and `save_result_to_inbox` in the payload.

Update CLI tests only as needed to verify that the moved builder preserves existing behavior.

Run focused verification:

```bash
.venv/bin/python -m pytest \
  tests/unit/agents/tools/test_cron.py \
  tests/unit/cli/test_cron_cmd.py \
  tests/unit/agents/tools/test_agent_management.py \
  -q
```

## Expected File Changes

Likely files:

```text
src/qwenpaw/agents/tools/cron.py
src/qwenpaw/agents/tools/__init__.py
src/qwenpaw/agents/react_agent.py
src/qwenpaw/app/crons/spec_builder.py
src/qwenpaw/cli/cron_cmd.py
src/qwenpaw/agents/skills/cron-zh/SKILL.md
src/qwenpaw/agents/skills/cron-en/SKILL.md
tests/unit/agents/tools/test_cron.py
tests/unit/cli/test_cron_cmd.py
```

Estimated size: 8-9 files, roughly 300-600 net lines depending on how much builder code is shared with the CLI.

## Acceptance Criteria

- Worker agents can create cron jobs without using `execute_shell_command`.
- Jobs created by workers are stored in the worker workspace by default.
- Jobs triggered later dispatch messages to the original frontend WeCom user/session.
- Existing `qwenpaw cron create` behavior remains compatible.
- Cron skill instructions prefer the new tool and reserve shell/CLI for non-create management tasks.
- Focused unit tests pass.

