#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QRecover Web UI - TestDisk/PhotoRec/Recuva GUI"""
import os
import sys
import locale
import shutil
import subprocess
import ctypes
import logging
import threading
import time
import json
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, request, jsonify, Response

from ai_assistant import assistant as ai_assistant
from version_utils import get_file_version, parse_version, sha256_of as _sha256_of

# ── Windows 控制台 UTF-8（修复中文乱码）──
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

def _decode_cli(raw: bytes) -> str:
    """将子进程（tasklist/wmic/powershell/testdisk 等）输出的字节安全解码为文本。

    开发机通常通过 .bat 设置 chcp 65001（UTF-8 控制台），而 PyInstaller 冻结的
    release 可执行文件（console=False）没有控制台，子进程会按系统 OEM/ANSI 代码页
    （中文 Windows 为 cp936/GBK）输出。固定用单一编码解码会在此场景下产生乱码，
    因此依次尝试 UTF-8 -> 系统首选编码 -> gbk，确保 release 构建正常显示中文。
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    candidates = ["utf-8", locale.getpreferredencoding(False) or "gbk", "gbk"]
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")

# ── 单实例检测：确保只有一个 QRecoverWeb 进程运行 ──
def _kill_old_instances():
    """终止旧的 QRecover 进程（按进程名匹配，避免误杀其他占用端口的服务）"""
    current_pid = os.getpid()
    pids_to_kill = set()

    # 仅按进程名清理旧 QRecover 实例；不再按固定端口（5000）清理，
    # 以免误杀恰好占用 5000 的其他程序（如其他本地服务）。
    # 方法：查找 QRecoverWeb.exe 进程
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq QRecoverWeb.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if 'QRecoverWeb.exe' in line:
                parts = line.split(',')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1].strip('"'))
                        if pid != current_pid:
                            pids_to_kill.add(pid)
                    except ValueError:
                        pass
    except Exception as e:
        logging.warning(f"tasklist 检测失败: {e}")

    # 终止所有旧进程
    for pid in pids_to_kill:
        try:
            logging.info(f"终止旧进程 PID={pid}")
            subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                         capture_output=True, timeout=5)
        except Exception:
            pass

    if pids_to_kill:
        time.sleep(1)  # 等待端口释放


def ensure_single_instance():
    """确保只有一个实例运行，如果有旧进程则终止它"""
    _kill_old_instances()

    # 使用互斥体防止竞态
    mutex_name = "Global\\QRecoverWeb_SingleInstance"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    return mutex

# ── 全局进程锁：同一时间只能有一个工具进程 ──
_ACTIVE_PROCESS = None  # 可以是 subprocess.Popen 对象，或 None
_ACTIVE_PROCESS_LOCK = threading.Lock()

def _check_and_clear_process() -> None:
    """检查全局进程是否还在运行，如果已结束则清除（线程安全）"""
    global _ACTIVE_PROCESS
    with _ACTIVE_PROCESS_LOCK:
        if _ACTIVE_PROCESS is not None:
            # 如果是 Popen 对象
            if hasattr(_ACTIVE_PROCESS, 'poll'):
                if _ACTIVE_PROCESS.poll() is not None:
                    _ACTIVE_PROCESS = None

def run_tool(exe_path: str, work_dir: Optional[str] = None) -> bool:
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
            # WinError 740 = 需要提权，用 ShellExecuteExW 触发 UAC 并取回进程句柄
            handle = _shell_run_elevated(exe_path, work_dir or os.path.dirname(exe_path))
            with _ACTIVE_PROCESS_LOCK:
                _ACTIVE_PROCESS = True  # 哨兵值，watcher 线程负责清除
            _start_process_watcher(handle)
        else:
            raise
    return True

def _shell_run_elevated(exe_path: str, work_dir: str) -> int:
    """ShellExecuteExW(runas) 提权启动，返回子进程句柄（SEE_MASK_NOCLOSEPROCESS）"""
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIconOrMonitor", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_SHOWNORMAL = 1
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe_path
    info.lpDirectory = work_dir
    info.nShow = SW_SHOWNORMAL
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise RuntimeError("UAC 提权失败（ShellExecuteExW 返回失败，可能被用户取消）")
    return info.hProcess

def _start_process_watcher(handle: int) -> None:
    """后台线程：阻塞等待提权进程句柄退出（零轮询），退出时清除锁并关句柄"""
    def watcher():
        global _ACTIVE_PROCESS
        try:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
        finally:
            with _ACTIVE_PROCESS_LOCK:
                _ACTIVE_PROCESS = None
            ctypes.windll.kernel32.CloseHandle(handle)
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

# TestDisk / PhotoRec 路径（按需下载到 tools/testdisk，不随安装包分发）
TESTDISK_DIR = os.path.join(BASE_DIR, "tools", "testdisk")
TESTDISK_EXE = os.path.join(TESTDISK_DIR, "testdisk_win.exe")
PHOTOREC_EXE = os.path.join(TESTDISK_DIR, "photorec_win.exe")
# 官方 7.3 WIP Windows 压缩包（约 ~20MB，首次用时联网下载解压）
TESTDISK_ZIP_URL = os.environ.get(
    "QRECOVER_TESTDISK_URL",
    "https://www.cgsecurity.org/testdisk-7.3-WIP.win64.zip",
).strip()
TESTDISK_ZIP_NAME = os.path.basename(TESTDISK_ZIP_URL) or "testdisk-7.3-WIP.win64.zip"

# Recuva 路径（由无感更新机制联网获取，不随安装包分发内置副本/安装器）
RECUVAPATHS = [
    r"C:\Program Files\Recuva\recuva.exe",
    r"C:\Program Files (x86)\Recuva\recuva.exe",
    os.path.join(BASE_DIR, "recuva_portable", "recuva.exe"),
    os.path.join(BASE_DIR, "tools", "recuva", "recuva.exe"),
]
# ── Recuva 无感自动更新（启动时后台静默从官网抓取最新版）──
RECUVAPORTABLE = os.path.join(BASE_DIR, "tools", "recuva")
RECUVAVERSIONFILE = os.path.join(RECUVAPORTABLE, "version.txt")
# 注：CCleaner 官方下载为前端动态生成的签名地址，无固定直链，
# 故不再硬编码官网直链，改用下方可配置的 manifest 更新源。
# 下载请求头（伪装浏览器，避免被拦截）
RECUVaHEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.ccleaner.com/recuva/download",
}
# ── 无感更新清单（manifest）机制 ──
# 从可配置的清单 URL 拉取 JSON：{ "version": "1.54.120.0",
#   "url": "<安装包直链>", "sha256": "<可选校验和>" }
# 与本地版本比对后静默下载安装。清单由你自己托管（如 GitHub Release），
# 即可实现“自动无感更新到官网/托管最新版”（CCleaner 无稳定直链，故采用此方案）。
# 若未配置清单 URL，则使用本地内置安装器作为更新源（离线可用，保持当前版本）。
RECUVaMANIFESTURL = os.environ.get("QRECOVER_RECUVAMANIFESTURL", "").strip()
RECUVaDLURL = os.environ.get("QRECOVER_RECUVADLURL", "").strip()
RECUVaUPDATELOG = os.path.join(BASE_DIR, "recuva_update.log")
RECUVaUPDATESTATE = os.path.join(BASE_DIR, "recuva_update_state.json")
# 可通过环境变量关闭：set QRECOVER_RECUVAAUTOUPDATE=0
RECUVaAUTOUPDATE = os.environ.get("QRECOVER_RECUVAAUTOUPDATE", "1") != "0"
RECUVaCHECKINTERVAL = 24 * 3600  # 每日至多检查一次，避免每次启动都下载
_updater_started = False

IS_WIN = sys.platform == "win32"

APP_VERSION = "2.0.5"

# 前端静态资源目录（源码运行：web/；PyInstaller onefile：解包目录内 web/）
if getattr(sys, 'frozen', False):
    RESOURCE_DIR = os.path.join(getattr(sys, '_MEIPASS', BASE_DIR), 'web')
else:
    RESOURCE_DIR = os.path.join(SCRIPT_DIR, 'web')

app = Flask(__name__, static_folder=RESOURCE_DIR, static_url_path='/static')

# ── 缓存策略：静态资源允许协商缓存（ETag/304），页面与接口禁缓存 ──
# 避免“网页端看不到新功能”的旧缓存问题，同时静态 JS/CSS 可 304 复用
@app.after_request
def _no_cache(resp):
    if request.path.startswith('/static/'):
        resp.headers.set('Cache-Control', 'no-cache, must-revalidate')
    else:
        resp.headers.set('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        resp.headers.set('Pragma', 'no-cache')
        resp.headers.set('Expires', '0')
    return resp

# ─────────── Routes ───────────
@app.route('/')
def index():
    """主页面（静态 index.html）"""
    return app.send_static_file('index.html')

# ─────────── Helpers ───────────
def get_drives() -> List[Dict[str, str]]:
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

def _find_recuva_in_registry() -> Optional[str]:
    """通过注册表查找 Recuva 可执行文件路径（适用于安装版）"""
    if not IS_WIN:
        return None
    try:
        import winreg
    except ImportError:
        return None

    # 常见注册表键：App Paths 直接指向可执行文件
    app_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Recuva.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\recuva.exe"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Recuva.exe"),
    ]
    for hkey, subkey in app_paths:
        try:
            with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, None)
                if value and os.path.isfile(value):
                    return value
        except OSError:
            pass

    # Piriform 软件自身注册表键，常包含 ProgramPath / Path
    piriform_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Piriform\Recuva"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Piriform\Recuva"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Piriform\Recuva"),
    ]
    for hkey, subkey in piriform_keys:
        try:
            with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ) as key:
                for value_name in ("ProgramPath", "Path", "InstallLocation", "ExePath"):
                    try:
                        value, _ = winreg.QueryValueEx(key, value_name)
                        if value:
                            cand = value if value.lower().endswith(".exe") else os.path.join(value, "Recuva.exe")
                            if os.path.isfile(cand):
                                return cand
                    except OSError:
                        pass
        except OSError:
            pass

    # Uninstall 键：通过 DisplayName 匹配，再取 InstallLocation
    uninstall_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hkey, root in uninstall_roots:
        try:
            with winreg.OpenKey(hkey, root, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root_key, i)
                        i += 1
                        with winreg.OpenKey(root_key, subkey_name, 0, winreg.KEY_READ) as subkey:
                            try:
                                display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                                if not display_name or "recuva" not in display_name.lower():
                                    continue
                            except OSError:
                                continue
                            for value_name in ("InstallLocation", "UninstallString"):
                                try:
                                    value, _ = winreg.QueryValueEx(subkey, value_name)
                                    if not value:
                                        continue
                                    # UninstallString 通常是 uninstaller 路径，取所在目录
                                    base = value.strip('"')
                                    if base.lower().endswith(".exe"):
                                        base = os.path.dirname(base)
                                    cand = os.path.join(base, "Recuva.exe")
                                    if os.path.isfile(cand):
                                        return cand
                                except OSError:
                                    pass
                    except OSError:
                        break
        except OSError:
            pass
    return None


def _find_recuva_in_path() -> Optional[str]:
    """在 PATH 中查找 recuva.exe（用户手动添加过的情况）"""
    if not IS_WIN:
        return None
    try:
        result = subprocess.run(
            ["where", "recuva.exe"],
            capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode == 0:
            first = result.stdout.strip().splitlines()[0].strip()
            if first and os.path.isfile(first):
                return first
    except Exception:
        pass
    return None


def _normalize_recuva_exe(path: str) -> str:
    """若找到的是 recuva64.exe，且同目录存在 recuva.exe，优先返回 recuva.exe。
    Recuva 在 64 位系统上会通过 recuva.exe 自动启动 64 位版本，
    因此调用 recuva.exe 更符合官方入口习惯。"""
    if not path:
        return path
    path = os.path.abspath(path)
    if os.path.basename(path).lower() == "recuva64.exe":
        plain = os.path.join(os.path.dirname(path), "recuva.exe")
        if os.path.isfile(plain):
            return plain
    return path


def find_recuva() -> Optional[str]:
    """查找 Recuva 可执行文件（安装版、便携版、PATH、注册表全覆盖）"""
    # 1) 注册表（安装版最可靠）
    path = _find_recuva_in_registry()
    if path:
        return _normalize_recuva_exe(path)

    # 2) 常见固定路径（含 Piriform 官方默认目录，同时兼容 recuva64.exe）
    candidates = []
    for base in RECUVAPATHS + [
        r"C:\Program Files\Piriform\Recuva",
        r"C:\Program Files (x86)\Piriform\Recuva",
    ]:
        if base.lower().endswith(".exe"):
            candidates.append(base)
            base_dir = os.path.dirname(base)
        else:
            base_dir = base
        candidates.append(os.path.join(base_dir, "recuva.exe"))
        candidates.append(os.path.join(base_dir, "Recuva.exe"))
        candidates.append(os.path.join(base_dir, "recuva64.exe"))
        candidates.append(os.path.join(base_dir, "Recuva64.exe"))
    for p in candidates:
        if os.path.isfile(p):
            return _normalize_recuva_exe(p)

    # 3) PATH 环境变量
    path = _find_recuva_in_path()
    if path:
        return _normalize_recuva_exe(path)

    return None

# ─────────── API ───────────
@app.route('/api/tools')
def api_tools():
    """返回可用工具列表与下载需求"""
    tools = {
        "testdisk": os.path.isfile(TESTDISK_EXE),
        "photorec": os.path.isfile(PHOTOREC_EXE),
        "recuva": find_recuva() is not None,
    }
    # TestDisk 未就绪时提示前端可触发按需下载
    if not tools["testdisk"]:
        tools["testdisk_needs_download"] = True
        tools["testdisk_url"] = TESTDISK_ZIP_URL
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

@app.route('/api/recuva/install', methods=['POST'])
def api_recuva_install():
    """一键安装 Recuva：优先无感更新源自动落地，失败则引导手动下载。"""
    try:
        recuva_auto_update_check(force=True)
        if find_recuva():
            ver = get_recuva_local_version()
            return jsonify({
                "status": "ok", "installed": True,
                "message": "Recuva 已安装完成（版本 %s），可直接使用。" % (ver or ""),
            })
        return jsonify({
            "status": "ok", "installed": False, "action": "open_url",
            "url": RECUVADOWNLOADURL,
            "message": ("未能自动获取 Recuva（未配置更新源）。已为你打开官方下载页，安装后本工具会自动识别；"
                        "也可把便携版 recuva.exe 放到 tools/recuva/ 目录。"),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ─────────── Recuva 无感自动更新 ───────────
def log_recuva_update(msg: str) -> None:
    """记录更新日志（无感，不打断界面）"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(RECUVaUPDATELOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def get_recuva_local_version() -> Optional[str]:
    """读取本地 Recuva 版本号（优先从实际检测到的 exe 读取 PE 版本）"""
    exe = find_recuva()
    if exe:
        v = get_file_version(exe)
        if v:
            return v
    # 回退：便携版目录 / 版本文件
    fallback_exe = os.path.join(RECUVAPORTABLE, "recuva.exe")
    v = get_file_version(fallback_exe)
    if v:
        return v
    try:
        if os.path.isfile(RECUVAVERSIONFILE):
            with open(RECUVAVERSIONFILE, encoding="utf-8") as f:
                return f.read().strip() or None
    except Exception:
        pass
    return None


def _download_file(url: str, dest: str, retries: int = 3) -> None:
    # 本地文件路径（无 scheme，绝对或相对）：直接复制，支持 clone 后任意目录
    if "://" not in url and os.path.isfile(url):
        shutil.copyfile(url, dest)
        return
    # http(s)/file:// 交由 urllib，带重试以应对不稳定的网络/代理
    import urllib.request
    import time
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
            })
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            return
        except Exception as e:
            last_err = e
            log.warning("下载失败（第 %d/%d 次）：%s", attempt, retries, e)
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("下载失败：%s" % last_err)


# ─────────── TestDisk / PhotoRec 按需下载 ───────────
_TESTDISK_DOWNLOADING = False
_TESTDISK_DOWNLOAD_LOCK = threading.Lock()


def ensure_testdisk(force: bool = False) -> Tuple[bool, str]:
    """确保 TestDisk/PhotoRec 已就绪（首次用时按需联网下载官方压缩包并解压）。

    返回 (ok: bool, message: str)。ok=False 时 message 提示用户手动下载。
    """
    global _TESTDISK_DOWNLOADING
    if os.path.isfile(TESTDISK_EXE) and os.path.isfile(PHOTOREC_EXE):
        return True, "ok"

    with _TESTDISK_DOWNLOAD_LOCK:
        if _TESTDISK_DOWNLOADING:
            return False, "正在下载 TestDisk，请稍候..."
        if not force and (os.path.isfile(TESTDISK_EXE) and os.path.isfile(PHOTOREC_EXE)):
            return True, "ok"
        _TESTDISK_DOWNLOADING = True
    try:
        tmp = tempfile.mkdtemp(prefix="qtd_")
        zip_path = os.path.join(tmp, TESTDISK_ZIP_NAME)
        log.info("开始下载 TestDisk: %s", TESTDISK_ZIP_URL)
        _download_file(TESTDISK_ZIP_URL, zip_path)
        if os.path.getsize(zip_path) < 1_000_000:
            raise RuntimeError("下载的 TestDisk 压缩包异常（过小，可能被拦截）")
        os.makedirs(TESTDISK_DIR, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            # 压缩包内含 testdisk-7.3-WIP/ 顶层目录，解压后归一化到 tools/testdisk
            z.extractall(tmp)
        # 找到解压出的 testdisk 目录
        src_dir = None
        for root, dirs, _ in os.walk(tmp):
            if os.path.isfile(os.path.join(root, "testdisk_win.exe")):
                src_dir = root
                break
        if not src_dir:
            raise RuntimeError("压缩包内未找到 testdisk_win.exe")
        for name in os.listdir(src_dir):
            s = os.path.join(src_dir, name)
            d = os.path.join(TESTDISK_DIR, name)
            if os.path.isfile(s):
                shutil.copy2(s, d)
            elif os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
        if not (os.path.isfile(TESTDISK_EXE) and os.path.isfile(PHOTOREC_EXE)):
            raise RuntimeError("TestDisk 解压后缺少可执行文件")
        return True, "TestDisk 下载并解压完成"
    except Exception as e:
        log.error("TestDisk 下载/解压失败: %s", e)
        return False, ("TestDisk 自动下载失败（%s）。请手动下载并解压到：%s\n"
                       "下载地址：%s" % (e, TESTDISK_DIR, TESTDISK_ZIP_URL))
    finally:
        _TESTDISK_DOWNLOADING = False
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


@app.route('/api/testdisk/ensure', methods=['POST'])
def api_ensure_testdisk():
    """按需下载 TestDisk/PhotoRec（首次运行或手动触发）"""
    force = request.get_json(silent=True) or {}
    force = bool(force.get('force', False))
    ok, msg = ensure_testdisk(force=force)
    return jsonify({"status": "ok" if ok else "error", "message": msg,
                    "ready": ok and os.path.isfile(TESTDISK_EXE)})


def _load_manifest() -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """拉取更新清单，返回 (version, url, sha256, pkg_type)。

    优先级（均支持运行时通过环境变量覆盖）：
      1) QRECOVER_RECUVAMANIFESTURL 指向的清单文件（JSON）
      2) QRECOVER_RECUVADLURL 直接给的直链（版本未知，仅用于下载）
    清单 JSON 形如：
      {"version":"x.y.z.0","url":"<直链>","sha256":"<可选>",
       "type":"zip"|"exe"}   # zip=免权限直接解压；exe=NSIS 静默安装
    """
    manifest_url = os.environ.get("QRECOVER_RECUVAMANIFESTURL", "").strip() or RECUVaMANIFESTURL
    dl_url = os.environ.get("QRECOVER_RECUVADLURL", "").strip() or RECUVaDLURL
    if manifest_url:
        try:
            data = None
            # 本地文件路径（无 scheme）：按 BASE_DIR 解析后直接读取，支持 clone 后任意目录
            if "://" not in manifest_url:
                local = os.path.join(BASE_DIR, manifest_url) if not os.path.isabs(manifest_url) else manifest_url
                if os.path.isfile(local):
                    with open(local, encoding="utf-8") as _mf:
                        data = json.load(_mf)
            if data is None:
                import urllib.request
                req = urllib.request.Request(manifest_url, headers=RECUVaHEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
            ver = str(data.get("version", "")).strip() or None
            url = str(data.get("url", "")).strip() or None
            sha = str(data.get("sha256", "")).strip().lower() or None
            pkg_type = str(data.get("type", "exe")).strip().lower()
            if pkg_type not in ("zip", "exe"):
                pkg_type = "exe"
            if url:
                log_recuva_update("已读取更新清单: version=%s, url=%s, type=%s"
                                  % (ver or "未知", url, pkg_type))
                return ver, url, sha, pkg_type
        except Exception as e:
            log_recuva_update("读取更新清单失败，回退内置安装器: %s" % e)
    if dl_url:
        return None, dl_url, None, "exe"
    return None, None, None, "exe"


def _obtain_installer(tmp: str) -> Tuple[Optional[str], Optional[str]]:
    """获取一个可用的 Recuva 安装包路径（优先清单/直链，回退内置安装器）。

    返回 (installer_path, pkg_type) ；都不行则返回 (None, None)。
    """
    ver, url, sha, pkg_type = _load_manifest()
    if url:
        try:
            # 相对路径（无 scheme）按项目根目录 BASE_DIR 解析，支持 clone 后任意目录开箱即用
            if "://" not in url:
                url = os.path.join(BASE_DIR, url)
            ext = ".zip" if pkg_type == "zip" else ".exe"
            installer = os.path.join(tmp, "recuva_pkg" + ext)
            _download_file(url, installer)
            if os.path.getsize(installer) <= 500000:
                raise RuntimeError("下载的安装包异常（大小不符，可能被拦截）")
            if sha:
                actual = _sha256_of(installer)
                if actual and actual != sha:
                    raise RuntimeError("校验和不符（期望 %s，实际 %s）" % (sha[:12], actual[:12]))
                elif actual:
                    log_recuva_update("校验通过: " + actual[:16])
            log_recuva_update("已从更新源获取安装包: " + url)
            return installer, pkg_type
        except Exception as e:
            log_recuva_update("更新源下载失败: %s" % e)
    return None, None


def _copy_recuva_files(new_exe: str, install_dir: str) -> None:
    base = os.path.dirname(new_exe)
    # 复制主程序与组件；跳过大小异常（<1MB）的文件，防止坏文件污染
    targets = ["recuva.exe", "recuva64.exe", "RecuvaShell64.dll", "uninst.exe"]
    for t in targets:
        src = os.path.join(base, t)
        if os.path.isfile(src) and os.path.getsize(src) >= 1024:
            shutil.copy2(src, os.path.join(RECUVAPORTABLE, t))
        elif os.path.isfile(src):
            log_recuva_update("跳过异常文件（过小）: %s" % t)
    lang_src = os.path.join(base, "Lang")
    if os.path.isdir(lang_src):
        lang_dst = os.path.join(RECUVAPORTABLE, "Lang")
        os.makedirs(lang_dst, exist_ok=True)
        for f in os.listdir(lang_src):
            s = os.path.join(lang_src, f)
            if os.path.isfile(s) and os.path.getsize(s) >= 1024:
                shutil.copy2(s, os.path.join(lang_dst, f))


def recuva_auto_update_check(force: bool = False) -> Dict[str, Any]:
    """后台静默检查并更新 Recuva。

    返回 dict：{"action": "updated"|"uptodate"|"skipped"|"error",
                "version": <新版本或本地版本>, "message": ...}
    后台线程调用时可忽略返回值；手动接口会用它做反馈。
    """
    result = {"action": "skipped", "version": None, "message": ""}
    if not RECUVaAUTOUPDATE:
        return result
    if not IS_WIN:
        return result
    try:
        now = time.time()
        state = {}
        if os.path.isfile(RECUVaUPDATESTATE):
            try:
                with open(RECUVaUPDATESTATE, encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        if not force and (now - float(state.get("last_check", 0))) < RECUVaCHECKINTERVAL:
            log_recuva_update("跳过：距上次检查不足 24 小时")
            result["action"] = "skipped"
            result["message"] = "距上次检查不足 24 小时"
            return result

        log_recuva_update("开始检查 Recuva 更新...")
        current = get_recuva_local_version()
        log_recuva_update("当前本地版本: %s" % (current or "未知（首次获取）"))

        # 解析更新源（清单/直链/内置安装器）
        ver, url, sha, _ = _load_manifest()
        if ver and current and parse_version(ver) <= parse_version(current):
            log_recuva_update("清单版本 %s 不高于本地 %s，已是最新" % (ver, current))
            result["action"] = "uptodate"
            result["version"] = current
            result["message"] = "已是最新 (%s)" % current
            return result

        tmp = tempfile.mkdtemp(prefix="qrecuva_")
        try:
            installer, pkg_type = _obtain_installer(tmp)
            if not installer:
                raise RuntimeError("无可用安装包（更新源不可达且未内置安装器）")
            if os.path.getsize(installer) < 500000:
                raise RuntimeError("安装包异常（大小不符）")

            install_dir = os.path.join(tmp, "inst")
            os.makedirs(install_dir, exist_ok=True)

            # 优先用 zip 包（无需管理员权限，真正无感）；否则走 NSIS 静默安装
            if pkg_type == "zip":
                import zipfile
                with zipfile.ZipFile(installer) as z:
                    z.extractall(install_dir)
            else:
                # NSIS 静默安装（部分机器需管理员权限，失败则优雅跳过）
                try:
                    subprocess.run([installer, "/S", "/D=" + install_dir],
                                   timeout=300, capture_output=True, check=True)
                except subprocess.CalledProcessError as e:
                    # 权限不足等：保持当前版本，静默跳过，不弹窗
                    raise RuntimeError("安装器静默安装失败（可能需管理员权限）: %s" % e)

            new_exe = None
            for root, _, files in os.walk(install_dir):
                if "recuva.exe" in files:
                    new_exe = os.path.join(root, "recuva.exe")
                    break
            if not new_exe:
                raise RuntimeError("安装包中未找到 recuva.exe")

            new_ver = get_file_version(new_exe)
            log_recuva_update("安装包版本: %s" % (new_ver or "未知"))
            if current and new_ver and parse_version(new_ver) <= parse_version(current):
                log_recuva_update("安装包版本不高于本地，无需更新")
                result["action"] = "uptodate"
                result["version"] = current
                result["message"] = "已是最新 (%s)" % current
                return result

            _copy_recuva_files(new_exe, install_dir)
            with open(RECUVAVERSIONFILE, "w", encoding="utf-8") as f:
                f.write(new_ver or (current or "0.0.0.0"))
            log_recuva_update("Recuva 已静默更新至 %s" % (new_ver or "新版本"))
            result["action"] = "updated"
            result["version"] = new_ver or current
            result["message"] = "已更新至 %s" % (new_ver or "新版本")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                state["last_check"] = time.time()
                state["installed_version"] = get_recuva_local_version()
                with open(RECUVaUPDATESTATE, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
            except Exception:
                pass
    except Exception as e:
        # 无感：任何失败仅记录日志，不影响主程序
        log_recuva_update("更新跳过（无感）: %s" % e)
        result["action"] = "error"
        result["message"] = str(e)
    return result


def start_recuva_updater() -> None:
    """启动无感更新后台线程（仅启动一次）"""
    global _updater_started
    if _updater_started:
        return
    if not RECUVaAUTOUPDATE or not IS_WIN:
        return
    _updater_started = True
    try:
        t = threading.Thread(target=recuva_auto_update_check,
                            name="recuva-updater", daemon=True)
        t.start()
    except Exception as e:
        log_recuva_update("更新线程启动失败: %s" % e)


@app.route('/api/recuva/update', methods=['POST'])
def api_recuva_update():
    """手动触发 Recuva 无感更新（force），同步返回结果摘要"""
    try:
        res = recuva_auto_update_check(force=True)
        logs = []
        if os.path.isfile(RECUVaUPDATELOG):
            with open(RECUVaUPDATELOG, encoding="utf-8") as f:
                logs = [l.strip() for l in f.readlines() if l.strip()][-8:]
        return jsonify({
            "status": "ok",
            "action": res.get("action"),
            "version": res.get("version"),
            "message": res.get("message"),
            "current_version": get_recuva_local_version(),
            "recent_log": logs,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


_DRIVES_CACHE = {"ts": 0.0, "data": None}
_DRIVES_CACHE_LOCK = threading.Lock()
_DRIVES_CACHE_TTL = 2.0  # 秒；磁盘容量查询有系统调用开销，短缓存即可

@app.route('/api/drives')
def api_drives():
    now = time.time()
    with _DRIVES_CACHE_LOCK:
        if _DRIVES_CACHE["data"] is not None and now - _DRIVES_CACHE["ts"] < _DRIVES_CACHE_TTL:
            return jsonify(_DRIVES_CACHE["data"])
    data = get_drives()
    with _DRIVES_CACHE_LOCK:
        _DRIVES_CACHE["ts"] = now
        _DRIVES_CACHE["data"] = data
    return jsonify(data)

@app.route('/api/scan')
def api_scan():
    """启动扫描工具（drive 可空，空则启动后在工具内自选）"""
    drive = request.args.get('drive', '').strip()
    tool = request.args.get('tool', 'testdisk')

    if drive and (len(drive) != 1 or not drive.isalpha()):
        log.warning(f'Invalid drive param: {drive!r}')
        return jsonify({"status": "error", "message": "无效的盘符参数"}), 400

    drive_tip = f"请在 TestDisk 窗口中选择 {drive}: 盘进行扫描操作。" if drive else "请在 TestDisk 窗口中选择目标磁盘进行扫描操作。"

    if tool == 'testdisk':
        if not os.path.isfile(TESTDISK_EXE):
            ok, msg = ensure_testdisk()
            if not ok:
                return jsonify({"status": "error", "message": msg})
        try:
            run_tool(TESTDISK_EXE, TESTDISK_DIR)
            return jsonify({"status": "ok", "message": f"✅ TestDisk 已在新窗口启动（UAC 提示已弹出），{drive_tip}"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to start TestDisk: {e}"})

    elif tool == 'recuva':
        recuva = find_recuva()
        if not recuva:
            return jsonify({"status": "error", "message": "Recuva 未找到，请先安装 Recuva。"})
        try:
            run_tool(recuva)
            rtip = f"请在 Recuva 窗口中选择 {drive}: 盘进行扫描。" if drive else "请在 Recuva 窗口中选择扫描位置。"
            return jsonify({"status": "ok", "message": f"✅ Recuva 已启动，{rtip}"})
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

    if drive and (len(drive) != 1 or not drive.isalpha()):
        log.warning('Invalid drive param in recover: %r', drive)
        return jsonify({"status": "error", "message": "无效的盘符参数"}), 400

    if tool == 'testdisk':
        if not os.path.isfile(PHOTOREC_EXE):
            ok, msg = ensure_testdisk()
            if not ok:
                return jsonify({"status": "error", "message": msg})
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

# ─────────── AI 智能恢复助手 ───────────
def _build_ai_context() -> Dict[str, Any]:
    """构建 AI 对话所需的运行环境上下文"""
    context = {"tools": ["photorec"], "drives": []}
    if os.path.isfile(TESTDISK_EXE):
        context["tools"].append("testdisk")
    if find_recuva():
        context["tools"].append("recuva")
    try:
        context["drives"] = [d["letter"] for d in get_drives()]
    except Exception:
        pass
    return context


@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    """AI 对话接口（同步，返回结构化推荐）"""
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', '').strip()
        history = data.get('history', [])
        if not message:
            return jsonify({"status": "error", "message": "消息不能为空"}), 400
        if len(message) > 2000:
            return jsonify({"status": "error", "message": "输入过长（上限 2000 字）"}), 400

        context = _build_ai_context()
        result = ai_assistant.chat(message, history=history, context=context)
        return jsonify({
            "status": "ok",
            "reply": result["text"],
            "recommend": {
                "tools": result["tools"],
                "drive": result["drive"],
                "confidence": result["confidence"],
            },
        })
    except Exception as e:
        log.error(f"AI 对话失败: {e}")
        return jsonify({"status": "error", "message": f"AI 处理失败: {e}"}), 500


@app.route('/api/ai/chat/stream', methods=['POST'])
def api_ai_chat_stream():
    """AI 对话接口（SSE 流式输出）"""
    data = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    history = data.get('history', [])
    if not message:
        return jsonify({"status": "error", "message": "消息不能为空"}), 400
    if len(message) > 2000:
        return jsonify({"status": "error", "message": "输入过长（上限 2000 字）"}), 400

    context = _build_ai_context()

    def gen():
        try:
            for chunk in ai_assistant.chat_stream(message, history=history, context=context):
                yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
            rec = ai_assistant.recommend(message, context)
            yield f"data: {json.dumps({'recommend': rec}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype='text/event-stream')

@app.route('/api/ai/config', methods=['GET', 'POST'])
def api_ai_config():
    """获取/保存 AI 配置"""
    if request.method == 'GET':
        return jsonify(ai_assistant.get_config())
    try:
        data = request.get_json(silent=True) or {}
        # 仅更新允许的字段
        allowed = ['provider', 'api_key', 'base_url', 'model', 'enabled', 'temperature']
        new_cfg = {k: v for k, v in data.items() if k in allowed}
        result = ai_assistant.save_config(new_cfg)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ─────────── 端口自动选择 ───────────
def find_free_port(preferred: int = 5000) -> int:
    """优先使用 preferred 端口；若被占用（如 WinError 10013）则让系统分配空闲端口。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', preferred))
        return preferred
    except OSError:
        s.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        return port
    finally:
        try:
            s.close()
        except OSError:
            pass


# ─────────── Main ───────────
def main() -> None:
    # 单实例检测：确保只有一个进程
    mutex = ensure_single_instance()
    # 启动 Recuva 无感自动更新（后台线程，不打断界面）
    start_recuva_updater()
    port = find_free_port(5000)
    print("Starting QRecover Web UI v2.0.5...")
    print("Open browser at: http://127.0.0.1:%d" % port)
    try:
        app.run(host='127.0.0.1', port=port, debug=False)
    finally:
        # 程序退出时释放互斥体
        ctypes.windll.kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()
