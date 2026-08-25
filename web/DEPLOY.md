# 中医药政策文献智能评价系统 — 部署手册

## 系统概述

Streamlit + FastAPI + Nginx 三服务 Web 应用。推理使用智谱 GLM-4 云端 API，**无需 GPU**。域名：**tcmeval.cn**。

| 组件 | 端口 | 说明 |
|------|------|------|
| Nginx | 80, 443 | 反向代理 + HTTPS 终止 |
| FastAPI | 8000 | 后端 API（评价、报告、历史） |
| Streamlit | 6006 | 前端界面（/literature 子路径） |

---

## 环境要求

- Ubuntu 20.04+ / Debian 11+
- Docker ≥ 20.10 + Docker Compose ≥ 2.0
- 公网 IP，80/443 端口可达
- 域名 tcmeval.cn 已解析到服务器 IP

---

## 一键部署

```bash
cd /root/web
chmod +x setup.sh
./setup.sh
```

脚本自动完成：系统检查 → 镜像构建 → SSL 证书 → 启动服务 → 冒烟测试。

---

## 手动部署

### 1. 配置环境变量

编辑 `.env`：

```
ZHIPUAI_API_KEY=你的智谱AI密钥
ZHIPUAI_MODEL=glm-4-flash
```

### 2. 构建镜像

```bash
docker compose build api streamlit
```

### 3. 申请 SSL 证书

```bash
# 先以 HTTP 模式启动 Nginx
docker compose --profile production up -d nginx

# 申请证书（确保域名已解析到本机）
docker compose --profile production run --rm certbot \
  certonly --webroot --webroot-path=/var/www/certbot \
  -d tcmeval.cn -d www.tcmeval.cn \
  --email 你的邮箱 --agree-tos --no-eff-email

# 重启 Nginx 加载证书
docker compose --profile production restart nginx
```

### 4. 启动全栈

```bash
docker compose --profile production up -d
```

### 5. 验证

```bash
curl https://tcmeval.cn/health
```

---

## 常用运维命令

```bash
# 查看服务状态
docker compose --profile production ps

# 查看日志
docker compose --profile production logs -f --tail=50

# 仅重启某个服务
docker compose restart api

# 更新代码后重建
git pull
docker compose build api streamlit
docker compose --profile production up -d --force-recreate api streamlit

# 停止系统
docker compose --profile production down
```

---

## 目录结构

```
/root/web/
├── setup.sh              # 一键部署脚本
├── docker-compose.yml    # 三服务编排（纯CPU，无GPU）
├── Dockerfile            # python:3.10-slim 基础镜像
├── nginx.conf            # Nginx 反向代理（HTTPS + rate limit）
├── entrypoint.sh         # 容器启动分发（api/streamlit/both）
├── .env                  # 环境变量（API Key，勿提交Git）
├── streamlit_app.py      # Streamlit 前端入口
├── backend/              # FastAPI 后端
├── shared/               # 共享模块（constants, types）
├── data/                 # 运行时数据（SQLite数据库, 历史记录）
└── scripts/              # 辅助脚本
```

---

## 备份建议

```bash
# crontab — 每天凌晨 2 点备份 data 目录
0 2 * * * tar czf /backup/tcm-$(date +\%Y\%m\%d).tar.gz /root/web/data/
```

---

## 与 AutoDL 版本的区别

| | AutoDL 版 | 云服务器版 |
|------|------|------|
| 基础镜像 | nvidia/cuda:12.8.0 | python:3.10-slim |
| GPU | 需要 NVIDIA 显卡 | 不需要 |
| 镜像体积 | ~8 GB | ~1 GB |
| 本地模型 | glm-4-9b-chat（备选） | 已移除，纯API |
| 数据库路径 | /root/autodl-tmp | data/ 目录 |
| 部署方式 | 手动启动 | setup.sh 一键部署 |

---

## 常见问题

**Q: 启动后访问域名被拒绝？**
检查防火墙：`ufw allow 80/tcp && ufw allow 443/tcp`，以及云服务商安全组是否放行 80/443。

**Q: SSL 证书申请失败？**
确认域名 DNS 已解析到本机 IP，且 80 端口可从公网访问。用 `curl http://tcmeval.cn/.well-known/acme-challenge/` 测试。
