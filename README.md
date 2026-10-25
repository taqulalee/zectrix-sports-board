# 赛事看板 Pro（在制）

当前阶段：四页动态看板（未来赛程／最近赛果／焦点赛事／F1赛事）＋语音互动（长按录音→转写→问答＋播报），真机运行中。

## 墨水屏 SDK（必备前置，本仓不含 SDK 源码）

宿主通过官方 ZECTRIX 本地推送 SDK 连接设备，本仓不含 SDK 源码，首次检出需另行获取：

- SDK：ZECTRIX 本地推送 SDK（Rust，crate 名 `zectrix-sdk`），版本 0.1.0，对应固件 3.7.2；适用设备 ZECTRIX Note 4（400×300），不适用 Note 4C；尚未发布到 crates.io，只能源码路径引用。
- 下载地址：https://github.com/itopinion/zectrix-local-push-sdk.git（公开仓，可直接克隆）。
- 摆放位置：克隆到本仓库目录旁边（与本仓库目录同级，目录名保持 zectrix-local-push-sdk）；宿主 `02_在制成果/host/Cargo.toml` 用路径引用（`zectrix-sdk = { path = "../../../zectrix-local-push-sdk" }`）。
- 固件升级：用 Chrome 或 Edge 登录极趣云 https://cloud.zectrix.com/ ，进“本地升级”，USB 连接 Note 4 按官方说明刷写；官方说明见 https://wiki.zectrix.com/zh/software/firmware ；升级后在设备“设置 → 网络同步”启用“待机保持 Wi-Fi”。
- 构建要求：Rust stable、CMake、C/C++ 编译器（Linux 装 build-essential、cmake、pkg-config）；首次构建需联网下载依赖。

## 生成演示数据

```bash
python 02_在制成果/src/build_demo_data.py \
  01_当前有效资料/data_snapshots/20261023 \
  02_在制成果/data/demo_sports.json
```

## 渲染预览

```bash
python 02_在制成果/src/render_sports_v2.py \
  02_在制成果/data/demo_sports.json \
  02_在制成果/previews \
  --font /path/to/authorized/chinese-font.ttf \
  --track-geojson 01_当前有效资料/circuit_data/cota_us_2012.geojson \
  --f1-logo 03_项目固定资源/赛事标识/私人测试/F1官方Logo_用户提供截图提取.png
```

- `[KNOWN｜HIGH]` 输出是400×300、8-bit连续灰度PNG。
- `[KNOWN｜HIGH]` 不传`--now`时按Asia/Shanghai当前时间重新计算焦点赛事倒计时。
- `[KNOWN｜HIGH]` 本阶段不执行NOTE4推送。
- `[KNOWN｜HIGH]` 当前为私人测试项目，F1图形Logo来自用户提供截图，不用于公开发布。

## 真机按键与页面

宿主使用单一SDK连接管理Gray16页面，页数动态变化：

- 第 1 页未来赛程、第 2 页最近赛果、第 3 页焦点赛事（只放大赛，10 天内无大赛显示空态）、第 4 页F1赛事（仅当下一场正赛在滚动 7 天内才出现，否则只有 3 页）。
- 上键短按：上一页。
- 下键短按：下一页。
- 正面确认键短按：刷新当前页。
- 上下键长按：忽略。
- 确认键长按 1 秒：开始录音（语音互动，见下节）。

## 语音互动

- 确认键长按 1 秒开始录音（固件出“正在聆听”条，松开结束）；转写用 faster-whisper medium（CPU，常驻 worker），繁体转简体。
- 语音页（转写中／结果页）显示时，长按无法再录音，须先按任意键取消回正常页。
- 问答分流（按顺序）：本地简单问题（下一场／比分／德比时间／F1 时间／翻页／刷新）→ 选队变更（说“把 A 换成 B”先出确认页，确认键短按保存、其他键取消）→ 其余走 Hermes（20 秒封顶，失败回“没听懂”）。
- 进行中的比赛本地无数据（进行中两边都不进板），含“正在”＋“比赛／进行”的问题直接走 Hermes 现查。
- 播报：Edge 神经网络音色（zh-CN-XiaoxiaoNeural）主用，Piper（zh_CN-huayan-medium）兜底；Hermes 答案去网址（提示词要求不带网址，输出再剥离），屏幕与语音都不带网址。

## NUC动态更新

- NUC每10分钟抓取五联赛（英超／西甲／意甲／德甲／欧冠）与F1数据，按选队过滤。
- 各页均重新计算，但只有语义内容发生变化时才原子替换PNG。
- 当前可见页变化后自动Gray16刷新；隐藏页仅更新文件。
- 按键切页始终读取最新页面。
- 焦点倒计时跨整小时时发生语义变化并更新。
