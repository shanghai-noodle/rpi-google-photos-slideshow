# 🖼️ 树莓派原生 Google 相册电子相框 (Raspberry Pi Native Google Photos Slideshow)

[![Version](https://img.shields.io/badge/version-v1.0.0-38bdf8)](https://github.com/shanghai-noodle/rpi-google-photos-slideshow)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)
[![Created with Google Antigravity](https://img.shields.io/badge/Created%20with-Google%20Antigravity-8b5cf6)](https://deepmind.google/)

**[English](README.md) | [简体中文](README.zh-CN.md)**

一款轻量级、专为 Linux 与树莓派（Raspberry Pi）打造的原生数字相框应用。支持直接流式加载 **Google 相册共享相册（Google Photos Shared Album）**（同时内置 **30 项精选演示媒体包**），具备 **9 种等间距竖屏/横屏智能拼图模板**、**主体感知智能居中裁剪**、**FFmpeg 实时视频格子播放**、**密码保护的 6 语言本地网页配置中心**，以及 **100% 纯内存（RAM）日志与缓存（零 SD 卡写入磨损）**。

> ✨ 本应用由 **shanghai-noodle** 使用 **Google Antigravity** 构建与创建。

---

## ✨ 核心功能

- **🎨 9 种等间距拼图模板 + 竖屏单张优先展示**：
  - **严格方向匹配与居中裁剪（Center-Crop）**：竖屏照片/视频（`高 >= 宽`）严格放入竖屏格子（`高 > 宽`），横屏照片/视频（`宽 > 高`）严格放入横屏格子（`宽 > 高`），并采用智能居中裁剪（`ImageOps.fit`），杜绝画面拉伸或方向错位。
  - **暗色散景（Bokeh）背景 + `2px` 纤细白色画框**：所有多图拼图均渲染在深色高斯散景背景之上，配有 `2px` 白色边框与均匀的 `16px` 间距：
    - `collage` *(默认)*：智能权重混合（约 32% 全屏竖屏/横屏单张展示 + 全部 9 种等间距网格拼图模板）。
    - `trio`：3 格拼图（顶部 1 张横屏 + 底部 2 张竖屏）。
    - `classic-5`：5 格经典拼图（顶部 1 张横屏主图 + 中间 2 张竖屏 + 底部 2 张横屏）。
    - `mosaic-7`：7 格交错马赛克拼图（含右下角竖屏主图）。
    - `sidebar-9`：9 格侧边栏拼图（左侧堆叠列 + 右侧竖屏主图）。
    - `mondrian-8`：8 格对称蒙德里安（Mondrian）网格。
    - `grid-3-2-3`：8 格三行拼图（3 张竖屏 + 2 张横屏 + 3 张竖屏）。
    - `stack`：2 张横屏垂直堆叠。
    - `triple`：3 张横屏垂直堆叠。
    - `bento`：4 格便当盒（Bento）网格。
    - `single`：每页仅全屏展示 1 张照片或视频。
- **🎬 任意拼图格实时视频播放 (`--videos play | skip | still`)**：
  - 自动识别 Google 相册共享相册中的视频（基于 `76647426` 元数据标记）及本地 `.mp4`/`.mov` 视频文件。
  - 将转码后的 MP4 视频流（`=m18`）预加载至 `/dev/shm`（内存虚拟磁盘），并通过 `ffmpeg` 在全屏或任意拼图格子内流畅播放（同一页面包含多个视频格子时会依次自动播放）。
- **🌐 密码保护的多语言本地网页控制中心 (`http://<树莓派IP>:8080`)**：
  - **多语言界面**：内置 **English（英语）**、**简体中文**、**日本語（日语）**、**한국어（韩语）**、**Español（西班牙语）**、**Deutsch（德语）** 6 种语言一键切换。
  - **安全认证**：采用 `PBKDF2-HMAC-SHA256` 加盐哈希存储密码（默认初始密码：`admin`，可在网页端随时修改）。
  - **实时远程控制与持久化配置**：随时在手机或电脑浏览器中更换 Google 相册共享链接、切换拼图样式、调整幻灯片时长、唤醒/休眠或彻底关闭夜间模式（Night Mode）、切换至内置演示模式（Demo Showcase）、查看实时内存日志、重启服务或重启树莓派（`sudo reboot`）。
- **✨ 内置 AI 生成的精选演示媒体包 (`demo_media/`)**：
  - **AI 生成媒体说明**：`demo_media/` 中内置的所有演示图片与视频均由 **Google Gemini 与 Veo AI 生成**（包含 **24 张写实高清照片** —— 12 张萌宠猫咪与狗狗 + 12 张城市园林景观与建筑公园，涵盖 `3:4` 竖屏与 `16:9` 横屏 —— 以及 **5 段 10 秒生动的 Gemini Veo MP4 视频**），100% 原创且遵循 MIT 开源协议。新设备安装后无需联网或配置私人相册即可开箱即用；同时支持在任何远程图片链接失效时自动回退至本地 `demo_media/` 照片。
- **🛡️ 零 SD 卡磨损 & 24/7 USB Wi-Fi 防休眠看门狗**：
  - 禁用 Python `.pyc` 字节码写入（`sys.dont_write_bytecode = True`），日志严格保存在 `/dev/shm/slideshow.log`（最大 2 MB 内存循环日志），相册缓存保存在 `/dev/shm/gphotos_cache.json`。
  - 内置 USB 无线网卡防休眠守护线程（自动执行 `iw dev wlan0 set power_save off` 并每 45 秒发送一次网关保活心跳），防止 USB Wi-Fi 网卡在夜间休眠时段断流。

---

## 🛠️ 树莓派完整安装与配置指南

### 1. 安装系统依赖包
在 Raspberry Pi OS（Lite 或 Desktop 版）或 Debian/Ubuntu 终端中执行：

```bash
sudo apt update
sudo apt install -y git python3-pygame python3-pil ffmpeg libegl1 libgbm1 libgles2 libgl1-mesa-dri
sudo usermod -aG video,render $USER
```

### 2. 克隆代码仓库
```bash
cd ~
git clone https://github.com/shanghai-noodle/rpi-google-photos-slideshow.git
cd rpi-google-photos-slideshow
```

### 3. 启动并通过网页控制台配置
运行电子相框（默认以 `0°` 标准横屏 `1920×1080` 启动并进入 **内置演示模式 Demo Showcase**）：

```bash
python3 -B slideshow.py --duration 15 --rotate 0 --landscape-mode collage --web-port 8080
```

1. 在同一局域网下的手机或电脑浏览器中打开 **`http://<树莓派IP>:8080`**。
   > **⚠️ 防火墙提示**：网页控制台默认监听 TCP **`8080`** 端口。如果您的设备启用了防火墙（如 `ufw` 或 `iptables`）或路由器开启了 AP 隔离，请确保放行局域网 `8080` 端口（例如执行 `sudo ufw allow 8080/tcp`）。
2. 使用默认初始密码 **`admin`** 登录（登录后可在 **安全与修改密码** 中设置新密码）。
3. 粘贴您的 **Google 相册共享链接**（`https://photos.app.goo.gl/...`），选择喜欢的拼图布局、屏幕旋转角度（`0°`、`90°`、`180°` 或 `270°`）与夜间休眠时段，点击 **保存并立即应用设置** 即可实时生效。

### 4. 配置开机自启服务 (`systemd`)
如需在树莓派通电开机后自动全屏运行幻灯片，直接执行内置的一键安装脚本（会自动识别您的当前用户名与仓库目录）：

```bash
./install_service.sh
```

*（或手动复制服务文件）*：
```bash
sed -i "s|/home/pi/rpi-google-photos-slideshow|$(pwd)|g; s|User=pi|User=$USER|g" slideshow.service
sudo cp slideshow.service /etc/systemd/system/slideshow.service
sudo systemctl daemon-reload
sudo systemctl enable --now slideshow.service
```

随时查看运行状态与实时内存日志：
```bash
sudo systemctl status slideshow.service
./view_logs.sh
```

---

## 🚀 快速启动与命令行参数

直接启动电子相框（默认进入 **内置演示模式 Demo Showcase**，随后可通过浏览器访问 `http://<树莓派IP>:8080` 配置您的相册）：

```bash
python3 -B slideshow.py \
  --duration 15 \
  --rotate 0 \
  --landscape-mode collage \
  --web-port 8080
```

或者直接通过命令行指定 Google 相册共享链接：

```bash
python3 -B slideshow.py \
  --album "https://photos.app.goo.gl/YOUR_SHARED_ALBUM_ID" \
  --duration 15 \
  --rotate 0 \
  --landscape-mode collage
```

### 命令行参数说明 (CLI Options)

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `-p`, `--profile` | `auto` | 硬件优化配置：`auto`（自动检测 Pi Zero 与 Pi 2+）、`pi2`（1GB+ 内存，全高清 `1080×1920` 抓取）、或 `pizero`（512MB 内存 / 单核 ARMv6 优化） |
| `-a`, `--album` | `demo` | Google 相册共享链接（`https://photos.app.goo.gl/...`）、ID 或 `demo`（内置 30 项演示媒体包） |
| `--web-port` | `8080` | 密码保护的本地网页配置中心端口（设为 `0` 可禁用） |
| `-d`, `--duration` | `15.0` | 每页幻灯片展示时长（秒） |
| `-t`, `--transition` | `none` | 幻灯片切换过渡效果：`none`（默认无过渡直接切换）、`fade`、`fade-black`、`slide-left`、`slide-right`、`slide-up`、`wipe` 或 `random` |
| `-l`, `--landscape-mode` | `collage` | 布局模式：`collage`（全部 9 种网格 + 单张混合）、`single`、`trio`、`classic-5`、`mosaic-7`、`sidebar-9`、`mondrian-8`、`grid-3-2-3`、`stack`、`triple`、`bento` |
| `-v`, `--videos` | `play` | 视频处理方式：`play`（在内存中通过 `ffmpeg` 实时播放）、`skip`（跳过视频）、`still`（仅显示静态缩略图） |
| `--max-video-seconds` | `20.0` | 单个视频片段最长播放秒数 |
| `--rotate` | `0` | 屏幕旋转角度（`0`、`90`、`180`、`270`，默认 `0` 横屏） |
| `--fit` | `contain-blur` | 单张照片填充模式：`contain-blur`（暗色散景背景）、`contain`（黑边完整显示）、`cover`（居中裁剪铺满） |
| `--night-start` | `22:00` | 夜间休眠黑屏开始时间（`HH:MM` 或 `10pm`） |
| `--night-end` | `06:00` | 夜间休眠唤醒时间（`HH:MM` 或 `6am`） |
| `--disable-night-mode` | `False` | 禁用夜间休眠模式（全天候 24/7 点亮屏幕） |
| `--no-shuffle` | `False` | 按相册顺序播放（禁用随机打乱） |
| `--refresh-hours` | `12.0` | 后台自动重新同步 Google 相册的小时数间隔（`0` 为禁用） |
| `--log-file` | `/dev/shm/slideshow.log` | 内存日志文件路径（`tmpfs`） |
| `--cache-file` | `/dev/shm/gphotos_cache.json` | 内存相册缓存路径（`tmpfs`） |

---

## ⌨️ 键盘快捷键

| 按键 | 功能 |
| :--- | :--- |
| **右方向键 (Right Arrow)** | 下一页幻灯片 |
| **左方向键 / P** | 上一页幻灯片 |
| **空格键 (Spacebar)** | 暂停 / 继续播放 |
| **N** | 手动切换夜间模式（唤醒 / 休眠） |
| **R** | 顺时针旋转屏幕 90° |
| **Q / Escape** | 退出程序 |

---

## 📄 查看实时内存日志

运行仓库自带的辅助脚本即可查看进程状态并实时追踪 `/dev/shm/slideshow.log` 内存日志：

```bash
./view_logs.sh
```

---

## 🧩 模块化架构 (`v1.0.0`)

```text
├── slideshow.py              # CLI 命令行入口与向后兼容重导出模块
├── photoframe/               # 核心模块化 Python 包
│   ├── __init__.py           # 包导出定义与版本元数据 (v1.0.0)
│   ├── hardware.py           # Pi Zero / Pi 2+ 硬件自动识别与定额内存日志管理
│   ├── config.py             # FrameConfigStore (PBKDF2-HMAC-SHA256 认证与持久化配置)
│   ├── gphotos.py            # GooglePhotosMixin (Google 相册共享页面解析、分页拉取与内存缓存)
│   ├── collage.py            # PhotoManager (9 种等间距横竖屏拼图模板与主体感知智能居中裁剪)
│   ├── web_portal.py         # 6 语言本地网页控制中心 (en, zh, ja, ko, es, de)
│   └── app.py                # PiSlideshowApp (KMSDRM 硬件显示、实时旋转、FFmpeg 视频流与夜间模式)
├── tests/                    # 100% 离线无头单元测试套件
│   ├── test_collage.py       # 横竖屏拼图几何结构、自然画幅比例与智能居中裁剪测试
│   ├── test_web_portal.py    # PBKDF2 密码认证、Cookie 会话、6 语言完整性与 HTTP API 测试
│   ├── test_gphotos.py       # Google 相册 HTML/RPC 解析与内存缓存读写测试
│   └── test_app_display.py   # 屏幕旋转坐标映射、FFmpeg 滤镜转换与夜间休眠计划测试
└── demo_media/               # 内置 30 项精选演示媒体包 (24 张高清照片 + 6 段 MP4 视频)
```

---

## 🧪 运行单元测试

仓库内置完整的离线无头（Headless）单元测试套件，覆盖横屏（`1920×1080`）与竖屏（`1080×1920`）下的全部 9 种拼图模板几何验证、主体感知居中裁剪、网页控制中心登录认证与 6 国语言完整性、Google 相册解析以及屏幕旋转/夜间模式定时逻辑：

```bash
python3 -m unittest discover -s tests -v
```

---

## 🙌 致谢与开源许可 (Credits & License)

- **构建工具**: **Created with Google Antigravity**
- **作者**: **shanghai-noodle**
- **当前版本**: `v1.0.0`
- **开源协议**: 基于 [MIT License](LICENSE) 开源发布。

