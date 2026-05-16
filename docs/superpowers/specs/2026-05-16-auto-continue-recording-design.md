# 自动续录设计

日期：2026-05-16

## 背景

当录制过程中一个视频 segment 正常结束时（FFmpeg 正常退出），如果用户还未主动停止录制（`Camera.is_recording == True`），应当自动开始下一个 segment，而不是等待用户重启。

## 规则

- segment 正常结束（retcode == 0）时，由 `_monitor_loop` 通过 callback 查询数据库 `Camera.is_recording`
- 若 `is_recording == True`：立即启动下一个 segment，保持录制连续性
- 若 `is_recording == False`（用户主动停止）：正常清理流程，`on_complete_cb` 执行 NAS 同步等后处理
- `is_recording = False` 只在用户主动调用 `stop_recording` 时设置，`on_complete_cb` 不再修改此标志

## 改动点

### Recorder（`app/services/recorder.py` + `app/domain/services/recorder.py`）

1. 新增 `should_continue_cb` 回调，签名 `async def (camera_mac: str) -> bool`
2. `_monitor_loop` 中，segment 正常结束时（retcode == 0）：
   - 调用 `should_continue_cb(mac)` 查询是否继续
   - 返回 `True`：执行续录逻辑（与 stalled 流程相同），**不**调用 `on_complete_cb`
   - 返回 `False`：走正常清理流程，调用 `on_complete_cb`
3. `set_callbacks` 新增 `should_continue` 参数

### RecordingDomainService（`app/domain/services/recording_domain.py`）

1. 新增 `should_continue_recording(camera_mac: str) -> bool` 方法
   - 查 `Camera.is_recording`，返回 `True` / `False`
2. `on_recording_complete` 中**移除** `cam.is_recording = False`（由 stop_recording 负责）
3. `stop_recording` 流程中保留 `cam.is_recording = False`

## 数据流（segment 正常结束）

```
_monitor_loop 检测 retcode == 0
  → should_continue_cb(camera_mac) 查 Camera.is_recording
    → True:  启动下一 segment（filename 含 seg{N}），不调用 on_complete_cb
    → False: 调用 on_complete_cb（NAS同步+DLNA），is_recording 保持 False
```

## 边界

- 用户点击「停止录制」→ `stop_recording` → 设置 `is_recording = False` → 下一 segment 结束时机检查到 `is_recording == False` → 正常清理
- 流中断（stalled）：依然走原来的 restart 逻辑，不受此改动影响