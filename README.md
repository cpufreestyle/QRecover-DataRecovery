# QRecover Web UI

> 基于 TestDisk、PhotoRec 和 Recuva 打造的现代化 Web 数据恢复工具

## ✨ 特性

- 🎨 **暗色渐变主题** — 紫/青/粉渐变，动画浮动背景
- 🤖 **AI 智能扫描建议** — 按磁盘类型/场景自动推荐恢复方案（本地规则，离线可用）
- 🔧 **自动安装工具** — 一键安装 TestDisk/PhotoRec/Recuva
- 💾 **TestDisk** — 分区扫描/修复（TUI 新窗口启动）
- 📄 **PhotoRec** — 文件恢复（支持图片/文档/视频等类型）
- 🔍 **Recuva** — GUI 文件恢复工具
- 📊 **驱动器可视化** — 彩色使用率条（绿/黄/红）
- 🔧 **自动检测工具** — 启动时检测已安装工具，未安装则禁用
- 🔒 **防重复点击** — 处理中锁定按钮

## 🚀 快速开始

### 方式一：直接运行 EXE

下载最新版 [QRecoverWeb.exe](https://gitee.com/cpufreestyle/QRecover/releases)，双击运行，浏览器访问 `http://127.0.0.1:5000`

### 方式二：从源码运行

```bash
cd C:\Tools\TestDiskGUI
pip install flask
python qrecover.py
```

然后访问 http://127.0.0.1:5000

## 📁 项目结构

```
C:\Tools\TestDiskGUI\
├── qrecover.py              # Flask 主程序（含内嵌 HTML 模板）
├── QRecover.bat             # Windows 启动器
├── QRecoverWeb_latest.exe   # 打包版可执行文件
├── testdisk-7.3-WIP/        # TestDisk/PhotoRec
├── recuva_portable/          # Recuva 便携版
└── README.md
```

## 🛠️ 工具说明

| 工具 | 类型 | 启动方式 |
|------|------|----------|
| TestDisk | TUI 分区修复 | 新控制台窗口 |
| PhotoRec | TUI 文件恢复 | 新控制台窗口 |
| Recuva | GUI 文件恢复 | 直接启动 |

## 🎯 版本历史

### v2.0.0 — AI 智能扫描建议 + 桌面端增强 🤖
- 新增 🤖 AI 智能扫描建议面板（本地规则，无需联网/API）
- 支持误删/格式化/分区丢失/无法访问 四类场景一键建议
- 一键「采用并切换」到推荐工具（Recuva / TestDisk·PhotoRec）
- 自动安装 TestDisk/PhotoRec/Recuva 工具（含 UAC 提权回退）
- 桌面端单实例防堆叠、窗口控制按钮注入
- 版本号统一为 v2.0.0

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

- **Python 3.8+** + Flask
- **TestDisk & PhotoRec 7.x**（已内置）
- **Recuva**（便携版，已内置）

## 📜 许可证

GPL v2+（与 TestDisk 保持一致）

---

*QRecover · 让数据恢复更简单更美观* 🎈
