/* The orb.
 *
 * One glowing core, drawn on a 2D canvas, and it is the whole status display.
 * It answers two questions at a glance: what is it doing, and -- because teal
 * means external and nothing else is ever teal -- is anything leaving this
 * machine right now.
 *
 * Never WebGL. That card is holding the models; a scene graph would compete
 * for the exact memory the agent needs. Radial gradients, a small particle
 * array and a few arcs cost nothing next to that.
 */

/* The document's palette, and the reason the orb is legible: local work is
 * amber, anything leaving is teal, refusals are red, and neutral is neutral. */
const AMBER = [217, 155, 61];
const TEAL = [61, 191, 176];
const RED = [217, 83, 79];
const NEUTRAL = [174, 181, 194];
const WHITE = [255, 255, 255];

/** What each state looks like before anything moves.
 *
 *  `bright` is the core's opacity floor; motion is added per state in draw().
 */
const LOOKS = {
  connecting: { rgb: NEUTRAL, bright: 0.18 },
  idle: { rgb: NEUTRAL, bright: 0.20 },
  listening: { rgb: NEUTRAL, bright: 0.85 },
  transcribing: { rgb: NEUTRAL, bright: 0.55 },
  thinking: { rgb: AMBER, bright: 0.75 },
  'tool.local': { rgb: AMBER, bright: 0.80 },
  'tool.external': { rgb: TEAL, bright: 0.90 },
  blocked: { rgb: RED, bright: 0.95 },
  speaking: { rgb: AMBER, bright: 0.85 },
  muted: { rgb: NEUTRAL, bright: 0.22 },
  error: { rgb: RED, bright: 0.70 },
};

/** States that are an event rather than a place. They flash over whatever is
 *  happening and the orb stays where it was afterwards -- `wake` arrives while
 *  the models are still loading, so settling the orb on it would leave the
 *  window holding a flash for as long as that takes. */
const FLASHES = {
  wake: WHITE,
};

/** Every state this orb knows what to do with. Anything else is ignored rather
 *  than guessed at -- a state it has never heard of would otherwise land as
 *  whatever the last one was, which is worse than not moving. */
export const STATES = [...Object.keys(LOOKS), ...Object.keys(FLASHES)];

/** The wake flash: one sharp expansion and out. */
const WAKE_MS = 180;
/** How long `blocked` stutters before the state underneath shows again. */
const BLOCKED_MS = 900;

const rgba = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;

/** Move `from` toward `to` at a rate that is frame-rate independent.
 *
 *  Every animated quantity here goes through this, so switching between 60 and
 *  10 fps changes the smoothness and not the speed.
 */
const ease = (from, to, rate, dt) => from + (to - from) * (1 - Math.exp(-rate * dt));

export class Orb {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{fpsFocused?: number, fpsBlurred?: number}} options
   */
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: true });
    this.fpsFocused = options.fpsFocused ?? 60;
    this.fpsBlurred = options.fpsBlurred ?? 10;

    this.state = 'connecting';
    this.muted = false;

    /* Two amplitudes, both measured rather than invented: the microphone's,
     * and the level of what is actually coming out of the speaker. The second
     * is what the speaking rings pulse with -- rings on a timer would look the
     * same and mean nothing. */
    this.micLevel = 0;
    this.outLevel = 0;
    this._mic = 0;
    this._out = 0;

    this._t = 0; // seconds since start, for the slow oscillators
    this._last = 0;
    this._acc = 0; // time banked toward the next frame
    this._raf = 0;
    this._wakeAt = -1e9;
    this._blockedAt = -1e9;
    this._blockedUnder = 'idle';

    this._bright = 0;
    this._radius = 1; // multiplier the states pull around
    this._spin = 0; // one accumulating angle for every arc
    this._particles = Array.from({ length: 14 }, (_, i) => ({
      angle: (i / 14) * Math.PI * 2,
      orbit: 0.34 + (i % 5) * 0.09,
      speed: 0.5 + (i % 4) * 0.22,
      size: i % 3 === 0 ? 1.9 : 1.3,
    }));

    this._motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
    this.reduced = this._motionQuery.matches;
    this._onMotion = (event) => {
      this.reduced = event.matches;
    };
    this._motionQuery.addEventListener('change', this._onMotion);

    this._onVisibility = () => {
      /* Paused when hidden. A window nobody is looking at has no business
       * asking for frames next to a model that wants the whole machine. */
      if (document.hidden) this.stop();
      else this.start();
    };
    document.addEventListener('visibilitychange', this._onVisibility);
    this._onFocus = () => {
      this._focused = document.hasFocus();
    };
    addEventListener('focus', this._onFocus);
    addEventListener('blur', this._onFocus);
    this._focused = document.hasFocus();

    this._resize = () => this.resize();
    addEventListener('resize', this._resize);
    this.resize();
  }

  destroy() {
    this.stop();
    document.removeEventListener('visibilitychange', this._onVisibility);
    removeEventListener('resize', this._resize);
    removeEventListener('focus', this._onFocus);
    removeEventListener('blur', this._onFocus);
    this._motionQuery.removeEventListener('change', this._onMotion);
  }

  resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const box = this.canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(box.width));
    const h = Math.max(1, Math.round(box.height));
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w;
    this.h = h;
  }

  /** One `state` event from the sidecar. */
  setState(value) {
    if (value in FLASHES) {
      this.flashWake();
      return;
    }
    if (!(value in LOOKS)) return;
    if (value === 'blocked') {
      /* Blocked is a flash over whatever was happening, not a place the orb
       * stays. Remember what it interrupted so it can go back there. */
      this._blockedAt = this._t;
      if (this.state !== 'blocked') this._blockedUnder = this.state;
      this.state = 'blocked';
      return;
    }
    this.state = value;
  }

  /** The wake flash. Deliberately not a state: it overlays whatever follows. */
  flashWake() {
    this._wakeAt = this._t;
  }

  setLevels({ mic, out }) {
    if (typeof mic === 'number') this.micLevel = mic;
    if (typeof out === 'number') this.outLevel = out;
  }

  start() {
    if (this._raf || document.hidden) return;
    this._last = performance.now();
    this._acc = 0;
    const tick = (now) => {
      this._raf = requestAnimationFrame(tick);
      const dt = Math.min(0.1, (now - this._last) / 1000);
      this._last = now;
      this._acc += dt;
      const interval = 1 / (this._focused ? this.fpsFocused : this.fpsBlurred);
      if (this._acc < interval) return;
      this.step(this._acc);
      this._acc = 0;
    };
    this._raf = requestAnimationFrame(tick);
  }

  stop() {
    if (!this._raf) return;
    cancelAnimationFrame(this._raf);
    this._raf = 0;
  }

  step(dt) {
    this._t += dt;

    /* Blocked flashes and then hands the orb back to whatever it interrupted.
     * Left as a resting state it would go on saying "refused" long after the
     * turn had carried on without the tool. */
    if (this.state === 'blocked' && this._t - this._blockedAt > BLOCKED_MS / 1000) {
      this.state = this._blockedUnder;
    }

    const look = LOOKS[this.muted ? 'muted' : this.state] ?? LOOKS.idle;
    this._mic = ease(this._mic, Math.min(1, this.micLevel * 4), 14, dt);
    this._out = ease(this._out, Math.min(1, this.outLevel * 4), 18, dt);

    if (!this.reduced) this._spin += dt * (this.state === 'tool.external' ? 1.6 : 1.1);

    this.draw(look, dt);
  }

  draw(look, dt) {
    const ctx = this.ctx;
    const cx = this.w / 2;
    const cy = this.h / 2;
    /* Sized off the shorter edge, so the compact window and the full one draw
     * the same thing at two sizes. */
    const unit = Math.min(this.w, this.h) / 2;
    const base = unit * 0.42;
    const state = this.muted ? 'muted' : this.state;
    const still = this.reduced || state === 'muted';

    ctx.clearRect(0, 0, this.w, this.h);

    let target = look.bright;
    let radius = 1;

    if (!still) {
      switch (state) {
        case 'idle':
          /* ~0.2 Hz. Nothing else moves. */
          target = look.bright * (0.75 + 0.25 * (0.5 + 0.5 * Math.sin(this._t * 2 * Math.PI * 0.2)));
          radius = 1 + 0.03 * Math.sin(this._t * 2 * Math.PI * 0.2);
          break;
        case 'connecting':
          target = look.bright * (0.6 + 0.4 * (0.5 + 0.5 * Math.sin(this._t * 2.2)));
          break;
        case 'listening':
          radius = 1 + this._mic * 0.1;
          break;
        case 'transcribing':
          /* Collapse inward and hold. Usually under a second, so it reads as
           * the pause between hearing and answering. */
          radius = 0.72;
          break;
        case 'thinking':
          /* The shell stays still on purpose: effort, not progress. A moving
           * shell reads as a progress bar, and there is no progress to
           * report -- the model is either done or it is not. */
          break;
        case 'speaking':
          radius = 1 + this._out * 0.12;
          target = look.bright * (0.8 + 0.2 * this._out);
          break;
        case 'error':
          target = look.bright * (0.45 + 0.55 * (0.5 + 0.5 * Math.sin(this._t * 2.6)));
          break;
        case 'blocked': {
          /* Flash and stutter. The stutter is a square wave, not a sine -- a
           * smooth pulse reads as breathing, which is the idle state. */
          const age = this._t - this._blockedAt;
          const on = Math.sin(age * 34) > 0;
          target = look.bright * (on ? 1 : 0.35);
          radius = 1 + (on ? 0.06 : 0);
          break;
        }
      }
    }

    this._bright = ease(this._bright, target, 9, dt);
    this._radius = ease(this._radius, radius, 12, dt);
    const r = base * this._radius;
    const glow = Math.max(0.05, this._bright);

    /* The outer field first, so everything else sits on top of it. */
    const field = ctx.createRadialGradient(cx, cy, r * 0.6, cx, cy, unit);
    field.addColorStop(0, rgba(look.rgb, 0.22 * glow));
    field.addColorStop(1, rgba(look.rgb, 0));
    ctx.fillStyle = field;
    ctx.fillRect(0, 0, this.w, this.h);

    if (!still) {
      if (state === 'speaking') this.drawSpeakingRings(cx, cy, r, look);
      else if (state === 'listening') this.drawListeningRing(cx, cy, r, look);
      else if (state === 'connecting') this.drawConnectingArc(cx, cy, r, look);
      else if (state === 'tool.local') this.drawArcs(cx, cy, r, look, 1);
      else if (state === 'tool.external') this.drawArcs(cx, cy, r, look, 2);
    }

    /* The core. */
    const core = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 0.1, cx, cy, r);
    core.addColorStop(0, rgba(look.rgb, Math.min(1, 0.35 + glow)));
    core.addColorStop(0.65, rgba(look.rgb, 0.55 * glow));
    core.addColorStop(1, rgba(look.rgb, 0.1 * glow));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = rgba(look.rgb, 0.3 + 0.35 * glow);
    ctx.lineWidth = 1.2;
    ctx.stroke();

    if (state === 'thinking' && !still) this.drawParticles(cx, cy, r, look, dt);
    if (this.muted) this.drawSlash(cx, cy, r, look);

    this.drawWakeFlash(cx, cy, base);
  }

  /** Concentric rings, pulsing with what is actually being played. */
  drawSpeakingRings(cx, cy, r, look) {
    const ctx = this.ctx;
    for (let i = 1; i <= 3; i += 1) {
      const phase = (this._t * 0.9 + i / 3) % 1;
      const radius = r * (1.15 + phase * 0.9);
      const alpha = (1 - phase) * (0.16 + this._out * 0.5);
      ctx.strokeStyle = rgba(look.rgb, alpha);
      ctx.lineWidth = 2 - phase;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  /** The ring whose radius is the microphone. Flat means nothing is bound. */
  drawListeningRing(cx, cy, r, look) {
    const ctx = this.ctx;
    const radius = r * 1.45 + this._mic * r * 0.55;
    ctx.strokeStyle = rgba(look.rgb, 0.28 + this._mic * 0.5);
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 5]);
    ctx.lineDashOffset = -this._spin * 12;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  drawConnectingArc(cx, cy, r, look) {
    const ctx = this.ctx;
    ctx.strokeStyle = rgba(look.rgb, 0.45);
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 1.5, this._spin, this._spin + Math.PI * 0.6);
    ctx.stroke();
  }

  /** One arc for a local call; two counter-rotating for an external one.
   *
   *  Two is not decoration. External is the one state a person has to notice
   *  without being told, so it moves differently as well as being teal.
   */
  drawArcs(cx, cy, r, look, count) {
    const ctx = this.ctx;
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    for (let i = 0; i < count; i += 1) {
      const direction = i % 2 === 0 ? 1 : -1;
      const start = this._spin * 2.1 * direction + i * Math.PI;
      ctx.strokeStyle = rgba(look.rgb, 0.75);
      ctx.beginPath();
      ctx.arc(cx, cy, r * 1.5, start, start + Math.PI * 0.5);
      ctx.stroke();
    }
    ctx.lineCap = 'butt';
  }

  drawParticles(cx, cy, r, look, dt) {
    const ctx = this.ctx;
    for (const p of this._particles) {
      p.angle += dt * p.speed * 1.9;
      const radius = r * p.orbit;
      const x = cx + Math.cos(p.angle) * radius;
      const y = cy + Math.sin(p.angle) * radius * 0.82;
      ctx.fillStyle = rgba(look.rgb, 0.85);
      ctx.beginPath();
      ctx.arc(x, y, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /** Muted is not a dimmer setting. The capture stream is stopped, so the orb
   *  should be unmistakably off rather than quietly quiet. */
  drawSlash(cx, cy, r, look) {
    const ctx = this.ctx;
    ctx.strokeStyle = rgba(look.rgb, 0.8);
    ctx.lineWidth = 2.4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.72, cy - r * 0.72);
    ctx.lineTo(cx + r * 0.72, cy + r * 0.72);
    ctx.stroke();
    ctx.lineCap = 'butt';
  }

  drawWakeFlash(cx, cy, base) {
    const age = this._t - this._wakeAt;
    if (age < 0 || age > WAKE_MS / 1000) return;
    const phase = age / (WAKE_MS / 1000);
    const ctx = this.ctx;
    ctx.strokeStyle = rgba(WHITE, (1 - phase) * 0.9);
    ctx.lineWidth = 2.5 * (1 - phase) + 0.5;
    ctx.beginPath();
    ctx.arc(cx, cy, base * (1 + phase * 1.6), 0, Math.PI * 2);
    ctx.stroke();
  }
}
