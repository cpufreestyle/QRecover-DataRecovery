# QRecover - TestDisk & PhotoRec 现代化 GUI 包装器

> 基于 TestDisk & PhotoRec（全球最优秀的开源数据恢复软件）打造的 Windows 原生 GUI

## ✨ 特性

- 🎨 **Catppuccin Mocha 深色主题** — 现代化 UI，护眼舒适
- 💾 **磁盘扫描页** — 一键启动 TestDisk 分区扫描/修复
- 📄 **文件恢复页** — 集成 PhotoRec/QPhotoRec，支持文件类型过滤
- 🗂️ **分区恢复向导** — 5 步引导式分区表修复流程
- 📋 **实时日志** — 所有操作可追溯
- 🔧 **自动检测 TestDisk 路径** — 也支持手动设置

## 🚀 使用方法

### 1. 安装 TestDisk（必须）

从官方下载并解压到 `C:\Tools\TestDisk`：
https://www.cgsecurity.org/wiki/TestDisk_Download

或用 winget：
```powershell
winget install CGSecurity.TestDisk
```

### 2. 启动 QRecover

双击 `QRecover.bat` 或运行：
```bash
python qrecover.py
```

## 📁 项目结构

```
C:\Tools\TestDiskGUI\
├── qrecover.py      # 主程序 (Python + tkinter)
├── QRecover.bat     # Windows 启动器
└── README.md        # 本文件
```

## ⚙️ 依赖

- **Python 3.8+** (tkinter 内置，无需额外安装 pip 包)
- **TestDisk & PhotoRec 7.x** (数据恢复引擎)

## 🎯 功能对比

| 功能 | TestDisk 原版 | QRecover |
|------|-------------|----------|
| 界面 | TUI 文字菜单 | 现代 GUI |
| 磁盘选择 | 手动输入编号 | 可视化列表 + 容量信息 |
| 文件类型过滤 | 复杂命令行 | 单选按钮分类 |
| 输出目录 | 手动路径输入 | 浏览器选择 |
| 进度显示 | 滚动文字 | 进度条 + 状态指示器 |
| 日志 | 无 | 实时日志面板 |
| 主题 | 终端配色 | Catppuccin Mocha |

## 📜 许可证

GPL v2+（与 TestDisk 保持一致）

---

*QRecover · 让开源数据恢复工具更易用*
