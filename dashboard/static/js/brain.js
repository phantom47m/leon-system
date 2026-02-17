/**
 * LEON BRAIN — 3D Neural Visualization
 * TRON / Iron Man Jarvis style holographic brain
 *
 * Two hemispheres made of glowing particle clouds connected
 * by pulsing neural pathways floating in 3D space.
 */

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';
import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/UnrealBloomPass.js';

// ── CONFIG ──────────────────────────────────────────────
const CFG = {
    // Brain shape
    LEFT_CENTER:  new THREE.Vector3(-1.8, 0, 0),
    RIGHT_CENTER: new THREE.Vector3( 1.8, 0, 0),
    HEMISPHERE_RADIUS: 1.6,

    // Particles
    NODE_COUNT: 220,          // nodes per hemisphere
    BRIDGE_PARTICLE_COUNT: 80,
    AMBIENT_PARTICLE_COUNT: 600,

    // Colors
    LEFT_COLOR:   new THREE.Color(0x4fc3f7),  // cyan/blue
    RIGHT_COLOR:  new THREE.Color(0xff7043),  // orange
    BRIDGE_COLOR: new THREE.Color(0xab47bc),  // purple
    ACTIVE_COLOR: new THREE.Color(0x76ff03),  // bright green pulse
    BG_COLOR:     0x0a0a1a,

    // Animation
    ROTATION_SPEED: 0.0003,
    PULSE_SPEED: 0.02,
    SIGNAL_SPEED: 0.008,
};

// ── GLOBALS ─────────────────────────────────────────────
let scene, camera, renderer, composer, controls;
let leftNodes = [], rightNodes = [], bridgeParticles = [];
let connections = [], signals = [];
let ambientParticles;
let clock = new THREE.Clock();
let brainState = {
    leftActive: true,
    rightActive: false,
    bridgeActive: false,
    activeAgents: [],
    agentCount: 0,
    taskCount: 0,
    completedCount: 0,
    queuedCount: 0,
    maxConcurrent: 5,
    uptime: 0,
    taskFeed: [],
};

// Module-level WebSocket reference for command sending
let wsConnection = null;

// Local feed items (commands + responses) merged with server items
let localFeedItems = [];

// Demo mode uptime counter
let demoUptimeStart = Date.now();

// WebSocket reconnect backoff
let wsReconnectDelay = 1000; // starts at 1s, grows exponentially
const WS_MAX_RECONNECT_DELAY = 30000;

// Max command input length
const MAX_INPUT_LENGTH = 2000;

// ── INIT ────────────────────────────────────────────────
function init() {
    // Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color(CFG.BG_COLOR);
    scene.fog = new THREE.FogExp2(CFG.BG_COLOR, 0.04);

    // Camera
    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.set(0, 2, 7);
    camera.lookAt(0, 0, 0);

    // Renderer
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ReinhardToneMapping;
    renderer.toneMappingExposure = 1.5;
    document.getElementById('brain-container').appendChild(renderer.domElement);

    // Post-processing (bloom glow)
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));

    const bloomPass = new UnrealBloomPass(
        new THREE.Vector2(window.innerWidth, window.innerHeight),
        1.5,   // strength
        0.4,   // radius
        0.85   // threshold
    );
    composer.addPass(bloomPass);

    // Controls
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;
    controls.minDistance = 3;
    controls.maxDistance = 15;

    // Build the brain
    createHemisphere('left');
    createHemisphere('right');
    createNeuralBridge();
    createConnections('left');
    createConnections('right');
    createAmbientParticles();
    createCoreGlow('left');
    createCoreGlow('right');

    // Lights
    const ambientLight = new THREE.AmbientLight(0x111122, 0.5);
    scene.add(ambientLight);

    // Events
    window.addEventListener('resize', onResize);

    // WebSocket for live brain data
    connectWebSocket();

    // Initialize command bar
    initCommandBar();

    // Start
    animate();
}

// ── HEMISPHERE ──────────────────────────────────────────
function createHemisphere(side) {
    const center = side === 'left' ? CFG.LEFT_CENTER : CFG.RIGHT_CENTER;
    const color = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;
    const nodes = side === 'left' ? leftNodes : rightNodes;

    // Create nodes in a brain-like ellipsoid shape
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(CFG.NODE_COUNT * 3);
    const colors = new Float32Array(CFG.NODE_COUNT * 3);
    const sizes = new Float32Array(CFG.NODE_COUNT);
    const phases = new Float32Array(CFG.NODE_COUNT); // for individual pulse timing

    for (let i = 0; i < CFG.NODE_COUNT; i++) {
        // Brain-shaped distribution (ellipsoid with wrinkle noise)
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = CFG.HEMISPHERE_RADIUS * (0.5 + 0.5 * Math.random());

        // Ellipsoid scaling (wider than tall, flatter on bridge side)
        const scaleX = 0.9;
        const scaleY = 1.1;
        const scaleZ = 1.0;

        // Add organic noise for brain wrinkle effect
        const noise = 0.15 * Math.sin(theta * 5) * Math.cos(phi * 3);

        const x = center.x + (r + noise) * Math.sin(phi) * Math.cos(theta) * scaleX;
        const y = center.y + (r + noise) * Math.sin(phi) * Math.sin(theta) * scaleY;
        const z = center.z + (r + noise) * Math.cos(phi) * scaleZ;

        positions[i * 3]     = x;
        positions[i * 3 + 1] = y;
        positions[i * 3 + 2] = z;

        colors[i * 3]     = color.r;
        colors[i * 3 + 1] = color.g;
        colors[i * 3 + 2] = color.b;

        sizes[i] = 2 + Math.random() * 4;
        phases[i] = Math.random() * Math.PI * 2;

        nodes.push({
            index: i,
            position: new THREE.Vector3(x, y, z),
            baseSize: sizes[i],
            phase: phases[i],
            active: false,
        });
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
        uniforms: {
            uTime: { value: 0 },
            uActiveColor: { value: CFG.ACTIVE_COLOR },
        },
        vertexShader: `
            attribute float size;
            attribute vec3 color;
            varying vec3 vColor;
            uniform float uTime;

            void main() {
                vColor = color;
                vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
                float pulse = 1.0 + 0.3 * sin(uTime * 2.0 + position.x * 3.0);
                gl_PointSize = size * pulse * (200.0 / -mvPosition.z);
                gl_Position = projectionMatrix * mvPosition;
            }
        `,
        fragmentShader: `
            varying vec3 vColor;

            void main() {
                float dist = length(gl_PointCoord - vec2(0.5));
                if (dist > 0.5) discard;

                // Soft glow
                float alpha = 1.0 - smoothstep(0.0, 0.5, dist);
                float glow = exp(-dist * 4.0);

                vec3 finalColor = vColor * (0.8 + glow * 0.5);
                gl_FragColor = vec4(finalColor, alpha * 0.85);
            }
        `,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });

    const points = new THREE.Points(geometry, material);
    points.userData = { side, material };
    scene.add(points);
}

// ── NEURAL BRIDGE ───────────────────────────────────────
function createNeuralBridge() {
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(CFG.BRIDGE_PARTICLE_COUNT * 3);
    const colors = new Float32Array(CFG.BRIDGE_PARTICLE_COUNT * 3);
    const sizes = new Float32Array(CFG.BRIDGE_PARTICLE_COUNT);

    for (let i = 0; i < CFG.BRIDGE_PARTICLE_COUNT; i++) {
        // Particles flowing between hemispheres
        const t = Math.random();
        const x = THREE.MathUtils.lerp(CFG.LEFT_CENTER.x + 0.8, CFG.RIGHT_CENTER.x - 0.8, t);
        const spread = 0.5 * Math.sin(t * Math.PI); // wider in middle
        const y = (Math.random() - 0.5) * spread;
        const z = (Math.random() - 0.5) * spread;

        positions[i * 3]     = x;
        positions[i * 3 + 1] = y;
        positions[i * 3 + 2] = z;

        colors[i * 3]     = CFG.BRIDGE_COLOR.r;
        colors[i * 3 + 1] = CFG.BRIDGE_COLOR.g;
        colors[i * 3 + 2] = CFG.BRIDGE_COLOR.b;

        sizes[i] = 1.5 + Math.random() * 3;

        bridgeParticles.push({
            index: i,
            t: t,           // position along bridge (0=left, 1=right)
            speed: 0.001 + Math.random() * 0.003,
            baseY: y,
            baseZ: z,
        });
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
        uniforms: { uTime: { value: 0 } },
        vertexShader: `
            attribute float size;
            attribute vec3 color;
            varying vec3 vColor;
            varying float vAlpha;
            uniform float uTime;

            void main() {
                vColor = color;
                vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
                float pulse = 1.0 + 0.5 * sin(uTime * 3.0 + position.x * 5.0);
                gl_PointSize = size * pulse * (200.0 / -mvPosition.z);
                vAlpha = 0.4 + 0.4 * sin(uTime * 2.0 + position.x * 8.0);
                gl_Position = projectionMatrix * mvPosition;
            }
        `,
        fragmentShader: `
            varying vec3 vColor;
            varying float vAlpha;

            void main() {
                float dist = length(gl_PointCoord - vec2(0.5));
                if (dist > 0.5) discard;
                float glow = exp(-dist * 3.0);
                gl_FragColor = vec4(vColor * (1.0 + glow), vAlpha * (1.0 - dist * 2.0));
            }
        `,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });

    const points = new THREE.Points(geometry, material);
    points.userData = { type: 'bridge', material };
    scene.add(points);
}

// ── CONNECTIONS (lines between nearby nodes) ────────────
function createConnections(side) {
    const nodes = side === 'left' ? leftNodes : rightNodes;
    const color = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;
    const maxDist = 0.8;

    const linePositions = [];

    for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
            const dist = nodes[i].position.distanceTo(nodes[j].position);
            if (dist < maxDist && Math.random() < 0.3) {
                linePositions.push(
                    nodes[i].position.x, nodes[i].position.y, nodes[i].position.z,
                    nodes[j].position.x, nodes[j].position.y, nodes[j].position.z
                );
                connections.push({ from: i, to: j, side, dist });
            }
        }
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(linePositions, 3));

    const material = new THREE.LineBasicMaterial({
        color: color,
        transparent: true,
        opacity: 0.08,
        blending: THREE.AdditiveBlending,
    });

    const lines = new THREE.LineSegments(geometry, material);
    lines.userData = { side };
    scene.add(lines);
}

// ── CORE GLOW (center of each hemisphere) ───────────────
function createCoreGlow(side) {
    const center = side === 'left' ? CFG.LEFT_CENTER : CFG.RIGHT_CENTER;
    const color = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;

    // Inner sphere
    const coreGeo = new THREE.SphereGeometry(0.15, 16, 16);
    const coreMat = new THREE.MeshBasicMaterial({
        color: color,
        transparent: true,
        opacity: 0.6,
    });
    const core = new THREE.Mesh(coreGeo, coreMat);
    core.position.copy(center);
    core.userData = { side, type: 'core' };
    scene.add(core);

    // Outer glow sphere
    const glowGeo = new THREE.SphereGeometry(0.4, 16, 16);
    const glowMat = new THREE.MeshBasicMaterial({
        color: color,
        transparent: true,
        opacity: 0.1,
        side: THREE.BackSide,
    });
    const glow = new THREE.Mesh(glowGeo, glowMat);
    glow.position.copy(center);
    glow.userData = { side, type: 'coreGlow' };
    scene.add(glow);

    // Point light
    const light = new THREE.PointLight(color, 0.5, 4);
    light.position.copy(center);
    scene.add(light);
}

// ── AMBIENT FLOATING PARTICLES ──────────────────────────
function createAmbientParticles() {
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(CFG.AMBIENT_PARTICLE_COUNT * 3);
    const sizes = new Float32Array(CFG.AMBIENT_PARTICLE_COUNT);

    for (let i = 0; i < CFG.AMBIENT_PARTICLE_COUNT; i++) {
        positions[i * 3]     = (Math.random() - 0.5) * 20;
        positions[i * 3 + 1] = (Math.random() - 0.5) * 12;
        positions[i * 3 + 2] = (Math.random() - 0.5) * 12;
        sizes[i] = 0.5 + Math.random() * 1.5;
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
        uniforms: { uTime: { value: 0 } },
        vertexShader: `
            attribute float size;
            uniform float uTime;
            varying float vAlpha;

            void main() {
                vec3 pos = position;
                pos.y += sin(uTime * 0.5 + position.x) * 0.1;
                pos.x += cos(uTime * 0.3 + position.z) * 0.05;

                vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
                gl_PointSize = size * (150.0 / -mvPosition.z);
                vAlpha = 0.1 + 0.1 * sin(uTime + position.x * 2.0);
                gl_Position = projectionMatrix * mvPosition;
            }
        `,
        fragmentShader: `
            varying float vAlpha;

            void main() {
                float dist = length(gl_PointCoord - vec2(0.5));
                if (dist > 0.5) discard;
                gl_FragColor = vec4(0.3, 0.3, 0.5, vAlpha * (1.0 - dist * 2.0));
            }
        `,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });

    ambientParticles = new THREE.Points(geometry, material);
    scene.add(ambientParticles);
}

// ── SIGNAL PULSE (traveling spark along bridge) ─────────
function fireSignal(direction) {
    // direction: 'left-to-right' or 'right-to-left'
    const start = direction === 'left-to-right' ? CFG.LEFT_CENTER.clone() : CFG.RIGHT_CENTER.clone();
    const end = direction === 'left-to-right' ? CFG.RIGHT_CENTER.clone() : CFG.LEFT_CENTER.clone();

    const geometry = new THREE.SphereGeometry(0.08, 8, 8);
    const material = new THREE.MeshBasicMaterial({
        color: CFG.ACTIVE_COLOR,
        transparent: true,
        opacity: 1.0,
    });
    const signal = new THREE.Mesh(geometry, material);
    signal.position.copy(start);

    // Trail light
    const light = new THREE.PointLight(CFG.ACTIVE_COLOR, 1, 2);
    signal.add(light);

    scene.add(signal);
    signals.push({
        mesh: signal,
        start: start,
        end: end,
        t: 0,
        speed: CFG.SIGNAL_SPEED + Math.random() * 0.005,
    });
}

// ── ANIMATE ─────────────────────────────────────────────
function animate() {
    requestAnimationFrame(animate);

    const time = clock.getElapsedTime();

    // Update all shader uniforms
    scene.traverse((obj) => {
        if (obj.userData?.material?.uniforms?.uTime) {
            obj.userData.material.uniforms.uTime.value = time;
        }
        if (obj.isPoints && obj.material?.uniforms?.uTime) {
            obj.material.uniforms.uTime.value = time;
        }

        // Pulse core glows
        if (obj.userData?.type === 'core') {
            const pulse = 0.5 + 0.3 * Math.sin(time * 2 + (obj.userData.side === 'left' ? 0 : Math.PI));
            obj.material.opacity = pulse;
            obj.scale.setScalar(0.8 + 0.4 * Math.sin(time * 1.5));
        }
        if (obj.userData?.type === 'coreGlow') {
            obj.scale.setScalar(1 + 0.3 * Math.sin(time * 1.2));
        }
    });

    // Animate bridge particles flowing
    scene.traverse((obj) => {
        if (obj.userData?.type === 'bridge' && obj.isPoints) {
            const positions = obj.geometry.attributes.position.array;
            for (let i = 0; i < bridgeParticles.length; i++) {
                const p = bridgeParticles[i];
                p.t += p.speed;
                if (p.t > 1) p.t = 0;
                if (p.t < 0) p.t = 1;

                const x = THREE.MathUtils.lerp(CFG.LEFT_CENTER.x + 0.8, CFG.RIGHT_CENTER.x - 0.8, p.t);
                const wave = Math.sin(p.t * Math.PI);
                const y = p.baseY + 0.2 * Math.sin(time * 2 + i) * wave;
                const z = p.baseZ + 0.2 * Math.cos(time * 2 + i) * wave;

                positions[i * 3]     = x;
                positions[i * 3 + 1] = y;
                positions[i * 3 + 2] = z;
            }
            obj.geometry.attributes.position.needsUpdate = true;
        }
    });

    // Animate signals traveling along bridge
    for (let i = signals.length - 1; i >= 0; i--) {
        const s = signals[i];
        s.t += s.speed;
        if (s.t >= 1) {
            scene.remove(s.mesh);
            signals.splice(i, 1);
            continue;
        }
        // Curved path
        s.mesh.position.lerpVectors(s.start, s.end, s.t);
        s.mesh.position.y += 0.5 * Math.sin(s.t * Math.PI);
        s.mesh.material.opacity = 1 - s.t * 0.5;
    }

    // Ambient particle drift
    if (ambientParticles) {
        ambientParticles.material.uniforms.uTime.value = time;
    }

    // Randomly fire signals across bridge
    if (Math.random() < 0.01 && brainState.bridgeActive) {
        fireSignal(Math.random() > 0.5 ? 'left-to-right' : 'right-to-left');
    }

    // Auto-fire when active
    if (brainState.leftActive && brainState.rightActive && Math.random() < 0.03) {
        fireSignal(Math.random() > 0.5 ? 'left-to-right' : 'right-to-left');
    }

    controls.update();
    composer.render();
}

// ── WEBSOCKET ───────────────────────────────────────────
function connectWebSocket() {
    try {
        const ws = new WebSocket(`ws://${window.location.host}/ws`);
        wsConnection = ws;

        ws.onopen = () => {
            wsReconnectDelay = 1000; // Reset backoff on successful connect
            // Stop demo mode if it was running
            if (demoInterval) {
                clearInterval(demoInterval);
                demoInterval = null;
            }
        };

        ws.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                return; // Ignore malformed messages
            }
            // Handle input responses separately
            if (data.type === 'input_response') {
                appendToFeed(data.timestamp || nowTime(), `Leon: ${escapeHtml(data.message)}`);
                return;
            }
            updateBrainState(data);
        };

        ws.onclose = () => {
            wsConnection = null;
            // Reconnect with exponential backoff
            setTimeout(connectWebSocket, wsReconnectDelay);
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_MAX_RECONNECT_DELAY);
        };

        ws.onerror = () => {
            wsConnection = null;
            // Run in demo mode if no server
            startDemoMode();
        };
    } catch (e) {
        // Demo mode
        startDemoMode();
    }
}

// ── DEMO MODE ───────────────────────────────────────────
let demoInterval = null;
let demoCompletedCount = 0;

const demoAgentPool = [
    { description: 'Scanning project dependencies', project: 'leon-system', type: 'scanner' },
    { description: 'Running test suite', project: 'openclaw', type: 'tester' },
    { description: 'Analyzing code patterns', project: 'leon-system', type: 'analyzer' },
    { description: 'Deploying service update', project: 'dashboard', type: 'deployer' },
    { description: 'Monitoring system health', project: 'infra', type: 'monitor' },
    { description: 'Indexing documentation', project: 'docs', type: 'indexer' },
];

function startDemoMode() {
    if (demoInterval) return; // Already running

    let demoAgents = [];
    let demoQueued = 2;

    demoInterval = setInterval(() => {
        // Randomly add/remove agents
        if (Math.random() > 0.5 && demoAgents.length < 4) {
            const pool = demoAgentPool.filter(a => !demoAgents.some(d => d.description === a.description));
            if (pool.length > 0) {
                const agent = { ...pool[Math.floor(Math.random() * pool.length)] };
                agent.startedAt = new Date(Date.now() - Math.floor(Math.random() * 120000)).toISOString();
                demoAgents.push(agent);
            }
        } else if (demoAgents.length > 0 && Math.random() > 0.6) {
            demoAgents.pop();
            demoCompletedCount++;
        }

        demoQueued = Math.floor(Math.random() * 5);
        const uptimeSeconds = Math.floor((Date.now() - demoUptimeStart) / 1000);

        brainState.leftActive = true;
        brainState.rightActive = demoAgents.length > 0 || Math.random() > 0.3;
        brainState.bridgeActive = brainState.rightActive;
        brainState.activeAgents = demoAgents;
        brainState.agentCount = demoAgents.length;
        brainState.taskCount = demoAgents.length + demoQueued;
        brainState.completedCount = demoCompletedCount;
        brainState.queuedCount = demoQueued;
        brainState.maxConcurrent = 5;
        brainState.uptime = uptimeSeconds;

        // Generate demo feed items from server perspective
        const now = nowTime();
        brainState.taskFeed = demoAgents.map(a => ({
            time: now,
            message: `⚡ Agent working: ${a.description}`
        }));

        updateUI();
    }, 2000);
}

function updateBrainState(data) {
    brainState = { ...brainState, ...data };

    if (data.signal) {
        fireSignal(data.signal);
    }

    updateUI();
}

// ── HELPERS ─────────────────────────────────────────────
function nowTime() {
    const d = new Date();
    return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
}

function formatUptime(totalSeconds) {
    const h = Math.floor(totalSeconds / 3600).toString().padStart(2, '0');
    const m = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, '0');
    const s = (totalSeconds % 60).toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ── UI UPDATES ──────────────────────────────────────────
function updateUI() {
    // Left brain status
    const leftStatus = document.getElementById('left-status');
    if (leftStatus) {
        leftStatus.textContent = brainState.leftActive ? '● ACTIVE' : '○ Idle';
        leftStatus.className = brainState.leftActive ? 'status active' : 'status idle';
    }

    // Right brain status
    const rightStatus = document.getElementById('right-status');
    if (rightStatus) {
        rightStatus.textContent = brainState.rightActive ? '● ACTIVE' : '○ Idle';
        rightStatus.className = brainState.rightActive ? 'status active' : 'status idle';
    }

    // Bridge status — show real connection state in split mode
    const bridgeStatus = document.getElementById('bridge-status');
    if (bridgeStatus) {
        if (brainState.brainRole === 'left') {
            if (brainState.bridgeConnected) {
                bridgeStatus.textContent = '● CONNECTED';
                bridgeStatus.className = 'status synced';
            } else {
                bridgeStatus.textContent = '○ DISCONNECTED';
                bridgeStatus.className = 'status idle';
            }
        } else {
            bridgeStatus.textContent = brainState.bridgeActive ? '● SYNCED' : '○ Idle';
            bridgeStatus.className = brainState.bridgeActive ? 'status synced' : 'status idle';
        }
    }

    // Right Brain location label
    const rightLabel = document.getElementById('right-brain-location');
    if (rightLabel) {
        if (brainState.brainRole === 'left' && brainState.rightBrainOnline) {
            rightLabel.textContent = 'HOMELAB';
            rightLabel.style.display = '';
        } else if (brainState.brainRole === 'left') {
            rightLabel.textContent = 'OFFLINE';
            rightLabel.style.display = '';
        } else {
            rightLabel.style.display = 'none';
        }
    }

    // Agent count (use agentCount from state, not activeAgents.length)
    const agentCount = document.getElementById('agent-count');
    if (agentCount) {
        agentCount.textContent = brainState.agentCount || 0;
    }

    // Task count
    const taskCount = document.getElementById('task-count');
    if (taskCount) {
        taskCount.textContent = brainState.taskCount || 0;
    }

    // Load bar (active / maxConcurrent)
    const loadFill = document.getElementById('load-fill');
    if (loadFill) {
        const max = brainState.maxConcurrent || 5;
        const active = brainState.agentCount || 0;
        const pct = Math.min(100, Math.round((active / max) * 100));
        loadFill.style.width = pct + '%';
    }

    // System stats (top bar)
    updateSystemStats();

    // Active agents panel
    updateAgentsPanel();

    // Activity feed — merge server + local items
    updateActivityFeed();
}

function updateSystemStats() {
    const uptimeEl = document.getElementById('stat-uptime');
    if (uptimeEl) {
        uptimeEl.textContent = formatUptime(brainState.uptime || 0);
    }

    const completedEl = document.getElementById('stat-completed');
    if (completedEl) {
        completedEl.textContent = brainState.completedCount || 0;
    }

    const queuedEl = document.getElementById('stat-queued');
    if (queuedEl) {
        queuedEl.textContent = brainState.queuedCount || 0;
    }
}

function updateAgentsPanel() {
    const agentsList = document.getElementById('agents-list');
    if (!agentsList) return;

    const agents = brainState.activeAgents || [];

    if (agents.length === 0) {
        agentsList.innerHTML = '<div class="agents-empty">No active agents</div>';
        return;
    }

    agentsList.innerHTML = agents.map(agent => {
        // Calculate elapsed time
        let elapsed = '';
        if (agent.startedAt) {
            const startMs = new Date(agent.startedAt).getTime();
            const elapsedSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
            const min = Math.floor(elapsedSec / 60);
            const sec = elapsedSec % 60;
            elapsed = `${min}m ${sec.toString().padStart(2, '0')}s`;
        }

        const desc = escapeHtml(agent.description || 'Working...');
        const project = escapeHtml(agent.project || '');

        return `<div class="agent-card">
            <div class="agent-card-top">
                <span class="agent-status-dot"></span>
                <span class="agent-desc">${desc}</span>
            </div>
            <div class="agent-card-bottom">
                ${project ? `<span class="agent-project">${project}</span>` : ''}
                ${elapsed ? `<span class="agent-elapsed">${elapsed}</span>` : ''}
            </div>
        </div>`;
    }).join('');
}

function updateActivityFeed() {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;

    // Merge server feed items with local items
    const serverItems = (brainState.taskFeed || []).map(t =>
        `<div class="feed-item"><span class="feed-time">${escapeHtml(t.time)}</span> ${escapeHtml(t.message)}</div>`
    );

    const localItems = localFeedItems.slice(-8).map(t =>
        `<div class="feed-item feed-local"><span class="feed-time">${escapeHtml(t.time)}</span> ${escapeHtml(t.message)}</div>`
    );

    // Show local items first (most recent), then server items
    const combined = [...localItems.reverse(), ...serverItems].slice(0, 10);

    if (combined.length > 0) {
        feed.innerHTML = combined.join('');
    }
}

// ── COMMAND BAR ─────────────────────────────────────────
function initCommandBar() {
    const input = document.getElementById('command-input');
    const sendBtn = document.getElementById('command-send');

    if (!input || !sendBtn) return;

    function sendCommand() {
        const text = input.value.trim();
        if (!text) return;
        if (text.length > MAX_INPUT_LENGTH) {
            appendToFeed(nowTime(), `System: Input too long (max ${MAX_INPUT_LENGTH} chars)`);
            return;
        }

        const time = nowTime();

        // Add command to local feed
        appendToFeed(time, `> ${text}`);

        // Send via WebSocket
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            wsConnection.send(JSON.stringify({ command: 'input', message: text }));
        } else {
            // Demo mode response
            setTimeout(() => {
                appendToFeed(nowTime(), `Leon: [Demo] Received: ${text}`);
            }, 500 + Math.random() * 1000);
        }

        input.value = '';
        input.focus();
    }

    sendBtn.addEventListener('click', sendCommand);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            sendCommand();
        }
    });
}

function appendToFeed(time, message) {
    localFeedItems.push({ time, message });

    // Keep local feed bounded
    if (localFeedItems.length > 50) {
        localFeedItems = localFeedItems.slice(-30);
    }

    // Immediate UI update for the feed
    updateActivityFeed();
}

// ── RESIZE ──────────────────────────────────────────────
function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    composer.setSize(window.innerWidth, window.innerHeight);
}

// ── START ───────────────────────────────────────────────
init();

// Export for external control
window.leonBrain = {
    fireSignal,
    updateState: updateBrainState,
    getState: () => brainState,
};
