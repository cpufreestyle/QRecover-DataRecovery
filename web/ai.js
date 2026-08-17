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
            apiFetch('/api/ai/config', {}, { silent: true }).then(r => r.json()).then(cfg => {
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
            apiFetch('/api/ai/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(r => r.json()).then(res => {
                if (res.ok) {
                    showToast('✅ AI 设置已保存', 'success');
                    document.getElementById('aiConfig').classList.remove('show');
                } else {
                    showToast('保存失败: ' + (res.error || '未知错误'), 'error');
                }
            }).catch(e => showToast('保存失败: ' + e.message, 'error'));
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
            apiFetch(url, {}, { silent: true }).then(r => r.json()).then(res => {
                showToast(res.status === 'ok' ? res.message : ('启动失败：' + (res.message || '')),
                          res.status === 'ok' ? 'success' : 'error');
            }).catch(e => showToast('启动失败：' + e.message, 'error'));
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
            apiFetch('/api/ai/chat/stream', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: body
            }, { silent: true }).then(r => {
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
