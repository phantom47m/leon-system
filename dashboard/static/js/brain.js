/**
 * LEON BRAIN — Holographic Wireframe Brain + Jarvis HUD Controller
 */

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';
import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/UnrealBloomPass.js';

// ═══════════════════════════════════════════════════════
// 3D BRAIN VISUALIZATION
// ═══════════════════════════════════════════════════════

const CFG = { BRAIN: 0x00ccff, GLOW: 0x0088cc, RING: 0x00aadd, BG: 0x020810 };

let scene, camera, renderer, composer, controls;
let brainGroup, leftMesh, rightMesh, wireL, wireR;
let jarvisRings = [];
let brainActivity = 0;
let clock = new THREE.Clock();

function init3D() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(CFG.BG);

    camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.1, 100);
    camera.position.set(0, 1.8, 7.5);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(innerWidth, innerHeight);
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ReinhardToneMapping;
    renderer.toneMappingExposure = 1.3;
    document.getElementById('brain-container').appendChild(renderer.domElement);

    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.7, 0.3, 0.85));

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.35;
    controls.minDistance = 4.5;
    controls.maxDistance = 12;
    controls.minPolarAngle = Math.PI * 0.3;
    controls.maxPolarAngle = Math.PI * 0.7;

    brainGroup = new THREE.Group();
    scene.add(brainGroup);

    createBrain();
    createRings();
    createDust();

    scene.add(new THREE.AmbientLight(0x081828, 0.4));
    addEventListener('resize', () => {
        camera.aspect = innerWidth / innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(innerWidth, innerHeight);
        composer.setSize(innerWidth, innerHeight);
    });
}

function makeBrainGeo(side) {
    const geo = new THREE.IcosahedronGeometry(1.35, 4);
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        let x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
        y *= 1.18; z *= 0.96;
        if (side === 'left' && x > 0.05) x = 0.05 + (x - 0.05) * 0.06;
        if (side === 'right' && x < -0.05) x = -0.05 + (x + 0.05) * 0.06;
        const fold = 0.035 * Math.sin(x * 9 + y * 7) * Math.cos(z * 8 + y * 5);
        const d = Math.sqrt(x*x + y*y + z*z) || 1;
        x += x * fold / d; y += y * fold / d; z += z * fold / d;
        pos.setXYZ(i, x + (side === 'left' ? -0.78 : 0.78), y, z);
    }
    geo.computeVertexNormals();
    return geo;
}

const holoVert = `
    varying vec3 vNormal; varying vec3 vPos; varying float vFresnel;
    uniform float uTime;
    void main() {
        vNormal = normalize(normalMatrix * normal);
        vPos = position;
        vec3 vDir = normalize(cameraPosition - (modelMatrix * vec4(position,1.0)).xyz);
        vFresnel = 1.0 - abs(dot(vDir, normalize((modelMatrix * vec4(normal,0.0)).xyz)));
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);
    }
`;
const holoFrag = `
    uniform vec3 uColor; uniform float uTime; uniform float uActivity;
    varying vec3 vNormal; varying vec3 vPos; varying float vFresnel;
    void main() {
        float edge = pow(vFresnel, 2.5) * 0.9;
        float scan = smoothstep(0.3, 0.7, 0.5 + 0.5 * sin(vPos.y * 35.0 + uTime * 2.5)) * 0.12;
        float pulse = 0.06 * sin(uTime * 3.0 + vPos.x * 5.0) * uActivity;
        float a = edge + scan + pulse + 0.02;
        gl_FragColor = vec4(uColor * (1.0 + edge * 0.6), a);
    }
`;

function createBrain() {
    function makeHalf(side) {
        const geo = makeBrainGeo(side);
        const mat = new THREE.ShaderMaterial({
            uniforms: { uTime: {value:0}, uColor: {value: new THREE.Color(CFG.BRAIN)}, uActivity: {value:0} },
            vertexShader: holoVert, fragmentShader: holoFrag,
            transparent: true, side: THREE.DoubleSide, depthWrite: false,
        });
        const mesh = new THREE.Mesh(geo, mat);
        brainGroup.add(mesh);
        const wire = new THREE.LineSegments(
            new THREE.WireframeGeometry(geo),
            new THREE.LineBasicMaterial({ color: CFG.BRAIN, transparent: true, opacity: 0.1 })
        );
        brainGroup.add(wire);
        return { mesh, wire };
    }
    const L = makeHalf('left'); leftMesh = L.mesh; wireL = L.wire;
    const R = makeHalf('right'); rightMesh = R.mesh; wireR = R.wire;

    // Subtle center glow
    const g = new THREE.Mesh(
        new THREE.SphereGeometry(0.5, 12, 12),
        new THREE.MeshBasicMaterial({ color: CFG.BRAIN, transparent: true, opacity: 0.03, side: THREE.BackSide })
    );
    brainGroup.add(g);
    brainGroup.add(new THREE.PointLight(CFG.BRAIN, 0.25, 4));
}

function createRings() {
    [
        { r:2.6, arc:1.5, s:0.11, t:0.12, o:0.2 },
        { r:2.9, arc:1.2, s:-0.08, t:-0.08, o:0.15 },
        { r:3.2, arc:0.8, s:0.05, t:0.22, o:0.1 },
        { r:2.3, arc:1.0, s:-0.14, t:-0.18, o:0.18 },
    ].forEach(c => {
        const m = new THREE.Mesh(
            new THREE.TorusGeometry(c.r, 0.006, 6, 100, Math.PI * c.arc),
            new THREE.MeshBasicMaterial({ color: CFG.RING, transparent: true, opacity: c.o })
        );
        m.rotation.set(Math.PI/2 + c.t, 0, Math.random() * 6.28);
        m.userData = { speed: c.s, baseOp: c.o };
        scene.add(m); jarvisRings.push(m);
    });

    // Dashed circles
    [2.0, 3.0].forEach((r, i) => {
        const pts = [];
        for (let j = 0; j <= 128; j++) {
            const a = (j/128) * Math.PI * 2;
            pts.push(new THREE.Vector3(Math.cos(a)*r, 0, Math.sin(a)*r));
        }
        const l = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(pts),
            new THREE.LineDashedMaterial({ color: CFG.RING, transparent: true, opacity: 0.05, dashSize: 0.15, gapSize: 0.35+i*0.2 })
        );
        l.computeLineDistances();
        l.rotation.x = Math.PI/2;
        l.userData = { speed: 0.012 * (i%2===0?1:-1), baseOp: 0.05 };
        scene.add(l); jarvisRings.push(l);
    });
}

function createDust() {
    const n = 100, pos = new Float32Array(n*3);
    for (let i = 0; i < n; i++) {
        pos[i*3] = (Math.random()-0.5)*14;
        pos[i*3+1] = (Math.random()-0.5)*9;
        pos[i*3+2] = (Math.random()-0.5)*9;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    scene.add(new THREE.Points(g, new THREE.PointsMaterial({
        color: CFG.BRAIN, size: 0.012, transparent: true, opacity: 0.25, depthWrite: false,
    })));
}

function animate() {
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();
    const target = Math.min(1, (brainState.agentCount||0) / 3);
    brainActivity += (target - brainActivity) * 0.02;

    if (leftMesh) { leftMesh.material.uniforms.uTime.value = t; leftMesh.material.uniforms.uActivity.value = brainActivity; }
    if (rightMesh) { rightMesh.material.uniforms.uTime.value = t; rightMesh.material.uniforms.uActivity.value = brainActivity; }
    const wo = 0.06 + 0.12 * brainActivity;
    if (wireL) wireL.material.opacity = wo;
    if (wireR) wireR.material.opacity = wo;
    if (brainGroup) brainGroup.rotation.y = Math.sin(t * 0.08) * 0.04;

    for (const r of jarvisRings) {
        r.rotation.z += r.userData.speed * 0.01;
        if (r.material.opacity !== undefined) r.material.opacity = r.userData.baseOp * (0.6 + 0.8 * brainActivity);
    }
    controls.update();
    composer.render();
}

// ═══════════════════════════════════════════════════════
// DASHBOARD CONTROLLER
// ═══════════════════════════════════════════════════════

let brainState = {
    leftActive: true, rightActive: false, bridgeActive: false,
    activeAgents: [], agentCount: 0, taskCount: 0,
    completedCount: 0, queuedCount: 0, maxConcurrent: 5,
    uptime: 0, taskFeed: [], voice: {},
};
let wsConnection = null;
let localFeedItems = [];
let demoUptimeStart = Date.now();
let wsReconnectDelay = 1000;
const WS_MAX_RECONNECT_DELAY = 30000;
let wsReconnectTimer = null;
const MAX_INPUT_LENGTH = 2000;
let commandHistory = [];
let historyIndex = -1;
const MAX_HISTORY = 50;
let healthPollTimer = null;
let isWaitingResponse = false;

// ── Auth ─────────────────────────────────────────────────
let wsAuthenticated = false;
function getStoredToken() { return localStorage.getItem('leon_session_token') || ''; }
function setStoredToken(t) { localStorage.setItem('leon_session_token', t); }

function showAuthOverlay(msg) {
    const o = document.getElementById('auth-overlay');
    if (!o) return;
    o.style.display = 'flex';
    const e = document.getElementById('auth-error');
    if (e) e.textContent = msg || '';
    const inp = document.getElementById('auth-token-input');
    const btn = document.getElementById('auth-submit');
    if (!inp || !btn) return;
    setTimeout(() => inp.focus(), 100);
    function doAuth() {
        const tk = inp.value.trim();
        if (!tk) return;
        setStoredToken(tk);
        o.style.display = 'none';
        wsConnection ? wsConnection.close() : connectWebSocket();
    }
    btn.onclick = doAuth;
    inp.onkeydown = (e) => { if (e.key === 'Enter') doAuth(); };
}
function hideAuthOverlay() { const o = document.getElementById('auth-overlay'); if (o) o.style.display = 'none'; }

function setConnectionStatus(s) {
    const dot = document.getElementById('ws-dot'), txt = document.getElementById('ws-status-text');
    if (!dot || !txt) return;
    const ind = document.getElementById('ws-indicator');
    dot.classList.remove('pulse','disconnected','reconnecting');
    if (ind) ind.classList.remove('disconnected','reconnecting');
    switch(s) {
        case 'connected': dot.classList.add('pulse'); dot.style.background=''; txt.textContent='SYSTEM ONLINE'; break;
        case 'disconnected': dot.classList.add('disconnected'); txt.textContent='DISCONNECTED'; if(ind)ind.classList.add('disconnected'); break;
        case 'reconnecting': dot.classList.add('reconnecting'); txt.textContent='RECONNECTING'; if(ind)ind.classList.add('reconnecting'); break;
        case 'demo': dot.classList.add('pulse'); dot.style.background='var(--gold)'; txt.textContent='DEMO MODE'; break;
    }
}
function setLoading(a) { isWaitingResponse = a; const l = document.getElementById('command-loading'); if(l) l.classList.toggle('active', a); }

// ── WebSocket ────────────────────────────────────────────
function connectWebSocket() {
    const token = getStoredToken();
    if (!token) { showAuthOverlay(''); setConnectionStatus('demo'); startDemoMode(); return; }
    setConnectionStatus('reconnecting');
    try {
        const ws = new WebSocket(`ws://${location.host}/ws`);
        wsConnection = ws; wsAuthenticated = false;
        ws.onopen = () => { wsReconnectDelay = 1000; ws.send(JSON.stringify({command:'auth',token})); };
        ws.onmessage = (ev) => {
            let d; try { d = JSON.parse(ev.data); } catch { return; }
            if (d.type==='auth_result') {
                if (d.success) { wsAuthenticated=true; hideAuthOverlay(); setConnectionStatus('connected'); if(demoInterval){clearInterval(demoInterval);demoInterval=null;} }
                else { wsAuthenticated=false; localStorage.removeItem('leon_session_token'); setConnectionStatus('disconnected'); showAuthOverlay(d.message||'Auth failed'); }
                return;
            }
            if (d.type==='input_response') { setLoading(false); appendToFeed(d.timestamp||nowTime(),`Leon: ${d.message}`,'feed-response'); return; }
            if (d.type==='agent_completed') { appendToFeed(nowTime(),`Agent #${(d.agent_id||'').slice(-8)} done: ${d.summary||''}`,'feed-agent-ok'); return; }
            if (d.type==='agent_failed') { appendToFeed(nowTime(),`Agent #${(d.agent_id||'').slice(-8)} failed: ${d.error||''}`,'feed-agent-fail'); return; }
            brainState = {...brainState,...d}; updateUI();
        };
        ws.onclose = () => { wsConnection=null; wsAuthenticated=false; setConnectionStatus('reconnecting'); wsReconnectTimer=setTimeout(connectWebSocket,wsReconnectDelay); wsReconnectDelay=Math.min(wsReconnectDelay*2,WS_MAX_RECONNECT_DELAY); };
        ws.onerror = () => { wsConnection=null; wsAuthenticated=false; setConnectionStatus('demo'); startDemoMode(); };
    } catch { setConnectionStatus('demo'); startDemoMode(); }
}

// ── Demo Mode ────────────────────────────────────────────
let demoInterval = null;
let demoCompletedCount = 0;
const demoPool = [
    {description:'Scanning dependencies',project:'leon-system'},{description:'Running tests',project:'openclaw'},
    {description:'Analyzing patterns',project:'leon-system'},{description:'Deploying update',project:'dashboard'},
];
function startDemoMode() {
    if (demoInterval) return;
    let agents=[], q=2;
    demoInterval = setInterval(() => {
        if (Math.random()>0.5 && agents.length<4) { const p=demoPool.filter(a=>!agents.some(d=>d.description===a.description)); if(p.length) { const a={...p[Math.floor(Math.random()*p.length)]}; a.startedAt=new Date(Date.now()-Math.floor(Math.random()*120000)).toISOString(); agents.push(a); } }
        else if (agents.length>0 && Math.random()>0.6) { agents.pop(); demoCompletedCount++; }
        q=Math.floor(Math.random()*5);
        brainState = {...brainState, leftActive:true, rightActive:agents.length>0, activeAgents:agents, agentCount:agents.length, taskCount:agents.length+q, completedCount:demoCompletedCount, queuedCount:q, uptime:Math.floor((Date.now()-demoUptimeStart)/1000)};
        updateUI();
    }, 2000);
}

// ── Helpers ──────────────────────────────────────────────
function nowTime() { const d=new Date(); return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0'); }
function formatUptime(s) { return `${Math.floor(s/3600).toString().padStart(2,'0')}:${Math.floor((s%3600)/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`; }
function escapeHtml(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ── UI Updates ───────────────────────────────────────────
function updateUI() {
    const ls = document.getElementById('left-status');
    if (ls) { ls.textContent = brainState.leftActive ? 'ACTIVE' : 'IDLE'; ls.className = 'card-val ' + (brainState.leftActive ? 'active' : 'idle'); }
    const rs = document.getElementById('right-status');
    if (rs) { rs.textContent = brainState.rightActive ? 'ACTIVE' : 'IDLE'; rs.className = 'card-val ' + (brainState.rightActive ? 'active' : 'idle'); }

    const ac = document.getElementById('agent-count'); if (ac) ac.textContent = brainState.agentCount||0;
    const tc = document.getElementById('task-count'); if (tc) tc.textContent = brainState.taskCount||0;

    const up = document.getElementById('stat-uptime'); if (up) up.textContent = formatUptime(brainState.uptime||0);
    const co = document.getElementById('stat-completed'); if (co) co.textContent = brainState.completedCount||0;
    const qu = document.getElementById('stat-queued'); if (qu) qu.textContent = brainState.queuedCount||0;

    updateAgentsPanel();
    updateVoiceState();
}

function updateAgentsPanel() {
    const el = document.getElementById('agents-list'); if (!el) return;
    const agents = brainState.activeAgents || [];
    if (!agents.length) { el.innerHTML = '<div class="agents-empty">IDLE</div>'; return; }
    el.innerHTML = agents.map(a => {
        let e=''; if(a.startedAt){const s=Math.max(0,Math.floor((Date.now()-new Date(a.startedAt).getTime())/1000));e=`${Math.floor(s/60)}m${(s%60).toString().padStart(2,'0')}s`;}
        return `<div class="agent-card"><span class="agent-desc">${escapeHtml((a.description||'Working...').substring(0,22))}</span>${e?`<span class="agent-elapsed">${e}</span>`:''}</div>`;
    }).join('');
}

function updateVoiceState() {
    const el = document.getElementById('voice-state'); if (!el) return;
    const v = brainState.voice||{};
    if (!v.active) { el.textContent='MIC OFF'; el.classList.remove('active'); return; }
    const labels = {idle:'MIC IDLE',listening:'LISTENING',awake:'AWAKE',processing:'THINKING',speaking:'SPEAKING',sleeping:'SLEEP',stopped:'MIC OFF',degraded:'DEGRADED'};
    el.textContent = labels[v.state] || 'MIC ' + (v.state||'OFF').toUpperCase();
    el.classList.toggle('active', ['listening','awake','speaking'].includes(v.state));
}

// ── Health Polling ───────────────────────────────────────
function startHealthPolling() { pollHealth(); healthPollTimer = setInterval(pollHealth, 5000); }
async function pollHealth() {
    try {
        const r = await fetch('/api/health'); if (!r.ok) return;
        const d = await r.json(); updateGauges(d); updateExtraStats(d);
    } catch {}
}

function updateGauges(d) {
    const C = 97.4;
    updateGauge('gauge-cpu','gauge-cpu-val',parseFloat(d.cpu)||0,C);
    updateGauge('gauge-mem','gauge-mem-val',d.memory?.percent?parseFloat(d.memory.percent):0,C);
    updateGauge('gauge-disk','gauge-disk-val',d.disk?.percent?parseFloat(d.disk.percent):0,C);
    updateGauge('gauge-gpu','gauge-gpu-val',d.gpu?.usage?parseFloat(d.gpu.usage):0,C);
}
function updateGauge(cid,vid,pct,C) {
    const c=document.getElementById(cid),v=document.getElementById(vid); if(!c||!v)return;
    c.style.strokeDashoffset = C - (C * Math.min(pct,100)/100);
    v.textContent = Math.round(pct)+'%';
    c.classList.remove('warn','critical');
    if(pct>90)c.classList.add('critical'); else if(pct>75)c.classList.add('warn');
}
function updateExtraStats(d) {
    const me=document.getElementById('mem-detail'); if(me&&d.memory)me.textContent=`${(d.memory.used_mb/1024).toFixed(1)} / ${(d.memory.total_mb/1024).toFixed(1)} GB`;
    if(d.gpu){const n=document.getElementById('gpu-name'),t=document.getElementById('gpu-temp'),v=document.getElementById('gpu-vram');if(n)n.textContent=d.gpu.name||'--';if(t)t.textContent=d.gpu.temp||'--';if(v)v.textContent=`VRAM: ${d.gpu.vram_used||'--'} / ${d.gpu.vram_total||'--'}`;}
    const de=document.getElementById('disk-detail'); if(de&&d.disk)de.textContent=`${d.disk.used_gb} / ${d.disk.total_gb} GB`;
    const net=d.network?Object.values(d.network)[0]:null;
    if(net){const rx=document.getElementById('net-rx'),tx=document.getElementById('net-tx');if(rx)rx.innerHTML=`&#x2193; ${net.rx_gb} GB`;if(tx)tx.innerHTML=`&#x2191; ${net.tx_gb} GB`;}
    const lo=document.getElementById('stat-load');if(lo)lo.textContent=d.load_avg||'--';
    const pr=document.getElementById('stat-proc');if(pr)pr.textContent=d.processes||'--';
    const br=document.getElementById('brain-role');if(br)br.textContent=(d.leon?.brain_role||'unified').toUpperCase();
    const nt=document.getElementById('notif-total');if(nt)nt.textContent=d.leon?.notifications?.total||0;
    const np=document.getElementById('notif-pending');if(np)np.textContent=d.leon?.notifications?.pending||0;
    const sa=document.getElementById('screen-activity');if(sa)sa.textContent=`Activity: ${d.leon?.screen?.activity||'--'}`;
    const sp=document.getElementById('screen-app');if(sp)sp.textContent=`App: ${d.leon?.screen?.active_app||'--'}`;
}

// ── Slash Commands ───────────────────────────────────────
const SLASH_COMMANDS = [
    {cmd:'/agents',desc:'List agents'},{cmd:'/status',desc:'System overview'},{cmd:'/kill',desc:'Kill agent'},
    {cmd:'/queue',desc:'Task queue'},{cmd:'/retry',desc:'Retry agent'},{cmd:'/history',desc:'Recent tasks'},
    {cmd:'/search',desc:'Search history'},{cmd:'/stats',desc:'Agent stats'},{cmd:'/schedule',desc:'Scheduled tasks'},
    {cmd:'/notifications',desc:'Alerts'},{cmd:'/screen',desc:'Screen awareness'},{cmd:'/gpu',desc:'GPU info'},
    {cmd:'/clipboard',desc:'Clipboard'},{cmd:'/changes',desc:'File changes'},{cmd:'/export',desc:'Export chat'},
    {cmd:'/context',desc:'Memory stats'},{cmd:'/bridge',desc:'Right Brain'},{cmd:'/setkey',desc:'Set API key'},
    {cmd:'/vault',desc:'Vault keys'},{cmd:'/approve',desc:'Grant permission'},{cmd:'/voice',desc:'Voice status'},
    {cmd:'/whatsapp',desc:'WhatsApp bridge'},{cmd:'/help',desc:'All commands'},
];

// ── Command Bar ──────────────────────────────────────────
function initCommandBar() {
    const input = document.getElementById('command-input');
    const sendBtn = document.getElementById('command-send');
    if (!input || !sendBtn) return;

    const ac = document.createElement('div');
    ac.id = 'cmd-ac';
    ac.style.cssText = 'display:none;position:absolute;bottom:100%;left:0;right:0;background:rgba(2,8,16,0.96);border:1px solid rgba(0,180,255,0.15);border-radius:8px;padding:4px 0;margin-bottom:4px;font-family:monospace;font-size:11px;z-index:100;max-height:240px;overflow-y:auto;backdrop-filter:blur(16px);';
    const bar = input.closest('#command-bar') || input.parentElement;
    bar.style.position = 'relative';
    bar.appendChild(ac);

    let selIdx = -1;

    function updateAC() {
        const v = input.value;
        if (!v.startsWith('/')) { ac.style.display='none'; selIdx=-1; return; }
        const matches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(v.toLowerCase()));
        if (!matches.length || (matches.length===1 && matches[0].cmd===v)) { ac.style.display='none'; selIdx=-1; return; }
        selIdx = -1;
        ac.innerHTML = matches.map((c,i) => `<div class="ac-item" data-cmd="${c.cmd}" data-i="${i}" style="padding:6px 12px;cursor:pointer;display:flex;justify-content:space-between;gap:12px;border-radius:4px;margin:0 3px;${i===selIdx?'background:rgba(0,180,255,0.1)':''}"><span style="color:#00ccff;font-weight:600">${c.cmd}</span><span style="color:rgba(255,255,255,0.2);font-size:10px">${c.desc}</span></div>`).join('');
        ac.querySelectorAll('.ac-item').forEach(el => {
            el.onmouseenter = () => el.style.background = 'rgba(0,180,255,0.06)';
            el.onmouseleave = () => el.style.background = parseInt(el.dataset.i)===selIdx ? 'rgba(0,180,255,0.1)' : '';
            el.onmousedown = (e) => { e.preventDefault(); input.value=el.dataset.cmd+' '; input.focus(); ac.style.display='none'; };
        });
        ac.style.display = 'block';
    }

    function send() {
        const text = input.value.trim();
        if (!text || text.length > MAX_INPUT_LENGTH) return;
        ac.style.display = 'none';
        if (commandHistory[commandHistory.length-1] !== text) { commandHistory.push(text); if (commandHistory.length>MAX_HISTORY) commandHistory=commandHistory.slice(-MAX_HISTORY); }
        historyIndex = -1;
        appendToFeed(nowTime(), `> ${text}`, 'feed-command');
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            setLoading(true);
            wsConnection.send(JSON.stringify({command:'input',message:text}));
            setTimeout(()=>setLoading(false), 30000);
        } else {
            setTimeout(()=>appendToFeed(nowTime(),`Leon: [Demo] ${text}`,'feed-response'), 300+Math.random()*700);
        }
        input.value = ''; input.focus();
    }

    sendBtn.addEventListener('click', send);
    input.addEventListener('input', updateAC);
    input.addEventListener('blur', () => setTimeout(()=>{ac.style.display='none';selIdx=-1;},150));

    input.addEventListener('keydown', (e) => {
        const items = ac.querySelectorAll('.ac-item');
        if (e.key==='Enter') {
            if (selIdx>=0 && items[selIdx]) { e.preventDefault(); input.value=items[selIdx].dataset.cmd+' '; ac.style.display='none'; selIdx=-1; }
            else send();
            return;
        }
        if (e.key==='Escape') { if(ac.style.display==='block'){ac.style.display='none';selIdx=-1;}else input.blur(); return; }
        if (ac.style.display==='block' && (e.key==='ArrowDown'||e.key==='ArrowUp')) {
            e.preventDefault();
            selIdx = e.key==='ArrowDown' ? (selIdx<items.length-1?selIdx+1:0) : (selIdx>0?selIdx-1:items.length-1);
            items.forEach((it,i) => it.style.background = i===selIdx?'rgba(0,180,255,0.1)':'');
            items[selIdx]?.scrollIntoView({block:'nearest'});
            return;
        }
        if (ac.style.display!=='block' && (e.key==='ArrowUp'||e.key==='ArrowDown')) {
            if (!commandHistory.length) return;
            e.preventDefault();
            if (e.key==='ArrowUp') { if(historyIndex<commandHistory.length-1)historyIndex++; }
            else { if(historyIndex>0)historyIndex--;else{historyIndex=-1;input.value='';return;} }
            input.value = commandHistory[commandHistory.length-1-historyIndex]||'';
            setTimeout(()=>input.setSelectionRange(input.value.length,input.value.length),0);
            return;
        }
        if (e.key==='Tab' && ac.style.display==='block') { e.preventDefault(); const t=selIdx>=0&&items[selIdx]?items[selIdx]:items[0]; if(t){input.value=t.dataset.cmd+' ';ac.style.display='none';selIdx=-1;} }
    });
}

function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const inp = document.getElementById('command-input'); if (!inp) return;
        if (document.activeElement?.id === 'auth-token-input') return;
        if (e.key==='/' && document.activeElement!==inp) { e.preventDefault(); inp.focus(); inp.value='/'; inp.dispatchEvent(new Event('input')); }
        if (e.key==='Escape' && document.activeElement===inp) inp.blur();
    });
}

// ── Feed ─────────────────────────────────────────────────
function appendToFeed(time, msg, cls) {
    const feed = document.getElementById('activity-feed'); if (!feed) return;
    if (!cls) { cls = msg.startsWith('> ')?'feed-command':msg.startsWith('Leon:')?'feed-response':msg.includes('done')?'feed-agent-ok':msg.includes('fail')?'feed-agent-fail':'feed-local'; }
    const div = document.createElement('div');
    div.className = `feed-item ${cls}`;
    div.innerHTML = `<span class="feed-time">${escapeHtml(time)}</span> ${escapeHtml(msg)}`;
    feed.appendChild(div);
    while (feed.children.length > 200) feed.removeChild(feed.firstChild);
    requestAnimationFrame(() => feed.scrollTo({top:feed.scrollHeight,behavior:'smooth'}));
    localFeedItems.push({time,msg}); if(localFeedItems.length>200)localFeedItems=localFeedItems.slice(-100);
}

// ── Start ────────────────────────────────────────────────
init3D();
animate();
window.leonBrain = { getState: () => brainState };
