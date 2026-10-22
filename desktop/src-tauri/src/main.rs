// Filomena desktop shell: starts the bundled Python server (the `meta-duplo-server` sidecar) on a free local port
// with a fresh session token, shows a "starting" page until it answers, then points the window at it. The server
// is stopped when the app quits. The UI itself is the same HTML the browser version serves (ADR-008).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::File;
use std::io::Read;
use std::net::{TcpListener, TcpStream};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// The running server, so it can be stopped on exit. If the app dies without stopping it, the server notices
/// (META_DUPLO_PARENT_PID) and stops itself.
struct Server(Mutex<Option<CommandChild>>);

/// A port nobody uses right now (the OS picks it).
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

/// 128 random bits as hex: the session token other local web pages cannot guess.
fn session_token() -> String {
    let mut bytes = [0u8; 16];
    if let Ok(mut f) = File::open("/dev/urandom") {
        let _ = f.read_exact(&mut bytes);
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Ask the server to stop. SIGTERM, not SIGKILL: the one-file binary runs Python in a child process and passes
/// SIGTERM on to it, whereas SIGKILL would leave that child running.
#[cfg(unix)]
fn stop(child: CommandChild) {
    unsafe {
        libc::kill(child.pid() as libc::pid_t, libc::SIGTERM);
    }
}

#[cfg(not(unix))]
fn stop(child: CommandChild) {
    let _ = child.kill();
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let port = free_port();
            let token = session_token();
            let (mut events, child) = app
                .shell()
                .sidecar("meta-duplo-server")?
                .args(["serve"])
                .env("META_DUPLO_HOST", "127.0.0.1")
                .env("META_DUPLO_PORT", port.to_string())
                .env("META_DUPLO_TOKEN", &token)
                .env("META_DUPLO_PARENT_PID", std::process::id().to_string())
                .spawn()?;
            *app.state::<Server>().0.lock().unwrap() = Some(child);

            // pass the server's own output on (visible when the app is started from a terminal)
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                            eprintln!("server: {}", String::from_utf8_lossy(&line).trim_end());
                        }
                        CommandEvent::Terminated(status) => eprintln!("server stopped: {status:?}"),
                        _ => {}
                    }
                }
            });

            // wait until the server answers (the one-file binary unpacks itself first), then open it
            let handle = app.handle().clone();
            thread::spawn(move || {
                let started = Instant::now();
                while TcpStream::connect(("127.0.0.1", port)).is_err() {
                    if started.elapsed() > Duration::from_secs(90) {
                        eprintln!("server did not start within 90 s");
                        return;
                    }
                    thread::sleep(Duration::from_millis(200));
                }
                let url: Url = format!("http://127.0.0.1:{port}/?token={token}").parse().expect("server URL");
                let window_handle = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    if let Some(window) = window_handle.get_webview_window("main") {
                        let _ = window.navigate(url);
                    }
                });
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while starting Filomena")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<Server>().0.lock().unwrap().take() {
                    stop(child);
                }
            }
        });
}
