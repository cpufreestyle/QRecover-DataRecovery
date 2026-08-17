#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包前压缩 web/ 静态资源（零依赖，保守策略）。

- CSS：去注释 + 折叠空白 + 去结构符周围空格（保留 : 前空格，避免破坏后代选择器）
- JS：仅去掉整行注释、行首缩进与空行（不做激进的 token 级压缩，保证语义安全）
- index.html / 其他文件：原样复制

CLI 用法：
    python web_minify.py [src_dir] [dst_dir]     # 默认 web/ -> build/web_min/
"""
import re
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def minify_css(text: str) -> str:
    # 去 /* */ 注释
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    # 折叠空白
    text = re.sub(r'\s+', ' ', text)
    # 去掉 { } ; , 周围空格；冒号只去其后空格（: 前空格属于后代选择器语义，须保留）
    text = re.sub(r'\s*([{};,])\s*', r'\1', text)
    text = re.sub(r':\s+', ':', text)
    # 去掉块末多余分号
    text = text.replace(';}', '}')
    return text.strip()


def minify_js(text: str) -> str:
    """整行注释 / 缩进 / 空行清理（保留换行结构，避免 ASI 语义变化）"""
    out = []
    in_block = False
    for line in text.split('\n'):
        s = line.strip()
        if in_block:
            if '*/' in s:
                in_block = False
                s = s.split('*/', 1)[1].strip()
            else:
                continue
        if s.startswith('/*'):
            if '*/' in s:
                s = s.split('*/', 1)[1].strip()
            else:
                in_block = True
                continue
        if not s or s.startswith('//'):
            continue
        out.append(s)
    return '\n'.join(out)


def minify_to(src_dir, dst_dir) -> None:
    src, dst = Path(src_dir), Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        text = f.read_text(encoding='utf-8')
        if f.suffix == '.css':
            result = minify_css(text)
        elif f.suffix == '.js':
            result = minify_js(text)
        else:
            result = text  # html 等原样保留
        (dst / f.name).write_text(result, encoding='utf-8', newline='\n')
        print('[minify] %s: %d -> %d bytes (%.0f%%)'
              % (f.name, len(text.encode()), len(result.encode()),
                 100 * len(result.encode()) / max(1, len(text.encode()))))


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else str(BASE / 'web')
    dst = sys.argv[2] if len(sys.argv) > 2 else str(BASE / 'build' / 'web_min')
    minify_to(src, dst)
