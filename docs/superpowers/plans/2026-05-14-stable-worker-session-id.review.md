# Review: Worker Agent 稳定 Session ID 实现计划

**Date:** 2026-05-14
**Reviewers:** coherence, feasibility
**Document:** `docs/superpowers/plans/2026-05-14-stable-worker-session-id.md`

---

## Overall Assessment

计划整体质量好，结构清晰，TDD 流程正确，风险考虑周全。核心目标应理解为：manager agent 代表前端企微用户调用 worker agent 时，worker session 必须稳定派生自前端用户的 root_session_id，从而让 worker 的上下文、审批路由和历史记录始终绑定到同一个企微用户会话，而不只是减少 timestamp+uuid session 文件。

---

## Auto-fixes Applied

### 1. Task 1 Step 4 行号范围错误 (feasibility, P3, confidence 0.85)

计划写 "修改 `agent_management.py:223-228`"，但 line 223 (`caller_agent_id = resolve_calling_agent_id(from_agent)`) 不在替换代码块中，且被 line 229 引用。按原范围机械替换会导致 `NameError`。

**修复：** `223-228` → `224-228`

### 2. Spec 覆盖检查表措辞歧义 (coherence, P3, confidence 0.65)

"build_agent_chat_request 已传 root_session_id" 中的 "已传" 暗示该函数已将 `root_session_id` 转发给 `resolve_agent_session_id`，与实际情况不符（这正是 Step 4 要补齐的）。

**修复：** 改为 "build_agent_chat_request 已有 root_session_id 参数，Step 4 补齐内部转发"

---

## Finding: Missing Integration Test (P3, omission, confidence 0.70)

**Section:** Task 1 - Tests

**Issue:** 计划新增 3 个 `resolve_agent_session_id` 单元测试，但没有验证 `build_agent_chat_request` 是否正确将 `root_session_id` 转发给 session 解析函数。现有 `build_agent_chat_request` 测试都不传 `root_session_id`，转发路径仅由 Task 2 手动 E2E 覆盖。

**Impact:** 后续改动转发逻辑时，自动化测试不会捕获回归。

**Suggestion:** 新增一个测试：

```python
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

---

## Finding: Session File Name Expectation Is Inaccurate (P3, correctness, confidence 0.85)

**Section:** Task 2 - Manual E2E Verification

**Issue:** 原计划预期磁盘文件名格式为 `{root_session_id}::worker_search`，但 session 文件实际由 `SafeJSONSession.sanitize_filename()` 处理，`:` 会被替换为 `--`。因此磁盘上不会原样出现 `wecom:user_xxx::worker_search`。

**Impact:** 执行者可能误判端到端验证失败。

**Suggestion:** 将验收标准改为：逻辑 session_id 应为 `wecom:user_xxx::worker_search`；磁盘文件名会被 sanitize，例如可能显示为 `wecom--user_xxx----worker_search.json`。

---

## Residual Concerns

| Concern | Risk | Source |
|---------|------|--------|
| `::` 分隔符出现在 `root_session_id` 内容中会产生歧义 | 低（root_session_id 为系统控制） | feasibility |
| `root_session_id=""` (空字符串) 边界行为未测试，当前逻辑会 fall through 到 UUID 生成，与预期一致但未显式验证 | 低 | feasibility |

---

## What's Good

- 问题描述清晰，修复前/后行为对比直观
- TDD 流程严格：先写失败测试 → 确认失败 → 实现 → 确认通过
- 三个测试覆盖了核心路径、降级路径、显式 session_id 优先级
- 风险表考虑到了向后兼容、多用户隔离、旧文件 orphaned
- Placeholder 扫描干净，无 TBD/TODO/模糊描述
- 回滚方案简单：单文件修改，git revert 即可

---

## Verdict

计划主体可以执行，但建议先修正集成测试示例，并更新手动验收中的文件名预期。补上 root_session_id 绑定前端企微用户的设计语义后，这份计划会更不容易被后续实现者误解。
