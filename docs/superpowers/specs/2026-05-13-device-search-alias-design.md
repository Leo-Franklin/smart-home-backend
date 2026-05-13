# 设备列表搜索增加别名支持

**日期**: 2026-05-13
**类型**: 功能增强

## 背景

`GET /api/v1/devices` 的 `search` 参数当前支持按 `ip` 和 `mac` 模糊检索，`Device` 模型已有 `alias` 字段，但搜索功能未覆盖。

## 设计决策

- **匹配方式**: 模糊匹配（大小写不敏感，包含匹配）
- **搜索关系**: OR 条件，任意字段匹配即返回
- **响应标注**: 不增加 `matched_by` 字段

## 改动范围

`app/routers/devices.py` 第 41-45 行

## 改动内容

在现有搜索条件中追加 `alias` 的模糊匹配：

```python
if search:
    q = q.where(
        (Device.ip.contains(search)) |
        (Device.mac.ilike(f"%{search}%")) |
        (Device.alias.ilike(f"%{search}%"))
    )
```

## 验证

- `search="卧室"` 可匹配别名 "卧室空调"
- `search="5"` 可同时匹配 `ip` 包含 5 的设备和别名包含 5 的设备