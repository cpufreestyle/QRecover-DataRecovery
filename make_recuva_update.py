#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 Recuva 无感更新所需的 zip 包与 manifest 清单。

用法:
    python make_recuva_update.py [--url <zip的公开URL>] [--out <输出目录>]

说明:
    - 自动读取 recuva_portable/recuva.exe 的真实版本号作为 manifest 的 version
      （与 qrecover.py 中 get_file_version 的比对逻辑一致）
    - 将 recuva_portable/ 整个目录打包为 recuva_update.zip
    - 生成 recuva_manifest.json:
        {"version": "1.54.0.120",
         "url": "<recuva_update.zip 的公开可下载地址>",
         "sha256": "<zip 的 SHA256>",
         "type": "zip"}
    - 把生成的文件放到 --out 目录（默认项目根目录）

部署:
    1) 把 recuva_update.zip 与 recuva_manifest.json 传到你的托管地址
       （GitHub Release / 自有服务器均可）
    2) 启动时设置环境变量 QRECOVER_RECUVAMANIFESTURL 指向 manifest 的 URL
       （本仓库的 QRecover.bat / QRecover_Desktop.bat 已内置，仅需替换其中 URL）
    3) 之后应用启动会静默比对版本，发现更高版本即从 zip 免权限解压更新
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.join(BASE, "recuva_portable")
EXE = os.path.join(PORTABLE, "recuva.exe")


def get_file_version(path):
    """与 qrecover.py 保持一致：读取 PE 数值型文件版本号"""
    if not os.path.isfile(path):
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

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD), ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD), ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD), ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        ffi_ptr = ctypes.c_void_p()
        ffi_len = wintypes.UINT(ctypes.sizeof(VS_FIXEDFILEINFO))
        if not v.VerQueryValueW(buf, "\\", ctypes.byref(ffi_ptr), ctypes.byref(ffi_len)):
            return None
        ffi = ctypes.cast(ffi_ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = ffi.dwFileVersionMS, ffi.dwFileVersionLS
        return "%d.%d.%d.%d" % ((ms >> 16) & 0xFFFF, ms & 0xFFFF,
                                 (ls >> 16) & 0xFFFF, ls & 0xFFFF)
    except Exception:
        return None


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="",
                    help="recuva_update.zip 的公开下载地址；不填则用本地相对路径")
    ap.add_argument("--out", default=BASE, help="输出目录（manifest/zip 生成位置）")
    args = ap.parse_args()

    if not os.path.isfile(EXE):
        print("[错误] 未找到 %s" % EXE)
        sys.exit(1)

    version = get_file_version(EXE)
    if not version:
        print("[警告] 无法读取 recuva.exe 版本号，manifest version 将留空")
    print("[信息] Recuva 当前版本: %s" % (version or "未知"))

    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, "recuva_update.zip")
    print("[信息] 正在打包 %s ..." % zip_path)
    # 跳过明显异常的极小文件（占位/损坏，通常 <1KB）；
    # 正常 Recuva 组件（含几十 KB 的 Lang dll）均保留。
    skipped = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(PORTABLE):
            for fn in files:
                full = os.path.join(root, fn)
                if os.path.getsize(full) < 1024:
                    skipped.append(os.path.relpath(full, PORTABLE))
                    continue
                arc = os.path.join("recuva_portable", os.path.relpath(full, PORTABLE))
                z.write(full, arc)
    if skipped:
        print("[警告] 已跳过异常小文件（<1KB）: %s" % ", ".join(skipped))

    digest = sha256_of(zip_path)
    print("[信息] 已生成 zip，SHA256: %s" % digest)

    url = args.url.strip()
    if not url:
        # 默认用本地相对路径（仅用于本地验证；正式部署请传入 --url）
        url = "file:///" + zip_path.replace("\\", "/")
    manifest = {
        "version": version or "0.0.0.0",
        "url": url,
        "sha256": digest,
        "type": "zip",
    }
    mf_path = os.path.join(args.out, "recuva_manifest.json")
    with open(mf_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("[完成] 已生成 manifest: %s" % mf_path)
    print("        部署时请把上面 zip 与 manifest 传到同一可访问地址，")
    print("        并设置 QRECOVER_RECUVAMANIFESTURL 指向 manifest 的 URL。")


if __name__ == "__main__":
    main()
