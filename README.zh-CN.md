<div align="center">

<img src="docs/assets/hero.svg" alt="S300 Control — 让声音合你心意" width="100%">

**把 S300 的声音控制，放进随手可用的调音台。**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![MIT](https://img.shields.io/badge/License-MIT-e6b450?style=flat-square)](LICENSE)
[![PWA](https://img.shields.io/badge/PWA-Installable-687586?style=flat-square)](#添加到手机主屏幕)
[![依赖](https://img.shields.io/badge/Runtime_dependencies-0-424b59?style=flat-square)](pyproject.toml)

[快速开始](#快速开始) · [配置](docs/configuration.md) · [部署](docs/deployment.md) · [协议](docs/protocol.md)

[English](README.md) · 简体中文

</div>

---

S300 Control 是一个可以自托管的漫步者 S300 控制面板。在清晰紧凑的网页里选择音效模式，或进入 DIY 调整六段 EQ、保存喜欢的曲线。后端主动发现自己所在局域网的音箱，每次设置都通过实机状态回读确认。

**本项目由社区独立开发，并非官方项目**，与 Edifier（漫步者）不存在隶属关系，亦未获得其认可或赞助。产品名称仅用于说明兼容设备。

无需前端构建、账号或云端中转，运行时只使用 Python 标准库。界面目前为简体中文。

## 一屏，调好你的声音

<img src="docs/assets/desktop-black.jpg" alt="黑色主题：音箱与音量位于上方，音效选择下方展开 DIY 六段 EQ 和曲线库" width="100%">

*黑色配色（Ayu Dark） · DIY 模式。*

<details>
<summary><strong>查看手机布局</strong></summary>
<br>
<img src="docs/assets/mobile-black.jpg" alt="同一黑色主题下的手机 DIY 调音布局" width="320">
</details>

## 主要功能

| 功能 | 使用体验 |
| :--- | :--- |
| **先选音效模式** | 经典、监听、动感、人声与 DIY；仅在 DIY 模式下显示 EQ 相关控件。 |
| **DIY 六段均衡器** | 62 Hz～16 kHz；支持拖动、逐档点按、键盘调节和实时曲线。 |
| **快捷与自定义曲线** | 六条内置 DIY 曲线；自定义曲线保存在后端，可供手机和桌面共用。 |
| **主动发现音箱** | 无需填 IP；通过 UDP 广播识别设备，地址变化后自动重连。 |
| **实机回读确认** | 一条持久 TCP 连接、串行写入，避免拖动时积压指令。 |
| **可安装 PWA** | 响应式布局、安全区适配，以及最高 1024 px 的高清多尺寸 App 图标。 |
| **轻量部署** | Python 标准库、Docker host 网络、只读镜像和独立配置目录。 |

EQ 的设备增益范围为 **−30～+30、每档 5**。物理 dB 换算尚未确认，因此界面不将其标为 dB。快捷曲线是本项目提供的 DIY 组合，不代表官方音效模式。

切换音效模式会保留已存的 DIY 曲线。选择 **DIY** 后才能调整 EQ；其他模式下，滑块、快捷曲线和我的曲线均隐藏。上方的音箱信息、音量与音效选择位置固定，DIY 控件仅在下方展开。

## 快速开始

使用与音箱处于同一局域网的 **Linux Docker 主机**。host 网络使后端能直接收发局域网发现广播，不需要配置音箱 IP。

在仓库根目录执行：

```sh
mkdir -p config
cp examples/settings.json config/settings.json
docker compose -f devlop/compose.yaml up -d --build
```

打开 **`http://<你的服务器>:18730/`**，等待自动发现。

```sh
docker compose -f devlop/compose.yaml ps
docker compose -f devlop/compose.yaml logs --tail=50 s300
```

容器默认使用 UID/GID 1000。若目录属于其他用户，可通过 `S300_UID`、`S300_GID` 设置对应的 ID，确保容器用户能写入 `config/`。

配置与曲线保存在 `config/`。可用选项见[配置说明](docs/configuration.md)，反向代理设置和备份方法见[部署说明](docs/deployment.md)。

可在 `config/settings.json` 将 `theme` 设为 `black`（黑色）、`white`（白色）或 `coffee`（咖啡色），重启服务后生效。每套配色全天固定，页面不提供主题切换。

### 直接运行 Python

后端需要 Python 3.11+，推荐在 Linux 上使用完整的网卡广播发现。

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
mkdir -p config
cp examples/settings.json config/settings.json
python -m s300_control
```

安装后也可以用 `s300-control` 启动服务；`s300 --url http://localhost:18730/` 查询后端状态。命令行与网页共用后端的设备连接。

## 添加到手机主屏幕

通过现有反向代理提供 **HTTPS**。iPhone 使用 Safari 打开，轻点 **分享 → 添加到主屏幕**，若出现选项则启用“作为 Web App 打开”。支持安装提示的浏览器也可以直接使用页面中的安装按钮。

页面向 iPhone 提供单一、不透明且匹配所选配色的 1024×1024 PNG 主屏幕图标。其他浏览器使用 manifest 中同配色的多分辨率图标和 maskable 图标。

如果此前已经添加过旧版本，iOS 可能保留旧图标。删除原有主屏幕快捷方式，重新加载 HTTPS 页面后再添加，即可重新获取高清版本。详见[部署与图标说明](docs/deployment.md#pwa-and-app-icons)。

Service Worker 缓存的是界面。状态和控制接口始终实时请求，不缓存设备状态，也不在恢复连接后重放旧指令；设备不可达时无法控制音箱。

## 清晰的职责

浏览器只请求当前站点的后端，不直接连接音箱地址。后端负责发现和一条持久 TCP 控制连接；所有设备写入都需要回读确认。拖动时界面即时反馈，等待中的值会被最新值替换。

服务没有内置鉴权。请通过网络或反向代理，将访问范围限制在可信客户端。

```text
src/s300_control/   Python 后端包和 PWA 静态资源
tests/             协议、模拟 TCP 设备、HTTP API 和图标测试
docs/              配置、部署、API、协议说明和界面截图
examples/          通用配置示例
tools/             界面演示预览、图标生成
devlop/            Dockerfile、Compose 和 Docker 构建忽略规则
```

## 开发与验证

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
node --test tests/test_frontend.cjs
node --check src/s300_control/static/app.js
node --check src/s300_control/static/sw.js
```

测试不需要连接真实音箱。预览 README 中的黑色 DIY 界面：

```sh
python tools/preview.py --theme black --mode 4
```

打开 `http://127.0.0.1:18731/`。该预览使用临时演示数据，不启动局域网发现或音箱连接；保存的曲线仅在预览期间保留。`--theme` 可选 `black`、`white`、`coffee`，`--mode` 可选 0～4，`--port` 可修改预览端口。

重建图标：

```sh
python -m pip install -e '.[artwork]'
python tools/generate_icons.py
```

## 兼容性

- 协议行为已在 S300 固件 **01.13.07** 上验证；其他固件、同平台其他型号需要独立验证。
- 已提供五种音效模式、DIY 模式下的六段 EQ 和音量控制，静音通过音量设为 0 实现。
- 尚未确认的输入切换、播放、关机和恢复出厂设置不暴露为操作。
- 音箱待机或连接名额占满可能导致暂时离线；唤醒设备、关闭多余客户端后重试。

欢迎反馈问题和经过验证的协议行为。问题报告请包含固件版本、复现步骤和预期行为。

## 许可证

[MIT](LICENSE)。Edifier、漫步者与 S300 为各自权利人的商标。
