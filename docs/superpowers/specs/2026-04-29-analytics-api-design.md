# Analytics API Design

**Date:** 2026-04-29  
**Status:** Approved  
**Scope:** Add 7 analytics endpoints + 1 device heatmap endpoint to support the frontend `/analytics` page.

---

## Problem

The frontend analytics page (`AnalyticsView.vue`) calls 8 backend endpoints that all return 404. Four of them require historical device online/offline data that the current schema does not store.

---

## Solution Overview

Introduce a `device_online_log` table using hourly buckets. The scanner writes one upsert row per known device per scan cycle. Analytics endpoints query this table for history-dependent metrics; simpler endpoints query existing `Device` and `Recording` tables directly.

---

## Section 1: Data Model

**New file:** `app/models/device_online_log.py`

```
DeviceOnlineLog
  id           INT PK autoincrement
  mac          VARCHAR(17) NOT NULL  (indexed)
  bucket_hour  DATETIME NOT NULL     (indexed) — truncated to hour, e.g. 2024-01-15 14:00:00
  device_type  VARCHAR(32)           — denormalized from Device for join-free GROUP BY
  online_count INT default 0        — times device was seen online within this hour
  scan_count   INT default 0        — total scans that ran within this hour

  UNIQUE(mac, bucket_hour)
```

`uptime_pct = online_count / scan_count * 100`

`device_type` is denormalized to avoid joins on hot analytics read paths.

`init_db()` in `database.py` must import this model so `create_all` picks it up.

---

## Section 2: Scanner Integration

**Modified file:** `app/routers/devices.py` — `_run_scan()` function.

After the existing Device upsert block and `await db.commit()`:

1. Compute `bucket_hour = now.replace(minute=0, second=0, microsecond=0)`
2. Build `online_macs = {d["mac"] for d in enriched}`
3. Query all known devices: `SELECT mac, device_type FROM devices`
4. For each device, build a row: `online_count = 1 if mac in online_macs else 0`, `scan_count = 1`
5. Bulk upsert using `sqlalchemy.dialects.sqlite.insert().on_conflict_do_update()`:
   - conflict target: `(mac, bucket_hour)`
   - update: `online_count += excluded.online_count`, `scan_count += 1`

**Failure isolation:** wrap in `try/except`, log a warning on failure — must never break the scan result or `scan_completed` broadcast.

---

## Section 3: Analytics Endpoints

### New file: `app/routers/analytics.py`

Router prefix: `/analytics`  
All endpoints require `CurrentUser`.  
Range param `7d` / `30d` / `90d` → `since = datetime.now() - timedelta(days=N)`.

#### `GET /analytics/device-type-stats`
- Source: `Device` table
- Query: `SELECT device_type, COUNT(*) GROUP BY device_type`
- Response: `{"data": [{"type": "camera", "count": 3}, ...]}`

#### `GET /analytics/response-time`
- Source: `Device` table
- Query: `SELECT mac, alias, hostname, response_time_ms WHERE response_time_ms IS NOT NULL ORDER BY response_time_ms DESC`
- Response: `{"data": [{"mac": "...", "name": "...", "avg_ms": 42.5}, ...]}`
- `name` = `alias` if set, else `hostname`, else `mac`

#### `GET /analytics/recording-calendar?range=90d`
- Source: `Recording` table
- Query: `SELECT DATE(started_at), COUNT(*) WHERE started_at >= since GROUP BY DATE(started_at)`
- Response: `{"data": [{"date": "2024-01-15", "count": 3}, ...]}`

#### `GET /analytics/new-devices?range=90d&group_by=week`
- Source: `Device` table
- Query: `SELECT strftime('%Y-W%W', created_at), COUNT(*) WHERE created_at >= since GROUP BY week`
- Response: `{"data": [{"period": "2024-W03", "count": 5}, ...]}`
- Only `group_by=week` is supported (frontend always sends this value).

#### `GET /analytics/online-trend?range=7d|30d`
- Source: `DeviceOnlineLog`
- Query: For each `bucket_hour` in range, count distinct MACs where `online_count > 0`. Aggregate to one point per day (average of hourly counts).
- Response: `{"data": [{"timestamp": "2024-01-15T00:00:00", "count": 12}, ...]}`

#### `GET /analytics/device-stability?range=7d|30d`
- Source: `DeviceOnlineLog` joined with `Device`
- Query: `SELECT mac, SUM(online_count), SUM(scan_count) WHERE bucket_hour >= since GROUP BY mac`
- `uptime_pct = SUM(online_count) / SUM(scan_count) * 100` (guard division by zero)
- Join Device for `alias`/`hostname`; sort by `uptime_pct DESC`
- Response: `{"data": [{"mac": "...", "name": "...", "uptime_pct": 95.3}, ...]}`

#### `GET /analytics/type-activity?range=7d`
- Source: `DeviceOnlineLog`
- Query: `GROUP BY device_type, CAST(strftime('%H', bucket_hour) AS INT)` — compute fraction online per hour slot
- Pivot: one object per hour (0–23) with a key per device_type
- Response: `{"data": [{"hour": 14, "camera": 0.8, "phone": 0.5, "unknown": 0.3}, ...]}`

### Modified file: `app/routers/devices.py`

#### `GET /devices/heatmap?range=7d|30d&device_type=...`
- Source: `DeviceOnlineLog`
- Query: `GROUP BY strftime('%w', bucket_hour), strftime('%H', bucket_hour)` — count distinct MACs with `online_count > 0`
- Optional filter: `WHERE device_type IN (...)` when `device_type` param is supplied (comma-separated)
- Response: `{"cells": [{"day": 1, "hour": 14, "value": 8}, ...]}`

---

## Files Changed

| Action | File |
|---|---|
| Create | `app/models/device_online_log.py` |
| Create | `app/routers/analytics.py` |
| Modify | `app/routers/devices.py` — add `/heatmap` + log upsert in `_run_scan()` |
| Modify | `app/database.py` — import `device_online_log` in `init_db()` |
| Modify | `app/main.py` — `include_router(analytics.router)` |
