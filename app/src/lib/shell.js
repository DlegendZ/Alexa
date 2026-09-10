/* The few things only the shell can do.
 *
 * Every one of these degrades to nothing in a browser, because the window is
 * developed in a browser -- a reload is a tenth of a second there and a Rust
 * build is not -- and a missing title bar should not take the transcript with
 * it.
 */

const tauri = () => globalThis.__TAURI__;

/** Whether there is a shell at all.
 *
 *  The window is developed in a browser, where there is no Rust half to ask
 *  for anything -- so the few controls that only the shell can honour have to
 *  know not to offer themselves. A "Restart now" button that silently does
 *  nothing is the failure this whole file's degrade-to-nothing rule exists to
 *  avoid, and it is the one place where degrading to nothing is not enough:
 *  the user has just been told a setting needs a restart. */
export const inShell = () => Boolean(tauri()?.core?.invoke);

const invoke = async (command, args) => {
  const api = tauri();
  if (!api?.core?.invoke) return null;
  try {
    return await api.core.invoke(command, args);
  } catch (error) {
    console.warn(`${command} failed`, error);
    return null;
  }
};

/** Config the shell already read, so the window does not parse TOML. */
export const settings = () => invoke('settings');

/** Compact mode: a small frameless always-on-top window showing just the orb,
 *  for when it is listening in the background. It is one window resized rather
 *  than a second one, so there is nothing to keep in step. */
export const setCompact = (compact) => invoke('set_compact', { compact });

/** Tell the shell what the orb is showing, so the tray icon can be tinted
 *  with it. That is what keeps the privacy affordance alive while the window
 *  is hidden: teal in the corner means something is leaving, and the tray is
 *  the only part of the app you can see from another program. */
export const setState = (state) => invoke('set_state', { state });

/** Quit properly: the sidecar is told to flush the session summary to Chroma
 *  before anything is killed. */
export const quit = () => invoke('quit');

export const minimise = () => invoke('minimise');

/** The other two window controls. They exist here because the title bar is
 *  drawn by the page now: Windows will not let you keep its caption buttons
 *  and drop the icon and the title beside them. */
export const toggleMaximise = () => invoke('toggle_maximise');

/** Pick the window up and move it, from wherever the page says is a handle.
 *
 *  Compact mode used to do this with `-webkit-app-region: drag`, and note 114
 *  is what that cost: a drag region swallows mouse events before the page sees
 *  them, so the click that left compact could never fire. Asking on
 *  `mousedown` leaves every event where the page can still read it, which is
 *  what lets a double click mean something. */
export const startDragging = () => invoke('drag_window');

/** Stop the sidecar and start it again, for the handful of settings the
 *  running process cannot pick up -- the model and the window it is given.
 *  Only the sidecar: a setting the Rust half reads is not on that list,
 *  because this would not apply it.
 *
 *  Deliberately not the crash-restart path. That one counts restarts and gives
 *  up after three in a minute, which is right for a process that keeps dying
 *  and wrong for a button somebody pressed on purpose. */
export const restartSidecar = () => invoke('restart_sidecar');

/** Re-read `[ui]` after a save and apply what the shell owns -- the Run key
 *  now, and the frame rates handed back. Null in a browser, where there is no
 *  shell and nothing of this to apply. */
export const applyLaunchSettings = () => invoke('apply_launch_settings');

/** Events the shell pushes at the window -- the tray menu arrives this way,
 *  because it happens where there is no DOM. */
export async function onShellEvent(name, handler) {
  const api = tauri();
  if (!api?.event?.listen) return () => {};
  return api.event.listen(name, (event) => handler(event.payload));
}
