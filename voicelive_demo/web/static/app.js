// -------------------------------------------------------------------------
// Voice Live Experiments — browser client.
//
// Responsibilities:
//   * capture the microphone, downsample to PCM16/24k, stream over /ws
//   * negotiate a WebRTC peer connection for the avatar (feature #2) and play
//     its audio+video track
//   * when the avatar is off, play the model's audio deltas via Web Audio
//   * render input/output transcripts (feature #3)
// -------------------------------------------------------------------------

const TARGET_RATE = 24000; // Voice Live PCM sample rate

const els = {
  summary: document.getElementById("summary"),
  transModel: document.getElementById("transModel"),
  startBtn: document.getElementById("startBtn"),
  stopBtn: document.getElementById("stopBtn"),
  status: document.getElementById("status"),
  transcript: document.getElementById("transcript"),
  avatar: document.getElementById("avatar"),
  avatarPlaceholder: document.getElementById("avatarPlaceholder"),
  placeholderText: document.getElementById("placeholderText"),
  stateBadge: document.getElementById("stateBadge"),
  stateDot: document.getElementById("stateDot"),
  faceModel: document.getElementById("faceModel"),
};

let appConfig = {
  avatar: false,
  faceModel: false,
  viseme: false,
  blendshapes: false,
  transcriptionModel: "—",
  summary: "",
};
let ws = null;
let pc = null;
let micStream = null;
let captureCtx = null;
let captureNode = null;
let playback = null; // PCM playback scheduler (avatar-off)
let faceRig = null; // local face model (avatar-off + animation)
let animScheduler = null; // viseme/blendshape -> face rig
let running = false;

// Interim transcript bubbles, keyed by role.
const interim = { user: null, assistant: null };

init();

async function init() {
  try {
    const res = await fetch("/config");
    appConfig = await res.json();
  } catch (e) {
    console.warn("Could not load /config", e);
  }
  els.summary.textContent = appConfig.summary || "";
  els.transModel.textContent = appConfig.transcriptionModel || "—";
  if (appConfig.faceModel) {
    els.placeholderText.textContent = "Local face model — speak to animate it.";
  } else {
    els.placeholderText.textContent = appConfig.avatar
      ? "Connecting avatar…"
      : "Avatar is off — audio only.";
  }
  els.startBtn.addEventListener("click", start);
  els.stopBtn.addEventListener("click", stop);
}

function setStatus(text, live) {
  els.status.textContent = text;
  els.status.classList.toggle("live", !!live);
}

async function start() {
  if (running) return;
  running = true;
  els.startBtn.disabled = true;
  els.stopBtn.disabled = false;
  setStatus("requesting microphone…");

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
  } catch (e) {
    setStatus("microphone denied");
    running = false;
    els.startBtn.disabled = false;
    els.stopBtn.disabled = true;
    return;
  }

  if (!appConfig.avatar) {
    playback = new PcmPlayer(TARGET_RATE);
  }

  if (appConfig.faceModel && window.FaceRig) {
    faceRig = new FaceRig(els.faceModel);
    animScheduler = new AnimationScheduler(faceRig);
    animScheduler.start();
    // Anchor the animation timeline to the moment audio actually starts.
    if (playback) {
      playback.onUtteranceStart = (whenMs) => {
        if (animScheduler) animScheduler.setAnchor(whenMs);
      };
    }
    els.faceModel.classList.add("active");
    els.avatarPlaceholder.style.display = "none";
  }

  connectWebSocket();
}

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setStatus("connected — configuring session…", true);
  ws.onclose = () => {
    setStatus("disconnected");
    if (running) stop();
  };
  ws.onerror = () => setStatus("connection error");
  ws.onmessage = (ev) => handleServerMessage(JSON.parse(ev.data));
}

async function handleServerMessage(msg) {
  switch (msg.type) {
    case "session_ready":
      setStatus("session ready — speak now", true);
      startMicCapture();
      if (msg.avatar) {
        await startAvatar(msg.iceServers || []);
      }
      break;

    case "avatar_answer":
      if (pc) {
        await pc.setRemoteDescription({ type: "answer", sdp: msg.sdp });
      }
      break;

    case "speech_started":
      if (playback) playback.flush(); // barge-in
      if (animScheduler) animScheduler.reset(); // face back to neutral
      // Anchor the user's bubble at speech start. Input-audio transcription
      // completes asynchronously and often arrives only after the assistant
      // has already begun replying; pre-creating the bubble here keeps the
      // turn order correct (user before assistant).
      ensureInterim("user", appConfig.transcriptionModel);
      break;

    case "animation_started":
      if (animScheduler) animScheduler.reset();
      // Re-arm the audio anchor so the next response's first audio chunk
      // re-establishes the animation timeline (without this, only the first
      // response would animate).
      if (playback) playback.started = false;
      break;

    case "viseme":
      if (animScheduler) animScheduler.pushViseme(msg.visemeId, msg.audioOffsetMs);
      break;

    case "blendshapes":
      if (animScheduler && Array.isArray(msg.frames)) {
        msg.frames.forEach((frame, i) =>
          animScheduler.pushFrame(frame, msg.frameIndex + i)
        );
      }
      break;

    case "animation_done":
      break;

    case "transcript_delta":
      appendInterim(msg.role, msg.delta, msg.model);
      break;

    case "transcript_final":
      finalizeTranscript(msg.role, msg.text, msg.model);
      break;

    case "audio_delta":
      if (playback && msg.audio) playback.enqueueBase64(msg.audio);
      break;

    case "avatar_state":
      setAvatarState(msg.state);
      break;

    case "error":
      addBubble("assistant", `⚠️ ${msg.message}`);
      break;
  }
}

// ---------------------------------------------------------------------------
// Microphone capture -> PCM16/24k -> base64 over WS
// ---------------------------------------------------------------------------
function startMicCapture() {
  if (captureCtx) return;
  captureCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = captureCtx.createMediaStreamSource(micStream);
  const bufferSize = 4096;
  captureNode = captureCtx.createScriptProcessor(bufferSize, 1, 1);

  captureNode.onaudioprocess = (e) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const input = e.inputBuffer.getChannelData(0);
    const down = downsample(input, captureCtx.sampleRate, TARGET_RATE);
    const pcm16 = floatToPcm16(down);
    const b64 = arrayBufferToBase64(pcm16.buffer);
    ws.send(JSON.stringify({ type: "input_audio", audio: b64 }));
  };

  source.connect(captureNode);
  // ScriptProcessor needs a sink to fire; route to a muted gain node.
  const sink = captureCtx.createGain();
  sink.gain.value = 0;
  captureNode.connect(sink);
  sink.connect(captureCtx.destination);
}

// ---------------------------------------------------------------------------
// Avatar WebRTC (feature #2)
// ---------------------------------------------------------------------------
async function startAvatar(iceServers) {
  pc = new RTCPeerConnection({ iceServers });

  pc.ontrack = (event) => {
    const [stream] = event.streams;
    if (event.track.kind === "video") {
      els.avatar.srcObject = stream;
      els.avatar.classList.add("active");
      els.avatarPlaceholder.style.display = "none";
    } else if (event.track.kind === "audio") {
      // Avatar audio rides on the same stream; the <video> element plays it.
      els.avatar.srcObject = stream;
    }
  };

  // We only receive media from the avatar.
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitForIceGathering(pc);

  ws.send(
    JSON.stringify({ type: "avatar_offer", sdp: pc.localDescription.sdp })
  );
  setStatus("negotiating avatar…", true);
}

function setAvatarState(state) {
  const speaking = state === "speaking";
  els.stateBadge.textContent = state;
  els.stateBadge.classList.toggle("speaking", speaking);
  els.stateDot.classList.toggle("speaking", speaking);
}

function waitForIceGathering(peer) {
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const check = () => {
      if (peer.iceGatheringState === "complete") {
        peer.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    peer.addEventListener("icegatheringstatechange", check);
    // Safety timeout: don't wait forever for relay candidates.
    setTimeout(resolve, 2000);
  });
}

// ---------------------------------------------------------------------------
// Transcript UI
// ---------------------------------------------------------------------------
function ensureInterim(role, model) {
  if (!interim[role]) {
    interim[role] = addBubble(role, "", model, true);
  }
  return interim[role];
}

function appendInterim(role, delta, model) {
  if (!delta) return;
  ensureInterim(role, model);
  interim[role].textNode.textContent += delta;
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function finalizeTranscript(role, text, model) {
  if (interim[role]) {
    const node = interim[role];
    interim[role] = null;
    if (text) node.textNode.textContent = text;
    // Drop a placeholder bubble that never received any transcription.
    if (!node.textNode.textContent) {
      node.bubble.remove();
      return;
    }
    node.bubble.classList.remove("interim");
  } else if (text) {
    addBubble(role, text, model);
  }
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function addBubble(role, text, model, isInterim) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}` + (isInterim ? " interim" : "");
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = role === "user" ? `you${model ? " · " + model : ""}` : "assistant";
  const textNode = document.createElement("span");
  textNode.textContent = text;
  bubble.appendChild(tag);
  bubble.appendChild(textNode);
  els.transcript.appendChild(bubble);
  els.transcript.scrollTop = els.transcript.scrollHeight;
  return { bubble, textNode };
}

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------
function stop() {
  running = false;
  els.startBtn.disabled = false;
  els.stopBtn.disabled = true;
  setStatus("disconnected");

  try {
    ws && ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "stop" }));
  } catch (_) {}
  if (ws) ws.close();
  ws = null;

  if (captureNode) captureNode.disconnect();
  if (captureCtx) captureCtx.close();
  captureNode = captureCtx = null;

  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micStream = null;

  if (pc) pc.close();
  pc = null;

  if (playback) playback.close();
  playback = null;

  if (animScheduler) animScheduler.stop();
  if (faceRig) faceRig.stop();
  animScheduler = null;
  faceRig = null;
  if (els.faceModel) {
    els.faceModel.classList.remove("active");
    els.faceModel.innerHTML = "";
  }

  els.avatar.classList.remove("active");
  els.avatar.srcObject = null;
  els.avatarPlaceholder.style.display = "";
  setAvatarState("idle");
}

// ---------------------------------------------------------------------------
// DSP helpers
// ---------------------------------------------------------------------------
function downsample(buffer, inRate, outRate) {
  if (outRate === inRate) return buffer;
  const ratio = inRate / outRate;
  const newLen = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLen);
  let offset = 0;
  for (let i = 0; i < newLen; i++) {
    const next = Math.round((i + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let j = Math.round(i * ratio); j < next && j < buffer.length; j++) {
      sum += buffer[j];
      count++;
    }
    result[i] = count ? sum / count : 0;
    offset = next;
  }
  return result;
}

function floatToPcm16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToInt16(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

// ---------------------------------------------------------------------------
// PCM playback scheduler (used when the avatar is OFF)
// ---------------------------------------------------------------------------
class PcmPlayer {
  constructor(rate) {
    this.ctx = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: rate,
    });
    this.rate = rate;
    this.nextTime = 0;
    this.sources = [];
    this.started = false; // true once the current utterance has begun
    this.onUtteranceStart = null; // (wallClockMs) => void
  }

  enqueueBase64(b64) {
    const pcm = base64ToInt16(b64);
    const float = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) float[i] = pcm[i] / 0x8000;

    const buffer = this.ctx.createBuffer(1, float.length, this.rate);
    buffer.copyToChannel(float, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);

    const now = this.ctx.currentTime;
    if (this.nextTime < now) this.nextTime = now + 0.05;
    // The first chunk of a fresh utterance defines the animation anchor: the
    // wall-clock time at which audio offset 0 is heard.
    if (!this.started) {
      this.started = true;
      if (this.onUtteranceStart) {
        const leadMs = (this.nextTime - now) * 1000;
        this.onUtteranceStart(performance.now() + leadMs);
      }
    }
    src.start(this.nextTime);
    this.nextTime += buffer.duration;
    this.sources.push(src);
    src.onended = () => {
      this.sources = this.sources.filter((s) => s !== src);
    };
  }

  flush() {
    this.sources.forEach((s) => {
      try {
        s.stop();
      } catch (_) {}
    });
    this.sources = [];
    this.nextTime = 0;
    this.started = false;
  }

  close() {
    this.flush();
    this.ctx.close();
  }
}
