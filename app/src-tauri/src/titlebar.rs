//! Making the title bar the same colour as the app.
//!
//! The bar is drawn by Windows, above the WebView and outside it, so no amount
//! of CSS reaches it. A dark window with a light grey caption strip on top of
//! it looks like two programs stacked, and that is what this fixes.
//!
//! `DwmSetWindowAttribute` with `DWMWA_CAPTION_COLOR` is the supported way and
//! it arrived in Windows 11 (build 22000). On anything older the call returns
//! an error, the bar stays the colour the system chose, and the app is fine --
//! which is why the results are deliberately ignored rather than reported.
//!
//! The one trap: a `COLORREF` is `0x00BBGGRR`, not RGB. Writing the hex the
//! way it appears in the stylesheet gives you the red and blue swapped, which
//! looks like a deliberate choice rather than a bug and would probably have
//! survived review.

#[cfg(windows)]
use windows::Win32::Foundation::{COLORREF, HWND};
#[cfg(windows)]
use windows::Win32::Graphics::Dwm::{
    DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR,
};

/// `--bg` and `--text` from the stylesheet, byte for byte. Two places for one
/// colour is a thing that goes stale, so if the palette moves, this moves.
const BG: (u8, u8, u8) = (0x26, 0x26, 0x24);
const FG: (u8, u8, u8) = (0xf5, 0xf4, 0xef);
/// Slightly lighter than the background, so the window still has an edge
/// against a dark desktop instead of bleeding into it.
const EDGE: (u8, u8, u8) = (0x40, 0x3f, 0x3b);

#[cfg(windows)]
fn colorref((r, g, b): (u8, u8, u8)) -> COLORREF {
    COLORREF(u32::from(r) | (u32::from(g) << 8) | (u32::from(b) << 16))
}

/// Paint the caption to match. Safe to call more than once.
#[cfg(windows)]
pub fn match_the_app(hwnd: isize) {
    let handle = HWND(hwnd as *mut std::ffi::c_void);
    for (attribute, colour) in [
        (DWMWA_CAPTION_COLOR, BG),
        (DWMWA_TEXT_COLOR, FG),
        (DWMWA_BORDER_COLOR, EDGE),
    ] {
        let value = colorref(colour);
        // SAFETY: `handle` comes from Tauri's own window and outlives the
        // call; the pointer and size describe a COLORREF, which is what each
        // of these three attributes takes.
        unsafe {
            let _ = DwmSetWindowAttribute(
                handle,
                attribute,
                std::ptr::addr_of!(value).cast(),
                std::mem::size_of::<COLORREF>() as u32,
            );
        }
    }
}

#[cfg(not(windows))]
pub fn match_the_app(_hwnd: isize) {}
