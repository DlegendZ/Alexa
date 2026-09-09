//! The tray icon, and the one thing it is for.
//!
//! It is tinted with the current state colour, which means the privacy
//! affordance survives the window being hidden: if something is leaving this
//! machine, the icon in the corner is teal, whether or not you are looking at
//! the orb.

use tauri::image::Image;

const SIZE: u32 = 32;

/// The same palette as the orb, for the same reason. A tray that used its own
/// colours would be a second thing to keep in step, and it would be wrong the
/// first time a state was added.
fn tint(state: &str) -> [u8; 3] {
    match state {
        // Teal is something leaving this machine and red is a refusal. Those
        // two are the whole reason the icon is tinted; everything else is the
        // white the orb is, including a tool running here.
        "tool.external" => [108, 171, 156],
        "blocked" | "error" => [204, 123, 114],
        "listening" | "transcribing" | "wake" | "thinking" | "speaking" | "tool.local" => {
            [255, 252, 247]
        }
        // `idle` is the closed microphone now, and it is the only thing that
        // means that -- with the ear open the resting state is `listening`.
        _ => [140, 137, 130],
    }
}

/// A filled disc, drawn by hand.
///
/// No image crate: this is one circle at one size, and pulling in a decoder to
/// produce thirty-two rows of pixels would cost more to build than it saves to
/// write. The edge is anti-aliased by distance because a 32-pixel circle with
/// hard edges reads as a square.
pub fn icon(state: &str) -> Image<'static> {
    let [r, g, b] = tint(state);
    let centre = (SIZE as f32 - 1.0) / 2.0;
    let radius = SIZE as f32 * 0.40;

    let mut rgba = Vec::with_capacity((SIZE * SIZE * 4) as usize);
    for y in 0..SIZE {
        for x in 0..SIZE {
            let dx = x as f32 - centre;
            let dy = y as f32 - centre;
            let distance = (dx * dx + dy * dy).sqrt();
            let alpha = ((radius - distance) * 1.6).clamp(0.0, 1.0);
            rgba.extend_from_slice(&[r, g, b, (alpha * 255.0) as u8]);
        }
    }
    Image::new_owned(rgba, SIZE, SIZE)
}
