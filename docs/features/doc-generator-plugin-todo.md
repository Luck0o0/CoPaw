# 文档生成插件 (doc-generator-plugin) — Features TODO

## 背景

worker_file 每次生成 PDF 都要让 LLM 从零写 Python 脚本（markdown→PDF），然后通过 `execute_shell_command` 执行。当前 workspace 积累了 14+ 个重复脚本，4294 行代码，每个脚本逻辑高度相似（只是配色不同）。

单次 PDF 生成耗时 ~170s，其中 LLM 写脚本和 shell 执行占了绝大部分。

## 目标

开发一个 QwenPaw 插件 `doc-generator`，统一处理常见文档格式的生成，封装为工具函数。agent 直接调用函数传参数，不再需要写脚本→跑 shell。

---

## Phase 1: PDF 生成 (优先)

### 1.1 markdown → PDF
- [ ] 输入: markdown 文本或 .md 文件路径
- [ ] 输出: A4 纵向 PDF，中文排版
- [ ] 功能:
  - [ ] 自定义配色方案（通过参数传入主色/辅色）
  - [ ] 表格斑马纹（自动检测表格并应用交替行色）
  - [ ] 自定义标题、页脚
  - [ ] 中文字体内置（无需每次扫描系统字体）
- [ ] 技术路线: weasyprint (HTML→PDF) 或 reportlab

### 1.2 HTML → PDF
- [ ] 输入: HTML 字符串或 .html 文件路径
- [ ] 输出: PDF 文件
- [ ] 功能: 自定义页面大小、边距

### 1.3 合并/拼接 PDF
- [ ] 输入: 多个 PDF 文件路径
- [ ] 输出: 合并后的单个 PDF
- [ ] 使用场景: 多个章节分别生成后合并

---

## Phase 2: DOCX 生成

### 2.1 markdown → DOCX
- [ ] 输入: markdown 文本或 .md 文件路径
- [ ] 输出: .docx 文件
- [ ] 功能: 保留标题层级、表格、图片引用

### 2.2 模板 → DOCX
- [ ] 输入: 模板文件 + 数据字典
- [ ] 输出: 填充后的 .docx
- [ ] 使用场景: 合同、报告、简历等固定格式文档

---

## Phase 3: PPT 生成

### 3.1 markdown → PPT
- [ ] 输入: markdown（用 `---` 或 `##` 分隔幻灯片）
- [ ] 输出: .pptx 文件
- [ ] 功能: 支持标题页、内容页、图片页、表格页

### 3.2 模板 → PPT
- [ ] 输入: 模板 + 内容数据
- [ ] 输出: 填充后的 .pptx

---

## Phase 4: XLSX 生成

### 4.1 表格数据 → XLSX
- [ ] 输入: CSV/JSON 数据
- [ ] 输出: .xlsx 文件
- [ ] 功能: 自定义样式、多 sheet、图表

---

## 通用要求

- [ ] **插件格式**: 遵循 QwenPaw 插件规范（参考 qwen-image 结构）
- [ ] **工具注册**: 每个格式一个工具函数 (generate_pdf / generate_docx / generate_pptx / generate_xlsx)
- [ ] **字体预置**: 内置常用中文字体路径配置，不再依赖运行时字体扫描
- [ ] **错误处理**: 统一的错误返回格式，不诱导 LLM 重试
- [ ] **流式返回**: 大文件支持进度回调
- [ ] **配置化**: API keys、字体路径等通过 plugin.json config_fields 暴露
