/**
 * LEON — Jarvis Neural Interface
 * Abstract holographic orb with neural activity + full dashboard controller
 */

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';
import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/UnrealBloomPass.js';

// ═══════════════════════════════════════════════════════
// 3D VISUALIZATION — Holographic neural orb
// ═══════════════════════════════════════════════════════

let scene, camera, renderer, composer, controls;
let coreOrb, outerShell, neuralPoints, rings = [];
let brainActivity = 0, clock = new THREE.Clock();

function init3D() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x020810);

    camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 100);
    camera.position.set(0, 1.2, 6);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(innerWidth, innerHeight);
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    document.getElementById('brain-container').appendChild(renderer.domElement);

    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 1.0, 0.4, 0.8));

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.04;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;
    controls.minDistance = 3.5;
    controls.maxDistance = 10;
    controls.enablePan = false;

    // ── Core orb (inner glowing sphere) ──
    coreOrb = new THREE.Mesh(
        new THREE.SphereGeometry(0.4, 32, 32),
        new THREE.MeshBasicMaterial({ color: 0x00ccff, transparent: true, opacity: 0.15 })
    );
    scene.add(coreOrb);

    // ── Outer shell (transparent wireframe sphere) ──
    const shellGeo = new THREE.IcosahedronGeometry(1.8, 2);
    outerShell = new THREE.LineSegments(
        new THREE.WireframeGeometry(shellGeo),
        new THREE.LineBasicMaterial({ color: 0x00aadd, transparent: true, opacity: 0.06 })
    );
    scene.add(outerShell);

    // ── Neural network (point cloud forming a sphere) ──
    const count = 2000;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const col = new THREE.Color(0x00ccff);

    for (let i = 0; i < count; i++) {
        // Distribute points in a brain-like spherical shape
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 0.6 + Math.random() * 1.0;
        const scaleY = 0.85; // Slightly flattened

        positions[i*3]   = r * Math.sin(phi) * Math.cos(theta);
        positions[i*3+1] = r * Math.sin(phi) * Math.sin(theta) * scaleY;
        positions[i*3+2] = r * Math.cos(phi);

        colors[i*3]   = col.r * (0.5 + Math.random() * 0.5);
        colors[i*3+1] = col.g * (0.5 + Math.random() * 0.5);
        colors[i*3+2] = col.b * (0.5 + Math.random() * 0.5);

        sizes[i] = 0.5 + Math.random() * 2.0;
    }

    const ptGeo = new THREE.BufferGeometry();
    ptGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    ptGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    ptGeo.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const ptMat = new THREE.ShaderMaterial({
        uniforms: { uTime: { value: 0 }, uActivity: { value: 0 } },
        vertexShader: `
            attribute float size;
            attribute vec3 color;
            varying vec3 vColor;
            varying float vAlpha;
            uniform float uTime;
            uniform float uActivity;
            void main() {
                vColor = color;
                vec4 mv = modelViewMatrix * vec4(position, 1.0);
                float dist = length(position);
                float pulse = 1.0 + 0.3 * sin(uTime * 2.0 + dist * 4.0) * (0.3 + uActivity);
                gl_PointSize = size * pulse * (100.0 / -mv.z);
                vAlpha = 0.15 + 0.25 * sin(uTime * 1.5 + position.y * 3.0 + position.x * 2.0);
                vAlpha *= (0.5 + uActivity * 0.5);
                gl_Position = projectionMatrix * mv;
            }
        `,
        fragmentShader: `
            varying vec3 vColor;
            varying float vAlpha;
            void main() {
                float d = length(gl_PointCoord - vec2(0.5));
                if (d > 0.5) discard;
                float glow = exp(-d * 4.0);
                gl_FragColor = vec4(vColor * (0.8 + glow * 0.4), vAlpha * glow);
            }
        `,
        transparent: true, depthWrite: false, vertexColors: true,
    });

    neuralPoints = new THREE.Points(ptGeo, ptMat);
    scene.add(neuralPoints);

    // ── Rings ──
    [
        { r: 2.2, arc: 1.4, speed: 0.1, tilt: 0.1, opacity: 0.15 },
        { r: 2.5, arc: 1.1, speed: -0.07, tilt: -0.15, opacity: 0.1 },
        { r: 2.8, arc: 0.7, speed: 0.04, tilt: 0.25, opacity: 0.08 },
    ].forEach(c => {
        const mesh = new THREE.Mesh(
            new THREE.TorusGeometry(c.r, 0.005, 4, 80, Math.PI * c.arc),
            new THREE.MeshBasicMaterial({ color: 0x0099cc, transparent: true, opacity: c.opacity })
        );
        mesh.rotation.set(Math.PI/2 + c.tilt, 0, Math.random() * 6.28);
        mesh.userData = { speed: c.speed, baseOp: c.opacity };
        scene.add(mesh);
        rings.push(mesh);
    });

    // Light
    scene.add(new THREE.PointLight(0x00bbff, 0.4, 6));

    addEventListener('resize', () => {
        camera.aspect = innerWidth / innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(innerWidth, innerHeight);
        composer.setSize(innerWidth, innerHeight);
    });
}

function animate3D() {
    requestAnimationFrame(animate3D);
    const t = clock.getElapsedTime();
    const target = Math.min(1, (brainState.agentCount || 0) / 3);
    brainActivity += (target - brainActivity) * 0.02;

    // Neural points animation
    if (neuralPoints) {
        neuralPoints.material.uniforms.uTime.value = t;
        neuralPoints.material.uniforms.uActivity.value = brainActivity;
        neuralPoints.rotation.y = t * 0.03;
    }

    // Core pulse
    if (coreOrb) {
        const s = 0.35 + 0.08 * Math.sin(t * 2) + brainActivity * 0.1;
        coreOrb.scale.setScalar(s / 0.4);
        coreOrb.material.opacity = 0.1 + 0.1 * brainActivity;
    }

    // Outer shell breathe
    if (outerShell) {
        outerShell.rotation.y = t * 0.02;
        outerShell.rotation.x = Math.sin(t * 0.1) * 0.05;
        outerShell.material.opacity = 0.04 + 0.04 * brainActivity;
    }

    // Rings
    for (const r of rings) {
        r.rotation.z += r.userData.speed * 0.008;
        r.material.opacity = r.userData.baseOp * (0.5 + brainActivity);
    }

    controls.update();
    composer.render();
}

// ═══════════════════════════════════════════════════════
// DASHBOARD CONTROLLER
// ═══════════════════════════════════════════════════════

let brainState = {
    leftActive: true, rightActive: false, activeAgents: [], agentCount: 0,
    taskCount: 0, completedCount: 0, queuedCount: 0, uptime: 0, voice: {},
};
let wsConnection = null, localFeedItems = [], demoUptimeStart = Date.now();
let wsReconnectDelay = 1000, wsReconnectTimer = null, wsAuthenticated = false;
let commandHistory = [], historyIndex = -1;
let isWaitingResponse = false, demoInterval = null, demoCompletedCount = 0;

// ── Auth ──
function getStoredToken() { return localStorage.getItem('leon_session_token') || ''; }
function setStoredToken(t) { localStorage.setItem('leon_session_token', t); }

function showAuthOverlay(msg) {
    const o = document.getElementById('auth-overlay'); if (!o) return;
    o.style.display = 'flex';
    const e = document.getElementById('auth-error'); if (e) e.textContent = msg || '';
    const inp = document.getElementById('auth-token-input');
    const btn = document.getElementById('auth-submit');
    if (!inp || !btn) return;
    setTimeout(() => inp.focus(), 100);
    function doAuth() { const tk = inp.value.trim(); if (!tk) return; setStoredToken(tk); o.style.display='none'; wsConnection ? wsConnection.close() : connectWS(); }
    btn.onclick = doAuth;
    inp.onkeydown = (e) => { if (e.key==='Enter') doAuth(); };
}
function hideAuthOverlay() { const o = document.getElementById('auth-overlay'); if (o) o.style.display = 'none'; }

function setStatus(s) {
    const dot = document.getElementById('ws-dot'), txt = document.getElementById('ws-status-text');
    if (!dot || !txt) return;
    const ind = document.getElementById('ws-indicator');
    dot.classList.remove('pulse','disconnected','reconnecting');
    if (ind) ind.classList.remove('disconnected','reconnecting');
    if (s==='connected') { dot.classList.add('pulse'); dot.style.background=''; txt.textContent='ONLINE'; }
    else if (s==='disconnected') { dot.classList.add('disconnected'); txt.textContent='OFFLINE'; if(ind)ind.classList.add('disconnected'); }
    else if (s==='reconnecting') { dot.classList.add('reconnecting'); txt.textContent='RECONNECTING'; if(ind)ind.classList.add('reconnecting'); }
    else if (s==='demo') { dot.classList.add('pulse'); dot.style.background='#ffaa00'; txt.textContent='DEMO'; }
}
function setLoading(a) { const l = document.getElementById('command-loading'); if(l) l.classList.toggle('active', a); }

// ── WebSocket ──
function connectWS() {
    const token = getStoredToken();
    if (!token) { showAuthOverlay(''); setStatus('demo'); startDemo(); return; }
    setStatus('reconnecting');
    try {
        const ws = new WebSocket(`ws://${location.host}/ws`);
        wsConnection = ws; wsAuthenticated = false;
        ws.onopen = () => { wsReconnectDelay=1000; ws.send(JSON.stringify({command:'auth',token})); };
        ws.onmessage = (ev) => {
            let d; try{d=JSON.parse(ev.data);}catch{return;}
            if(d.type==='auth_result'){if(d.success){wsAuthenticated=true;hideAuthOverlay();setStatus('connected');if(demoInterval){clearInterval(demoInterval);demoInterval=null;}}else{wsAuthenticated=false;localStorage.removeItem('leon_session_token');setStatus('disconnected');showAuthOverlay(d.message||'Auth failed');}return;}
            if(d.type==='input_response'){setLoading(false);feed(d.timestamp||now(),`Leon: ${d.message}`,'feed-response');return;}
            if(d.type==='agent_completed'){feed(now(),`Agent #${(d.agent_id||'').slice(-8)} done: ${d.summary||''}`,'feed-agent-ok');return;}
            if(d.type==='agent_failed'){feed(now(),`Agent #${(d.agent_id||'').slice(-8)} failed: ${d.error||''}`,'feed-agent-fail');return;}
            brainState={...brainState,...d};updateUI();
        };
        ws.onclose = () => { wsConnection=null;wsAuthenticated=false;setStatus('reconnecting');wsReconnectTimer=setTimeout(connectWS,wsReconnectDelay);wsReconnectDelay=Math.min(wsReconnectDelay*2,30000); };
        ws.onerror = () => { wsConnection=null;wsAuthenticated=false;setStatus('demo');startDemo(); };
    } catch { setStatus('demo'); startDemo(); }
}

// ── Demo ──
function startDemo() {
    if (demoInterval) return;
    let agents=[];
    demoInterval = setInterval(() => {
        if(Math.random()>0.5&&agents.length<3) agents.push({description:'Working on task',project:'leon',startedAt:new Date().toISOString()});
        else if(agents.length>0&&Math.random()>0.6){agents.pop();demoCompletedCount++;}
        brainState={...brainState,leftActive:true,rightActive:agents.length>0,activeAgents:agents,agentCount:agents.length,taskCount:agents.length+2,completedCount:demoCompletedCount,queuedCount:Math.floor(Math.random()*3),uptime:Math.floor((Date.now()-demoUptimeStart)/1000)};
        updateUI();
    }, 2000);
}

// ── Helpers ──
function now() { const d=new Date(); return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0'); }
function fmt(s) { return `${Math.floor(s/3600).toString().padStart(2,'0')}:${Math.floor((s%3600)/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`; }
function esc(s) { const d=document.createElement('div');d.textContent=s;return d.innerHTML; }

// ── UI ──
function updateUI() {
    const el = (id) => document.getElementById(id);
    const set = (id,v) => { const e=el(id); if(e) e.textContent=v; };

    set('left-status', brainState.leftActive?'ACTIVE':'IDLE');
    const ls=el('left-status');if(ls)ls.className='card-val '+(brainState.leftActive?'active':'idle');
    set('right-status', brainState.rightActive?'ACTIVE':'IDLE');
    const rs=el('right-status');if(rs)rs.className='card-val '+(brainState.rightActive?'active':'idle');

    set('agent-count', brainState.agentCount||0);
    set('task-count', brainState.taskCount||0);
    set('stat-uptime', fmt(brainState.uptime||0));
    set('stat-completed', brainState.completedCount||0);
    set('stat-queued', brainState.queuedCount||0);

    // Agents list
    const al=el('agents-list');
    if(al){
        const agents=brainState.activeAgents||[];
        if(!agents.length){al.innerHTML='<div class="agents-empty">IDLE</div>';}
        else{al.innerHTML=agents.map(a=>{let e='';if(a.startedAt){const s=Math.max(0,Math.floor((Date.now()-new Date(a.startedAt).getTime())/1000));e=`${Math.floor(s/60)}m${(s%60).toString().padStart(2,'0')}s`;}return`<div class="agent-card"><span class="agent-desc">${esc((a.description||'...').substring(0,22))}</span>${e?`<span class="agent-elapsed">${e}</span>`:''}</div>`;}).join('');}
    }

    // Voice
    const ve=el('voice-state');
    if(ve){const v=brainState.voice||{};if(!v.active){ve.textContent='MIC OFF';ve.classList.remove('active');}else{const l={idle:'MIC IDLE',listening:'LISTENING',awake:'AWAKE',processing:'THINKING',speaking:'SPEAKING'};ve.textContent=l[v.state]||'MIC';ve.classList.toggle('active',['listening','awake','speaking'].includes(v.state));}}
}

// ── Health Polling ──
function startHealth() { pollHealth(); setInterval(pollHealth, 5000); }
async function pollHealth() {
    try {
        const r = await fetch('/api/health'); if(!r.ok)return;
        const d = await r.json();
        const C=97.4;

        gauge('gauge-cpu','gauge-cpu-val',parseFloat(d.cpu)||0,C);
        gauge('gauge-mem','gauge-mem-val',d.memory?.percent?parseFloat(d.memory.percent):0,C);
        gauge('gauge-disk','gauge-disk-val',d.disk?.percent?parseFloat(d.disk.percent):0,C);
        gauge('gauge-gpu','gauge-gpu-val',d.gpu?.usage?parseFloat(d.gpu.usage):0,C);

        const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v;};
        const html=(id,v)=>{const e=document.getElementById(id);if(e)e.innerHTML=v;};
        if(d.memory)set('mem-detail',`${(d.memory.used_mb/1024).toFixed(1)} / ${(d.memory.total_mb/1024).toFixed(1)} GB`);
        if(d.gpu){set('gpu-name',d.gpu.name||'--');set('gpu-temp',d.gpu.temp||'--');set('gpu-vram',`VRAM: ${d.gpu.vram_used||'--'} / ${d.gpu.vram_total||'--'}`);}
        if(d.disk)set('disk-detail',`${d.disk.used_gb} / ${d.disk.total_gb} GB`);
        const net=d.network?Object.values(d.network)[0]:null;
        if(net){html('net-rx',`&#x2193; ${net.rx_gb} GB`);html('net-tx',`&#x2191; ${net.tx_gb} GB`);}
        set('stat-load',d.load_avg||'--');
        set('stat-proc',d.processes||'--');
        if(d.leon){
            set('brain-role',(d.leon.brain_role||'unified').toUpperCase());
            set('notif-total',d.leon.notifications?.total||0);
            set('notif-pending',d.leon.notifications?.pending||0);
            set('screen-activity',`Activity: ${d.leon.screen?.activity||'--'}`);
            set('screen-app',`App: ${d.leon.screen?.active_app||'--'}`);
        }
    } catch {}
}
function gauge(cid,vid,pct,C) {
    const c=document.getElementById(cid),v=document.getElementById(vid);if(!c||!v)return;
    c.style.strokeDashoffset=C-(C*Math.min(pct,100)/100);
    v.textContent=Math.round(pct)+'%';
    c.classList.remove('warn','critical');
    if(pct>90)c.classList.add('critical');else if(pct>75)c.classList.add('warn');
}

// ── Commands ──
const CMDS=['/agents','/status','/kill','/queue','/retry','/history','/search','/stats','/schedule','/notifications','/screen','/gpu','/clipboard','/changes','/export','/context','/bridge','/setkey','/vault','/approve','/voice','/whatsapp','/help'];

function initCmd() {
    const input=document.getElementById('command-input'),btn=document.getElementById('command-send');
    if(!input||!btn)return;

    const ac=document.createElement('div');
    ac.style.cssText='display:none;position:absolute;bottom:100%;left:0;right:0;background:rgba(2,8,16,0.96);border:1px solid rgba(0,160,220,0.15);border-radius:8px;padding:4px 0;margin-bottom:4px;font-size:11px;z-index:100;max-height:200px;overflow-y:auto;backdrop-filter:blur(16px);';
    const bar=document.getElementById('command-bar');
    bar.appendChild(ac);

    let sel=-1;
    function showAC(){
        const v=input.value;if(!v.startsWith('/')){ac.style.display='none';sel=-1;return;}
        const m=CMDS.filter(c=>c.startsWith(v.toLowerCase()));
        if(!m.length){ac.style.display='none';sel=-1;return;}
        sel=-1;
        ac.innerHTML=m.map((c,i)=>`<div data-c="${c}" style="padding:5px 10px;cursor:pointer;color:#00ccff;font-family:monospace;font-size:11px;border-radius:4px;margin:0 3px" onmouseenter="this.style.background='rgba(0,160,220,0.08)'" onmouseleave="this.style.background=''" onmousedown="event.preventDefault();document.getElementById('command-input').value='${c} ';document.getElementById('command-input').focus();this.parentElement.style.display='none'">${c}</div>`).join('');
        ac.style.display='block';
    }

    function send(){
        const text=input.value.trim();if(!text||text.length>2000)return;
        ac.style.display='none';
        if(commandHistory[commandHistory.length-1]!==text){commandHistory.push(text);if(commandHistory.length>50)commandHistory=commandHistory.slice(-50);}
        historyIndex=-1;
        feed(now(),`> ${text}`,'feed-command');
        if(wsConnection&&wsConnection.readyState===WebSocket.OPEN){setLoading(true);wsConnection.send(JSON.stringify({command:'input',message:text}));setTimeout(()=>setLoading(false),30000);}
        else setTimeout(()=>feed(now(),`Leon: [Demo] ${text}`,'feed-response'),500);
        input.value='';input.focus();
    }

    btn.onclick=send;
    input.addEventListener('input',showAC);
    input.addEventListener('blur',()=>setTimeout(()=>{ac.style.display='none';sel=-1;},150));
    input.addEventListener('keydown',(e)=>{
        if(e.key==='Enter'){send();return;}
        if(e.key==='Escape'){ac.style.display='none';input.blur();return;}
        if(ac.style.display!=='block'&&(e.key==='ArrowUp'||e.key==='ArrowDown')){
            if(!commandHistory.length)return;e.preventDefault();
            if(e.key==='ArrowUp'){if(historyIndex<commandHistory.length-1)historyIndex++;}else{if(historyIndex>0)historyIndex--;else{historyIndex=-1;input.value='';return;}}
            input.value=commandHistory[commandHistory.length-1-historyIndex]||'';
        }
    });

    // Global / shortcut
    document.addEventListener('keydown',(e)=>{
        if(document.activeElement?.id==='auth-token-input')return;
        if(e.key==='/'&&document.activeElement!==input){e.preventDefault();input.focus();input.value='/';input.dispatchEvent(new Event('input'));}
    });
}

// ── Feed ──
function feed(time,msg,cls){
    const f=document.getElementById('activity-feed');if(!f)return;
    if(!cls)cls=msg.startsWith('> ')?'feed-command':msg.startsWith('Leon:')?'feed-response':'feed-local';
    const div=document.createElement('div');
    div.className=`feed-item ${cls}`;
    div.innerHTML=`<span class="feed-time">${esc(time)}</span> ${esc(msg)}`;
    f.appendChild(div);
    while(f.children.length>200)f.removeChild(f.firstChild);
    requestAnimationFrame(()=>f.scrollTo({top:f.scrollHeight,behavior:'smooth'}));
}

// ── Start ──
init3D();
animate3D();
connectWS();
initCmd();
startHealth();
