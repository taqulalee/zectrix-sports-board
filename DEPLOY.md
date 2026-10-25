# 部署（克隆后可用）

一台 ZECTRIX Note 4 ＋ 一台 Linux 机器，按下面步骤可跑起一台墨水屏赛事看板。

## 前置

- 设备：ZECTRIX Note 4，固件 3.7.2（升级办法见 README 的 SDK 一节）。
- 机器：Linux，装好 Git、curl、Rust stable、CMake、C/C++ 编译器、Python 3.12。
- 网络：ESPN、Jolpica、OpenF1 数据源均免 Key，首次跑更新器需联网。

## 步骤

### 1. 克隆本仓与 SDK

```bash
git clone https://github.com/taqulalee/zectrix-sports-board.git
git clone https://github.com/itopinion/zectrix-local-push-sdk.git
```

SDK 与本仓库目录同级、目录名保持 `zectrix-local-push-sdk`（宿主 Cargo.toml 用相对路径引用它）。

### 2. Python 环境（仓库根）

```bash
cd zectrix-sports-board
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. 字体（自备，不随仓发布）

准备一个中文字体，放 `assets/zfull_GB.ttf`（或设 `FONT` 环境变量指向字体文件）。

提示：页面版式按 zfull_GB 量过，换字体可能导致队名截断或留白变化，需重核版式。

### 4. 选队

```bash
mkdir -p 02_在制成果/runtime
cp 02_在制成果/team_selection.example.json 02_在制成果/runtime/team_selection.json
```

或启动选队页自选（勾选即保存）：

```bash
cd 02_在制成果 && ../.venv/bin/python web/team_picker.py serve
```

浏览器打开 `http://<机器IP>:8790`。

### 5. 先跑一次更新器，生成页面

```bash
03_项目固定资源/bin/update_live.sh
```

页面生成在 `02_在制成果/runtime/pages/`。更新器默认用仓库根 `.venv` 的 Python，可用 `PYTHON` 环境变量覆盖；字体可用 `FONT` 环境变量覆盖。

### 6. 构建宿主

```bash
cd 02_在制成果/host && cargo build
```

首次构建需联网下载依赖。

### 7. 启动宿主

```bash
cd 02_在制成果/host
nohup ./target/debug/zectrix-sports-dashboard <设备IP> > ~/zectrix-sports-dashboard.log 2>&1 &
```

pages 目录与更新器脚本默认走仓库相对布局，不用另传。日志出现 `READY` 即已上屏焦点页；宿主此后每 600 秒自动跑更新器，数据变了才刷屏。

按键：上／下短按切页，确认短按重推当前页；确认键长按是固件录音（语音交互用）。

## 配置速查

- `PYTHON`：更新器用 Python，默认 `$ROOT/.venv/bin/python`。
- `FONT`：渲染用字体，默认 `$ROOT/assets/zfull_GB.ttf`。
- 选队页端口：8790。
- 关键路径：页面 `02_在制成果/runtime/pages/`，数据 `02_在制成果/runtime/sports.json`，选队 `02_在制成果/runtime/team_selection.json`。

## 常见问题

- Cargo 报找不到 `zectrix-sdk`：SDK 没放在仓库目录同级，或目录名不对。
- 更新器报找不到 Python：先在仓库根建 `.venv` 并装依赖；或设 `PYTHON` 指向可用 Python。
- 更新器报找不到字体：按第 3 步放好字体，或设 `FONT`。
- 选队缺失不报错：build 不筛选，五联赛全量上板；先补选队（第 4 步）。
- 设备报 Busy：宿主独占设备连接，勿双开宿主。
- 推送结果未知不自动重发：按确认键重推当前页。

## 安全说明

- 本仓不含密钥与账号；数据源均免 Key。
- F1 徽标为用户截图提取（`03_项目固定资源/赛事标识/私人测试/`），随仓仅作部署配套。
