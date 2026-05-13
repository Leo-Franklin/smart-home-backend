# 后端代码组织重构设计

## 状态：已批准，待实现

## 目标

将后端代码从单文件/扁平结构重构为清晰的 DDD-lite 分层架构，解决 `main.py`（486行）和 `routers/` 内业务逻辑过重的问题，同时不引入完整 DDD 的过度复杂度。

---

## 目标目录结构

```
app/
├── __init__.py
├── main.py                      # 应用入口，路由注册，中间件（目标：≤120行）
├── config.py                    # 配置（不变）
├── deps.py                      # 依赖注入（不变）
├── auth.py                      # 认证（不变）
├── database.py                  # 数据库连接（不变）
├── desktop.py                   # 桌面模式（不变）
│
├── api/                        # HTTP 层（原 app/routers/ → app/api/）
│   ├── __init__.py
│   ├── devices.py               # 设备管理（瘦身后，仅 HTTP handler）
│   ├── cameras.py               # 摄像头管理（瘦身后）
│   ├── recordings.py             # 录像管理（瘦身后）
│   ├── schedules.py             # 调度管理
│   ├── members.py               # 成员管理
│   ├── dlna.py                 # DLNA管理
│   ├── analytics.py             # 数据分析
│   ├── system.py                # 系统信息
│   ├── user.py                  # 用户管理
│   └── ws.py                    # WebSocket（保持轻量）
│
├── domain/                     # 领域层（新增，原 app/models/ + app/services/ 迁移）
│   ├── __init__.py
│   ├── models/                  # SQLAlchemy 模型
│   │   ├── __init__.py
│   │   ├── device.py
│   │   ├── camera.py
│   │   ├── recording.py
│   │   ├── dlna_device.py
│   │   ├── member.py
│   │   ├── schedule.py
│   │   ├── device_online_log.py
│   │   └── user_settings.py
│   │
│   ├── services/                # 领域服务（原 app/services/ 迁移 + 新增）
│   │   ├── __init__.py
│   │   ├── scanner.py           # 设备扫描（迁移自 app/services/scanner.py）
│   │   ├── recorder.py          # 录制器（迁移自 app/services/recorder.py）
│   │   ├── nas_syncer.py        # NAS同步（迁移自 app/services/nas_syncer.py）
│   │   ├── presence_service.py  # 在场检测（迁移）
│   │   ├── camera_health.py     # 摄像头健康检查（迁移）
│   │   ├── scheduler_service.py # 调度服务（迁移）
│   │   ├── ws_manager.py        # WebSocket管理器（迁移）
│   │   ├── dlna_service.py     # DLNA服务（迁移）
│   │   ├── onvif_client.py     # ONVIF协议客户端（从 services 迁移）
│   │   └── recording_domain.py # 【新增】录制领域服务
│   │                              # 承载：录制完成→NAS同步→DB更新→DLNA投屏
│   │
│   └── repositories/            # 【新增】仓储层（接口定义）
│       ├── __init__.py
│       ├── device_repo.py
│       ├── camera_repo.py
│       ├── recording_repo.py
│       └── schedule_repo.py
│
├── schemas/                    # Pydantic 模型（保持不变）
│   ├── __init__.py
│   ├── device.py
│   ├── camera.py
│   ├── recording.py
│   ├── dlna.py
│   ├── member.py
│   ├── schedule.py
│   ├── user.py
│   └── common.py               # PagedResponse 等公共模型
│
└── infrastructure/            # 【新增】基础设施层
    ├── __init__.py
    └── persistence.py         # SQLAlchemy 会话管理补充
```

---

## 核心变化

### 1. `main.py` 瘦身（486行 → ≤120行）

**迁移到 `domain/services/recording_domain.py`：**
- `_on_recording_complete` → `RecordingDomainService.on_recording_complete()`
- `_cast_recording` → 内嵌于 `on_recording_complete()`
- `_on_recording_failed` → `RecordingDomainService.on_recording_failed()`

**迁移到 `domain/services/scheduler_service.py` 或新建 `presence_domain.py`：**
- `_make_scheduled_trigger()` → `SchedulerService.make_trigger()`
- `_auto_start_recording()` → `PresenceDomainService.auto_start_recording()`
- `_auto_stop_recording()` → `PresenceDomainService.auto_stop_recording()`

**瘦身后的 `main.py` 职责：**
- Loguru 配置
- 全局服务实例化（Recorder, NasSyncer, SchedulerService, PresenceService, WsManager, DLNAController）
- `lifespan` 管理：仅调用各服务的 start/stop，不含业务逻辑
- 路由注册
- 异常处理器
- 静态文件挂载

### 2. `routers/devices.py` 瘦身（376行 → ~100行）

**迁移到 `domain/services/scanner.py`：**
- `_enrich_device()`
- `_find_unknown_devices()`
- `_log_scan_result()`
- `_run_scan()`

**瘦身后仅保留：**
- `list_devices()` — HTTP handler
- `trigger_scan()` — 触发后台扫描
- `list_device_types()` — HTTP handler
- `get_topology()` — HTTP handler
- `device_heatmap()` — HTTP handler
- `get_device()` — HTTP handler
- `update_device()` — HTTP handler
- `delete_device()` — HTTP handler

### 3. 仓储层（Repositories）

定义接口以解耦业务逻辑与 ORM，便于未来扩展存储后端：

```python
# domain/repositories/device_repo.py
from typing import Protocol
from app.domain.models.device import Device

class DeviceRepository(Protocol):
    async def get_by_mac(self, mac: str) -> Device | None: ...
    async def list_all(self) -> list[Device]: ...
    async def upsert_batch(self, devices: list[Device]) -> None: ...
    async def mark_offline(self, exclude_macs: list[str]) -> int: ...
```

实现暂保留在 services 层（`domain/services/scanner.py` 等），待后续需要换存储后端时再迁移到 `infrastructure/persistence.py`。

### 4. 目录迁移

| 原路径 | 新路径 |
|--------|--------|
| `app/models/` | `app/domain/models/` |
| `app/services/` | `app/domain/services/` |
| `app/routers/` | `app/api/` |
| `app/services/onvif_client.py` | `app/domain/services/onvif_client.py` |

---

## 向后兼容策略

为减少测试和外部调用方的改动，通过 `app/domain/__init__.py` 和各子模块的 `__init__.py` 统一 re-export：

```python
# app/domain/models/__init__.py
from app.domain.models.device import Device
from app.domain.models.camera import Camera
# ... 其他模型
__all__ = ["Device", "Camera", ...]
```

外部 import 路径（如 `app.models.camera`）通过 alias 保持可用：

```python
# app/models/__init__.py（向后兼容桩）
from app.domain.models import *
```

同样处理 `app/services/` 和 `app/routers/`。

---

## TDD 实施计划

### 第一阶段：测试先行

为每个要迁移/新建的服务编写单元测试：

1. `test_recording_domain.py` — 测试 `RecordingDomainService.on_recording_complete()` 和 `on_recording_failed()`
2. `test_scanner_service.py` — 测试 `Scanner` 类的核心方法（mock 网络调用）
3. `test_scheduler_service.py` — 测试调度任务的添加/恢复逻辑

### 第二阶段：逐个迁移

按依赖顺序迁移：

1. `domain/services/recording_domain.py`（新建）
2. `domain/services/presence_domain.py`（新建）
3. 迁移 `app/services/*.py` → `app/domain/services/`
4. 迁移 `app/models/` → `app/domain/models/`
5. 迁移 `app/routers/` → `app/api/`

### 第三阶段：验证

- 运行全部现有测试，确保通过
- 清理旧的 re-export 桩代码
- 验证 `main.py` 行数 ≤120

---

## DDD-lite 说明

本设计采用"DDD-lite"策略：

- **引入**：
  - Domain Services：跨多个聚合的领域逻辑（`RecordingDomainService`）
  - Repository 接口：解耦 ORM
  - 清晰的领域层与 API 层边界

- **不引入**（当前规模不需要）：
  - 聚合根（Aggregate）
  - 领域事件（Domain Events）
  - 值对象（Value Objects）
  - 完整的 Repository 模式实现（接口与实现分离到不同层）

---

## 文件变更清单

### 新建
- `app/domain/services/recording_domain.py`
- `app/domain/services/presence_domain.py`
- `app/domain/repositories/__init__.py`
- `app/domain/repositories/device_repo.py`
- `app/domain/repositories/camera_repo.py`
- `app/domain/repositories/recording_repo.py`
- `app/domain/repositories/schedule_repo.py`
- `app/infrastructure/__init__.py`
- `app/infrastructure/persistence.py`

### 迁移（重命名）
- `app/models/` → `app/domain/models/`
- `app/services/` → `app/domain/services/`
- `app/routers/` → `app/api/`

### 修改
- `app/main.py`（瘦身）
- `app/api/devices.py`（瘦身）
- `app/api/cameras.py`（如有需要）
- `app/domain/__init__.py`（re-export 向后兼容）
- `app/models/__init__.py`（re-export 桩）
- `app/services/__init__.py`（re-export 桩）
- `app/routers/__init__.py`（re-export 桩）
- 全部测试文件的 import 路径调整

### 暂不改动
- `app/config.py`
- `app/deps.py`
- `app/auth.py`
- `app/database.py`
- `app/desktop.py`
- `app/schemas/`
