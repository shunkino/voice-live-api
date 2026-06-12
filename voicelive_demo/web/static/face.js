// -------------------------------------------------------------------------
// Local face model driven by Voice Live animation cues.
//
// Azure's Voice Live API can emit an *animation* stream alongside the audio:
//   * viseme IDs   (0-21, the standard Azure Speech viseme set) + audio offset
//   * 3D blendshapes (ARKit-style channels, 60 fps)
//
// This module renders a simple 2D face (SVG) **in the browser** and maps those
// cues onto its rig. It is intentionally self-contained (no external 3D assets
// or CDNs) so it works offline, but the mapping layer below — `visemeToShape`
// and `blendshapesToRig` — is exactly what you would feed into a Three.js /
// Unity / Unreal rig instead: turn the cue into rig parameters, then apply.
// -------------------------------------------------------------------------

// Azure Speech viseme id (0-21) -> target mouth-rig parameters.
//   jaw   : vertical mouth opening   (0 closed .. 1 wide open)
//   wide  : horizontal stretch       (0 neutral .. 1 "eee" smile-wide)
//   round : lip rounding / pucker    (0 neutral .. 1 "ooo")
//   press : pressed/closed lips      (0 .. 1, for p/b/m and f/v)
const VISEME_SHAPES = {
  0: { jaw: 0.0, wide: 0.12, round: 0.0, press: 0.0 }, // silence
  1: { jaw: 0.5, wide: 0.4, round: 0.0, press: 0.0 }, // æ ʌ ə
  2: { jaw: 0.9, wide: 0.3, round: 0.0, press: 0.0 }, // ɑ
  3: { jaw: 0.6, wide: 0.1, round: 0.5, press: 0.0 }, // ɔ
  4: { jaw: 0.4, wide: 0.5, round: 0.0, press: 0.0 }, // ɛ ʊ
  5: { jaw: 0.35, wide: 0.3, round: 0.3, press: 0.0 }, // ɝ
  6: { jaw: 0.25, wide: 0.8, round: 0.0, press: 0.0 }, // j i ɪ
  7: { jaw: 0.3, wide: 0.0, round: 0.9, press: 0.0 }, // w u
  8: { jaw: 0.5, wide: 0.1, round: 0.7, press: 0.0 }, // o
  9: { jaw: 0.7, wide: 0.2, round: 0.4, press: 0.0 }, // aʊ
  10: { jaw: 0.5, wide: 0.2, round: 0.5, press: 0.0 }, // ɔɪ
  11: { jaw: 0.7, wide: 0.3, round: 0.1, press: 0.0 }, // aɪ
  12: { jaw: 0.4, wide: 0.3, round: 0.0, press: 0.0 }, // h
  13: { jaw: 0.35, wide: 0.2, round: 0.4, press: 0.0 }, // ɹ
  14: { jaw: 0.4, wide: 0.4, round: 0.0, press: 0.0 }, // l
  15: { jaw: 0.15, wide: 0.6, round: 0.0, press: 0.0 }, // s z
  16: { jaw: 0.2, wide: 0.3, round: 0.5, press: 0.0 }, // ʃ tʃ dʒ ʒ
  17: { jaw: 0.2, wide: 0.4, round: 0.0, press: 0.0 }, // ð
  18: { jaw: 0.12, wide: 0.5, round: 0.0, press: 0.4 }, // f v
  19: { jaw: 0.25, wide: 0.4, round: 0.0, press: 0.0 }, // d t n θ
  20: { jaw: 0.3, wide: 0.3, round: 0.0, press: 0.0 }, // k g ŋ
  21: { jaw: 0.06, wide: 0.3, round: 0.0, press: 0.9 }, // p b m
};

const NEUTRAL = { jaw: 0, wide: 0.12, round: 0, press: 0, smile: 0, brow: 0, blink: 0 };

function visemeToShape(id) {
  return VISEME_SHAPES[id] || VISEME_SHAPES[0];
}

// ARKit-style blendshape channel order used by Azure's 3D blendshape output.
// We only read a curated subset and fold it into the same rig parameters so
// the face also shows brows / blinks / smiles, not just lip-sync.
const BS = {
  eyeBlinkLeft: 0,
  eyeBlinkRight: 7,
  jawOpen: 17,
  mouthClose: 18,
  mouthFunnel: 19,
  mouthPucker: 20,
  mouthSmileLeft: 23,
  mouthSmileRight: 24,
  mouthPressLeft: 35,
  mouthPressRight: 36,
  browDownLeft: 41,
  browDownRight: 42,
  browInnerUp: 43,
  browOuterUpLeft: 44,
  browOuterUpRight: 45,
};

function bsVal(frame, idx) {
  return idx < frame.length ? frame[idx] || 0 : 0;
}

function blendshapesToRig(frame) {
  const smile = (bsVal(frame, BS.mouthSmileLeft) + bsVal(frame, BS.mouthSmileRight)) / 2;
  const press = (bsVal(frame, BS.mouthPressLeft) + bsVal(frame, BS.mouthPressRight)) / 2;
  const round = Math.max(bsVal(frame, BS.mouthPucker), bsVal(frame, BS.mouthFunnel));
  const browUp =
    (bsVal(frame, BS.browInnerUp) +
      bsVal(frame, BS.browOuterUpLeft) +
      bsVal(frame, BS.browOuterUpRight)) /
    3;
  const browDown = (bsVal(frame, BS.browDownLeft) + bsVal(frame, BS.browDownRight)) / 2;
  const blink = (bsVal(frame, BS.eyeBlinkLeft) + bsVal(frame, BS.eyeBlinkRight)) / 2;
  const jaw = Math.max(0, bsVal(frame, BS.jawOpen) - bsVal(frame, BS.mouthClose));
  return {
    jaw,
    wide: smile * 0.8,
    round,
    press,
    smile,
    brow: browUp - browDown,
    blink,
  };
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

// ---------------------------------------------------------------------------
// FaceRig — a small SVG face whose rig parameters are animated each frame.
// ---------------------------------------------------------------------------
const SVGNS = "http://www.w3.org/2000/svg";

class FaceRig {
  constructor(container) {
    this.container = container;
    // current (rendered) and target rig state; we ease current -> target.
    this.cur = { ...NEUTRAL };
    this.target = { ...NEUTRAL };
    this._build();
    this._raf = null;
    this._blinkTimer = 0;
    this._render();
  }

  _build() {
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 200 240");
    svg.setAttribute("class", "face-svg");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    const mk = (tag, attrs) => {
      const el = document.createElementNS(SVGNS, tag);
      for (const k in attrs) el.setAttribute(k, attrs[k]);
      svg.appendChild(el);
      return el;
    };

    // Head
    mk("ellipse", { cx: 100, cy: 120, rx: 78, ry: 92, class: "face-head" });
    // Brows
    this.browL = mk("rect", { x: 52, y: 78, width: 36, height: 7, rx: 3.5, class: "face-brow" });
    this.browR = mk("rect", { x: 112, y: 78, width: 36, height: 7, rx: 3.5, class: "face-brow" });
    // Eyes (white + pupil + lid for blink)
    mk("ellipse", { cx: 70, cy: 102, rx: 16, ry: 12, class: "face-eye-white" });
    mk("ellipse", { cx: 130, cy: 102, rx: 16, ry: 12, class: "face-eye-white" });
    this.pupilL = mk("circle", { cx: 70, cy: 102, r: 6, class: "face-pupil" });
    this.pupilR = mk("circle", { cx: 130, cy: 102, r: 6, class: "face-pupil" });
    this.lidL = mk("rect", { x: 54, y: 90, width: 32, height: 0, class: "face-lid" });
    this.lidR = mk("rect", { x: 114, y: 90, width: 32, height: 0, class: "face-lid" });
    // Nose
    mk("path", { d: "M100 110 L94 138 Q100 144 106 138 Z", class: "face-nose" });
    // Mouth (filled cavity + lips outline)
    this.mouthFill = mk("path", { d: "", class: "face-mouth-fill" });
    this.mouth = mk("path", { d: "", class: "face-mouth" });

    this.svg = svg;
    this.container.appendChild(svg);
  }

  setTarget(rig) {
    // Merge so partial updates (viseme-only) keep brow/blink from blendshapes.
    this.target = { ...this.target, ...rig };
  }

  neutral() {
    this.target = { ...NEUTRAL };
  }

  start() {
    if (this._raf) return;
    const loop = () => {
      this._step();
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  _step() {
    // Idle micro-blink so a quiet face still feels alive.
    this._blinkTimer -= 1;
    let blinkBoost = 0;
    if (this._blinkTimer < 0) {
      if (this._blinkTimer > -8) blinkBoost = 1; // ~8 frames of blink
      else if (this._blinkTimer < -180) this._blinkTimer = 0; // ~3s cadence
    }

    const c = this.cur;
    const t = this.target;
    // Mouth eases fast (lip-sync needs to be snappy), expression eases slower.
    c.jaw = lerp(c.jaw, t.jaw, 0.45);
    c.wide = lerp(c.wide, t.wide, 0.45);
    c.round = lerp(c.round, t.round, 0.4);
    c.press = lerp(c.press, t.press, 0.5);
    c.smile = lerp(c.smile, t.smile, 0.2);
    c.brow = lerp(c.brow, t.brow, 0.2);
    c.blink = Math.max(lerp(c.blink, t.blink, 0.3), blinkBoost);
    this._render();
  }

  _render() {
    const c = this.cur;

    // ---- Mouth geometry ----
    const cx = 100;
    const cy = 170;
    const press = c.press;
    // width shrinks with rounding, grows with "wide"; clamps for sanity.
    let halfW = 30 + c.wide * 18 - c.round * 16;
    halfW = Math.max(10, halfW);
    // opening height from jaw, suppressed by lip press.
    const openH = Math.max(0, c.jaw * 34 * (1 - press * 0.9));
    const top = cy - openH / 2;
    const bot = cy + openH / 2;
    // smile curve raises the corners.
    const corner = -c.smile * 10 + (1 - c.jaw) * 2;

    const lx = cx - halfW;
    const rx = cx + halfW;
    const upper = `M ${lx} ${cy + corner} Q ${cx} ${top - 2} ${rx} ${cy + corner}`;
    const lowerOpen = `Q ${cx} ${bot + 4} ${lx} ${cy + corner}`;
    const lowerClosed = `Q ${cx} ${cy + 3 - corner} ${lx} ${cy + corner}`;
    const path = openH > 2 ? `${upper} ${lowerOpen} Z` : `${upper} ${lowerClosed}`;
    this.mouth.setAttribute("d", path);
    this.mouthFill.setAttribute("d", openH > 2 ? `${upper} ${lowerOpen} Z` : "");

    // ---- Brows ----
    const browShift = -c.brow * 8;
    this.browL.setAttribute("y", 78 + browShift);
    this.browR.setAttribute("y", 78 + browShift);

    // ---- Blink (eyelid height covers the eye) ----
    const lid = Math.max(0, Math.min(1, c.blink)) * 24;
    this.lidL.setAttribute("height", lid);
    this.lidR.setAttribute("height", lid);
  }
}

// ---------------------------------------------------------------------------
// AnimationScheduler — plays viseme/blendshape cues in sync with the audio.
//
// Cues carry a timeline position (audio_offset_ms for visemes, frame_index/60
// for blendshapes) relative to the *start of the response audio*. We anchor
// that timeline to the wall clock the moment audio playback actually begins,
// then a rAF loop applies whichever cue is current.
// ---------------------------------------------------------------------------
class AnimationScheduler {
  constructor(rig) {
    this.rig = rig;
    this.visemes = []; // {t, id}
    this.frames = []; // {t, frame}
    this.anchor = null; // performance.now() at audio offset 0
    this._raf = null;
  }

  reset() {
    this.visemes = [];
    this.frames = [];
    this.anchor = null;
    this.rig.neutral();
  }

  // Called when the response's audio actually starts playing.
  setAnchor(whenMs) {
    this.anchor = whenMs;
  }

  pushViseme(id, offsetMs) {
    this.visemes.push({ t: offsetMs, id });
  }

  pushFrame(frame, frameIndex) {
    this.frames.push({ t: (frameIndex * 1000) / 60, frame });
  }

  start() {
    this.rig.start();
    if (this._raf) return;
    const loop = () => {
      this._tick();
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this.rig.stop();
  }

  _tick() {
    if (this.anchor == null) return;
    const pos = performance.now() - this.anchor;

    // Latest viseme whose offset has passed.
    let v = null;
    for (let i = 0; i < this.visemes.length; i++) {
      if (this.visemes[i].t <= pos) v = this.visemes[i];
      else break;
    }
    // Latest blendshape frame whose time has passed.
    let f = null;
    for (let i = 0; i < this.frames.length; i++) {
      if (this.frames[i].t <= pos) f = this.frames[i];
      else break;
    }

    if (f) {
      // Blendshapes are the richest signal: drive the whole face from them.
      this.rig.setTarget(blendshapesToRig(f.frame));
    } else if (v) {
      // Viseme-only: drive the mouth, leave brows/blink to the idle animation.
      this.rig.setTarget(visemeToShape(v.id));
    }

    // Drop cues we've well passed (small backlog kept for safety).
    while (this.visemes.length > 2 && this.visemes[1].t < pos - 500) {
      this.visemes.shift();
    }
    while (this.frames.length > 2 && this.frames[1].t < pos - 500) {
      this.frames.shift();
    }
  }
}

window.FaceRig = FaceRig;
window.AnimationScheduler = AnimationScheduler;
