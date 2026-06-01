#!/usr/bin/env python3
"""QRecover Web UI - TestDisk/PhotoRec/Recuva GUI"""
import os
import sys
import shutil
import subprocess
import ctypes
import webbrowser
import logging
from flask import Flask, render_template_string, request, jsonify

def run_tool(exe_path, work_dir=None):
    """启动工具，自动处理 UAC 提权。"""
    if not os.path.isfile(exe_path):
        raise FileNotFoundError(f"找不到: {exe_path}")
    try:
        # 先尝试直接 Popen（EXE 已是管理员时直接继承）
        subprocess.Popen([exe_path], cwd=work_dir or os.path.dirname(exe_path))
    except OSError as e:
        if getattr(e, 'winerror', None) == 740 or '740' in str(e):
            # WinError 740 = 需要提权，用 ShellExecuteW 触发 UAC
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe_path, None,
                work_dir or os.path.dirname(exe_path), 1
            )
            if ret <= 32:
                raise RuntimeError(f"UAC 提权失败 (ShellExecuteW 返回 {ret})")
        else:
            raise
    return True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# PyInstaller 打包后路径处理
# onedir 模式：外部文件放在 EXE 旁边，不用打包进 EXE
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = SCRIPT_DIR

# ── Logging ──
log_file = os.path.join(BASE_DIR, 'qrecover.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
log.info('QRecover starting...')

# TestDisk 路径
TESTDISK_DIR = os.path.join(BASE_DIR, "testdisk-7.3-WIP")
TESTDISK_EXE = os.path.join(TESTDISK_DIR, "testdisk_win.exe")
PHOTOREC_EXE = os.path.join(TESTDISK_DIR, "photorec_win.exe")

# Recuva 路径
RECUVA_PATHS = [
    r"C:\Program Files\Recuva\recuva.exe",
    r"C:\Program Files (x86)\Recuva\recuva.exe",
    os.path.join(BASE_DIR, "recuva_portable", "recuva.exe"),
]
RECUVA_INSTALLER = os.path.join(BASE_DIR, "Recuva_1.54.120_Machine_X64_nullsoft_en-US.exe")

IS_WIN = sys.platform == "win32"

app = Flask(__name__)

# ─────────── Routes ───────────
@app.route('/')
def index():
    """Render main page"""
    return render_template_string(HTML)

# ─────────── Helpers ───────────
def get_drives():
    """获取 Windows 驱动器列表"""
    drives = []
    if IS_WIN:
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            path = f"{letter}:\\"
            if os.path.isdir(path):
                try:
                    t, u, f = shutil.disk_usage(path)
                    drives.append({
                        "letter": letter,
                        "total": f"{t/1024**3:.1f}",
                        "free": f"{f/1024**3:.1f}",
                        "used": f"{u/t*100:.0f}%"
                    })
                except OSError:
                    pass
    return drives

def find_recuva():
    """查找 Recuva 可执行文件"""
    for path in RECUVA_PATHS:
        if os.path.isfile(path):
            return path
    return None

# ─────────── HTML 模板 ───────────
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QRecover - 数据恢复工具</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💾</text></svg>">
    <style>
        :root {
            --bg: #0f0f1a;
            --surface: #1a1a2e;
            --card: #16213e;
            --border: #2a2a4a;
            --text: #e8e8f0;
            --text-dim: #8888aa;
            --accent: #6c63ff;
            --accent-glow: rgba(108,99,255,0.3);
            --success: #00d68f;
            --success-bg: rgba(0,214,143,0.12);
            --warning: #ffaa00;
            --warning-bg: rgba(255,170,0,0.12);
            --danger: #ff4757;
            --danger-bg: rgba(255,71,87,0.12);
            --info: #54a0ff;
            --info-bg: rgba(84,160,255,0.12);
            --pink: #ff6b9d;
            --cyan: #00d4ff;
            --gradient-1: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --gradient-2: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            --gradient-3: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            --shadow-sm: 0 2px 8px rgba(0,0,0,0.2);
            --shadow-md: 0 4px 20px rgba(0,0,0,0.3);
            --shadow-lg: 0 8px 40px rgba(0,0,0,0.4);
            --radius: 16px;
            --radius-sm: 10px;
            --radius-xs: 6px;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* 背景装饰 */
        body::before {
            content: '';
            position: fixed;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: 
                radial-gradient(circle at 20% 20%, rgba(108,99,255,0.06) 0%, transparent 50%),
                radial-gradient(circle at 80% 80%, rgba(0,212,255,0.05) 0%, transparent 50%),
                radial-gradient(circle at 50% 50%, rgba(255,107,157,0.04) 0%, transparent 60%);
            z-index: -1;
            animation: bgFloat 20s ease-in-out infinite alternate;
        }
        @keyframes bgFloat {
            0% { transform: translate(0, 0) rotate(0deg); }
            100% { transform: translate(-2%, -2%) rotate(1deg); }
        }

        /* 主容器 */
        .container {
            max-width: 720px;
            margin: 0 auto;
            padding: 30px 20px 60px;
        }

        /* 头部 */
        .header {
            text-align: center;
            padding: 40px 20px 35px;
            position: relative;
        }

        .logo-icon {
            font-size: 3.5rem;
            display: inline-block;
            animation: logoFloat 3s ease-in-out infinite;
            filter: drop-shadow(0 4px 16px var(--accent-glow));
        }
        @keyframes logoFloat {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-8px); }
        }

        h1 {
            font-size: 2.2rem;
            font-weight: 800;
            margin-top: 14px;
            background: var(--gradient-1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.5px;
        }

        .subtitle {
            color: var(--text-dim);
            font-size: 0.95rem;
            margin-top: 8px;
            font-weight: 400;
        }

        /* ═══ 61 儿童节特别版 ═══ */
        /* 飘浮粒子画布 */
        #confetti-canvas {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            pointer-events: none;
            z-index: 9999;
        }
        /* 节日主横幅 */
        .children-day-banner {
            background: linear-gradient(135deg, #ff6b6b 0%, #ffa07a 20%, #ffd700 40%, #98fb98 60%, #87ceeb 80%, #dda0dd 100%);
            background-size: 400% 400%;
            border-radius: 20px;
            padding: 22px 28px 18px;
            margin-bottom: 28px;
            text-align: center;
            position: relative;
            overflow: hidden;
            box-shadow: 0 6px 30px rgba(255,215,0,0.25), 0 2px 12px rgba(255,107,107,0.15), inset 0 1px 0 rgba(255,255,255,0.4);
            animation: bannerGradient 6s ease infinite;
        }
        .children-day-banner::before {
            content: '';
            position: absolute;
            top: -50%; left: -50%; width: 200%; height: 200%;
            background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.2) 0%, transparent 50%);
            pointer-events: none;
        }
        .children-day-banner::after {
            content: '🎀🎊🎉🧁🍭🎁';
            position: absolute;
            bottom: 4px; right: 14px;
            font-size: 1rem;
            letter-spacing: 4px;
            opacity: 0.5;
        }
        @keyframes bannerGradient {
            0% { background-position: 0% 50%; }
            25% { background-position: 50% 100%; }
            50% { background-position: 100% 50%; }
            75% { background-position: 50% 0%; }
            100% { background-position: 0% 50%; }
        }
        .banner-title {
            font-size: 1.3rem;
            font-weight: 900;
            color: #fff;
            text-shadow: 0 2px 8px rgba(0,0,0,0.15), 0 0 20px rgba(255,255,255,0.3);
            margin-bottom: 6px;
            letter-spacing: 2px;
            position: relative;
            z-index: 1;
        }
        .banner-sub {
            font-size: 0.82rem;
            color: rgba(255,255,255,0.9);
            font-weight: 500;
            position: relative;
            z-index: 1;
        }
        .banner-balloons {
            position: absolute;
            top: 6px; left: 16px;
            font-size: 1.6rem;
            display: flex; gap: 6px;
        }
        .banner-balloons span {
            display: inline-block;
            animation: balloonFloat 3s ease-in-out infinite;
        }
        .banner-balloons span:nth-child(2) { animation-delay: 0.4s; }
        .banner-balloons span:nth-child(3) { animation-delay: 0.8s; }
        @keyframes balloonFloat {
            0%, 100% { transform: translateY(0) rotate(-3deg) scale(1); }
            33% { transform: translateY(-8px) rotate(5deg) scale(1.1); }
            66% { transform: translateY(-4px) rotate(-2deg) scale(0.95); }
        }
        /* 节日星星 */
        .star-row {
            display: flex;
            justify-content: center;
            gap: 12px;
            margin-bottom: 24px;
        }
        .star-row span {
            font-size: 1rem;
            animation: starTwinkle 2s ease-in-out infinite;
            filter: drop-shadow(0 0 4px rgba(255,215,0,0.5));
        }
        .star-row span:nth-child(1) { animation-delay: 0s; color: #ffd700; }
        .star-row span:nth-child(2) { animation-delay: 0.3s; color: #ff6b6b; }
        .star-row span:nth-child(3) { animation-delay: 0.6s; color: #87ceeb; }
        .star-row span:nth-child(4) { animation-delay: 0.9s; color: #98fb98; }
        .star-row span:nth-child(5) { animation-delay: 1.2s; color: #dda0dd; }
        @keyframes starTwinkle {
            0%, 100% { transform: scale(1) rotate(0deg); opacity: 1; }
            50% { transform: scale(1.3) rotate(15deg); opacity: 0.7; }
        }

        /* 安装按钮 */
        .hint-btn {
            display: inline-block;
            padding: 3px 10px;
            background: var(--warning-bg);
            color: var(--warning);
            border: 1px solid var(--warning);
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .hint-btn:hover {
            background: var(--warning);
            color: #000;
        }

        /* 节日装饰：工具卡片彩带 */
        .tool-btn { border-color: var(--border); }
        .tool-btn.active { border-color: #ffd700; }
        .tool-btn.active::after { background: #ffd700; }
        /* 节日装饰：按钮彩条 */
        .btn-scan {
            background: linear-gradient(135deg, #ff6b6b, #ffa07a, #ffd700) !important;
            box-shadow: 0 4px 20px rgba(255,107,107,0.35) !important;
        }
        .btn-scan:hover:not(:disabled) {
            box-shadow: 0 6px 28px rgba(255,107,107,0.5) !important;
        }
        .btn-recover {
            background: linear-gradient(135deg, #4facfe, #00f2fe, #43e97b) !important;
            box-shadow: 0 4px 20px rgba(67,233,123,0.35) !important;
        }
        .btn-recover:hover:not(:disabled) {
            box-shadow: 0 6px 28px rgba(67,233,123,0.5) !important;
        }
        /* 节日装饰：logo 特效 */
        .logo-icon { filter: drop-shadow(0 0 24px rgba(255,215,0,0.4)); }
        /* 节日装饰：标题 */
        h1 {
            background: linear-gradient(135deg, #ffd700, #ff6b6b, #dda0dd, #87ceeb, #98fb98) !important;
            background-size: 200% 200% !important;
            -webkit-background-clip: text !important;
            -webkit-text-fill-color: transparent !important;
            background-clip: text !important;
            animation: titleRainbow 4s ease infinite !important;
        }
        @keyframes titleRainbow {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        /* 节日装饰：驱动器卡片选中态 */
        .drive-card.selected {
            border-color: #ffd700;
            box-shadow: 0 4px 20px rgba(255,215,0,0.2);
        }
        .drive-card:hover { border-color: #ff6b9d; }

        /* 工具切换卡片 */
        .tool-switch {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            margin-bottom: 26px;
        }
        .tool-btn {
            background: var(--card);
            border: 2px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 18px 16px;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            text-align: center;
            position: relative;
            overflow: hidden;
        }
        .tool-btn::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            opacity: 0;
            transition: opacity 0.3s;
        }
        .tool-btn:hover {
            border-color: var(--accent);
            box-shadow: 0 4px 20px var(--accent-glow), var(--shadow-sm);
            transform: translateY(-2px);
        }
        .tool-btn.active {
            border-color: var(--accent);
            background: linear-gradient(135deg, rgba(108,99,255,0.15), rgba(118,75,162,0.1));
            box-shadow: 0 4px 24px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.05);
        }
        .tool-btn.active::after {
            content: '';
            position: absolute;
            bottom: 0; left: 50%;
            transform: translateX(-50%);
            width: 40px; height: 3px;
            background: var(--accent);
            border-radius: 3px 3px 0 0;
        }
        .tool-icon {
            font-size: 2rem;
            display: block;
            margin-bottom: 8px;
        }
        .tool-name {
            font-weight: 700;
            font-size: 1rem;
            color: var(--text);
            margin-bottom: 4px;
        }
        .tool-desc {
            font-size: 0.78rem;
            color: var(--text-dim);
        }
        .tool-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.65rem;
            font-weight: 600;
            margin-top: 8px;
            letter-spacing: 0.5px;
        }
        .badge-cli { background: var(--info-bg); color: var(--info); }
        .badge-gui { background: var(--success-bg); color: var(--success); }
        .tool-btn.disabled {
            opacity: 0.4;
            cursor: not-allowed;
            pointer-events: none;
        }

        /* 区块标题 */
        .section {
            margin-bottom: 22px;
        }
        .section-title {
            font-size: 0.88rem;
            font-weight: 700;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .section-title::after {
            content: '';
            flex: 1;
            height: 1px;
            background: linear-gradient(90deg, var(--border), transparent);
        }

        /* 驱动器列表 */
        .drive-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
            gap: 12px;
        }
        .drive-card {
            background: var(--card);
            border: 2px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 16px 12px;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            text-align: center;
            position: relative;
        }
        .drive-card:hover {
            border-color: var(--info);
            box-shadow: 0 4px 16px rgba(84,160,255,0.15);
            transform: translateY(-2px);
        }
        .drive-card.selected {
            border-color: var(--success);
            background: linear-gradient(135deg, rgba(0,214,143,0.08), rgba(0,212,255,0.05));
            box-shadow: 0 4px 20px rgba(0,214,143,0.15);
        }
        .drive-letter {
            font-size: 1.8rem;
            font-weight: 800;
            background: var(--gradient-3);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .drive-info {
            font-size: 0.72rem;
            color: var(--text-dim);
            margin-top: 6px;
            line-height: 1.5;
        }
        .drive-bar {
            width: 100%;
            height: 4px;
            background: var(--border);
            border-radius: 2px;
            margin-top: 8px;
            overflow: hidden;
        }
        .drive-bar-fill {
            height: 100%;
            border-radius: 2px;
            transition: width 0.5s ease;
        }
        .bar-low { background: var(--success); }
        .bar-mid { background: var(--warning); }
        .bar-high { background: var(--danger); }

        /* 操作按钮 */
        .action-row {
            display: flex;
            gap: 12px;
            margin-bottom: 16px;
        }
        .btn {
            flex: 1;
            padding: 16px 20px;
            border: none;
            border-radius: var(--radius-sm);
            font-size: 0.98rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            position: relative;
            overflow: hidden;
        }
        .btn::after {
            content: '';
            position: absolute;
            top: 50%; left: 50%;
            width: 0; height: 0;
            background: rgba(255,255,255,0.15);
            border-radius: 50%;
            transform: translate(-50%, -50%);
            transition: width 0.4s, height 0.4s;
        }
        .btn:active::after { width: 300px; height: 300px; }
        .btn-scan {
            background: var(--gradient-1);
            color: white;
            box-shadow: 0 4px 16px rgba(108,99,255,0.3);
        }
        .btn-scan:hover:not(:disabled) {
            box-shadow: 0 6px 24px rgba(108,99,255,0.45);
            transform: translateY(-2px);
        }
        .btn-recover {
            background: linear-gradient(135deg, #11998e, #38ef7d);
            color: white;
            box-shadow: 0 4px 16px rgba(56,239,125,0.3);
        }
        .btn-recover:hover:not(:disabled) {
            box-shadow: 0 6px 24px rgba(56,239,125,0.45);
            transform: translateY(-2px);
        }
        .btn:disabled {
            opacity: 0.35;
            cursor: not-allowed;
            box-shadow: none !important;
            transform: none !important;
        }

        /* 状态消息 */
        .status {
            padding: 14px 18px;
            border-radius: var(--radius-xs);
            font-size: 0.88rem;
            display: none;
            align-items: center;
            gap: 10px;
            line-height: 1.5;
            animation: statusIn 0.3s ease;
        }
        @keyframes statusIn {
            from { opacity: 0; transform: translateY(-8px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .status.show { display: flex; }
        .status.success { background: var(--success-bg); color: var(--success); border-left: 3px solid var(--success); }
        .status.error { background: var(--danger-bg); color: var(--danger); border-left: 3px solid var(--danger); }
        .status.info { background: var(--info-bg); color: var(--info); border-left: 3px solid var(--info); }
        .status.warning { background: var(--warning-bg); color: var(--warning); border-left: 3px solid var(--warning); }
        .status-icon { font-size: 1.2rem; flex-shrink: 0; }

        /* 工具提示 */
        .tool-hint {
            font-size: 0.76rem;
            color: var(--text-dim);
            text-align: center;
            padding: 10px;
            background: rgba(255,170,0,0.06);
            border: 1px dashed var(--warning);
            border-radius: var(--radius-xs);
            margin-top: 10px;
            display: none;
        }
        .tool-hint.show { display: block; }

        /* 底部信息 */
        .footer {
            text-align: center;
            padding: 30px 0 10px;
            color: var(--text-dim);
            font-size: 0.75rem;
            opacity: 0.5;
        }

        /* 响应式 */
        @media (max-width: 480px) {
            .container { padding: 16px 12px 40px; }
            h1 { font-size: 1.7rem; }
            .logo-icon { font-size: 2.8rem; }
            .header { padding: 24px 12px 24px; }
            .action-row { flex-direction: column; }
            .drive-list { grid-template-columns: repeat(auto-fill, minmax(90px, 1fr)); }
            .children-day-banner { font-size: 0.85rem; padding: 11px 16px; }
        }

        /* 加载动画 */
        .loading-dot {
            display: inline-block;
            animation: dotPulse 1.2s ease-in-out infinite;
        }
        .loading-dot:nth-child(2) { animation-delay: 0.2s; }
        .loading-dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes dotPulse {
            0%, 80%, 100% { opacity: 0.3; }
            40% { opacity: 1; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <div class="logo-icon">💾</div>
            <h1>QRecover</h1>
            <p class="subtitle">专业数据恢复工具集</p>
        </div>

        <!-- ═══ 61儿童节特别版 ═══ -->
        <canvas id="confetti-canvas"></canvas>
        <div class="star-row">
            <span>⭐</span><span>🌟</span><span>✨</span><span>💫</span><span>⭐</span>
        </div>
        <div class="children-day-banner">
            <div class="banner-balloons"><span>🎈</span><span>🎈</span><span>🎈</span></div>
            <div class="banner-title">🎠 六一儿童节快乐！</div>
            <div class="banner-sub">愿每个大人的心里，都住着一个快乐的小孩 🍬</div>
        </div>

        <!-- 工具切换 -->
        <div class="tool-switch">
            <div class="tool-btn active" id="btnTestDisk" onclick="switchTool('testdisk')">
                <span class="tool-icon">🛠️</span>
                <div class="tool-name">TestDisk</div>
                <div class="tool-desc">分区表修复 & 文件恢复</div>
                <span class="tool-badge badge-cli">CLI / TUI</span>
            </div>
            <div class="tool-btn" id="btnRecuva" onclick="switchTool('recuva')">
                <span class="tool-icon">🎨</span>
                <div class="tool-name">Recuva</div>
                <div class="tool-desc">图形化文件恢复向导</div>
                <span class="tool-badge badge-gui">GUI</span>
            </div>
        </div>

        <!-- 驱动器选择 -->
        <div class="section">
            <div class="section-title">📀 选择目标驱动器</div>
            <div class="drive-list" id="driveList"></div>
        </div>

        <!-- 操作按钮 -->
        <div class="action-row">
            <button class="btn btn-scan" id="btnScan" disabled onclick="scanDrive()">
                🔍 扫描磁盘
            </button>
            <button class="btn btn-recover" id="btnRecover" disabled onclick="recoverFiles()">
                💾 恢复文件
            </button>
        </div>

        <!-- 状态消息 -->
        <div class="status" id="status"></div>

        <!-- 工具提示 -->
        <div class="tool-hint" id="recuvaHint">
            ⚠️ Recuva 未安装。请先&nbsp;
            <button class="hint-btn" onclick="installRecuva()">下载安装 Recuva</button>
            &nbsp;或使用 TestDisk 进行恢复。
        </div>

        <!-- 底部 -->
        <div class="footer">
            QRecover v1.1.4 · Powered by Flask · 💻 Made with ❤️
        </div>
    </div>

    <script>
        let selectedDrive = null;
        let currentTool = 'testdisk';
        let isProcessing = false;

        // 切换工具
        function switchTool(tool) {
            if (isProcessing) return;
            currentTool = tool;
            
            document.getElementById('btnTestDisk').classList.toggle('active', tool === 'testdisk');
            document.getElementById('btnRecuva').classList.toggle('active', tool === 'recuva');
            
            // 更新按钮状态
            updateButtons();
        }

        // 更新按钮状态
        function updateButtons() {
            const canAct = selectedDrive && !isProcessing;
            document.getElementById('btnScan').disabled = !canAct;
            document.getElementById('btnRecover').disabled = !canAct;
        }

        // 加载驱动器
        async function loadDrives() {
            try {
                const res = await fetch('/api/drives');
                const drives = await res.json();
                const list = document.getElementById('driveList');
                
                // 保留选中状态
                const prevSelected = selectedDrive;
                list.innerHTML = '';
                
                if (drives.length === 0) {
                    list.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-dim);padding:24px;">未检测到可用驱动器</div>';
                    return;
                }
                
                drives.forEach(d => {
                    const card = document.createElement('div');
                    card.className = 'drive-card' + (d.letter === prevSelected ? ' selected' : '');
                    
                    const usedPercent = parseInt(d.used) || 0;
                    let barClass = 'bar-low';
                    if (usedPercent > 80) barClass = 'bar-high';
                    else if (usedPercent > 50) barClass = 'bar-mid';
                    
                    card.innerHTML = `
                        <div class="drive-letter">${d.letter}:</div>
                        <div class="drive-info">${d.total} GB 总容量</div>
                        <div class="drive-info">${d.free} GB 可用</div>
                        <div class="drive-bar"><div class="drive-bar-fill ${barClass}" style="width:${d.used}"></div></div>
                    `;
                    card.onclick = () => selectDrive(d.letter, card);
                    list.appendChild(card);
                });
            } catch (e) {
                showStatus('error', '加载驱动器列表失败：' + e.message);
            }
        }

        function selectDrive(letter, card) {
            if (isProcessing) return;
            selectedDrive = letter;
            document.querySelectorAll('.drive-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            updateButtons();
        }

        // 显示状态消息
        function showStatus(type, msg) {
            const el = document.getElementById('status');
            const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
            el.className = `status show ${type}`;
            el.innerHTML = `<span class="status-icon">${icons[type] || '📋'}</span><span>${msg}</span>`;
        }

        function hideStatus() {
            document.getElementById('status').className = 'status';
        }

        // 扫描
        async function scanDrive() {
            if (!selectedDrive || isProcessing) return;
            isProcessing = true;
            updateButtons();
            hideStatus();

            try {
                const res = await fetch(`/api/scan?drive=${selectedDrive}&tool=${currentTool}`);
                const data = await res.json();
                if (data.status === 'ok') {
                    showStatus('success', data.message);
                } else {
                    showStatus('error', data.message);
                }
            } catch (e) {
                showStatus('error', '网络请求失败：' + e.message);
            } finally {
                isProcessing = false;
                updateButtons();
            }
        }

        // 恢复
        async function recoverFiles() {
            if (!selectedDrive || isProcessing) return;
            isProcessing = true;
            updateButtons();
            hideStatus();

            try {
                const res = await fetch(`/api/recover?drive=${selectedDrive}&tool=${currentTool}`);
                const data = await res.json();
                if (data.status === 'ok') {
                    showStatus('success', data.message);
                } else {
                    showStatus('error', data.message);
                }
            } catch (e) {
                showStatus('error', '网络请求失败：' + e.message);
            } finally {
                isProcessing = false;
                updateButtons();
            }
        }

        // 安装 Recuva
        async function installRecuva() {
            try {
                const res = await fetch('/api/install_recuva');
                const data = await res.json();
                if (data.status === 'ok' && data.action === 'open_url') {
                    window.open(data.url, '_blank');
                    showStatus('info', data.message);
                } else if (data.status === 'ok') {
                    showStatus('info', data.message || 'Recuva 安装程序已启动，请在弹出的窗口中完成安装。');
                } else {
                    showStatus('error', data.message);
                }
            } catch (e) {
                showStatus('error', '启动安装程序失败：' + e.message);
            }
        }

        // 检查工具状态
        async function checkTools() {
            try {
                const res = await fetch('/api/tools');
                const tools = await res.json();
                
                const recuvaBtn = document.getElementById('btnRecuva');
                const hint = document.getElementById('recuvaHint');
                
                if (!tools.recuva) {
                    recuvaBtn.classList.add('disabled');
                    hint.classList.add('show');
                    if (currentTool === 'recuva') {
                        switchTool('testdisk');
                    }
                } else {
                    recuvaBtn.classList.remove('disabled');
                    hint.classList.remove('show');
                }
                
                if (!tools.testdisk) {
                    document.getElementById('btnTestDisk').classList.add('disabled');
                }
            } catch (e) {
                console.warn('工具检测失败:', e);
            }
        }

        // 初始化
        async function init() {
            await checkTools();
            await loadDrives();
        }

        init();
        setInterval(loadDrives, 15000);

        // ═══ 61 儿童节彩屑动画 ═══
        (function() {
            const canvas = document.getElementById('confetti-canvas');
            const ctx = canvas.getContext('2d');
            let W, H;
            const colors = ['#ff6b6b','#ffa07a','#ffd700','#98fb98','#87ceeb','#dda0dd','#ff69b4','#00ced1'];
            const shapes = ['circle','rect','star'];
            let particles = [];

            function resize() {
                W = canvas.width = window.innerWidth;
                H = canvas.height = window.innerHeight;
            }
            resize();
            window.addEventListener('resize', resize);

            class Particle {
                constructor() { this.reset(true); }
                reset(init) {
                    this.x = Math.random() * W;
                    this.y = init ? Math.random() * H : -10;
                    this.size = Math.random() * 6 + 3;
                    this.color = colors[Math.floor(Math.random() * colors.length)];
                    this.shape = shapes[Math.floor(Math.random() * shapes.length)];
                    this.vy = Math.random() * 1.2 + 0.4;
                    this.vx = Math.random() * 0.6 - 0.3;
                    this.rot = Math.random() * 360;
                    this.rotV = Math.random() * 3 - 1.5;
                    this.opacity = Math.random() * 0.5 + 0.3;
                }
                update() {
                    this.y += this.vy;
                    this.x += this.vx + Math.sin(this.y * 0.01) * 0.3;
                    this.rot += this.rotV;
                    if (this.y > H + 10) this.reset(false);
                }
                draw() {
                    ctx.save();
                    ctx.translate(this.x, this.y);
                    ctx.rotate(this.rot * Math.PI / 180);
                    ctx.globalAlpha = this.opacity;
                    ctx.fillStyle = this.color;
                    if (this.shape === 'circle') {
                        ctx.beginPath();
                        ctx.arc(0, 0, this.size, 0, Math.PI * 2);
                        ctx.fill();
                    } else if (this.shape === 'rect') {
                        ctx.fillRect(-this.size, -this.size/2, this.size*2, this.size);
                    } else {
                        drawStar(ctx, 0, 0, 5, this.size, this.size/2);
                    }
                    ctx.restore();
                }
            }

            function drawStar(ctx, cx, cy, spikes, outerR, innerR) {
                let rot = -Math.PI / 2;
                const step = Math.PI / spikes;
                ctx.beginPath();
                for (let i = 0; i < spikes * 2; i++) {
                    const r = i % 2 === 0 ? outerR : innerR;
                    ctx.lineTo(cx + Math.cos(rot) * r, cy + Math.sin(rot) * r);
                    rot += step;
                }
                ctx.closePath();
                ctx.fill();
            }

            for (let i = 0; i < 35; i++) particles.push(new Particle());

            function animate() {
                ctx.clearRect(0, 0, W, H);
                particles.forEach(p => { p.update(); p.draw(); });
                requestAnimationFrame(animate);
            }
            animate();
        })();
    </script>
</body>
</html>"""

@app.route('/api/tools')
def api_tools():
    """返回可用工具列表"""
    tools = {
        "testdisk": os.path.isfile(TESTDISK_EXE),
        "recuva": find_recuva() is not None
    }
    return jsonify(tools)

RECUVA_DOWNLOAD_URL = 'https://www.ccleaner.com/recuva/download'

@app.route('/api/install_recuva')
def api_install_recuva():
    """启动 Recuva 安装程序或打开下载页面"""
    if os.path.isfile(RECUVA_INSTALLER):
        try:
            subprocess.Popen([RECUVA_INSTALLER], shell=True)
            return jsonify({"status": "ok", "message": "Recuva 安装程序已启动，请在弹出的窗口中完成安装。"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"启动安装程序失败: {e}"})
    else:
        return jsonify({"status": "ok", "action": "open_url", "url": RECUVA_DOWNLOAD_URL, "message": "请先下载安装 Recuva。"})

@app.route('/api/drives')
def api_drives():
    return jsonify(get_drives())

@app.route('/api/scan')
def api_scan():
    """启动扫描工具"""
    drive = request.args.get('drive', '')
    tool = request.args.get('tool', 'testdisk')
    
    if not drive or len(drive) != 1 or not drive.isalpha():
        log.warning(f'Invalid drive param: {drive!r}')
        return jsonify({"status": "error", "message": "无效的盘符参数"}), 400
    
    if tool == 'testdisk':
        if not os.path.isfile(TESTDISK_EXE):
            return jsonify({"status": "error", "message": f"TestDisk not found at {TESTDISK_EXE}"})
        try:
            run_tool(TESTDISK_EXE, TESTDISK_DIR)
            return jsonify({"status": "ok", "message": f"✅ TestDisk 已在新窗口启动（UAC 提示已弹出），请在 TestDisk 窗口中选择 {drive}: 盘进行扫描操作。"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start TestDisk: {e}"})
    
    elif tool == 'recuva':
        recuva = find_recuva()
        if not recuva:
            return jsonify({"status": "error", "message": "Recuva 未找到，请先安装 Recuva。"})
        try:
            run_tool(recuva)
            return jsonify({"status": "ok", "message": f"✅ Recuva 已启动，请在 Recuva 窗口中选择 {drive}: 盘进行扫描。"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start Recuva: {e}"})
    
    return jsonify({"status": "error", "message": "Invalid tool specified"})

@app.route('/api/recover')
def api_recover():
    """启动恢复工具"""
    drive = request.args.get('drive', '')
    tool = request.args.get('tool', 'testdisk')
    out_dir = request.args.get('out', os.path.expanduser("~\\Recovered"))
    os.makedirs(out_dir, exist_ok=True)

    if not drive or len(drive) != 1 or not drive.isalpha():
        log.warning('Invalid drive param in recover: %r', drive)
        return jsonify({"status": "error", "message": "无效的盘符参数"}), 400

    if tool == 'testdisk':
        if not os.path.isfile(PHOTOREC_EXE):
            return jsonify({"status": "error", "message": f"PhotoRec not found at {PHOTOREC_EXE}"})
        try:
            run_tool(PHOTOREC_EXE, TESTDISK_DIR)
            return jsonify({"status": "ok", "message": f"✅ PhotoRec 已在新窗口启动（UAC 提示已弹出）！恢复的文件将保存到: {out_dir}"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start PhotoRec: {e}"})
    
    elif tool == 'recuva':
        recuva = find_recuva()
        if not recuva:
            return jsonify({"status": "error", "message": "Recuva 未找到，请先安装 Recuva。"})
        try:
            run_tool(recuva)
            return jsonify({"status": "ok", "message": f"✅ Recuva 已启动！请在 Recuva 中选择恢复路径: {out_dir}"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start Recuva: {e}"})
    
    return jsonify({"status": "error", "message": "Invalid tool specified"})

# ─────────── Main ───────────
def main():
    print("Starting QRecover Web UI v1.1.4...")
    print("Open browser at: http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == "__main__":
    main()
