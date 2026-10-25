use std::io::{BufRead, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::time::Duration;
use zectrix_sdk::{Device, Event, ImageFormat, Key, KeyAction, OpenOptions};

enum VoiceMsg {
    Ready,
    Done(Option<String>),
    Died,
}

enum Dispatch {
    Sent,
    Queued(usize),
    Dropped,
}

enum Pending {
    Text { title: String, text: String },
    Flip { step: i32 },
    Refresh,
}

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
    let worker_script = args
        .get(4)
        .cloned()
        .unwrap_or_else(|| "../src/voice_worker.py".into());
    let voice_page = std::path::Path::new(&worker_script)
        .parent()
        .map(|p| p.join("voice_page.py").to_string_lossy().into_owned())
        .unwrap_or_else(|| "voice_page.py".into());
    let voice_answer = std::path::Path::new(&worker_script)
        .parent()
        .map(|p| p.join("voice_answer.py").to_string_lossy().into_owned())
        .unwrap_or_else(|| "voice_answer.py".into());
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
    let (voice_tx, mut voice_rx) = tokio::sync::mpsc::channel::<VoiceMsg>(8);
    let mut voice_busy = false;
    let mut voice_showing = false;
    let mut recording = false;
    let mut voice_refresh = false;
    let mut pending_display: std::collections::VecDeque<Pending> =
        std::collections::VecDeque::new();
    let mut worker_ready = false;
    let mut voice_queue: std::collections::VecDeque<std::path::PathBuf> =
        std::collections::VecDeque::new();
    let mut voice_current: Option<std::path::PathBuf> = None;
    let mut voice_seq: u64 = 0;
    let mut worker_crashes: u32 = 0;
    let mut worker_last_died: Option<std::time::Instant> = None;
    let (mut voice_child, mut voice_stdin) =
        match spawn_voice_worker(&worker_script, voice_tx.clone()) {
            Some((child, stdin)) => (Some(child), Some(stdin)),
            None => {
                println!("VOICE_WORKER_SPAWN_FAILED");
                (None, None)
            }
        };
    loop {
        tokio::select! {
            event = device.next_event() => {
                let Some(event) = event? else { break; };
                if voice_showing {
                    if matches!(event, Event::Key { .. }) {
                        voice_showing = false;
                        voice_refresh = false;
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
                        recording = true;
                        println!("VOICE_LISTENING");
                    }
                    Event::RecordingFinished { audio, reason } => {
                        recording = false;
                        match audio {
                            Some(audio) => {
                                println!("VOICE_RECORDED\tsamples={}\treason={:?}", audio.samples.len(), reason);
                                if let Err(error) = std::fs::create_dir_all(&voice_dir) {
                                    println!("VOICE_DIR_FAILED\t{error}");
                                } else {
                                    voice_seq += 1;
                                    let wav_path = voice_dir.join(format!("voice_{voice_seq}.wav"));
                                    match write_wav(&wav_path, &audio.samples) {
                                        Ok(()) => {
                                            let dispatch = if worker_ready && !voice_busy {
                                                if send_to_worker(&mut voice_stdin, &wav_path) {
                                                    voice_busy = true;
                                                    voice_current = Some(wav_path);
                                                    Dispatch::Sent
                                                } else {
                                                    println!("VOICE_WORKER_WRITE_FAILED");
                                                    voice_queue.push_front(wav_path);
                                                    Dispatch::Queued(1)
                                                }
                                            } else if voice_queue.len() >= 3 {
                                                let _ = std::fs::remove_file(&wav_path);
                                                println!("VOICE_QUEUE_FULL");
                                                Dispatch::Dropped
                                            } else {
                                                voice_queue.push_back(wav_path);
                                                Dispatch::Queued(voice_queue.len())
                                            };
                                            // A result that completed while recording must be
                                            // shown first, in recording order; the spinner must
                                            // not overwrite a result the user never saw. A
                                            // dropped recording keeps its notice instead, and
                                            // the pending result flushes at the next Done.
                                            if matches!(dispatch, Dispatch::Dropped) {
                                                match show_voice_text(&device, &voice_page, &voice_dir, "语音识别", "排队已满，请稍后再说").await {
                                                    Ok(()) => voice_showing = true,
                                                    Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                                                }
                                            } else if let Some(item) = pending_display.pop_front() {
                                                if let Some(item) = execute_pending(&device, &dir, &mut current, &mut last_shown, &voice_page, &voice_dir, &updater, &mut updating, &update_tx, &mut voice_showing, &mut voice_refresh, item).await {
                                                    pending_display.push_front(item);
                                                }
                                            } else {
                                                let feedback = match &dispatch {
                                                    Dispatch::Sent => "转写中…".to_string(),
                                                    Dispatch::Queued(n) => format!("转写中，排队第 {n} 条"),
                                                    Dispatch::Dropped => unreachable!(),
                                                };
                                                match show_voice_text(&device, &voice_page, &voice_dir, "语音识别", &feedback).await {
                                                    Ok(()) => voice_showing = true,
                                                    Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                                                }
                                            }
                                        }
                                        Err(error) => println!("VOICE_WAV_FAILED\t{error}"),
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
                let mut pushed = false;
                if !clamp_current(&pages, &mut current) {
                    println!("NO_PAGES");
                } else {
                    if current != prev {
                        match show_page(&device, &dir, pages[current]).await {
                            Ok(png) => {
                                last_shown = png;
                                voice_showing = false;
                                pushed = true;
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
                                    pushed = true;
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
                    if voice_refresh {
                        voice_refresh = false;
                        if !pushed {
                            match show_voice_text(&device, &voice_page, &voice_dir, "语音识别", "已刷新").await {
                                Ok(()) => voice_showing = true,
                                Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                            }
                        }
                    }
                }
            }
            msg = voice_rx.recv() => {
                match msg {
                    Some(VoiceMsg::Ready) => {
                        worker_ready = true;
                        println!("VOICE_WORKER_READY");
                        drain_voice_queue(&mut voice_queue, &mut voice_stdin, &mut voice_busy, &mut voice_current);
                    }
                    Some(VoiceMsg::Done(result)) => {
                        voice_busy = false;
                        if let Some(wav) = voice_current.take() {
                            let _ = std::fs::remove_file(&wav);
                        }
                        let item = match result {
                            Some(text) => {
                                println!("VOICE_TEXT\t{text}");
                                if text.is_empty() {
                                    Pending::Text { title: "语音识别".to_string(), text: "没听清，请再说一遍".to_string() }
                                } else if text.contains("刷新") {
                                    Pending::Refresh
                                } else if text.contains("上一页") || text.contains("前一页") {
                                    Pending::Flip { step: -1 }
                                } else if text.contains("下一页") || text.contains("后一页") || text.contains("翻页") {
                                    Pending::Flip { step: 1 }
                                } else {
                                    match run_voice_answer(&voice_answer, &text) {
                                        Some(answer) if !answer.trim().is_empty() => Pending::Text { title: "语音回答".to_string(), text: answer },
                                        _ => Pending::Text { title: "语音识别".to_string(), text: format!("{text}\n没听懂，请换个说法") },
                                    }
                                }
                            }
                            None => {
                                println!("VOICE_TRANSCRIBE_FAILED");
                                Pending::Text { title: "语音识别".to_string(), text: "转写失败，请再说一遍".to_string() }
                            }
                        };
                        pending_display.push_back(item);
                        if !recording {
                            if let Some(item) = pending_display.pop_front() {
                                if let Some(item) = execute_pending(&device, &dir, &mut current, &mut last_shown, &voice_page, &voice_dir, &updater, &mut updating, &update_tx, &mut voice_showing, &mut voice_refresh, item).await {
                                    pending_display.push_front(item);
                                }
                            }
                        }
                        if worker_ready {
                            drain_voice_queue(&mut voice_queue, &mut voice_stdin, &mut voice_busy, &mut voice_current);
                        }
                    }
                    Some(VoiceMsg::Died) => {
                        worker_ready = false;
                        voice_busy = false;
                        if let Some(wav) = voice_current.take() {
                            voice_queue.push_front(wav);
                        }
                        if let Some(mut old) = voice_child.take() {
                            let _ = old.try_wait();
                        }
                        println!("VOICE_WORKER_DIED");
                        let now = std::time::Instant::now();
                        if let Some(last) = worker_last_died {
                            if now.duration_since(last).as_secs() < 60 {
                                worker_crashes += 1;
                            } else {
                                worker_crashes = 1;
                            }
                        } else {
                            worker_crashes = 1;
                        }
                        worker_last_died = Some(now);
                        if worker_crashes >= 3 {
                            println!("VOICE_WORKER_BROKEN");
                        } else {
                            match spawn_voice_worker(&worker_script, voice_tx.clone()) {
                                Some((child, stdin)) => {
                                    voice_child = Some(child);
                                    voice_stdin = Some(stdin);
                                }
                                None => {
                                    voice_stdin = None;
                                    println!("VOICE_WORKER_SPAWN_FAILED");
                                }
                            }
                        }
                    }
                    None => println!("VOICE_CHANNEL_CLOSED"),
                }
            }
        }
    }
    voice_stdin.take();
    if let Some(mut child) = voice_child.take() {
        let _ = child.kill();
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
    title: &str,
    text: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    std::fs::create_dir_all(voice_dir)?;
    let png_path = voice_dir.join("voice_page.png");
    let status = Command::new(voice_page).arg(text).arg(&png_path).arg(title).status()?;
    if !status.success() {
        return Err(format!("voice_page exited {status}").into());
    }
    let png = std::fs::read(&png_path)?;
    device.show_with_format(&png, ImageFormat::Gray16).await?;
    Ok(())
}

fn run_voice_answer(script: &str, text: &str) -> Option<String> {
    match Command::new(script).arg(text).output() {
        Ok(out) => {
            let answer = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if answer.is_empty() {
                None
            } else {
                Some(answer)
            }
        }
        Err(error) => {
            println!("VOICE_ANSWER_FAILED\t{error}");
            None
        }
    }
}

async fn execute_pending(
    device: &Device,
    dir: &str,
    current: &mut usize,
    last_shown: &mut Vec<u8>,
    voice_page: &str,
    voice_dir: &std::path::Path,
    updater: &str,
    updating: &mut bool,
    update_tx: &tokio::sync::mpsc::Sender<bool>,
    voice_showing: &mut bool,
    voice_refresh: &mut bool,
    item: Pending,
) -> Option<Pending> {
    match item {
        Pending::Text { title, text } => {
            match show_voice_text(device, voice_page, voice_dir, &title, &text).await {
                Ok(()) => {
                    *voice_showing = true;
                    None
                }
                Err(error) => {
                    println!("VOICE_PAGE_FAILED\t{error}");
                    Some(Pending::Text { title, text })
                }
            }
        }
        Pending::Flip { step } => {
            *voice_showing = false;
            let pages = scan_pages(dir);
            if !clamp_current(&pages, current) {
                println!("NO_PAGES");
                return None;
            }
            let len = pages.len() as i32;
            *current = (*current as i32 + step).rem_euclid(len) as usize;
            match show_page(device, dir, pages[*current]).await {
                Ok(png) => {
                    *last_shown = png;
                    println!("PAGE\t{}\t{}\tkey=voice", *current + 1, pages[*current].0);
                    None
                }
                Err(error) => {
                    println!("PAGE_READ_FAILED\t{error}");
                    Some(Pending::Flip { step })
                }
            }
        }
        Pending::Refresh => {
            if !*updating {
                *updating = true;
                let updater = updater.to_string();
                let tx = update_tx.clone();
                tokio::task::spawn_blocking(move || {
                    let changed = run_updater(&updater);
                    let _ = tx.blocking_send(changed);
                });
            }
            *voice_refresh = true;
            match show_voice_text(device, voice_page, voice_dir, "语音识别", "正在刷新…").await {
                Ok(()) => {
                    *voice_showing = true;
                    None
                }
                Err(error) => {
                    println!("VOICE_PAGE_FAILED\t{error}");
                    Some(Pending::Refresh)
                }
            }
        }
    }
}

fn spawn_voice_worker(
    script: &str,
    tx: tokio::sync::mpsc::Sender<VoiceMsg>,
) -> Option<(Child, ChildStdin)> {
    let mut child = Command::new(script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .ok()?;
    let stdin = child.stdin.take()?;
    let stdout = child.stdout.take()?;
    std::thread::spawn(move || {
        let reader = std::io::BufReader::new(stdout);
        let mut ready = false;
        for line in reader.lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            if !ready {
                if line == "READY" {
                    ready = true;
                    let _ = tx.blocking_send(VoiceMsg::Ready);
                } else {
                    break;
                }
            } else if let Some(text) = line.strip_prefix("OK\t") {
                let _ = tx.blocking_send(VoiceMsg::Done(Some(text.to_string())));
            } else if line.strip_prefix("ERR\t").is_some() {
                let _ = tx.blocking_send(VoiceMsg::Done(None));
            } else {
                break;
            }
        }
        let _ = tx.blocking_send(VoiceMsg::Died);
    });
    Some((child, stdin))
}

fn send_to_worker(stdin: &mut Option<ChildStdin>, wav: &std::path::Path) -> bool {
    match stdin {
        Some(s) => {
            let line = format!("{}\n", wav.display());
            s.write_all(line.as_bytes()).and_then(|_| s.flush()).is_ok()
        }
        None => false,
    }
}

fn drain_voice_queue(
    queue: &mut std::collections::VecDeque<std::path::PathBuf>,
    stdin: &mut Option<ChildStdin>,
    busy: &mut bool,
    current: &mut Option<std::path::PathBuf>,
) {
    if *busy {
        return;
    }
    if let Some(wav) = queue.pop_front() {
        if send_to_worker(stdin, &wav) {
            *busy = true;
            *current = Some(wav);
        } else {
            queue.push_front(wav);
            println!("VOICE_WORKER_WRITE_FAILED");
        }
    }
}
