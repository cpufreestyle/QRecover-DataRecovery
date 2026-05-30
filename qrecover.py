#!/usr/bin/env python3
"""QRecover Web UI - TestDisk/PhotoRec/Recuva GUI"""
import os
import sys
import shutil
import subprocess
from flask import Flask, render_template_string, request, jsonify

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# PyInstaller 打包后路径处理
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = SCRIPT_DIR

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
                except:
                    pass
    return drives

def find_recuva():
    """查找 Recuva 可执行文件"""
    for path in RECUVA_PATHS:
        if os.path.isfile(path):
            return path
    return None

# ─────────── HTML 模板 ───────────
HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QRecover - 数据恢复工具</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', sans-serif;
            background: #1e1e2e;
            color: #cdd6f4;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: #313244;
            border-radius: 16px;
            padding: 40px;
            max-width: 600px;
            width: 100%;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        h1 {
            text-align: center;
            font-size: 2rem;
            margin-bottom: 10px;
            color: #cba6f7;
        }
        .subtitle {
            text-align: center;
            color: #a6adc8;
            margin-bottom: 30px;
            font-size: 0.9rem;
        }
        .tool-switch {
            display: flex;
            gap: 10px;
            margin-bottom: 25px;
        }
        .tool-btn {
            flex: 1;
            padding: 12px;
            border: 2px solid #45475a;
            border-radius: 8px;
            background: #45475a;
            color: #cdd6f4;
            cursor: pointer;
            text-align: center;
            transition: all 0.2s;
            font-size: 0.9rem;
        }
        .tool-btn:hover { border-color: #89b4fa; }
        .tool-btn.active {
            border-color: #a6e3a1;
            background: #585b70;
        }
        .section {
            margin-bottom: 25px;
        }
        .section-title {
            font-size: 1rem;
            color: #89b4fa;
            margin-bottom: 12px;
            font-weight: 600;
        }
        .drive-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
            gap: 10px;
            margin-bottom: 15px;
        }
        .drive-card {
            background: #45475a;
            border: 2px solid transparent;
            border-radius: 8px;
            padding: 12px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
        }
        .drive-card:hover {
            border-color: #89b4fa;
            background: #585b70;
        }
        .drive-card.selected {
            border-color: #a6e3a1;
            background: #585b70;
        }
        .drive-letter {
            font-size: 1.5rem;
            font-weight: bold;
            color: #f5c2e7;
        }
        .drive-info {
            font-size: 0.75rem;
            color: #a6adc8;
            margin-top: 4px;
        }
        .btn {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 8px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            margin-bottom: 10px;
        }
        .btn-primary {
            background: #89b4fa;
            color: #1e1e2e;
        }
        .btn-primary:hover { background: #74c7ec; }
        .btn-primary:disabled {
            background: #585b70;
            color: #6c7086;
            cursor: not-allowed;
        }
        .btn-success {
            background: #a6e3a1;
            color: #1e1e2e;
        }
        .btn-success:hover { background: #94e2d5; }
        .btn-info {
            background: #f5c2e7;
            color: #1e1e2e;
        }
        .btn-info:hover { background: #eba0ac; }
        .status {
            margin-top: 15px;
            padding: 12px;
            border-radius: 8px;
            font-size: 0.9rem;
            display: none;
        }
        .status.show { display: block; }
        .status.success { background: #a6e3a1; color: #1e1e2e; }
        .status.error { background: #f38ba8; color: #1e1e2e; }
        .status.info { background: #89b4fa; color: #1e1e2e; }
        .progress-bar {
            width: 100%;
            height: 8px;
            background: #45475a;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 10px;
            display: none;
        }
        .progress-bar.show { display: block; }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #89b4fa, #a6e3a1);
            border-radius: 4px;
            animation: progress-anim 2s ease-in-out infinite;
        }
        .tool-btn.disabled {
            opacity: 0.5;
            cursor: not-allowed;
            border-color: #45475a;
        }
        .tool-btn.disabled:hover {
            border-color: #45475a;
            background: #45475a;
        }
        .tool-hint {
            font-size: 0.75rem;
            color: #a6adc8;
            margin-top: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔧 QRecover</h1>
        <p class="subtitle">数据恢复工具</p>

        <!-- 工具切换 -->
        <div class="tool-switch">
            <div class="tool-btn active" id="btnTestDisk" onclick="switchTool('testdisk')">
                🛠️ TestDisk<br><small>命令行界面</small>
            </div>
            <div class="tool-btn" id="btnRecuva" onclick="switchTool('recuva')">
                🎨 Recuva<br><small>图形界面</small>
            </div>
        </div>

        <!-- 驱动器选择 -->
        <div class="section">
            <div class="section-title">📀 选择驱动器</div>
            <div class="drive-list" id="driveList"></div>
        </div>

        <!-- 操作按钮 -->
        <div class="section">
            <button class="btn btn-primary" id="btnScan" disabled onclick="scanDrive()">
                🔍 扫描磁盘
            </button>
            <button class="btn btn-success" id="btnRecover" disabled onclick="recoverFiles()">
                💾 恢复文件
            </button>
        </div>

        <!-- 进度条 -->
        <div class="progress-bar" id="progressBar">
            <div class="progress-fill"></div>
        </div>

        <!-- 状态 -->
        <div class="status" id="status"></div>
    </div>

    <script>
        let selectedDrive = null;
        let currentTool = 'testdisk';

        // 切换工具
        function switchTool(tool) {
            currentTool = tool;
            document.getElementById('btnTestDisk').classList.remove('active');
            document.getElementById('btnRecuva').classList.remove('active');
            document.getElementById('btn' + tool.charAt(0).toUpperCase() + tool.slice(1)).classList.add('active');
        }

        // 加载驱动器
        async function loadDrives() {
            const res = await fetch('/api/drives');
            const drives = await res.json();
            const list = document.getElementById('driveList');
            list.innerHTML = '';
            drives.forEach(d => {
                const card = document.createElement('div');
                card.className = 'drive-card';
                card.innerHTML = `
                    <div class="drive-letter">${d.letter}:</div>
                    <div class="drive-info">${d.total} GB</div>
                    <div class="drive-info">已用 ${d.used}</div>
                `;
                card.onclick = () => selectDrive(d.letter, card);
                list.appendChild(card);
            });
        }

        function selectDrive(letter, card) {
            selectedDrive = letter;
            document.querySelectorAll('.drive-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            document.getElementById('btnScan').disabled = false;
            document.getElementById('btnRecover').disabled = false;
        }

        async function scanDrive() {
            if (!selectedDrive) return;
            showStatus('info', `正在启动 ${currentTool} 扫描 ${selectedDrive} 盘...`);
            document.getElementById('progressBar').classList.add('show');
            const res = await fetch(`/api/scan?drive=${selectedDrive}&tool=${currentTool}`);
            const data = await res.json();
            document.getElementById('progressBar').classList.remove('show');
            if (data.status === 'ok') {
                showStatus('success', data.message);
            } else {
                showStatus('error', data.message);
            }
        }

        async function recoverFiles() {
            if (!selectedDrive) return;
            showStatus('info', `正在启动 ${currentTool} 恢复 ${selectedDrive} 盘文件...`);
            document.getElementById('progressBar').classList.add('show');
            const res = await fetch(`/api/recover?drive=${selectedDrive}&tool=${currentTool}`);
            const data = await res.json();
            document.getElementById('progressBar').classList.remove('show');
            if (data.status === 'ok') {
                showStatus('success', data.message);
            } else {
                showStatus('error', data.message);
            }
        }

        function showStatus(type, msg) {
            const el = document.getElementById('status');
            el.className = `status show ${type}`;
            el.textContent = `[${type.toUpperCase()}] ${msg}`;
        }

        // 初始化
        loadDrives();
        setInterval(loadDrives, 10000);
    </script>
</body>
</html>
"""

@app.route('/api/tools')
def api_tools():
    """返回可用工具列表"""
    tools = {
        "testdisk": os.path.isfile(TESTDISK_EXE),
        "recuva": find_recuva() is not None
    }
    return jsonify(tools)

@app.route('/api/install_recuva')
def api_install_recuva():
    """启动 Recuva 安装程序"""
    if os.path.isfile(RECUVA_INSTALLER):
        try:
            subprocess.Popen([RECUVA_INSTALLER], shell=True)
            return jsonify({"status": "ok", "message": "Recuva installer launched. Please install Recuva."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to launch installer: {e}"})
    else:
        return jsonify({"status": "error", "message": "Recuva installer not found."})

@app.route('/api/drives')
def api_drives():
    return jsonify(get_drives())

@app.route('/api/scan')
def api_scan():
    """启动扫描工具"""
    drive = request.args.get('drive', '')
    tool = request.args.get('tool', 'testdisk')
    
    if not drive:
        return jsonify({"status": "error", "message": "No drive specified"})
    
    if tool == 'testdisk':
        # 使用 TestDisk
        if not os.path.isfile(TESTDISK_EXE):
            return jsonify({"status": "error", "message": f"TestDisk not found at {TESTDISK_EXE}"})
        try:
            subprocess.Popen([TESTDISK_EXE], shell=True)
            return jsonify({"status": "ok", "message": f"TestDisk opened for drive {drive}. Please use the TestDisk window to scan."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start TestDisk: {e}"})
    
    elif tool == 'recuva':
        # 使用 Recuva
        recuva = find_recuva()
        if not recuva:
            return jsonify({"status": "error", "message": "Recuva not found. Please install Recuva first."})
        try:
            subprocess.Popen([recuva], shell=True)
            return jsonify({"status": "ok", "message": f"Recuva opened for drive {drive}. Please use the Recuva window to scan."})
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
    
    if tool == 'testdisk':
        # 使用 PhotoRec
        if not os.path.isfile(PHOTOREC_EXE):
            return jsonify({"status": "error", "message": f"PhotoRec not found at {PHOTOREC_EXE}"})
        try:
            subprocess.Popen([PHOTOREC_EXE], shell=True)
            return jsonify({"status": "ok", "message": f"PhotoRec opened. Output folder: {out_dir}"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start PhotoRec: {e}"})
    
    elif tool == 'recuva':
        # 使用 Recuva（恢复功能在同一个 GUI 里）
        recuva = find_recuva()
        if not recuva:
            return jsonify({"status": "error", "message": "Recuva not found. Please install Recuva first."})
        try:
            subprocess.Popen([recuva], shell=True)
            return jsonify({"status": "ok", "message": f"Recuva opened. Please use Recuva to recover files to {out_dir}"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start Recuva: {e}"})
    
    return jsonify({"status": "error", "message": "Invalid tool specified"})

# ─────────── Main ───────────
def main():
    print("Starting QRecover Web UI...")
    print("Open browser at: http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == "__main__":
    main()
