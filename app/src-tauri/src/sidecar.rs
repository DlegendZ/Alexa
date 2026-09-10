//! The Python process, and the shell's responsibility for it.
//!
//! The shell owns the window and the operating system; the sidecar owns the
//! audio and the thinking. This file is the whole of what the first knows
//! about the second: where its executable is, how to find the port and token
//! it wrote, and what to do when it dies.

use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// `CREATE_NO_WINDOW`. The sidecar is a console application -- a Python
/// console script, and later a PyInstaller build of one -- so Windows gives it
/// a console of its own, which appears beside the window as a black rectangle
/// nobody asked for and nobody can close without killing the assistant.
///
/// It is not the handshake, which is a file the two processes share and has
/// nothing to do with what the desktop shows. It is the subsystem of the child
/// process, and this flag is the whole of the fix.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

use serde::{Deserialize, Serialize};

/// How long to wait for the sidecar to write its handshake. Generous, because
/// preflight talks to Ollama before the socket binds, and a cold model is the
/// slowest thing that happens on a healthy launch.
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(90);

/// Restart it this many times in a minute, and then stop. A restart loop is
/// worse than an honest failure: it burns the card and hides the reason.
const RESTARTS_ALLOWED: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(60);

/// What the shell waits for the sidecar to give it, when quitting, before it
/// stops being polite. The sidecar is flushing the session summary to Chroma.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Handshake {
    pub port: u16,
    pub token: String,
    #[serde(default)]
    pub pid: u32,
    #[serde(default)]
    pub started: f64,
    #[serde(default)]
    pub protocol: u8,
}

/// `%LOCALAPPDATA%\Sunday`, or wherever `SUNDAY_HOME` points -- the same rule
/// the Python side follows, because there is one data directory and both
/// processes have to agree on it.
pub fn home() -> PathBuf {
    if let Ok(set) = std::env::var("SUNDAY_HOME") {
        return PathBuf::from(set);
    }
    let local = std::env::var("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(local).join("Sunday")
}

pub fn handshake_path() -> PathBuf {
    home().join("handshake.json")
}

/// Where the sidecar executable is.
///
/// Installed, it sits in `sidecar\` beside `Sunday.exe` as a PyInstaller
/// one-folder build. In development it is the editable install's console
/// script, which is why nothing here needs `PYTHONPATH`.
fn sidecar_exe() -> Option<PathBuf> {
    let mut tried = Vec::new();

    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            tried.push(dir.join("sidecar").join("sunday-sidecar.exe"));
            // `cargo tauri dev` puts the binary in target\debug, four levels
            // below the repo root.
            for up in [3usize, 4, 5] {
                let mut root = dir.to_path_buf();
                for _ in 0..up {
                    root = match root.parent() {
                        Some(parent) => parent.to_path_buf(),
                        None => break,
                    };
                }
                tried.push(root.join(".venv").join("Scripts").join("sunday-sidecar.exe"));
            }
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        tried.push(cwd.join(".venv").join("Scripts").join("sunday-sidecar.exe"));
        if let Some(parent) = cwd.parent() {
            tried.push(parent.join(".venv").join("Scripts").join("sunday-sidecar.exe"));
        }
    }

    tried.into_iter().find(|path| path.is_file())
}

fn now_epoch() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn read_handshake(path: &Path) -> Option<Handshake> {
    let raw = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

pub struct Sidecar {
    child: Option<Child>,
    pub handshake: Option<Handshake>,
    restarts: Vec<Instant>,
    pub fault: Option<String>,
}

impl Default for Sidecar {
    fn default() -> Self {
        Self { child: None, handshake: None, restarts: Vec::new(), fault: None }
    }
}

impl Sidecar {
    /// Spawn it and wait for the handshake it writes on the way up.
    ///
    /// A failure is recorded here rather than by the caller, because there are
    /// two callers -- the thread that starts it and the window that asks for
    /// the port -- and a fault set outside the lock leaves a gap in which the
    /// second one tries again and spawns a second sidecar onto one card.
    pub fn start(&mut self) -> Result<Handshake, String> {
        let outcome = self.spawn_and_wait();
        if let Err(why) = &outcome {
            self.fault = Some(why.clone());
        }
        outcome
    }

    fn spawn_and_wait(&mut self) -> Result<Handshake, String> {
        let exe = sidecar_exe().ok_or_else(|| {
            "could not find sunday-sidecar.exe -- in development, run \
             `pip install -e \".[dev]\"` in the repo's .venv"
                .to_string()
        })?;

        // A handshake is written each launch, but the old one is still on disk
        // until then. Connecting to it would point the window at a port
        // belonging to nothing, or to something else entirely -- so the file
        // only counts once it is newer than the moment we spawned.
        let spawned_at = now_epoch();
        let path = handshake_path();

        let mut command = Command::new(&exe);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let child = command
            .spawn()
            .map_err(|e| format!("could not start the sidecar: {e}"))?;
        self.child = Some(child);

        let deadline = Instant::now() + HANDSHAKE_TIMEOUT;
        while Instant::now() < deadline {
            if let Some(child) = self.child.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    return Err(format!(
                        "the sidecar exited before it was listening ({status}). \
                         Ollama not running is the usual reason."
                    ));
                }
            }
            if let Some(found) = read_handshake(&path) {
                if found.started >= spawned_at - 1.0 {
                    self.handshake = Some(found.clone());
                    self.fault = None;
                    return Ok(found);
                }
            }
            std::thread::sleep(Duration::from_millis(120));
        }
        Err("the sidecar never wrote its handshake".to_string())
    }

    /// Has it died? Called once a second by the watcher.
    pub fn exited(&mut self) -> bool {
        match self.child.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(Some(_))),
            None => false,
        }
    }

    /// Restart it, unless it has already been restarted too often to believe
    /// that restarting is the answer.
    pub fn restart(&mut self) -> Result<Handshake, String> {
        let now = Instant::now();
        self.restarts.retain(|at| now.duration_since(*at) < RESTART_WINDOW);
        if self.restarts.len() >= RESTARTS_ALLOWED {
            let message = format!(
                "the sidecar has crashed {RESTARTS_ALLOWED} times in a minute. \
                 Not restarting it again -- look at the log in {}.",
                home().join("logs").display()
            );
            self.fault = Some(message.clone());
            return Err(message);
        }
        self.restarts.push(now);
        self.handshake = None;
        self.start()
    }

    /// Quit properly. The socket has already been sent `shutdown`, which is
    /// what flushes the session summary into Chroma; this waits for that to
    /// finish and then stops waiting.
    pub fn stop(&mut self) {
        let Some(mut child) = self.child.take() else { return };
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(80));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}
