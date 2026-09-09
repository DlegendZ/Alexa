/* The few things only the shell can do.
 *
 * Every one of these degrades to nothing in a browser, because the window is
 * developed in a browser -- a reload is a tenth of a second there and a Rust
 * build is not -- and a missing title bar should not take the transcript with
 * it.
 */

const tauri = () => globalThis.__TAURI__;

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

export const setAutostart = (on) => invoke('set_autostart', { on });
export const autostartEnabled = () => invoke('autostart_enabled');

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

/** Close means hide to the tray, which is what the native close did -- the
 *  thing is meant to keep listening. Quitting is `exit` in the box or the
 *  tray menu, both of which flush the session summary first. */
export const close = () => invoke('hide_window');

/** Events the shell pushes at the window -- the tray menu arrives this way,
 *  because it happens where there is no DOM. */
export async function onShellEvent(name, handler) {
  const api = tauri();
  if (!api?.event?.listen) return () => {};
  return api.event.listen(name, (event) => handler(event.payload));
}
