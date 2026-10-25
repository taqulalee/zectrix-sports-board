use zectrix_sdk::{Device, Event, ImageFormat, Key, KeyAction, OpenOptions};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let target = std::env::args().nth(1);
    let dir = std::env::args()
        .nth(2)
        .unwrap_or_else(|| "sports_v2_pages".into());
    let pages = [
        ("schedule", "01_schedule.png"),
        ("results", "02_results_logo.png"),
        ("focus", "03_focus_countdown.png"),
    ];
    let device = Device::open(OpenOptions {
        target,
        client_name: Some("Sports Dashboard Pro".into()),
    })
    .await?;

    let mut current = 2usize;
    show_page(&device, &dir, pages[current]).await?;
    println!("READY\tpage={}\tname={}", current + 1, pages[current].0);

    while let Some(event) = device.next_event().await? {
        match event {
            Event::Key {
                key: Key::Up,
                action: KeyAction::Short,
            } => {
                current = (current + pages.len() - 1) % pages.len();
                show_page(&device, &dir, pages[current]).await?;
                println!("PAGE\t{}\t{}\tkey=up", current + 1, pages[current].0);
            }
            Event::Key {
                key: Key::Down,
                action: KeyAction::Short,
            } => {
                current = (current + 1) % pages.len();
                show_page(&device, &dir, pages[current]).await?;
                println!("PAGE\t{}\t{}\tkey=down", current + 1, pages[current].0);
            }
            Event::Key {
                key: Key::Confirm,
                action: KeyAction::Short,
            } => {
                show_page(&device, &dir, pages[current]).await?;
                println!("PAGE\t{}\t{}\tkey=confirm", current + 1, pages[current].0);
            }
            Event::Key { key, action } => {
                println!("IGNORED_KEY\t{key:?}\t{action:?}");
            }
            Event::Disconnected { reason } => {
                println!("DISCONNECTED\t{reason:?}");
                break;
            }
            other => println!("EVENT\t{other:?}"),
        }
    }
    device.close().await?;
    Ok(())
}

async fn show_page(
    device: &Device,
    dir: &str,
    page: (&str, &str),
) -> Result<(), Box<dyn std::error::Error>> {
    let png = std::fs::read(format!("{dir}/{}", page.1))?;
    device.show_with_format(&png, ImageFormat::Gray16).await?;
    Ok(())
}
