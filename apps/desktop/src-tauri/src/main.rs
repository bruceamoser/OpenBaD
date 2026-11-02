#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if let Some(code) = openbad_desktop_lib::early_exit_for(&arguments) {
        std::process::exit(code);
    }
    openbad_desktop_lib::run();
}
