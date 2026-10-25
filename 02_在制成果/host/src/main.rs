use std::process::Command;
use std::time::Duration;
use zectrix_sdk::{Device, Event, ImageFormat, Key, KeyAction, OpenOptions};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let target = args.get(1).cloned();
    let dir = args
        .get(2)
        .cloned()
        .unwrap_or_else(|| "../runtime/pages".into());
    let updater = args
        .get(3)
        .cloned()
        .unwrap_or_else(|| "../../03_项目固定资源/bin/update_live.sh".into());
    let transcriber = args
        .get(4)
        .cloned()
        .unwrap_or_else(|| "../src/voice_transcribe.py".into());
    let voice_page = std::path::Path::new(&transcriber)
        .parent()
        .map(|p| p.join("voice_page.py").to_string_lossy().into_owned())
        .unwrap_or_else(|| "voice_page.py".into());
    let voice_dir = std::path::Path::new(&dir)
        .parent()
        .map(|p| p.join("voice"))
        .unwrap_or_else(|| std::path::PathBuf::from(format!("{dir}/voice")));
    run_updater(&updater);
    let pages = scan_pages(&dir);
    let device = Device::open(OpenOptions {
        target,
        client_name: Some("Sports Dashboard Pro".into()),
    })
    .await?;

    if pages.is_empty() {
        return Err(std::io::Error::new(std::io::ErrorKind::NotFound, "no pages found").into());
    }
    let mut current = 2usize.min(pages.len() - 1);
    let mut last_shown = show_page(&device, &dir, pages[current]).await?;
    println!("READY\tpage={}\tname={}", current + 1, pages[current].0);

    let mut refresh_tick = tokio::time::interval(Duration::from_secs(600));
    refresh_tick.tick().await;
    let (update_tx, mut update_rx) = tokio::sync::mpsc::channel::<bool>(1);
    let mut updating = false;
    let (voice_tx, mut voice_rx) = tokio::sync::mpsc::channel::<Option<String>>(1);
    let mut voice_busy = false;
    let mut voice_showing = false;
    loop {
        tokio::select! {
            event = device.next_event() => {
                let Some(event) = event? else { break; };
                if voice_showing {
                    if matches!(event, Event::Key { .. }) {
                        voice_showing = false;
                        let pages = scan_pages(&dir);
                        if clamp_current(&pages, &mut current) {
                            match show_page(&device, &dir, pages[current]).await {
                                Ok(png) => {
                                    last_shown = png;
                                    println!("VOICE_CLOSED\t{}\t{}", current + 1, pages[current].0);
                                }
                                Err(error) => println!("VOICE_CLOSE_FAILED\t{error}"),
                            }
                        }
                        continue;
                    }
                }
                match event {
                    Event::Key { key: Key::Up, action: KeyAction::Short } => {
                        let pages = scan_pages(&dir);
                        if clamp_current(&pages, &mut current) {
                            current = (current + pages.len() - 1) % pages.len();
                            match show_page(&device, &dir, pages[current]).await {
                                Ok(png) => {
                                    last_shown = png;
                                    println!("PAGE\t{}\t{}\tkey=up", current + 1, pages[current].0);
                                }
                                Err(error) => println!("PAGE_READ_FAILED\t{error}"),
                            }
                        } else {
                            println!("NO_PAGES");
                        }
                    }
                    Event::Key { key: Key::Down, action: KeyAction::Short } => {
                        let pages = scan_pages(&dir);
                        if clamp_current(&pages, &mut current) {
                            current = (current + 1) % pages.len();
                            match show_page(&device, &dir, pages[current]).await {
                                Ok(png) => {
                                    last_shown = png;
                                    println!("PAGE\t{}\t{}\tkey=down", current + 1, pages[current].0);
                                }
                                Err(error) => println!("PAGE_READ_FAILED\t{error}"),
                            }
                        } else {
                            println!("NO_PAGES");
                        }
                    }
                    Event::Key { key: Key::Confirm, action: KeyAction::Short } => {
                        let pages = scan_pages(&dir);
                        if clamp_current(&pages, &mut current) {
                            match show_page(&device, &dir, pages[current]).await {
                                Ok(png) => {
                                    last_shown = png;
                                    println!("PAGE\t{}\t{}\tkey=confirm", current + 1, pages[current].0);
                                }
                                Err(error) => println!("PAGE_READ_FAILED\t{error}"),
                            }
                        } else {
                            println!("NO_PAGES");
                        }
                    }
                    Event::RecordingStarted => {
                        println!("VOICE_LISTENING");
                        match show_voice_text(&device, &voice_page, &voice_dir, "正在聆听…").await {
                            Ok(()) => voice_showing = true,
                            Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                        }
                    }
                    Event::RecordingFinished { audio, reason } => {
                        match audio {
                            Some(audio) => {
                                println!("VOICE_RECORDED\tsamples={}\treason={:?}", audio.samples.len(), reason);
                                if voice_busy {
                                    println!("VOICE_SKIPPED\tbusy");
                                } else {
                                    voice_busy = true;
                                    if let Err(error) = std::fs::create_dir_all(&voice_dir) {
                                        println!("VOICE_DIR_FAILED\t{error}");
                                        voice_busy = false;
                                    } else {
                                        let wav_path = voice_dir.join("last.wav");
                                        match write_wav(&wav_path, &audio.samples) {
                                            Ok(()) => {
                                                let tx = voice_tx.clone();
                                                let transcriber = transcriber.clone();
                                                let wav = wav_path.to_string_lossy().into_owned();
                                                tokio::task::spawn_blocking(move || {
                                                    let out = Command::new(&transcriber).arg(&wav).output();
                                                    let text = match out {
                                                        Ok(o) if o.status.success() => {
                                                            Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
                                                        }
                                                        _ => None,
                                                    };
                                                    let _ = tx.blocking_send(text);
                                                });
                                            }
                                            Err(error) => {
                                                println!("VOICE_WAV_FAILED\t{error}");
                                                voice_busy = false;
                                            }
                                        }
                                    }
                                }
                            }
                            None => println!("VOICE_EMPTY\treason={:?}", reason),
                        }
                    }
                    Event::Key { key, action } => println!("IGNORED_KEY\t{key:?}\t{action:?}"),
                    Event::Disconnected { reason } => {
                        println!("DISCONNECTED\t{reason:?}");
                        break;
                    }
                    other => println!("EVENT\t{other:?}"),
                }
            }
            _ = refresh_tick.tick() => {
                if updating {
                    println!("UPDATE_SKIPPED\tbusy");
                } else {
                    updating = true;
                    let updater = updater.clone();
                    let tx = update_tx.clone();
                    tokio::task::spawn_blocking(move || {
                        let changed = run_updater(&updater);
                        let _ = tx.blocking_send(changed);
                    });
                }
            }
            changed = update_rx.recv() => {
                updating = false;
                let pages = scan_pages(&dir);
                let prev = current;
                if !clamp_current(&pages, &mut current) {
                    println!("NO_PAGES");
                } else {
                    if current != prev {
                        match show_page(&device, &dir, pages[current]).await {
                            Ok(png) => {
                                last_shown = png;
                                voice_showing = false;
                                println!("PAGE_FALLBACK\t{}\t{}", current + 1, pages[current].0);
                            }
                            Err(error) => println!("PAGE_FALLBACK_FAILED\t{error}"),
                        }
                    }
                    if changed == Some(true) {
                        match std::fs::read(format!("{dir}/{}", pages[current].1)) {
                            Ok(latest) => {
                                if latest != last_shown {
                                    device.show_with_format(&latest, ImageFormat::Gray16).await?;
                                    last_shown = latest;
                                    voice_showing = false;
                                    println!("AUTO_REFRESH\t{}\t{}", current + 1, pages[current].0);
                                } else {
                                    println!("AUTO_REFRESH_SKIPPED\tvisible_page_unchanged");
                                }
                            }
                            Err(error) => println!("AUTO_REFRESH_FAILED\t{error}"),
                        }
                    } else {
                        println!("POLL\tno_content_change");
                    }
                }
            }
            text = voice_rx.recv() => {
                voice_busy = false;
                match text {
                    Some(Some(text)) => {
                        let _ = std::fs::remove_file(voice_dir.join("last.wav"));
                        let display = if text.is_empty() { "没听清，请再说一遍" } else { &text };
                        println!("VOICE_TEXT\t{text}");
                        match show_voice_text(&device, &voice_page, &voice_dir, display).await {
                            Ok(()) => voice_showing = true,
                            Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                        }
                    }
                    Some(None) => {
                        println!("VOICE_TRANSCRIBE_FAILED\tkeeping_wav");
                        match show_voice_text(&device, &voice_page, &voice_dir, "转写失败，请再说一遍").await {
                            Ok(()) => voice_showing = true,
                            Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                        }
                    }
                    None => println!("VOICE_CHANNEL_CLOSED"),
                }
            }
        }
    }
    device.close().await?;
    Ok(())
}

fn scan_pages(dir: &str) -> Vec<(&'static str, &'static str)> {
    const KNOWN: [(&str, &str); 4] = [
        ("schedule", "01_schedule.png"),
        ("results", "02_results.png"),
        ("focus", "03_focus.png"),
        ("f1focus", "04_f1_focus.png"),
    ];
    KNOWN
        .into_iter()
        .filter(|(_, file)| std::path::Path::new(&format!("{dir}/{file}")).exists())
        .collect()
}

fn clamp_current(pages: &[(&str, &str)], current: &mut usize) -> bool {
    if pages.is_empty() {
        return false;
    }
    if *current >= pages.len() {
        *current = pages.len() - 1;
    }
    true
}

fn run_updater(path: &str) -> bool {
    match Command::new(path).status() {
        Ok(status) if status.code() == Some(0) => {
            println!("UPDATE\tchanged");
            true
        }
        Ok(status) if status.code() == Some(3) => {
            println!("UPDATE\tunchanged");
            false
        }
        Ok(status) => {
            println!("UPDATE_FAILED\tstatus={status}");
            false
        }
        Err(error) => {
            println!("UPDATE_FAILED\t{error}");
            false
        }
    }
}

async fn show_page(
    device: &Device,
    dir: &str,
    page: (&str, &str),
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let png = std::fs::read(format!("{dir}/{}", page.1))?;
    device.show_with_format(&png, ImageFormat::Gray16).await?;
    Ok(png)
}

fn write_wav(path: &std::path::Path, samples: &[i16]) -> std::io::Result<()> {
    let n = (samples.len() * 2) as u32;
    let mut b = Vec::new();
    b.extend(b"RIFF");
    b.extend((36 + n).to_le_bytes());
    b.extend(b"WAVEfmt ");
    b.extend(16u32.to_le_bytes());
    b.extend(1u16.to_le_bytes());
    b.extend(1u16.to_le_bytes());
    b.extend(16000u32.to_le_bytes());
    b.extend(32000u32.to_le_bytes());
    b.extend(2u16.to_le_bytes());
    b.extend(16u16.to_le_bytes());
    b.extend(b"data");
    b.extend(n.to_le_bytes());
    for x in samples {
        b.extend(x.to_le_bytes());
    }
    std::fs::write(path, b)
}

async fn show_voice_text(
    device: &Device,
    voice_page: &str,
    voice_dir: &std::path::Path,
    text: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let png_path = voice_dir.join("voice_page.png");
    let status = Command::new(voice_page).arg(text).arg(&png_path).status()?;
    if !status.success() {
        return Err(format!("voice_page exited {status}").into());
    }
    let png = std::fs::read(&png_path)?;
    device.show_with_format(&png, ImageFormat::Gray16).await?;
    Ok(())
}
