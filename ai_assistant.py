#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QRecover 智能恢复助手 - AI 引擎 (支持 LLM API + 本地规则兜底)"""
import os
import sys
import json
import re
import time
import logging
import threading
import socket
import urllib.request
import urllib.error
import http.client

try:
    import ssl
except Exception:  # pragma: no cover
    ssl = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'ai_config.json')

log = logging.getLogger(__name__)

# ── 系统提示词 ──
SYSTEM_PROMPT = """你是一位资深的"数据恢复"专家助手，服务于 QRecover 数据恢复工具。
QRecover 集成了三款恢复工具：
1. TestDisk —— 分区表修复、恢复丢失分区、修复无法访问的磁盘（RAW 盘）。命令行 TUI 界面。
2. PhotoRec —— 文件级恢复（按文件签名扫描，无视文件系统），支持照片/视频/文档等数百种格式。命令行 TUI 界面。
3. Recuva —— 图形化(GUI)恢复向导，适合误删文件（回收站/Shift+Delete），操作简单。

请遵循以下原则回答用户：
- 先判断场景：是分区/磁盘打不开、误删、格式化、还是具体某类文件丢失。
- 给出明确的工具推荐与简要步骤（按工具名加粗）。
- 必须提醒关键注意事项：发现数据丢失后**立即停止使用该磁盘**，恢复的文件**务必保存到另一块磁盘/分区**，避免覆盖导致永久丢失。
- 语言简洁、专业、友好，使用中文。
- 若信息不足，主动追问一个关键问题（如：是什么盘？丢失多久了？是否被新数据覆盖过？）。"""


class AIAssistant:
    """智能恢复助手核心"""

    def __init__(self):
        self._lock = threading.Lock()
        self.config = self._load_config()

    # ── 配置 ──
    def _load_config(self):
        defaults = {
            "provider": "heuristic",      # heuristic | openai | ollama
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-3.5-turbo",
            "enabled": True,
            "temperature": 0.5,
            # ollama 的默认地址（无 api_key 也能用）
            "ollama_base_url": "http://localhost:11434/v1",
        }
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                defaults.update(saved)
        except Exception:
            pass
        return defaults

    def save_config(self, new_cfg: dict):
        # 若切换到 ollama，自动补全 base_url
        if new_cfg.get('provider') == 'ollama' and not new_cfg.get('base_url'):
            new_cfg['base_url'] = self.config.get('ollama_base_url')
        with self._lock:
            self.config.update(new_cfg)
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, indent=2, ensure_ascii=False)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    def get_config(self):
        # 不返回 api_key 明文给前端
        safe = dict(self.config)
        safe['api_key'] = '******' if safe.get('api_key') else ''
        safe['configured'] = (
            safe.get('provider') == 'heuristic'
            or bool(safe.get('api_key'))
            or safe.get('provider') == 'ollama'   # ollama 无需 key
        )
        return safe

    # ── 主入口（同步，返回结构化 dict） ──
    def chat(self, message: str, history=None, context: dict = None) -> dict:
        if not message or not message.strip():
            return {
                "text": "请描述你遇到的数据丢失情况，例如：'我不小心把U盘格式化了，里面有旅行照片'。",
                "tools": [], "drive": None, "confidence": 0.0, "warnings": [],
            }

        signals = self._detect(message, context)
        provider = self.config.get('provider', 'heuristic')
        use_llm = self._should_use_llm(provider)

        if use_llm:
            try:
                text = self._llm_chat(message, history, context, signals)
                return {
                    "text": text,
                    "tools": signals["recommend_tools"],
                    "drive": signals["drive"],
                    "confidence": signals["confidence"],
                    "warnings": signals["warnings"],
                }
            except Exception as e:
                log.warning(f"LLM 调用失败，回退本地规则: {e}")
                return {
                    "text": f"[LLM 调用失败：{self._friendly_error(e)}，已切换为本地智能分析]\n\n"
                            + self._build_reply(signals, context),
                    "tools": signals["recommend_tools"],
                    "drive": signals["drive"],
                    "confidence": signals["confidence"],
                    "warnings": signals["warnings"],
                }

        return {
            "text": self._build_reply(signals, context),
            "tools": signals["recommend_tools"],
            "drive": signals["drive"],
            "confidence": signals["confidence"],
            "warnings": signals["warnings"],
        }

    # ── 公开：仅做信号分析，返回推荐信息（供流式接口附加） ──
    def recommend(self, message: str, context: dict = None):
        s = self._detect(message, context)
        return {
            "tools": s["recommend_tools"],
            "drive": s["drive"],
            "confidence": s["confidence"],
        }

    # ── 主入口（流式，yield 文本分片） ──
    def chat_stream(self, message: str, history=None, context: dict = None):
        if not message or not message.strip():
            yield "请描述你遇到的数据丢失情况，例如：'我不小心把U盘格式化了，里面有旅行照片'。"
            return

        signals = self._detect(message, context)
        provider = self.config.get('provider', 'heuristic')
        use_llm = self._should_use_llm(provider)

        if use_llm:
            try:
                for chunk in self._llm_chat_stream(message, history, context, signals):
                    yield chunk
                return
            except Exception as e:
                log.warning(f"LLM 流式调用失败，回退本地规则: {e}")
                yield f"[LLM 调用失败：{self._friendly_error(e)}，已切换为本地智能分析]\n\n"

        yield self._build_reply(signals, context)

    # ── 是否使用 LLM ──
    def _should_use_llm(self, provider):
        if not self.config.get('enabled', True):
            return False
        if provider == 'openai':
            return bool(self.config.get('api_key'))
        if provider == 'ollama':
            return True   # ollama 本地运行，无需 key
        return False

    # ── 错误友好化 ──
    def _friendly_error(self, e):
        if isinstance(e, urllib.error.HTTPError):
            code = e.code
            if code in (401, 403):
                return "API Key 无效或无权限"
            if code == 429:
                return "请求过于频繁，已被限流"
            return f"服务端错误({code})"
        if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError)):
            return "网络超时或无法连接"
        return str(e)

    # ── 信号检测（打分 + 结构化） ──
    def _detect(self, message: str, context: dict = None):
        m = message
        msg = message.lower()

        # 提取盘符（兼容大写 D: 与裸盘符 D:，且不再崩溃）
        dm = re.search(r'([c-z])\s*盘', msg) or re.search(r'([c-z]):', msg)
        drive = dm.group(1).upper() + ':' if dm else None

        # 否定词过滤（避免“不要格式化”“不要删除”误判）
        neg = bool(re.search(r'(不要|别|无需|无需|没有|未)',
                             msg[: max(0, msg.find('格式化'))] if '格式化' in msg else
                             msg[: max(0, msg.find('删除'))] if '删除' in msg else ''))

        # 关键词集合
        def hit(keywords):
            return [k for k in keywords if k in msg]

        cat = {
            'partition': hit(['分区', '盘符丢失', '盘不见了', '打不开', 'raw', '未格式化',
                              '无法访问', '变成raw', '盘符没了', '分区表', '不显示', '盘符消失']),
            'format':    hit(['格式化', 'format', '格盘', '快速格式化', '完全格式化']),
            'delete':    hit(['误删', '删除', '删了', 'shift', '回收站', '清空回收', 'shift+delete', '丢文件']),
            'photo':     hit(['照片', '图片', '相片', 'jpg', 'png', 'raw图', '相机', '拍照', '合影']),
            'video':     hit(['视频', '录像', 'mp4', 'mov', 'avi', 'mts', '剪辑']),
            'doc':       hit(['文档', '文件', 'doc', 'pdf', 'xls', 'ppt', 'word', 'excel', '表格']),
            'usb':       hit(['u盘', 'u 盘', '优盘', 'sd卡', '内存卡', '相机卡', 'tf卡', '移动硬盘', '闪存', '存储卡']),
        }
        # 否定词抑制误判
        if neg and 'format' in ' '.join(cat['format']):
            cat['format'] = []
        if neg and 'delete' in ' '.join(cat['delete']):
            cat['delete'] = []

        bitlocker = bool(hit(['bitlocker', '加密盘', '加密分区', '锁盘', '加密了']))
        ssd = bool(hit(['固态', 'ssd', 'trim', 'nvme']))
        is_sys = ('系统盘' in msg) or ('c盘' in msg) or (drive == 'C:')

        # 打分（命中数归一）
        def score(lst):
            return min(1.0, len(lst) / 2.0)

        scores = {k: score(v) for k, v in cat.items()}
        # 盘符/介质线索提升对应场景权重
        if drive:
            scores['partition'] = max(scores['partition'], 0.3)

        # 推荐工具映射
        tool_map = {
            'partition': (['testdisk', 'photorec'], 0.95),
            'format':    (['testdisk', 'photorec'], 0.9),
            'delete':    (['recuva', 'photorec'], 0.85),
            'photo':     (['photorec'], 0.8),
            'video':     (['photorec'], 0.8),
            'doc':       (['photorec', 'recuva'], 0.75),
            'usb':       (['photorec'], 0.7),
        }

        available = set((context or {}).get('tools', ['testdisk', 'recuva']))
        # 总是认为 photorec 可用（内置）
        available.add('photorec')

        # 选出命中且与可用工具匹配的场景，按分数降序
        ranked = sorted(
            [(k, scores[k]) for k in scores if scores[k] > 0],
            key=lambda x: -x[1]
        )

        recommend_tools = []
        if ranked:
            primary = ranked[0][0]
            cand, conf = tool_map[primary]
            for t in cand:
                if t in available:
                    recommend_tools.append(t)
            # 若首选工具（如 testdisk/recuva）不可用，退到 photorec
            if not recommend_tools:
                recommend_tools = ['photorec'] if 'photorec' in available else []
            confidence = conf
        else:
            recommend_tools = ['photorec'] if 'photorec' in available else []
            confidence = 0.4

        # 额外警告
        warnings = []
        if is_sys:
            warnings.append('sys')
        if bitlocker:
            warnings.append('bitlocker')
        if ssd:
            warnings.append('ssd')

        return {
            "drive": drive,
            "cat": cat,
            "scores": scores,
            "ranked": ranked,
            "recommend_tools": recommend_tools,
            "confidence": confidence,
            "warnings": warnings,
            "bitlocker": bitlocker,
            "ssd": ssd,
            "is_sys": is_sys,
        }

    # ── 本地规则生成回复文本 ──
    def _build_reply(self, s: dict, context: dict = None) -> str:
        cat = s['cat']
        lines = ["🔍 **智能分析完成**，根据你的情况给出以下建议：\n"]

        # 分区/磁盘打不开 → TestDisk
        if cat['partition']:
            has_testdisk = 'testdisk' in (context or {}).get('tools', ['testdisk', 'recuva'])
            if has_testdisk:
                lines.append("**推荐工具：TestDisk**")
                lines.append("• 适用：分区表损坏、磁盘变成 RAW、盘符丢失、提示“未格式化”。")
                lines.append("• 步骤：选择磁盘 → Analyse → 找到丢失分区 → Write 写回分区表。")
                lines.append("• 恢复后若分区内文件仍缺失，再用 **PhotoRec** 做文件级扫描。")
            else:
                lines.append("⚠️ 检测到 TestDisk 未安装，请先在设置中配置工具路径。")

        # 格式化
        if cat['format']:
            has_testdisk = 'testdisk' in (context or {}).get('tools', ['testdisk', 'recuva'])
            if has_testdisk:
                lines.append("**推荐工具：TestDisk（优先）→ PhotoRec（兜底）**")
                lines.append("• 若是“快速格式化”，分区表通常还在，先用 TestDisk 重建/修复分区表，数据大概率可完整恢复。")
                lines.append("• 若是“完全格式化/慢格”，直接用 **PhotoRec** 按文件签名恢复。")
            else:
                lines.append("**推荐工具：PhotoRec**")
                lines.append("• 完全格式化后用 PhotoRec 按文件签名扫描恢复。")

        # 误删
        if cat['delete']:
            has_recuva = 'recuva' in (context or {}).get('tools', ['testdisk', 'recuva'])
            if has_recuva:
                lines.append("**推荐工具：Recuva（首选，图形化更友好）**")
                lines.append("• 适用：误删文件、清空回收站、Shift+Delete 删除。")
                lines.append("• 步骤：选择文件类型 → 扫描位置 → 深度扫描 → 勾选恢复（保存到其它盘）。")
                lines.append("• 若 Recuva 找不到，再用 **PhotoRec** 兜底扫描。")
            else:
                lines.append("**推荐工具：PhotoRec**")
                lines.append("• Recuva 未安装，可直接用 PhotoRec 扫描恢复删除文件。")

        # 具体文件类型
        if cat['photo'] or cat['video'] or cat['doc']:
            if cat['photo']:
                cat_name, fmt = '照片/图片', '（jpg/png/raw/nef/cr2 等）'
            elif cat['video']:
                cat_name, fmt = '视频', '（mp4/mov/avi/mts 等）'
            else:
                cat_name, fmt = '文档', '（doc/pdf/xls/ppt 等）'
            lines.append(f"**推荐工具：PhotoRec** —— 针对{cat_name}{fmt}恢复效果最佳")
            lines.append("• 照片/视频类文件有固定签名，PhotoRec 即使文件系统损坏也能找回。")
            lines.append("• 步骤：选择磁盘 → 选择分区（或整个磁盘）→ 选文件类型 → 设置输出目录（其它盘）。")

        # U盘/存储卡
        if cat['usb'] and not (cat['partition'] or cat['format'] or cat['delete']
                               or cat['photo'] or cat['video'] or cat['doc']):
            lines.append("**推荐工具：PhotoRec**")
            lines.append("• U盘/SD卡/相机卡常因直接拔插导致文件系统损坏，PhotoRec 不依赖文件系统，恢复成功率高。")

        # 兜底
        if len(lines) <= 2:
            lines.append("**推荐工具：PhotoRec（通用兜底）**")
            lines.append("• 不确定场景时，PhotoRec 可无视文件系统、按文件签名扫描，兼容性最强。")
            lines.append("• 若是分区/磁盘整体打不开，优先用 **TestDisk** 修复分区表。")

        # 专项警告
        if s['bitlocker']:
            lines.append("\n🔐 **加密盘提示**：BitLocker/加密分区在解密状态下恢复才有效；若已锁盘，请先解锁再恢复，否则 PhotoRec 只能找回密文。")
        if s['ssd']:
            lines.append("\n💡 **固态硬盘(SSD/TRIM)提示**：SSD 启用了 TRIM 后，删除的文件可能被主控直接清空，恢复成功率显著低于机械硬盘，建议尽快尝试并降低预期。")

        # 系统盘特别警告
        if s['is_sys']:
            lines.append("\n⚠️ **系统盘(C盘)警告**：恢复系统盘文件风险较高，请勿将 recovered 文件保存到 C 盘，并尽量在 PE 环境或挂为从盘操作。")

        # 通用注意事项
        lines.append("\n📌 **关键注意事项**")
        lines.append("1. 立即停止对该磁盘的写入操作，避免新数据覆盖旧文件。")
        lines.append("2. 恢复的文件务必保存到**另一块磁盘/分区**。")
        drive_tip = f"3. 当前选择的目标盘：{s['drive']}" if s['drive'] else "3. 在上方选择具体磁盘后点击「恢复文件」。"
        lines.append(drive_tip)

        return "\n".join(lines)

    # ── LLM 调用（OpenAI 兼容，带重试） ──
    def _build_messages(self, message, history, context, signals):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context or signals:
            ctx_lines = ["[当前环境上下文]"]
            tools = (context or {}).get('tools', [])
            if tools:
                ctx_lines.append(f"可用工具: {', '.join(tools)}（photorec 始终内置可用）")
            drives = (context or {}).get('drives', [])
            if drives:
                ctx_lines.append(f"检测到磁盘: {', '.join(drives)}")
            if signals and signals.get('drive'):
                ctx_lines.append(f"用户提及的盘符: {signals['drive']}")
            if ctx_lines:
                messages.append({"role": "system", "content": "\n".join(ctx_lines)})
        if history:
            for h in history[-8:]:
                if h.get('role') in ('user', 'assistant'):
                    messages.append({"role": h['role'], "content": h['content']})
        messages.append({"role": "user", "content": message})
        return messages

    def _request_headers(self):
        headers = {"Content-Type": "application/json"}
        # ollama 无需鉴权头；openai 必须带 Bearer
        if self.config.get('api_key'):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        return headers

    def _llm_endpoint(self):
        provider = self.config.get('provider')
        if provider == 'ollama':
            base = self.config.get('ollama_base_url', 'http://localhost:11434/v1').rstrip('/')
        else:
            base = self.config.get('base_url', 'https://api.openai.com/v1').rstrip('/')
        return f"{base}/chat/completions"

    def _do_request(self, payload, stream=False):
        url = self._llm_endpoint()
        data = json.dumps(payload).encode('utf-8')
        last_err = None
        # 本地推理服务（LM Studio / Ollama，通常跑在 localhost）必须绕过系统 HTTP 代理，
        # 否则 urllib 会把请求转给代理（如 Clash），代理无法回环访问而返回 502 Bad Gateway。
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or '').lower()
        use_proxy_bypass = host in ('localhost', '127.0.0.1', '::1')
        for attempt in range(3):   # 重试 2 次
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers=self._request_headers(),
                    method='POST'
                )
                if use_proxy_bypass:
                    resp = urllib.request.build_opener(
                        urllib.request.ProxyHandler({})
                    ).open(req, timeout=30)
                else:
                    resp = urllib.request.urlopen(req, timeout=30)
                return resp
            except Exception as e:
                last_err = e
                log.warning(f"LLM 请求第 {attempt+1} 次失败: {e}")
                if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                    break  # 鉴权/地址错误不必重试
                time.sleep(1.0 * (attempt + 1))
        raise last_err if last_err else RuntimeError("未知错误")

    def _llm_chat(self, message, history, context, signals):
        payload = {
            "model": self.config.get('model', 'gpt-3.5-turbo'),
            "messages": self._build_messages(message, history, context, signals),
            "temperature": self.config.get('temperature', 0.5),
            "stream": False,
        }
        resp = self._do_request(payload, stream=False)
        result = json.loads(resp.read().decode('utf-8'))
        return result['choices'][0]['message']['content'].strip()

    def _llm_chat_stream(self, message, history, context, signals):
        payload = {
            "model": self.config.get('model', 'gpt-3.5-turbo'),
            "messages": self._build_messages(message, history, context, signals),
            "temperature": self.config.get('temperature', 0.5),
            "stream": True,
        }
        resp = self._do_request(payload, stream=True)
        # 逐行读取 SSE
        for raw in resp:
            line = raw.decode('utf-8').strip()
            if not line:
                continue
            if line.startswith('data:'):
                data = line[len('data:'):].strip()
                if data == '[DONE]':
                    break
                try:
                    obj = json.loads(data)
                    delta = obj['choices'][0]['delta'].get('content', '')
                    if delta:
                        yield delta
                except Exception:
                    continue


# 单例
assistant = AIAssistant()
