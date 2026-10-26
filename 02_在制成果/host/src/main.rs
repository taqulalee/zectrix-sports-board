use std::io::{BufRead, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::time::Duration;
use zectrix_sdk::{AudioClip, Device, Event, ImageFormat, Key, KeyAction, OpenOptions};

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

enum HermesMsg {
    Done { answer: Option<String>, asked: String },
}

struct TeamProposal {
    file_json: String,
    desc: String,
}

enum TeamResult {
    None,
    Unparsed,
    NoSelection,
    Proposal { file_json: String, desc: String },
}

enum Pending {
    Text { title: String, text: String },
    Flip { step: i32 },
    Refresh,
    ConfirmTeam { file_json: String, desc: String },
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
    let voice_hermes = std::path::Path::new(&worker_script)
        .parent()
        .map(|p| p.join("voice_hermes.py").to_string_lossy().into_owned())
        .unwrap_or_else(|| "voice_hermes.py".into());
    let voice_speak = std::path::Path::new(&worker_script)
        .parent()
        .map(|p| p.join("voice_speak.py").to_string_lossy().into_owned())
        .unwrap_or_else(|| "voice_speak.py".into());
    let voice_team = std::path::Path::new(&worker_script)
        .parent()
        .map(|p| p.join("voice_team.py").to_string_lossy().into_owned())
        .unwrap_or_else(|| "voice_team.py".into());
    let team_selection = std::path::Path::new(&dir)
        .parent()
        .map(|p| p.join("team_selection.json").to_string_lossy().into_owned())
        .unwrap_or_else(|| "team_selection.json".into());
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
    let (hermes_tx, mut hermes_rx) = tokio::sync::mpsc::channel::<HermesMsg>(8);
    let mut hermes_busy = false;
    let mut hermes_queue: std::collections::VecDeque<String> = std::collections::VecDeque::new();
    let mut confirm_team: Option<TeamProposal> = None;
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
                    if let Event::Key { key, action } = &event {
                        let mut handled = false;
                        if let Some(proposal) = confirm_team.take() {
                            if matches!((*key, *action), (Key::Confirm, KeyAction::Short)) {
                                let tmp_path = format!("{team_selection}.tmp");
                                match std::fs::write(&tmp_path, &proposal.file_json)
                                    .and_then(|()| std::fs::rename(&tmp_path, &team_selection))
                                {
                                    Ok(()) => {
                                        println!("VOICE_TEAM_SAVED\t{}", proposal.desc);
                                        voice_showing = false;
                                        if !updating {
                                            updating = true;
                                            let updater = updater.to_string();
                                            let tx = update_tx.clone();
                                            tokio::task::spawn_blocking(move || {
                                                let changed = run_updater(&updater);
                                                let _ = tx.blocking_send(changed);
                                            });
                                        }
                                        voice_refresh = true;
                                        match show_voice_text(&device, &voice_page, &voice_dir, "语音确认", "已保存").await {
                                            Ok(()) => {
                                                voice_showing = true;
                                                speak_text(&device, &voice_speak, &voice_dir, "已保存").await;
                                            }
                                            Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                                        }
                                    }
                                    Err(error) => {
                                        println!("VOICE_TEAM_SAVE_FAILED\t{error}");
                                        match show_voice_text(&device, &voice_page, &voice_dir, "语音确认", "保存失败，请再说一遍").await {
                                            Ok(()) => voice_showing = true,
                                            Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                                        }
                                    }
                                }
                                handled = true;
                            }
                        }
                        if !handled {
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
                    Event::Key { key: Key::Up, action: KeyAction::Long } |
                    Event::Key { key: Key::Down, action: KeyAction::Long } => {
                        let pages = scan_pages(&dir);
                        if clamp_current(&pages, &mut current) {
                            match show_page(&device, &dir, pages[current]).await {
                                Ok(png) => {
                                    last_shown = png;
                                    println!("PAGE\t{}\t{}\tkey=refresh", current + 1, pages[current].0);
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
                                                if let Some(item) = execute_pending(&device, &dir, &mut current, &mut last_shown, &voice_page, &voice_dir, &updater, &mut updating, &update_tx, &mut voice_showing, &mut voice_refresh, &voice_speak, &mut confirm_team, item).await {
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
                                confirm_team = None;
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
                                    confirm_team = None;
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
                                Ok(()) => {
                                    voice_showing = true;
                                    speak_text(&device, &voice_speak, &voice_dir, "已刷新").await;
                                }
                                Err(error) => println!("VOICE_PAGE_FAILED\t{error}"),
                            }
                        }
                    }
                }
            }
            hermes = hermes_rx.recv() => {
                if let Some(HermesMsg::Done { answer, asked }) = hermes {
                    hermes_busy = false;
                    let item = match answer {
                        Some(a) if !a.trim().is_empty() => Pending::Text { title: "语音回答".to_string(), text: a },
                        _ => match run_voice_fallback(&voice_answer, &asked) {
                            Some(fb) => Pending::Text { title: "语音回答".to_string(), text: fb },
                            None => Pending::Text { title: "语音识别".to_string(), text: format!("{asked}\n没听懂，请换个说法") },
                        },
                    };
                    pending_display.push_back(item);
                    if !recording {
                        if let Some(item) = pending_display.pop_front() {
                            if let Some(item) = execute_pending(&device, &dir, &mut current, &mut last_shown, &voice_page, &voice_dir, &updater, &mut updating, &update_tx, &mut voice_showing, &mut voice_refresh, &voice_speak, &mut confirm_team, item).await {
                                pending_display.push_front(item);
                            }
                        }
                    }
                    if let Some(next) = hermes_queue.pop_front() {
                        hermes_busy = true;
                        start_hermes(hermes_tx.clone(), &voice_hermes, &next);
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
                                        _ => match run_voice_team(&voice_team, &text) {
                                            TeamResult::Proposal { file_json, desc } => Pending::ConfirmTeam { file_json, desc },
                                            TeamResult::Unparsed => Pending::Text { title: "语音识别".to_string(), text: "没听清要改哪支队，请再说一遍".to_string() },
                                            TeamResult::NoSelection => Pending::Text { title: "语音识别".to_string(), text: "选队文件不存在，请先在选队网页选一次".to_string() },
                                            TeamResult::None => if hermes_busy {
                                                hermes_queue.push_back(text.clone());
                                                Pending::Text { title: "语音回答".to_string(), text: "正在查…".to_string() }
                                            } else {
                                                hermes_busy = true;
                                                start_hermes(hermes_tx.clone(), &voice_hermes, &text);
                                                Pending::Text { title: "语音回答".to_string(), text: "正在查…".to_string() }
                                            },
                                        },
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
                                if let Some(item) = execute_pending(&device, &dir, &mut current, &mut last_shown, &voice_page, &voice_dir, &updater, &mut updating, &update_tx, &mut voice_showing, &mut voice_refresh, &voice_speak, &mut confirm_team, item).await {
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

fn run_voice_fallback(script: &str, text: &str) -> Option<String> {
    match Command::new(script).arg(text).arg("fallback").output() {
        Ok(out) => {
            let answer = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if answer.is_empty() {
                None
            } else {
                Some(answer)
            }
        }
        Err(error) => {
            println!("VOICE_FALLBACK_FAILED\t{error}");
            None
        }
    }
}

fn run_voice_hermes(script: &str, text: &str) -> Option<String> {
    match Command::new(script).arg(text).output() {
        Ok(out) => {
            if !out.status.success() {
                println!("VOICE_HERMES_FAILED\tstatus={}", out.status);
                return None;
            }
            let answer = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if answer.is_empty() {
                None
            } else {
                Some(answer)
            }
        }
        Err(error) => {
            println!("VOICE_HERMES_FAILED\t{error}");
            None
        }
    }
}

fn run_voice_team(script: &str, text: &str) -> TeamResult {
    match Command::new(script).arg(text).output() {
        Ok(out) => {
            let line = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if line.is_empty() {
                TeamResult::None
            } else if line == "UNPARSED" {
                TeamResult::Unparsed
            } else if line == "NO_SELECTION" {
                TeamResult::NoSelection
            } else if let Some(rest) = line.strip_prefix("OK\t") {
                match rest.split_once('\t') {
                    Some((file_json, desc)) => TeamResult::Proposal {
                        file_json: file_json.to_string(),
                        desc: desc.to_string(),
                    },
                    None => {
                        println!("VOICE_TEAM_BAD_OUTPUT");
                        TeamResult::None
                    }
                }
            } else {
                println!("VOICE_TEAM_BAD_OUTPUT");
                TeamResult::None
            }
        }
        Err(error) => {
            println!("VOICE_TEAM_FAILED\t{error}");
            TeamResult::None
        }
    }
}

fn start_hermes(
    tx: tokio::sync::mpsc::Sender<HermesMsg>,
    script: &str,
    asked: &str,
) {
    let script = script.to_string();
    let asked = asked.to_string();
    tokio::task::spawn_blocking(move || {
        let answer = run_voice_hermes(&script, &asked);
        let _ = tx.blocking_send(HermesMsg::Done { answer, asked });
    });
}

async fn speak_text(
    device: &Device,
    voice_speak: &str,
    voice_dir: &std::path::Path,
    text: &str,
) {
    if text == "正在查…" || text.trim().is_empty() {
        return;
    }
    let pcm_path = voice_dir.join("speak.pcm");
    match Command::new(voice_speak).arg(text).arg(&pcm_path).status() {
        Ok(status) if status.success() => match std::fs::read(&pcm_path) {
            Ok(bytes) => {
                let samples: Vec<i16> = bytes
                    .chunks_exact(2)
                    .map(|c| i16::from_le_bytes([c[0], c[1]]))
                    .collect();
                if samples.is_empty() {
                    println!("VOICE_SPEAK_EMPTY");
                } else {
                    match device.play(&AudioClip { samples }, 80).await {
                        Ok(_) => println!("VOICE_SPEAK_DONE"),
                        Err(error) => println!("VOICE_SPEAK_FAILED\t{error}"),
                    }
                }
            }
            Err(error) => println!("VOICE_SPEAK_READ_FAILED\t{error}"),
        },
        Ok(status) => println!("VOICE_SPEAK_FAILED\tstatus={status}"),
        Err(error) => println!("VOICE_SPEAK_FAILED\t{error}"),
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
    voice_speak: &str,
    confirm_team: &mut Option<TeamProposal>,
    item: Pending,
) -> Option<Pending> {
    match item {
        Pending::Text { title, text } => {
            *confirm_team = None;
            match show_voice_text(device, voice_page, voice_dir, &title, &text).await {
                Ok(()) => {
                    *voice_showing = true;
                    speak_text(device, voice_speak, voice_dir, &text).await;
                    None
                }
                Err(error) => {
                    println!("VOICE_PAGE_FAILED\t{error}");
                    Some(Pending::Text { title, text })
                }
            }
        }
        Pending::Flip { step } => {
            *confirm_team = None;
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
            *confirm_team = None;
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
        Pending::ConfirmTeam { file_json, desc } => {
            match show_voice_text(device, voice_page, voice_dir, "语音确认", &desc).await {
                Ok(()) => {
                    *confirm_team = Some(TeamProposal { file_json, desc: desc.clone() });
                    *voice_showing = true;
                    speak_text(device, voice_speak, voice_dir, &desc).await;
                    None
                }
                Err(error) => {
                    println!("VOICE_PAGE_FAILED\t{error}");
                    Some(Pending::ConfirmTeam { file_json, desc })
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
