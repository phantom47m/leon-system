/**
 * LEON BRAIN — 3D Neural Visualization + Dashboard Controller
 *
 * Premium Jarvis-style holographic brain with real-time system monitoring,
 * WebSocket communication, and keyboard-driven command interface.
 */

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';
import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/postprocessing/UnrealBloomPass.js';

// ── CONFIG ──────────────────────────────────────────────
const CFG = {
    // Symmetric brain — both hemispheres EQUAL
    LEFT_CENTER:  new THREE.Vector3(-1.2, 0, 0),
    RIGHT_CENTER: new THREE.Vector3( 1.2, 0, 0),
    HEMISPHERE_RADIUS: 1.6,

    NODE_COUNT: 400,
    BRIDGE_PARTICLE_COUNT: 60,
    AMBIENT_PARTICLE_COUNT: 200,

    // Jarvis palette — all cyan/blue/white, matching hemispheres
    LEFT_COLOR:   new THREE.Color(0x00d4ff),
    RIGHT_COLOR:  new THREE.Color(0x00a8ff),  // Slightly different cyan, not orange
    BRIDGE_COLOR: new THREE.Color(0x44bbff),
    ACTIVE_COLOR: new THREE.Color(0x00ffcc),
    RING_COLOR:   new THREE.Color(0x00d4ff),
    BG_COLOR:     0x020812,

    ROTATION_SPEED: 0.0003,
    PULSE_SPEED: 0.02,
    SIGNAL_SPEED: 0.008,
};

// ── GLOBALS ─────────────────────────────────────────────
let scene, camera, renderer, composer, controls;
let leftNodes = [], rightNodes = [], bridgeParticles = [];
let connections = [], signals = [];
let ambientParticles;
let connectionMaterials = [];
let brainActivity = 0;
let neuronFireState = { left: [], right: [] };
let frameCount = 0;
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

let wsConnection = null;
let localFeedItems = [];
let demoUptimeStart = Date.now();

// WebSocket reconnect
let wsReconnectDelay = 1000;
const WS_MAX_RECONNECT_DELAY = 30000;
let wsReconnectTimer = null;

// Input
const MAX_INPUT_LENGTH = 2000;

// Command history
let commandHistory = [];
let historyIndex = -1;
const MAX_HISTORY = 50;

// Health polling
let healthPollTimer = null;
const HEALTH_POLL_INTERVAL = 5000;

// Loading state
let isWaitingResponse = false;

// Keyboard hint visibility
let kbdHintTimer = null;

// ── INIT ────────────────────────────────────────────────
function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(CFG.BG_COLOR);

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.set(0, 1.5, 9);
    camera.lookAt(0, 0, 0);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ReinhardToneMapping;
    renderer.toneMappingExposure = 1.0;
    document.getElementById('brain-container').appendChild(renderer.domElement);

    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));

    const bloomPass = new UnrealBloomPass(
        new THREE.Vector2(window.innerWidth, window.innerHeight),
        0.35,
        0.3,
        0.8
    );
    composer.addPass(bloomPass);

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.3;
    controls.minDistance = 5;
    controls.maxDistance = 12;
    controls.minPolarAngle = Math.PI * 0.35;
    controls.maxPolarAngle = Math.PI * 0.65;
    controls.minAzimuthAngle = -Math.PI * 0.15;
    controls.maxAzimuthAngle =  Math.PI * 0.15;

    createHemisphere('left');
    createHemisphere('right');
    createNeuralBridge();
    createConnections('left');
    createConnections('right');
    createAmbientParticles();
    createCoreGlow('left');
    createCoreGlow('right');
    createJarvisRings();

    const ambientLight = new THREE.AmbientLight(0x111122, 0.5);
    scene.add(ambientLight);

    window.addEventListener('resize', onResize);

    connectWebSocket();
    initCommandBar();
    initKeyboardShortcuts();
    startHealthPolling();

    animate();
}

// ── HEMISPHERE ──────────────────────────────────────────
function createHemisphere(side) {
    const center = side === 'left' ? CFG.LEFT_CENTER : CFG.RIGHT_CENTER;
    const color = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;
    const nodes = side === 'left' ? leftNodes : rightNodes;

    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(CFG.NODE_COUNT * 3);
    const colors = new Float32Array(CFG.NODE_COUNT * 3);
    const sizes = new Float32Array(CFG.NODE_COUNT);

    for (let i = 0; i < CFG.NODE_COUNT; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = CFG.HEMISPHERE_RADIUS * (0.5 + 0.5 * Math.random());

        // Brain-like shape: taller, slight front-back elongation
        const scaleY = 1.2;   // Taller
        const scaleZ = 1.05;  // Slightly deeper front-to-back

        let rawX = r * Math.sin(phi) * Math.cos(theta);
        // Flatten inner face slightly (where hemispheres meet)
        if (side === 'left' && rawX > 0) rawX *= 0.15;
        if (side === 'right' && rawX < 0) rawX *= 0.15;

        // Brain surface folds (sulci) — add organic waviness
        const fold = 0.08 * Math.sin(theta * 8 + phi * 6) * Math.cos(phi * 3);

        const x = center.x + rawX + fold * 0.3;
        const y = center.y + (r + fold) * Math.sin(phi) * Math.sin(theta) * scaleY;
        const z = center.z + (r + fold) * Math.cos(phi) * scaleZ;

        positions[i * 3]     = x;
        positions[i * 3 + 1] = y;
        positions[i * 3 + 2] = z;

        colors[i * 3]     = color.r;
        colors[i * 3 + 1] = color.g;
        colors[i * 3 + 2] = color.b;

        sizes[i] = 1 + Math.random() * 2;

        nodes.push({
            index: i,
            position: new THREE.Vector3(x, y, z),
            baseSize: sizes[i],
            phase: Math.random() * Math.PI * 2,
        });
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
        uniforms: {
            uTime: { value: 0 },
            uActivity: { value: 0 },
            uActiveColor: { value: CFG.ACTIVE_COLOR },
        },
        vertexShader: `
            attribute float size;
            attribute vec3 color;
            varying vec3 vColor;
            uniform float uTime;
            uniform float uActivity;

            void main() {
                vColor = color;
                vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
                float speed = mix(0.8, 3.5, uActivity);
                float pulse = 1.0 + 0.3 * sin(uTime * speed + position.x * 3.0);
                gl_PointSize = size * pulse * (120.0 / -mvPosition.z);
                gl_Position = projectionMatrix * mvPosition;
            }
        `,
        fragmentShader: `
            varying vec3 vColor;
            uniform float uActivity;

            void main() {
                float dist = length(gl_PointCoord - vec2(0.5));
                if (dist > 0.45) discard;
                float core = 1.0 - smoothstep(0.0, 0.15, dist);
                float halo = 1.0 - smoothstep(0.0, 0.45, dist);
                float brightness = mix(0.6, 1.2, uActivity);
                vec3 finalColor = vColor * brightness;
                float alpha = mix(halo * 0.4, core * 0.9 + halo * 0.3, 0.5);
                gl_FragColor = vec4(finalColor, alpha);
            }
        `,
        transparent: true,
        blending: THREE.NormalBlending,
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
        const t = Math.random();
        const x = THREE.MathUtils.lerp(CFG.LEFT_CENTER.x + 0.8, CFG.RIGHT_CENTER.x - 0.8, t);
        const spread = 0.5 * Math.sin(t * Math.PI);
        const y = (Math.random() - 0.5) * spread;
        const z = (Math.random() - 0.5) * spread;

        positions[i * 3]     = x;
        positions[i * 3 + 1] = y;
        positions[i * 3 + 2] = z;

        colors[i * 3]     = CFG.BRIDGE_COLOR.r;
        colors[i * 3 + 1] = CFG.BRIDGE_COLOR.g;
        colors[i * 3 + 2] = CFG.BRIDGE_COLOR.b;

        sizes[i] = 0.8 + Math.random() * 1.2;

        bridgeParticles.push({
            index: i,
            t: t,
            speed: 0.001 + Math.random() * 0.003,
            baseY: y,
            baseZ: z,
        });
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
        uniforms: {
            uTime: { value: 0 },
            uActivity: { value: 0 },
        },
        vertexShader: `
            attribute float size;
            attribute vec3 color;
            varying vec3 vColor;
            varying float vAlpha;
            uniform float uTime;
            uniform float uActivity;

            void main() {
                vColor = color;
                vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
                float speed = mix(1.5, 3.0, uActivity);
                float pulse = 1.0 + 0.5 * sin(uTime * speed + position.x * 5.0);
                gl_PointSize = size * pulse * (120.0 / -mvPosition.z);
                vAlpha = 0.3 + 0.3 * sin(uTime * 2.0 + position.x * 8.0);
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

// ── CONNECTIONS ─────────────────────────────────────────
function createConnections(side) {
    const nodes = side === 'left' ? leftNodes : rightNodes;
    const color = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;
    const maxDist = 0.7;
    const linePositions = [];

    for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
            const dist = nodes[i].position.distanceTo(nodes[j].position);
            if (dist < maxDist && Math.random() < 0.4) {
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
        opacity: 0.2,
        blending: THREE.NormalBlending,
    });

    const lines = new THREE.LineSegments(geometry, material);
    lines.userData = { side };
    scene.add(lines);
    connectionMaterials.push(material);
}

// ── CORE GLOW ───────────────────────────────────────────
function createCoreGlow(side) {
    const center = side === 'left' ? CFG.LEFT_CENTER : CFG.RIGHT_CENTER;
    const color = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;

    const coreGeo = new THREE.SphereGeometry(0.08, 16, 16);
    const coreMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.15 });
    const core = new THREE.Mesh(coreGeo, coreMat);
    core.position.copy(center);
    core.userData = { side, type: 'core' };
    scene.add(core);

    const glowGeo = new THREE.SphereGeometry(0.4, 16, 16);
    const glowMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.04, side: THREE.BackSide });
    const glow = new THREE.Mesh(glowGeo, glowMat);
    glow.position.copy(center);
    glow.userData = { side, type: 'coreGlow' };
    scene.add(glow);

    const light = new THREE.PointLight(color, 0.15, 4);
    light.position.copy(center);
    light.userData = { side, type: 'coreLight' };
    scene.add(light);
}

// ── JARVIS RINGS — Rotating holographic arcs ────────────
let jarvisRings = [];

function createJarvisRings() {
    const ringConfigs = [
        { radius: 3.2, tube: 0.008, arc: Math.PI * 1.6, color: 0x00d4ff, speed: 0.15, tilt: 0.1 },
        { radius: 3.5, tube: 0.006, arc: Math.PI * 1.3, color: 0x00a8ff, speed: -0.12, tilt: -0.15 },
        { radius: 3.8, tube: 0.005, arc: Math.PI * 0.9, color: 0x0088cc, speed: 0.08, tilt: 0.25 },
        { radius: 2.8, tube: 0.007, arc: Math.PI * 1.1, color: 0x00d4ff, speed: -0.2, tilt: -0.05 },
        { radius: 4.1, tube: 0.004, arc: Math.PI * 0.7, color: 0x006699, speed: 0.06, tilt: 0.3 },
    ];

    for (const cfg of ringConfigs) {
        const geometry = new THREE.TorusGeometry(cfg.radius, cfg.tube, 8, 128, cfg.arc);
        const material = new THREE.MeshBasicMaterial({
            color: cfg.color,
            transparent: true,
            opacity: 0.25,
            side: THREE.DoubleSide,
        });
        const ring = new THREE.Mesh(geometry, material);
        ring.rotation.x = Math.PI / 2 + cfg.tilt;
        ring.rotation.z = Math.random() * Math.PI * 2;
        ring.userData = { speed: cfg.speed, baseTilt: cfg.tilt };
        scene.add(ring);
        jarvisRings.push(ring);
    }

    // Dashed circle rings (like HUD targeting circles)
    for (let i = 0; i < 3; i++) {
        const r = 2.5 + i * 0.8;
        const segments = 128;
        const points = [];
        for (let j = 0; j <= segments; j++) {
            const angle = (j / segments) * Math.PI * 2;
            points.push(new THREE.Vector3(Math.cos(angle) * r, 0, Math.sin(angle) * r));
        }
        const geometry = new THREE.BufferGeometry().setFromPoints(points);
        const material = new THREE.LineDashedMaterial({
            color: 0x00d4ff,
            transparent: true,
            opacity: 0.06 + i * 0.02,
            dashSize: 0.3,
            gapSize: 0.5 + i * 0.3,
        });
        const circle = new THREE.Line(geometry, material);
        circle.computeLineDistances();
        circle.rotation.x = Math.PI / 2;
        circle.userData = { speed: 0.02 * (i % 2 === 0 ? 1 : -1) };
        scene.add(circle);
        jarvisRings.push(circle);
    }
}

// ── AMBIENT PARTICLES ───────────────────────────────────
function createAmbientParticles() {
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(CFG.AMBIENT_PARTICLE_COUNT * 3);
    const sizes = new Float32Array(CFG.AMBIENT_PARTICLE_COUNT);

    for (let i = 0; i < CFG.AMBIENT_PARTICLE_COUNT; i++) {
        positions[i * 3]     = (Math.random() - 0.5) * 20;
        positions[i * 3 + 1] = (Math.random() - 0.5) * 12;
        positions[i * 3 + 2] = (Math.random() - 0.5) * 12;
        sizes[i] = 0.3 + Math.random() * 0.8;
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
                pos.y += sin(uTime * 0.3 + position.x) * 0.05;
                pos.x += cos(uTime * 0.2 + position.z) * 0.03;
                vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
                gl_PointSize = size * (80.0 / -mvPosition.z);
                vAlpha = 0.04 + 0.04 * sin(uTime + position.x * 2.0);
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
        blending: THREE.NormalBlending,
        depthWrite: false,
    });

    ambientParticles = new THREE.Points(geometry, material);
    scene.add(ambientParticles);
}

// ── SIGNAL PULSE ────────────────────────────────────────
function fireSignal(direction) {
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

    const light = new THREE.PointLight(CFG.ACTIVE_COLOR, 1, 2);
    signal.add(light);

    scene.add(signal);
    signals.push({
        mesh: signal,
        start,
        end,
        t: 0,
        speed: CFG.SIGNAL_SPEED + Math.random() * 0.005,
    });
}

// ── ANIMATE ─────────────────────────────────────────────
function animate() {
    requestAnimationFrame(animate);

    const time = clock.getElapsedTime();
    frameCount++;

    const target = Math.min(1, (brainState.agentCount || 0) / 3);
    brainActivity += (target - brainActivity) * 0.02;

    const connOpacity = THREE.MathUtils.lerp(0.15, 0.45, brainActivity);
    const bridgeSpeedMult = THREE.MathUtils.lerp(0.3, 1.5, brainActivity);
    const signalChance = THREE.MathUtils.lerp(0.0, 0.05, brainActivity);
    const coreOpacity = THREE.MathUtils.lerp(0.05, 0.15, brainActivity);
    const corePulseSpeed = THREE.MathUtils.lerp(0.8, 3.0, brainActivity);
    const corePulseAmp = THREE.MathUtils.lerp(0.02, 0.12, brainActivity);
    const autoRotateSpeed = THREE.MathUtils.lerp(0.2, 0.6, brainActivity);

    controls.autoRotateSpeed = autoRotateSpeed;

    for (const mat of connectionMaterials) {
        mat.opacity = connOpacity;
    }

    scene.traverse((obj) => {
        if (obj.userData?.material?.uniforms?.uTime) {
            obj.userData.material.uniforms.uTime.value = time;
        }
        if (obj.userData?.material?.uniforms?.uActivity) {
            obj.userData.material.uniforms.uActivity.value = brainActivity;
        }
        if (obj.isPoints && obj.material?.uniforms?.uTime) {
            obj.material.uniforms.uTime.value = time;
        }
        if (obj.isPoints && obj.material?.uniforms?.uActivity) {
            obj.material.uniforms.uActivity.value = brainActivity;
        }
        if (obj.userData?.type === 'core') {
            const pulse = coreOpacity + corePulseAmp * Math.sin(time * corePulseSpeed + (obj.userData.side === 'left' ? 0 : Math.PI));
            obj.material.opacity = pulse;
            obj.scale.setScalar(0.9 + corePulseAmp * Math.sin(time * corePulseSpeed * 0.8));
        }
        if (obj.userData?.type === 'coreGlow') {
            obj.scale.setScalar(1 + 0.15 * Math.sin(time * corePulseSpeed * 0.6));
        }
        if (obj.userData?.type === 'coreLight') {
            obj.intensity = THREE.MathUtils.lerp(0.05, 0.15, brainActivity);
        }
    });

    // Bridge particles
    scene.traverse((obj) => {
        if (obj.userData?.type === 'bridge' && obj.isPoints) {
            const positions = obj.geometry.attributes.position.array;
            for (let i = 0; i < bridgeParticles.length; i++) {
                const p = bridgeParticles[i];
                p.t += p.speed * bridgeSpeedMult;
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

    // Signals
    for (let i = signals.length - 1; i >= 0; i--) {
        const s = signals[i];
        s.t += s.speed;
        if (s.t >= 1) {
            scene.remove(s.mesh);
            signals.splice(i, 1);
            continue;
        }
        s.mesh.position.lerpVectors(s.start, s.end, s.t);
        s.mesh.position.y += 0.5 * Math.sin(s.t * Math.PI);
        s.mesh.material.opacity = 1 - s.t * 0.5;
    }

    if (ambientParticles) {
        ambientParticles.material.uniforms.uTime.value = time;
    }

    // Jarvis rings rotation
    for (const ring of jarvisRings) {
        ring.rotation.z += ring.userData.speed * 0.01;
        // Subtle breathing on ring opacity based on brain activity
        if (ring.material && ring.material.opacity !== undefined) {
            const baseOpacity = ring.material.userData?.baseOpacity || ring.material.opacity;
            if (!ring.material.userData) ring.material.userData = {};
            ring.material.userData.baseOpacity = ring.material.userData.baseOpacity || ring.material.opacity;
            ring.material.opacity = ring.material.userData.baseOpacity * (0.8 + 0.4 * brainActivity);
        }
    }

    if (brainActivity > 0.1 && Math.random() < signalChance) {
        fireSignal(Math.random() > 0.5 ? 'left-to-right' : 'right-to-left');
    }

    if (brainActivity > 0.3 && frameCount % 15 === 0) {
        fireNeurons('left', leftNodes);
        fireNeurons('right', rightNodes);
    }
    updateNeuronFiring(time);

    controls.update();
    composer.render();
}

// ── NEURON FIRING ───────────────────────────────────────
function fireNeurons(side, nodes) {
    const count = 3 + Math.floor(Math.random() * 3);
    const state = neuronFireState[side];
    for (let n = 0; n < count; n++) {
        const idx = Math.floor(Math.random() * nodes.length);
        if (!state.some(f => f.index === idx)) {
            state.push({ index: idx, framesLeft: 10 });
        }
    }
}

function updateNeuronFiring() {
    scene.traverse((obj) => {
        if (!obj.isPoints || !obj.userData?.side) return;
        const side = obj.userData.side;
        const state = neuronFireState[side];
        if (!state || state.length === 0) return;

        const colors = obj.geometry.attributes.color;
        if (!colors) return;

        const baseColor = side === 'left' ? CFG.LEFT_COLOR : CFG.RIGHT_COLOR;

        for (let i = state.length - 1; i >= 0; i--) {
            const fire = state[i];
            fire.framesLeft--;

            const idx = fire.index;
            if (fire.framesLeft > 0) {
                const t = fire.framesLeft / 10;
                colors.array[idx * 3]     = THREE.MathUtils.lerp(baseColor.r, CFG.ACTIVE_COLOR.r, t);
                colors.array[idx * 3 + 1] = THREE.MathUtils.lerp(baseColor.g, CFG.ACTIVE_COLOR.g, t);
                colors.array[idx * 3 + 2] = THREE.MathUtils.lerp(baseColor.b, CFG.ACTIVE_COLOR.b, t);
            } else {
                colors.array[idx * 3]     = baseColor.r;
                colors.array[idx * 3 + 1] = baseColor.g;
                colors.array[idx * 3 + 2] = baseColor.b;
                state.splice(i, 1);
            }
        }
        colors.needsUpdate = true;
    });
}

// ── AUTH ─────────────────────────────────────────────────
let wsAuthenticated = false;

function getStoredToken() {
    return localStorage.getItem('leon_session_token') || '';
}

function setStoredToken(token) {
    localStorage.setItem('leon_session_token', token);
}

function showAuthOverlay(errorMsg) {
    const overlay = document.getElementById('auth-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    const errEl = document.getElementById('auth-error');
    if (errEl) errEl.textContent = errorMsg || '';

    const tokenInput = document.getElementById('auth-token-input');
    const submitBtn = document.getElementById('auth-submit');
    if (!tokenInput || !submitBtn) return;

    setTimeout(() => tokenInput.focus(), 100);

    function doAuth() {
        const token = tokenInput.value.trim();
        if (!token) return;
        setStoredToken(token);
        overlay.style.display = 'none';
        if (wsConnection) {
            wsConnection.close();
        } else {
            connectWebSocket();
        }
    }

    submitBtn.onclick = doAuth;
    tokenInput.onkeydown = (e) => {
        if (e.key === 'Enter') doAuth();
    };
}

function hideAuthOverlay() {
    const overlay = document.getElementById('auth-overlay');
    if (overlay) overlay.style.display = 'none';
}

// ── CONNECTION STATUS UI ─────────────────────────────────
function setConnectionStatus(status) {
    const dot = document.getElementById('ws-dot');
    const text = document.getElementById('ws-status-text');
    if (!dot || !text) return;

    const indicator = document.getElementById('ws-indicator');

    // Remove all state classes
    dot.classList.remove('pulse', 'disconnected', 'reconnecting');
    if (indicator) {
        indicator.classList.remove('disconnected', 'reconnecting');
    }

    switch (status) {
        case 'connected':
            dot.classList.add('pulse');
            dot.style.background = '';
            text.textContent = 'SYSTEM ONLINE';
            if (indicator) indicator.style.color = '';
            break;
        case 'disconnected':
            dot.classList.add('disconnected');
            text.textContent = 'DISCONNECTED';
            if (indicator) {
                indicator.classList.add('disconnected');
            }
            break;
        case 'reconnecting':
            dot.classList.add('reconnecting');
            text.textContent = 'RECONNECTING';
            if (indicator) {
                indicator.classList.add('reconnecting');
            }
            break;
        case 'demo':
            dot.classList.add('pulse');
            dot.style.background = 'var(--gold)';
            text.textContent = 'DEMO MODE';
            if (indicator) indicator.style.color = 'var(--gold)';
            break;
    }
}

// ── LOADING STATE ────────────────────────────────────────
function setLoading(active) {
    isWaitingResponse = active;
    const loader = document.getElementById('command-loading');
    if (loader) {
        if (active) {
            loader.classList.add('active');
        } else {
            loader.classList.remove('active');
        }
    }
}

// ── WEBSOCKET ───────────────────────────────────────────
function connectWebSocket() {
    const token = getStoredToken();

    if (!token) {
        showAuthOverlay('');
        setConnectionStatus('demo');
        startDemoMode();
        return;
    }

    setConnectionStatus('reconnecting');

    try {
        const ws = new WebSocket(`ws://${window.location.host}/ws`);
        wsConnection = ws;
        wsAuthenticated = false;

        ws.onopen = () => {
            wsReconnectDelay = 1000;
            ws.send(JSON.stringify({ command: 'auth', token: token }));
        };

        ws.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                return;
            }

            if (data.type === 'auth_result') {
                if (data.success) {
                    wsAuthenticated = true;
                    hideAuthOverlay();
                    setConnectionStatus('connected');
                    if (demoInterval) {
                        clearInterval(demoInterval);
                        demoInterval = null;
                    }
                } else {
                    wsAuthenticated = false;
                    localStorage.removeItem('leon_session_token');
                    setConnectionStatus('disconnected');
                    showAuthOverlay(data.message || 'Authentication failed');
                }
                return;
            }

            if (data.type === 'input_response') {
                setLoading(false);
                appendToFeed(data.timestamp || nowTime(), `Leon: ${data.message}`, 'feed-response');
                return;
            }
            if (data.type === 'agent_completed') {
                appendToFeed(nowTime(), `Agent #${(data.agent_id || '').slice(-8)} completed: ${data.summary || ''}`, 'feed-agent-ok');
                return;
            }
            if (data.type === 'agent_failed') {
                appendToFeed(nowTime(), `Agent #${(data.agent_id || '').slice(-8)} failed: ${data.error || ''}`, 'feed-agent-fail');
                return;
            }
            updateBrainState(data);
        };

        ws.onclose = () => {
            wsConnection = null;
            wsAuthenticated = false;
            setConnectionStatus('reconnecting');
            if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
            wsReconnectTimer = setTimeout(connectWebSocket, wsReconnectDelay);
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_MAX_RECONNECT_DELAY);
        };

        ws.onerror = () => {
            wsConnection = null;
            wsAuthenticated = false;
            setConnectionStatus('demo');
            startDemoMode();
        };
    } catch (e) {
        setConnectionStatus('demo');
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
    if (demoInterval) return;

    let demoAgents = [];
    let demoQueued = 2;

    demoInterval = setInterval(() => {
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

        const now = nowTime();
        brainState.taskFeed = demoAgents.map(a => ({
            time: now,
            message: `Agent working: ${a.description}`
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
    // Left brain
    const leftStatus = document.getElementById('left-status');
    if (leftStatus) {
        leftStatus.textContent = brainState.leftActive ? 'ACTIVE' : 'IDLE';
        leftStatus.className = brainState.leftActive ? 'strip-status active' : 'strip-status idle';
    }

    // Right brain
    const rightStatus = document.getElementById('right-status');
    if (rightStatus) {
        rightStatus.textContent = brainState.rightActive ? 'ACTIVE' : 'IDLE';
        rightStatus.className = brainState.rightActive ? 'strip-status active' : 'strip-status idle';
    }

    // Counts
    const agentCount = document.getElementById('agent-count');
    if (agentCount) agentCount.textContent = brainState.agentCount || 0;

    const taskCount = document.getElementById('task-count');
    if (taskCount) taskCount.textContent = brainState.taskCount || 0;

    updateSystemStats();
    updateAgentsPanel();
    updateActivityFeed();
    updateVoiceState();
}

function updateSystemStats() {
    const uptimeEl = document.getElementById('stat-uptime');
    if (uptimeEl) uptimeEl.textContent = formatUptime(brainState.uptime || 0);

    const completedEl = document.getElementById('stat-completed');
    if (completedEl) completedEl.textContent = brainState.completedCount || 0;

    const queuedEl = document.getElementById('stat-queued');
    if (queuedEl) queuedEl.textContent = brainState.queuedCount || 0;
}

function updateAgentsPanel() {
    const agentsList = document.getElementById('agents-list');
    if (!agentsList) return;

    const agents = brainState.activeAgents || [];

    if (agents.length === 0) {
        agentsList.innerHTML = '<div class="agents-empty">IDLE</div>';
        return;
    }

    agentsList.innerHTML = agents.map(agent => {
        let elapsed = '';
        if (agent.startedAt) {
            const startMs = new Date(agent.startedAt).getTime();
            const elapsedSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
            const min = Math.floor(elapsedSec / 60);
            const sec = elapsedSec % 60;
            elapsed = `${min}m${sec.toString().padStart(2, '0')}s`;
        }

        const desc = escapeHtml((agent.description || 'Working...').substring(0, 20));

        return `<div class="agent-card">
            <span class="agent-desc">${desc}</span>
            ${elapsed ? `<span class="agent-elapsed">${elapsed}</span>` : ''}
        </div>`;
    }).join('');
}

function updateActivityFeed() {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;
    const countEl = document.getElementById('chat-count');
    if (countEl) countEl.textContent = feed.children.length;
}

function updateVoiceState() {
    const el = document.getElementById('voice-state');
    if (!el) return;
    const voice = brainState.voice || {};
    if (!voice.active) {
        el.textContent = 'VOICE: OFF';
        el.classList.remove('active');
        return;
    }
    const state = voice.state || 'unknown';
    const labels = {
        idle: 'VOICE: IDLE',
        listening: 'VOICE: LISTENING',
        awake: 'VOICE: AWAKE',
        processing: 'VOICE: THINKING',
        speaking: 'VOICE: SPEAKING',
        sleeping: 'VOICE: SLEEP',
        stopped: 'VOICE: OFF',
        degraded: 'VOICE: DEGRADED',
    };
    el.textContent = labels[state] || `VOICE: ${state.toUpperCase()}`;
    el.classList.toggle('active', state === 'listening' || state === 'awake' || state === 'speaking');
}

// ── HEALTH POLLING ──────────────────────────────────────
function startHealthPolling() {
    pollHealth();
    healthPollTimer = setInterval(pollHealth, HEALTH_POLL_INTERVAL);
}

async function pollHealth() {
    try {
        const resp = await fetch('/api/health');
        if (!resp.ok) return;
        const data = await resp.json();
        updateGauges(data);
        updateExtraStats(data);
    } catch (e) {
        // Server unreachable
    }
}

function updateGauges(data) {
    const C = 97.4; // circumference = 2 * PI * 15.5

    const cpuVal = parseFloat(data.cpu) || 0;
    updateGauge('gauge-cpu', 'gauge-cpu-val', cpuVal, C);

    const memPct = data.memory?.percent ? parseFloat(data.memory.percent) : 0;
    updateGauge('gauge-mem', 'gauge-mem-val', memPct, C);

    const diskPct = data.disk?.percent ? parseFloat(data.disk.percent) : 0;
    updateGauge('gauge-disk', 'gauge-disk-val', diskPct, C);

    // GPU
    const gpuPct = data.gpu?.usage ? parseFloat(data.gpu.usage) : 0;
    updateGauge('gauge-gpu', 'gauge-gpu-val', gpuPct, C);
}

function updateExtraStats(data) {
    // Memory detail
    const memEl = document.getElementById('mem-detail');
    if (memEl && data.memory) {
        const used = (data.memory.used_mb / 1024).toFixed(1);
        const total = (data.memory.total_mb / 1024).toFixed(1);
        memEl.textContent = `${used} / ${total} GB`;
    }

    // GPU details
    if (data.gpu) {
        const nameEl = document.getElementById('gpu-name');
        const tempEl = document.getElementById('gpu-temp');
        const vramEl = document.getElementById('gpu-vram');
        if (nameEl) nameEl.textContent = data.gpu.name || '--';
        if (tempEl) tempEl.textContent = data.gpu.temp || '-- °C';
        if (vramEl) vramEl.textContent = `VRAM: ${data.gpu.vram_used || '--'} / ${data.gpu.vram_total || '--'}`;
    }

    // Disk detail
    const diskEl = document.getElementById('disk-detail');
    if (diskEl && data.disk) {
        diskEl.textContent = `${data.disk.used_gb} / ${data.disk.total_gb} GB`;
    }

    // Network
    const net = data.network ? Object.values(data.network)[0] : null;
    if (net) {
        const rxEl = document.getElementById('net-rx');
        const txEl = document.getElementById('net-tx');
        if (rxEl) rxEl.innerHTML = `&#x2193; RX: ${net.rx_gb} GB`;
        if (txEl) txEl.innerHTML = `&#x2191; TX: ${net.tx_gb} GB`;
    }

    // Load + processes
    const loadEl = document.getElementById('stat-load');
    if (loadEl) loadEl.textContent = data.load_avg || '--';
    const procEl = document.getElementById('stat-proc');
    if (procEl) procEl.textContent = data.processes || '--';

    // Leon stats
    const leon = data.leon || {};
    const brainEl = document.getElementById('brain-role');
    if (brainEl) brainEl.textContent = (leon.brain_role || 'unified').toUpperCase();

    // Notifications
    const notifs = leon.notifications || {};
    const ntEl = document.getElementById('notif-total');
    const npEl = document.getElementById('notif-pending');
    if (ntEl) ntEl.textContent = notifs.total || 0;
    if (npEl) npEl.textContent = notifs.pending || 0;

    // Screen awareness
    const screen = leon.screen || {};
    const saEl = document.getElementById('screen-activity');
    const spEl = document.getElementById('screen-app');
    if (saEl) saEl.textContent = `Activity: ${screen.activity || '--'}`;
    if (spEl) spEl.textContent = `App: ${screen.active_app || '--'}`;
}

function updateGauge(circleId, valId, percent, circumference) {
    const circle = document.getElementById(circleId);
    const valEl = document.getElementById(valId);
    if (!circle || !valEl) return;

    const offset = circumference - (circumference * Math.min(percent, 100) / 100);
    circle.style.strokeDashoffset = offset;

    valEl.textContent = Math.round(percent) + '%';

    // Color thresholds
    circle.classList.remove('warn', 'critical');
    if (percent > 90) {
        circle.classList.add('critical');
    } else if (percent > 75) {
        circle.classList.add('warn');
    }
}

// ── SLASH COMMANDS ──────────────────────────────────────
const SLASH_COMMANDS = [
    { cmd: '/agents',        desc: 'List active agents' },
    { cmd: '/status',        desc: 'System overview' },
    { cmd: '/kill',          desc: 'Terminate an agent' },
    { cmd: '/queue',         desc: 'Show queued tasks' },
    { cmd: '/retry',         desc: 'Retry a failed agent' },
    { cmd: '/history',       desc: 'Recent completed tasks' },
    { cmd: '/search',        desc: 'Search agent history' },
    { cmd: '/stats',         desc: 'Agent run statistics' },
    { cmd: '/schedule',      desc: 'View scheduled tasks' },
    { cmd: '/notifications', desc: 'Recent notifications' },
    { cmd: '/screen',        desc: 'Screen awareness status' },
    { cmd: '/gpu',           desc: 'GPU usage and temperature' },
    { cmd: '/clipboard',     desc: 'Clipboard contents' },
    { cmd: '/changes',       desc: 'File changes in projects' },
    { cmd: '/export',        desc: 'Export conversation' },
    { cmd: '/context',       desc: 'Memory context stats' },
    { cmd: '/bridge',        desc: 'Right Brain connection' },
    { cmd: '/setkey',        desc: 'Store API key in vault' },
    { cmd: '/vault',         desc: 'List vault keys' },
    { cmd: '/approve',       desc: 'Grant temp permission' },
    { cmd: '/login',         desc: 'Authenticate as owner' },
    { cmd: '/voice',         desc: 'Voice system status' },
    { cmd: '/restart',       desc: 'How to restart Leon' },
    { cmd: '/whatsapp',      desc: 'WhatsApp bridge status' },
    { cmd: '/help',          desc: 'Show all commands' },
];

// ── COMMAND BAR ─────────────────────────────────────────
function initCommandBar() {
    const input = document.getElementById('command-input');
    const sendBtn = document.getElementById('command-send');
    if (!input || !sendBtn) return;

    // Autocomplete dropdown
    const autocomplete = document.createElement('div');
    autocomplete.id = 'command-autocomplete';
    autocomplete.style.cssText = `
        display: none;
        position: absolute;
        bottom: 100%;
        left: 0;
        right: 0;
        background: rgba(5, 5, 16, 0.96);
        border: 1px solid rgba(0, 212, 255, 0.2);
        border-radius: 10px;
        padding: 6px 0;
        margin-bottom: 6px;
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 12px;
        z-index: 100;
        max-height: 280px;
        overflow-y: auto;
        backdrop-filter: blur(20px);
        box-shadow: 0 -8px 30px rgba(0, 0, 0, 0.4), 0 0 20px rgba(0, 212, 255, 0.05);
    `;
    const commandBar = input.closest('.command-bar') || input.parentElement;
    commandBar.style.position = 'relative';
    commandBar.appendChild(autocomplete);

    let selectedIdx = -1;

    function updateAutocomplete() {
        const val = input.value;
        if (!val.startsWith('/')) {
            autocomplete.style.display = 'none';
            selectedIdx = -1;
            return;
        }

        const query = val.toLowerCase();
        const matches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(query));

        if (matches.length === 0 || (matches.length === 1 && matches[0].cmd === query)) {
            autocomplete.style.display = 'none';
            selectedIdx = -1;
            return;
        }

        selectedIdx = -1;
        renderAutocomplete(matches);
        autocomplete.style.display = 'block';
    }

    function renderAutocomplete(matches) {
        autocomplete.innerHTML = matches.map((c, i) => `
            <div class="autocomplete-item" data-cmd="${c.cmd}" data-idx="${i}" style="
                padding: 8px 14px;
                cursor: pointer;
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                transition: background 0.15s;
                border-radius: 6px;
                margin: 0 4px;
                ${i === selectedIdx ? 'background: rgba(0, 212, 255, 0.1);' : ''}
            ">
                <span style="color: #00d4ff; font-weight: 600; font-size: 12px;">${escapeHtml(c.cmd)}</span>
                <span style="color: rgba(255,255,255,0.3); font-size: 11px;">${escapeHtml(c.desc)}</span>
            </div>
        `).join('');

        // Click/hover handlers
        autocomplete.querySelectorAll('.autocomplete-item').forEach(el => {
            el.addEventListener('mouseenter', () => {
                el.style.background = 'rgba(0, 212, 255, 0.08)';
            });
            el.addEventListener('mouseleave', () => {
                const idx = parseInt(el.dataset.idx);
                el.style.background = idx === selectedIdx ? 'rgba(0, 212, 255, 0.1)' : 'transparent';
            });
            el.addEventListener('mousedown', (e) => {
                e.preventDefault();
                input.value = el.dataset.cmd + ' ';
                input.focus();
                autocomplete.style.display = 'none';
            });
        });
    }

    function sendCommand() {
        const text = input.value.trim();
        if (!text) return;
        if (text.length > MAX_INPUT_LENGTH) {
            appendToFeed(nowTime(), `System: Input too long (max ${MAX_INPUT_LENGTH} chars)`, 'feed-system');
            return;
        }

        autocomplete.style.display = 'none';

        // Save to command history
        if (commandHistory[commandHistory.length - 1] !== text) {
            commandHistory.push(text);
            if (commandHistory.length > MAX_HISTORY) {
                commandHistory = commandHistory.slice(-MAX_HISTORY);
            }
        }
        historyIndex = -1;

        const time = nowTime();
        appendToFeed(time, `> ${text}`, 'feed-command');

        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            setLoading(true);
            wsConnection.send(JSON.stringify({ command: 'input', message: text }));
            // Auto-clear loading after timeout
            setTimeout(() => setLoading(false), 30000);
        } else {
            setTimeout(() => {
                appendToFeed(nowTime(), `Leon: [Demo] Received: ${text}`, 'feed-response');
            }, 300 + Math.random() * 700);
        }

        input.value = '';
        input.focus();
    }

    sendBtn.addEventListener('click', sendCommand);

    input.addEventListener('keydown', (e) => {
        const items = autocomplete.querySelectorAll('.autocomplete-item');

        if (e.key === 'Enter') {
            if (selectedIdx >= 0 && items[selectedIdx]) {
                e.preventDefault();
                input.value = items[selectedIdx].dataset.cmd + ' ';
                autocomplete.style.display = 'none';
                selectedIdx = -1;
            } else {
                sendCommand();
            }
            return;
        }

        if (e.key === 'Escape') {
            if (autocomplete.style.display === 'block') {
                autocomplete.style.display = 'none';
                selectedIdx = -1;
            } else {
                input.blur();
            }
            return;
        }

        // Arrow key navigation — autocomplete first, then command history
        if (autocomplete.style.display === 'block' && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault();
            if (e.key === 'ArrowDown') {
                selectedIdx = selectedIdx < items.length - 1 ? selectedIdx + 1 : 0;
            } else {
                selectedIdx = selectedIdx > 0 ? selectedIdx - 1 : items.length - 1;
            }
            // Highlight selected
            items.forEach((item, i) => {
                item.style.background = i === selectedIdx ? 'rgba(0, 212, 255, 0.1)' : 'transparent';
            });
            // Scroll selected into view
            if (items[selectedIdx]) {
                items[selectedIdx].scrollIntoView({ block: 'nearest' });
            }
            return;
        }

        // Command history navigation (when autocomplete is NOT showing)
        if (autocomplete.style.display !== 'block' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
            if (commandHistory.length === 0) return;
            e.preventDefault();
            if (e.key === 'ArrowUp') {
                if (historyIndex < commandHistory.length - 1) {
                    historyIndex++;
                }
            } else {
                if (historyIndex > 0) {
                    historyIndex--;
                } else {
                    historyIndex = -1;
                    input.value = '';
                    return;
                }
            }
            input.value = commandHistory[commandHistory.length - 1 - historyIndex] || '';
            // Move cursor to end
            setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
            return;
        }

        // Tab completion
        if (e.key === 'Tab' && autocomplete.style.display === 'block') {
            e.preventDefault();
            const target = selectedIdx >= 0 && items[selectedIdx] ? items[selectedIdx] : items[0];
            if (target) {
                input.value = target.dataset.cmd + ' ';
                autocomplete.style.display = 'none';
                selectedIdx = -1;
            }
        }
    });

    input.addEventListener('input', updateAutocomplete);

    input.addEventListener('blur', () => {
        setTimeout(() => { autocomplete.style.display = 'none'; selectedIdx = -1; }, 150);
    });

    input.addEventListener('focus', () => {
        showKbdHint();
    });
}

// ── KEYBOARD SHORTCUTS ──────────────────────────────────
function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const input = document.getElementById('command-input');
        if (!input) return;

        // Don't capture if typing in auth input
        if (document.activeElement?.id === 'auth-token-input') return;

        // / key focuses command input (when not already focused)
        if (e.key === '/' && document.activeElement !== input) {
            e.preventDefault();
            input.focus();
            input.value = '/';
            // Trigger autocomplete
            input.dispatchEvent(new Event('input'));
            return;
        }

        // Escape blurs input
        if (e.key === 'Escape' && document.activeElement === input) {
            input.blur();
            return;
        }
    });
}

function showKbdHint() {
    const hint = document.getElementById('kbd-hint');
    if (!hint) return;
    hint.classList.add('visible');
    if (kbdHintTimer) clearTimeout(kbdHintTimer);
    kbdHintTimer = setTimeout(() => {
        hint.classList.remove('visible');
    }, 3000);
}

// ── FEED ────────────────────────────────────────────────
function appendToFeed(time, message, cssClass) {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;

    if (!cssClass) {
        if (message.startsWith('> ')) {
            cssClass = 'feed-command';
        } else if (message.startsWith('Leon:')) {
            cssClass = 'feed-response';
        } else if (message.includes('completed') || message.includes('finished')) {
            cssClass = 'feed-agent-ok';
        } else if (message.includes('failed') || message.includes('error')) {
            cssClass = 'feed-agent-fail';
        } else {
            cssClass = 'feed-local';
        }
    }

    const div = document.createElement('div');
    div.className = `feed-item ${cssClass}`;
    div.innerHTML = `<span class="feed-time">${escapeHtml(time)}</span> ${escapeHtml(message)}`;
    feed.appendChild(div);

    // Bound feed size
    while (feed.children.length > 200) {
        feed.removeChild(feed.firstChild);
    }

    // Smooth scroll to bottom
    requestAnimationFrame(() => {
        feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' });
    });

    // Update count
    const countEl = document.getElementById('chat-count');
    if (countEl) countEl.textContent = feed.children.length;

    localFeedItems.push({ time, message });
    if (localFeedItems.length > 200) {
        localFeedItems = localFeedItems.slice(-100);
    }
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
