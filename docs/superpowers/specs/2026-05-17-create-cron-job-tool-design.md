# Create Cron Job Agent Tool Design

## Summary

Add a dedicated `create_cron_job` agent tool so worker agents can create scheduled jobs through a structured Python call instead of shelling out to `qwenpaw cron create`.

Scope is intentionally narrow: **creation only**, **worker-scoped**, **minimum LLM-facing surface**. Management commands (list/get/state/pause/resume/delete/run) remain CLI/API-only in this phase.

## Background

Today the cron skill instructs agents to invoke:

```bash
qwenpaw cron create --agent-id <agent_id> ...
```

The agent reaches that command through `execute_shell_command`. The CLI builds a payload and POSTs it to `/cron/jobs`.

This path has several weaknesses for worker-created reminders:

- The LLM must produce a correct shell command with correct quoting, escaping, and flags.
- Frontend channel context is plumbed into the child process through the `QWENPAW_CRON_DISPATCH_CONTEXT` environment variable and then re-injected via `_apply_forwarded_dispatch_context()` in `cli/cron_cmd.py`. Two places already touch the same dispatch fields, and the `bot_code == "blade" -> channel == "bladex"` normalization lives in a third file (`agents/tools/shell.py`).
- The path is long: agent tool → shell → CLI parser → HTTP API → CronManager.
- The worker may accidentally point dispatch at its own local session instead of the original frontend WeCom user. The env-based shim hides this until something goes wrong in production.

### Prerequisite

This design assumes `channel_meta` is already forwarded from the frontend request through `submit_to_agent` / `chat_with_agent` into the worker's runtime context. That work landed in commits `160f0266`, `828083af`, `842ca768`. Worker-side `get_current_channel_meta()` is expected to return the original frontend metadata.

## Goals

- Provide a first-class `create_cron_job` tool for agents.
- Replace shell + env plumbing with a direct in-process call.
- Resolve dispatch target from current channel metadata automatically.
- Keep jobs in the current worker's workspace by default.
- Preserve the existing `qwenpaw cron` CLI for humans, tests, and troubleshooting.
- Collapse three duplicate code paths (CLI builder, shell env shim, dispatch normalization) into shared modules.

## Non-Goals

- No `list/get/state/pause/resume/delete/run` agent tools in this phase.
- No changes to the cron API, scheduler, repository, or executor semantics.
- No idempotency / duplicate-detection logic in this phase. Name collisions are handled by whatever the server already does. Idempotency keys are explicitly Future Work.
- No frontend cron UI changes.
- No removal of the CLI or env shim in this PR. Removal is a separate cleanup with explicit preconditions (see [Rollout And Removal](#rollout-and-removal)).

## Scope Boundary

The minimal answer to "add a `create_cron_job` tool" would be a single new file that calls the existing HTTP API. This design intentionally goes wider because the existing creation path is already split across three places that disagree (CLI builder, shell env shim, CLI env consumer), and adding a fourth (the tool) without consolidating would make the duplication worse, not better.

To keep the scope honest, work is grouped as follows:

| Bucket                                                                                                                                                                                                                                                                                                                                                                | Status in this PR series                          |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| **Required prerequisites**: `channels/normalize.py`, `crons/spec_builder.py` + `CronSpecError`, `crons/service.py` (incl. `get_cron_manager_for_current_agent`). These collapse the three duplicate paths into shared modules. The tool depends on all three; landing the tool without them would lock in the duplication. Each is a self-contained, zero-behavior-change refactor. | Required.                                         |
| **The tool itself**: `agents/tools/cron.py`, registration in `agents/tools/__init__.py` and `agents/react_agent.py`, skill updates.                                                                                                                                                                                                                                       | Required.                                         |
| **Call-site migrations**: `shell.py` switches to the shared `normalize.py`; `cli/cron_cmd.py` switches to `spec_builder.py`; `app/crons/api.py`'s `POST /cron/jobs` switches to the service. These are pure refactors that should not change observable behavior.                                                                                                          | Required.                                         |
| **Env-shim removal**: deleting `QWENPAW_CRON_DISPATCH_CONTEXT` and `_apply_forwarded_dispatch_context` / `_build_cron_dispatch_context_env`.                                                                                                                                                                                                                              | Deferred to a follow-up PR (see Rollout section). |
| **New surface**: idempotency keys, manager-vs-worker hard reject (needs delegation chain), management tools (list/get/delete/etc.), repeat_*, alternative delivery modes.                                                                                                                                                                                                | Future work; explicitly out of scope.             |

If reviewers find any "required" item too large for one PR, the implementation order in [Implementation Order](#implementation-order) is designed so each step is independently reviewable and shippable.

## Architecture

Three shared modules are extracted first, then the agent tool is built on top of them.

```text
src/qwenpaw/
├── app/
│   ├── channels/
│   │   └── normalize.py        # NEW: meta -> dispatch resolution (single source of truth)
│   ├── crons/
│   │   ├── spec_builder.py     # NEW: builds CronJobSpec payload, raises CronSpecError
│   │   ├── service.py          # NEW: in-process entry point; router and tool both call it
│   │   └── api.py              # CHANGED: POST /cron/jobs delegates to service
│   ├── runner/
│   │   └── runner.py           # CHANGED: AgentRunner accepts cron_manager; wraps agent execution in active_cron_manager() context manager
│   └── workspace/
│       └── workspace.py        # CHANGED: pass cron_manager into AgentRunner via ServiceDescriptor.init_args
├── agents/
│   ├── react_agent.py          # CHANGED: register create_cron_job tool only (no signature change to QwenPawAgent)
│   └── tools/
│       ├── shell.py            # CHANGED: use shared normalize
│       └── cron.py             # NEW: thin agent tool wrapping the three modules above
├── config/
│   └── context.py              # CHANGED: new ContextVar current_cron_manager + accessors + active_cron_manager() context manager
└── cli/
    └── cron_cmd.py             # CHANGED: delegates to spec_builder; env shim stays for now
```

### 1. `app/channels/normalize.py`

Single source of truth for translating channel metadata into a dispatch descriptor. Pure function, no I/O.

```python
def resolve_dispatch_from_channel_meta(
    meta: Mapping[str, Any] | None,
) -> dict | None:
    """Return {'channel', 'target_user', 'target_session', 'meta'} or None.

    None means the metadata is incomplete; the caller decides whether
    to error or fall back to explicit arguments.
    """
```

Behavior:

- `meta["channel_id"]` wins over inferred channels (explicit beats inferred).
- If `channel_id` is missing and `bot_code == "blade"`, the channel is `"bladex"`.
- Returns `None` if `channel`, `target_user`, or `target_session` cannot be resolved.

Existing call sites that must be migrated to use this function:

- `agents/tools/shell.py:_build_cron_dispatch_context_env` (env shim — keep working, just call the shared helper).
- New `agents/tools/cron.py` (this design).

### 2. `app/crons/spec_builder.py`

Pure payload builder. UI-agnostic. Replaces `_build_spec_from_cli` and `_build_schedule_from_cli` in `cli/cron_cmd.py`.

```python
class CronSpecError(ValueError):
    """Raised when CronJobSpec arguments are invalid. UI-agnostic."""


def build_cron_job_payload(
    *,
    task_type: str,                       # "text" | "agent"
    schedule_type: str,                   # "cron" | "scheduled"
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

The builder **must not raise `click.UsageError`**. Callers adapt:

```python
# cli/cron_cmd.py
try:
    payload = build_cron_job_payload(...)
except CronSpecError as e:
    raise click.UsageError(str(e)) from e

# agents/tools/cron.py
try:
    payload = build_cron_job_payload(...)
except CronSpecError as e:
    return _error_response(str(e))
```

### 3. `app/crons/service.py`

In-process service layer. Used by both the HTTP router and the agent tool. The tool does **not** make a loopback HTTP request.

The current router (`app/crons/api.py:95-105`) generates the job id inline with `uuid.uuid4()` and then calls `CronManager.create_or_replace_job(spec)`. The service collapses that pattern into one place. The HTTP router resolves the manager via its existing `get_cron_manager(request)` dependency; the agent tool resolves it through a workspace accessor that does not require a FastAPI `Request`.

```python
# app/crons/service.py
import uuid
from .manager import CronManager
from .models import CronJobSpec


async def create_cron_job_via_manager(
    manager: CronManager,
    spec: CronJobSpec,
) -> CronJobSpec:
    """Create a cron job through an already-resolved manager.

    Generates the job id server-side and persists via create_or_replace_job.
    Returns the created spec.
    """
    job_id = str(uuid.uuid4())
    created = spec.model_copy(update={"id": job_id})
    await manager.create_or_replace_job(created)
    return created


async def get_cron_manager_for_current_agent() -> CronManager:
    """Resolve CronManager for the active agent context, no FastAPI Request.

    Reads the new `current_cron_manager` ContextVar set by the agent runner
    at request entry. Raises if the active agent has no CronManager
    initialized or the ContextVar is unset.
    """
    from ...config.context import get_current_cron_manager

    mgr = get_current_cron_manager()
    if mgr is None:
        raise RuntimeError(
            "CronManager is not available in the current agent context."
        )
    return mgr
```

Manager resolution is split by caller:

- **HTTP router (`app/crons/api.py`)** keeps using `Depends(get_cron_manager)` to obtain the manager, then calls `create_cron_job_via_manager(mgr, spec)`. The `uuid.uuid4()` line currently in the route handler moves into the service.
- **Agent tool (`agents/tools/cron.py`)** calls `get_cron_manager_for_current_agent()` followed by `create_cron_job_via_manager(mgr, spec)`. No JSON round-trip, no header plumbing, no `httpx` client inside the agent process.
- **CLI** keeps going through HTTP because it is a separate process.

This split is deliberate: **manager-resolution policy** (which workspace owns the job) stays with the caller, where the right context lives; **creation mechanics** (id generation, persistence) live in one shared service function.

### 3a. New ContextVar wiring (required for the tool path)

Verification of the current code shows there is **no existing non-`Request` accessor** for `CronManager`. Today, the only two consumers (`app/crons/api.py:26` and `app/routers/config.py:554`) both reach `workspace.cron_manager` from inside FastAPI handlers via `Depends(get_agent_for_request)`. `MultiAgentManager` is stored in `request.app.state.multi_agent_manager` and is not exposed as a module-level singleton. The agent class is `QwenPawAgent` (`react_agent.py:80`), and it only holds `self._workspace_dir` — not the parent `Workspace` and not the `CronManager`.

The construction chain in production is:

```text
Workspace
  └── _service_manager registers AgentRunner with init_args=lambda ws: {agent_id, workspace_dir, task_tracker}
       └── AgentRunner constructs QwenPawAgent(workspace_dir=..., memory_manager=..., context_manager=..., ...)
            └── QwenPawAgent.reply() sets current_workspace_dir at react_agent.py:1394
```

Neither AgentRunner nor QwenPawAgent currently receives `cron_manager`. There are exactly two real `QwenPawAgent` construction sites: `app/runner/runner.py:606` (production path) and `cli/task_cmd.py:106` (the `qwenpaw task run` CLI path, no Workspace, no CronManager).

The agent tool therefore needs new wiring, following the established ContextVar pattern in `src/qwenpaw/config/context.py` (where `current_workspace_dir`, `current_recent_max_bytes`, etc. already live):

```python
# config/context.py  (additions)
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from qwenpaw.app.crons.manager import CronManager

current_cron_manager: ContextVar["CronManager | None"] = ContextVar(
    "current_cron_manager", default=None,
)


def get_current_cron_manager() -> "CronManager | None":
    return current_cron_manager.get()


@contextmanager
def active_cron_manager(mgr: "CronManager | None") -> Iterator[None]:
    """Bind a CronManager to the current async context for the duration
    of one agent execution. Resets the ContextVar on exit so the runner
    cannot leak a manager into a later request.
    """
    token = current_cron_manager.set(mgr)
    try:
        yield
    finally:
        current_cron_manager.reset(token)
```

The context manager is the supported way to set this ContextVar. There is intentionally no bare `set_current_cron_manager` setter — every call site must pair set with reset. This is the one place where the cron ContextVar diverges from the existing `set_current_workspace_dir` pattern (which never resets), because `current_cron_manager` holds a live object reference that the runner reuses across requests; leaking a stale manager would be a real bug, not just a logging quirk.

**Where to call `active_cron_manager` — at the AgentRunner layer, wrapping the actual agent execution.** This is the result of code verification: AgentRunner is the natural place to know whether a CronManager exists (it does in production, doesn't from `qwenpaw task run`). Threading `cron_manager` through QwenPawAgent's constructor just for it to enter the context manager would touch one more class for no gain.

Concrete changes:

1. **`src/qwenpaw/app/workspace/workspace.py:156-163`** — extend the AgentRunner ServiceDescriptor:

   ```python
   sm.register(
       ServiceDescriptor(
           name="runner",
           service_class=AgentRunner,
           init_args=lambda ws: {
               "agent_id": ws.agent_id,
               "workspace_dir": ws.workspace_dir,
               "task_tracker": ws._task_tracker,
               "cron_manager": ws.cron_manager,      # NEW
           },
           ...
       )
   )
   ```

2. **`src/qwenpaw/app/runner/runner.py`** — `AgentRunner.__init__` accepts and stores `cron_manager: CronManager | None = None`. Wrap the actual agent execution (the agent invocation at `runner.py:791` and the mission-mode branches at `runner.py:770-787`) in the `active_cron_manager` context manager. Setting and resetting are paired so the ContextVar cannot leak across requests handled by the same long-lived `AgentRunner` instance.

   ```python
   from ...config.context import active_cron_manager

   # Construction and pre-flight (lines ~606-755) stays as-is.
   agent = QwenPawAgent(agent_config=..., workspace_dir=self.workspace_dir, ...)
   await agent.register_mcp_clients()
   agent.set_console_output_enabled(enabled=False)
   ...
   agent.rebuild_sys_prompt()

   # Bind cron_manager only around the actual run, with guaranteed reset.
   with active_cron_manager(self._cron_manager):
       if mission_info is not None:
           ...
           async for msg, last in run_mission_phase1(agent=agent, ...):
               yield msg, last
       else:
           async for msg, last in _stream_printing_messages_interruptible(
               agents=[agent],
               coroutine_task=agent(msgs),
           ):
               yield msg, last
   ```

   The `with` block must span every code path that can invoke the agent (standard `agent(msgs)` and both mission phases). `interrupt()` handling in the `except asyncio.CancelledError` branch at `runner.py:820-822` runs **inside** the `with` because cancellation happens while the agent is still active.

   Why set inside the block, not at construction time: between construction (line 606) and the first agent call (line 791) there are async operations (`register_mcp_clients`, sys prompt rebuild, plan handling). Setting the ContextVar at line 606 would leave it bound for the entire `query_handler` scope and risk lingering into bookkeeping done **after** the `async for` loop ends. The context-manager scope keeps the binding to the minimum needed for tools to see it.

3. **`src/qwenpaw/cli/task_cmd.py:106`** — no change. `qwenpaw task run` does not have a Workspace and never had a CronManager. The default ContextVar value is `None`, so `create_cron_job` invoked from that path returns a clean "CronManager not available" error. That is the correct semantics — cron creation belongs to running workspaces, not one-shot task runs.

4. **`src/qwenpaw/agents/react_agent.py`** — register `create_cron_job` in the tool table (and/or rely on the plugin discovery loop at `react_agent.py:289-302`). **No constructor signature change.** No `reply()` change beyond what registration requires.

This keeps the change surface tight: three files in the wiring (`config/context.py`, `workspace.py`, `runner.py`), one in registration (`react_agent.py`), zero in `QwenPawAgent` internals.

The new `get_cron_manager_for_current_agent` accessor is small and reusable — any future in-process caller (background task, scheduler hook, etc.) can use it without going through FastAPI.

### 4. `agents/tools/cron.py`

The agent tool itself. Stays thin. It composes the three modules above and adds:

- Agent-context resolution (which agent owns the job — current agent identity, no overrides).
- Channel-meta-based dispatch resolution. v1 does not accept LLM-supplied channel/target overrides.

Note: there is **no code-level manager-vs-worker check** in v1. Ownership semantics (manager should delegate to a worker first for worker-specific reminders) are documented in [Agent ID Resolution And Manager Policy](#agent-id-resolution-and-manager-policy) and enforced only through skill guidance.

## Tool Signature (LLM-Facing)

Deliberately minimal. Six parameters. Advanced fields exist in the underlying builder but are not exposed to the LLM in v1.

```python
async def create_cron_job(
    *,
    task_type: str,          # "text" | "agent"
    schedule_type: str,      # "cron" | "scheduled"
    name: str,
    text: str,
    cron: str | None = None,
    run_at: str | None = None,
    timeout_seconds: int = 120,
) -> ToolResponse: ...
```

Defaults applied internally (not configurable through the tool):

| Field                  | Default                                          |
| ---------------------- | ------------------------------------------------ |
| `channel`              | from `channel_meta` via normalize.py             |
| `target_user`          | from `channel_meta`                              |
| `target_session`       | from `channel_meta`                              |
| `agent_id`             | `resolve_calling_agent_id(None)` (current agent) |
| `timezone`             | user timezone from config, fallback `UTC`        |
| `enabled`              | `True`                                           |
| `mode`                 | `"final"`                                        |
| `save_result_to_inbox` | server default (omit field)                      |
| `share_session`        | `True`                                           |
| `repeat_*`             | not supported in v1                              |

Rationale for the cut: each LLM-facing parameter is an error surface. v1 covers the two scheduling shapes that motivate this work:

- **One-shot reminder** — "remind me at 09:00 tomorrow" → `schedule_type="scheduled"`, `run_at="2026-05-18T09:00:00+08:00"`, no `cron`.
- **Recurring reminder** — "remind me every day at 09:00" → `schedule_type="cron"`, `cron="0 9 * * *"`, no `run_at`.

Recurrence in v1 is expressed **only** through a cron expression. The `scheduled` + `repeat_every_days` / `repeat_end_type` / `repeat_until` / `repeat_count` combination remains available via the CLI for power users but is not part of the tool surface. If a real use case appears that cron expressions cannot express (e.g., "every 3 days for 10 occurrences"), the repeat fields can be added later; until then, the simpler surface wins.

Alternative delivery modes (`stream`), inbox toggles, and explicit timezone overrides are similarly deferred to future work.

## Agent ID Resolution And Manager Policy

The tool always uses `resolve_calling_agent_id(None)` from `agents/tools/agent_management.py` to determine the owning agent. Job ownership follows current agent identity:

- Manager → worker (delegated) → `create_cron_job` ⇒ worker owns the job. ✅ (intended path)
- User → manager (no delegation) → `create_cron_job` ⇒ manager owns the job. ✅ (allowed; channel_meta forwarding already makes dispatch correct)

### Why no hard reject in v1

`agent_context.py` today exposes `current_agent_id`, `current_session_id`, `current_root_session_id`, and `current_channel_meta`. It does **not** expose a delegation chain or parent-agent pointer. Inferring "is this the top-level manager?" would require adding new ContextVars and wiring their lifecycle through every delegation call site — a non-trivial change that does not belong in this PR.

Without that signal, the only available proxy is "agent id matches the configured manager id," which is fragile and project-specific.

v1 therefore **does not hard-reject** manager-level calls. Instead:

- The cron skill (`cron-zh` / `cron-en` SKILL.md) instructs: "If you are the top-level manager and the user is asking for a worker-specific reminder, delegate to the worker first, then call `create_cron_job` from inside the worker."
- If the manager calls it anyway and `channel_meta` is present, the job will be valid: dispatch points to the original frontend user, and the manager workspace owns it. Whether that's the right workspace depends on the prompt, which the LLM can judge with the skill text.

A future PR can add delegation-chain tracking to `agent_context` and turn this into a hard reject if real flows show the soft guidance is insufficient. That's a deliberate "loosen now, tighten later if evidence requires it" — the opposite of the previous draft.

Manual override (`agent_id=...`) is **not** exposed to the LLM in v1. Power users can still go through the CLI.

## Dispatch Resolution

Priority is fixed:

1. Channel-meta-derived dispatch from `resolve_dispatch_from_channel_meta(get_current_channel_meta())`.
2. If channel meta is incomplete, return a structured error. The LLM is **not** asked to invent WeCom IDs.

In v1 the LLM cannot override channel/target_user/target_session. That removes a class of bugs where the LLM hallucinates a target. If override is needed later, it can be added behind a feature flag with clear preconditions.

## Data Flow

```text
frontend WeCom request
  ├─ channel_meta set on request (cf. commit 160f0266)
  └─ manager agent
       └─ delegate (submit_to_agent / chat_with_agent) — forwards channel_meta
            └─ worker agent runtime
                 └─ create_cron_job(...)
                      ├─ resolve_calling_agent_id() -> worker
                      ├─ resolve_dispatch_from_channel_meta(meta) -> dispatch
                      ├─ build_cron_job_payload(spec, dispatch, ...) -> dict
                      └─ create_cron_job_service(spec, agent_id=worker) -> job
                           └─ CronManager stores job in worker workspace
                               └─ scheduled trigger
                                    └─ CronExecutor dispatches result to original WeCom target
```

## Error Handling

Returns `ToolResponse` with a short error message. No tracebacks leak to the LLM.

Required error cases:

- `name` missing or empty.
- `text` missing or empty.
- `cron` missing when `schedule_type == "cron"`.
- `run_at` missing when `schedule_type == "scheduled"`.
- Channel metadata incomplete (cannot resolve channel/user/session).
- Invalid `task_type` or `schedule_type`.
- `CronSpecError` raised by `build_cron_job_payload`.
- Service-layer failure: error message includes status (e.g. validation, conflict) and a short reason. No raw exception strings.

## Registration

Add the tool in two places:

- `src/qwenpaw/agents/tools/__init__.py` — append to `__all__`.
- `src/qwenpaw/agents/react_agent.py` — extend `tool_functions` (or rely on the plugin-tool discovery loop at `react_agent.py:289-302`, in which case the agent config must enable `create_cron_job`).

Tool name: `create_cron_job`.

## Skill Updates

Update both:

- `src/qwenpaw/agents/skills/cron-zh/SKILL.md`
- `src/qwenpaw/agents/skills/cron-en/SKILL.md`

Required changes:

- Prefer `create_cron_job` for creating jobs.
- Do not use `execute_shell_command` to invoke `qwenpaw cron create` when `create_cron_job` is available.
- `qwenpaw cron` remains available for inspection, troubleshooting, and management commands not yet exposed as tools.
- If the agent is the top-level manager and the user is asking for a worker-specific reminder, delegate to that worker first and call `create_cron_job` from inside the worker. (Soft guidance; no hard reject in v1.)
- The tool resolves dispatch target from current channel context; the agent should not try to compose WeCom user IDs by hand.

## Implementation Order

Each step is independently reviewable and revertible. Steps 1–3 are pure refactors and must not change existing behavior (existing CLI tests and cron API tests should pass unchanged).

1. **Extract `channels/normalize.py`.** `shell.py` switches to the helper. Add unit tests for normalization (incl. the explicit-`channel_id`-wins case).
2. **Extract `crons/spec_builder.py` + `CronSpecError`.** CLI switches to delegating to it. CLI tests stay green.
3. **Extract `crons/service.py`.** Adds `create_cron_job_via_manager` and `get_cron_manager_for_current_agent`. `app/crons/api.py`'s `POST /cron/jobs` handler switches to calling `create_cron_job_via_manager(mgr, spec)`. Existing cron API tests stay green.
4. **Wire `current_cron_manager` ContextVar.** Add the ContextVar + `active_cron_manager` context manager in `config/context.py`. Extend `AgentRunner.__init__` (`app/runner/runner.py`) to accept `cron_manager: CronManager | None = None`. In the agent execution section of `query_handler` (around `runner.py:770-793`), wrap the agent-running block — both mission-mode branches and the standard `_stream_printing_messages_interruptible(agents=[agent], coroutine_task=agent(msgs))` call — in `with active_cron_manager(self._cron_manager):`. The `with` block must cover every path that can drive the agent, including the `CancelledError` interrupt branch. Update `app/workspace/workspace.py` AgentRunner `ServiceDescriptor.init_args` (`workspace.py:160-164`) to pass `cron_manager=ws.cron_manager`. CLI path (`task_cmd.py:106`) is unchanged — its default `None` produces a clean error if a cron job is ever attempted from that path, which is the correct semantics. No changes to `QwenPawAgent` construction.
5. **Add `agents/tools/cron.py`.** Composes 1–4, resolves the manager via `get_cron_manager_for_current_agent()`. New unit tests live in `tests/unit/agents/tools/test_cron.py`.
6. **Register + skill updates.**
7. **Soak.** One full release with the new tool enabled; cron skill prefers it; no regressions reported in worker flows.
8. **Cleanup PR (separate).** Remove `_apply_forwarded_dispatch_context` from `cli/cron_cmd.py`, remove `_build_cron_dispatch_context_env` from `shell.py`, remove the `QWENPAW_CRON_DISPATCH_CONTEXT` env constant. Preconditions for this PR are listed below.

## Rollout And Removal

The env-shim (`QWENPAW_CRON_DISPATCH_CONTEXT`) is **not** removed in the same PR that introduces the tool. It is removed when all of the following are true:

- ✅ `create_cron_job` has shipped in at least one release with default-on registration.
- ✅ `cron-zh` and `cron-en` skill documents no longer instruct agents to shell out for creation.
- ✅ Telemetry / log grep shows no agent-driven `qwenpaw cron create` invocations for one release window.

When those preconditions hold, the cleanup PR removes both the env builder in `shell.py` and the env consumer in `cli/cron_cmd.py` in a single change. Until then, shelling out keeps working unchanged.

## Testing Plan

New test file `tests/unit/agents/tools/test_cron.py`:

- Creates a `text` scheduled job using channel metadata as dispatch target.
- `bot_code == "blade"` is normalized to `channel == "bladex"`.
- **Explicit `channel_id` wins over `bot_code` inference** (regression for the inference layer).
- Returns a readable error when channel metadata is incomplete.
- Returns a readable error when `schedule_type == "cron"` and `cron` is missing.
- Returns a readable error when `schedule_type == "scheduled"` and `run_at` is missing.
- Owning agent in the created job is the current worker (verified through the service layer mock, not via HTTP header).
- `timeout_seconds` is forwarded into the payload.

New test file `tests/unit/app/crons/test_spec_builder.py`:

- All CronSpec validation paths previously covered in CLI tests, moved here.
- CLI tests remain but are reduced to "CLI wires args through to the builder."

New test file `tests/unit/app/channels/test_normalize.py`:

- `bot_code == "blade"` → `bladex`.
- Explicit `channel_id` wins.
- Missing fields return `None`.
- Non-dict input returns `None`.

Existing tests touched:

- `tests/unit/cli/test_cron_cmd.py` — adjusted to expect delegation to `build_cron_job_payload`; CLI surface unchanged.
- `tests/unit/agents/tools/test_agent_management.py` — unchanged unless `resolve_calling_agent_id` is touched.

Focused verification:

```bash
.venv/bin/python -m pytest \
  tests/unit/app/channels/test_normalize.py \
  tests/unit/app/crons/test_spec_builder.py \
  tests/unit/agents/tools/test_cron.py \
  tests/unit/cli/test_cron_cmd.py \
  tests/unit/agents/tools/test_agent_management.py \
  -q
```

## Expected File Changes

```text
src/qwenpaw/app/channels/normalize.py            (new)
src/qwenpaw/app/crons/spec_builder.py            (new)
src/qwenpaw/app/crons/service.py                 (new)
src/qwenpaw/agents/tools/cron.py                 (new)
src/qwenpaw/agents/tools/__init__.py             (register)
src/qwenpaw/agents/react_agent.py                (register tool only — no signature change to QwenPawAgent)
src/qwenpaw/agents/tools/shell.py                (use shared normalize)
src/qwenpaw/cli/cron_cmd.py                      (delegate to spec_builder)
src/qwenpaw/app/crons/api.py                     (POST /cron/jobs delegates to service)
src/qwenpaw/config/context.py                    (new ContextVar: current_cron_manager + accessors)
src/qwenpaw/app/workspace/workspace.py           (pass cron_manager into AgentRunner via ServiceDescriptor)
src/qwenpaw/app/runner/runner.py                 (AgentRunner accepts cron_manager; wraps agent execution in active_cron_manager() context manager with paired set/reset)
src/qwenpaw/agents/skills/cron-zh/SKILL.md       (skill update)
src/qwenpaw/agents/skills/cron-en/SKILL.md       (skill update)
tests/unit/app/channels/test_normalize.py        (new)
tests/unit/app/crons/test_spec_builder.py        (new)
tests/unit/agents/tools/test_cron.py             (new)
tests/unit/cli/test_cron_cmd.py                  (adjust)
```

Estimated size: ~14 files. Net LoC is moderate — extracted modules absorb code currently sitting in CLI and shell shims, so the increase is smaller than it looks.

## Future Work (Explicitly Deferred)

- Idempotency keys to prevent the LLM from creating the same reminder twice on retry.
- Management tools (`list_cron_jobs`, `delete_cron_job`, etc.).
- Manager-level cron creation, if a real use case appears.
- Explicit overrides of channel/target via tool arguments, behind a clear preconditions check.
- Repeated-schedule support (`repeat_every_days`, `repeat_end_type`, etc.) in the tool surface.

## Acceptance Criteria

- Worker agents can create cron jobs without invoking `execute_shell_command`.
- Jobs created by a worker are owned by that worker and stored in its workspace.
- Triggered jobs dispatch results to the original frontend WeCom user/session.
- `bot_code == "blade"` normalization to `channel == "bladex"` lives in exactly one module (`channels/normalize.py`) and is called from all sites that previously open-coded it.
- `build_cron_job_payload` is the only source of CronJobSpec construction; CLI and tool both delegate to it; it raises `CronSpecError`, not `click.UsageError`.
- `app/crons/api.py`'s `POST /cron/jobs` handler and the new agent tool both call `create_cron_job_via_manager` (id generation + persistence lives in one place). The agent tool resolves its manager via `get_cron_manager_for_current_agent()` and does not make a loopback HTTP request.
- Existing `qwenpaw cron create` continues to work unchanged.
- The env shim (`QWENPAW_CRON_DISPATCH_CONTEXT`) is unchanged in this PR; its removal is a separate, gated cleanup.
- New unit tests for `normalize.py`, `spec_builder.py`, and `tools/cron.py` pass; existing cron tests pass unchanged.
