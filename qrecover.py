#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QRecover Web UI - TestDisk/PhotoRec/Recuva GUI"""
import os
import sys
import locale
import shutil
import subprocess
import ctypes
import ctypes.wintypes
import logging
import threading
import time
import json
import tempfile
from flask import Flask, render_template_string, request, jsonify, Response

# ── Windows 控制台 UTF-8（修复中文乱码）──
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

def _decode_cli(raw):
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

from ai_assistant import assistant as ai_assistant

# ── 单实例检测：确保只有一个 QRecoverWeb 进程运行 ──
def _kill_old_instances():
    """终止所有占用端口 5000 的旧进程（排除自身）"""
    current_pid = os.getpid()
    pids_to_kill = set()

    # 方法1：查找所有监听端口 5000 的进程
    try:
        result = subprocess.run(
            ['netstat', '-ano', '-p', 'TCP'],
            capture_output=True, encoding='utf-8', errors='replace', timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if ':5000' in line and 'LISTENING' in line.upper():
                parts = line.split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid != current_pid and pid > 0:
                            pids_to_kill.add(pid)
                    except ValueError:
                        pass
    except Exception as e:
        logging.warning(f"netstat 检测失败: {e}")

    # 方法2：查找 QRecoverWeb.exe 进程
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

def _check_and_clear_process():
    """检查全局进程是否还在运行，如果已结束则清除（线程安全）"""
    global _ACTIVE_PROCESS
    with _ACTIVE_PROCESS_LOCK:
        if _ACTIVE_PROCESS is not None:
            # 如果是 Popen 对象
            if hasattr(_ACTIVE_PROCESS, 'poll'):
                if _ACTIVE_PROCESS.poll() is not None:
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
                    out = _decode_cli(out)
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
RECUVAIINSTALLER = None  # 不再内置安装器，缺省引导用户联网下载（见 /api/install_recuva）

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

app = Flask(__name__)

# ── 禁止浏览器缓存页面与接口，避免“网页端看不到新功能(AI)”的缓存问题 ──
@app.after_request
def _no_cache(resp):
    resp.headers.set('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
    resp.headers.set('Pragma', 'no-cache')
    resp.headers.set('Expires', '0')
    return resp

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

@app.route('/api/install_recuva')
def api_install_recuva():
    """启动 Recuva 安装程序或打开下载页面"""
    if os.path.isfile(RECUVAIINSTALLER):
        try:
            subprocess.Popen([RECUVAIINSTALLER])
            return jsonify({"status": "ok", "message": "Recuva 安装程序已启动，请在弹出的窗口中完成安装。"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"启动安装程序失败: {e}"})
    else:
        return jsonify({"status": "ok", "action": "open_url", "url": RECUVADOWNLOADURL, "message": "请先下载安装 Recuva。"})

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
def log_recuva_update(msg):
    """记录更新日志（无感，不打断界面）"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(RECUVaUPDATELOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def get_file_version(path):
    """读取 PE 文件的版本号（如 '1.54.120.0'），失败返回 None"""
    if not IS_WIN or not os.path.isfile(path):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        v = ctypes.windll.version
        v.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        v.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        v.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        v.GetFileVersionInfoW.restype = wintypes.BOOL
        v.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        v.VerQueryValueW.restype = wintypes.BOOL

        handle = wintypes.DWORD(0)
        size = v.GetFileVersionInfoSizeW(path, ctypes.byref(handle))
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not v.GetFileVersionInfoW(path, 0, size, ctypes.cast(buf, ctypes.c_void_p)):
            return None

        # 1) 优先用数值型固定文件版本（与代码页无关，最可靠）
        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        ffi_ptr = ctypes.c_void_p()
        ffi_len = wintypes.UINT(ctypes.sizeof(VS_FIXEDFILEINFO))
        if v.VerQueryValueW(buf, "\\", ctypes.byref(ffi_ptr), ctypes.byref(ffi_len)):
            ffi = ctypes.cast(ffi_ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
            ms, ls = ffi.dwFileVersionMS, ffi.dwFileVersionLS
            return "%d.%d.%d.%d" % ((ms >> 16) & 0xFFFF, ms & 0xFFFF,
                                     (ls >> 16) & 0xFFFF, ls & 0xFFFF)

        # 2) 回退：字符串版本（处理代码页，ANSI 用 latin-1 兜底）
        trans_ptr = ctypes.c_void_p()
        trans_len = wintypes.UINT(0)
        if not v.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                                 ctypes.byref(trans_ptr), ctypes.byref(trans_len)):
            return None
        lang = ctypes.cast(trans_ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
        sub = "\\StringFileInfo\\%04x%04x\\FileVersion" % (lang[0], lang[1])
        str_ptr = ctypes.c_void_p()
        str_len = wintypes.UINT(0)
        if not v.VerQueryValueW(buf, sub, ctypes.byref(str_ptr), ctypes.byref(str_len)):
            return None
        if lang[1] == 1200:  # 1200 = UTF-16 (Unicode)
            return ctypes.wstring_at(str_ptr, str_len.value // 2)
        raw = ctypes.string_at(str_ptr, str_len.value)
        return raw.split(b"\x00")[0].decode("latin-1", "replace")
    except Exception:
        return None


def parse_version(v):
    parts = []
    for p in (v or "").split('.'):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def get_recuva_local_version():
    exe = os.path.join(RECUVAPORTABLE, "recuva.exe")
    v = get_file_version(exe)
    if v:
        return v
    try:
        if os.path.isfile(RECUVAVERSIONFILE):
            with open(RECUVAVERSIONFILE, encoding="utf-8") as f:
                return f.read().strip() or None
    except Exception:
        pass
    return None


def _download_file(url, dest):
    # 本地文件路径（无 scheme，绝对或相对）：直接复制，支持 clone 后任意目录
    if "://" not in url and os.path.isfile(url):
        shutil.copyfile(url, dest)
        return
    # http(s)/file:// 交由 urllib
    import urllib.request
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


# ─────────── TestDisk / PhotoRec 按需下载 ───────────
_TESTDISK_DOWNLOADING = False
_TESTDISK_DOWNLOAD_LOCK = threading.Lock()


def ensure_testdisk(force=False):
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


def _sha256_of(path):
    """计算文件 SHA256，失败返回 None"""
    try:
        h = __import__("hashlib").sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest().lower()
    except Exception:
        return None


def _load_manifest():
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


def _obtain_installer(tmp):
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
            log_recuva_update("更新源下载失败，回退内置安装器: %s" % e)
    # 回退：使用项目内置的官方安装器（离线可用）
    if os.path.isfile(RECUVAIINSTALLER):
        dst = os.path.join(tmp, "bundled_installer.exe")
        shutil.copy2(RECUVAIINSTALLER, dst)
        log_recuva_update("使用内置安装包: " + RECUVAIINSTALLER)
        return dst, "exe"
    return None, None


def _copy_recuva_files(new_exe, install_dir):
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


def recuva_auto_update_check(force=False):
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


def start_recuva_updater():
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


@app.route('/api/drives')
def api_drives():
    return jsonify(get_drives())

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
def _build_ai_context():
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', '微软雅黑', 'PingFang SC', 'Noto Sans CJK SC', sans-serif;
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
        .drive-error {
            grid-column: 1 / -1;
            padding: 20px; text-align: center;
            background: rgba(255,80,80,0.08);
            border: 1px solid rgba(255,80,80,0.35);
            border-radius: 12px; color: #ff9a9a;
        }
        .drive-error-tip { font-size: 0.78rem; opacity: 0.85; margin-top: 4px; }
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

        /* ══ AI 智能恢复助手 ══ */
        .ai-fab {
            position: fixed;
            bottom: 24px; right: 24px;
            width: 60px; height: 60px;
            border-radius: 50%;
            background: var(--gradient-1);
            border: none;
            color: white;
            font-size: 26px;
            cursor: pointer;
            box-shadow: 0 6px 24px rgba(108,99,255,0.5);
            z-index: 9998;
            transition: transform 0.3s, box-shadow 0.3s;
            display: flex; align-items: center; justify-content: center;
        }
        .ai-fab:hover { transform: scale(1.1) rotate(10deg); box-shadow: 0 8px 32px rgba(108,99,255,0.7); }
        .ai-fab.hidden { transform: scale(0); opacity: 0; pointer-events: none; }

        .ai-panel {
            position: fixed;
            bottom: 96px; right: 24px;
            width: 380px; max-width: calc(100vw - 32px);
            height: 520px; max-height: calc(100vh - 140px);
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 18px;
            box-shadow: 0 12px 48px rgba(0,0,0,0.55);
            z-index: 9999;
            display: flex; flex-direction: column;
            overflow: hidden;
            transform: translateY(20px) scale(0.95);
            opacity: 0; pointer-events: none;
            transition: transform 0.3s cubic-bezier(0.4,0,0.2,1), opacity 0.3s;
        }
        .ai-panel.show { transform: translateY(0) scale(1); opacity: 1; pointer-events: all; }

        .ai-header {
            padding: 16px 18px;
            background: var(--gradient-1);
            color: white;
            display: flex; align-items: center; gap: 10px;
            position: relative;
        }
        .ai-header .ai-title { font-weight: 700; font-size: 0.95rem; flex: 1; }
        .ai-header .ai-sub { font-size: 0.72rem; opacity: 0.8; }
        .ai-header .ai-close {
            background: rgba(255,255,255,0.2); border: none; color: white;
            width: 28px; height: 28px; border-radius: 8px; cursor: pointer; font-size: 16px;
            display: flex; align-items: center; justify-content: center;
        }
        .ai-header .ai-close:hover { background: rgba(255,255,255,0.35); }

        .ai-messages {
            flex: 1; overflow-y: auto; padding: 16px;
            display: flex; flex-direction: column; gap: 12px;
            background: var(--bg);
        }
        .ai-msg {
            max-width: 88%; padding: 10px 14px; border-radius: 14px;
            font-size: 0.85rem; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
        }
        .ai-msg.user {
            align-self: flex-end;
            background: var(--gradient-1); color: white;
            border-bottom-right-radius: 4px;
        }
        .ai-msg.bot {
            align-self: flex-start;
            background: var(--card); border: 1px solid var(--border); color: var(--text);
            border-bottom-left-radius: 4px;
        }
        .ai-msg.bot strong { color: var(--accent); }
        .ai-msg.thinking { color: var(--text-dim); font-style: italic; }

        .ai-quick {
            padding: 10px 12px; display: flex; flex-wrap: wrap; gap: 6px;
            border-top: 1px solid var(--border); background: var(--card);
        }
        .ai-chip {
            font-size: 0.72rem; padding: 5px 10px; border-radius: 20px;
            background: var(--bg); border: 1px solid var(--border); color: var(--text-dim);
            cursor: pointer; transition: all 0.2s;
        }
        .ai-chip:hover { border-color: var(--accent); color: var(--accent); }

        .ai-input-row {
            display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border);
            background: var(--card);
        }
        .ai-input {
            flex: 1; padding: 10px 14px; border-radius: 12px;
            border: 1px solid var(--border); background: var(--bg); color: var(--text);
            font-size: 0.85rem; resize: none; outline: none; max-height: 80px;
            font-family: inherit;
        }
        .ai-input:focus { border-color: var(--accent); }
        .ai-send {
            padding: 0 16px; border: none; border-radius: 12px; cursor: pointer;
            background: var(--gradient-1); color: white; font-size: 0.85rem; font-weight: 700;
            transition: opacity 0.2s;
        }
        .ai-send:disabled { opacity: 0.4; cursor: not-allowed; }

        .ai-tools {
            margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border);
            display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
        }
        .ai-tools-tip { font-size: 0.78rem; opacity: 0.85; width: 100%; }
        .ai-tool-btn {
            padding: 7px 14px; border: none; border-radius: 10px; cursor: pointer;
            background: var(--gradient-1); color: white; font-size: 0.8rem; font-weight: 700;
            box-shadow: 0 2px 10px var(--accent-glow);
        }
        .ai-tool-btn:hover { filter: brightness(1.1); }

        .ai-config-btn {
            position: absolute; top: 14px; right: 46px;
            background: rgba(255,255,255,0.2); border: none; color: white;
            width: 28px; height: 28px; border-radius: 8px; cursor: pointer; font-size: 13px;
        }
        .ai-config-btn:hover { background: rgba(255,255,255,0.35); }
        .ai-header .ai-clear {
            background: rgba(255,255,255,0.2); border: none; color: white;
            width: 28px; height: 28px; border-radius: 8px; cursor: pointer; font-size: 14px;
            display: flex; align-items: center; justify-content: center;
        }
        .ai-header .ai-clear:hover { background: rgba(255,255,255,0.35); }

        .ai-config {
            padding: 16px; background: var(--bg); border-top: 1px solid var(--border);
            display: none; flex-direction: column; gap: 10px;
        }
        .ai-config.show { display: flex; }
        .ai-config label { font-size: 0.75rem; color: var(--text-dim); }
        .ai-config select, .ai-config input {
            padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border);
            background: var(--card); color: var(--text); font-size: 0.8rem; outline: none;
            font-family: inherit;
        }
        .ai-config select:focus, .ai-config input:focus { border-color: var(--accent); }
        .ai-config .ai-save {
            padding: 8px; border: none; border-radius: 8px; cursor: pointer;
            background: var(--gradient-2); color: white; font-weight: 700; font-size: 0.8rem;
        }
        .ai-config .ai-hint { font-size: 0.68rem; color: var(--text-dim); line-height: 1.5; }

        /* ══ 组件安装向导 ══ */
        .setup-panel {
            background: linear-gradient(135deg, rgba(108,99,255,0.08), rgba(0,214,143,0.05));
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px 18px;
            margin-bottom: 22px;
        }
        .setup-panel.hidden { display: none; }
        .setup-desc { font-size: 0.82rem; color: var(--text-dim); margin-bottom: 16px; line-height: 1.5; }
        .setup-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
        .setup-card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 18px 16px;
            text-align: center;
            display: flex; flex-direction: column; align-items: center; gap: 8px;
        }
        .setup-icon { font-size: 2.2rem; }
        .setup-name { font-weight: 700; font-size: 0.92rem; }
        .setup-status { font-size: 0.76rem; color: var(--text-dim); min-height: 18px; }
        .setup-status.ready { color: var(--success); }
        .setup-status.pending { color: var(--warning); }
        .setup-status.working { color: var(--info); }
        .setup-btn {
            margin-top: 4px;
            padding: 9px 16px; border: none; border-radius: 10px; cursor: pointer;
            background: var(--gradient-1); color: #fff; font-size: 0.82rem; font-weight: 700;
            transition: all 0.2s; width: 100%;
        }
        .setup-btn:hover:not(:disabled) { filter: brightness(1.1); transform: translateY(-1px); }
        .setup-btn:disabled { opacity: 0.55; cursor: not-allowed; }
        .setup-btn.done { background: var(--gradient-2); }
        @media (max-width: 480px) { .setup-grid { grid-template-columns: 1fr; } }
    </style>
    <script>
        // 全局错误兜底：忽略跨域/无详情的脚本错误（预览 WebView 常见），
        // 避免其冒泡为弹窗；真实错误仅记录，不阻断页面交互。
        window.addEventListener('error', function (e) {
            if (!e || !e.message || e.message === 'Script error.' || e.message === '') {
                if (e && e.preventDefault) { try { e.preventDefault(); } catch (_) {} }
                return false;
            }
            console.warn('[QRecover] 已捕获页面错误:', e.message);
        }, true);
        window.addEventListener('unhandledrejection', function (e) {
            console.warn('[QRecover] 未处理的 Promise 拒绝（已忽略）:', e && e.reason);
        });
    </script>
</head>
<body>
    <canvas id="confetti-canvas"></canvas>
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

        <!-- ══ 组件安装向导 ══ -->
        <div class="setup-panel hidden" id="setupPanel">
            <div class="section-title">🛠️ 首次使用 · 一键安装恢复引擎</div>
            <p class="setup-desc">开始恢复前，请先安装恢复引擎（仅首次，自动下载并缓存到本地 <code>tools/</code> 目录）：</p>
            <div class="setup-grid">
                <div class="setup-card">
                    <div class="setup-icon">🔧</div>
                    <div class="setup-name">TestDisk / PhotoRec</div>
                    <div class="setup-status" id="setupStatusTestDisk">检测中…</div>
                    <button class="setup-btn" id="btnInstallTestDisk" onclick="installTestDisk()">一键安装</button>
                </div>
                <div class="setup-card">
                    <div class="setup-icon">🎨</div>
                    <div class="setup-name">Recuva（可选）</div>
                    <div class="setup-status" id="setupStatusRecuva">检测中…</div>
                    <button class="setup-btn" id="btnInstallRecuva" onclick="installRecuva()">一键安装</button>
                </div>
            </div>
            <div class="status" id="setupStatus" style="margin-top:14px;"></div>
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
            // 更新工具切换滑块颜色
            const ts = document.querySelector('.tool-switch');
            if (ts) ts.style.setProperty('--slide-bg', theme.gradient1);
            updateThemeUI(name);
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

        // 加载驱动器（带重试 + 友好提示）
        async function loadDrives() {
            const list = document.getElementById('driveList');
            const prevSelected = selectedDrive;
            const maxRetry = 3;
            for (let attempt = 1; attempt <= maxRetry; attempt++) {
                try {
                    const res = await fetch('/api/drives');
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    const drives = await res.json();
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
                    return;
                } catch (e) {
                    if (attempt < maxRetry) { await new Promise(r => setTimeout(r, 800)); continue; }
                    list.innerHTML = `
                        <div class="drive-error">
                            <div>⚠️ 驱动器列表加载失败：${e.message}</div>
                            <div class="drive-error-tip">请确认 QRecover 服务正在运行（python qrecover.py），然后点击重试。</div>
                            <button class="ai-tool-btn" style="margin-top:10px;" onclick="loadDrives()">🔄 重试</button>
                        </div>`;
                }
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

        // ── 组件安装向导 ──
        function showSetupStatus(type, msg) {
            const el = document.getElementById('setupStatus');
            if (!el) return;
            const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
            el.className = 'status show ' + (type || '');
            el.innerHTML = `<span class="status-icon">${icons[type] || '📋'}</span><span>${msg}</span>`;
        }

        function setSetupCard(kind, status, text, installed) {
            const statusEl = document.getElementById('setupStatus' + (kind === 'testdisk' ? 'TestDisk' : 'Recuva'));
            const btn = document.getElementById('btnInstall' + (kind === 'testdisk' ? 'TestDisk' : 'Recuva'));
            if (statusEl) {
                statusEl.className = 'setup-status ' + (status || '');
                statusEl.textContent = text;
            }
            if (btn) {
                if (installed) {
                    btn.textContent = '✅ 已安装';
                    btn.classList.add('done');
                    btn.disabled = true;
                } else {
                    btn.textContent = '一键安装';
                    btn.classList.remove('done');
                    btn.disabled = false;
                }
            }
        }

        async function refreshSetup() {
            try {
                const res = await fetch('/api/tools');
                const tools = await res.json();
                const panel = document.getElementById('setupPanel');
                if (!panel) return;
                let anyMissing = false;

                if (tools.testdisk) {
                    setSetupCard('testdisk', 'ready', '✅ 已就绪', true);
                } else {
                    anyMissing = true;
                    setSetupCard('testdisk', 'pending', '⬇️ 待安装（约 20MB）', false);
                }

                if (tools.recuva) {
                    setSetupCard('recuva', 'ready', '✅ 已就绪', true);
                } else {
                    anyMissing = true;
                    setSetupCard('recuva', 'pending', '⬇️ 待安装（可选）', false);
                }

                panel.classList.toggle('hidden', !anyMissing);
            } catch (e) {
                console.warn('安装向导检测失败:', e);
            }
        }

        async function installTestDisk() {
            const btn = document.getElementById('btnInstallTestDisk');
            const statusEl = document.getElementById('setupStatusTestDisk');
            if (btn) btn.disabled = true;
            if (statusEl) { statusEl.className = 'setup-status working'; statusEl.textContent = '⏳ 正在下载并解压…'; }
            showSetupStatus('info', '正在下载 TestDisk / PhotoRec（约 20MB），请稍候…');
            try {
                const res = await fetch('/api/testdisk/ensure', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force: false })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    setSetupCard('testdisk', 'ready', '✅ 已就绪', true);
                    showSetupStatus('success', 'TestDisk / PhotoRec 安装完成！现在可以开始恢复。');
                } else {
                    setSetupCard('testdisk', 'pending', '❌ 安装失败', false);
                    showSetupStatus('error', (data.message || '安装失败') + ' 你也可以手动下载并解压到 tools/testdisk/ 目录。');
                }
            } catch (e) {
                setSetupCard('testdisk', 'pending', '❌ 网络错误', false);
                showSetupStatus('error', '安装请求失败：' + e.message);
            } finally {
                if (btn) btn.disabled = false;
                checkTools();
            }
        }

        // 安装 Recuva（向导面板与工具提示共用）
        async function installRecuva() {
            const btn = document.getElementById('btnInstallRecuva');
            const statusEl = document.getElementById('setupStatusRecuva');
            if (btn) btn.disabled = true;
            if (statusEl) { statusEl.className = 'setup-status working'; statusEl.textContent = '⏳ 正在尝试安装…'; }
            showSetupStatus('info', '正在尝试自动安装 Recuva…');
            try {
                const res = await fetch('/api/recuva/install', { method: 'POST' });
                const data = await res.json();
                if (data.installed) {
                    setSetupCard('recuva', 'ready', '✅ 已就绪', true);
                    showSetupStatus('success', data.message || 'Recuva 安装完成！');
                } else if (data.action === 'open_url') {
                    window.open(data.url, '_blank');
                    setSetupCard('recuva', 'pending', '请在浏览器中下载并安装', false);
                    showSetupStatus('warning', '已为你打开 Recuva 官方下载页，请下载并安装。安装后本工具会自动识别（也可把便携版放到 tools/recuva/）。');
                } else {
                    showSetupStatus('error', data.message || 'Recuva 安装失败');
                }
            } catch (e) {
                showSetupStatus('error', '安装请求失败：' + e.message);
            } finally {
                if (btn) btn.disabled = false;
                checkTools();
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
            await refreshSetup();
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
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
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
    <!-- ══ AI 智能恢复助手 ══ -->
    <button class="ai-fab" id="aiFab" title="智能恢复助手">🤖</button>
    <div class="ai-panel" id="aiPanel">
        <div class="ai-header">
            <span style="font-size:1.3rem;">🤖</span>
            <div>
                <div class="ai-title">智能恢复助手</div>
                <div class="ai-sub">描述你的情况，AI 帮你选方案</div>
            </div>
            <button class="ai-config-btn" id="aiConfigBtn" title="AI 设置">⚙</button>
            <button class="ai-clear" id="aiClear" title="清空对话">🗑</button>
            <button class="ai-close" id="aiClose" title="收起">✕</button>
        </div>
        <div class="ai-config" id="aiConfig">
            <label>AI 模式</label>
            <select id="cfgProvider">
                <option value="heuristic">本地智能分析（无需联网，开箱即用）</option>
                <option value="openai">LLM API（OpenAI 兼容）</option>
                <option value="ollama">本地 Ollama</option>
            </select>
            <label>API 地址 (Base URL)</label>
            <input id="cfgBaseUrl" placeholder="https://api.openai.com/v1" value="https://api.openai.com/v1">
            <label>模型名</label>
            <input id="cfgModel" placeholder="gpt-3.5-turbo" value="gpt-3.5-turbo">
            <label>API Key</label>
            <input id="cfgApiKey" type="password" placeholder="sk-..." >
            <button class="ai-save" id="cfgSave">保存设置</button>
            <div class="ai-hint">本地模式无需任何配置即可使用。配置 LLM 后，助手将获得更强的语义理解能力。API Key 仅保存在本地 ai_config.json。</div>
        </div>
        <div class="ai-messages" id="aiMessages">
            <div class="ai-msg bot">👋 你好！我是 QRecover 智能恢复助手。<br><br>请描述你遇到的数据丢失情况，例如：<br>· "我不小心把U盘格式化了，里面有旅行照片"<br>· "D盘分区打不开，提示未格式化"<br>· "误删了重要的Word文档"<br><br>我会帮你推荐最合适的恢复工具和步骤。</div>
        </div>
        <div class="ai-quick" id="aiQuick">
            <span class="ai-chip" onclick="aiSendQuick('U盘被格式化了，里面有照片')">U盘格式化·照片</span>
            <span class="ai-chip" onclick="aiSendQuick('D盘打不开，提示未格式化')">分区RAW·打不开</span>
            <span class="ai-chip" onclick="aiSendQuick('误删了重要文档')">误删文档</span>
            <span class="ai-chip" onclick="aiSendQuick('回收站被清空了')">回收站清空</span>
        </div>
        <div class="ai-input-row">
            <textarea class="ai-input" id="aiInput" rows="1" placeholder="描述你的情况..." onkeydown="aiKey(event)"></textarea>
            <button class="ai-send" id="aiSend" onclick="aiSend()">发送</button>
        </div>
    </div>

    <script>
        // ── AI 智能恢复助手逻辑 ──
        let aiHistory = [];
        let aiBusy = false;

        const aiFab = document.getElementById('aiFab');
        const aiPanel = document.getElementById('aiPanel');
        const aiMessages = document.getElementById('aiMessages');
        const aiInput = document.getElementById('aiInput');
        const aiSendBtn = document.getElementById('aiSend');

        function aiOpen() {
            aiPanel.classList.add('show');
            aiFab.classList.add('hidden');
            aiInput.focus();
        }
        function aiClosePanel() {
            aiPanel.classList.remove('show');
            aiFab.classList.remove('hidden');
        }
        aiFab.onclick = aiOpen;
        document.getElementById('aiClose').onclick = aiClosePanel;

        // 设置面板
        document.getElementById('aiConfigBtn').onclick = function() {
            const cfg = document.getElementById('aiConfig');
            cfg.classList.toggle('show');
            if (cfg.classList.contains('show')) loadAiConfig();
        };
        document.getElementById('cfgSave').onclick = saveAiConfig;

        function loadAiConfig() {
            fetch('/api/ai/config').then(r => r.json()).then(cfg => {
                document.getElementById('cfgProvider').value = cfg.provider || 'heuristic';
                document.getElementById('cfgBaseUrl').value = cfg.base_url || 'https://api.openai.com/v1';
                document.getElementById('cfgModel').value = cfg.model || 'gpt-3.5-turbo';
                if (cfg.configured && cfg.provider !== 'heuristic') {
                    document.getElementById('cfgApiKey').placeholder = '已配置 (留空不修改)';
                }
            }).catch(() => {});
        }
        function saveAiConfig() {
            const payload = {
                provider: document.getElementById('cfgProvider').value,
                base_url: document.getElementById('cfgBaseUrl').value,
                model: document.getElementById('cfgModel').value,
                api_key: document.getElementById('cfgApiKey').value,
            };
            fetch('/api/ai/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(r => r.json()).then(res => {
                if (res.ok) {
                    alert('AI 设置已保存');
                    document.getElementById('aiConfig').classList.remove('show');
                } else {
                    alert('保存失败: ' + (res.error || '未知错误'));
                }
            }).catch(e => alert('保存失败: ' + e.message));
        }

        function aiEscape(text) {
            return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
        function aiRender(inner) {
            // inner 为纯文本（含 \n 与 **加粗**）
            return aiEscape(inner)
                .replace(/\n/g, '<br>')
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        }
        function aiAddMsg(role, text) {
            const div = document.createElement('div');
            div.className = 'ai-msg ' + role;
            if (role === 'bot') {
                div.innerHTML = aiRender(text);
            } else {
                div.textContent = text;
            }
            aiMessages.appendChild(div);
            aiMessages.scrollTop = aiMessages.scrollHeight;
            return div;
        }

        const AI_TOOL_LABEL = {'testdisk':'打开 TestDisk', 'photorec':'打开 PhotoRec', 'recuva':'打开 Recuva'};
        function aiLaunchTool(tool, drive) {
            let url;
            if (tool === 'testdisk') url = '/api/scan?tool=testdisk';
            else if (tool === 'photorec') url = '/api/recover?tool=testdisk';   // recover 路由的 testdisk = PhotoRec
            else url = '/api/recover?tool=' + tool;                              // recuva
            if (drive) url += '&drive=' + drive;
            fetch(url).then(r => r.json()).then(res => {
                alert(res.status === 'ok' ? res.message : ('启动失败：' + (res.message || '')));
            }).catch(e => alert('启动失败：' + e.message));
        }
        function aiAddRecommend(div, tools, drive) {
            if (!tools || !tools.length) return;
            const wrap = document.createElement('div');
            wrap.className = 'ai-tools';
            const tip = document.createElement('div');
            tip.className = 'ai-tools-tip';
            tip.textContent = '👉 一键启动推荐工具：';
            wrap.appendChild(tip);
            tools.forEach(t => {
                const b = document.createElement('button');
                b.className = 'ai-tool-btn';
                b.textContent = AI_TOOL_LABEL[t] || ('打开 ' + t);
                b.onclick = () => aiLaunchTool(t, drive);
                wrap.appendChild(b);
            });
            div.appendChild(wrap);
            aiMessages.scrollTop = aiMessages.scrollHeight;
        }

        function aiSend() {
            const text = aiInput.value.trim();
            if (!text || aiBusy) return;
            aiAddMsg('user', text);
            aiInput.value = '';
            aiBusy = true;
            aiSendBtn.disabled = true;
            const botDiv = aiAddMsg('bot', '🤔 正在分析你的情况...');

            const body = JSON.stringify({ message: text, history: aiHistory });
            let full = '', lastRec = null;
            fetch('/api/ai/chat/stream', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: body
            }).then(r => {
                if (!r.ok || !r.body) throw new Error('流式接口异常');
                const reader = r.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buf = '';
                function pump() {
                    return reader.read().then(({done, value}) => {
                        if (done) return;
                        buf += decoder.decode(value, {stream: true});
                        const parts = buf.split('\n\n');
                        buf = parts.pop();
                        for (const p of parts) {
                            const line = p.trim();
                            if (!line.startsWith('data:')) continue;
                            const json = JSON.parse(line.slice(5).trim());
                            if (json.chunk) {
                                full += json.chunk;
                                botDiv.innerHTML = aiRender(full);
                                aiMessages.scrollTop = aiMessages.scrollHeight;
                            } else if (json.recommend) {
                                lastRec = json.recommend;
                            } else if (json.error) {
                                full += '\n[错误] ' + json.error;
                                botDiv.innerHTML = aiRender(full);
                            }
                        }
                        return pump();
                    });
                }
                return pump();
            }).then(() => {
                if (lastRec) aiAddRecommend(botDiv, lastRec.tools, lastRec.drive);
                aiHistory.push({role: 'user', content: text});
                aiHistory.push({role: 'assistant', content: full});
            }).catch(e => {
                botDiv.innerHTML = aiRender('❌ 网络错误：' + e.message);
            }).finally(() => {
                aiBusy = false;
                aiSendBtn.disabled = false;
            });
        }

        function aiSendQuick(text) {
            aiInput.value = text;
            aiSend();
        }

        function aiKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                aiSend();
            }
        }

        function aiClearChat() {
            aiHistory = [];
            aiMessages.innerHTML = '<div class="ai-msg bot">👋 对话已清空。请重新描述你遇到的数据丢失情况，我会帮你推荐最合适的恢复工具和步骤。</div>';
        }
        document.getElementById('aiClear').onclick = aiClearChat;

        // 自动调整输入框高度
        aiInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 80) + 'px';
        });
    </script>
</body>
</html>
"""

# ─────────── Main ───────────
def main():
    # 单实例检测：确保只有一个进程
    mutex = ensure_single_instance()
    # 启动 Recuva 无感自动更新（后台线程，不打断界面）
    start_recuva_updater()
    print("Starting QRecover Web UI v1.1.7...")
    print("Open browser at: http://127.0.0.1:5000")
    try:
        app.run(host='127.0.0.1', port=5000, debug=False)
    finally:
        # 程序退出时释放互斥体
        ctypes.windll.kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()
