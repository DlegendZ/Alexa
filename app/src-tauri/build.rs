fn main() {
    // Cargo re-runs a build script only when something it was *told* about
    // changes, and `tauri_build` names exactly two things: `tauri.conf.json`
    // and `capabilities`. The icon is neither. It is a Windows resource
    // compiled into the executable from `icons/icon.ico`, so regenerating the
    // icons and rebuilding left the old one embedded -- indefinitely, and
    // looking exactly like a change that had not worked. Three rebuilds went
    // past before anyone thought to read this file.
    println!("cargo:rerun-if-changed=icons");

    // The installer carries a frozen sidecar from `sidecar/`, and
    // `tauri_build` refuses to run at all when a declared resource is missing
    // -- which would make `tauri dev` and `cargo check` depend on a
    // PyInstaller build nobody developing the window needs. So the folder is
    // made if absent. A release build without the frozen sidecar in it still
    // builds, and says so: an installer like that works only on a machine
    // with this repository's `.venv` above it.
    let _ = std::fs::create_dir_all("sidecar");
    println!("cargo:rerun-if-changed=sidecar/sunday-sidecar.exe");
    if std::env::var("PROFILE").as_deref() == Ok("release")
        && !std::path::Path::new("sidecar/sunday-sidecar.exe").is_file()
    {
        println!(
            "cargo:warning=no frozen sidecar in src-tauri/sidecar -- run packaging/build_sidecar.py \
             first, or this installer needs a .venv"
        );
    }

    tauri_build::build()
}
