// The window is the product; this file is the part of it Windows can see.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;
mod tray;

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, LogicalSize, Manager, State, WindowEvent};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_global_shortcut::{
    Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState,
};

use sidecar::{Handshake, Sidecar};

/// Compact mode: a frameless always-on-top window with nothing in it but the
/// orb. One window resized rather than a second one, so there is nothing to
/// keep in step.
const COMPACT: (f64, f64) = (200.0, 240.0);
const FULL: (f64, f64) = (960.0, 700.0);

struct Shell {
    sidecar: Mutex<Sidecar>,
    /// What the orb is showing. The window is the only thing that knows, and
    /// the tray is the only thing that needs telling.
    state: Mutex<String>,
    /// Why the global hotkey is not working, if it is not.
    ///
    /// Another program can already hold `Ctrl+Alt+Space`, and on this machine
    /// one did. Refusing to start over that would be worse than having no
    /// hotkey -- but so is printing it to a console that a packaged app does
    /// not have. It reaches the window instead, which is the only place the
    /// person who pressed the keys is looking.
    hotkey: Mutex<Option<String>>,
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
    /// None when the hotkey registered. Some(reason) when it did not.
    #[serde(skip_serializing_if = "Option::is_none")]
    hotkey_error: Option<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            fps_focused: 60,
            fps_blurred: 10,
            start_minimised: false,
            autostart: false,
            trace: true,
            hotkey_error: None,
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
            hotkey_error: None,
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
fn settings(shell: State<'_, Shell>) -> Settings {
    let mut out = read_settings();
    out.hotkey_error = shell.hotkey.lock().ok().and_then(|held| held.clone());
    out
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
    let _ = window.set_decorations(!compact);
    let _ = window.set_always_on_top(compact);
    let _ = window.set_size(LogicalSize::new(w, h));
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

// -- the window ----------------------------------------------------------

fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
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
            hotkey: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            connection,
            settings,
            set_state,
            set_compact,
            minimise,
            set_autostart,
            autostart_enabled,
            quit,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            let show_item = MenuItemBuilder::with_id("show", "Show").build(app)?;
            let mute_item = MenuItemBuilder::with_id("mute", "Mute").build(app)?;
            let mode_item = MenuItemBuilder::with_id("mode", "Voice / text").build(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "Quit").build(app)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show_item, &mute_item, &mode_item])
                .separator()
                .items(&[&quit_item])
                .build()?;

            TrayIconBuilder::with_id("sunday")
                .icon(tray::icon("connecting"))
                .tooltip("Sunday")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show(app),
                    // Mute and mode are the window's to change, because the
                    // window is what holds the socket. The tray only asks.
                    "mute" => {
                        let _ = app.emit("toggle-mute", ());
                    }
                    "mode" => {
                        let _ = app.emit("toggle-mode", ());
                    }
                    "quit" => quit_politely(app),
                    _ => {}
                })
                .on_tray_icon_event(|icon, event| {
                    if let TrayIconEvent::DoubleClick { .. } = event {
                        show(&icon.app_handle().clone());
                    }
                })
                .build(app)?;

            // Push to talk. It reaches the sidecar as `listen`, which is one
            // event rather than a standing mode -- the wake word is the usual
            // way in, and this is for when you would rather not say it.
            let hotkey = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space);
            app.handle().plugin(
                tauri_plugin_global_shortcut::Builder::new()
                    .with_handler(move |app, _shortcut, event| {
                        if event.state() != ShortcutState::Pressed {
                            return;
                        }
                        show(app);
                        let _ = app.emit("listen", ());
                    })
                    .build(),
            )?;
            if let Err(why) = app.global_shortcut().register(hotkey) {
                // Another program already has it. Carry on -- a window that
                // refuses to start over a hotkey is worse than a window with
                // no hotkey -- but record it where the window can say so. A
                // console message is invisible in a packaged app, and a
                // shortcut that quietly does nothing is the failure this
                // codebase keeps relearning.
                let told = format!(
                    "Ctrl+Alt+Space is already taken by another program, so push-to-talk from the keyboard will not work ({why})."
                );
                eprintln!("{told}");
                if let Some(shell) = app.try_state::<Shell>() {
                    if let Ok(mut held) = shell.hotkey.lock() {
                        *held = Some(told);
                    }
                }
            }

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

            if read_settings().start_minimised {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window hides it, because there is a tray icon and
            // the thing is meant to be listening in the background. Quit is
            // in the tray menu, and it is the one that stops the sidecar.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("Sunday could not start");
}
