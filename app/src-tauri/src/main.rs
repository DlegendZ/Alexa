// The window is the product; this file is the part of it Windows can see.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;
mod titlebar;
mod tray;

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, LogicalSize, Manager, State, WindowEvent};
use tauri_plugin_autostart::ManagerExt;

use sidecar::{Handshake, Sidecar};

/// Compact mode: a small always-on-top window with nothing in it but the orb.
/// One window resized rather than a second one, so there is nothing to keep in
/// step. The frame is off in both sizes now -- see `titlebar`.
const COMPACT: (f64, f64) = (200.0, 240.0);
const FULL: (f64, f64) = (960.0, 700.0);

struct Shell {
    sidecar: Mutex<Sidecar>,
    /// What the orb is showing. The window is the only thing that knows, and
    /// the tray is the only thing that needs telling.
    state: Mutex<String>,
}

/// The `[ui]` block, read once and handed to the window.
///
/// The window does not parse TOML. There is one config file and one process
/// that already knows how to find it -- but the window is loaded before the
/// socket is, so these four come the short way.
#[derive(Debug, Serialize, Clone)]
struct Settings {
    fps_focused: u32,
    fps_blurred: u32,
    start_minimised: bool,
    autostart: bool,
    trace: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            fps_focused: 60,
            fps_blurred: 10,
            start_minimised: false,
            autostart: false,
            trace: true,
        }
    }
}

fn read_settings() -> Settings {
    let mut settings = Settings::default();
    // Same precedence as the Python side: the repo-root copy wins over the
    // installed one. Two files disagreeing is confusing enough without the
    // two processes disagreeing about which of them counts.
    let candidates = [
        std::env::current_dir().ok().map(|d| d.join("config.toml")),
        std::env::current_dir()
            .ok()
            .and_then(|d| d.parent().map(|p| p.join("config.toml"))),
        Some(sidecar::home().join("config.toml")),
    ];
    for path in candidates.into_iter().flatten() {
        let Ok(raw) = std::fs::read_to_string(&path) else { continue };
        let Ok(parsed) = raw.parse::<toml::Table>() else { continue };
        let Some(ui) = parsed.get("ui").and_then(|v| v.as_table()) else { continue };
        let number = |key: &str, fallback: u32| {
            ui.get(key).and_then(|v| v.as_integer()).unwrap_or(fallback as i64) as u32
        };
        let flag = |key: &str, fallback: bool| {
            ui.get(key).and_then(|v| v.as_bool()).unwrap_or(fallback)
        };
        settings = Settings {
            fps_focused: number("fps_focused", 60),
            fps_blurred: number("fps_blurred", 10),
            start_minimised: flag("start_minimised", false),
            autostart: flag("autostart", false),
            trace: flag("trace", true),
        };
        break;
    }
    // SUNDAY_TRACE=0 turns the backstage channel off for one run without
    // editing a file, and the window has to agree with the sidecar about it.
    if std::env::var("SUNDAY_TRACE").is_ok_and(|v| v == "0") {
        settings.trace = false;
    }
    settings
}

// -- commands ------------------------------------------------------------

/// Port and token. The WebView cannot read a file, and should not learn how:
/// a window that can read `handshake.json` can read everything next to it.
#[tauri::command]
fn connection(shell: State<'_, Shell>) -> Result<Handshake, String> {
    let mut guard = shell.sidecar.lock().map_err(|e| e.to_string())?;
    if let Some(found) = guard.handshake.clone() {
        return Ok(found);
    }
    if let Some(fault) = guard.fault.clone() {
        return Err(fault);
    }
    guard.start()
}

#[tauri::command]
fn settings() -> Settings {
    read_settings()
}

/// The window telling the shell what the orb is showing, so the tray can be
/// tinted with it. External activity is visible in the corner even when the
/// window is hidden, which is the whole reason the tray is tinted at all.
#[tauri::command]
fn set_state(app: AppHandle, shell: State<'_, Shell>, state: String) {
    if let Ok(mut held) = shell.state.lock() {
        if *held == state {
            return;
        }
        *held = state.clone();
    }
    if let Some(icon) = app.tray_by_id("sunday") {
        let _ = icon.set_icon(Some(tray::icon(&state)));
    }
}

#[tauri::command]
fn set_compact(app: AppHandle, compact: bool) {
    let Some(window) = app.get_webview_window("main") else { return };
    let (w, h) = if compact { COMPACT } else { FULL };
    // Both sizes are frameless now, so there are no decorations to toggle and
    // no new frame to repaint on the way back. What compact still changes is
    // the size and whether it floats over everything else.
    let _ = window.set_always_on_top(compact);
    if !compact {
        let _ = window.unmaximize();
    }
    let _ = window.set_size(LogicalSize::new(w, h));
}

/// Maximise, or put it back. The title bar belongs to the page now, so the
/// two halves of the native button are one command with the state in it.
#[tauri::command]
fn toggle_maximise(app: AppHandle) {
    let Some(window) = app.get_webview_window("main") else { return };
    if window.is_maximized().unwrap_or(false) {
        let _ = window.unmaximize();
    } else {
        let _ = window.maximize();
    }
}

/// Pick the window up, from wherever the page decided is a handle.
///
/// The full window's title bar is a CSS drag region and can stay one: it holds
/// nothing you click except buttons, which are marked `no-drag`. Compact mode
/// cannot, because there the handle and the thing you click are the same
/// pixels -- and a drag region eats mouse events before the page sees them,
/// which is the trap note 114 was written from. This is the other way round:
/// the page reads the event and asks for the drag, so it still knows a double
/// click when it sees one.
#[tauri::command]
fn drag_window(app: AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.start_dragging();
    }
}

#[tauri::command]
fn minimise(app: AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.minimize();
    }
}

/// Opt-in, via the `Run` registry key. Off by default: something that holds
/// several gigabytes of a graphics card should not start itself unasked.
#[tauri::command]
fn set_autostart(app: AppHandle, on: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    if on { manager.enable() } else { manager.disable() }.map_err(|e| e.to_string())
}

#[tauri::command]
fn autostart_enabled(app: AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

/// The window has already sent `shutdown` over the socket, which is what
/// flushes the session summary into Chroma. This waits for that to land.
#[tauri::command]
fn quit(app: AppHandle, shell: State<'_, Shell>) {
    if let Ok(mut guard) = shell.sidecar.lock() {
        guard.stop();
    }
    app.exit(0);
}

/// Leave compact mode from the shell side.
///
/// The window can ask for this itself, but compact is exactly the state in
/// which the window may be unclickable -- so the tray needs its own way out,
/// or a mis-click strands the app as a small circle with no menu.
#[tauri::command]
fn expand(app: AppHandle) {
    set_compact(app.clone(), false);
    let _ = app.emit("expanded", ());
    show(&app);
}

// -- the window ----------------------------------------------------------

fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        if let Ok(handle) = window.hwnd() {
            titlebar::match_the_app(handle.0 as isize);
        }
    }
}

/// The tray's quit, which has to do what the window's quit does: tell the
/// sidecar first, then stop waiting. Killing it here would lose the session
/// summary every time somebody quit from the corner instead of the window.
fn quit_politely(app: &AppHandle) {
    let _ = app.emit("shutdown", ());
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(400));
        if let Some(shell) = handle.try_state::<Shell>() {
            if let Ok(mut guard) = shell.sidecar.lock() {
                guard.stop();
            }
        }
        handle.exit(0);
    });
}

/// One second at a time, is it still alive?
///
/// Restarting it is the shell's job because the shell is what spawned it; the
/// window only ever learns that the connection went away. Three crashes in a
/// minute stops the restarting, because a restart loop hides the reason.
fn watch(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(1));
        let Some(shell) = app.try_state::<Shell>() else { return };
        let Ok(mut guard) = shell.sidecar.lock() else { return };
        if !guard.exited() {
            continue;
        }
        match guard.restart() {
            Ok(handshake) => {
                drop(guard);
                let _ = app.emit("sidecar-restarted", handshake);
            }
            Err(why) => {
                drop(guard);
                let _ = app.emit("sidecar-lost", why);
                return;
            }
        }
    });
}

fn main() {
    tauri::Builder::default()
        // First, and it has to be: everything below it assumes this is the
        // only instance. A second launch focusing the first window is the
        // whole point -- two sidecars would fight over one card.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show(app);
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(Shell {
            sidecar: Mutex::new(Sidecar::default()),
            state: Mutex::new("connecting".to_string()),
        })
        .invoke_handler(tauri::generate_handler![
            connection,
            expand,
            settings,
            set_state,
            set_compact,
            minimise,
            toggle_maximise,
            drag_window,
            set_autostart,
            autostart_enabled,
            quit,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // The frame is off in `tauri.conf.json`, and said again here.
            // Not belt and braces for its own sake: the config is baked into
            // the binary at build time, so an exe built before the title bar
            // moved into the page comes up with both -- the system's caption
            // strip above the one the page draws. Asking at runtime costs
            // nothing and cannot be stale.
            //
            // The border is the one piece of frame left, and Windows still
            // draws it; it is painted to match, or the window has a hairline
            // around it that reads from inside as an edge beside the orb.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_decorations(false);
                if let Ok(handle) = window.hwnd() {
                    titlebar::match_the_app(handle.0 as isize);
                }
            }

            let show_item = MenuItemBuilder::with_id("show", "Show Alexa").build(app)?;
            let expand_item =
                MenuItemBuilder::with_id("expand", "Leave compact mode").build(app)?;
            // One voice item rather than a mute and a mode. They were two
            // switches for one fact, and no combination of them was useful.
            let voice_item = MenuItemBuilder::with_id("voice", "Voice on / off").build(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "Quit Alexa").build(app)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show_item, &expand_item, &voice_item])
                .separator()
                .items(&[&quit_item])
                .build()?;

            TrayIconBuilder::with_id("sunday")
                .icon(tray::icon("connecting"))
                .tooltip("Sunday")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show(app),
                    "expand" => expand(app.clone()),
                    // Voice is the window's to change, because the window
                    // is what holds the socket. The tray only asks.
                    "voice" => {
                        let _ = app.emit("toggle-voice", ());
                    }
                    "quit" => quit_politely(app),
                    _ => {}
                })
                .on_tray_icon_event(|icon, event| {
                    // Left click as well as double: a tray icon you have to
                    // discover the double-click on is a tray icon that has
                    // stranded somebody.
                    let wake = matches!(
                        event,
                        TrayIconEvent::DoubleClick { .. }
                            | TrayIconEvent::Click {
                                button: tauri::tray::MouseButton::Left,
                                button_state: tauri::tray::MouseButtonState::Up,
                                ..
                            }
                    );
                    if wake {
                        expand(icon.app_handle().clone());
                    }
                })
                .build(app)?;

            // Spawning it here rather than on the window's first request means
            // the models start loading while the WebView is still painting.
            let spawn = handle.clone();
            std::thread::spawn(move || {
                let Some(shell) = spawn.try_state::<Shell>() else { return };
                let started = {
                    let Ok(mut guard) = shell.sidecar.lock() else { return };
                    guard.start()
                };
                match started {
                    Ok(handshake) => {
                        let _ = spawn.emit("sidecar-ready", handshake);
                        watch(spawn.clone());
                    }
                    // The fault is already recorded, inside the lock the start
                    // was holding. Saying so here as well would be a second
                    // writer for one fact.
                    Err(why) => {
                        let _ = spawn.emit("sidecar-lost", why);
                    }
                }
            });

            let wanted = read_settings();

            // `[ui] autostart` is a wish; the Run registry key is the fact.
            // Syncing them here means the config file is the one place it is
            // decided, rather than a checkbox somebody has to find -- and
            // turning it off in config turns it off on the machine.
            let manager = app.autolaunch();
            let registered = manager.is_enabled().unwrap_or(false);
            if wanted.autostart != registered {
                let outcome = if wanted.autostart {
                    manager.enable()
                } else {
                    manager.disable()
                };
                if let Err(why) = outcome {
                    eprintln!("could not change autostart: {why}");
                }
            }

            if wanted.start_minimised {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing quits, and quits *politely* -- the same thing typing
            // `exit` does. It used to hide to the tray, on the argument that
            // something meant to listen in the background should not be shut
            // by a stray click; the answer to that is the tray's own Show,
            // and a close button that does not close is a control that lies.
            //
            // The order is the part that matters and is why this cannot just
            // be `app.exit(0)`: the sidecar is told first, because `shutdown`
            // is what folds the session summary into Chroma, and Chroma is
            // SQLite, which does not survive being killed mid-write.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                quit_politely(&window.app_handle().clone());
            }
        })
        .run(tauri::generate_context!())
        .expect("Sunday could not start");
}
