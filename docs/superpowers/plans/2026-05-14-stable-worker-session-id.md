# Worker Agent 稳定 Session ID 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** manager agent 代表前端企微用户调用 worker agent 时，worker session 必须稳定派生自前端用户的 root_session_id，从而让 worker 的上下文、审批路由和历史记录始终绑定到同一个企微用户会话，而不是每次 `chat_with_agent` / `submit_to_agent` 都生成新的 timestamp+uuid session。

**Architecture:** 在 `resolve_agent_session_id` 中引入 `root_session_id` 参数，当用户未显式传入 `session_id` 时，优先基于 `root_session_id` 和 `to_agent` 生成稳定的派生 ID（格式 `{root_session_id}::{to_agent}`），降级方案保持原有 UUID 生成逻辑。

**Tech Stack:** Python, pytest

---

## 问题背景

当前行为（`agent_management.py:110-119`）：

```python
def resolve_agent_session_id(from_agent, to_agent, session_id):
    caller_agent_id = resolve_calling_agent_id(from_agent)
    if not session_id:
        return generate_unique_session_id(caller_agent_id, to_agent)  # 每次新 UUID
    return session_id
```

`generate_unique_session_id` 使用 `timestamp + uuid`，导致同一个企微用户的多次任务在 worker 端产生完全不同的 session，上下文完全隔离。

修复后行为：

```python
def resolve_agent_session_id(from_agent, to_agent, session_id, root_session_id=None):
    caller_agent_id = resolve_calling_agent_id(from_agent)
    if not session_id:
        if root_session_id:
            return f"{root_session_id}::{to_agent}"  # 稳定复用
        return generate_unique_session_id(caller_agent_id, to_agent)
    return session_id
```

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `src/qwenpaw/agents/tools/agent_management.py` | session ID 解析逻辑 + agent 间通信工具 | 修改 `resolve_agent_session_id` 签名和实现；修改 `build_agent_chat_request` 传递 `root_session_id` |
| `tests/unit/agents/tools/test_agent_management.py` | agent_management 模块单元测试 | 新增 4 个测试用例覆盖稳定 session 逻辑（含 build_agent_chat_request 转发验证） |

---

## Task 1: 修改 `resolve_agent_session_id` 支持稳定派生 ID

**Files:**
- Modify: `src/qwenpaw/agents/tools/agent_management.py:110-119`
- Test: `tests/unit/agents/tools/test_agent_management.py`

- [ ] **Step 1: 编写失败测试 — 验证 root_session_id 生成稳定 session**

在 `tests/unit/agents/tools/test_agent_management.py` 末尾添加：

```python
def test_resolve_agent_session_id_derives_from_root_session():
    """When session_id is empty but root_session_id is provided,
    derive a stable session_id from root_session_id + to_agent."""
    result = agent_management.resolve_agent_session_id(
        from_agent="manager",
        to_agent="worker_search",
        session_id=None,
        root_session_id="wecom:user_123",
    )

    assert result == "wecom:user_123::worker_search"


def test_resolve_agent_session_id_falls_back_to_unique_when_no_root():
    """When both session_id and root_session_id are empty,
    fall back to generate_unique_session_id."""
    result = agent_management.resolve_agent_session_id(
        from_agent="manager",
        to_agent="worker_search",
        session_id=None,
        root_session_id=None,
    )

    assert result.startswith("manager:to:worker_search:")


def test_resolve_agent_session_id_prefers_explicit_session_id():
    """When session_id is explicitly provided, ignore root_session_id."""
    result = agent_management.resolve_agent_session_id(
        from_agent="manager",
        to_agent="worker_search",
        session_id="my-custom-session",
        root_session_id="wecom:user_123",
    )

    assert result == "my-custom-session"


def test_build_agent_chat_request_forwards_root_session_id():
    """build_agent_chat_request passes root_session_id through to
    resolve_agent_session_id for stable session derivation."""
    session_id, payload, _ = agent_management.build_agent_chat_request(
        from_agent="manager",
        to_agent="worker_search",
        text="search something",
        root_session_id="wecom:user_123",
        session_id=None,
    )

    assert session_id == "wecom:user_123::worker_search"
    assert payload["session_id"] == "wecom:user_123::worker_search"
    assert payload["root_session_id"] == "wecom:user_123"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/luck0o0/github/QwenPaw
pytest tests/unit/agents/tools/test_agent_management.py::test_resolve_agent_session_id_derives_from_root_session -v
```

**Expected:** FAIL — `TypeError: resolve_agent_session_id() got an unexpected keyword argument 'root_session_id'`

- [ ] **Step 3: 修改 `resolve_agent_session_id` 实现**

修改 `src/qwenpaw/agents/tools/agent_management.py:110-119`：

```python
def resolve_agent_session_id(
    from_agent: Optional[str],
    to_agent: str,
    session_id: Optional[str],
    root_session_id: Optional[str] = None,
) -> str:
    """Resolve the effective session ID based on session reuse semantics.

    Priority:
    1. Explicit ``session_id`` argument
    2. Stable derived ID from ``root_session_id + to_agent``
    3. Unique timestamp+uuid ID (fallback)
    """
    caller_agent_id = resolve_calling_agent_id(from_agent)
    if session_id:
        return session_id
    if root_session_id:
        return f"{root_session_id}::{to_agent}"
    return generate_unique_session_id(caller_agent_id, to_agent)
```

- [ ] **Step 4: 修改 `build_agent_chat_request` 传递 `root_session_id`**

修改 `src/qwenpaw/agents/tools/agent_management.py:224-228`：

```python
    final_session_id = resolve_agent_session_id(
        caller_agent_id,
        to_agent,
        session_id,
        root_session_id=root_session_id,
    )
```

- [ ] **Step 5: 运行测试确认全部通过**

```bash
cd /Users/luck0o0/github/QwenPaw
pytest tests/unit/agents/tools/test_agent_management.py -v
```

**Expected:** 所有测试 PASS，包括新增的 4 个测试。

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/agents/tools/agent_management.py tests/unit/agents/tools/test_agent_management.py
git commit -m "feat(agents): derive stable worker session_id from root_session_id

When chat_with_agent/submit_to_agent is called without an explicit
session_id, derive a stable session ID from root_session_id + to_agent
instead of generating a new timestamp+uuid every time.

This ensures the same manager session always reuses the same worker
session, preserving context and preventing session file accumulation."
```

---

## Task 2: 端到端验证（手动）

- [ ] **Step 1: 启动 QwenPaw 服务**

```bash
cd /Users/luck0o0/github/QwenPaw
python -m qwenpaw serve
```

- [ ] **Step 2: 通过 manager 向同一 worker 发送两条消息**

在 console 或企微中向 manager 发送：

```
调用 worker_search 帮我搜索 "Python async"
```

等待完成后，再次发送：

```
调用 worker_search 帮我搜索 "Python decorator"
```

- [ ] **Step 3: 检查 worker session 文件**

```bash
ls -la ~/.qwenpaw/workspaces/worker_search/sessions/
```

**Expected:** 只有一个 session 文件。逻辑 session_id 为 `{root_session_id}::worker_search`（例如 `wecom:user_xxx::worker_search`）；磁盘文件名经 `SafeJSONSession.sanitize_filename()` 处理后 `:` 会被替换为 `--`，实际文件名类似 `wecom--user_xxx----worker_search.json`。

- [ ] **Step 4: 检查 worker 是否保留了历史上下文**

向 manager 发送：

```
调用 worker_search，问它我刚才搜了什么
```

**Expected:** worker_search 能回答出之前搜索的内容（上下文保持连续）。

---

## Spec 覆盖检查

| 需求 | 对应 Task |
|------|----------|
| 同一 (root_session, worker) 复用稳定 session_id | Task 1 Step 3 |
| 显式传入 session_id 时仍优先使用 | Task 1 测试 `test_resolve_agent_session_id_prefers_explicit_session_id` |
| 无 root_session_id 时保持原有降级行为 | Task 1 测试 `test_resolve_agent_session_id_falls_back_to_unique_when_no_root` |
| 不破坏现有调用方（build_agent_chat_request 已有 root_session_id 参数，Step 4 补齐内部转发） | Task 1 Step 4 |
| build_agent_chat_request 正确将 root_session_id 转发到 resolve_agent_session_id 和 payload | Task 1 测试 `test_build_agent_chat_request_forwards_root_session_id` |

## Placeholder 扫描

- 无 "TBD" / "TODO" / "implement later"
- 无 "Add appropriate error handling" 等模糊描述
- 所有代码块包含完整可运行代码
- 所有测试包含完整断言

---

## 风险与回滚

| 风险 | 缓解措施 |
|------|---------|
| 稳定 ID 格式变更可能导致旧 session 文件 orphaned | 旧文件自然淘汰，不影响新逻辑；如需清理可手动删除 |
| 多用户共享同一 worker session | 不同 root_session_id 生成不同派生 ID，天然隔离 |
| 显式传 session_id 的调用方受影响 | 签名增加可选参数，100% 向后兼容 |

**回滚：** 单文件修改（`agent_management.py`），git revert 即可。
