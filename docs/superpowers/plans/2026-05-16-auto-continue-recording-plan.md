# 自动续录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 segment 正常结束时，由 `_monitor_loop` 通过 callback 查询 `Camera.is_recording` 决定是否自动续录下一个 segment。

**Architecture:** 在 `Recorder` 新增 `should_continue_cb` 回调，在 `_monitor_loop` 检测 segment 正常结束后调用此 callback 查询 `is_recording` 状态；`RecordingDomainService` 提供该回调实现并移除 `on_recording_complete` 中对 `is_recording` 的修改。

**Tech Stack:** Python asyncio, SQLite/SQLAlchemy, ffmpeg subprocess

---

## 文件变更概览

| 文件 | 改动 |
|------|------|
| `app/services/recorder.py` | 新增 `should_continue_cb` + 修改 `_monitor_loop` 逻辑 |
| `app/domain/services/recorder.py` | 同上（与上方完全相同的改动） |
| `app/domain/services/recording_domain.py` | 新增 `should_continue_recording` 方法 + 移除 `on_recording_complete` 中的 `is_recording = False` |
| `app/main.py` | `set_callbacks` 调用追加 `should_continue` 参数 |

---

## Task 1: `app/domain/services/recorder.py` — 新增 `should_continue_cb` 并改造 `_monitor_loop`

**文件:** `app/domain/services/recorder.py`

- [ ] **Step 1: 添加 `should_continue_cb` 到 `__init__` 和 `set_callbacks`**

在 `Recorder.__init__` 中添加 `self._should_continue_cb = None`，`set_callbacks` 签名改为 `set_callbacks(self, on_complete=None, on_failed=None, should_continue=None)`，保存到 `self._should_continue_cb`。

- [ ] **Step 2: 修改 `_monitor_loop` 中 `retcode == 0` 的处理逻辑**

在 `# Handle normally finished` 块中，找到:
```python
if retcode == 0:
    logger.info(f"录制正常完成: {mac}")
    if self._on_complete_cb:
        await self._on_complete_cb(task)
```

改为:
```python
if retcode == 0:
    logger.info(f"录制正常完成: {mac}")
    # 自动续录判断
    if self._should_continue_cb and await self._should_continue_cb(mac):
        # 启动下一 segment，不调用 on_complete_cb
        next_index = task.segment_index + 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_mac = mac.replace(":", "")
        seg_path = self.temp_dir / f"{safe_mac}_{ts}_seg{next_index}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-rtsp_transport", "tcp",
            "-i", task.rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-t", str(task.segment_seconds),
            "-movflags", "+frag_keyframe+empty_moov",
            str(seg_path),
        ]
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE),
        )
        new_task = RecordingTask(
            camera_mac=mac,
            process=proc,
            output_path=seg_path,
            started_at=datetime.now(),
            segment_seconds=task.segment_seconds,
            rtsp_url=task.rtsp_url,
            recording_id=task.recording_id,
            last_bytes=0,
            last_check=None,
            segment_index=next_index,
        )
        self.active[mac] = new_task
        logger.info(f"[{mac}] 自动续录 segment {next_index}: {seg_path}")
    else:
        if self._on_complete_cb:
            await self._on_complete_cb(task)
```

**注意:** 这段续录逻辑与 stalled 处理中的重启逻辑基本相同，区别在于从 `task.recording_id` 而非 `None` 开始新 segment。

- [ ] **Step 3: Commit**

```bash
git add app/domain/services/recorder.py
git commit -m "feat(recorder): add should_continue_cb for auto-continue on segment complete"
```

---

## Task 2: `app/services/recorder.py` — 同上改动

**文件:** `app/services/recorder.py`（与 Task 1 完全相同的改动）

- [ ] **Step 1: 添加 `should_continue_cb` 到 `__init__` 和 `set_callbacks`**
- [ ] **Step 2: 修改 `_monitor_loop` 中 `retcode == 0` 的处理逻辑**（与 Task 1 Step 2 完全相同）
- [ ] **Step 3: Commit**

```bash
git add app/services/recorder.py
git commit -m "feat(recorder): add should_continue_cb for auto-continue on segment complete"
```

---

## Task 3: `app/domain/services/recording_domain.py` — 新增 `should_continue_recording` 方法并移除 `is_recording = False`

**文件:** `app/domain/services/recording_domain.py`

- [ ] **Step 1: 新增 `should_continue_recording` 方法**

在类中添加:
```python
async def should_continue_recording(self, camera_mac: str) -> bool:
    async with AsyncSessionLocal() as db:
        cam = (await db.execute(select(Camera).where(Camera.device_mac == camera_mac))).scalar_one_or_none()
        return cam.is_recording if cam else False
```

- [ ] **Step 2: 移除 `on_recording_complete` 中的 `cam.is_recording = False`**

找到 `on_recording_complete` 方法中 `if cam: cam.is_recording = False` 这一行（以及对应 await db.commit 前的同样一行），删除这两处 `is_recording = False`。

- [ ] **Step 3: Commit**

```bash
git add app/domain/services/recording_domain.py
git commit -m "feat(recording_domain): add should_continue_recording, stop setting is_recording=False on complete"
```

---

## Task 4: `app/main.py` — 追加 `should_continue` 参数到 `set_callbacks`

**文件:** `app/main.py:40`

- [ ] **Step 1: 更新 `set_callbacks` 调用**

第 40 行当前是:
```python
recorder.set_callbacks(on_complete=lambda t: recording_domain.on_recording_complete(t), on_failed=lambda t, rc, err: recording_domain.on_recording_failed(t, rc, err))
```

改为:
```python
recorder.set_callbacks(
    on_complete=lambda t: recording_domain.on_recording_complete(t),
    on_failed=lambda t, rc, err: recording_domain.on_recording_failed(t, rc, err),
    should_continue=lambda mac: recording_domain.should_continue_recording(mac),
)
```

- [ ] **Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat(main): wire should_continue callback into recorder"
```

---

## Spec 覆盖检查

| Spec 要求 | 对应任务 |
|-----------|---------|
| `should_continue_cb` 回调签名 `async def (camera_mac) -> bool` | Task 1 Step 1, Task 2 Step 1 |
| `_monitor_loop` 中 retcode==0 时调用 callback 查询 is_recording | Task 1 Step 2, Task 2 Step 2 |
| `should_continue_cb` 返回 True 则启动下一 segment | Task 1 Step 2, Task 2 Step 2 |
| `should_continue_cb` 返回 False 则调用 on_complete_cb | Task 1 Step 2, Task 2 Step 2 |
| `RecordingDomainService.should_continue_recording` 方法 | Task 3 Step 1 |
| 移除 `on_recording_complete` 中的 `is_recording = False` | Task 3 Step 2 |
| `stop_recording` 流程保留 `is_recording = False` | 无需改动（已在 domain service 中） |
| main.py 接入 callback | Task 4 |

---

## 类型一致性检查

- `should_continue_cb(mac)` — `mac` 类型 `str`，与 `RecordingTask.camera_mac` 一致 ✓
- `should_continue_recording(camera_mac: str) -> bool` — 签名与 callback 匹配 ✓
- Task 1 Step 2 中新 segment 的 `recording_id=task.recording_id` 保持连续性 ✓