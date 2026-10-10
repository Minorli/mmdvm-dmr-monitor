# MMDVM DMR Monitor / Starwave 星台

[English quick start](README.en.md) · [部署与运维](docs/deployment.md) · [架构与恢复](docs/architecture.md) · [面板操作](docs/ui.md)

Starwave 是一个 DMR 通话监听与记录工具。它通过 SSH 在 Pi-Star 上旁路采集 MMDVM 网络数据，在服务端分别处理两个时隙，提供音频回放、自动转录、中文翻译和通联记录。界面采用 Arco Design，围绕 TS、TG、呼号、NET/RX、时长和链路状态组织信息。

项目仓库：[Minorli/mmdvm-dmr-monitor](https://github.com/Minorli/mmdvm-dmr-monitor)。电台连接及翻译服务均在部署后配置，源码不包含部署者的接口地址、密钥或通话数据。

## 监听控制

首次部署只启动 Web，处于待机，不采集通话、不加载语音模型。用户点击 **开始监听** 后才连接电台并处理语音；点击 **停止监听** 会断开采集、结束识别子进程、暂停任务，已有录音与文字继续可读。

用户的选择会持久保存：开始后，异常重启自动恢复；手动停止后，重启保持停止。网络断线自动重连，后台任务异常退出由 Docker 或 systemd 拉起。不要同时用两种方式运行同一 DATA_DIR。

## Docker 快速启动

需要 Docker Engine 和 Compose 插件。无需在宿主机安装 Python、Node.js 或声码器依赖。

```sh
cp .env.example .env
# 按需编辑资源参数；设备和翻译接口可在网页配置。
docker compose up -d --build
```

默认打开 `http://127.0.0.1:8788`。基础配置不要求任何密钥，Web 可先待机运行。打开「连接设置」填写设备地址、SSH 端口、用户和密码，即可使用密码登录，无需先配置 SSH 免密。首次连接要核对设备指纹，保存后点击「开始监听」。默认端口仅发布到回环地址。

已有密钥登录也可以继续使用部署时提供的 SSH 身份和经过验证的 known_hosts 文件：

```sh
# .env 中设置 SSH_KEY_PATH、SSH_KNOWN_HOSTS_PATH；文件权限匹配 APP_UID。
docker compose -f compose.yaml -f deploy/compose.ssh.yaml up -d --build
```

停止监听后，打开「翻译设置」填写接口地址、模型、Responses 或 Chat Completions 模式和 API 密钥，也可关闭翻译。密钥只写入后台，页面只显示是否已配置；更换提供方后启用翻译需要提供新密钥，关闭翻译则可清除旧密钥。保存的设置在容器重建后保留。没有启用翻译时，仍可监听、录音和转录。

环境变量和 `.env` 可提供初始默认值，网页保存的设置优先。已有部署也可用 `deploy/compose.api-key.yaml` 和 `OPENAI_API_KEY_PATH` 挂载单个只读密钥文件；控制凭据可使用 `deploy/compose.control-token.yaml`。完整配置、宿主机旧数据迁移、资源限制与 HTTPS 反代见 [部署指南](docs/deployment.md)。

首次开始监听可能需要下载语音模型；模型缓存会留在独立卷中。默认 CPU 模型为多语种 `small`。根据机器资源设置 `WHISPER_THREADS`、`TRANSCRIPTION_WORKERS` 和 `APP_CPUS`，无需通过丢掉录音来控制积压。

## 数据与恢复

- `/data` 持久化 SQLite、任务状态、用户监听选择、录音、呼号缓存及网页连接/翻译配置和私密凭据；`/models` 持久化模型缓存。完整数据备份包含网页保存的 SSH 密码和 API 密钥，应作为私有数据保管。删除容器不会删除这些卷，`docker compose down -v` 会删除命名卷，应仅在明确要清空数据时使用。
- 录音路径为 `audio/yyyymmdd/hh-mi-ss_TSx_DMRID_callsign.wav`，默认北京时间。未查询到呼号使用 `unknown`；同名追加序号。网页回放仍按通话 UUID 定位。
- 严格不足一秒的短发话不显示、不送识别。一秒及以上保留。长通话按片段处理，相邻片段保留一秒上下文；语音检测未发现讲话时隐藏，检测到讲话但识别为空时保留录音卡片。
- 当前录音每秒保存原子快照。重启会恢复可用片段、任务和阶段；最近尚未同步的一小段音频仍可能丢失。已完成录音写入并同步后才发布文件名。
- SQLite 是持久队列，任务不会因超过内存队列容量而被丢掉。当前通话优先于历史补处理，界面分别显示积压。重试有次数限制，失败后保留录音和已完成文字。
- `AUDIO_RETENTION_DAYS=0` 默认长期保留。显式配置天数后，仅过期的已完成音频会清理，文字保留，待处理任务的录音不删除。

### 备份、恢复和检查

完整备份与维护命令需要先停止应用，以保持文件与数据库一致；不是只在面板上停止监听。使用命名卷时可运行：

```sh
docker compose stop
# 为备份选择一个新的目录；命令不覆盖现有备份。
docker compose run --rm --no-deps starwave python -m backend.maintenance backup --destination /data/backups/manual-01
docker compose up -d
```

`audit` 校验数据库并报告缺失、未引用录音；`retry-failed --stage translation` 重置终止失败的任务；`prune-audio --days 7` 是显式音频清理。恢复使用 `restore --source`，只接受空 DATA_DIR，逐文件验证清单和 SHA256，不覆盖既有数据。外部备份存储与具体命令见部署指南。

## 本机开发与守护

支持 Linux，建议 Python 3.12、uv、Node.js 22+、Git 和 C11 编译器。

```sh
uv sync --frozen
./scripts/build-codec.sh
npm --prefix web ci
npm --prefix web run build
cp .env.example .env
./scripts/start.sh
```

前台运行后，在页面上开始监听。长期本机部署可用 `./scripts/install-service.sh` 安装并启用用户服务，再执行 `systemctl --user restart pi-star-record.service`；凭据只由后台配置读取。安装参数和 systemd 操作见部署指南。

## 验证与贡献

```sh
./scripts/check.sh
./scripts/check.sh --docker
```

检查包含 Python lint、任务/存储/控制/录音恢复测试、前端测试与类型构建。Docker 烟测验证非 root、只读运行、解码器、SQLite 版本、默认待机、停止和重建后的卷持久化；另用独立内部网络和临时 SSH 服务验证密码认证、指纹确认及连接配置持久化。CI 使用同样的检查。

贡献时不要使用真实服务、真实密钥或现场通话作为测试夹具。请遵循 [贡献指南](CONTRIBUTING.md) 和 [安全说明](SECURITY.md)。

## 范围与许可

采集端目前支持 Linux AF_PACKET、IPv4 Homebrew DMRD UDP 62031；通过已有 SSH 权限运行只读采集脚本。它不改变 Pi-Star 无线配置、不向网络发起发话或订阅额外 TG。当前输入涵盖经过该 Pi-Star 的网络通话及转发到网络的本地 RF。

自动文字是工具输出，音质、口音、误码和短发话会影响识别，基础设施健康不代表文字准确。软件声码器不支持解密加密语音。

应用代码采用 [MIT](LICENSE)。声码器、字体、SQLite 和依赖保留各自许可，见 [第三方说明](NOTICE.md) 与 [native/README.md](native/README.md)。仓库和镜像不包含现场录音、转录数据或凭据。
