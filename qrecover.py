#!/usr/bin/env python3
"""QRecover Web UI - TestDisk/PhotoRec/Recuva GUI"""
import os
import sys
import shutil
import subprocess
import ctypes
import ctypes.wintypes
import webbrowser
import logging
import threading
import time
import ctypes.wintypes
from flask import Flask, render_template_string, request, jsonify

# ── 单实例检测：确保只有一个 QRecoverWeb 进程运行 ──
def ensure_single_instance():
    """确保只有一个实例运行，如果有旧进程则终止它"""
    current_pid = os.getpid()
    
    # 查找所有 QRecoverWeb 进程
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq QRecoverWeb.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if 'QRecoverWeb.exe' in line:
                # CSV 格式: "QRecoverWeb.exe","PID","Session Name",...
                parts = line.split(',')
                if len(parts) >= 2:
                    pid_str = parts[1].strip('"')
                    try:
                        pid = int(pid_str)
                        if pid != current_pid:
                            logging.info(f"终止旧进程 PID={pid}")
                            subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                                         capture_output=True, timeout=5)
                            time.sleep(0.5)
                    except ValueError:
                        pass
    except Exception as e:
        logging.warning(f"检测旧进程失败: {e}")
    
    # 使用互斥体防止竞态
    mutex_name = "Global\\QRecoverWeb_SingleInstance"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    return mutex

# ── 全局进程锁：同一时间只能有一个工具进程 ──
_ACTIVE_PROCESS = None  # 可以是 subprocess.Popen 对象，或 None
_ACTIVE_PROCESS_LOCK = threading.Lock()

def _check_and_clear_process():
    """检查全局进程是否还在运行，如果已结束则清除（线程安全）"""
    global _ACTIVE_PROCESS
    with _ACTIVE_PROCESS_LOCK:
        if _ACTIVE_PROCESS is not None:
            # 如果是 Popen 对象
            if hasattr(_ACTIVE_PROCESS, 'poll'):
                if _ACTIVE_PROCESS.poll() is not None:
                    _ACTIVE_PROCESS = None
            # 如果是 pid (int)
            elif isinstance(_ACTIVE_PROCESS, int):
                try:
                    proc = subprocess.Popen(['tasklist', '/fi', f'PID eq {_ACTIVE_PROCESS}', '/nh'],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    out, _ = proc.communicate()
                    if isinstance(out, bytes):
                        out = out.decode('gbk', errors='replace')
                    if str(_ACTIVE_PROCESS) not in out:
                        _ACTIVE_PROCESS = None
                except Exception:
                    _ACTIVE_PROCESS = None

def run_tool(exe_path, work_dir=None):
    """启动工具，自动处理 UAC 提权。同一时间只能有一个进程。"""
    global _ACTIVE_PROCESS
    
    if not os.path.isfile(exe_path):
        raise FileNotFoundError(f"找不到: {exe_path}")
    
    # 检查是否已有进程在运行
    _check_and_clear_process()
    with _ACTIVE_PROCESS_LOCK:
        if _ACTIVE_PROCESS is not None:
            raise RuntimeError("已有工具进程在运行中，请先关闭当前工具窗口。")
    
    try:
        # 先尝试直接 Popen（EXE 已是管理员时直接继承）
        proc = subprocess.Popen(
            [exe_path],
            cwd=work_dir or os.path.dirname(exe_path),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        with _ACTIVE_PROCESS_LOCK:
            _ACTIVE_PROCESS = proc
    except OSError as e:
        if getattr(e, 'winerror', None) == 740 or '740' in str(e):
            # WinError 740 = 需要提权，用 ShellExecuteW 触发 UAC
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe_path, None,
                work_dir or os.path.dirname(exe_path), 1
            )
            if ret <= 32:
                raise RuntimeError(f"UAC 提权失败 (ShellExecuteW 返回 {ret})")
            # ShellExecuteW 成功，但无法获取进程句柄
            # 用后台线程定期检测进程是否存在（按 exe 文件名）
            with _ACTIVE_PROCESS_LOCK:
                _ACTIVE_PROCESS = True  # 哨兵值，表示有进程但无法追踪
            _start_process_watcher(os.path.basename(exe_path))
        else:
            raise
    return True

def _start_process_watcher(exe_name):
    """后台线程：定期检测指定 exe 是否还在运行，退出时清除锁"""
    def watcher():
        global _ACTIVE_PROCESS
        while True:
            time.sleep(3)
            # 检查进程是否还在运行
            try:
                proc = subprocess.Popen(
                    ['tasklist', '/fi', f'IMAGENAME eq {exe_name}', '/nh', '/fo', 'csv'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                out, _ = proc.communicate(timeout=5)
                if isinstance(out, bytes):
                    out = out.decode('gbk', errors='replace')
                # 如果 tasklist 输出不包含 exe 名，说明进程已退出
                if exe_name.lower() not in out.lower():
                    with _ACTIVE_PROCESS_LOCK:
                        _ACTIVE_PROCESS = None
                    break
            except Exception:
                with _ACTIVE_PROCESS_LOCK:
                    _ACTIVE_PROCESS = None
                break
    t = threading.Thread(target=watcher, daemon=True)
    t.start()

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
RECUVAPATHS = [
    r"C:\Program Files\Recuva\recuva.exe",
    r"C:\Program Files (x86)\Recuva\recuva.exe",
    os.path.join(BASE_DIR, "recuva_portable", "recuva.exe"),
]
RECUVAIINSTALLER = os.path.join(BASE_DIR, "Recuva_1.54.120_Machine_X64_nullsoft_en-US.exe")

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
    for path in RECUVAPATHS:
        if os.path.isfile(path):
            return path
    return None

# ─────────── API ───────────
@app.route('/api/tools')
def api_tools():
    """返回可用工具列表"""
    tools = {
        "testdisk": os.path.isfile(TESTDISK_EXE),
        "recuva": find_recuva() is not None
    }
    return jsonify(tools)

RECUVADOWNLOADURL = 'https://www.ccleaner.com/recuva/download'

@app.route('/api/status')
def api_status():
    """返回当前是否有工具进程在运行"""
    global _ACTIVE_PROCESS
    _check_and_clear_process()
    with _ACTIVE_PROCESS_LOCK:
        if _ACTIVE_PROCESS is not None:
            return jsonify({"status": "busy", "message": "有工具进程正在运行中，请先关闭当前工具窗口。"})
        else:
            return jsonify({"status": "idle", "message": "无工具进程运行。"})

@app.route('/api/install_recuva')
def api_install_recuva():
    """启动 Recuva 安装程序或打开下载页面"""
    if os.path.isfile(RECUVAIINSTALLER):
        try:
            subprocess.Popen([RECUVAIINSTALLER], shell=True)
            return jsonify({"status": "ok", "message": "Recuva 安装程序已启动，请在弹出的窗口中完成安装。"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"启动安装程序失败: {e}"})
    else:
        return jsonify({"status": "ok", "action": "open_url", "url": RECUVADOWNLOADURL, "message": "请先下载安装 Recuva。"})

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

# ─────────── HTML Template ───────────
HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QRecover v1.1.4</title>
    <style>
        :root {
            --bg: #0a0a0f;
            --card: #12121a;
            --card-hover: #1a1a26;
            --border: #1e1e2e;
            --text: #e8e8f0;
            --text-dim: #8888a0;
            --accent: #6c63ff;
            --accent-glow: rgba(108,99,255,0.25);
            --success: #00d687;
            --success-bg: rgba(0,214,135,0.08);
            --warning: #ffaa00;
            --warning-bg: rgba(255,170,0,0.08);
            --danger: #ff4757;
            --danger-bg: rgba(255,71,87,0.08);
            --info: #54a0ff;
            --info-bg: rgba(84,160,255,0.08);
            --gradient-1: linear-gradient(135deg, #6c63ff, #764ba2);
            --gradient-2: linear-gradient(135deg, #00d687, #00d4ff);
            --gradient-3: linear-gradient(135deg, #ff6b6b, #ffa07a);
            --radius-sm: 10px;
            --radius-xs: 8px;
            --shadow-sm: 0 2px 8px rgba(0,0,0,0.3);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            line-height: 1.6;
        }
        .container { max-width: 640px; margin: 0 auto; padding: 20px 16px 60px; }
        
        /* 头部 */
        .header {
            text-align: center;
            padding: 32px 16px 24px;
            position: relative;
        }
        .logo-icon { font-size: 3.5rem; margin-bottom: 8px; display: block; }
        h1 {
            font-size: 2rem;
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

        /* ══ 61 儿童节特别版 ══ */
        #confetti-canvas {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            pointer-events: none;
            z-index: 9999;
        }
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
        .logo-icon { filter: drop-shadow(0 0 24px rgba(255,215,0,0.4)); }
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
        .drive-card:hover { border-color: #ff6b9d; }
        .drive-card.selected {
            border-color: #ffd700;
            box-shadow: 0 4px 20px rgba(255,215,0,0.2);
        }

        /* 工具切换卡片（滑动式切换器） */
        .tool-switch {
            position: relative;
            background: var(--card);
            border: 2px solid var(--border);
            border-radius: 14px;
            padding: 6px;
            display: flex;
            margin-bottom: 26px;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.2);
        }
        /* 滑动指示器 */
        .tool-switch {
            --slide-bg: var(--gradient-1);
        }
        .tool-switch::before {
            content: '';
            position: absolute;
            top: 6px; left: 6px;
            width: calc(50% - 6px);
            height: calc(100% - 12px);
            background: var(--slide-bg);
            border-radius: 10px;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 0;
            box-shadow: 0 2px 12px var(--accent-glow);
        }
        .tool-switch[data-tool="recuva"]::before {
            transform: translateX(100%);
        }
        .tool-btn {
            flex: 1;
            background: transparent;
            border: none;
            border-radius: 10px;
            padding: 16px 12px;
            cursor: pointer;
            transition: all 0.3s;
            text-align: center;
            position: relative;
            z-index: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
        }
        .tool-btn:hover:not(.disabled) {
            background: rgba(255,255,255,0.05);
        }
        .tool-btn.disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }
        .tool-icon {
            font-size: 1.8rem;
            display: block;
            filter: drop-shadow(0 2px 4px rgba(0,0,0,0.3));
            transition: transform 0.3s;
        }
        .tool-btn.active .tool-icon {
            transform: scale(1.15);
        }
        .tool-name {
            font-weight: 700;
            font-size: 0.9rem;
            color: var(--text);
        }
        .tool-desc {
            font-size: 0.72rem;
            color: var(--text-dim);
            line-height: 1.3;
        }
        .tool-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        .badge-cli { background: var(--info-bg); color: var(--info); }
        .badge-gui { background: var(--success-bg); color: var(--success); }
        .badge-unavailable { background: var(--danger-bg); color: var(--danger); }

        /* 区块标题 */
        .section {
            margin-bottom: 22px;
        }
        /* 主题切换器 */
        .theme-switcher {
            position: relative; /* 修复：relative 让 picker 下拉正确定位 */
            display: flex;
            align-items: center;
            gap: 6px;
            z-index: 10;
            margin-top: 8px;
        }
        .theme-label {
            font-size: 0.78rem;
            color: var(--text-dim);
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 4px 12px;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .theme-label:hover {
            border-color: var(--accent);
            color: var(--accent);
        }
        .theme-picker {
            display: none;
            position: absolute;
            top: calc(100% + 6px);
            right: 0;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 6px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
            gap: 4px;
            flex-direction: row;
            z-index: 100;
        }
        .theme-picker.show { display: flex; z-index: 999; position: absolute; top: calc(100% + 4px); right: 0; box-shadow: 0 8px 24px rgba(0,0,0,0.18); }
        .theme-btn {
            width: 32px;
            height: 32px;
            border: 2px solid transparent;
            border-radius: 8px;
            background: var(--bg);
            cursor: pointer;
            font-size: 1.1rem;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            opacity: 0.6;
        }
        .theme-btn:hover {
            opacity: 1;
            transform: scale(1.15);
        }
        .theme-btn.active {
            border-color: var(--accent);
            opacity: 1;
            box-shadow: 0 0 8px var(--accent-glow);
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
            <!-- 主题切换器 -->
            <div class="theme-switcher">
                <span class="theme-label" id="currentTheme">🌸 紫罗兰</span>
                <div class="theme-picker" id="themePicker">
                    <button class="theme-btn active" data-theme="violet" onclick="applyTheme('violet')" title="紫罗兰">🌸</button>
                    <button class="theme-btn" data-theme="aurora" onclick="applyTheme('aurora')" title="极光绿">🌿</button>
                    <button class="theme-btn" data-theme="sunset" onclick="applyTheme('sunset')" title="落日橙">🌅</button>
                    <button class="theme-btn" data-theme="cyber" onclick="applyTheme('cyber')" title="赛博朋克">🤖</button>
                </div>
            </div>
        </div>

        <!-- ══ 状态栏 ══ -->
        <div class="status-bar">
            <span>🛡️ QRecover Web UI</span>
            <span class="status-sep">|</span>
            <span id="processStatus">空闲中</span>
        </div>

        <!-- 工具切换（滑动式） -->
        <div class="tool-switch" id="toolSwitch" data-tool="testdisk">
            <div class="tool-btn active" id="btnTestDisk" onclick="switchTool('testdisk')">
                <span class="tool-icon">🔧</span>
                <div class="tool-name">TestDisk</div>
                <div class="tool-desc">分区表修复 & 文件恢复</div>
                <span class="tool-badge badge-cli" id="badgeTestDisk">CLI / TUI</span>
            </div>
            <div class="tool-btn" id="btnRecuva" onclick="switchTool('recuva')">
                <span class="tool-icon">🎨</span>
                <div class="tool-name">Recuva</div>
                <div class="tool-desc">图形化文件恢复向导</div>
                <span class="tool-badge badge-gui" id="badgeRecuva">GUI</span>
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
        
        // ── 主题切换 ──
        const THEMES = {
            violet: {
                name: '紫罗兰',
                icon: '🌸',
                accent: '#6c63ff',
                'accent-rgb': '108,99,255',
                gradient1: 'linear-gradient(135deg, #6c63ff, #764ba2)',
                gradient2: 'linear-gradient(135deg, #00d687, #00d4ff)',
                gradient3: 'linear-gradient(135deg, #ff6b6b, #ffa07a)',
            },
            aurora: {
                name: '极光绿',
                icon: '🌿',
                accent: '#00d687',
                'accent-rgb': '0,214,135',
                gradient1: 'linear-gradient(135deg, #00d687, #00d4ff)',
                gradient2: 'linear-gradient(135deg, #6c63ff, #00d4ff)',
                gradient3: 'linear-gradient(135deg, #43e97b, #38f9d7)',
            },
            sunset: {
                name: '落日橙',
                icon: '🌅',
                accent: '#ff7e5f',
                'accent-rgb': '255,126,95',
                gradient1: 'linear-gradient(135deg, #ff7e5f, #feb47b)',
                gradient2: 'linear-gradient(135deg, #ff9a9e, #fecfef)',
                gradient3: 'linear-gradient(135deg, #ff7e5f, #feb47b)',
            },
            cyber: {
                name: '赛博朋克',
                icon: '🤖',
                accent: '#00f5ff',
                'accent-rgb': '0,245,255',
                gradient1: 'linear-gradient(135deg, #00f5ff, #7400b8)',
                gradient2: 'linear-gradient(135deg, #ff0a54, #00f5ff)',
                gradient3: 'linear-gradient(135deg, #7400b8, #ff0a54)',
            }
        };

        function getTheme() {
            return localStorage.getItem('qrecover-theme') || 'violet';
        }

        function applyTheme(name) {
            const theme = THEMES[name];
            if (!theme) return;
            const root = document.documentElement;
            root.style.setProperty('--accent', theme.accent);
            root.style.setProperty('--accent-glow', `rgba(${theme['accent-rgb']},0.25)`);
            root.style.setProperty('--gradient-1', theme.gradient1);
            root.style.setProperty('--gradient-2', theme.gradient2);
            root.style.setProperty('--gradient-3', theme.gradient3);
            localStorage.setItem('qrecover-theme', name);
            // 同时更新工具切换滑块颜色
            const ts = document.querySelector('.tool-switch');
            if (ts) ts.style.setProperty('--slide-bg', theme.gradient1);
            updateThemeUI(name);
            // 工具切换滑块颜色也要更新
            const ts = document.querySelector('.tool-switch');
            if (ts) {
                const bar = ts.querySelector('::before');
                // 直接更新背景
                ts.style.setProperty('--slide-bg', theme.gradient1);
            }
        }

        function updateThemeUI(name) {
            const theme = THEMES[name];
            if (!theme) return;
            // 更新切换器按钮文字
            const btns = document.querySelectorAll('.theme-btn');
            btns.forEach(b => {
                const n = b.dataset.theme;
                b.classList.toggle('active', n === name);
            });
            // 更新当前主题显示
            const display = document.getElementById('currentTheme');
            if (display) display.textContent = theme.icon + ' ' + theme.name;
        }

        function initTheme() {
            const saved = getTheme();
            applyTheme(saved);
        }

        // ── 工具切换 ──

        let currentTool = 'testdisk';
        let isProcessing = false;
        let statusTimer = null;

        // 切换工具
        function switchTool(tool) {
            if (isProcessing) return;
            
            // 检查工具是否可用
            const btn = document.getElementById(tool === 'testdisk' ? 'btnTestDisk' : 'btnRecuva');
            if (btn.classList.contains('disabled')) {
                showStatus('warning', '该工具未安装，请先安装后再切换。');
                return;
            }
            
            currentTool = tool;
            
            // 更新滑动指示器
            document.getElementById('toolSwitch').dataset.tool = tool;
            
            // 更新按钮激活状态
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
                // 不立即重置 isProcessing，等轮询检测到进程结束后重置
                // 启动状态轮询
                startStatusPolling();
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
                // 不立即重置 isProcessing，等轮询检测到进程结束后重置
                // 启动状态轮询
                startStatusPolling();
            }
        }

        // 状态轮询
        function startStatusPolling() {
            if (statusTimer) return; // 已经在轮询
            
            statusTimer = setInterval(async () => {
                try {
                    const res = await fetch('/api/status');
                    const data = await res.json();
                    
                    if (data.status === 'idle') {
                        // 进程已结束
                        isProcessing = false;
                        updateButtons();
                        clearInterval(statusTimer);
                        statusTimer = null;
                    }
                    // 如果还是 busy，继续轮询
                } catch (e) {
                    console.warn('状态轮询失败:', e);
                }
            }, 2000); // 每 2 秒轮询一次
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
                const recuvaBadge = document.getElementById('badgeRecuva');
                const hint = document.getElementById('recuvaHint');
                
                if (!tools.recuva) {
                    recuvaBtn.classList.add('disabled');
                    recuvaBadge.className = 'tool-badge badge-unavailable';
                    recuvaBadge.textContent = '未安装';
                    hint.classList.add('show');
                    if (currentTool === 'recuva') {
                        switchTool('testdisk');
                    }
                } else {
                    recuvaBtn.classList.remove('disabled');
                    recuvaBadge.className = 'tool-badge badge-gui';
                    recuvaBadge.textContent = 'GUI';
                    hint.classList.remove('show');
                }
                
                const testdiskBtn = document.getElementById('btnTestDisk');
                const testdiskBadge = document.getElementById('badgeTestDisk');
                if (!tools.testdisk) {
                    testdiskBtn.classList.add('disabled');
                    testdiskBadge.className = 'tool-badge badge-unavailable';
                    testdiskBadge.textContent = '未安装';
                } else {
                    testdiskBtn.classList.remove('disabled');
                    testdiskBadge.className = 'tool-badge badge-cli';
                    testdiskBadge.textContent = 'CLI / TUI';
                }
            } catch (e) {
                console.warn('工具检测失败:', e);
            }
        }

        // 初始化
        async function init() {
            await initTheme();
        checkTools();
            await loadDrives();
            // 初始检查一次进程状态
            const res = await fetch('/api/status');
            const data = await res.json();
            if (data.status === 'busy') {
                isProcessing = true;
                updateButtons();
                startStatusPolling();
            }
        }

        init();
        setInterval(loadDrives, 15000);

        // ══ 61 儿童节彩屑动画 ══
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
    
        // 点击外部关闭主题选择器
        document.addEventListener('click', function(e) {
            const picker = document.getElementById('themePicker');
            const label = document.getElementById('currentTheme');
            if (picker && label && !picker.contains(e.target) && !label.contains(e.target)) {
                picker.classList.remove('show');
            }
        });
        document.getElementById('currentTheme').addEventListener('click', function(e) {
            e.stopPropagation();
            document.getElementById('themePicker').classList.toggle('show');
        });
</script>
</body>
</html>
"""

# ─────────── Main ───────────
def main():
    # 单实例检测：确保只有一个进程
    mutex = ensure_single_instance()
    print("Starting QRecover Web UI v1.1.7...")
    print("Open browser at: http://127.0.0.1:5000")
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        # 程序退出时释放互斥体
        ctypes.windll.kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()
