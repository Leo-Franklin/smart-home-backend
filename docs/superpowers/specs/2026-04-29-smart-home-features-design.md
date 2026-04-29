# 智能家居后端功能扩展设计文档

**日期：** 2026-04-29
**优先级顺序：** Phase A（联动自动化）→ Phase B（实用增强）→ Phase C（数据洞察）
**推进策略：** 串行，每阶段完整交付后再进入下一阶段

---

## 背景

当前系统已实现：设备扫描、ONVIF 摄像头管理、ffmpeg 录制调度、NAS 同步、DLNA 投屏、成员在线检测（ping + webhook）、WebSocket 实时推送。各模块通过 `ws_manager.broadcast()` 发事件，但模块间没有联动消费。本次扩展在已有事件节点上叠加响应层，逐步打通模块间协作。

---

## Phase A — 联动自动化

### 核心思路

在现有事件总线上叠联动层，不引入新的消息队列或框架：

```
[scanner]          ──► scan_completed       ──► [A2] 陌生设备检测
[presence_service] ──► member_arrived/left  ──► [A1] 自动触发录制
[recorder]         ──► recording_completed  ──► [A4] 自动投屏
[camera_health]    ──► camera_offline       ──► [A3] 摄像头掉线告警
```

---

### A1 — 成员到家/离家自动触发录制

**数据变更：**
- `Member` 表新增 `auto_record_cameras: JSON`（摄像头 MAC 列表，默认 `[]`）
- `MemberCreate` / `MemberUpdate` schema 同步增加该字段

**逻辑变更（`presence_service._fire_event`）：**
- `arrived` → 对 `auto_record_cameras` 列表内每个 camera_mac 调 `recorder.start_recording()`，已在录制则跳过
- `left` → 检查该摄像头是否还有其他在家成员绑定；若无则调 `recorder.stop_recording()`

**依赖注入：**
- `recorder` 是 `app.state` 单例，在 `PresenceService.start()` 时通过参数传入引用，避免循环 import

**边界处理：**
- 摄像头无 RTSP URL → 记 warning 日志，跳过，不阻断其他摄像头
- 多成员共享同一摄像头：arrived 时重复 start 因 `is_recording` 判断静默跳过；left 时只有所有绑定成员均离家才 stop

---

### A2 — 陌生设备入网告警

**数据变更：** 无

**逻辑变更（`scanner.py` 扫描完成后）：**
- 对每个新发现的 MAC 查询 `MemberDevice` 表
- 若不在任何成员设备列表 → `ws_manager.broadcast("unknown_device_detected", {mac, ip, hostname, first_seen})`

**去重策略：**
- 仅对"本次扫描首次出现"或"距上次 `last_seen` 超过 24 小时"的陌生设备告警，避免每轮扫描噪音

---

### A3 — 摄像头掉线检测

**数据变更：**
- `Camera` 表新增 `is_online: bool`（默认 `True`）、`last_probe_at: datetime`

**新增模块：** `services/camera_health.py`
- 类 `CameraHealthChecker`，结构与 `PresenceService` 相同（asyncio loop）
- 探测方式：`ffprobe -v quiet -show_entries format=duration -i {rtsp_url}`，超时 5s
- 状态变化时推 `camera_online` / `camera_offline` WS 事件
- 轮询间隔由新增配置 `CAMERA_HEALTH_INTERVAL_SECONDS`（默认 60）控制
- 在 `app/main.py` lifespan 中启动 / 停止

---

### A4 — 录制完成后自动投屏

**数据变更：**
- `Camera` 表新增 `auto_cast_dlna: str | None`（目标 DLNA 设备 UDN，默认 `None`）

**逻辑变更：**
- `recorder.py` 的分段录制完成回调 / NAS sync 完成后，检查 `camera.auto_cast_dlna`
- 若已配置目标 UDN → 调 `dlna_service.cast(udn, file_path)`
- recorder 通过 `AsyncSessionLocal` 查询 Camera 配置（与现有用法一致）

---

## Phase B — 实用增强

### B1 — 摄像头实时截图

**新增接口：** `POST /cameras/{mac}/snapshot`

**实现：**
- 调用 `ffmpeg -rtsp_transport tcp -i {rtsp_url} -frames:v 1 -f image2 pipe:1`，stdout pipe 到内存
- 返回 `StreamingResponse(content=jpeg_bytes, media_type="image/jpeg")`
- 超时 8s，超时后强制 kill ffmpeg 进程，返回 503
- 不落磁盘，全内存处理

**边界：**
- 摄像头未配置 RTSP URL → 422
- ffmpeg 输出为空（摄像头无信号）→ 503

---

### B2 — RTSP → HLS 直播转码

**新增接口：**
- `POST /cameras/{mac}/live/start` — 启动转码进程
- `DELETE /cameras/{mac}/live/stop` — 停止转码进程
- `GET /cameras/{mac}/live/index.m3u8` — 返回播放列表（重定向到静态文件）

**新增模块：** `services/hls_streamer.py`
- 类 `HlsStreamer`，管理每个 `camera_mac` 对应的 ffmpeg 进程（`active: dict[str, Process]`），挂到 `app.state`
- ffmpeg 命令：
  ```
  ffmpeg -rtsp_transport tcp -i {rtsp_url}
         -c:v copy -c:a aac
         -f hls -hls_time 2 -hls_list_size 5 -hls_flags delete_segments
         data/hls/{mac}/index.m3u8
  ```
- HLS 文件写到 `data/hls/{mac}/`，`delete_segments` flag 自动滚动清理旧 .ts 片段
- 进程崩溃时推 `camera_live_failed` WS 事件

**静态文件：** `app.mount("/hls", StaticFiles(directory="data/hls"))` 挂载，前端直接访问 .m3u8 / .ts

---

### B3 — 丰富推送渠道

**新增配置（`config.py`）：**
```
TELEGRAM_BOT_TOKEN: str = ""
TELEGRAM_CHAT_ID: str = ""
WECOM_WEBHOOK_URL: str = ""   # 企业微信机器人，出站调用，校验域名为 qyapi.weixin.qq.com
```

**新增模块：** `services/notifier.py`
```python
class Notifier:
    async def send(self, title: str, body: str, extra: dict = {}) -> None:
        # 并发调 Telegram / WeCom / 已有 member webhook
```

**接入点：**
- `unknown_device_detected` → `Notifier.send("陌生设备入网", ...)`
- `camera_offline` → `Notifier.send("摄像头掉线", ...)`
- `member_arrived / member_left` → 通过 Notifier 多渠道推送（替代现有单 webhook）

**安全说明：**
- Telegram 调用走 `api.telegram.org`，出站请求，无 SSRF 风险
- WeCom Webhook URL 在 notifier 内部校验域名为 `qyapi.weixin.qq.com`，不复用 SSRF 校验逻辑

---

### B4 — 录制文件浏览器内播放

**新增接口：** `GET /recordings/{id}/hls/index.m3u8`

**实现：**
- 请求到来时检查 `data/hls_cache/{recording_id}/index.m3u8` 是否已存在
- 不存在 → 后台起 ffmpeg 转码（one-shot，转完即退出），返回 `202 {"status": "converting"}`
- 转码完成后推 `recording_hls_ready` WS 事件；再次请求重定向到静态文件
- 缓存 TTL 1 小时，到期删除目录

**与 B2 区别：** B2 是实时流（ffmpeg 持续运行）；B4 是离线文件按需转码（one-shot）

---

## Phase C — 数据洞察

### 整体原则

全部只读接口，不引入新后台任务，不改动现有表结构（C4 有一处例外）。所有接口走现有 JWT 认证。

---

### C1 — 成员在家时长统计

**新增接口：** `GET /members/{id}/stats?range=7d|30d|custom&start=&end=`

**实现：** 查 `PresenceLog`，将 `arrived` / `left` 事件配对计算每段在家时长，按自然日聚合。当天仍在家（无配对 left）→ 以 `now` 作为结束时间。

**响应结构：**
```json
{
  "member_id": 1,
  "range_days": 7,
  "total_minutes": 2340,
  "daily": [
    {"date": "2026-04-23", "minutes": 360}
  ]
}
```

---

### C2 — 录制统计汇总

**新增接口：** `GET /recordings/stats?range=7d|30d&camera_mac=`

**实现：** 聚合 `Recording` 表（`status = completed`），GROUP BY `camera_mac` + 日期。

**响应结构：**
```json
{
  "cameras": [
    {
      "camera_mac": "aa:bb:cc:dd:ee:ff",
      "count": 12,
      "total_duration_seconds": 43200,
      "total_size_bytes": 1073741824
    }
  ],
  "daily": [
    {"date": "2026-04-23", "count": 3, "duration_seconds": 10800}
  ]
}
```

---

### C3 — 仪表板总览接口

**新增接口：** `GET /dashboard`

**实现：** 并发查各表，单次请求返回全局快照。

**响应结构：**
```json
{
  "members_home": 2,
  "members_total": 3,
  "cameras_recording": 1,
  "cameras_online": 3,
  "cameras_total": 3,
  "devices_online": 14,
  "devices_total": 21,
  "recordings_today_count": 4,
  "recordings_today_duration_seconds": 14400,
  "unknown_devices_today": 1
}
```

---

### C4 — 设备活跃时段热力图

**数据变更（唯一例外）：**
- 新增 `device_ping_log` 表：`(id, mac, pinged_at)`
- 由 `PresenceService._ping_ip` 成功时写入
- 启动时清理 30 天前的旧记录

**新增接口：** `GET /devices/heatmap?range=7d|30d`

**响应结构：** 24×7 矩阵（维度：星期 × 小时），值为该时段 ping 成功次数
```json
{
  "matrix": [[0, 0, 3, ...], ...],
  "range_days": 7
}
```

---

## 文件变更汇总

### Phase A
| 文件 | 变更类型 |
|------|----------|
| `app/models/member.py` | 新增 `auto_record_cameras` 字段 |
| `app/schemas/member.py` | 新增字段 |
| `app/models/camera.py` | 新增 `is_online`, `last_probe_at`, `auto_cast_dlna` |
| `app/schemas/camera.py` | 新增字段 |
| `app/services/presence_service.py` | `_fire_event` 增加录制联动；注入 recorder 引用 |
| `app/services/scanner.py` | 扫描后增加陌生设备检测逻辑 |
| `app/services/camera_health.py` | 新建，摄像头掉线检测服务 |
| `app/config.py` | 新增 `CAMERA_HEALTH_INTERVAL_SECONDS` |
| `app/main.py` | lifespan 启动/停止 `CameraHealthChecker` |

### Phase B
| 文件 | 变更类型 |
|------|----------|
| `app/routers/cameras.py` | 新增 snapshot、live start/stop 接口 |
| `app/services/hls_streamer.py` | 新建，HLS 转码进程管理 |
| `app/services/notifier.py` | 新建，多渠道推送 |
| `app/config.py` | 新增 Telegram / WeCom 配置项 |
| `app/main.py` | 挂载 `/hls` 静态文件目录；注入 HlsStreamer |
| `app/routers/recordings.py` | 新增 HLS 转码缓存接口 |

### Phase C
| 文件 | 变更类型 |
|------|----------|
| `app/routers/members.py` | 新增 `/stats` 接口 |
| `app/routers/recordings.py` | 新增 `/stats` 接口 |
| `app/routers/system.py` | 新增 `/dashboard` 接口 |
| `app/routers/devices.py` | 新增 `/heatmap` 接口 |
| `app/models/device.py` | 新增 `device_ping_log` 表（C4） |
| `app/services/presence_service.py` | `_ping_ip` 成功时写入 ping log（C4） |
