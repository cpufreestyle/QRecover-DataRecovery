#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PE 文件版本号读取与比较（qrecover 与 make_recuva_update 共用）"""
import ctypes
import os
import sys
from ctypes import wintypes

IS_WIN = sys.platform == "win32"


def get_file_version(path):
    """读取 PE 文件的版本号（如 '1.54.120.0'），失败返回 None"""
    if not IS_WIN or not os.path.isfile(path):
        return None
    try:
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
    """把 'x.y.z.w' 解析为可比较的 4 元组，异常部分按 0 处理"""
    parts = []
    for p in (v or "").split('.'):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])
