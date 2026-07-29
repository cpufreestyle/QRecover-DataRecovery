#!/usr/bin/env python3
"""QRecover Desktop - 原生桌面应用启动器 (WebView 封装)"""
import os
import sys
import json
import threading
import time
import logging
import ctypes
from pathlib import Path

# ── Windows 控制台 UTF-8（修复中文乱码）──
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 路径处理 ──
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)

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

# ── 导入 Flask 应用 ──
sys.path.insert(0, BASE_DIR)
from qrecover import app, start_recuva_updater

# 启动 Recuva 无感自动更新（后台线程）
start_recuva_updater()

# ── 配置文件路径 ──
CONFIG_FILE = os.path.join(BASE_DIR, 'qrecover_desktop.json')

def load_config():
    defaults = {
        "width": 1000,
        "height": 700,
        "minimize_to_tray": True,
        "start_minimized": False,
        "always_on_top": False,
        "first_run": True,
    }
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            defaults.update(saved)
    except Exception:
        pass
    return defaults

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"保存配置失败: {e}")

config = load_config()

# ══════════════════════════════════════════════════════════════
# Flask 后台线程
# ══════════════════════════════════════════════════════════════
def run_flask():
    log.info("Flask 服务启动中...")
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
time.sleep(1)  # 等待 Flask 就绪

# ══════════════════════════════════════════════════════════════
# 桌面窗口 (pywebview)
# ══════════════════════════════════════════════════════════════
import webview

def on_closing():
    if config.get('minimize_to_tray', True):
        window.hide()
        return False  # 阻止关闭
    else:
        log.info("QRecover Desktop 退出")
        return True

def on_closed():
    log.info("窗口已关闭")

class QRecoverAPI:
    def __init__(self):
        self._window = None

    def set_window(self, w):
        self._window = w

    def minimize(self):
        if self._window:
            self._window.minimize()

    def maximize(self):
        if self._window:
            if self._window.maximized:
                self._window.restore()
            else:
                self._window.maximize()

    def close(self):
        if self._window:
            if config.get('minimize_to_tray', True):
                self._window.hide()
            else:
                self._window.destroy()

    def get_config(self):
        return config

    def set_config(self, key, value):
        config[key] = value
        save_config(config)
        return {"ok": True}

    def get_version(self):
        return "QRecover Desktop v2.0.0"

    def open_external(self, url):
        import webbrowser
        webbrowser.open(url)

    def show_notification(self, title, message):
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
        except Exception:
            pass

api = QRecoverAPI()

# ── 桌面桥接 JS 注入 (纯 JS，不含 script 标签) ──
DESKTOP_BRIDGE_JS = """
(function() {
    // 桌面端 API 桥接
    window.QRecoverDesktop = {
        minimize: function() { pywebview.api.minimize(); },
        maximize: function() { pywebview.api.maximize(); },
        close: function() { pywebview.api.close(); },
        getConfig: async function() { return await pywebview.api.get_config(); },
        setConfig: async function(key, value) { return await pywebview.api.set_config(key, value); },
        getVersion: async function() { return await pywebview.api.get_version(); },
        openExternal: function(url) { pywebview.api.open_external(url); },
        notify: function(title, msg) { pywebview.api.show_notification(title, msg); },
    };

    // 更新标题
    var h1 = document.querySelector('h1');
    if (h1) h1.textContent = 'QRecover Desktop';

    var subtitle = document.querySelector('.subtitle');
    if (subtitle) subtitle.textContent = '桌面版 · 专业数据恢复工具集';

    var footer = document.querySelector('.footer');
    if (footer) footer.innerHTML = 'QRecover Desktop v2.0.0 · Powered by Flask + WebView';

    // 注入桌面端样式
    var style = document.createElement('style');
    style.textContent = '.window-controls{position:fixed;top:12px;right:16px;display:flex;gap:8px;z-index:10000;-webkit-app-region:no-drag;}' +
        '.win-btn{width:32px;height:32px;border-radius:8px;border:1px solid #1e1e2e;background:#12121a;color:#8888a0;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;transition:all 0.2s;line-height:1;}' +
        '.win-btn:hover{background:#1a1a26;color:#e8e8f0;border-color:#6c63ff;}' +
        '.win-btn.close-btn:hover{background:#ff4757;color:white;border-color:#ff4757;}' +
        '#confetti-canvas{-webkit-app-region:no-drag;}';
    document.head.appendChild(style);

    // 添加窗口控制按钮
    var controls = document.createElement('div');
    controls.className = 'window-controls';
    controls.innerHTML = '<div class="win-btn" onclick="window.QRecoverDesktop.minimize()" title="最小化到托盘" style="font-family:monospace;">_</div>' +
        '<div class="win-btn" onclick="window.QRecoverDesktop.maximize()" title="最大化/还原" style="font-family:monospace;">[]</div>' +
        '<div class="win-btn close-btn" onclick="window.QRecoverDesktop.close()" title="关闭到托盘" style="font-family:monospace;">X</div>';
    document.body.appendChild(controls);

    // 第一个点击关闭按钮时提示
    var firstClose = true;
    var closeBtn = controls.querySelector('.close-btn');
    var origClose = closeBtn.onclick;
    closeBtn.onclick = function() {
        if (firstClose) {
            firstClose = false;
            pywebview.api.show_notification('提示', '窗口将最小化到系统托盘。\\n\\n如需完全退出，请在托盘图标上右键选择退出。');
        }
        window.QRecoverDesktop.close();
    };

    console.log('QRecover Desktop v2.0.0 启动完成');
})();
"""

def on_loaded():
    """页面加载完成后注入桌面桥接"""
    try:
        window.evaluate_js(DESKTOP_BRIDGE_JS)
        log.info("桌面桥接注入完成")
    except Exception as e:
        log.warning(f"桥接注入延迟: {e}")
        # 延迟重试
        time.sleep(0.5)
        try:
            window.evaluate_js(DESKTOP_BRIDGE_JS)
            log.info("桌面桥接注入完成(重试)")
        except Exception as e2:
            log.error(f"桥接注入失败: {e2}")

# ── 创建窗口 ──
log.info("启动 QRecover Desktop...")

window = webview.create_window(
    title='QRecover Desktop - 数据恢复工具',
    url='http://127.0.0.1:5000',
    width=config.get('width', 1000),
    height=config.get('height', 700),
    min_size=(680, 480),
    resizable=True,
    fullscreen=False,
    frameless=False,
    easy_drag=False,
    confirm_close=config.get('minimize_to_tray', True),
    background_color='#0a0a0f',
    js_api=api,
    text_select=False,
)

api.set_window(window)

# 窗口事件绑定
window.events.closing += on_closing
window.events.closed += on_closed
window.events.loaded += on_loaded

# ── 启动 ──
if __name__ == '__main__':
    log.info("QRecover Desktop v2.0.0 启动完成")

    # 优先 Edge WebView2 (Windows 10/11 内置)，回退到 CEF
    gui_order = ['edgechromium', 'cef', 'mshtml']
    started = False

    for gui_name in gui_order:
        try:
            log.info(f"尝试 GUI: {gui_name}")
            webview.start(gui=gui_name, debug=False)
            started = True
            break
        except Exception as e:
            log.warning(f"{gui_name} 启动失败: {e}")
            continue

    if not started:
        log.error("所有 GUI 后端均启动失败")
        print("\n启动桌面窗口失败！请确认:")
        print("  1. Windows 10/11 已安装 Edge WebView2 运行时")
        print("    下载: https://developer.microsoft.com/microsoft-edge/webview2/")
        print("  2. 或安装 cefpython3: pip install cefpython3")
        input("\n按 Enter 退出...")
