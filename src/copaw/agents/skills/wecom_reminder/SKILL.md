---
name: wecom-reminder
description: 当用户通过企业微信发送定时提醒类消息时使用本 skill。例如"3分钟后提醒我喝水"、"明天8点提醒我开会"。本 skill 解析时间表达式并调用 cron API 创建提醒任务。| Use when user requests a reminder via WeCom (e.g., "remind me to drink water in 3 minutes").
metadata: { "builtin_skill_version": "1.0", "copaw": { "emoji": "⏰", "requires": { "channels": ["wecom"] } } }
---

# WeCom Reminder Skill

## 什么时候用

用户通过企业微信发送需要未来定时执行的通知类消息时使用。

### 应该使用
- "3分钟后提醒我..."
- "明天8点提醒我..."
- "每天早上9点提醒我..."
- "每隔1小时提醒我..."

### 不应使用
- 只是现在要执行的任务
- 非提醒类的复杂对话

## 时间表达式解析

支持以下时间格式：

### 相对时间
- `Xm` - X分钟后 (e.g., "3分钟后")
- `Xh` - X小时后 (e.g., "2小时后")
- `Xd` - X天后 (e.g., "1天后")
- `XhYm` - X小时Y分钟后 (e.g., "1小时30分钟后")

### 绝对时间
- `明天Hh:mm` - 明天具体时间 (e.g., "明天8点" → "明天08:00")
- `今天Hh:mm` - 今天具体时间 (e.g., "今天20点" → "今天20:00")
- `YYYY-MM-DD HH:MM` - 具体日期时间

### 循环时间
- `every Xm` - 每X分钟
- `every Xh` - 每X小时
- `cron "0 9 * * *"` - 标准cron表达式

## 创建提醒流程

1. 解析用户消息中的时间和内容
2. 调用 cron API 创建任务
3. 返回确认信息给用户

## API 调用示例

```bash
# 创建一次性提醒 (3分钟后)
copaw cron create \
  --agent-id <agent_id> \
  --type text \
  --name "喝水提醒" \
  --at "2026-04-01T10:03:00Z" \
  --channel wecom \
  --target-user "LiuKang" \
  --target-session "wecom:LiuKang" \
  --text "该喝水了！💧"

# 创建循环提醒 (每小时)
copaw cron create \
  --agent-id <agent_id> \
  --type text \
  --name "喝水提醒" \
  --interval 3600 \
  --channel wecom \
  --target-user "LiuKang" \
  --target-session "wecom:LiuKang" \
  --text "该喝水了！💧"
```

## 解析示例

| 用户消息 | 解析结果 |
|----------|----------|
| "3分钟后提醒我喝水" | at: 3分钟后, text: "喝水" |
| "明天8点提醒我开会" | at: 明天08:00, text: "开会" |
| "每小时提醒我休息眼睛" | interval: 3600秒, text: "休息眼睛" |

## 错误处理

- 时间格式无法解析：询问用户重新表述
- 创建任务失败：返回错误信息
- 时间已过期：提醒用户并建议新时间
