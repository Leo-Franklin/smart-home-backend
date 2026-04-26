# 智能家居后端

基于 FastAPI + SQLite + APScheduler 的智能家居管理后端，支持摄像头管理、录制调度、NAS 同步和设备扫描。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器
- ffmpeg（录制功能必需）

## 快速启动

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少修改以下字段：

| 变量 | 说明 |
|------|------|
| `JWT_SECRET_KEY` | 随机字符串，至少 32 位 |
| `ADMIN_USERNAME` | 管理员用户名 |
| `ADMIN_PASSWORD` | 管理员密码 |
| `CAMERA_ONVIF_PASSWORD` | 摄像头 ONVIF 密码 |
| `NETWORK_RANGE` | 要扫描的网段，如 `192.168.1.0/24` |

NAS 同步（可选）：

| 变量 | 说明 |
|------|------|
| `NAS_MODE` | `mount`（Docker 挂载）或 `smb`（网络推送） |
| `NAS_MOUNT_PATH` | mount 模式：容器内挂载路径 |
| `NAS_SMB_HOST` | smb 模式：NAS IP |
| `NAS_SMB_SHARE` | smb 模式：共享文件夹名 |
| `NAS_SMB_USER` | smb 用户名 |
| `NAS_SMB_PASSWORD` | smb 密码 |

### 3. 启动服务

**生产模式：**
```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**开发模式（热重载）：**
```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后访问：
- API 文档：http://localhost:8000/api/docs
- 健康检查：http://localhost:8000/api/v1/health
- 登录接口：`POST /api/v1/auth/login`（form-data: username / password）

### 4. 获取 JWT Token

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=admin&password=your_password"
```

## Docker 部署

```bash
docker build -t smart-home-backend .
docker run -d \
  --name smart-home-backend \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  smart-home-backend
```

## 注意事项

- **ffmpeg**：录制功能依赖 ffmpeg，需单独安装并加入 PATH。健康检查接口会显示 `ffmpeg: false` 表示未安装。
- **Scapy / nmap**：设备扫描在 Windows 上需要 WinPcap 或 Npcap（libpcap）；启动时若出现 `No libpcap provider available` 警告，扫描功能将降级。
- **NAS**：未配置 NAS 时，健康检查中 `nas_writable: false` 属正常现象，不影响其他功能。

## 项目结构

```
app/
├── main.py            # FastAPI 应用入口 & lifespan
├── config.py          # 环境变量配置（pydantic-settings）
├── database.py        # SQLAlchemy 异步引擎 & 初始化
├── auth.py            # JWT 签发 & 校验
├── deps.py            # FastAPI 依赖注入
├── models/            # SQLAlchemy ORM 模型
├── schemas/           # Pydantic 请求/响应 Schema
├── routers/           # API 路由（设备、摄像头、录制、调度、WebSocket）
└── services/          # 业务服务（录制、ONVIF、NAS、扫描、调度）
data/
├── smart_home.db      # SQLite 数据库（自动创建）
└── app.log            # 日志文件
```
