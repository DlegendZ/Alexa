//! The one piece of the frame Windows still draws.
//!
//! The window has no decorations: the title bar is markup, because Windows
//! will not let you keep its caption buttons and drop the icon and the title
//! beside them, and it draws all three at a size chosen for a file manager.
//!
//! What is left outside the WebView is the border, one pixel of it, and no
//! stylesheet reaches that either. Left alone it is the system accent, which
//! against this palette reads as a window belonging to some other program.
//!
//! `DwmSetWindowAttribute` with `DWMWA_BORDER_COLOR` is the supported way and
//! it arrived in Windows 11 (build 22000). On anything older the call returns
//! an error, the border stays the colour the system chose, and the app is
//! fine -- which is why the result is deliberately ignored rather than
//! reported.
//!
//! The one trap, and it survived a review once: a `COLORREF` is `0x00BBGGRR`,
//! not RGB. Writing the hex the way it appears in the stylesheet gives you the
//! red and blue swapped, which looks like a deliberate choice rather than a
//! bug.

#[cfg(windows)]
use windows::Win32::Foundation::{COLORREF, HWND};
#[cfg(windows)]
use windows::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_BORDER_COLOR};

/// `--bg` from the stylesheet, byte for byte. It used to be a few shades
/// lighter so the window had an edge against a dark desktop; that edge reads,
/// from inside, as a hairline drawn beside the orb, and the orb spent a whole
/// milestone losing its own. Two places for one colour is a thing that goes
/// stale, so if the palette moves, this moves.
const EDGE: (u8, u8, u8) = (0x26, 0x26, 0x24);

#[cfg(windows)]
fn colorref((r, g, b): (u8, u8, u8)) -> COLORREF {
    COLORREF(u32::from(r) | (u32::from(g) << 8) | (u32::from(b) << 16))
}

/// Paint the border to match. Safe to call more than once.
#[cfg(windows)]
pub fn match_the_app(hwnd: isize) {
    let handle = HWND(hwnd as *mut std::ffi::c_void);
    let value = colorref(EDGE);
    // SAFETY: `handle` comes from Tauri's own window and outlives the call;
    // the pointer and size describe a COLORREF, which is what this attribute
    // takes.
    unsafe {
        let _ = DwmSetWindowAttribute(
            handle,
            DWMWA_BORDER_COLOR,
            std::ptr::addr_of!(value).cast(),
            std::mem::size_of::<COLORREF>() as u32,
        );
    }
}

#[cfg(not(windows))]
pub fn match_the_app(_hwnd: isize) {}
