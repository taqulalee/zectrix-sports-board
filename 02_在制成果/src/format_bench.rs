use std::collections::BTreeMap;
use std::time::{Duration, Instant};
use zectrix_sdk::{Device, Event, ImageFormat, OpenOptions};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let target = std::env::args().nth(1);
    let dir = std::env::args()
        .nth(2)
        .unwrap_or_else(|| "sports_v2_pages".into());
    let device = Device::open(OpenOptions {
        target,
        client_name: Some("Sports Dashboard format benchmark".into()),
    })
    .await?;
    println!("CONNECTED");

    let pages = [
        ("schedule", "01_schedule.png"),
        ("results", "02_results.png"),
        ("focus", "03_focus.png"),
    ];
    let modes = [
        ("mono", ImageFormat::Mono),
        ("gray4", ImageFormat::Gray4),
        ("gray16", ImageFormat::Gray16),
    ];
    let mut measurements: BTreeMap<&str, Vec<u128>> = BTreeMap::new();

    for (mode_name, mode) in modes {
        for (page_name, filename) in pages {
            let png = std::fs::read(format!("{dir}/{filename}"))?;
            let started = Instant::now();
            device.show_with_format(&png, mode).await?;
            let elapsed_ms = started.elapsed().as_millis();
            measurements.entry(mode_name).or_default().push(elapsed_ms);
            println!("DISPLAY\t{mode_name}\t{page_name}\t{elapsed_ms}");
            tokio::time::sleep(Duration::from_secs(8)).await;
        }
    }

    for (mode, values) in &measurements {
        let sum: u128 = values.iter().sum();
        println!(
            "SUMMARY\t{mode}\tcount={}\taverage_ms={}\tmin_ms={}\tmax_ms={}",
            values.len(),
            sum / values.len() as u128,
            values.iter().min().unwrap(),
            values.iter().max().unwrap()
        );
    }
    println!("HOLDING\tgray16\tfocus");

    loop {
        match tokio::time::timeout(Duration::from_secs(30), device.next_event()).await {
            Ok(Ok(Some(event))) => {
                println!("EVENT\t{event:?}");
                if matches!(event, Event::Disconnected { .. }) {
                    break;
                }
            }
            Ok(Ok(None)) => break,
            Ok(Err(error)) => {
                println!("EVENT_ERROR\t{error}");
                break;
            }
            Err(_) => println!("HEARTBEAT\tstill_holding"),
        }
    }
    device.close().await?;
    Ok(())
}
