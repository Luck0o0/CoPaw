# 自定义 Channel 从全局 config.json 继承配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有 agent 自动继承全局 `config.json` 中自定义 channel（如 bladex）的配置字段，不再需要每个 agent 手动补 `agent.json`。

**Architecture:** 修改 `list_channels()` API 的 fallback 分支——当前自定义 channel 不在 agent 配置中时返回空兜底 `{"enabled": False, "bot_prefix": ""}`，改为从全局 `config.json` 的 `channels` 下读取同名 key 的完整字段作为兜底。

**Tech Stack:** Python 3.12 + FastAPI + Pydantic

---

## File Structure

| 路径 | 操作 | 责任 |
|---|---|---|
| `src/qwenpaw/app/routers/config.py:108-111` | 修改 | `list_channels()` 兜底分支改从全局 config 继承 |

---

## Task: list_channels 兜底改为全局 config.json

**Files:**
- Modify: `src/qwenpaw/app/routers/config.py:108-111`

- [ ] **Step 1: 修改 list_channels 的 else 分支**

当前代码（`config.py:102-114`）：

```python
    for key in available:
        if key in all_configs:
            channel_data = (
                dict(all_configs[key])
                if isinstance(all_configs[key], dict)
                else all_configs[key]
            )
        else:
            # Channel registered but no config saved yet, use empty default
            channel_data = {"enabled": False, "bot_prefix": ""}
        if isinstance(channel_data, dict):
            channel_data["isBuiltin"] = key in BUILTIN_CHANNEL_KEYS
        result[key] = channel_data
```

改为：

```python
    # 全局 config.json 的 channels（作为自定义 channel 的兜底）
    from ..config.utils import load_config as _load_global_config
    _global_channels = getattr(_load_global_config(), "channels", None)
    _global_extra = getattr(_global_channels, "__pydantic_extra__", None) or {} if _global_channels else {}

    for key in available:
        if key in all_configs:
            channel_data = (
                dict(all_configs[key])
                if isinstance(all_configs[key], dict)
                else all_configs[key]
            )
        else:
            # 自定义 channel（非内置）：优先从全局 config.json 读取默认配置
            global_default = {}
            if _global_channels and hasattr(_global_channels, key):
                global_default = getattr(_global_channels, key)
                if hasattr(global_default, "model_dump"):
                    global_default = global_default.model_dump()
                elif isinstance(global_default, dict):
                    global_default = dict(global_default)
            elif key in _global_extra:
                global_default = dict(_global_extra[key])
            if global_default:
                channel_data = global_default
            else:
                channel_data = {"enabled": False, "bot_prefix": ""}
        if isinstance(channel_data, dict):
            channel_data["isBuiltin"] = key in BUILTIN_CHANNEL_KEYS
        result[key] = channel_data
```

逻辑：
1. 先从 agent 自己的 `agent.json` 读 channel 配置（原逻辑不变）
2. 如果 agent 没有 → 去全局 `config.json` 的 `channels` 找（含 `__pydantic_extra__` 中存储的自定义 channel）
3. 如果全局也没有 → 退回空兜底 `{"enabled": False, "bot_prefix": ""}`

- [ ] **Step 2: 重启 qwenpaw 并验证**

```bash
# 重启 qwenpaw
kill $(lsof -ti :8088)
cd /Users/luck0o0/github/QwenPaw
nohup .venv/bin/qwenpaw app --host 0.0.0.0 --port 8088 >> ~/.qwenpaw/qwenpaw.log 2>&1 &

# 验证：切换到非 default agent（如 reminder），查询 bladex channel
curl -s http://localhost:8088/api/config/channels | python3 -c "
import sys,json
d=json.load(sys.stdin)
b=d.get('bladex',{})
print('bladex_url:', b.get('bladex_url'))
print('bladex_token:', 'set' if b.get('bladex_token') else 'empty')
"
```

预期输出：`bladex_url: http://127.0.0.1:80` 和 `bladex_token: set`（全局 config 的值被正确继承）。

- [ ] **Step 3: 在 Web Console 中确认**

选任意 agent → Channels 页面 → 点开 bladex 卡片 → 抽屉中能看到 `bladex_url` 和 `bladex_token` 输入框且有值。

- [ ] **Step 4: 提交**

```bash
cd /Users/luck0o0/github/QwenPaw
git add src/qwenpaw/app/routers/config.py
git commit -m "Fix: list_channels — 自定义 channel 从全局 config.json 继承配置

所有 agent 自动继承全局 config.json 中的自定义 channel（如 bladex）
的默认字段（bladex_url、bladex_token 等），不再需要每个 agent
手动补 agent.json。仅当全局 config 中也找不到时才退回空兜底。"
```

---

## Self-Review

**Spec coverage:** 单一需求 — 自定义 channel 从全局继承。

**Placeholder scan:** 无 TBD/TODO。

**Type consistency:** `_global_channels` 是 Pydantic `ChannelConfig` 对象，`getattr` 取值后走 `model_dump()` 转 dict，与现有 `channel_data` 类型一致。
