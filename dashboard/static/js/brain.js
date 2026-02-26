/**
 * LEON — JARVIS Neural Interface  v20
 * Radial hub navigation. Canvas stars. No WebGL.
 * Performance: all rAF loops pause when tab is hidden, capped at 30fps.
 */

// ═══════════════════════════════════════════════════════
// PERFORMANCE: pause all animations when tab not visible
// ═══════════════════════════════════════════════════════
let _tabVisible = !document.hidden;
document.addEventListener('visibilitychange', () => { _tabVisible = !document.hidden; });

// Throttled rAF — runs callback at max targetFps, skips entirely when tab hidden
function rafThrottle(fn, targetFps) {
    const interval = 1000 / targetFps;
    let last = 0;
    function loop(ts) {
        if (_tabVisible && ts - last >= interval) {
            last = ts;
            fn(ts);
        }
        requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
}

// ═══════════════════════════════════════════════════════
// STARS BACKGROUND
// ═══════════════════════════════════════════════════════
(function initStars() {
    const canvas = document.getElementById('stars-bg');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let stars = [];

    function resize() {
        canvas.width  = window.innerWidth;
        canvas.height = window.innerHeight;
        buildStars();
    }
    function buildStars() {
        stars = [];
        // Fewer stars on small/slow screens
        const n = Math.floor((canvas.width * canvas.height) / 5000);
        for (let i = 0; i < n; i++) {
            stars.push({
                x: Math.random() * canvas.width,
                y: Math.random() * canvas.height,
                r: Math.random() * 1.1 + 0.1,
                base: Math.random() * 0.55 + 0.08,
                phase: Math.random() * Math.PI * 2,
                speed: Math.random() * 0.018 + 0.004,
            });
        }
    }
    function draw(ts) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const vg = ctx.createRadialGradient(canvas.width*.5, canvas.height*.5, 0, canvas.width*.5, canvas.height*.5, canvas.width*.65);
        vg.addColorStop(0, 'rgba(0,14,32,0.22)');
        vg.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = vg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        const t = ts / 1000;
        for (const s of stars) {
            const a = s.base * (0.45 + 0.55 * Math.sin(s.phase + t * s.speed * 6));
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(170,225,255,${a})`;
            ctx.fill();
        }
    }
    window.addEventListener('resize', resize);
    resize();
    rafThrottle(draw, 30);  // 30fps max, pauses when tab hidden
})();


// ═══════════════════════════════════════════════════════
// HUB SVG ANIMATION
// ═══════════════════════════════════════════════════════
(function initHub() {
    // Build tick marks
    const tg = document.getElementById('hub-ticks');
    if (tg) {
        const ns = 'http://www.w3.org/2000/svg';
        const total = 72;
        for (let i = 0; i < total; i++) {
            const ang = (i / total) * Math.PI * 2 - Math.PI / 2;
            const major = i % 6 === 0;
            const r1 = major ? 179 : 182;
            const r2 = 188;
            const cos = Math.cos(ang), sin = Math.sin(ang);
            const ln = document.createElementNS(ns, 'line');
            ln.setAttribute('x1', (250 + cos * r1).toFixed(2));
            ln.setAttribute('y1', (250 + sin * r1).toFixed(2));
            ln.setAttribute('x2', (250 + cos * r2).toFixed(2));
            ln.setAttribute('y2', (250 + sin * r2).toFixed(2));
            ln.setAttribute('stroke', '#00d4ff');
            ln.setAttribute('stroke-width', major ? '1.6' : '0.6');
            tg.appendChild(ln);
        }
    }

    // Rotating outer accent ring — 20fps, pauses when hidden
    const spin = document.getElementById('hub-spin');
    let angle = 0;
    rafThrottle(function() {
        angle = (angle + 0.18) % 360;
        if (spin) spin.setAttribute('transform', `rotate(${angle.toFixed(1)} 250 250)`);
    }, 20);

    // Pulsing halo — 20fps, pauses when hidden
    const halo = document.getElementById('hub-halo');
    let haloT = 0;
    rafThrottle(function(ts) {
        haloT = ts / 1000;
        if (halo) {
            halo.setAttribute('r', (88 + 10 * Math.sin(haloT)).toFixed(1));
            halo.setAttribute('opacity', (0.06 + 0.06 * Math.sin(haloT)).toFixed(3));
        }
    }, 20);
})();


// ═══════════════════════════════════════════════════════
// HUB PANEL NAVIGATION
// ═══════════════════════════════════════════════════════

let activePanel = null;

// Panel → arc → hub hint text
const PANEL_META = {
    system:  { arc: 'arc-system', hint: 'SYSTEM',  color: '#00d4ff' },
    agents:  { arc: 'arc-agents', hint: 'AGENTS',  color: '#00d4ff' },
    feed:    { arc: 'arc-feed',   hint: 'FEED',    color: '#00ffaa' },
    projects: { arc: 'arc-status', hint: 'PROJECTS', color: '#8888ff' },
};

function openPanel(name) { sfx('open');
    const panel = document.getElementById(`panel-${name}`);
    const meta  = PANEL_META[name];
    if (!panel) return;

    panel.classList.add('open');
    if (meta) {
        const arc = document.getElementById(meta.arc);
        if (arc) { arc.style.opacity = '1'; arc.setAttribute('filter', 'url(#fx-nav)'); }
        setHubHint(meta.hint, meta.color);
    }
    activePanel = name;
    try { localStorage.setItem('leon_last_panel', name); } catch {}
    if (name === 'feed') { feedUnread = 0; document.title = 'LEON — Neural Interface'; }
    if (name === 'projects') { fetchProjects(); }
}

function closePanel(name) { sfx('close');
    const panel = document.getElementById(`panel-${name}`);
    const meta  = PANEL_META[name];
    if (!panel) return;

    panel.classList.remove('open');
    if (meta) {
        const arc = document.getElementById(meta.arc);
        if (arc) { arc.style.opacity = '0.38'; arc.removeAttribute('filter'); }
    }
    if (activePanel === name) {
        activePanel = null;
        setHubHint(null);
    }
}

function togglePanel(name) {
    if (activePanel === name) {
        closePanel(name);
    } else {
        if (activePanel) closePanel(activePanel);
        openPanel(name);
    }
}

// Update hub center text to reflect hovered/open panel
function setHubHint(label, color) {
    const hs = document.getElementById('hub-status');
    if (!hs) return;
    if (!label) {
        // Restore to default (set by updateUI)
        const cnt = brainState.agentCount || 0;
        hs.setAttribute('fill', cnt > 0 ? '#ff7700' : '#00ffaa');
        hs.textContent = cnt > 0 ? `${cnt} AGENT${cnt > 1 ? 'S' : ''} ACTIVE` : 'ONLINE';
    } else {
        hs.setAttribute('fill', color || '#00d4ff');
        hs.textContent = `[ ${label} ]`;
    }
}

// Wire up nav arc hit targets
function initNavArcs() {
    // Hit target → panel toggle
    const hitMap = {
        'hit-agents': 'agents',
        'hit-feed':   'feed',
        'hit-system': 'system',
        'hit-status': 'projects',
    };
    for (const [hitId, panelName] of Object.entries(hitMap)) {
        const el = document.getElementById(hitId);
        if (!el) continue;
        const arcId = PANEL_META[panelName]?.arc;
        el.addEventListener('click', () => togglePanel(panelName));
        el.addEventListener('mouseenter', () => {
            const arc = document.getElementById(arcId);
            if (arc && activePanel !== panelName) {
                arc.style.opacity = '0.8';
                arc.setAttribute('filter', 'url(#fx-nav)');
            }
            setHubHint(PANEL_META[panelName]?.hint, PANEL_META[panelName]?.color);
        });
        el.addEventListener('mouseleave', () => {
            const arc = document.getElementById(arcId);
            if (arc && activePanel !== panelName) {
                arc.style.opacity = '0.38';
                arc.removeAttribute('filter');
            }
            if (activePanel !== panelName) setHubHint(activePanel ? PANEL_META[activePanel]?.hint : null);
        });
    }

    // MIC arc hit target
    const hitMic = document.getElementById('hit-mic');
    if (hitMic) {
        hitMic.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleMic();
        });
        hitMic.addEventListener('mouseenter', () => {
            setHubHint(micMuted ? 'UNMUTE MIC' : 'MUTE MIC', micMuted ? '#ff2244' : '#ff8800');
        });
        hitMic.addEventListener('mouseleave', () => {
            if (activePanel) setHubHint(PANEL_META[activePanel]?.hint, PANEL_META[activePanel]?.color);
            else setHubHint(null);
            // Restore arc filter to match current mic state
            const arc = document.getElementById('hub-arc');
            if (arc && micMuted) arc.setAttribute('filter', 'url(#fx-orange)');
            else if (arc) arc.removeAttribute('filter');
        });
    }
}

// ── Projects panel ─────────────────────────────────────
async function fetchProjects() {
    const list = document.getElementById('projects-list');
    if (!list) return;
    list.innerHTML = '<div class="iv sm" style="opacity:0.4">Loading…</div>';
    try {
        const res = await fetch('/api/projects');
        const projects = await res.json();
        if (!projects.length) {
            list.innerHTML = '<div class="iv sm" style="opacity:0.4">No projects configured</div>';
            return;
        }
        list.innerHTML = projects.map(p => `
            <div class="proj-item" onclick="openProject('${p.path.replace(/'/g, "\\'")}')">
                <div class="proj-name">${p.name}</div>
                <div class="proj-meta">${(p.tech_stack || []).join(' · ') || p.type || ''}</div>
            </div>
        `).join('');
    } catch (e) {
        list.innerHTML = '<div class="iv sm" style="color:#ff3355">Failed to load</div>';
    }
}

async function openProject(path) {
    try {
        await fetch('/api/projects/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
    } catch {}
}

// MIC toggle — sends voice command to Leon via WebSocket
let micMuted = true;
let micToggleLock = false;
function toggleMic() {
    if (micToggleLock) return;
    micToggleLock = true;
    setTimeout(() => { micToggleLock = false; }, 400);
    micMuted = !micMuted;
    sfx(micMuted ? 'mic-off' : 'mic-on');
    updateMicArc(micMuted ? 'muted' : 'active');
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        wsConnection.send(JSON.stringify({ command: micMuted ? 'voice_mute' : 'voice_unmute' }));
    }
    feed(now(), micMuted ? '> Microphone muted' : '> Microphone activated', 'feed-command');
    if (!activePanel) openPanel('feed');
}

// MIC arc visual states — clear colour coding + hub glow + text indicator
function updateMicArc(state) {
    const arc   = document.getElementById('hub-arc');
    const label = document.getElementById('hub-mic-state');
    const hub   = document.getElementById('hub');
    if (!arc) return;

    const C        = 2 * Math.PI * 225; // 1414
    const fullSpan = 236;               // 60° at r=225

    const STATES = {
        // RED  = muted / off
        muted:      { color: '#ff2244', span: fullSpan * 0.40, text: '◉  MUTED',     labelColor: '#ff2244', active: false, hubClass: 'mic-muted'    },
        // ORANGE = active / listening
        active:     { color: '#ff8800', span: fullSpan,         text: '◎  ACTIVE',    labelColor: '#ff8800', active: true,  hubClass: 'mic-active'   },
        listening:  { color: '#ff9900', span: fullSpan,         text: '◎  LISTENING', labelColor: '#ff9900', active: true,  hubClass: 'mic-active'   },
        awake:      { color: '#ffaa00', span: fullSpan,         text: '◎  AWAKE',     labelColor: '#ffaa00', active: true,  hubClass: 'mic-active'   },
        processing: { color: '#ffcc00', span: fullSpan * 0.65,  text: '◈  THINKING',  labelColor: '#ffcc00', active: true,  hubClass: 'mic-active'   },
        speaking:   { color: '#ff7700', span: fullSpan,         text: '◎  SPEAKING',  labelColor: '#ff7700', active: true,  hubClass: 'mic-active'   },
    };

    // Unknown states (e.g. "idle" from voice system) default to active, not muted
    const s = STATES[state] || (state === 'muted' ? STATES.muted : STATES.active);

    // Arc color + length
    arc.setAttribute('stroke', s.color);
    arc.setAttribute('stroke-dasharray', `${s.span.toFixed(1)} ${(C - s.span).toFixed(1)}`);

    // Also update the orange filter on the arc — remove for non-orange states
    if (state === 'muted') arc.setAttribute('filter', 'url(#fx-orange)');
    else arc.removeAttribute('filter');

    // Mic state text inside hub
    if (label) {
        label.textContent = s.text;
        label.setAttribute('fill', s.labelColor);
        label.setAttribute('opacity', '0.85');
    }

    // Hub glow: green when active, default cyan when muted
    if (hub) {
        hub.classList.toggle('mic-active', s.active);
        hub.classList.toggle('mic-muted',  !s.active);
    }

    // Top-bar voice chip
    const vc = document.getElementById('voice-state');
    if (vc) {
        vc.textContent = s.text.trim();
        vc.classList.toggle('active', s.active);
    }
}

// Auto-open feed panel when Leon sends a response
function autoOpenFeed() {
    if (activePanel !== 'feed') {
        if (activePanel) closePanel(activePanel);
        openPanel('feed');
    }
}


// ═══════════════════════════════════════════════════════
// DASHBOARD CONTROLLER
// ═══════════════════════════════════════════════════════

let brainState = {
    leftActive: true, rightActive: false,
    activeAgents: [], agentCount: 0,
    taskCount: 0, completedCount: 0, queuedCount: 0,
    uptime: 0, voice: {},
};

let wsConnection     = null;
let wsAuthenticated  = false;
let wsReconnectDelay = 1000;
let wsReconnectTimer = null;
let demoInterval     = null;
let loadingTimer     = null;
let demoCompleted    = 0;
let demoUptimeStart  = Date.now();
let commandHistory   = JSON.parse(localStorage.getItem('leon_cmd_history') || '[]');
let historyIndex     = -1;
let feedAutoScroll      = true;
let feedUnread          = 0;
let thinkingTimer       = null;
let wsCountdownTimer    = null;
const alertCooldowns    = {};

// ── API usage tracker ──────────────────────────────────
// claude-sonnet-4 pricing: ~$3/1M input, ~$15/1M output (rough estimate)
const apiTracker = {
    messages: 0,
    inputTokens: 0,
    outputTokens: 0,
    // Rough estimate: avg 200 input + 400 output per exchange
    addExchange(inputEst = 200, outputEst = 400) {
        this.messages++;
        this.inputTokens  += inputEst;
        this.outputTokens += outputEst;
        this.render();
    },
    cost() {
        return (this.inputTokens / 1e6 * 3) + (this.outputTokens / 1e6 * 15);
    },
    totalTokens() { return this.inputTokens + this.outputTokens; },
    render() {
        const api = document.getElementById('stat-api');
        const tok = document.getElementById('stat-tokens');
        if (api) api.textContent = `$${this.cost().toFixed(2)}`;
        if (tok) {
            const t = this.totalTokens();
            tok.textContent = t > 999 ? `${(t/1000).toFixed(1)}k` : `${t}`;
        }
    },
};

// ── Token storage ──────────────────────────────────────
const IS_LOCAL = (location.hostname === 'localhost' || location.hostname === '127.0.0.1');
function getToken()  { return IS_LOCAL ? '__local__' : (localStorage.getItem('leon_session_token') || ''); }
function setToken(t) { if (!IS_LOCAL) localStorage.setItem('leon_session_token', t); }

// ── Auth overlay ───────────────────────────────────────
function showAuth(msg) {
    const o = document.getElementById('auth-overlay'); if (!o) return;
    o.style.display = 'flex';
    const err = document.getElementById('auth-error'); if (err) err.textContent = msg || '';
    const inp = document.getElementById('auth-token-input');
    const btn = document.getElementById('auth-submit');
    if (!inp || !btn) return;
    setTimeout(() => inp.focus(), 100);
    function doAuth() {
        const tk = inp.value.trim(); if (!tk) return;
        setToken(tk); o.style.display = 'none';
        wsConnection ? wsConnection.close() : connectWS();
    }
    btn.onclick = doAuth;
    inp.onkeydown = (e) => { if (e.key === 'Enter') doAuth(); };
}
function hideAuth() {
    const o = document.getElementById('auth-overlay'); if (o) o.style.display = 'none';
}

// ── Connection status ──────────────────────────────────
function setStatus(s) {
    const dot = document.getElementById('ws-dot');
    const txt = document.getElementById('ws-status-text');
    const ind = document.getElementById('ws-indicator');
    if (!dot || !txt) return;
    dot.className = 'ws-dot';
    if (ind) ind.className = 'ws-ind';

    if (s === 'connected') {
        txt.textContent = 'ONLINE';
    } else if (s === 'disconnected') {
        dot.classList.add('disconnected'); txt.textContent = 'OFFLINE';
        if (ind) ind.classList.add('disconnected');
    } else if (s === 'reconnecting') {
        dot.classList.add('reconnecting'); txt.textContent = 'RECONNECTING';
        if (ind) ind.classList.add('reconnecting');
    } else if (s === 'demo') {
        dot.style.background = '#ffaa00'; dot.style.boxShadow = '0 0 8px #ffaa00';
        txt.textContent = 'DEMO';
    }
}
function setLoading(on) {
    const l = document.getElementById('command-loading'); if (l) l.classList.toggle('active', on);
    const hs = document.getElementById('hub-status');
    if (on) {
        let dots = 0;
        thinkingTimer = setInterval(() => {
            if (hs && !activePanel) {
                dots = (dots + 1) % 4;
                hs.textContent = 'THINKING' + '.'.repeat(dots);
                hs.setAttribute('fill', '#ffcc00');
            }
        }, 380);
    } else {
        if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
        if (hs && !activePanel) setHubHint(null);
    }
}

// ── WebSocket ──────────────────────────────────────────
let _wsRetryCount = 0;
const _WS_MAX_RETRIES = 50;

function connectWS() {
    const token = getToken();
    if (!token) { showAuth(''); setStatus('demo'); startDemo(); return; }

    if (_wsRetryCount >= _WS_MAX_RETRIES) {
        setStatus('disconnected');
        feed(now(), 'Connection lost — too many retries. Reload page to reconnect.', 'feed-agent-fail');
        return;
    }

    setStatus('reconnecting');
    try {
        const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${wsProto}//${location.host}/ws`);
        wsConnection = ws; wsAuthenticated = false;

        ws.onopen = () => {
            if (wsCountdownTimer) { clearInterval(wsCountdownTimer); wsCountdownTimer = null; }
            wsReconnectDelay = 1000;
            _wsRetryCount = 0;
            ws.send(JSON.stringify({ command: 'auth', token }));
        };
        ws.onmessage = (ev) => {
            let d; try { d = JSON.parse(ev.data); } catch { return; }
            if (d.type === 'auth_result') {
                if (d.success) {
                    wsAuthenticated = true; hideAuth(); setStatus('connected');
                    if (demoInterval) { clearInterval(demoInterval); demoInterval = null; }
                    // Sync initial mute state — UI starts muted, tell server
                    ws.send(JSON.stringify({ command: micMuted ? 'voice_mute' : 'voice_unmute' }));
                    feed(now(), `Connected to ${brainState.aiName || 'AI'}`, 'feed-system');
                } else {
                    wsAuthenticated = false;
                    localStorage.removeItem('leon_session_token');
                    setStatus('disconnected'); showAuth(d.message || 'Authentication failed');
                }
                return;
            }
            if (d.type === 'input_response') {
                if (loadingTimer) { clearTimeout(loadingTimer); loadingTimer = null; }
                setLoading(false);
                feed(d.timestamp || now(), `${brainState.aiName || 'AI'}: ${d.message}`, 'feed-response');
                autoOpenFeed();
                // Track API usage (use server-provided token count if available, else estimate)
                apiTracker.addExchange(d.input_tokens || 200, d.output_tokens || Math.ceil((d.message||'').length / 4));
                // Browser notification when tab is in background
                if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
                    new Notification(brainState.aiName || 'AI', { body: (d.message || '').substring(0, 120) });
                }
                return;
            }
            if (d.type === 'agent_completed') {
                feed(now(), `Agent #${(d.agent_id||'').slice(-8)} done: ${d.summary||''}`, 'feed-agent-ok');
                // Notify on agent completion too
                if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
                    new Notification(`${brainState.aiName || 'AI'} — Agent Done`, { body: (d.summary || 'Task completed').substring(0, 100) });
                }
                return;
            }
            if (d.type === 'agent_failed') {
                feed(now(), `Agent #${(d.agent_id||'').slice(-8)} failed: ${d.error||''}`, 'feed-agent-fail');
                return;
            }
            if (d.type === 'pong') {
                if (_pingTs) {
                    const ms = Date.now() - _pingTs; _pingTs = 0;
                    const txt = document.getElementById('ws-status-text');
                    if (txt) { txt.textContent = `${ms}ms`; setTimeout(() => { if (txt.textContent.endsWith('ms')) txt.textContent = 'ONLINE'; }, 2500); }
                }
                return;
            }
            if (d.type === 'vad_event') {
                handleVadEvent(d.event, d.text);
                return;
            }
            if (d.type === 'settings' || d.type === 'settings_updated') {
                applySettings(d);
                return;
            }
            if (d.type === 'voice_wake_result') {
                const p = document.getElementById('ptt-btn');
                if (d.success) {
                    if (p) { p.classList.add('ptt-active'); setTimeout(() => p.classList.remove('ptt-active'), 2000); }
                } else {
                    feed(now(), `Leon: ${d.message || 'Voice not active'}`, 'feed-response');
                }
                return;
            }
            if (d.type === 'plan_update') {
                feed(d.timestamp || now(), `PLAN: ${d.message || ''}`, 'feed-agent-ok');
                return;
            }
            if (d.type === 'plan_created') {
                if (d.status) brainState = { ...brainState, planMode: d.status };
                feed(d.timestamp || now(), `PLAN: Generated — ${(d.status || {}).totalTasks || '?'} tasks across ${(d.plan || {phases:[]}).phases.length} phases`, 'feed-agent-ok');
                updateUI();
                return;
            }
            brainState = { ...brainState, ...d };
            updateUI();
        };
        // Ping loop — measure round-trip latency every 10s
        let _pingTs = 0;
        const _pingInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                _pingTs = Date.now();
                ws.send(JSON.stringify({ command: 'ping' }));
            } else { clearInterval(_pingInterval); }
        }, 10000);
        ws.onclose = () => { clearInterval(_pingInterval);
            wsConnection = null; wsAuthenticated = false; setStatus('reconnecting');
            _wsRetryCount++;
            // Countdown in status text
            if (wsCountdownTimer) clearInterval(wsCountdownTimer);
            const delay = wsReconnectDelay;
            let rem = Math.ceil(delay / 1000);
            const txt = document.getElementById('ws-status-text');
            if (txt) txt.textContent = `RETRY ${rem}s`;
            wsCountdownTimer = setInterval(() => {
                rem--;
                if (rem > 0 && txt) txt.textContent = `RETRY ${rem}s`;
                else { clearInterval(wsCountdownTimer); wsCountdownTimer = null; }
            }, 1000);
            wsReconnectTimer = setTimeout(connectWS, delay);
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
        };
        ws.onerror = () => {
            // Don't immediately fall back to demo — let onclose handle retry
            if (ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) return;
            wsConnection = null; wsAuthenticated = false;
        };
    } catch { setStatus('demo'); startDemo(); }
}

// Reconnect on visibility change — if tab comes back from background and WS is dead, reconnect fast
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !wsConnection && getToken()) {
        if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
        wsReconnectDelay = 1000;
        _wsRetryCount = 0;
        connectWS();
    }
});

// ── Demo mode ──────────────────────────────────────────
function startDemo() {
    if (demoInterval) return;
    let agents = [];
    demoInterval = setInterval(() => {
        if (Math.random() > 0.5 && agents.length < 3) {
            agents.push({ description: 'Processing task', project: 'leon', startedAt: new Date().toISOString() });
        } else if (agents.length > 0 && Math.random() > 0.6) { agents.pop(); demoCompleted++; }
        brainState = {
            ...brainState,
            leftActive: true, rightActive: agents.length > 0,
            activeAgents: agents, agentCount: agents.length,
            taskCount: agents.length + 2, completedCount: demoCompleted,
            queuedCount: Math.floor(Math.random() * 3),
            uptime: Math.floor((Date.now() - demoUptimeStart) / 1000),
        };
        updateUI();
    }, 2000);
}

// ── Helpers ────────────────────────────────────────────
function now() {
    const d = new Date();
    return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}
function fmt(s) {
    return [Math.floor(s/3600), Math.floor((s%3600)/60), s%60].map(n => n.toString().padStart(2,'0')).join(':');
}
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
// Lightweight markdown → HTML (used for Leon's responses in the feed)
function renderMd(s) {
    let t = esc(s);
    t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/`([^`]+)`/g, '<code style="background:rgba(0,200,255,0.11);padding:1px 5px;border-radius:3px;font-size:0.88em;font-family:monospace">$1</code>');
    t = t.replace(/\n/g, '<br>');
    t = t.replace(/(^|<br>)[-*] /g, '$1• ');
    return t;
}

// ── UI Update ──────────────────────────────────────────
function updateUI() {
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };

    set('left-status',  brainState.leftActive  ? 'ACTIVE' : 'IDLE');
    set('right-status', brainState.rightActive ? 'ACTIVE' : 'IDLE');
    const ls = document.getElementById('left-status');
    if (ls) ls.className = 'iv ' + (brainState.leftActive  ? 'active' : 'idle');
    const rs = document.getElementById('right-status');
    if (rs) rs.className = 'iv ' + (brainState.rightActive ? 'active' : 'idle');

    set('agent-count',    brainState.agentCount    || 0);
    set('task-count',     brainState.taskCount     || 0);
    set('stat-uptime',    fmt(brainState.uptime    || 0));
    set('stat-completed', brainState.completedCount || 0);
    set('stat-queued',    brainState.queuedCount    || 0);
    set('hub-agents',     `AGENTS: ${brainState.agentCount || 0}`);

    // Hub status text (only update if no panel hint active)
    if (!activePanel) {
        const hs = document.getElementById('hub-status');
        if (hs) {
            const cnt = brainState.agentCount || 0;
            hs.setAttribute('fill', cnt > 0 ? '#ff7700' : '#00ffaa');
            hs.textContent = cnt > 0 ? `${cnt} AGENT${cnt > 1 ? 'S' : ''} ACTIVE` : 'ONLINE';
        }
    }

    // Agents list — live terminal cards
    const al = document.getElementById('agents-list');
    if (al) {
        const agents = brainState.activeAgents || [];
        if (!agents.length) {
            al.innerHTML = '<div class="agents-empty">IDLE</div>';
            _stopAgentLogPolling();
        } else {
            // Clear any stale IDLE placeholder
            al.querySelector('.agents-empty')?.remove();
            // Build terminal cards — preserve existing DOM nodes to avoid scroll reset
            const currentIds = new Set(agents.map(a => a.id).filter(Boolean));
            // Remove cards for agents that finished
            al.querySelectorAll('.agent-terminal[data-id]').forEach(el => {
                if (!currentIds.has(el.dataset.id)) el.remove();
            });
            // Add/update cards
            agents.forEach(a => {
                if (!a.id) return;
                let card = al.querySelector(`.agent-terminal[data-id="${a.id}"]`);
                if (!card) {
                    card = document.createElement('div');
                    card.className = 'agent-terminal';
                    card.dataset.id = a.id;
                    const proj = esc(a.project_name || a.projectName || '');
                    const desc = esc((a.description || '...').substring(0, 40));
                    card.innerHTML = `
                        <div class="agent-terminal-header">
                            <span class="agent-terminal-name" title="${desc}">${proj || desc}</span>
                            <div class="agent-terminal-meta">
                                <span class="agent-terminal-elapsed" data-started="${a.startedAt || ''}">0m00s</span>
                                <div class="agent-terminal-dot"></div>
                            </div>
                        </div>
                        <div class="agent-terminal-action" id="ataction-${a.id}">Initializing...</div>
                        <div class="agent-terminal-body" id="atbody-${a.id}">
                            <div class="agent-terminal-line dim">Waiting for output...</div>
                        </div>`;
                    al.appendChild(card);
                }
                // Update elapsed time
                const elEl = card.querySelector('.agent-terminal-elapsed');
                if (elEl && elEl.dataset.started) {
                    const s = Math.max(0, Math.floor((Date.now() - new Date(elEl.dataset.started).getTime()) / 1000));
                    elEl.textContent = `${Math.floor(s/60)}m${(s%60).toString().padStart(2,'0')}s`;
                }
            });
            _startAgentLogPolling(agents);
            // Auto-open agents panel when agents are running
            if (!activePanel) openPanel('agents');
        }
        const arc = document.getElementById('arc-agents');
        if (arc) arc.style.opacity = agents.length > 0 ? '0.8' : '0.38';
    }

    // Voice state → MIC arc — sync from server state (is_muted is authoritative)
    if (brainState.voice && typeof brainState.voice.is_muted === 'boolean') {
        const serverMuted = brainState.voice.is_muted;
        if (serverMuted !== micMuted) micMuted = serverMuted;  // keep client in sync
    }
    updateMicArc(micMuted ? 'muted' : 'active');

    // Update banner — show when a new version is available
    if (brainState.updateAvailable) {
        const b = document.getElementById('update-banner');
        if (b) {
            b.style.display = 'block';
            const msgEl = document.getElementById('update-msg');
            if (msgEl) msgEl.textContent = `Update v${brainState.updateVersion} available`;
            const linkEl = document.getElementById('update-link');
            if (linkEl && brainState.updateUrl) linkEl.href = brainState.updateUrl;
        }
    }

    // No-provider banner
    const npb = document.getElementById('no-provider-banner');
    const hasProvider = brainState.aiProvider && brainState.aiProvider !== 'none';
    if (npb) npb.style.display = hasProvider ? 'none' : 'block';

    // No claude CLI banner (agents won't work)
    const ncb = document.getElementById('no-claude-banner');
    if (ncb) {
        // Only show if provider is configured but claude CLI is missing
        const showCliWarn = hasProvider && brainState.claudeCliAvailable === false;
        ncb.style.display = showCliWarn ? 'block' : 'none';
    }

    // Stack banners vertically if multiple visible
    let bannerOffset = 0;
    for (const id of ['update-banner', 'no-provider-banner', 'no-claude-banner']) {
        const el = document.getElementById(id);
        if (el && el.style.display !== 'none') {
            el.style.top = bannerOffset + 'px';
            bannerOffset += el.offsetHeight || 37;
        }
    }
}

// ── Health Polling ─────────────────────────────────────
function startHealth() { pollHealth(); setInterval(pollHealth, 5000); }
async function pollHealth() {
    try {
        const r = await fetch('/api/health'); if (!r.ok) return;
        const d = await r.json();

        const cpuVal  = parseFloat(d.cpu)             || 0;
        const memVal  = parseFloat(d.memory?.percent) || 0;
        const diskVal = parseFloat(d.disk?.percent)   || 0;
        const gpuVal  = parseFloat(d.gpu?.usage)      || 0;
        setBar('bar-cpu',  'val-cpu',  cpuVal);
        setBar('bar-mem',  'val-mem',  memVal);
        setBar('bar-disk', 'val-disk', diskVal);
        setBar('bar-gpu',  'val-gpu',  gpuVal);
        // Spike alerts — max once per metric per 5 min
        const ALERT_COOLDOWN = 5 * 60 * 1000, ALERT_THRESH = 92;
        function alertIfHigh(label, val) {
            if (val < ALERT_THRESH) return;
            const t = Date.now();
            if (!alertCooldowns[label] || t - alertCooldowns[label] > ALERT_COOLDOWN) {
                alertCooldowns[label] = t;
                feed(now(), `⚠ ${label} spike: ${Math.round(val)}%`, 'feed-agent-fail');
            }
        }
        alertIfHigh('CPU', cpuVal); alertIfHigh('RAM', memVal);

        const set  = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
        const html = (id, v) => { const e = document.getElementById(id); if (e) e.innerHTML   = v; };

        if (d.memory) set('mem-detail',  `${(d.memory.used_mb/1024).toFixed(1)} / ${(d.memory.total_mb/1024).toFixed(1)} GB`);
        if (d.disk)   set('disk-detail', `${d.disk.used_gb} / ${d.disk.total_gb} GB`);
        if (d.gpu) {
            set('gpu-name', d.gpu.name  || '--');
            set('gpu-temp', d.gpu.temp  || '--');
            set('gpu-vram', `${d.gpu.vram_used||'--'} / ${d.gpu.vram_total||'--'}`);
        }
        const net = d.network ? Object.values(d.network)[0] : null;
        if (net) { html('net-rx', `${net.rx_gb} GB`); html('net-tx', `${net.tx_gb} GB`); }
        set('stat-load', d.load_avg  || '--');
        set('stat-proc', d.processes || '--');
        if (d.leon) {
            set('brain-role',      (d.leon.brain_role || 'unified').toUpperCase());
            set('notif-total',     d.leon.notifications?.total   || 0);
            set('notif-pending',   d.leon.notifications?.pending || 0);
            const sa   = d.leon.screen?.activity;
            const sapp = d.leon.screen?.active_app;
            set('screen-activity', `Activity: ${(sa   && sa   !== 'unknown') ? sa   : '--'}`);
            set('screen-app',      `App: ${(sapp && sapp !== 'unknown') ? sapp : '--'}`);
            // (status-agents moved to agents panel)
        }
    } catch { /* silent */ }
}
function setBar(barId, valId, pct) {
    const bar = document.getElementById(barId);
    const val = document.getElementById(valId);
    const p   = Math.min(pct, 100);
    if (bar) bar.style.width = p + '%';
    if (val) val.textContent = Math.round(p) + '%';
    if (bar && p > 90) bar.style.background = 'linear-gradient(90deg, #440011, #ff3355)';
}

// ── Commands ───────────────────────────────────────────
const CMDS = ['/agents','/status','/kill','/queue','/retry','/history','/search','/stats','/schedule','/notifications','/screen','/gpu','/clipboard','/changes','/export','/context','/bridge','/setkey','/vault','/approve','/voice','/whatsapp','/help'];

function initCmd() {
    const input = document.getElementById('command-input');
    const btn   = document.getElementById('cmd-send');
    if (!input || !btn) return;

    const bar = document.getElementById('cmd-bar');
    // Restore unsent draft
    const draft = localStorage.getItem('leon_input_draft');
    if (draft) { input.value = draft; }
    const ac  = document.createElement('div');
    ac.style.cssText = 'display:none;position:absolute;bottom:calc(100% + 6px);left:0;right:0;background:rgba(2,9,20,0.97);border:1px solid rgba(0,160,220,0.2);border-radius:8px;padding:4px 0;font-size:11px;z-index:100;max-height:200px;overflow-y:auto;backdrop-filter:blur(20px);';
    bar.appendChild(ac);

    function showAC() {
        const v = input.value;
        if (!v.startsWith('/')) { ac.style.display = 'none'; return; }
        const matches = CMDS.filter(c => c.startsWith(v.toLowerCase()));
        if (!matches.length) { ac.style.display = 'none'; return; }
        ac.innerHTML = matches.map(c =>
            `<div style="padding:5px 12px;cursor:pointer;color:#00ccff;font-family:monospace;font-size:11px;border-radius:3px;margin:0 3px"
                onmouseenter="this.style.background='rgba(0,160,220,0.1)'"
                onmouseleave="this.style.background=''"
                onmousedown="event.preventDefault();document.getElementById('command-input').value='${c} ';document.getElementById('command-input').focus();this.parentElement.style.display='none'"
            >${c}</div>`
        ).join('');
        ac.style.display = 'block';
    }

    function send() {
        const text = input.value.trim();
        if (!text || text.length > 2000) return;
        sfx('send');
        ac.style.display = 'none';
        if (commandHistory[commandHistory.length - 1] !== text) {
            commandHistory.push(text);
            if (commandHistory.length > 50) commandHistory = commandHistory.slice(-50);
            try { localStorage.setItem('leon_cmd_history', JSON.stringify(commandHistory)); } catch {}
        }
        historyIndex = -1;
        feed(now(), `> ${text}`, 'feed-command');
        autoOpenFeed();

        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            setLoading(true);
            wsConnection.send(JSON.stringify({ command: 'input', message: text }));
            if (loadingTimer) clearTimeout(loadingTimer);
            loadingTimer = setTimeout(() => { loadingTimer = null; setLoading(false); }, 30000);
            // Estimate input tokens from message length
            apiTracker.inputTokens += Math.ceil(text.length / 4) + 150; // +150 system prompt est
            apiTracker.messages++;
            apiTracker.render();
        } else {
            setTimeout(() => { feed(now(), `Leon: [Demo] ${text}`, 'feed-response'); }, 500);
        }
        input.value = ''; try { localStorage.removeItem('leon_input_draft'); } catch {}
        input.focus();
    }

    btn.onclick = send;

    // ── PTT / Activate button ──
    const pttBtn = document.getElementById('ptt-btn');
    if (pttBtn) {
        pttBtn.addEventListener('click', () => {
            sfx('mic-on');
            if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
                wsConnection.send(JSON.stringify({ command: 'voice_wake' }));
                feed(now(), '> Voice activated', 'feed-command');
                if (!activePanel) openPanel('feed');
            }
            pttBtn.classList.add('ptt-active');
            setTimeout(() => pttBtn.classList.remove('ptt-active'), 1500);
        });
    }

    input.addEventListener('input', () => {
        showAC();
        // Draft persistence
        try {
            if (input.value) localStorage.setItem('leon_input_draft', input.value);
            else localStorage.removeItem('leon_input_draft');
        } catch {}
        // Character counter — show when approaching 2000-char limit
        let cc = document.getElementById('cmd-char-count');
        const rem = 2000 - input.value.length;
        if (rem < 200) {
            if (!cc) {
                cc = document.createElement('span');
                cc.id = 'cmd-char-count';
                cc.style.cssText = 'font-family:monospace;font-size:9px;opacity:0.7;white-space:nowrap;transition:color 0.2s;';
                bar.appendChild(cc);
            }
            cc.textContent = rem;
            cc.style.color = rem < 50 ? '#ff3355' : '#ff8800';
        } else if (cc) { cc.remove(); }
    });
    input.addEventListener('blur', () => setTimeout(() => { ac.style.display = 'none'; }, 150));
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { send(); return; }
        if (e.key === 'Escape') { ac.style.display = 'none'; input.blur(); return; }
        if (ac.style.display !== 'block' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
            if (!commandHistory.length) return; e.preventDefault();
            if (e.key === 'ArrowUp') { if (historyIndex < commandHistory.length-1) historyIndex++; }
            else { if (historyIndex > 0) historyIndex--; else { historyIndex=-1; input.value=''; return; } }
            input.value = commandHistory[commandHistory.length-1-historyIndex] || '';
        }
    });
    document.addEventListener('keydown', (e) => {
        if (document.activeElement?.id === 'auth-token-input') return;
        const inInput = document.activeElement === input;
        // Ctrl+K — focus command bar from anywhere
        if (e.ctrlKey && e.key === 'k') {
            e.preventDefault(); input.focus(); return;
        }
        if (inInput) return; // let the input field handle its own keys
        if (e.key === '/') {
            e.preventDefault(); input.focus(); input.value = '/';
            input.dispatchEvent(new Event('input')); return;
        }
        // Escape — close active panel
        if (e.key === 'Escape' && activePanel) { closePanel(activePanel); return; }
        // ? — shortcuts help
        if (e.key === '?') { e.preventDefault(); showShortcutsHelp(); return; }
        // 1-4 — toggle panels (no modifier keys)
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
            if (e.key === '1') { e.preventDefault(); togglePanel('system'); }
            else if (e.key === '2') { e.preventDefault(); togglePanel('agents'); }
            else if (e.key === '3') { e.preventDefault(); togglePanel('feed'); }
            else if (e.key === '4') { e.preventDefault(); togglePanel('projects'); }
        }
    });
}

// ── Feed ───────────────────────────────────────────────
function feedDom(time, msg, cls) {
    const f = document.getElementById('activity-feed'); if (!f) return;
    if (!cls) cls = msg.startsWith('> ') ? 'feed-command' : msg.startsWith('Leon:') ? 'feed-response' : 'feed-local';
    const div = document.createElement('div');
    div.className = `feed-item ${cls}`;
    const msgHtml = cls === 'feed-response' ? renderMd(msg) : esc(msg);
    // Copy button floats right — must come before text in DOM for float to work
    const copyHtml = cls === 'feed-response' ? '<button class="feed-copy" title="Copy response">⎘</button>' : '';
    div.innerHTML = `${copyHtml}<span class="feed-time">${esc(time)}</span> ${msgHtml}`;
    // Relative timestamp on hover
    div.dataset.ts = Date.now();
    const timeEl = div.querySelector('.feed-time');
    if (timeEl) {
        div.addEventListener('mouseenter', () => {
            const secs = Math.floor((Date.now() - parseInt(div.dataset.ts)) / 1000);
            const rel = secs < 5 ? 'just now' : secs < 60 ? `${secs}s ago` : secs < 3600 ? `${Math.floor(secs/60)}m ago` : `${Math.floor(secs/3600)}h ago`;
            timeEl._orig = timeEl._orig || timeEl.textContent;
            timeEl.textContent = rel;
        });
        div.addEventListener('mouseleave', () => {
            if (timeEl._orig) { timeEl.textContent = timeEl._orig; delete timeEl._orig; }
        });
    }
    if (cls === 'feed-response') {
        const btn = div.querySelector('.feed-copy');
        if (btn) btn.addEventListener('click', () => {
            const rawText = msg.startsWith('Leon: ') ? msg.slice(6) : msg;
            navigator.clipboard.writeText(rawText).then(() => {
                btn.textContent = '✓'; btn.style.color = 'var(--green)'; btn.style.borderColor = 'var(--green)';
                setTimeout(() => { btn.textContent = '⎘'; btn.style.color = ''; btn.style.borderColor = ''; }, 1500);
            }).catch(() => {});
        });
    }
    f.appendChild(div);
    while (f.children.length > 200) f.removeChild(f.firstChild);
    if (feedAutoScroll) {
        requestAnimationFrame(() => f.scrollTo({ top: f.scrollHeight, behavior: 'smooth' }));
    } else {
        const rb = document.getElementById('feed-scroll-resume'); if (rb) rb.style.display = 'flex';
    }
}
function feed(time, msg, cls) {
    if (!cls) cls = msg.startsWith('> ') ? 'feed-command' : msg.startsWith('Leon:') ? 'feed-response' : 'feed-local';
    feedDom(time, msg, cls);
    // Unread badge — only count Leon responses & agent events when feed is not visible
    if (activePanel !== 'feed' && (cls === 'feed-response' || cls === 'feed-agent-ok' || cls === 'feed-agent-fail')) {
        feedUnread++;
        document.title = `(${feedUnread}) LEON — Neural Interface`;
    }
    try {
        const stored = JSON.parse(localStorage.getItem('leon_feed') || '[]');
        stored.push({ time, msg, cls });
        if (stored.length > 50) stored.splice(0, stored.length - 50);
        localStorage.setItem('leon_feed', JSON.stringify(stored));
    } catch {}
}
function clearFeed() {
    const f = document.getElementById('activity-feed');
    if (f) f.innerHTML = '';
    try { localStorage.removeItem('leon_feed'); } catch {}
}
function exportFeed() {
    const f = document.getElementById('activity-feed'); if (!f) return;
    const lines = [];
    for (const item of f.querySelectorAll('.feed-item')) {
        const timeEl = item.querySelector('.feed-time');
        const time = timeEl?._orig || timeEl?.textContent || '';
        const msgText = Array.from(item.childNodes)
            .filter(n => n !== timeEl && !(n.classList?.contains('feed-copy')))
            .map(n => n.textContent).join('').trim();
        lines.push(`[${time.trim()}] ${msgText}`);
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `leon-${new Date().toISOString().slice(0,10)}.txt`;
    a.click(); URL.revokeObjectURL(url);
}
function toggleFeedSearch() {
    const inp = document.getElementById('feed-search');
    const btn = document.getElementById('btn-feed-search');
    if (!inp) return;
    const visible = inp.style.display !== 'none';
    inp.style.display = visible ? 'none' : 'block';
    if (!visible) { inp.focus(); }
    else { inp.value = ''; filterFeed(''); }
    if (btn) btn.style.color = visible ? '' : 'var(--cyan)';
}
function filterFeed(query) {
    const f = document.getElementById('activity-feed'); if (!f) return;
    const q = query.toLowerCase().trim();
    for (const item of f.querySelectorAll('.feed-item')) {
        const text = item.textContent.toLowerCase();
        item.style.display = (!q || text.includes(q)) ? '' : 'none';
    }
}
function feedScrollResume() {
    feedAutoScroll = true;
    const btn = document.getElementById('feed-scroll-resume'); if (btn) btn.style.display = 'none';
    const f = document.getElementById('activity-feed');
    if (f) f.scrollTo({ top: f.scrollHeight, behavior: 'smooth' });
}
function showShortcutsHelp() {
    const existing = document.getElementById('shortcuts-overlay');
    if (existing) { existing.remove(); return; }
    const o = document.createElement('div');
    o.id = 'shortcuts-overlay';
    o.style.cssText = 'position:fixed;inset:0;z-index:9990;background:rgba(2,8,18,0.93);backdrop-filter:blur(22px);display:flex;align-items:center;justify-content:center;animation:fadeInOv 0.15s ease';
    o.innerHTML = `
        <div style="font-family:Orbitron,monospace;color:#00d4ff;max-width:360px;width:90%;padding:32px;
            border:1px solid rgba(0,180,255,0.25);border-radius:6px;background:rgba(4,16,32,0.95);">
            <div style="font-size:13px;font-weight:900;letter-spacing:8px;margin-bottom:22px;text-align:center;
                text-shadow:0 0 20px rgba(0,212,255,0.5)">SHORTCUTS</div>
            <table style="width:100%;border-collapse:collapse;font-size:10px;line-height:2">
                <tr><td style="color:#00d4ff;padding-right:18px;white-space:nowrap">/</td><td style="color:rgba(0,212,255,0.6)">Focus command bar</td></tr>
                <tr><td style="color:#00d4ff;white-space:nowrap">Ctrl + K</td><td style="color:rgba(0,212,255,0.6)">Focus command bar (empty)</td></tr>
                <tr><td style="color:#00d4ff;white-space:nowrap">Esc</td><td style="color:rgba(0,212,255,0.6)">Close active panel</td></tr>
                <tr><td style="color:#00d4ff;white-space:nowrap">1 – 4</td><td style="color:rgba(0,212,255,0.6)">Toggle panels</td></tr>
                <tr><td style="color:#00d4ff;white-space:nowrap">↑ ↓</td><td style="color:rgba(0,212,255,0.6)">Command history</td></tr>
                <tr><td style="color:#00d4ff;white-space:nowrap">?</td><td style="color:rgba(0,212,255,0.6)">This help screen</td></tr>
            </table>
            <div style="text-align:center;margin-top:22px;font-size:7px;color:rgba(0,180,255,0.2);letter-spacing:3px">CLICK OR ANY KEY TO CLOSE</div>
        </div>`;
    o.addEventListener('click', () => o.remove());
    document.addEventListener('keydown', () => o.remove(), { once: true });
    document.body.appendChild(o);
}
function loadFeedHistory() {
    try {
        const stored = JSON.parse(localStorage.getItem('leon_feed') || '[]');
        if (!stored.length) return;
        const f = document.getElementById('activity-feed'); if (!f) return;
        const sep = document.createElement('div');
        sep.className = 'feed-item feed-local';
        sep.innerHTML = `<span class="feed-time">──</span> <em style="opacity:0.4">session history</em>`;
        f.appendChild(sep);
        for (const item of stored) feedDom(item.time, item.msg, item.cls);
    } catch {}
}

// ── OpenClaw crab button ───────────────────────────────
function initClawButton() {
    const btn = document.getElementById('btn-openclaw');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        feed(now(), '> 🦀 Starting OpenClaw...', 'feed-command');
        autoOpenFeed();
        btn.style.transform = 'scale(1.3)';
        btn.style.borderColor = '#00d4ff';
        setTimeout(() => { btn.style.transform = ''; btn.style.borderColor = ''; }, 300);
        try {
            const r = await fetch('/api/openclaw-url');
            const d = await r.json();
            if (d.url) {
                window.open(d.url, '_blank');
                feed(now(), '> 🦀 OpenClaw dashboard opened', 'feed-local');
            } else {
                feed(now(), `Leon: OpenClaw error — ${d.error || 'unknown'}`, 'feed-response');
            }
        } catch {
            feed(now(), 'Leon: Failed to reach OpenClaw gateway', 'feed-response');
        }
    });
}

// ── Live Transcription Display ─────────────────────────
let _transcriptionEl = null;
let _transcriptionTimer = null;

function _getTranscriptionEl() {
    if (!_transcriptionEl) {
        _transcriptionEl = document.createElement('div');
        _transcriptionEl.id = 'live-transcription';
        _transcriptionEl.style.cssText = `
            position: fixed;
            bottom: 72px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0,0,0,0.75);
            border: 1px solid rgba(0,200,255,0.3);
            border-radius: 8px;
            padding: 8px 18px;
            font-size: 13px;
            font-family: monospace;
            letter-spacing: 0.04em;
            color: #00d4ff;
            pointer-events: none;
            z-index: 9999;
            max-width: 520px;
            text-align: center;
            transition: opacity 0.3s;
            opacity: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        `;
        document.body.appendChild(_transcriptionEl);
    }
    return _transcriptionEl;
}

function handleVadEvent(event, text) {
    const el = _getTranscriptionEl();
    clearTimeout(_transcriptionTimer);

    if (event === 'recording') {
        el.innerHTML = '<span style="color:#ff4466">⏺</span> &nbsp;Listening...';
        el.style.opacity = '1';
        el.style.borderColor = 'rgba(255,68,102,0.5)';

    } else if (event === 'transcription') {
        const escaped = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        el.innerHTML = `<span style="color:#aaa">heard:</span> &nbsp;<span style="color:#fff">${escaped}</span>`;
        el.style.opacity = '1';
        el.style.borderColor = 'rgba(0,200,255,0.4)';
        // Fade out after 4 seconds
        _transcriptionTimer = setTimeout(() => {
            el.style.opacity = '0';
        }, 4000);
    }
}

// ── Futuristic UI Sounds ───────────────────────────────
const _ac = new (window.AudioContext || window.webkitAudioContext)();

function _resumeAC() { if (_ac.state === 'suspended') _ac.resume(); }

function sfx(type) {
    _resumeAC();
    const now = _ac.currentTime;
    const g = _ac.createGain();
    g.connect(_ac.destination);

    if (type === 'click') {
        // Short futuristic chirp — high-to-low sweep
        const o = _ac.createOscillator();
        o.type = 'sine';
        o.frequency.setValueAtTime(1800, now);
        o.frequency.exponentialRampToValueAtTime(900, now + 0.08);
        g.gain.setValueAtTime(0.18, now);
        g.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
        o.connect(g); o.start(now); o.stop(now + 0.12);

    } else if (type === 'open') {
        // Panel open — ascending double-tone
        [0, 0.06].forEach((delay, i) => {
            const o = _ac.createOscillator();
            o.type = 'sine';
            o.frequency.setValueAtTime(600 + i * 300, now + delay);
            o.frequency.exponentialRampToValueAtTime(1200 + i * 300, now + delay + 0.1);
            const og = _ac.createGain();
            og.gain.setValueAtTime(0.12, now + delay);
            og.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.15);
            o.connect(og); og.connect(_ac.destination);
            o.start(now + delay); o.stop(now + delay + 0.15);
        });

    } else if (type === 'close') {
        // Panel close — descending sweep
        const o = _ac.createOscillator();
        o.type = 'sine';
        o.frequency.setValueAtTime(1100, now);
        o.frequency.exponentialRampToValueAtTime(400, now + 0.1);
        g.gain.setValueAtTime(0.12, now);
        g.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
        o.connect(g); o.start(now); o.stop(now + 0.12);

    } else if (type === 'mic-on') {
        // Mic activate — rising triple blip
        [0, 0.07, 0.14].forEach((delay, i) => {
            const o = _ac.createOscillator();
            o.type = 'triangle';
            o.frequency.value = 800 + i * 200;
            const og = _ac.createGain();
            og.gain.setValueAtTime(0.15, now + delay);
            og.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.06);
            o.connect(og); og.connect(_ac.destination);
            o.start(now + delay); o.stop(now + delay + 0.06);
        });

    } else if (type === 'mic-off') {
        // Mic deactivate — descending double blip
        [0, 0.07].forEach((delay, i) => {
            const o = _ac.createOscillator();
            o.type = 'triangle';
            o.frequency.value = 900 - i * 250;
            const og = _ac.createGain();
            og.gain.setValueAtTime(0.13, now + delay);
            og.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.07);
            o.connect(og); og.connect(_ac.destination);
            o.start(now + delay); o.stop(now + delay + 0.07);
        });

    } else if (type === 'send') {
        // Message send — quick upward blip
        const o = _ac.createOscillator();
        o.type = 'sine';
        o.frequency.setValueAtTime(700, now);
        o.frequency.exponentialRampToValueAtTime(1400, now + 0.07);
        g.gain.setValueAtTime(0.14, now);
        g.gain.exponentialRampToValueAtTime(0.001, now + 0.09);
        o.connect(g); o.start(now); o.stop(now + 0.09);

    } else if (type === 'boot') {
        // Startup sweep — cinematic rising tone
        const o = _ac.createOscillator();
        o.type = 'sine';
        o.frequency.setValueAtTime(200, now);
        o.frequency.exponentialRampToValueAtTime(1600, now + 0.4);
        g.gain.setValueAtTime(0.0, now);
        g.gain.linearRampToValueAtTime(0.2, now + 0.1);
        g.gain.exponentialRampToValueAtTime(0.001, now + 0.45);
        o.connect(g); o.start(now); o.stop(now + 0.45);
    }
}

// ── Boot ───────────────────────────────────────────────
// Set initial MIC arc state
updateMicArc('muted');

// Boot sound — play once on first user interaction (browser requires gesture)
document.addEventListener('click', () => sfx('boot'), { once: true });

// Initialize panel navigation
initNavArcs();

// OpenClaw button
initClawButton();

// Restore last open panel (default: feed)
const _lastPanel = localStorage.getItem('leon_last_panel') || 'feed';
openPanel(_lastPanel);

// Restore persisted feed messages from previous session
loadFeedHistory();

// Feed search input wiring
(function initFeedSearch() {
    const inp = document.getElementById('feed-search'); if (!inp) return;
    inp.addEventListener('input', () => filterFeed(inp.value));
    inp.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { toggleFeedSearch(); }
    });
})();

// Feed scroll-lock: pause auto-scroll when user scrolls up
(function initFeedScroll() {
    const f = document.getElementById('activity-feed'); if (!f) return;
    f.addEventListener('scroll', () => {
        const atBottom = f.scrollHeight - f.scrollTop - f.clientHeight < 30;
        feedAutoScroll = atBottom;
        const btn = document.getElementById('feed-scroll-resume');
        if (btn) btn.style.display = atBottom ? 'none' : 'flex';
    });
})();

// Request browser notification permission (silently — no prompt if already decided)
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

// Live clock in top bar
function tickClock() {
    const el = document.getElementById('stat-clock'); if (!el) return;
    const d = new Date();
    el.textContent = [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => n.toString().padStart(2,'0')).join(':');
}
tickClock();
setInterval(tickClock, 1000);

// Live agent elapsed-time tick (independent of WS broadcast cadence)
function tickAgentElapsed() {
    const agents = brainState.activeAgents || [];
    if (!agents.length) return;
    document.querySelectorAll('.agent-terminal[data-id]').forEach(card => {
        const el = card.querySelector('.agent-terminal-elapsed'); if (!el || !el.dataset.started) return;
        const s = Math.max(0, Math.floor((Date.now() - new Date(el.dataset.started).getTime()) / 1000));
        el.textContent = `${Math.floor(s/60)}m${(s%60).toString().padStart(2,'0')}s`;
    });
}
setInterval(tickAgentElapsed, 1000);

// ── Agent Log Polling ───────────────────────────────
let _agentLogInterval = null;

function _startAgentLogPolling(agents) {
    if (_agentLogInterval) return; // already polling
    _agentLogInterval = setInterval(() => _fetchAgentLogs(agents), 3000);
    _fetchAgentLogs(agents); // immediate first fetch
}

function _stopAgentLogPolling() {
    if (_agentLogInterval) { clearInterval(_agentLogInterval); _agentLogInterval = null; }
}

async function _fetchAgentLogs(agents) {
    const currentAgents = brainState.activeAgents || [];
    if (!currentAgents.length) { _stopAgentLogPolling(); return; }
    for (const a of currentAgents) {
        if (!a.id) continue;
        const body   = document.getElementById(`atbody-${a.id}`);
        const action = document.getElementById(`ataction-${a.id}`);
        if (!body) continue;
        try {
            const r = await fetch(`/api/agent-log/${a.id}`);
            if (!r.ok) continue;
            const d = await r.json();

            // Update the current-action summary line
            if (action) {
                action.textContent = d.current_action
                    ? d.current_action.replace(/^#+\s*/, '')
                    : (d.lines && d.lines.length ? 'Working...' : 'Initializing...');
            }

            // Always render full log — user wants to see live terminal output
            if (!d.lines || !d.lines.length) continue;
            const lines = d.lines.slice(-30);
            const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
            body.innerHTML = lines.map(l => {
                const escaped = esc(l);
                let cls = '';
                if (l.startsWith('✓') || l.includes('completed') || l.includes('✅')) cls = 'ok';
                else if (l.startsWith('✗') || l.toLowerCase().includes('error') || l.includes('❌')) cls = 'err';
                else if (_re_tool.test(l)) cls = 'tool';
                else if (l.startsWith('#') || l.startsWith('**')) cls = 'info';
                else if (l.trim() === '' || l.startsWith('---') || l.startsWith('```')) cls = 'dim';
                return `<div class="agent-terminal-line ${cls}">${escaped}</div>`;
            }).join('');
            if (atBottom) body.scrollTop = body.scrollHeight;
        } catch (_) {}
    }
}
const _re_tool = /\b(Bash|Edit|Write|Read|Glob|Grep|WebFetch|Task)\(/;

// Connect and start
connectWS();
initCmd();
startHealth();

// ── Settings Panel ─────────────────────────────────────
let settingsOpen = false;

function toggleSettings() {
    const panel = document.getElementById('panel-settings');
    settingsOpen = !settingsOpen;
    panel.style.display = settingsOpen ? 'block' : 'none';
    if (settingsOpen) {
        // Request current settings from server
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            wsConnection.send(JSON.stringify({ command: 'get_settings' }));
        }
    }
}

function setResponseMode(mode) {
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        wsConnection.send(JSON.stringify({ command: 'set_response_mode', mode }));
    }
    document.querySelectorAll('.mode-btn[data-mode]').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
    });
}

function onVolumeSlider(val) {
    document.getElementById('vol-display').textContent = val + '%';
}

function applyVolume(val) {
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        wsConnection.send(JSON.stringify({ command: 'set_voice_volume', volume: parseInt(val) }));
    }
}

function settingsToggleMic() {
    const btn = document.getElementById('settings-mute-btn');
    const muted = btn.textContent.includes('Muted');
    if (!muted) {
        btn.textContent = '🔇 Muted';
        btn.classList.remove('active');
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN)
            wsConnection.send(JSON.stringify({ command: 'voice_mute' }));
    } else {
        btn.textContent = '🎤 Active';
        btn.classList.add('active');
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN)
            wsConnection.send(JSON.stringify({ command: 'voice_unmute' }));
    }
}

// Handle settings updates from server
function applySettings(data) {
    if (data.response_mode) {
        document.querySelectorAll('.mode-btn[data-mode]').forEach(b => {
            b.classList.toggle('active', b.dataset.mode === data.response_mode);
        });
    }
    if (data.voice_volume !== undefined) {
        document.getElementById('voice-vol-slider').value = data.voice_volume;
        document.getElementById('vol-display').textContent = data.voice_volume + '%';
    }
    if (data.muted !== undefined) {
        const btn = document.getElementById('settings-mute-btn');
        btn.textContent = data.muted ? '🔇 Muted' : '🎤 Active';
        btn.classList.toggle('active', !data.muted);
    }
}
