// Filomena desktop shell: starts the bundled Python server (the `meta-duplo-server` sidecar) on a free local port
// with a fresh session token, shows a "starting" page until it answers, then points the window at it. The server
// is stopped when the app quits. The UI itself is the same HTML the browser version serves (ADR-008).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
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

/// The server's output also goes to ~/Library/Logs/Filomena/server.log (kept small: rewritten at each start).
fn log_file() -> Option<File> {
    let dir = PathBuf::from(std::env::var_os("HOME")?).join("Library/Logs/Filomena");
    fs::create_dir_all(&dir).ok()?;
    OpenOptions::new().create(true).write(true).truncate(true).open(dir.join("server.log")).ok()
}

/// Replace the start page's progress with the reason and the server's last lines (dist/index.html: showError).
fn show_error(handle: &tauri::AppHandle, text: &str, tail: &Arc<Mutex<VecDeque<String>>>) {
    let log = tail.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n");
    let js = format!("window.showError && window.showError({:?}, {:?})", text, log);
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(&js);
    }
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

            // the server's output: to the terminal (when started from one), the log file, and the last 40 lines kept
            // for the start page should it fail
            let ready = Arc::new(AtomicBool::new(false));
            let tail: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
            let (out_tail, out_ready, out_handle) = (tail.clone(), ready.clone(), app.handle().clone());
            tauri::async_runtime::spawn(async move {
                let mut file = log_file();
                while let Some(event) = events.recv().await {
                    let line = match event {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => String::from_utf8_lossy(&line).trim_end().to_string(),
                        CommandEvent::Terminated(status) => {
                            let msg = format!("server stopped: {status:?}");
                            eprintln!("{msg}");
                            if let Some(f) = file.as_mut() {
                                let _ = writeln!(f, "{msg}");
                            }
                            if !out_ready.load(Ordering::SeqCst) {
                                show_error(&out_handle, "The server stopped while starting.", &out_tail);
                            }
                            continue;
                        }
                        _ => continue,
                    };
                    eprintln!("server: {line}");
                    if let Some(f) = file.as_mut() {
                        let _ = writeln!(f, "{line}");
                    }
                    let mut t = out_tail.lock().unwrap();
                    t.push_back(line);
                    if t.len() > 40 {
                        t.pop_front();
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
                        show_error(&handle, "The server did not answer within 90 seconds.", &tail);
                        return;
                    }
                    thread::sleep(Duration::from_millis(200));
                }
                ready.store(true, Ordering::SeqCst);
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
