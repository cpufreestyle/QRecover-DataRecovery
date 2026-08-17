

## Codely Structured Memories

### User
- [2026-08-15 23:15:06] 用户会在 agent 会话进行中并行编辑仓库文件（如 2026-08-15 会话中同步把版本号 2.0.3→2.0.4，与 agent 计划一致）。**Why:** 用户会实时跟进 agent 的优化方案并动手配合。**How to apply:** QRecover 仓库中遇到 edit_stale_file_read 或发现非预期 diff 时，先 git diff 确认是否用户并行编辑，避免误判为异常。

### Feedback

### Project
- [2026-08-17 11:55:01] QRecover 项目本地 ai_config.json 配置 AI 助手指向 LM Studio（http://localhost:12340/v1，gemma 模型）。LM Studio 未运行时走 TCP 探活（1s）快速回退本地启发式规则（2026-08-17 优化后实测 ~2s，此前为 ~18s）。**Why:** 用户本机装了 LM Studio 用于本地推理。**How to apply:** 涉及 /api/ai/chat 性能或 AI 配置问题时先检查该配置与 LM Studio 状态。
- [2026-08-17 11:55:01] QRecover v2.0.4 架构：前端在 web/（index.html/style.css/app.js/ai.js，源码运行由 Flask static_folder 服务）；web_minify.py 在 PyInstaller build.spec 构建时自动压缩 web/ 到 build/web_min 打包；frozen 模式从 sys._MEIPASS/web 读取。打包前需先结束运行中的 QRecoverDesktop.exe 进程（否则 PermissionError WinError 5）。**Why:** EXE 被运行中进程锁定。**How to apply:** 重打包失败先查 Get-Process QRecoverDesktop。

### Reference

