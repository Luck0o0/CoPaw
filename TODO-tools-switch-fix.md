# Tools 页面 Switch 组件修复方案

## 问题

`console/src/pages/Agent/Tools/index.tsx` 第 228-229 行：

```tsx
<Switch
  checked={hasEnabledTools && !hasDisabledTools}
  onChange={() => (hasDisabledTools ? enableAll() : disableAll())}
```

Switch 在混合状态（部分启用、部分禁用）时行为不一致：

| 工具状态   | Switch 显示    | 点击执行      | 用户预期   |
|-----------|---------------|--------------|-----------|
| 全部启用   | ON  "启用全部"  | `disableAll` ✓ | 禁用全部    |
| 混合      | OFF "禁用全部"  | `enableAll`  ✗ | 禁用全部    |
| 全部禁用   | OFF "禁用全部"  | `enableAll`  ✓ | 启用全部    |

当用户看到 "禁用全部" 标签并点击时，如果当前是混合状态，实际执行的是 `enableAll()`，与标签含义相反。

## 根因

`onChange` 用 `hasDisabledTools` 推测用户意图，而不是根据 Switch 的新位置（checked → unchecked）来决定动作。

## 修复

将 `onChange` 改为接收 Switch 的 `checked` 值，根据切换后的目标状态执行对应操作：

```tsx
<Switch
  checked={hasEnabledTools && !hasDisabledTools}
  onChange={(checked) => (checked ? enableAll() : disableAll())}
```

- `checked === true`（用户把开关拨到 ON）→ `enableAll()` 启用全部
- `checked === false`（用户把开关拨到 OFF）→ `disableAll()` 禁用全部

## 影响范围

- 文件：`console/src/pages/Agent/Tools/index.tsx` 第 229 行
- 改动：1 行，`onChange={() => ...}` → `onChange={(checked) => ...}`
- 不影响其他功能
