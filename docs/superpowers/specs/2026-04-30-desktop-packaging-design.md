# Desktop Packaging Design — SmartHome Windows 一键安装包

**日期：** 2026-04-30  
**状态：** 已批准  
**目标：** 将前后端项目打包为 Windows 双击安装包，面向无技术背景的家庭用户。

---

## 1. 目标与约束

- **目标平台：** 仅 Windows（Win10/11）
- **分发形式：** 单个安装包 `.exe`（Inno Setup）
- **UI 呈现：** 安装后双击图标 → 程序后台运行 → 自动打开系统默认浏览器 → `http://localhost:8000`
- **数据目录：** 安装向导中用户可配置，默认为第一个非 C 盘（如 `D:\SmartHome\data`）；仅有 C 盘时兜底为 `C:\SmartHome\data`
- **原生依赖：** ffmpeg、nmap、Npcap 全部打包到安装包中，用户无需手动安装

---

## 2. 整体架构

### 目录结构

```
smart_home/
├── backend/
│   ├── app/
│   │   ├── desktop.py        # 新增：系统托盘 + 单实例检测 + 自动开浏览器
│   │   ├── config.py         # 修改：支持从 app.cfg 读取 data_dir
│   │   └── main.py           # 修改：挂载前端静态文件 + 桌面模式启动
│   ├── frontend/             # 构建时生成：Vue dist 产物（被 FastAPI 托管）
│   ├── smart-home.spec       # 新增：PyInstaller 打包配置
│   └── pyproject.toml        # 修改：新增 pystray、Pillow 依赖
├── smart-home-frontend/      # 无需修改
└── installer/
    ├── build.ps1             # 新增：一键构建脚本
    ├── installer.iss         # 新增：Inno Setup 安装包配置
    └── redist/
        ├── npcap.exe         # 手动放入：Npcap 安装包
        ├── ffmpeg.exe        # 手动放入：ffmpeg Windows 单文件版
        └── nmap/             # 手动放入：nmap Windows 便携版
            ├── nmap.exe
            └── ...
```

### 安装后磁盘布局

```
C:\Program Files\SmartHome\       # 程序目录（只读）
  SmartHome.exe                   # 主程序入口
  app.cfg                         # 记录 data_dir 路径
  ffmpeg\ffmpeg.exe
  nmap\nmap.exe
  _internal\                      # PyInstaller 打包产物
    app\                          # 应用源码（.pyc）
    frontend\                     # Vue 编译产物（HTML/JS/CSS）
    ... (Python runtime + 所有包)

D:\SmartHome\data\                # 数据目录（用户可配置，首次启动自动创建）
  smart_home.db
  app.log
  recordings\
  dlna_media\
```

### `app.cfg` 格式

```ini
[paths]
data_dir = D:\SmartHome\data
```

---

## 3. 用户体验流程

### 安装

```
双击 SmartHome-Setup.exe
  → 欢迎页
  → 选择程序安装目录（默认 C:\Program Files\SmartHome）
  → 选择数据目录（默认：第一个非 C 盘，如 D:\SmartHome\data）
  → 执行安装：
      1. 复制程序文件到 {app}
      2. 静默安装 Npcap（后台，用户不感知）
      3. 写入 {app}\app.cfg
      4. 创建数据目录
      5. 创建桌面快捷方式 + 开始菜单条目
      6. 注册卸载程序
  → 完成
```

### 日常使用

```
双击桌面 SmartHome 图标
  → 检测端口 8000 是否已占用
      ├─ 已占用：直接打开浏览器，退出新进程
      └─ 未占用：
            → 读取 app.cfg 确定 data_dir
            → 启动 FastAPI（端口 8000）
            → 系统托盘出现图标
            → 延迟 1.5s 自动打开浏览器 → http://localhost:8000
```

### 退出

```
系统托盘图标右键
  → "打开界面"：唤起浏览器窗口
  → "退出"：优雅关闭 uvicorn，移除托盘图标
```

---

## 4. 后端代码改动

### 4.1 新增 `app/desktop.py`

职责：
- `is_already_running()` — 尝试连接 `localhost:8000`，判断是否已有实例
- `open_browser()` — 延迟 1.5s 后调用 `webbrowser.open("http://localhost:8000")`，在新线程中运行
- `run_tray_icon(shutdown_event)` — 创建系统托盘图标（`pystray`），菜单包含"打开界面"和"退出"；点击"退出"时设置 `shutdown_event`，触发 uvicorn 优雅关闭

### 4.2 修改 `app/config.py`

新增逻辑：
- `is_packaged() -> bool`：检测 `sys.frozen` 判断是否以打包 exe 运行
- `get_data_dir() -> Path`：
  - 打包模式：读取 `{exe目录}/app.cfg` 中的 `data_dir`
  - 开发模式：使用原有 `.env` / 默认值（`./data`）
- `DATABASE_URL`、日志路径、`RECORDING_TEMP_DIR`、`LOCAL_STORAGE_PATH` 全部由 `data_dir` 派生

### 4.3 修改 `app/main.py`

新增逻辑：
- **前端静态文件托管**：所有 `/api` 路由注册完成后，挂载 `frontend/` 目录（`StaticFiles(html=True)`），支持 Vue Router history 模式
- **桌面模式启动**（仅 `is_packaged()` 时生效）：
  - lifespan startup 中：启动托盘线程、启动自动开浏览器线程
  - lifespan shutdown 中：清理托盘资源
- **nmap / ffmpeg 路径注入**：打包模式下，将 `{exe目录}/nmap` 和 `{exe目录}/ffmpeg` 加入 PATH

### 4.4 修改 `pyproject.toml`

```toml
dependencies 新增：
  "pystray>=0.19.0",
  "Pillow>=11.0.0",
```

---

## 5. 构建流程

### 一键构建脚本 `installer/build.ps1`

```
步骤 1：cd smart-home-frontend && npm run build
        产物：smart-home-frontend/dist/

步骤 2：将 dist/ 复制到 backend/frontend/

步骤 3：cd backend && uv run pyinstaller smart-home.spec
        产物：backend/dist/SmartHome/

步骤 4：iscc installer/installer.iss
        产物：installer/output/SmartHome-Setup.exe
```

前置条件（构建机需要）：
- Node.js（前端构建）
- Python 3.11 + uv（后端打包）
- PyInstaller（`uv add --dev pyinstaller`）
- Inno Setup 6（`iscc` 在 PATH 中）
- `installer/redist/` 下已手动放入 `npcap.exe`、`ffmpeg.exe`、`nmap/`

> **注意：Npcap 许可证**  
> 将 Npcap 打包进安装程序分发需使用 [Npcap OEM 版本](https://npcap.com/oem/)（商业授权）。个人/内部使用可用免费版，但不得公开再分发。`redist/npcap.exe` 需从官方渠道自行获取，不提交到 git 仓库。

### PyInstaller spec 关键配置（`backend/smart-home.spec`）

| 配置项 | 值 |
|--------|----|
| 入口点 | `main.py` |
| 打包模式 | `--onedir`（启动速度快于 `--onefile`） |
| 控制台窗口 | `console=False`（不弹出黑色命令行） |
| 打包数据文件 | `app/`、`frontend/`（Vue 产物）、onvif-zeep WSDL 文件 |
| 隐式导入 | `scapy.all`、`scapy.layers.all`、`passlib.handlers.bcrypt`、`sqlalchemy.dialects.sqlite`、`jose`、`multipart`、`aiosqlite` 等 |
| 外部二进制 | ffmpeg.exe 和 nmap.exe **不经 PyInstaller 打包**，由 Inno Setup 单独部署 |

### Inno Setup 关键配置（`installer/installer.iss`）

- 自定义向导页（Pascal 脚本）：数据目录输入框，默认值由 `GetFirstNonCDrive()` 函数计算
- `GetFirstNonCDrive()`：遍历 D-Z 盘，取第一个存在的盘符返回 `X:\SmartHome\data`，无则返回 `C:\SmartHome\data`
- 安装后执行：
  1. 静默运行 `npcap.exe /S`
  2. 用 Pascal 脚本写入 `{app}\app.cfg`
  3. 创建数据目录

---

## 6. 测试要点

| 场景 | 验证内容 |
|------|----------|
| 首次安装（多盘机器） | 向导默认显示 D 盘路径，安装后 `app.cfg` 内容正确 |
| 首次安装（仅 C 盘） | 向导默认显示 `C:\SmartHome\data`，安装成功 |
| 双击启动 | 无控制台窗口，托盘图标出现，浏览器自动打开 |
| 重复双击 | 不启动第二个进程，直接打开浏览器 |
| 托盘退出 | uvicorn 正常关闭，端口释放 |
| 卸载 | 程序文件清除，数据目录**保留**（用户数据不删除） |

---

## 7. 不在本次范围内

- macOS / Linux 打包
- 自动更新机制
- 代码签名（安装时可能触发 Windows SmartScreen 警告，属已知限制）
- CI/CD 自动构建
