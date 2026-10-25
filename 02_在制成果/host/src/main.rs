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
    loop {
        tokio::select! {
            event = device.next_event() => {
                let Some(event) = event? else { break; };
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
