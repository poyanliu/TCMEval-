# TCMEval

中医药评价相关项目集合，包含三个独立子系统。

## 项目结构

| 目录 | 说明 | 技术栈 |
|------|------|--------|
| `web/` | 中医药政策文献智能评价系统 | Streamlit + FastAPI + GLM-4 |
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
