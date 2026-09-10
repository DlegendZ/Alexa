/* Every transition in the window takes its duration from here, so reduced
 * motion is one rule rather than a check at each call site. The stylesheet
 * already cuts CSS transitions for it; Svelte's transitions are JavaScript and
 * never see that rule, so they need their own. */

const still =
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** A duration in milliseconds, or none at all when motion is off. */
export const ms = (n) => (still ? 0 : n);
