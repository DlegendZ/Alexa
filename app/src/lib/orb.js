/* The orb.
 *
 * A web of particles, drawn on a 2D canvas, and it is the whole status
 * display. It answers two questions at a glance: what is it doing, and --
 * because teal means external and nothing else is ever teal -- is anything
 * leaving this machine right now.
 *
 * Never WebGL. That card is holding the models; a scene graph would compete
 * for the exact memory the agent needs. Thirty-four points on a sphere, the
 * lines between the near ones, and a bloom behind the lot cost nothing next
 * to that. The pair loop is 561 distance checks a frame, which is less work
 * than one of the radial gradients the previous version drew three of.
 *
 * It used to be a single glowing sphere. The web says two things the sphere
 * could not. A mesh has *structure*, so a state can change how connected it
 * is rather than only how bright it is -- thinking is a dense web churning,
 * transcribing is the same web pulled in tight, and a closed microphone is
 * a web with most of its lines gone. And a mesh has direction, so speaking
 * can be a wave that travels through it rather than a ring that leaves it.
 *
 * White, except for the two facts worth interrupting somebody for: teal while
 * something is reaching off this machine, red for a refusal. Everything else
 * the assistant does is white, which is what makes those two mean something.
 */

/* Teal is something leaving this machine and red is a refusal. Those two are
 * the whole palette now: everything the assistant does *on* this machine --
 * thinking, listening, speaking, running a local tool -- is white.
 *
 * Amber used to mean "working on your machine" and it is gone. Two colours
 * carry more than three did: the question the orb has to answer without being
 * asked is "is anything leaving?", and a white orb that turns teal answers it
 * from across a room in a way an amber one turning teal never did. Local work
 * is told apart from thinking by density and speed instead, which are two
 * channels the web has and a sphere did not. */
const TEAL = [108, 171, 156];
const RED = [204, 123, 114];
const WHITE = [255, 252, 247];
const DIM = [198, 194, 186];

/** What each state looks like before anything moves.
 *
 *  `bright` is the web's opacity floor; `link` is how far apart two nodes can
 *  be and still be joined, which is the quantity a sphere did not have; motion
 *  is added per state in draw().
 *
 *  The `link` numbers are calibrated, not chosen. Thirty-four points spread
 *  over a unit sphere sit about 0.68 apart, so anything under that draws no
 *  lines at all -- the first set of these ran from 0.4 to 0.9 and produced a
 *  cloud of dots with one link in it, which is not a web and did not look
 *  like one. Read them against 0.68: 0.7 is a handful, 0.9 is a mesh, 1.05
 *  knits the thing shut.
 */
const LOOKS = {
  connecting: { rgb: DIM, bright: 0.3, link: 0.66 },
  /* Voice off, and the only state in which it is: with the ear open the
     resting state is `listening`, so an idle web is a closed microphone. It
     is drawn asleep -- dim, drifting, and with most of the links gone. */
  idle: { rgb: DIM, bright: 0.34, link: 0.7 },
  listening: { rgb: WHITE, bright: 0.95, link: 0.92 },
  transcribing: { rgb: WHITE, bright: 0.7, link: 1.05 },
  thinking: { rgb: WHITE, bright: 0.85, link: 0.86 },
  'tool.local': { rgb: WHITE, bright: 1, link: 0.98 },
  'tool.external': { rgb: TEAL, bright: 1, link: 0.82 },
  blocked: { rgb: RED, bright: 1, link: 0.74 },
  speaking: { rgb: WHITE, bright: 0.95, link: 0.9 },
  error: { rgb: RED, bright: 0.75, link: 0.62 },
};

/** States that are an event rather than a place. They pulse over whatever is
 *  happening and the orb stays where it was afterwards -- `wake` arrives while
 *  the models are still loading, so settling the orb on it would leave the
 *  window holding an acknowledgement for as long as that takes. */
const FLASHES = {
  wake: WHITE,
};

/** Every state this orb knows what to do with. Anything else is ignored rather
 *  than guessed at -- a state it has never heard of would otherwise land as
 *  whatever the last one was, which is worse than not moving. */
export const STATES = [...Object.keys(LOOKS), ...Object.keys(FLASHES)];

/** The wake pulse.
 *
 *  It used to be a ring expanding out of the orb, which is the one shape this
 *  drawing had already decided against everywhere else -- a splash reads as a
 *  notification badge, and it was the only thing in here that left the body of
 *  the orb. The web does it from the inside now: every link that could exist
 *  snaps in, the whole thing flares, and it settles back. Same 'I heard you',
 *  no splash, and it uses the channel the mesh has rather than borrowing one.
 */
const WAKE_MS = 320;
/** How long `blocked` stutters before the state underneath shows again. */
const BLOCKED_MS = 900;

/** How many points are in the web.
 *
 *  Every pair is measured every frame, so this is quadratic: 34 is 561 pairs,
 *  48 would be 1128. Thirty-four is the number at which the mesh still reads
 *  as a mesh at 44 pixels -- the compact window and the title bar draw the
 *  same object at a fifth of the size -- and does not turn into a solid disc
 *  at 150.
 */
const NODES = 34;

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

/** Points spread evenly over a sphere, by the golden angle.
 *
 *  Evenly is the whole requirement: random points clump, and a clump in a
 *  particle web is a bright blob that reads as a fault in the drawing rather
 *  than as noise.
 */
function lattice(count) {
  const golden = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: count }, (_, i) => {
    const y = 1 - (i / (count - 1)) * 2;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    return {
      /* Where it sits when nothing is happening. */
      base: [Math.cos(theta) * ring, y, Math.sin(theta) * ring],
      /* Its own drift, so the web breathes unevenly. Derived from the index
       * rather than random, so two runs of the app look the same. */
      phase: (i * 1.7) % (Math.PI * 2),
      rate: 0.6 + ((i * 5) % 7) / 8,
      /* Filled in every frame; kept on the object so the frame allocates
       * nothing. Sixty frames a second of 34 fresh arrays is garbage the
       * collector has to chase while a model is generating. */
      x: 0,
      y: 0,
      z: 0,
      sx: 0,
      sy: 0,
      alpha: 0,
      size: 0,
    };
  });
}

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

    /* Two amplitudes, both measured rather than invented: the microphone's,
     * and the level of what is actually coming out of the speaker. The second
     * is what the speaking wave moves with -- a wave on a timer would look
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
    this._link = 0.6; // how connected the web is, eased like everything else
    this._spin = 0; // yaw, accumulating
    this._roll = 0; // a second axis, so it is a sphere and not a carousel
    /* The colour is eased as well as the brightness, so a state change is a
     * hue drifting rather than a lamp being swapped. Teal arriving is the one
     * transition a person has to notice, and it still does -- it is a large
     * change over 200 ms rather than an instant one. */
    this._rgb = [...DIM];

    this._nodes = lattice(NODES);

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

  /** The wake pulse. Deliberately not a state: it overlays whatever follows. */
  flashWake() {
    this._wakeAt = this._t;
  }

  /** 1 the instant the phrase lands, 0 once the pulse is over.
   *
   *  Squared rather than linear so it drops away fast: the point of the pulse
   *  is the leading edge, and a slow tail turns an acknowledgement into a
   *  second animation competing with whatever state arrives next.
   */
  wakePulse() {
    const age = this._t - this._wakeAt;
    if (age < 0 || age > WAKE_MS / 1000) return 0;
    const left = 1 - age / (WAKE_MS / 1000);
    return left * left;
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

    const look = LOOKS[this.state] ?? LOOKS.idle;
    this._mic = ease(this._mic, Math.min(1, this.micLevel * 4), 14, dt);
    this._out = ease(this._out, Math.min(1, this.outLevel * 4), 18, dt);
    for (let i = 0; i < 3; i += 1) {
      this._rgb[i] = ease(this._rgb[i], look.rgb[i], 9, dt);
    }
    this._link = ease(this._link, look.link, 7, dt);

    if (!this.reduced) {
      this._spin += dt * this.spinRate();
      this._roll += dt * this.spinRate() * 0.37;
    }

    this.draw(look, dt);
  }

  /** How fast the web turns, per state.
   *
   *  Speed is a channel of its own, and it is the one that reads at a glance
   *  from across a room: external is faster than local, and thinking is
   *  faster than either, because thinking is the state you are waiting out.
   */
  spinRate() {
    switch (this.state) {
      case 'thinking':
        return 1.15;
      case 'tool.external':
        return 0.95;
      case 'tool.local':
        return 0.7;
      case 'transcribing':
        return 0.15;
      case 'idle':
        return 0.08;
      case 'listening':
        return 0.3;
      default:
        return 0.22;
    }
  }

  draw(look, dt) {
    const ctx = this.ctx;
    const cx = this.w / 2;
    const cy = this.h / 2;
    /* Sized off the shorter edge, so the compact window and the full one draw
     * the same thing at two sizes. */
    const unit = Math.min(this.w, this.h) / 2;
    const base = unit * 0.62;
    const state = this.state;
    const still = this.reduced;
    const rgb = this._rgb.map(Math.round);

    ctx.clearRect(0, 0, this.w, this.h);

    let target = look.bright;
    let radius = 1;
    /* How far a node wanders off its lattice point. The web is a solid on
     * every state except the two that are meant to look unsettled. */
    let jitter = 0.02;

    if (!still) {
      switch (state) {
        case 'idle':
          /* ~0.2 Hz, but not on one wave. Nothing else moves. */
          target = look.bright * (0.74 + 0.26 * (0.5 + 0.5 * organic(this._t, 1.25)));
          radius = 1 + 0.05 * organic(this._t, 1.25);
          break;
        case 'connecting':
          target = look.bright * (0.45 + 0.55 * (0.5 + 0.5 * Math.sin(this._t * 2.2)));
          radius = 0.88 + 0.06 * Math.sin(this._t * 2.2);
          jitter = 0.09;
          break;
        case 'listening':
          /* The web opens with your voice. Flat means nothing is bound. */
          radius = 1 + this._mic * 0.22 + 0.02 * organic(this._t, 2.4);
          break;
        case 'transcribing':
          /* Collapse inward and hold, with the links pulled tight. Usually
           * under a second, so it reads as the pause between hearing and
           * answering. */
          radius = 0.66;
          break;
        case 'thinking':
          /* The one state where the nodes leave their lattice points. Effort,
           * not progress: a web churning has no direction to read as a
           * progress bar, because the model is either done or it is not. */
          radius = 1 + 0.03 * organic(this._t, 3.1);
          jitter = 0.16;
          break;
        case 'tool.local':
        case 'tool.external':
          radius = 1 + 0.04 * Math.sin(this._t * 3.4);
          jitter = 0.06;
          break;
        case 'speaking':
          /* The wave itself is in nodeAlpha(); this is the swell under it. */
          radius = 1 + this._out * 0.2;
          target = look.bright * (0.72 + 0.28 * this._out);
          break;
        case 'error':
          target = look.bright * (0.4 + 0.6 * (0.5 + 0.5 * Math.sin(this._t * 2.4)));
          jitter = 0.12;
          break;
        case 'blocked': {
          /* Flash and stutter. The stutter is a square wave, not a sine -- a
           * smooth pulse reads as breathing, which is the idle state. */
          const age = this._t - this._blockedAt;
          const on = Math.sin(age * 30) > 0;
          target = look.bright * (on ? 1 : 0.35);
          radius = 1 + (on ? 0.06 : 0);
          break;
        }
      }
    }

    this._bright = ease(this._bright, target, 9, dt);
    this._radius = ease(this._radius, radius, 11, dt);
    const r = base * this._radius;
    /* The pulse rides on top of whatever the state is doing rather than
     * replacing it, which is the whole reason it is not a state. */
    const wake = still ? 0 : this.wakePulse();
    const glow = Math.min(1, Math.max(0.05, this._bright) + wake * 0.45);

    /* The bloom. There is no outline anywhere in here for the same reason
     * there never was: an edge is what makes a drawing read as a widget. The
     * light falls off over most of the canvas, and the web sits inside it. */
    const bloom = ctx.createRadialGradient(cx, cy, r * 0.1, cx, cy, unit * 1.05);
    bloom.addColorStop(0, rgba(rgb, 0.2 * glow));
    bloom.addColorStop(0.5, rgba(rgb, 0.07 * glow));
    bloom.addColorStop(1, rgba(rgb, 0));
    ctx.fillStyle = bloom;
    ctx.fillRect(0, 0, this.w, this.h);

    this.project(cx, cy, r, still ? 0 : jitter, glow, state, wake);
    this.drawLinks(rgb, glow, wake);
    this.drawNodes(rgb, glow);
    this.drawCore(cx, cy, r, rgb, glow);
  }

  /** Rotate the lattice, jitter it, and put every node on the screen.
   *
   *  Two axes rather than one. A single yaw makes the points travel in
   *  horizontal bands, which reads as a carousel; a second, slower roll is
   *  what makes it read as a sphere being turned over.
   */
  project(cx, cy, r, jitter, glow, state, wake) {
    const yaw = this._spin;
    const roll = this._roll;
    const cosY = Math.cos(yaw);
    const sinY = Math.sin(yaw);
    const cosR = Math.cos(roll);
    const sinR = Math.sin(roll);

    for (const p of this._nodes) {
      const wobble = jitter ? Math.sin(this._t * p.rate * 2.1 + p.phase) * jitter : 0;
      const scale = 1 + wobble;
      const bx = p.base[0] * scale;
      const by = p.base[1] * scale;
      const bz = p.base[2] * scale;

      // yaw about Y
      const x1 = bx * cosY + bz * sinY;
      const z1 = bz * cosY - bx * sinY;
      // roll about X
      const y2 = by * cosR - z1 * sinR;
      const z2 = z1 * cosR + by * sinR;

      p.x = x1;
      p.y = y2;
      p.z = z2;
      p.sx = cx + x1 * r;
      p.sy = cy + y2 * r;
      /* Depth, cheaply: nearer is bigger and brighter. Orthographic, because
       * a perspective divide on a sphere this small buys nothing you can
       * see and costs a branch per node for the points behind the camera. */
      const near = (z2 + 1) / 2;
      p.size = (0.9 + near * 2.1) * (1 + wake * 0.7);
      p.alpha = (0.18 + near * 0.62) * glow * this.nodeWave(p, state);
    }
  }

  /** The per-node modulation that is not depth.
   *
   *  Only speaking uses it, and it is the reason the web is a web: a wave
   *  travelling front-to-back through the mesh, moving with what is actually
   *  coming out of the speaker. A ring leaving a sphere says "something
   *  happened"; a wave through a mesh says "it is still talking".
   */
  nodeWave(p, state) {
    if (state !== 'speaking') return 1;
    const wave = Math.sin(this._t * 6.5 - p.z * 3.2);
    return 0.55 + 0.45 * (0.5 + 0.5 * wave) * (0.4 + this._out * 0.6) * 2;
  }

  /** Every pair close enough to be joined, faded by how close.
   *
   *  This is the quadratic half and the whole character of the thing. The
   *  threshold is a state's own quantity: `transcribing` pulls it up so the
   *  web knits itself tight, `idle` -- which now means the ear is shut --
   *  drops it far enough that most of the lines go and what is left drifts.
   */
  drawLinks(rgb, glow, wake = 0) {
    const ctx = this.ctx;
    /* The wake pulse widens the threshold rather than drawing anything new:
     * for a third of a second every link the lattice could have exists. */
    const limit = this._link + wake * 0.55;
    if (limit <= 0.01) return;
    const nodes = this._nodes;
    ctx.lineWidth = 1;
    for (let i = 0; i < nodes.length; i += 1) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dz = a.z - b.z;
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (d > limit) continue;
        /* Brightest when the two are nearly touching, gone at the threshold,
         * so links appear and disappear by fading rather than by popping. */
        const close = 1 - d / limit;
        const depth = (a.z + b.z + 2) / 4;
        const alpha = close * (0.18 + depth * 0.45) * glow;
        if (alpha < 0.006) continue;
        ctx.strokeStyle = rgba(rgb, alpha);
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }
    }
  }

  drawNodes(rgb, glow) {
    const ctx = this.ctx;
    for (const p of this._nodes) {
      const alpha = Math.min(1, p.alpha);
      if (alpha < 0.01) continue;
      ctx.fillStyle = rgba(rgb, alpha);
      ctx.beginPath();
      ctx.arc(p.sx, p.sy, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /** A small light at the middle of the web.
   *
   *  Without it the thing is a hollow shell and the eye has nowhere to rest;
   *  with it, the mesh reads as something *around* a light rather than as a
   *  diagram of a molecule.
   */
  drawCore(cx, cy, r, rgb, glow) {
    const ctx = this.ctx;
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 0.5);
    core.addColorStop(0, rgba(rgb, Math.min(1, 0.35 + glow * 0.5)));
    core.addColorStop(0.5, rgba(rgb, 0.16 * glow));
    core.addColorStop(1, rgba(rgb, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.5, 0, Math.PI * 2);
    ctx.fill();
  }
}
