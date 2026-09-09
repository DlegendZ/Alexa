/* The orb.
 *
 * One glowing core, drawn on a 2D canvas, and it is the whole status display.
 * It answers two questions at a glance: what is it doing, and -- because teal
 * means external and nothing else is ever teal -- is anything leaving this
 * machine right now.
 *
 * Never WebGL. That card is holding the models; a scene graph would compete
 * for the exact memory the agent needs. Layered radial gradients, a small
 * particle array and a few arcs cost nothing next to that.
 *
 * The first version of this looked like a status LED from 1998, and the reason
 * was structural rather than a matter of taste: it had a hard one-pixel stroke
 * around a single gradient, and every animated quantity was one sine wave. A
 * circle with a crisp edge reads as a widget. Something with no edge at all --
 * light falling off into the background over forty pixels -- reads as a light.
 * So there is no stroke anywhere in here now, and nothing moves on one
 * frequency: the breathing is three sines with incommensurate periods, which
 * never quite repeats and is what makes it feel alive rather than clocked.
 */

/* Warmed to match the window, and the meanings are untouched: amber is work
 * on this machine, teal is something leaving it, red is a refusal. */
const AMBER = [217, 119, 87];
const TEAL = [108, 171, 156];
const RED = [204, 123, 114];
const NEUTRAL = [168, 164, 155];
const WHITE = [255, 250, 244];

/** What each state looks like before anything moves.
 *
 *  `bright` is the core's opacity floor; motion is added per state in draw().
 */
const LOOKS = {
  connecting: { rgb: NEUTRAL, bright: 0.18 },
  idle: { rgb: NEUTRAL, bright: 0.24 },
  listening: { rgb: NEUTRAL, bright: 0.85 },
  transcribing: { rgb: NEUTRAL, bright: 0.55 },
  thinking: { rgb: AMBER, bright: 0.78 },
  'tool.local': { rgb: AMBER, bright: 0.84 },
  'tool.external': { rgb: TEAL, bright: 0.92 },
  blocked: { rgb: RED, bright: 0.95 },
  speaking: { rgb: AMBER, bright: 0.88 },
  muted: { rgb: NEUTRAL, bright: 0.2 },
  error: { rgb: RED, bright: 0.7 },
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

/** Three sines whose periods do not divide into each other, so the sum never
 *  repeats within a session. One sine is a metronome; this is breathing. */
const organic = (t, base) =>
  Math.sin(t * base) * 0.6 + Math.sin(t * base * 1.73 + 1.1) * 0.28 + Math.sin(t * base * 2.61 + 2.7) * 0.12;

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
     * is what the speaking ripples move with -- ripples on a timer would look
     * the same and mean nothing. */
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
    /* The colour is eased as well as the brightness, so a state change is a
     * hue drifting rather than a lamp being swapped. Teal arriving is the one
     * transition a person has to notice, and it still does -- it is a large
     * change over 200 ms rather than an instant one. */
    this._rgb = [...NEUTRAL];

    this._particles = Array.from({ length: 18 }, (_, i) => ({
      angle: (i / 18) * Math.PI * 2,
      orbit: 0.3 + ((i * 7) % 11) / 22,
      speed: 0.45 + ((i * 5) % 7) / 9,
      /* A depth, so they pass in front of and behind the core instead of
       * sliding around a flat ring. Size and opacity follow it. */
      tilt: 0.5 + ((i * 3) % 5) / 10,
      phase: (i * 1.7) % (Math.PI * 2),
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
    for (let i = 0; i < 3; i += 1) {
      this._rgb[i] = ease(this._rgb[i], look.rgb[i], 9, dt);
    }

    if (!this.reduced) this._spin += dt * (this.state === 'tool.external' ? 0.9 : 0.55);

    this.draw(look, dt);
  }

  draw(look, dt) {
    const ctx = this.ctx;
    const cx = this.w / 2;
    const cy = this.h / 2;
    /* Sized off the shorter edge, so the compact window and the full one draw
     * the same thing at two sizes. */
    const unit = Math.min(this.w, this.h) / 2;
    const base = unit * 0.38;
    const state = this.muted ? 'muted' : this.state;
    const still = this.reduced || state === 'muted';
    const rgb = this._rgb.map(Math.round);

    ctx.clearRect(0, 0, this.w, this.h);

    let target = look.bright;
    let radius = 1;

    if (!still) {
      switch (state) {
        case 'idle':
          /* ~0.2 Hz, but not on one wave. Nothing else moves. */
          target = look.bright * (0.72 + 0.28 * (0.5 + 0.5 * organic(this._t, 1.25)));
          radius = 1 + 0.045 * organic(this._t, 1.25);
          break;
        case 'connecting':
          target = look.bright * (0.5 + 0.5 * (0.5 + 0.5 * Math.sin(this._t * 2.2)));
          break;
        case 'listening':
          radius = 1 + this._mic * 0.14 + 0.02 * organic(this._t, 2.4);
          break;
        case 'transcribing':
          /* Collapse inward and hold. Usually under a second, so it reads as
           * the pause between hearing and answering. */
          radius = 0.7;
          break;
        case 'thinking':
          /* The shell stays still on purpose: effort, not progress. A moving
           * shell reads as a progress bar, and there is no progress to
           * report -- the model is either done or it is not. */
          radius = 1 + 0.02 * organic(this._t, 3.1);
          break;
        case 'speaking':
          radius = 1 + this._out * 0.14;
          target = look.bright * (0.78 + 0.22 * this._out);
          break;
        case 'error':
          target = look.bright * (0.4 + 0.6 * (0.5 + 0.5 * Math.sin(this._t * 2.4)));
          break;
        case 'blocked': {
          /* Flash and stutter. The stutter is a square wave, not a sine -- a
           * smooth pulse reads as breathing, which is the idle state. */
          const age = this._t - this._blockedAt;
          const on = Math.sin(age * 30) > 0;
          target = look.bright * (on ? 1 : 0.4);
          radius = 1 + (on ? 0.05 : 0);
          break;
        }
      }
    }

    this._bright = ease(this._bright, target, 9, dt);
    this._radius = ease(this._radius, radius, 11, dt);
    const r = base * this._radius;
    const glow = Math.max(0.05, this._bright);

    if (!still) {
      if (state === 'speaking') this.drawRipples(cx, cy, r, rgb);
      else if (state === 'listening') this.drawListeningRing(cx, cy, r, rgb);
      else if (state === 'connecting') this.drawConnectingArc(cx, cy, r, rgb);
      else if (state === 'tool.local') this.drawArcs(cx, cy, r, rgb, 1);
      else if (state === 'tool.external') this.drawArcs(cx, cy, r, rgb, 2);
    }

    /* Three layers of light, and the reason there is no outline anywhere: an
     * edge is what made the first version look like a button. The bloom falls
     * off over most of the canvas, the body carries the colour, and a small
     * offset highlight gives it somewhere for the light to be coming from. */
    const bloom = ctx.createRadialGradient(cx, cy, r * 0.3, cx, cy, unit * 1.05);
    bloom.addColorStop(0, rgba(rgb, 0.3 * glow));
    bloom.addColorStop(0.45, rgba(rgb, 0.09 * glow));
    bloom.addColorStop(1, rgba(rgb, 0));
    ctx.fillStyle = bloom;
    ctx.fillRect(0, 0, this.w, this.h);

    if (state === 'thinking' && !still) this.drawParticles(cx, cy, r, rgb, dt, true);

    const body = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 1.15);
    body.addColorStop(0, rgba(rgb, Math.min(1, 0.55 + glow * 0.45)));
    body.addColorStop(0.55, rgba(rgb, 0.55 * glow + 0.12));
    body.addColorStop(0.82, rgba(rgb, 0.22 * glow));
    body.addColorStop(1, rgba(rgb, 0));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 1.15, 0, Math.PI * 2);
    ctx.fill();

    const hx = cx - r * 0.32;
    const hy = cy - r * 0.36;
    const shine = ctx.createRadialGradient(hx, hy, 0, hx, hy, r * 0.95);
    shine.addColorStop(0, rgba(WHITE, 0.28 * glow));
    shine.addColorStop(1, rgba(WHITE, 0));
    ctx.fillStyle = shine;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 1.1, 0, Math.PI * 2);
    ctx.fill();

    if (state === 'thinking' && !still) this.drawParticles(cx, cy, r, rgb, dt, false);
    if (this.muted) this.drawSlash(cx, cy, r, rgb);

    this.drawWakeFlash(cx, cy, base);
  }

  /** Ripples leaving the core, moving with what is actually being played. */
  drawRipples(cx, cy, r, rgb) {
    const ctx = this.ctx;
    for (let i = 0; i < 3; i += 1) {
      const phase = (this._t * 0.55 + i / 3) % 1;
      const radius = r * (1 + phase * 1.5);
      const alpha = (1 - phase) * (1 - phase) * (0.1 + this._out * 0.45);
      const ring = ctx.createRadialGradient(cx, cy, radius * 0.86, cx, cy, radius);
      ring.addColorStop(0, rgba(rgb, 0));
      ring.addColorStop(0.6, rgba(rgb, alpha));
      ring.addColorStop(1, rgba(rgb, 0));
      ctx.fillStyle = ring;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /** The halo whose radius is the microphone. Flat means nothing is bound. */
  drawListeningRing(cx, cy, r, rgb) {
    const ctx = this.ctx;
    const radius = r * (1.5 + this._mic * 0.55);
    const ring = ctx.createRadialGradient(cx, cy, radius * 0.78, cx, cy, radius * 1.08);
    ring.addColorStop(0, rgba(rgb, 0));
    ring.addColorStop(0.55, rgba(rgb, 0.16 + this._mic * 0.4));
    ring.addColorStop(1, rgba(rgb, 0));
    ctx.fillStyle = ring;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.08, 0, Math.PI * 2);
    ctx.fill();
  }

  drawConnectingArc(cx, cy, r, rgb) {
    this.softArc(cx, cy, r * 1.55, this._spin, Math.PI * 0.55, rgb, 0.4, 2);
  }

  /** One arc for a local call; two counter-rotating for an external one.
   *
   *  Two is not decoration. External is the one state a person has to notice
   *  without being told, so it moves differently as well as being teal.
   */
  drawArcs(cx, cy, r, rgb, count) {
    for (let i = 0; i < count; i += 1) {
      const direction = i % 2 === 0 ? 1 : -1;
      const start = this._spin * 2.2 * direction + i * Math.PI;
      this.softArc(cx, cy, r * 1.5, start, Math.PI * 0.5, rgb, 0.8, 3.5);
    }
  }

  /** An arc that fades out at both ends, so it reads as a sweep of light
   *  rather than a drawn stroke with two cut ends. */
  softArc(cx, cy, radius, start, span, rgb, alpha, width) {
    const ctx = this.ctx;
    const steps = 18;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    for (let i = 0; i < steps; i += 1) {
      const a0 = start + (span * i) / steps;
      const a1 = start + (span * (i + 1)) / steps;
      /* Brightest in the middle of the sweep, gone at both ends. */
      const along = (i + 0.5) / steps;
      const fade = Math.sin(along * Math.PI);
      ctx.strokeStyle = rgba(rgb, alpha * fade);
      ctx.beginPath();
      ctx.arc(cx, cy, radius, a0, a1 + 0.01);
      ctx.stroke();
    }
    ctx.lineCap = 'butt';
  }

  /** Motes orbiting inside the core. Drawn in two passes -- the ones behind
   *  the body before it, the ones in front after -- so they have depth
   *  instead of sliding around a flat ring. */
  drawParticles(cx, cy, r, rgb, dt, behind) {
    const ctx = this.ctx;
    for (const p of this._particles) {
      if (behind) p.angle += dt * p.speed * 1.5;
      const depth = Math.sin(p.angle + p.phase);
      if (behind !== depth < 0) continue;
      const radius = r * p.orbit * 1.5;
      const x = cx + Math.cos(p.angle) * radius;
      const y = cy + Math.sin(p.angle) * radius * p.tilt;
      const near = (depth + 1) / 2;
      const size = 0.9 + near * 1.9;
      ctx.fillStyle = rgba(rgb, 0.25 + near * 0.55);
      ctx.beginPath();
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /** Muted is not a dimmer setting. The capture stream is stopped, so the orb
   *  should be unmistakably off rather than quietly quiet. */
  drawSlash(cx, cy, r, rgb) {
    const ctx = this.ctx;
    ctx.strokeStyle = rgba(rgb, 0.75);
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.66, cy - r * 0.66);
    ctx.lineTo(cx + r * 0.66, cy + r * 0.66);
    ctx.stroke();
    ctx.lineCap = 'butt';
  }

  drawWakeFlash(cx, cy, base) {
    const age = this._t - this._wakeAt;
    if (age < 0 || age > WAKE_MS / 1000) return;
    const phase = age / (WAKE_MS / 1000);
    const ctx = this.ctx;
    const radius = base * (1 + phase * 1.9);
    const ring = ctx.createRadialGradient(cx, cy, radius * 0.8, cx, cy, radius);
    ring.addColorStop(0, rgba(WHITE, 0));
    ring.addColorStop(0.7, rgba(WHITE, (1 - phase) * 0.55));
    ring.addColorStop(1, rgba(WHITE, 0));
    ctx.fillStyle = ring;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}
