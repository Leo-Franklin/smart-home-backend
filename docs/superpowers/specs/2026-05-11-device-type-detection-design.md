# 设备类型识别增强设计

## 目标

将设备类型识别准确率从当前"仅依赖 hostname + vendor OUI + 基础端口"提升到"端口 + HTTP Banner + hostname + vendor 多维度综合判断"。

## 现状问题

- 荣耀手机被识别为 Unknown（vendor 关键词匹配不完整）
- TP-Link 摄像头被识别为 Router（vendor 命中后直接返回，未检查摄像头端口）
- 国产 IoT 设备（Tuya/Broadlink/Yeelight 等）识别率低
- HTTP 端口未利用，很多设备 Web 管理界面能暴露真实类型

## 改动一：调整判断顺序

**当前顺序：** 端口检测 → hostname → vendor（fallback）

**调整后：** 端口检测 → HTTP Banner → hostname → vendor（最低优先级 fallback）

摄像头端口检测（554/2020/8000）保持最高优先级，但扩大端口覆盖：
- 新增 `8080`（海康/大华 Web 管理）
- 新增 `8443`（部分摄像头 HTTPS）
- 新增 `8554`（RTSP alternate）
- 新增 `5000`（通用 ONVIF）

## 改动二：补全厂商与关键词

**荣耀手机：**
- vendor 列表已有 `"honor"`，但需确保 OUI 匹配 `"honor"` 和 `"honor technology"`

**TP-Link 摄像头隔离：**
- vendor 命中 `tp-link` 时，若 hostname 包含 `cam`/`ipc`/`nvr`/`dvr`，返回 camera 而非 router
- 增加萤石 `ezviz` 作为摄像头厂商

**国产 IoT 厂商补全：**
- Tuya/SmartLife → iot
- Broadlink → iot（空调伴侣）
- Yeelight → iot
- Aqara → iot
- Sonoff → iot
- SwitchBot/Switchbot → iot
- 萤石 EZVIZ → camera

## 改动三：HTTP Banner 探测

**实现方式：**
- 对开放端口 `80/8080/443/8443` 的设备，发轻量 HTTP HEAD 请求（timeout 0.5s）
- 提取 `Server`、`WWW-Authenticate` header
- 维护小型指纹库，命中则返回对应类型（置信度高于 vendor）

**指纹库：**
| 指纹 | 类型 | 说明 |
|------|------|------|
| Dahua/DahuaTech | camera | 大华设备 |
| Hikvision/DS- | camera | 海康设备 |
| Netwave | camera | 部分 IP Camera |
| TVT | camera | 天地伟业 |
| NVR | camera | NVR 设备页面 |
| TP-LINK HTTP Server | router | TP-Link 路由器 |
| NETGEAR | router | 网件路由器 |
| NetCore | router | 磊机路由器 |
| Realtek | computer | 主板/计算机 |
| Intel | computer | Intel 设备 |

**HTTP 探测为可选增强：** 若指纹库未命中，不影响原有 vendor/hostname 判断。

## 数据流

```
扫描发现设备
  → 并行执行：vendor查询 + hostname解析 + 端口扫描 + HTTP探测
  → guess_device_type(vendor, ports, hostname, http_banner)
  → 优先级：端口 > HTTP指纹 > hostname > vendor
```

## 函数签名变更

```python
@staticmethod
def guess_device_type(
    vendor: str,
    open_ports: list[int],
    hostname: str | None = None,
    http_banner: str | None = None,  # 新增：HTTP Server header
) -> str:
```

## 测试计划

1. 荣耀手机 MAC 被正确识别为 phone
2. TP-Link 摄像头（C100）被正确识别为 camera
3. Broadlink 空调伴侣被识别为 iot
4. Yeelight 被识别为 iot
5. 已有测试 `test_a2_unknown_device.py` 覆盖 unknown_device 逻辑
