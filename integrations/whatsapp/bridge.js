/**
 * Leon WhatsApp Bridge
 *
 * Connects to WhatsApp Web via QR code, listens for messages from
 * allowed phone numbers, and relays them to Leon's /api/message endpoint.
 *
 * How it works: you message yourself in WhatsApp ("Message Yourself" chat),
 * the bridge picks it up, sends it to Leon, and Leon's reply appears in
 * the same chat.
 *
 * Environment variables:
 *   LEON_API_URL      - Leon dashboard URL (default: http://127.0.0.1:3000)
 *   LEON_API_TOKEN    - Bearer token for /api/message auth
 *   LEON_WHATSAPP_ALLOWED - Comma-separated allowed phone numbers (e.g. "15551234567")
 */

const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const QRCode = require("qrcode");
const http = require("http");
const https = require("https");

// ── Config ──────────────────────────────────────────────
const API_URL = process.env.LEON_API_URL || "http://127.0.0.1:3000";
const API_TOKEN = process.env.LEON_API_TOKEN;
const ALLOWED_RAW = process.env.LEON_WHATSAPP_ALLOWED || "";
const ALLOWED_NUMBERS = new Set(
  ALLOWED_RAW.split(",")
    .map((n) => n.trim())
    .filter(Boolean)
);

const HTTP_TIMEOUT_MS = 120_000; // 2 min — AI responses can be slow
const MAX_CHUNK = 4000; // WhatsApp message length limit
const MAX_RECONNECTS = Infinity; // never give up
const RECONNECT_DELAY_MS = 10_000;
const MAX_RECONNECT_DELAY_MS = 5 * 60_000; // cap at 5 min between retries
const KEEPALIVE_INTERVAL_MS = 10 * 60_000; // ping every 10 min to keep session alive

if (!API_TOKEN) {
  console.error("[bridge] LEON_API_TOKEN is required. Check Leon dashboard output for the token.");
  process.exit(1);
}

if (ALLOWED_NUMBERS.size === 0) {
  console.error("[bridge] LEON_WHATSAPP_ALLOWED is required. Set to your phone number (e.g. 15551234567).");
  process.exit(1);
}

console.log(`[bridge] Allowed numbers: ${[...ALLOWED_NUMBERS].join(", ")}`);
console.log(`[bridge] Leon API: ${API_URL}/api/message`);

// Track message IDs sent by the bridge so we don't reply to our own responses.
// Uses a Map<id, timestamp> with TTL eviction to prevent unbounded growth.
const sentByBridge = new Map();
const SENT_TTL_MS = 5 * 60_000; // 5 minutes
let myNumber = null;
let processing = false; // Prevent overlapping/looping message handling
let reconnectCount = 0;

// Evict stale entries from sentByBridge every 2 minutes
setInterval(() => {
  const cutoff = Date.now() - SENT_TTL_MS;
  for (const [id, ts] of sentByBridge) {
    if (ts < cutoff) sentByBridge.delete(id);
  }
}, 2 * 60_000);

// ── WhatsApp Client ─────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: ".wwebjs_auth" }),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--no-zygote",
      "--disable-gpu",
    ],
  },
  restartOnAuthFail: true,
});

client.on("qr", (qr) => {
  console.log("\n[bridge] Scan this QR code with WhatsApp:\n");
  qrcode.generate(qr, { small: true });
  // Also save as PNG so it's easy to open and scan
  const pngPath = "/tmp/leon_whatsapp_qr.png";
  QRCode.toFile(pngPath, qr, { scale: 8 }, (err) => {
    if (!err) console.log(`[bridge] QR saved as image: ${pngPath}`);
  });
});

client.on("ready", async () => {
  console.log("[bridge] WhatsApp client ready — listening for messages");
  const info = client.info;
  myNumber = info.wid.user;
  console.log(`[bridge] My number: ${myNumber}`);

  // Keepalive — fetch chats every 10 min to prevent session expiry
  setInterval(async () => {
    if (!clientReady) return;
    try {
      await client.getChats();
      console.log("[bridge] Keepalive ping OK");
    } catch (err) {
      console.warn(`[bridge] Keepalive failed: ${err.message}`);
    }
  }, KEEPALIVE_INTERVAL_MS);
});

client.on("authenticated", () => {
  console.log("[bridge] Session authenticated (QR not needed next time)");
});

client.on("auth_failure", (msg) => {
  console.error(`[bridge] Auth failure: ${msg}`);
});

client.on("disconnected", (reason) => {
  console.warn(`[bridge] Disconnected: ${reason}`);
  clientReady = false;

  reconnectCount++;
  const delay = Math.min(RECONNECT_DELAY_MS * reconnectCount, MAX_RECONNECT_DELAY_MS);
  console.log(`[bridge] Reconnecting in ${delay / 1000}s (attempt ${reconnectCount})...`);
  setTimeout(() => {
    console.log("[bridge] Attempting reconnect...");
    client.initialize().catch((err) => {
      console.error(`[bridge] Reconnect failed: ${err.message}`);
    });
  }, delay);
});

// ── Message handler ─────────────────────────────────────
client.on("message_create", async (msg) => {
  // Debug: log every message so we can see what's coming through
  const from = msg.from || "unknown";
  const to = msg.to || "unknown";
  console.log(`[bridge] DEBUG: message_create — from=${from} to=${to} fromMe=${msg.fromMe} body="${(msg.body || "").substring(0, 50)}"`);

  // Skip group messages
  if (from.includes("@g.us") || to.includes("@g.us")) return;
  // Skip status broadcasts
  if (from === "status@broadcast" || to === "status@broadcast") return;

  // Skip messages sent by the bridge itself (Leon's replies)
  const msgId = msg.id && msg.id._serialized;
  if (msgId && sentByBridge.has(msgId)) {
    sentByBridge.delete(msgId);
    return;
  }

  if (msg.fromMe) {
    // This is a message WE sent from our phone.
    // Only process if it's in the self-chat ("Message Yourself").
    // Self-chat uses @lid format (new) or @c.us with own number (old).
    const toId = to.replace("@c.us", "").replace("@lid", "");
    const isSelfChat = to.endsWith("@lid") || (myNumber && toId === myNumber);
    if (!isSelfChat) {
      // Not self-chat — it's a message we sent to someone else, ignore
      return;
    }
    console.log(`[bridge] Self-chat message detected`);
  } else {
    // Incoming message from someone else — check allowlist
    const phone = from.replace("@c.us", "");
    if (!ALLOWED_NUMBERS.has(phone)) {
      console.log(`[bridge] Ignored message from non-allowed: ${phone}`);
      return;
    }
  }

  const text = msg.body?.trim();
  if (!text) return;

  // Don't process messages that look like bridge responses
  if (text.startsWith("[Leon]") || text === "Leon may be offline or unreachable. Try again shortly.") return;

  // Prevent overlapping — if we're already processing, skip
  if (processing) {
    console.log(`[bridge] Skipping (already processing a message)`);
    return;
  }
  processing = true;

  console.log(`[bridge] Processing: "${text.substring(0, 80)}${text.length > 80 ? "..." : ""}"`);

  try {
    const leonReply = await postToLeon(text);
    const responseText = typeof leonReply === "string" ? leonReply : leonReply.response || "";
    const audioBase64 = typeof leonReply === "object" ? leonReply.audio_base64 : null;
    const audioMime = typeof leonReply === "object" ? leonReply.audio_mime : null;

    console.log(`[bridge] Leon response: ${responseText.substring(0, 80)}${responseText.length > 80 ? "..." : ""}`);

    const chat = await msg.getChat();

    // Send text response (split if needed)
    if (responseText) {
      const chunks = splitMessage(responseText);
      for (const chunk of chunks) {
        const replyText = `[Leon] ${chunk}`;
        const sent = await chat.sendMessage(replyText);
        if (sent && sent.id && sent.id._serialized) {
          sentByBridge.set(sent.id._serialized, Date.now());
        }
      }
    }

    // Send voice note if audio was generated
    if (audioBase64 && audioMime) {
      try {
        const media = new MessageMedia(audioMime, audioBase64, "leon_response.mp3");
        const voiceSent = await chat.sendMessage(media, { sendAudioAsVoice: true });
        if (voiceSent && voiceSent.id && voiceSent.id._serialized) {
          sentByBridge.set(voiceSent.id._serialized, Date.now());
        }
        console.log("[bridge] Voice note sent");
      } catch (voiceErr) {
        console.warn(`[bridge] Voice note failed: ${voiceErr.message}`);
        // Not fatal — text was already sent
      }
    }
  } catch (err) {
    console.error(`[bridge] Error: ${err.message}`);
    // Don't send error messages to chat — just log it. Prevents loops.
    console.error(`[bridge] NOT sending error to chat (loop prevention)`);
  } finally {
    processing = false;
  }
});

// ── HTTP POST to Leon ───────────────────────────────────
function postToLeon(message) {
  return new Promise((resolve, reject) => {
    const url = new URL("/api/message", API_URL);
    const isHttps = url.protocol === "https:";
    const transport = isHttps ? https : http;

    const body = JSON.stringify({ message, source: "whatsapp" });

    const req = transport.request(
      {
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${API_TOKEN}`,
          "Content-Length": Buffer.byteLength(body),
        },
        timeout: HTTP_TIMEOUT_MS,
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          if (res.statusCode === 200) {
            try {
              const parsed = JSON.parse(data);
              // Return full object so caller can access audio_base64, etc.
              resolve(parsed);
            } catch {
              resolve({ response: data });
            }
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${data.substring(0, 200)}`));
          }
        });
      }
    );

    req.on("timeout", () => {
      req.destroy();
      reject(new Error("Request timed out (120s)"));
    });

    req.on("error", (err) => reject(err));
    req.write(body);
    req.end();
  });
}

// ── Split long messages ─────────────────────────────────
function splitMessage(text) {
  if (text.length <= MAX_CHUNK) return [text];

  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= MAX_CHUNK) {
      chunks.push(remaining);
      break;
    }
    let splitAt = remaining.lastIndexOf("\n", MAX_CHUNK);
    if (splitAt < MAX_CHUNK * 0.5) {
      splitAt = remaining.lastIndexOf(" ", MAX_CHUNK);
    }
    if (splitAt < MAX_CHUNK * 0.3) {
      splitAt = MAX_CHUNK;
    }
    chunks.push(remaining.substring(0, splitAt));
    remaining = remaining.substring(splitAt).trimStart();
  }
  return chunks;
}

// ── Outbound HTTP server (for Leon to send proactive messages) ──
const OUTBOUND_PORT = 3001;
let clientReady = false;
const outboundServer = http.createServer(async (req, res) => {
  if (req.method === "POST" && req.url === "/send") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        if (!clientReady) {
          res.writeHead(503);
          res.end(JSON.stringify({ error: "WhatsApp not ready yet, try again in 30s" }));
          return;
        }
        const { number, message, audio_base64, audio_mime } = JSON.parse(body);
        if (!number || !message) {
          res.writeHead(400);
          res.end(JSON.stringify({ error: "number and message required" }));
          return;
        }
        // Send to the "Message Yourself" chat (self-chat) or a specific number
        const chatId = number.includes("@") ? number : `${number}@c.us`;

        // Send text
        const sent = await client.sendMessage(chatId, `[Leon] ${message}`);
        if (sent && sent.id && sent.id._serialized) {
          sentByBridge.set(sent.id._serialized, Date.now());
        }

        // Send voice note if audio provided
        if (audio_base64 && audio_mime) {
          try {
            const media = new MessageMedia(audio_mime, audio_base64, "leon_update.mp3");
            const voiceSent = await client.sendMessage(chatId, media, { sendAudioAsVoice: true });
            if (voiceSent && voiceSent.id && voiceSent.id._serialized) {
              sentByBridge.set(voiceSent.id._serialized, Date.now());
            }
            console.log(`[bridge] Outbound voice note sent to ${number}`);
          } catch (voiceErr) {
            console.warn(`[bridge] Outbound voice note failed: ${voiceErr.message}`);
          }
        }

        console.log(`[bridge] Outbound to ${number}: ${message.substring(0, 60)}...`);
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true }));
      } catch (err) {
        console.error(`[bridge] Outbound error: ${err.message}`);
        res.writeHead(500);
        res.end(JSON.stringify({ error: err.message }));
        // Detached frame = Chrome page is dead — exit so watchdog restarts us clean
        if (err.message && err.message.includes('detached Frame')) {
          console.error('[bridge] Detached frame detected — exiting for clean restart');
          setTimeout(() => process.exit(1), 500);
        }
      }
    });
  } else if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200);
    res.end(JSON.stringify({
      status: "ok",
      whatsapp_ready: clientReady,
      my_number: myNumber || null,
      reconnect_count: reconnectCount,
      uptime_seconds: Math.floor(process.uptime()),
    }));
  } else {
    res.writeHead(404);
    res.end("Not found");
  }
});

// ── Start ───────────────────────────────────────────────
console.log("[bridge] Starting WhatsApp bridge...");

// Start outbound server immediately (doesn't need WhatsApp ready)
outboundServer.listen(OUTBOUND_PORT, "127.0.0.1", () => {
  console.log(`[bridge] Outbound API listening on http://127.0.0.1:${OUTBOUND_PORT}/send`);
});

client.on("ready", () => {
  clientReady = true;
  reconnectCount = 0;
  console.log("[bridge] WhatsApp ready — outbound messages enabled");
});
client.initialize().catch((err) => {
  console.error(`[bridge] Failed to initialize: ${err.message}`);
  process.exit(1);
});

// Graceful shutdown
process.on("SIGINT", async () => {
  console.log("\n[bridge] Shutting down...");
  await client.destroy();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  console.log("[bridge] SIGTERM received, shutting down...");
  await client.destroy();
  process.exit(0);
});
