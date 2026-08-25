# TCMEval

中医药评价相关项目集合，包含三个独立子系统。

## 项目结构

| 目录 | 说明 | 技术栈 |
|------|------|--------|
| `web/` | 中医药政策文献智能评价系统 | HTML + FastAPI + GLM-4 |
| `ecoeval/` | 中医治未病卫生经济学综合评价系统 | Flask + DeepSeek + SQLite |
| `questionnaire/` | 问卷项目 | Flask + HTML |

## 快速启动

详见各项目目录内的启动说明（`web/启动指南.md` 等）。

## 环境变量

各项目通过 `.env` 文件加载敏感配置（API Key 等），
仓库内提供 `.env.example` 模板，复制并填入真实值即可：

```bash
cp .env.example .env
# 编辑 .env 填入真实 API Key
```

> 注意：`.env` 与 `*.db` 已加入 `.gitignore`，不会被提交。

## 版本改动

### v1.1.0 — 2026-08-25：Streamlit 前端替换为 HTML

**web/（中医药政策文献智能评价系统）**
- 用轻量 HTML 单页前端（`frontend/index.html`，Chart.js 可视化）替换原 Streamlit 前端，解决页面加载缓慢问题
- 复用现成 FastAPI 后端（端口 8000），新增登录接口 `backend/routers/auth.py`
- 新增历史记录报告下载接口 `GET /api/history/{id}/report`
- 修复报告下载中文文件名 latin-1 编码报错（改用 RFC 5987 URL 编码）
- 前端支持：登录、单文件/批量评价、总分仪表盘 + 雷达图 + 柱状图、二级指标得分卡片、历史记录、优秀文献展馆、报告下载（docx/pdf/txt）

**ecoeval/（卫生经济学综合评价系统）**
- 移除 `app.py` 中硬编码的 DeepSeek API Key，改为从 `.env` 读取
- 新增 `.env.example` 模板

### v1.0.0 — 2026-08-13：初始提交

- `web/`：中医药政策文献智能评价系统（原 Streamlit 版）
- `ecoeval/`：中医治未病卫生经济学综合评价系统
- `questionnaire/`：问卷项目
