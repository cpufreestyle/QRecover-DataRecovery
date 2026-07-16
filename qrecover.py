#!/usr/bin/env python3
"""QRecover Web UI - TestDisk/PhotoRec/Recuva GUI"""
import os
import sys
import shutil
import subprocess
import ctypes
import ctypes.wintypes
import logging
import threading
import time
import tempfile
import zipfile
import urllib.request
from flask import Flask, render_template_string, request, jsonify

# ── 单实例检测：确保只有一个 QRecoverWeb 进程运行 ──
def ensure_single_instance():
    """确保只有一个实例运行，如果有旧进程则终止它"""
    current_pid = os.getpid()
    exe_name = os.path.basename(sys.executable).lower()
    # 需要清理的进程名：已知产品 EXE + 当前可执行文件名（仅当不是 python 解释器时）
    targets = ['qrecoverweb.exe']
    if exe_name not in ('python.exe', 'pythonw.exe', 'py.exe'):
        targets.append(exe_name)

    for name in targets:
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split('\n'):
                if name not in line.lower():
                    continue
                parts = line.split(',')
                if len(parts) < 2:
                    continue
                pid_str = parts[1].strip('"')
                try:
                    pid = int(pid_str)
                except ValueError:
                    continue
                if pid != current_pid:
                    logging.info(f"终止旧进程 {name} PID={pid}")
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                                 capture_output=True, timeout=5)
                    time.sleep(0.5)
        except Exception as e:
            logging.warning(f"检测旧进程 {name} 失败: {e}")
    
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

# 工具自动安装配置（缺工具时由界面一键下载安装）
TESTDISK_ZIP_URL = "https://www.cgsecurity.org/testdisk-7.3-WIP.win64.zip"
BUNDLE_ZIP_URL = "https://gitee.com/cpufreestyle/QRecover/releases/download/v1.1.9/QRecoverWeb-v1.1.9.zip"
RECUVA_INSTALLER_NAME = "Recuva_1.54.120_Machine_X64_nullsoft_en-US.exe"

# 安装任务状态，供前端轮询
_INSTALL_STATE = {
    "running": False,
    "step": "idle",
    "progress": 0,
    "message": "",
    "done": False,
    "error": None,
}
_INSTALL_LOCK = threading.Lock()
_INSTALL_THREAD = None


def _install_report(progress, step, message="", done=False):
    _INSTALL_STATE["progress"] = progress
    _INSTALL_STATE["step"] = step
    if message:
        _INSTALL_STATE["message"] = message
    if done:
        _INSTALL_STATE["done"] = True
    log.info("[install] %s%% %s %s", progress, step, message)


def _download_file(url, dest, prog0, prog1):
    """下载文件到 dest，期间把进度从 prog0 上报到 prog1。"""
    req = urllib.request.Request(url, headers={"User-Agent": "QRecover/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        fetched = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                fetched += len(chunk)
                if total:
                    p = prog0 + int(fetched / total * (prog1 - prog0))
                    _install_report(p, "download",
                                    f"下载中 {fetched // 1048576}MB / {total // 1048576}MB")
                else:
                    _install_report(min(prog1, prog0 + fetched // 1048576),
                                    "download", f"下载中 {fetched // 1048576}MB")


def _extract_testdisk(zip_path, dest_dir):
    """从压缩包中解压 testdisk-7.3-WIP（含 testdisk_win.exe / photorec_win.exe）。"""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        exe = [n for n in names if n.lower().endswith("testdisk_win.exe")]
        if not exe:
            raise RuntimeError("压缩包内未找到 testdisk_win.exe")
        root = exe[0].replace("\\", "/").split("/")[0]
        members = [n for n in names if n.startswith(root + "/") or n.startswith(root + "\\")]
        z.extractall(dest_dir, members=members)
    src = os.path.join(dest_dir, root)
    dst = os.path.join(dest_dir, "testdisk-7.3-WIP")
    if os.path.normpath(src) != os.path.normpath(dst):
        if os.path.isdir(dst):
            for item in os.listdir(src):
                shutil.move(os.path.join(src, item), os.path.join(dst, item))
            os.rmdir(src)
        elif os.path.isdir(src):
            os.rename(src, dst)


def _extract_zip_exe(zip_path, name_substr, out_path):
    """从压缩包提取第一个名称包含 name_substr 的 .exe 到 out_path。"""
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            nl = n.lower()
            if name_substr in nl and nl.endswith(".exe"):
                with z.open(n) as s, open(out_path, "wb") as o:
                    o.write(s.read())
                return True
    return False


def _install_recuva(installer_exe):
    """静默安装 Recuva 到项目内 recuva_portable 目录（必要时触发 UAC 提权）。"""
    port = os.path.join(BASE_DIR, "recuva_portable")
    os.makedirs(port, exist_ok=True)
    try:
        subprocess.run([installer_exe, "/S", f"/D={port}"], timeout=180, check=False)
    except OSError as e:
        if getattr(e, 'winerror', None) == 740 or '740' in str(e):
            # WinError 740 = 需要提权，用 ShellExecuteW 触发 UAC
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", installer_exe, f"/S /D={port}", port, 0)
    # 提权安装为异步，等待 recuva.exe 出现
    for _ in range(40):
        if os.path.isfile(os.path.join(port, "recuva.exe")):
            return True
        time.sleep(1)
    return os.path.isfile(os.path.join(port, "recuva.exe"))


def _install_worker():
    """后台线程：自动下载并安装缺失的恢复工具。"""
    global _INSTALL_THREAD
    try:
        _INSTALL_STATE["running"] = True
        _INSTALL_STATE["done"] = False
        _INSTALL_STATE["error"] = None
        need_td = not os.path.isfile(TESTDISK_EXE)
        need_rec = find_recuva() is None
        if not need_td and not need_rec:
            _install_report(100, "done", "所有工具均已安装，无需下载。", done=True)
            return

        tmp = tempfile.mkdtemp(prefix="qrec_install_")
        try:
            if need_rec:
                # 捆绑包同时包含 TestDisk 与 Recuva 安装程序
                dl = os.path.join(tmp, "bundle.zip")
                _install_report(3, "download", "正在下载 QRecover 工具包…")
                _download_file(BUNDLE_ZIP_URL, dl, 3, 75)
                if need_td:
                    _install_report(78, "extract", "正在解压 TestDisk / PhotoRec…")
                    _extract_testdisk(dl, BASE_DIR)
                    if not os.path.isfile(TESTDISK_EXE):
                        raise RuntimeError("TestDisk 解压失败，请检查下载文件。")
                _install_report(85, "extract", "正在自动安装 Recuva…")
                inst = os.path.join(tmp, "recuva_inst.exe")
                if _extract_zip_exe(dl, "recuva_1.54", inst):
                    _install_recuva(inst)
                _install_report(100, "done",
                                "✅ 工具已自动安装完成，现在可以使用全部恢复功能。", done=True)
            else:
                # 仅需 TestDisk：优先官方源（更小），失败则回退捆绑包
                dl = os.path.join(tmp, "td.zip")
                try:
                    _install_report(3, "download", "正在下载 TestDisk…")
                    _download_file(TESTDISK_ZIP_URL, dl, 3, 80)
                    _extract_testdisk(dl, BASE_DIR)
                    _install_report(100, "done", "✅ TestDisk 已自动安装完成。", done=True)
                except Exception as e:
                    log.warning("官方源下载失败，回退到备用源: %s", e)
                    _install_report(3, "download", "官方源失败，改用备用源…")
                    _download_file(BUNDLE_ZIP_URL, dl, 3, 80)
                    _extract_testdisk(dl, BASE_DIR)
                    _install_report(100, "done", "✅ TestDisk 已自动安装完成。", done=True)
        except Exception as e:
            log.exception("工具自动安装失败")
            _INSTALL_STATE["error"] = str(e)
            _INSTALL_STATE["message"] = f"安装失败：{e}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            _INSTALL_STATE["running"] = False
    finally:
        _INSTALL_THREAD = None


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

def get_drive_detail(letter):
    """获取单个驱动器的详细信息：类型 / 文件系统 / 是否系统盘"""
    detail = {"letter": letter, "type": "fixed", "fstype": "未知", "system": False}
    if IS_WIN:
        k32 = ctypes.windll.kernel32
        try:
            k32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
            k32.GetDriveTypeW.restype = ctypes.c_uint
            dt = k32.GetDriveTypeW(f"{letter}:\\")
            # 0未知 1无根 2可移动 3固定 4网络 5光驱 6内存盘
            if dt == 2:
                detail["type"] = "removable"
            elif dt == 3:
                detail["type"] = "fixed"
            elif dt == 4:
                detail["type"] = "remote"
            elif dt in (5, 6):
                detail["type"] = "other"
        except Exception:
            pass
        try:
            k32.GetVolumeInformationW.argtypes = [
                ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_wchar_p, ctypes.c_uint
            ]
            k32.GetVolumeInformationW.restype = ctypes.c_int
            buf = ctypes.create_unicode_buffer(64)
            k32.GetVolumeInformationW(f"{letter}:\\", None, 0, None, None, None, buf, 64)
            detail["fstype"] = buf.value or "未知"
        except Exception:
            pass
        try:
            sd = os.environ.get("SystemDrive", "C:").rstrip("\\")
            if sd.upper().startswith(letter.upper() + ":"):
                detail["system"] = True
        except Exception:
            pass
    return detail

def build_ai_advice(drive, info, scenario):
    """本地规则：根据磁盘信息与场景生成智能恢复建议（不联网）"""
    dtype = info.get("type", "fixed")
    is_sys = info.get("system", False)
    fstype = info.get("fstype", "未知")
    dtype_cn = {"removable": "可移动磁盘(U盘/SD卡)", "fixed": "本地磁盘",
                "remote": "网络磁盘", "other": "其他设备"}.get(dtype, "磁盘")

    warnings = [
        f"恢复的文件务必保存到【其他磁盘】，不要存回 {drive}: 盘，否则会覆盖待恢复数据。",
        "发现数据丢失后，立即停止对该盘的任何写入（勿装软件、勿存文件）。",
    ]
    if is_sys:
        warnings.append("这是系统盘，操作请格外谨慎；重要数据建议先做磁盘镜像再恢复。")

    if scenario == "deleted":
        tool = "recuva"
        reason = (f"{drive}: 为{dtype_cn}（文件系统 {fstype}）。针对【误删文件】场景，"
                  "推荐使用 Recuva：图形化向导、上手快，对近期删除的文件恢复成功率最高。")
        steps = [
            "在上方工具切换中选择 Recuva（已为你高亮）。",
            f"点击「扫描磁盘」，在 Recuva 向导里选择 {drive}: 盘。",
            "选择文件类型或直接「扫描」，预览找到的文件。",
            "勾选要恢复的文件，点击恢复并保存到【其他磁盘】。",
        ]
        if is_sys:
            reason += "若 Recuva 找不到，再切换到 TestDisk，用「恢复文件」启动 PhotoRec 做深度雕刻。"
    elif scenario == "formatted":
        tool = "testdisk"
        reason = (f"{drive}: 盘（{fstype}）疑似被格式化/清空。格式化会重建文件系统，"
                  "依赖目录结构的恢复效果差，应使用 PhotoRec 按文件特征做「文件雕刻」。"
                  "请选择 TestDisk 工具，再用「恢复文件」按钮启动 PhotoRec。")
        steps = [
            "在工具切换中选择 TestDisk。",
            "点击「恢复文件」（实际启动 PhotoRec 文件雕刻）。",
            f"选择 {drive}: 盘与要恢复的文件类型。",
            "将结果保存到【其他磁盘】，等待深度扫描完成。",
        ]
        warnings.append("格式化后越早恢复成功率越高，继续使用会加剧覆盖。")
    elif scenario == "partition":
        tool = "testdisk"
        reason = (f"{drive}: 盘出现【分区丢失/误删分区/引导损坏】。TestDisk 擅长重建分区表、"
                  "找回丢失分区并修复引导，是最合适的工具。")
        steps = [
            "选择 TestDisk 工具并点击「扫描磁盘」。",
            f"在 TestDisk 中选择 {drive}: 所在的物理磁盘（PhysicalDrive）。",
            "分析分区结构，找到丢失分区后将其标记为可恢复。",
            "写回分区表前务必确认无误（建议先备份）。",
        ]
        warnings.append("写回分区表有风险，请先确认检测到的分区正确。")
    elif scenario == "inaccessible":
        tool = "testdisk"
        reason = (f"{drive}: 盘无法访问/提示未格式化，多为分区表或引导损坏。"
                  "先用 TestDisk 尝试修复；若数据重要则直接用 PhotoRec 抢救文件。")
        steps = [
            f"选择 TestDisk，扫描 {drive}: 所在的物理磁盘。",
            "尝试修复分区表/引导，使磁盘重新可访问。",
            "若仍不可访问，切换「恢复文件」用 PhotoRec 抢救数据。",
        ]
    else:
        tool = "recuva"
        reason = "请选择上方「发生了什么」场景，AI 会给出更精准的建议。"
        steps = []

    return {
        "drive": drive,
        "scenario": scenario,
        "drive_type": dtype,
        "fstype": fstype,
        "system": is_sys,
        "recommended_tool": tool,
        "recommended_label": "Recuva" if tool == "recuva" else "TestDisk / PhotoRec",
        "reason": reason,
        "steps": steps,
        "warnings": warnings,
        "output_tip": f"建议恢复目标：除 {drive}: 之外的其他磁盘（如 D:\\Recovered）。",
    }

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

@app.route('/api/install_recuva', methods=['POST'])
@app.route('/api/install_tools', methods=['POST'])
def api_install_tools():
    """自动下载并安装缺失的恢复工具（后台执行，前端轮询进度）。"""
    global _INSTALL_THREAD
    with _INSTALL_LOCK:
        if _INSTALL_STATE["running"] or (_INSTALL_THREAD and _INSTALL_THREAD.is_alive()):
            return jsonify({"status": "running", "message": "安装正在进行中…"})
        _INSTALL_THREAD = threading.Thread(target=_install_worker, daemon=True)
        _INSTALL_THREAD.start()
    return jsonify({"status": "started", "message": "已开始自动安装工具，请在界面查看进度。"})


@app.route('/api/install_status')
def api_install_status():
    """返回当前工具安装进度。"""
    return jsonify(_INSTALL_STATE)

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

@app.route('/api/ai_advice')
def api_ai_advice():
    """AI 智能扫描建议（本地规则，无需联网/API）"""
    drive = (request.args.get('drive', '') or '').upper()
    scenario = request.args.get('scenario', 'auto')
    if not drive or len(drive) != 1 or not drive.isalpha():
        return jsonify({"ok": False, "message": "请先选择目标驱动器"})
    info = get_drive_detail(drive)
    if scenario == 'auto':
        scenario = 'deleted'  # 默认误删是最常见的场景
    advice = build_ai_advice(drive, info, scenario)
    return jsonify({"ok": True, **advice})

# ─────────── HTML Template ───────────
HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QRecover v2.0.0</title>
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

        /* 工具自动安装横幅 */
        .install-banner {
            display: flex;
            align-items: center;
            gap: 14px;
            background: linear-gradient(135deg, rgba(108,99,255,0.12), rgba(0,214,135,0.10));
            border: 1px solid var(--accent);
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 22px;
            box-shadow: 0 4px 20px var(--accent-glow);
        }
        .install-banner.hidden { display: none; }
        .ib-icon { font-size: 1.8rem; line-height: 1; }
        .ib-body { flex: 1; min-width: 0; }
        .ib-title { font-weight: 700; font-size: 0.95rem; color: var(--text); margin-bottom: 4px; }
        .ib-msg { font-size: 0.8rem; color: var(--text-dim); margin-bottom: 8px; word-break: break-all; }
        .ib-progress { height: 8px; background: var(--border); border-radius: 99px; overflow: hidden; }
        .ib-bar {
            height: 100%; width: 0%;
            background: var(--gradient-1);
            border-radius: 99px;
            transition: width 0.4s ease;
        }
        .ib-pct { font-size: 0.85rem; font-weight: 700; color: var(--accent); min-width: 38px; text-align: right; }

        /* AI 智能建议面板 */
        .ai-panel {
            background: linear-gradient(135deg, rgba(108,99,255,0.10), rgba(0,214,135,0.07));
            border: 1px solid var(--accent);
            border-radius: 16px;
            padding: 16px 18px;
            margin-bottom: 22px;
            box-shadow: 0 4px 24px var(--accent-glow);
            position: relative;
            overflow: hidden;
        }
        .ai-panel::before {
            content: '';
            position: absolute; top: -40%; right: -10%;
            width: 180px; height: 180px;
            background: radial-gradient(circle, var(--accent-glow), transparent 70%);
            pointer-events: none;
        }
        .ai-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; position: relative; }
        .ai-logo {
            font-size: 1.9rem; line-height: 1;
            filter: drop-shadow(0 0 8px var(--accent-glow));
            animation: aiPulse 2.4s ease-in-out infinite;
        }
        @keyframes aiPulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.12); } }
        .ai-title { font-weight: 800; font-size: 1.05rem; color: var(--text); letter-spacing: .5px; }
        .ai-sub { font-size: 0.75rem; color: var(--text-dim); margin-top: 2px; }
        .ai-scenarios { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; position: relative; }
        .ai-sc {
            padding: 6px 13px;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 99px;
            color: var(--text-dim);
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .ai-sc:hover { border-color: var(--accent); color: var(--text); }
        .ai-sc.active {
            background: var(--gradient-1);
            border-color: transparent;
            color: #fff;
            box-shadow: 0 3px 14px var(--accent-glow);
        }
        .ai-body { position: relative; }
        .ai-placeholder { color: var(--text-dim); font-size: 0.85rem; text-align: center; padding: 10px 0; }
        .ai-loading { color: var(--text-dim); font-size: 0.85rem; padding: 8px 0; }
        .ai-rec { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
        .ai-rec-label { font-size: 0.8rem; color: var(--text-dim); }
        .ai-rec-tool {
            padding: 5px 14px; border-radius: 99px; font-weight: 800; font-size: 0.9rem; color: #fff;
            background: var(--gradient-1);
            box-shadow: 0 3px 14px var(--accent-glow);
        }
        .ai-rec-tool.recuva { background: var(--gradient-3); }
        .ai-apply {
            margin-left: auto;
            padding: 6px 14px; border: none; border-radius: 8px; cursor: pointer;
            background: var(--gradient-2); color: #04210f; font-weight: 800; font-size: 0.8rem;
        }
        .ai-apply:hover { filter: brightness(1.08); }
        .ai-reason { font-size: 0.85rem; line-height: 1.6; color: var(--text); margin-bottom: 12px; }
        .ai-block-title { font-size: 0.82rem; font-weight: 700; color: var(--accent); margin: 10px 0 6px; }
        .ai-steps { list-style: none; counter-reset: step; padding: 0; margin: 0 0 4px; }
        .ai-steps li { display: flex; gap: 10px; align-items: flex-start; font-size: 0.83rem; line-height: 1.55; color: var(--text); margin-bottom: 7px; }
        .ai-step-num {
            flex: 0 0 20px; height: 20px; border-radius: 50%;
            background: var(--gradient-1); color: #fff; font-size: 0.72rem; font-weight: 800;
            display: flex; align-items: center; justify-content: center; margin-top: 1px;
        }
        .ai-warns { list-style: none; padding: 0; margin: 0; }
        .ai-warns li { font-size: 0.8rem; line-height: 1.5; color: var(--warning); margin-bottom: 5px; }
        .ai-out {
            margin-top: 10px; padding: 9px 12px; border-radius: 10px;
            background: var(--success-bg); color: var(--success);
            font-size: 0.8rem; font-weight: 600;
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

        <!-- 工具自动安装横幅 -->
        <div class="install-banner hidden" id="installBanner">
            <div class="ib-icon">📦</div>
            <div class="ib-body">
                <div class="ib-title" id="ibTitle">检测到恢复工具未安装</div>
                <div class="ib-msg" id="ibMsg">正在自动下载并安装 TestDisk / PhotoRec / Recuva…</div>
                <div class="ib-progress"><div class="ib-bar" id="ibBar"></div></div>
            </div>
            <div class="ib-pct" id="ibPct">0%</div>
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

        <!-- AI 智能建议 -->
        <div class="ai-panel" id="aiPanel">
            <div class="ai-head">
                <span class="ai-logo">🤖</span>
                <div class="ai-head-text">
                    <div class="ai-title">AI 智能扫描建议</div>
                    <div class="ai-sub">根据磁盘类型与场景，自动推荐恢复方案</div>
                </div>
            </div>
            <div class="ai-scenarios" id="aiScenarios">
                <button class="ai-sc active" data-s="deleted" onclick="setAiScenario('deleted')">误删文件</button>
                <button class="ai-sc" data-s="formatted" onclick="setAiScenario('formatted')">格式化/清空</button>
                <button class="ai-sc" data-s="partition" onclick="setAiScenario('partition')">分区丢失</button>
                <button class="ai-sc" data-s="inaccessible" onclick="setAiScenario('inaccessible')">无法访问</button>
            </div>
            <div class="ai-body" id="aiBody">
                <div class="ai-placeholder">请先选择上方驱动器，AI 将给出智能建议。</div>
            </div>
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
            ⚠️ 恢复工具未安装，正在自动下载安装…&nbsp;
            <button class="hint-btn" onclick="autoInstallTools()">立即安装</button>
        </div>

        <!-- 底部 -->
        <div class="footer">
            QRecover v2.0.0 · Powered by Flask · 💻 Made with ❤️
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
            loadAiAdvice();
        }

        // ── AI 智能扫描建议 ──
        let aiScenario = 'auto';
        function setAiScenario(s) {
            aiScenario = s;
            document.querySelectorAll('.ai-sc').forEach(b => b.classList.toggle('active', b.dataset.s === s));
            loadAiAdvice();
        }
        async function loadAiAdvice() {
            const body = document.getElementById('aiBody');
            if (!selectedDrive) {
                body.innerHTML = '<div class="ai-placeholder">请先选择上方驱动器，AI 将给出智能建议。</div>';
                return;
            }
            body.innerHTML = `<div class="ai-loading">🤖 AI 正在分析 ${selectedDrive}: 盘…</div>`;
            try {
                const res = await fetch(`/api/ai_advice?drive=${selectedDrive}&scenario=${aiScenario}`);
                const d = await res.json();
                if (!d.ok) {
                    body.innerHTML = `<div class="ai-placeholder">${d.message}</div>`;
                    return;
                }
                const stepsHtml = d.steps.map((s, i) =>
                    `<li><span class="ai-step-num">${i + 1}</span><span>${s}</span></li>`).join('');
                const warnsHtml = d.warnings.map(w => `<li>${w}</li>`).join('');
                body.innerHTML = `
                    <div class="ai-rec">
                        <span class="ai-rec-label">推荐工具</span>
                        <span class="ai-rec-tool ${d.recommended_tool}">${d.recommended_label}</span>
                        <button class="ai-apply" onclick="applyAiAdvice('${d.recommended_tool}')">采用并切换</button>
                    </div>
                    <div class="ai-reason">${d.reason}</div>
                    ${d.steps.length ? `<div class="ai-block-title">📋 恢复步骤</div><ol class="ai-steps">${stepsHtml}</ol>` : ''}
                    <div class="ai-block-title">⚠️ 注意事项</div>
                    <ul class="ai-warns">${warnsHtml}</ul>
                    <div class="ai-out">💡 ${d.output_tip}</div>
                `;
            } catch (e) {
                body.innerHTML = `<div class="ai-placeholder">AI 分析失败：${e.message}</div>`;
            }
        }
        function applyAiAdvice(tool) {
            switchTool(tool);
            showStatus('info', '已切换到 AI 推荐工具：' + (tool === 'recuva' ? 'Recuva' : 'TestDisk / PhotoRec'));
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

        // ══ 工具自动安装 ══
        let _installStarted = false;

        function setInstallPct(pct) {
            pct = Math.max(0, Math.min(100, pct | 0));
            const bar = document.getElementById('ibBar');
            const pctEl = document.getElementById('ibPct');
            if (bar) bar.style.width = pct + '%';
            if (pctEl) pctEl.textContent = pct + '%';
        }

        function showInstallBanner(show, msg, pct) {
            const b = document.getElementById('installBanner');
            if (!b) return;
            b.classList.toggle('hidden', !show);
            if (msg != null) {
                const m = document.getElementById('ibMsg');
                if (m) m.textContent = msg;
            }
            if (pct != null) setInstallPct(pct);
        }

        async function autoInstallTools() {
            if (_installStarted) return;
            _installStarted = true;
            showInstallBanner(true, '正在准备自动安装…', 0);
            try {
                await fetch('/api/install_tools', { method: 'POST' });
            } catch (e) { /* ignore */ }
            pollInstall();
        }

        function pollInstall() {
            const timer = setInterval(async () => {
                try {
                    const res = await fetch('/api/install_status');
                    const s = await res.json();
                    if (s.running || (!s.done && !s.error && s.progress > 0)) {
                        setInstallPct(s.progress);
                        const m = document.getElementById('ibMsg');
                        if (m && s.message) m.textContent = s.message;
                    }
                    if (s.done || s.error) {
                        clearInterval(timer);
                        if (s.error) {
                            const t = document.getElementById('ibTitle');
                            const m = document.getElementById('ibMsg');
                            if (t) t.textContent = '工具安装失败';
                            if (m) m.textContent = s.message || '安装失败，请检查网络后重试。';
                        } else {
                            setInstallPct(100);
                            const t = document.getElementById('ibTitle');
                            const m = document.getElementById('ibMsg');
                            if (t) t.textContent = '工具已安装完成';
                            if (m) m.textContent = s.message || '安装完成';
                            setTimeout(() => showInstallBanner(false), 2500);
                        }
                        _installStarted = false;
                        await checkTools();
                    }
                } catch (e) {
                    clearInterval(timer);
                    _installStarted = false;
                }
            }, 800);
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

                // 缺工具时自动触发安装
                if ((!tools.testdisk || !tools.recuva) && !_installStarted) {
                    autoInstallTools();
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
    print("Starting QRecover Web UI v2.0.0...")
    print("Open browser at: http://127.0.0.1:5000")
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        # 程序退出时释放互斥体
        ctypes.windll.kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()
