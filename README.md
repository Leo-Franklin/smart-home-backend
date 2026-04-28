# 智能家居后端

基于 FastAPI + SQLite + APScheduler 的智能家居管理后端，支持摄像头管理、录制调度、NAS 同步、设备扫描、DLNA 投屏和家庭成员在线检测。

## 功能概览

| 模块 | 说明 |
|------|------|
| 设备管理 | 局域网设备扫描（Scapy + nmap），在线状态跟踪，分页查询 |
| 摄像头管理 | ONVIF 发现与配置，实时流地址，录制控制 |
| 录制调度 | ffmpeg 分段录制，APScheduler 定时任务，录制文件管理 |
| NAS 同步 | 本地存储 / Docker 挂载 / SMB 三种模式，录制完成自动同步 |
| DLNA 投屏 | SSDP 发现局域网 MediaRenderer，上传媒体文件并推送播放 |
| 成员在线检测 | 绑定成员与设备 MAC，轮询检测在线状态，Webhook 通知，记录出入日志 |
| WebSocket | 实时事件推送（扫描结果、录制状态、DLNA 发现、成员在线变化） |
| 认证 | JWT Bearer Token，单管理员账户 |

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

**必填：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JWT_SECRET_KEY` | — | 随机字符串，至少 32 位 |
| `ADMIN_USERNAME` | `admin` | 管理员用户名 |
| `ADMIN_PASSWORD` | — | 管理员密码，至少 8 位 |
| `CAMERA_ONVIF_PASSWORD` | — | 摄像头 ONVIF 密码 |
| `NETWORK_RANGE` | `auto` | 要扫描的网段，如 `192.168.1.0/24`；`auto` 自动推断 |

**录制（可选）：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RECORDING_TEMP_DIR` | `/tmp/recordings` | ffmpeg 临时输出目录 |
| `RECORDING_SEGMENT_SECONDS` | `1800` | 单段录制时长（秒） |
| `RECORDING_RETENTION_DAYS` | `30` | 录制文件保留天数 |

**NAS 存储（可选）：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NAS_MODE` | `local` | `local`（本地）/ `mount`（Docker 挂载）/ `smb`（网络推送） |
| `LOCAL_STORAGE_PATH` | `./data/recordings` | local 模式：本地保存路径 |
| `NAS_MOUNT_PATH` | `/nas/cameras` | mount 模式：容器内挂载路径 |
| `NAS_SMB_HOST` | — | smb 模式：NAS IP |
| `NAS_SMB_SHARE` | — | smb 模式：共享文件夹名 |
| `NAS_SMB_USER` | — | smb 用户名 |
| `NAS_SMB_PASSWORD` | — | smb 密码 |

**其他（可选）：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CORS_ALLOW_ORIGINS` | `http://localhost:5173` | 允许的跨域来源，多个用逗号分隔 |
| `SCAN_INTERVAL_SECONDS` | `60` | 设备自动扫描间隔（秒） |
| `PRESENCE_POLL_INTERVAL_SECONDS` | `30` | 成员在线检测轮询间隔（秒） |
| `LOG_LEVEL` | `INFO` | 日志级别 |

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

后续请求在 Header 中携带：`Authorization: Bearer <token>`

### 5. WebSocket 连接

```
ws://localhost:8000/api/v1/ws?token=<jwt_token>
```

服务端主动推送的事件类型：`scan_completed`、`recording_completed`、`recording_failed`、`dlna_discover_started`、`dlna_discover_completed`、`presence_changed`。

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
- **NAS**：未配置 NAS 时默认使用 `local` 模式，健康检查中 `nas_writable: false` 仅在写入测试失败时出现，不影响其他功能。
- **DLNA 媒体文件**：上传的媒体文件保存在 `data/dlna_media/`，TTL 为 1 小时，最大 500 MB；支持格式：`.mp4 .mkv .avi .mov .ts .mp3 .m4a .flac .wav .m3u8`。
- **成员 Webhook**：Webhook URL 必须使用 `https`，且不能指向内网 IP 地址。

## 项目结构

```
app/
├── main.py              # FastAPI 应用入口 & lifespan
├── config.py            # 环境变量配置（pydantic-settings）
├── database.py          # SQLAlchemy 异步引擎 & 初始化
├── auth.py              # JWT 签发 & 校验
├── deps.py              # FastAPI 依赖注入
├── models/              # SQLAlchemy ORM 模型
│   ├── device.py        # 网络设备
│   ├── camera.py        # 摄像头
│   ├── recording.py     # 录制记录
│   ├── schedule.py      # 录制调度
│   ├── member.py        # 家庭成员 & 在线日志
│   └── dlna_device.py   # DLNA 设备
├── schemas/             # Pydantic 请求/响应 Schema
├── routers/             # API 路由
│   ├── system.py        # 健康检查 & 认证
│   ├── devices.py       # 设备管理 & 扫描
│   ├── cameras.py       # 摄像头管理
│   ├── recordings.py    # 录制记录
│   ├── schedules.py     # 录制调度
│   ├── members.py       # 家庭成员 & 在线检测
│   ├── dlna.py          # DLNA 发现 & 投屏
│   └── ws.py            # WebSocket 实时推送
└── services/            # 业务服务
    ├── recorder.py      # ffmpeg 录制任务
    ├── onvif_client.py  # ONVIF 摄像头控制
    ├── nas_syncer.py    # NAS/本地存储同步
    ├── scanner.py       # 局域网设备扫描
    ├── scheduler_service.py  # APScheduler 封装
    ├── presence_service.py   # 成员在线检测
    ├── dlna_service.py  # DLNA/SSDP 控制
    └── ws_manager.py    # WebSocket 连接管理
data/
├── smart_home.db      # SQLite 数据库（自动创建）
└── app.log            # 日志文件
```
