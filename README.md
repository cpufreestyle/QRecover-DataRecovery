# QRecover Web UI

> 基于 TestDisk、PhotoRec 和 Recuva 打造的现代化 Web 数据恢复工具

## ✨ 特性

- 🎨 **暗色渐变主题** — 紫/青/粉多套主题，渐变背景
- 🛠️ **首次使用一键安装向导** — 自动检测并引导下载 TestDisk/PhotoRec/Recuva
- 💾 **TestDisk** — 分区扫描/修复（TUI 新窗口启动）
- 📄 **PhotoRec** — 文件恢复（支持图片/文档/视频等类型）
- 🔍 **Recuva** — GUI 文件恢复工具
- 📊 **驱动器可视化** — 彩色使用率条（绿/黄/红）
- 🔧 **自动检测工具** — 启动时检测已安装工具，未安装则禁用
- 🔒 **防重复点击** — 处理中锁定按钮

## 🚀 快速开始

### 方式一：直接运行 EXE

下载最新版 [QRecoverDesktop.exe](https://github.com/cpufreestyle/QRecover-DataRecovery/releases)（Gitee 镜像见 [Releases](https://gitee.com/cpufreestyle/QRecover/releases)），双击运行即可，首次启动会自动弹出安装向导。

### 方式二：从源码运行

```bash
cd C:\Tools\TestDiskGUI
pip install flask
python qrecover.py
```

然后访问 http://127.0.0.1:5000

## 📁 项目结构

```
QRecover/
├── qrecover.py              # Flask 主程序（API 路由 + 工具启动/下载/更新）
├── web/                     # 前端静态资源（index.html / style.css / app.js / ai.js）
├── qrecover_desktop.py      # 桌面壳（pywebview 封装）
├── ai_assistant.py          # 本地规则 + LLM 恢复建议
├── version_utils.py         # PE 文件版本号读取 / SHA256（共用工具模块）
├── web_minify.py            # 打包前压缩前端资源（build.spec 自动调用）
├── run_dev_server.py        # 开发用：5055 端口启动 Web 服务
├── make_recuva_update.py    # 构建 Recuva 更新包
├── QRecover_Desktop.bat     # Windows 桌面启动器
├── build.spec               # PyInstaller 打包配置
└── README.md
```
> TestDisk/PhotoRec/Recuva 不再随仓库分发，改为**首次启动由向导按需联网下载**并缓存到 `tools/`。

## 🛠️ 工具说明

| 工具 | 类型 | 启动方式 |
|------|------|----------|
| TestDisk | TUI 分区修复 | 新控制台窗口 |
| PhotoRec | TUI 文件恢复 | 新控制台窗口 |
| Recuva | GUI 文件恢复 | 直接启动 |

## 🎯 版本历史

### v2.0.5 — Recuva 安装检测增强 🔍
- 修复 Recuva 已安装但界面仍显示"未安装"的问题
- 增强 `find_recuva()`：支持注册表（App Paths / Piriform / Uninstall）、PATH 环境变量、Piriform 官方默认目录
- 同时兼容 `recuva.exe` 与 `recuva64.exe`，找到 64 位版本时优先返回官方入口 `recuva.exe`
- 版本号读取跟随实际检测到的 Recuva 路径

### v2.0.4 — 全面优化 🚀
- **代码结构**：前端 HTML/CSS/JS 从 `qrecover.py` 剥离为独立静态文件（`web/`），主程序从 2400+ 行瘦身至 ~900 行
- **去重复**：PE 版本号读取/SHA256 抽取为 `version_utils.py` 公共模块；LLM 本地服务增加 TCP 探活，未运行时秒级回退启发式（原 ~18s）
- **性能**：桌面启动等待从固定 1s 改为端口轮询（窗口弹出更快）；`/api/drives` 加短缓存；页面不可见时暂停驱动器轮询；静态资源支持 ETag/304 协商缓存；UAC 提权进程改用句柄等待（零 tasklist 轮询）
- **打包**：`web/` 静态资源纳入 PyInstaller datas，构建时经 `web_minify.py` 零依赖压缩（CSS/JS 体积 ~60-70%）；扩充 excludes；`pyinstaller` 移出运行时依赖
- **体验**：AI 面板阻塞式 `alert()` 改为轻量 Toast；前端统一 `apiFetch()` 错误边界（网络异常 Toast + 服务端错误信息透传）；桌面版外链走 WebView 桥接打开；成功状态消息 8s 自动淡出；驱动器卡片 DocumentFragment 批量渲染
- **工程**：新增 `.editorconfig` 统一行尾；`run_dev_server.py` 开发启动脚本；批处理版本号统一

### v2.0.3 — 一键安装向导 + 体验优化 🛠️
- 新增「首次使用·一键安装恢复引擎」向导（TestDisk/PhotoRec/Recuva 自动下载）
- 统一版本号、移除常驻节日彩屑动画、降低无谓轮询
- 下载增加重试机制，提升弱网/代理环境下的成功率
- 合并重复的工具检测逻辑，启动更轻快

### v1.1.0 — 六一儿童节特别版 🎪
- UI 全面重构：多彩渐变主题 + 动画背景
- Canvas 全屏彩屑、星星闪烁、气球浮动
- 彩虹渐变横幅 + 节日文案
- 修复 TestDisk/PhotoRec 启动问题
- 修复重复 main() 语法错误
- 自动检测工具安装状态

### v1.0.0 — 首个发布版
- Flask Web UI 暗色主题
- 集成 TestDisk/PhotoRec/Recuva
- 修复根路由 404

## ⚙️ 依赖

- **Python 3.8+** + Flask + pywebview（源码运行）
- **TestDisk & PhotoRec / Recuva**：首次启动由内置向导按需联网下载并缓存到 `tools/`，无需手动配置

## 📜 许可证

GPL v2+（与 TestDisk 保持一致）

---

*QRecover · 让数据恢复更简单更美观* 🎈
