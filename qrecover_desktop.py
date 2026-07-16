#!/usr/bin/env python3
"""QRecover Desktop - 原生桌面应用启动器 (WebView 封装)"""
import os
import sys
import json
import threading
import time
import logging
import ctypes
import ctypes.wintypes
from pathlib import Path

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
from qrecover import app, ensure_single_instance

# ── 单实例：终止旧进程，防止多个桌面窗口堆叠 ──
_SINGLE_INSTANCE_MUTEX = ensure_single_instance()

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
    """在后台线程中运行 Flask"""
    log.info("Flask 服务启动中...")
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
time.sleep(0.8)  # 等待 Flask 就绪

# ══════════════════════════════════════════════════════════════
# 桌面窗口 (pywebview)
# ══════════════════════════════════════════════════════════════
import webview

def on_closing():
    """窗口关闭时的处理"""
    if config.get('minimize_to_tray', True):
        window.hide()
        return False  # 阻止关闭
    else:
        log.info("QRecover Desktop 退出")
        # 终止所有子进程
        try:
            ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)
        except Exception:
            pass
        return True  # 允许关闭

def on_closed():
    log.info("窗口已关闭")

class QRecoverAPI:
    """暴露给 JS 的原生 API"""
    
    def __init__(self):
        self._window = None
    
    def set_window(self, w):
        self._window = w
    
    def minimize(self):
        if self._window:
            self._window.minimize()
    
    def maximize(self):
        if self._window:
            self._window.restore() if self._window.maximized else self._window.maximize()
    
    def close(self):
        if self._window:
            # 如果启用最小化到托盘，隐藏而不是关闭
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
        """Windows 原生通知"""
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
        except Exception:
            pass

api = QRecoverAPI()

# ── 注入桌面增强 CSS/JS ──
DESKTOP_BRIDGE = """
// ── 桌面端增强 ──
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

// 页面加载完成后添加桌面特性（窗口已加载，直接执行）
(function() {
    // 更新标题栏显示桌面版信息
    var h1 = document.querySelector('h1');
    if (h1) h1.textContent = 'QRecover Desktop';
    
    var subtitle = document.querySelector('.subtitle');
    if (subtitle) subtitle.textContent = '桌面版 · 专业数据恢复工具集';
    
    // 更新版本号
    var footer = document.querySelector('.footer');
    if (footer) footer.innerHTML = 'QRecover Desktop v2.0.0 · Powered by Flask + WebView · Made with ❤️';
    
    // 添加桌面端专属样式
    var style = document.createElement('style');
    style.textContent = `
        /* 桌面端拖拽区域 */
        .header { -webkit-app-region: no-drag; padding-top: 8px !important; }
        .header .logo-icon { cursor: default; }
        
        /* 桌面端窗口控制按钮 */
        .window-controls {
            position: fixed;
            top: 12px;
            right: 16px;
            display: flex;
            gap: 8px;
            z-index: 10000;
            -webkit-app-region: no-drag;
        }
        .win-btn {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--text-dim);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            transition: all 0.2s;
        }
        .win-btn:hover {
            background: var(--card-hover);
            color: var(--text);
            border-color: var(--accent);
        }
        .win-btn.close-btn:hover {
            background: var(--danger);
            color: white;
            border-color: var(--danger);
        }
        
        /* 隐藏 confetti canvas 的拖拽区域 */
        #confetti-canvas { -webkit-app-region: no-drag; }
    `;
    document.head.appendChild(style);
    
    // 添加窗口控制按钮
    var controls = document.createElement('div');
    controls.className = 'window-controls';
    controls.innerHTML = `
        <div class="win-btn" onclick="window.QRecoverDesktop.minimize()" title="最小化到托盘">
            ─
        </div>
        <div class="win-btn" onclick="window.QRecoverDesktop.maximize()" title="最大化/还原">
            □
        </div>
        <div class="win-btn close-btn" onclick="window.QRecoverDesktop.close()" title="关闭到托盘">
            ✕
        </div>
    `;
    document.body.appendChild(controls);
    
    console.log('QRecover Desktop bridge initialized');
})();
"""

def on_loaded():
    """页面加载完成后注入桌面桥接代码"""
    window.evaluate_js(DESKTOP_BRIDGE)
    log.info("桌面桥接注入完成")

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
    try:
        log.info("QRecover Desktop v2.0.0 启动完成")
        webview.start(gui='cef', debug=False)
    except Exception as e:
        log.error(f"启动失败，尝试回退到 edgechromium: {e}")
        try:
            webview.start(gui='edgechromium', debug=False)
        except Exception as e2:
            log.error(f"回退也失败: {e2}")
            print(f"\n启动桌面窗口失败: {e2}")
            print("请确保安装了 Microsoft Edge WebView2 运行时")
            print("下载: https://developer.microsoft.com/microsoft-edge/webview2/")
            input("\n按 Enter 退出...")
