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
        let _lastDrivesLoad = 0;
        async function loadDrives() {
            _lastDrivesLoad = Date.now();
            const list = document.getElementById('driveList');
            const prevSelected = selectedDrive;
            const maxRetry = 3;
            for (let attempt = 1; attempt <= maxRetry; attempt++) {
                try {
                    const res = await apiFetch('/api/drives', {}, { silent: true });
                    const drives = await res.json();
                    list.innerHTML = '';

                    if (drives.length === 0) {
                        list.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-dim);padding:24px;">未检测到可用驱动器</div>';
                        return;
                    }

                    // DocumentFragment 批量构建，一次性插入（避免逐卡片触发布局）
                    const frag = document.createDocumentFragment();
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
                        frag.appendChild(card);
                    });
                    list.appendChild(frag);
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

        // ── 轻量 Toast（替代阻塞式 alert） ──
        let _toastTimer = null;
        function showToast(msg, type) {
            let el = document.getElementById('qrToast');
            if (!el) {
                el = document.createElement('div');
                el.id = 'qrToast';
                document.body.appendChild(el);
            }
            el.className = 'qr-toast ' + (type || '');
            el.textContent = msg;
            requestAnimationFrame(() => el.classList.add('show'));
            clearTimeout(_toastTimer);
            _toastTimer = setTimeout(() => el.classList.remove('show'), 4000);
        }

        // ── 统一 API 请求：网络异常 Toast；非 2xx 抛出携带服务端错误信息的 Error ──
        async function apiFetch(url, opts = {}, { silent = false } = {}) {
            let res;
            try {
                res = await fetch(url, opts);
            } catch (e) {
                if (!silent) showToast('网络请求失败：' + e.message, 'error');
                throw e;
            }
            if (!res.ok) {
                let msg = 'HTTP ' + res.status;
                try {
                    const j = await res.clone().json();
                    if (j && j.message) msg = j.message;
                } catch (_) {}
                const err = new Error(msg);
                err.status = res.status;
                throw err;
            }
            return res;
        }

        // 显示状态消息
        let _statusAutoHide = null;
        function showStatus(type, msg) {
            const el = document.getElementById('status');
            const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
            el.className = `status show ${type}`;
            el.innerHTML = `<span class="status-icon">${icons[type] || '📋'}</span><span>${msg}</span>`;
            // 成功/提示类消息自动淡出，错误/警告保留等待用户处理
            clearTimeout(_statusAutoHide);
            if (type === 'success' || type === 'info') {
                _statusAutoHide = setTimeout(hideStatus, 8000);
            }
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
                const res = await apiFetch(`/api/scan?drive=${selectedDrive}&tool=${currentTool}`);
                const data = await res.json();
                if (data.status === 'ok') {
                    showStatus('success', data.message);
                } else {
                    showStatus('error', data.message);
                }
            } catch (e) {
                showStatus('error', e.message);
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
                const res = await apiFetch(`/api/recover?drive=${selectedDrive}&tool=${currentTool}`);
                const data = await res.json();
                if (data.status === 'ok') {
                    showStatus('success', data.message);
                } else {
                    showStatus('error', data.message);
                }
            } catch (e) {
                showStatus('error', e.message);
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



        async function installTestDisk() {
            const btn = document.getElementById('btnInstallTestDisk');
            const statusEl = document.getElementById('setupStatusTestDisk');
            if (btn) btn.disabled = true;
            if (statusEl) { statusEl.className = 'setup-status working'; statusEl.textContent = '⏳ 正在下载并解压…'; }
            showSetupStatus('info', '正在下载 TestDisk / PhotoRec（约 20MB），请稍候…');
            try {
                const res = await apiFetch('/api/testdisk/ensure', {
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
                const res = await apiFetch('/api/recuva/install', { method: 'POST' });
                const data = await res.json();
                if (data.installed) {
                    setSetupCard('recuva', 'ready', '✅ 已就绪', true);
                    showSetupStatus('success', data.message || 'Recuva 安装完成！');
                } else if (data.action === 'open_url') {
                    // 桌面版优先走 WebView 桥接（window.open 在部分 WebView 中无效）
                    if (window.QRecoverDesktop && window.QRecoverDesktop.openExternal) {
                        window.QRecoverDesktop.openExternal(data.url);
                    } else {
                        window.open(data.url, '_blank');
                    }
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

        // 检查工具状态 + 刷新安装向导（合并原 refreshSetup）
        async function checkTools() {
            try {
                const res = await apiFetch('/api/tools', {}, { silent: true });
                const tools = await res.json();

                // 工具徽章
                const recuvaBtn = document.getElementById('btnRecuva');
                const recuvaBadge = document.getElementById('badgeRecuva');
                const hint = document.getElementById('recuvaHint');
                if (!tools.recuva) {
                    recuvaBtn.classList.add('disabled');
                    recuvaBadge.className = 'tool-badge badge-unavailable';
                    recuvaBadge.textContent = '未安装';
                    hint.classList.add('show');
                    if (currentTool === 'recuva') switchTool('testdisk');
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

                // 安装向导面板：仅当任一恢复引擎缺失时显示
                const panel = document.getElementById('setupPanel');
                if (panel) {
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
                }
            } catch (e) {
                console.warn('工具检测失败:', e);
            }
        }

        // 初始化
        async function init() {
            await initTheme();
            await checkTools();
            await loadDrives();
            // 初始检查一次进程状态
            try {
                const res = await apiFetch('/api/status', {}, { silent: true });
                const data = await res.json();
                if (data.status === 'busy') {
                    isProcessing = true;
                    updateButtons();
                    startStatusPolling();
                }
            } catch (e) {
                console.warn('初始状态检查失败:', e);
            }
        }

        init();
        // 定时刷新驱动器：页面不可见时跳过轮询，重新可见且距上次刷新较久时补一次
        setInterval(() => {
            if (document.hidden) return;
            loadDrives();
        }, 60000);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && Date.now() - _lastDrivesLoad > 30000) {
                loadDrives();
            }
        });


    
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
