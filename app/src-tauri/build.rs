fn main() {
    // Cargo re-runs a build script only when something it was *told* about
    // changes, and `tauri_build` names exactly two things: `tauri.conf.json`
    // and `capabilities`. The icon is neither. It is a Windows resource
    // compiled into the executable from `icons/icon.ico`, so regenerating the
    // icons and rebuilding left the old one embedded -- indefinitely, and
    // looking exactly like a change that had not worked. Three rebuilds went
    // past before anyone thought to read this file.
    println!("cargo:rerun-if-changed=icons");
    tauri_build::build()
}
