# 部署与维护

首次启动只提供网页并处于停止监听状态。打开页面，点击“开始监听”才会
连接 Pi-Star 并加载语音模型；点击“停止监听”会结束采集并保存已收到的录音。
应用将最后一次监听选择保存在 SQLite：手动开始后异常退出或重启会恢复监听，
手动停止后重启继续待机。首次安装不会自动开始监听。

## Docker Compose

需要 Docker Engine 和 Compose v2。当前镜像在 Linux amd64 上验证；解码和
本地语音识别应放在有足够内存的主机，Pi-Star 只运行通过 SSH 临时启动的采集器。

新建部署可以先运行 `docker compose up -d --build`，无需配置任何凭据即可打开
`http://127.0.0.1:8788` 的待机网页。密码登录可直接在页面「连接设置」填写设备
地址、SSH 端口、用户名和密码，不需要挂载 SSH 身份密钥。支持先测试连接，再保存。
首次连接或设备主机密钥改变时，页面显示 SHA256 指纹；核对后确认信任，再测试或保存。
主机密钥扫描本身不证明设备身份，应通过设备或已有可信记录核对指纹。
参见 [OpenSSH 主机密钥扫描说明](https://man.openbsd.org/ssh-keyscan)。

网页连接设置在停止监听时修改，保存后立即用于下一次监听，不需要重建容器。
修改设备地址、端口、用户或登录方式后，不能空着密码复用旧目标的凭据。
同一目标再次编辑时，密码留空会保留已保存的密码；接口只返回是否已保存，不返回密码。
测试连接使用短脚本验证 SSH、Python、免密 `sudo` 和原始套接字权限，不采集通话。
SSH 密码登录不意味着允许带密码运行 `sudo`；目标账户仍需能执行 `sudo -n python3`。

使用密钥登录时，再配置以下 SSH 文件和环境。

为应用准备一把专用 SSH 密钥和一个只含目标设备可信主机密钥的 known_hosts
文件。容器只读取这两个文件，不挂载用户的整个 `~/.ssh`。目标账户需支持
密钥登录并允许 `sudo -n python3` 执行旁路采集。通过可信方式核对设备 SSH
主机指纹后再创建 known_hosts，保持严格主机密钥校验。

在项目的私有 `.env` 中设置部署参数，例如：

```dotenv
PI_STAR_HOST=pi-star@pi-star.local
COMPOSE_FILE=compose.yaml:deploy/compose.ssh.yaml
SSH_KEY_PATH=./secrets/pi_ssh_key
SSH_KNOWN_HOSTS_PATH=./secrets/known_hosts
APP_UID=1000
APP_GID=1000
HOST_BIND=127.0.0.1
PORT=8788
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
OPENAI_BASE_URL=https://api.openai.com/v1
TRANSLATION_MODEL=gpt-4.1-mini
```

SSH 私钥应设为 `0600`，所属 UID 与 `APP_UID` 一致；known_hosts 至少需能被该
UID 读取。文件来自 Compose secret 时，文件原有的权限和所有者仍然生效，
不能靠 secret 的 `uid`/`mode` 属性修改它们。
参见 [Docker 的 secret 文件权限说明](https://docs.docker.com/reference/compose-file/services/#secrets)。

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 -f starwave
```

打开 `http://127.0.0.1:8788`，确认网页已就绪后点击“开始监听”。未配置
`PI_STAR_HOST` 或可用 SSH 凭据时，网页仍可以待机，开始监听会报告配置问题。
镜像默认启用 `REQUIRE_EXPLICIT_SSH=true`，容器必须提供身份密钥和可信主机密钥，
或使用网页保存的密码和已确认的主机密钥；仅设置主机名时，“开始监听”会拒绝请求并继续待机，模型不会
提前下载。本机运行默认仍允许用户既有的 SSH 配置或 agent 身份。

网页配置优先于环境中的默认电台地址。设置保存为 `/data/connection.json`，密码和
主机密钥存于 `/data/.connection/` 的私有目录中，文件权限为 `0600`；不进入镜像，
也不返回给网页。项目的完整数据备份包含当前连接配置和这份密码，因此备份需要
作为私有数据保存。部署挂载的 SSH 身份密钥和 API 密钥仍应独立备份。
首次开始监听需要联网下载模型；模型下载到持久化缓存中，镜像构建不下载模型。

默认只将端口发布到主机 `127.0.0.1`。需要局域网访问时，在部署配置中设置
`HOST_BIND` 为主机局域网地址或 `0.0.0.0`。通过外网提供服务时，应在有 HTTPS
和访问控制的反向代理后运行，并配置下述控制令牌。控制令牌保护启动、停止等
控制请求；录音和文本的访问权限应由代理统一管理。

反向代理应保留原始 `Host`，传递正确的 `X-Forwarded-Proto`，并将代理实际源
IP 或 CIDR 配置到 `FORWARDED_ALLOW_IPS`，使 HTTPS 下的同源控制校验正常工作。
该值默认仅信任 `127.0.0.1`；Docker 中的宿主机代理或另一容器可能使用不同的
源地址，应填写对应可信地址，并限制后端端口的直接访问。

### 网页翻译设置

停止监听后，在「翻译设置」填写提供方的接口地址、模型和 API 密钥，选择 `responses` 或
`chat_completions`，并设置是否启用翻译。保存后无需重建镜像；关闭翻译仍可使用
监听、录音和转录。环境变量及项目 `.env` 提供初始默认值，网页保存的设置优先。

API 密钥只写入服务端，网页不会读取已有密钥。同一接口地址重新编辑时留空可保留
密钥，修改模型或 API 模式也可保留。更换提供方后启用翻译必须提供新密钥；禁用
翻译时可清除旧密钥，不会回退复用环境中的凭据。保存元数据位于 `/data/translation.json`，密钥位于
`/data/.translation/<uuid>/api_key`，文件权限为 `0600`。这两部分都随 `/data`
持久化，完整数据备份包含网页保存的 API 密钥，应作为私有数据保存。

`GET /api/translation-settings` 只返回 `base_url`、`model`、`api_mode`、`enabled`
和 `api_key_configured`。`PUT /api/translation-settings` 接受 `base_url`、`model`、
`api_mode`、`enabled` 及只写的 `api_key`，可用 `clear_api_key: true` 显式清除密钥；
启用翻译而没有可用密钥时拒绝保存。修改使用与监听控制相同的同源请求头和可选
控制令牌，且要求停止监听。填写接口地址时不要把密钥写入 URL。

### 用文件提供 API 密钥与控制令牌

`OPENAI_API_KEY` 可作为尚未保存网页翻译配置时的初始密钥；`CONTROL_TOKEN` 用于
保护网页控制和配置操作。这两个环境变量可用于简单部署。需要避免将它们
保存在容器环境变量中时，使用可选的 secret 配置。例如，先在私有 `.env` 中
填入文件路径，文件内容不应提交到仓库：

```dotenv
OPENAI_API_KEY_PATH=./secrets/openai_api_key
CONTROL_TOKEN_PATH=./secrets/control_token
```

```sh
docker compose -f compose.yaml -f deploy/compose.ssh.yaml -f deploy/compose.api-key.yaml \
  -f deploy/compose.control-token.yaml up -d --build
```

启动监听的完整命令还应包含 SSH 配置，或将启用的配置文件列表记录在私有 `.env`
中，例如 Linux 上 `COMPOSE_FILE=compose.yaml:deploy/compose.ssh.yaml:deploy/compose.api-key.yaml:deploy/compose.control-token.yaml`。
后续更新使用同一份配置，保持凭据挂载不变。

两个密钥/令牌可选文件可以分别使用。凭据文件也需由 `APP_UID` 读取。API 密钥在后台
读取，网页不会得到它；页面控制操作需要用户提供配置的控制令牌。不要把
API 密钥写进 Dockerfile、镜像构建参数或前端代码。

同源控制 API 为 `POST /api/listening/start` 和 `POST /api/listening/stop`，请求体
为 `{}`，需要 `X-Starwave-Request: 1`。配置控制令牌后还需
`Authorization: Bearer <CONTROL_TOKEN>`。`GET /api/status` 的 `listening_state`
表示停止、启动中、运行、停止中或错误，`control_requires_token` 表示是否需要令牌。

### 持久化、资源和更新

| 容器位置 | 默认 Compose 卷 | 内容 |
|---|---|---|
| `/data` | `starwave_recordings` | SQLite、录音、RadioID 缓存、网页配置/私密凭据及数据备份 |
| `/models/cache` | `starwave_models` 下的 `cache/` | 语音模型和下载缓存 |
| `/run/secrets/pi_ssh_key` | 单个只读文件 | 专用 SSH 身份密钥 |
| `/run/secrets/pi_known_hosts` | 单个只读文件 | 已核对的 Pi-Star 主机密钥 |

卷名称随 Compose 项目名变化。`docker compose down` 保留卷，后续 `up` 可以
使用原数据；重建镜像不会删除录音。删除卷会删除数据，更新时不要使用 `down -v`。
备份应覆盖完整 `/data`：先停止监听，再停止应用或使用项目的数据库备份功能，
保持 SQLite 与录音文件的一致性。模型缓存可以重新下载，凭据文件应独立管理。

容器以 UID/GID `1000:1000` 运行。`APP_UID`、`APP_GID` 同时控制构建的账户和
Compose 运行身份；修改后需重新构建，并调整已有卷或绑定目录的所有权。
新建命名卷会取得镜像内目录的所有权。绑定已有数据目录时，操作员应提前设置
适当权限，入口脚本会在目录不可写时给出错误，不会以 root 自动修改历史数据。

默认限制为 4 个 CPU、4 GiB 内存、256 个进程；两个 ASR 进程各使用 2 个线程。
Docker 日志按 10 MiB × 3 份
轮换。可通过 `APP_CPUS`、`APP_MEMORY` 调整。容器文件系统只读，可写空间只有
数据卷、模型卷及临时目录；不需要 `privileged`、主机网络或额外 Linux 权限。
AF_PACKET 采集在 Pi-Star 上通过 SSH 执行，容器不在本机开原始抓包套接字。

更新时先停止监听，保留数据卷和凭据，再运行 `docker compose up -d --build`。
确认 `/api/health` 和网页正常后重新开始监听。Docker 使用 `unless-stopped`
恢复异常退出，`init: true` 处理子进程回收，停止宽限为 60 秒。
健康检查验证应用就绪；Pi-Star 暂时离线会显示降级，不会为了 SSH 断连重启容器。

### 绑定已有数据和模型缓存

已有本机部署可以叠加 `deploy/compose.bind.yaml`，直接复用目录。它按挂载目标
替换默认的两个命名卷，将宿主数据目录绑定到 `/data`，原 faster-whisper 下载
目录绑定到 `/models`，并设置 `CACHE_DIR=/models`。已有模型目录无需移动到
`cache/` 子目录。两个源目录必须使用绝对路径并提前存在，Compose 不自动创建。
目录及其内容应由 `APP_UID`/`APP_GID` 读写；入口会拒绝相对路径和不可写目录。

可以将实际部署路径写进仓库外的私有配置，如
`~/.config/pi-star-record/compose.local.env`，并设为 `0600`。示例中的 `/srv/` 路径
需要替换为操作员现有目录；API 密钥文件只包含密钥值，不是完整的 dotenv 文件：

```dotenv
HOST_DATA_DIR=/srv/starwave/data
HOST_MODEL_CACHE_DIR=/srv/starwave/whisper
SSH_KEY_PATH=/srv/starwave/secrets/pi_ssh_key
SSH_KNOWN_HOSTS_PATH=/srv/starwave/secrets/known_hosts
OPENAI_API_KEY_PATH=/srv/starwave/secrets/openai_api_key
PI_STAR_HOST=pi-star@pi-star.local
APP_UID=1000
APP_GID=1000
```

迁移时先保存最后的停止选择，并停止旧进程。已有控制按钮时，先在页面停止
监听；旧版没有控制按钮时，新版未保存过监听选择的数据库会保持首次待机。
由操作员执行以下服务切换和离线备份，避免两份进程同时写同一份数据：

```sh
systemctl --user stop pi-star-record.service
systemctl --user disable pi-star-record.service
uv run --frozen python -m backend.maintenance --data-dir /srv/starwave/data audit
uv run --frozen python -m backend.maintenance --data-dir /srv/starwave/data \
  backup --destination /srv/starwave/backups/pre-docker
starwave_config="$HOME/.config/pi-star-record/compose.local.env"
docker compose --env-file "$starwave_config" -f compose.yaml \
  -f deploy/compose.bind.yaml -f deploy/compose.ssh.yaml \
  -f deploy/compose.api-key.yaml up -d --build
```

备份目的目录应尚未存在，备份工具会验证数据库和录音并保存校验清单。配置控制
令牌时再叠加对应配置文件。启动后先确认历史录音、健康检查与待机状态，再由
用户点击“开始监听”。后续更新继续使用相同的私有配置和配置文件列表。

## 本机运行与 systemd 用户服务

需要 Python 3.11+、uv、Node.js 22、Git 和 C 编译器。首次安装：

```sh
uv sync --locked
./scripts/build-codec.sh
npm --prefix web ci
npm --prefix web run build
cp .env.example .env
chmod 600 .env
./scripts/start.sh
```

本机 `DATA_DIR` 默认指向项目 `data/`，`CACHE_DIR` 默认指向项目 `.cache/whisper/`。
SSH 文件路径使用 `SSH_KEY_FILE`、`SSH_KNOWN_HOSTS_FILE`；文件式后台密钥使用
`OPENAI_API_KEY_FILE`、`CONTROL_TOKEN_FILE`。前台启动也遵循持久化的监听选择。
`start.sh` 直接使用已创建的 `.venv`，缺少依赖环境、声码器或前端构建时会退出并
给出构建提示；启动服务不会临时安装依赖。

```sh
# 只输出按当前 checkout 路径渲染的 unit，可先审阅。
./scripts/install-service.sh --print
# 安装并 enable；不会停止、启动或重启已经运行的服务。
./scripts/install-service.sh
# 操作员准备好后再执行。
systemctl --user restart pi-star-record.service
systemctl --user status pi-star-record.service
journalctl --user -u pi-star-record.service -f
```

服务路径从项目目录生成，支持空格，无固定用户名或安装目录。默认异常重启间隔
5 秒、停止宽限 60 秒、内存上限 4 GiB、进程上限 256。需调整资源时，使用
`systemctl --user edit pi-star-record.service` 添加覆盖配置。注销后持续运行需要
由操作员启用 `loginctl enable-linger "$USER"`。移动 checkout 后应重新安装 unit。
同一份数据不要同时交给前台进程、用户服务和 Docker 写入。

## 构建与验证

```sh
./scripts/check.sh
# 另行构建镜像并在无网络、无真实凭据的隔离容器里测试持久化。
./scripts/check.sh --docker
```

检查包括后端测试、固定 SHA 的 native 构建、前端测试和生产构建、Shell 检查、
Compose 配置。镜像测试验证普通用户运行、声码器可用、私密主机文件未打包、
无网络待机健康检查、SQLite/录音/缓存跨容器重建保存及正常停止。服务安装测试
对所有 `systemctl` 操作使用测试桩，不操作当前用户服务。

Docker 的构建上下文采用文件白名单，默认排除 `data/`、`.cache/`、`.env*`、
SSH/API 凭据、数据库、录音、截图、日志和 Git 元数据。Git 忽略规则也排除私密
配置及凭据目录；提交前仍应检查暂存文件，避免复制现场素材到源码目录。
前端使用 `package-lock.json`
配合 `npm ci`，后端使用 `uv.lock` 配合 `uv sync --locked`，mbelib 固定提交版本。
基础镜像标签和系统软件包需随安全更新维护；锁文件确保应用依赖版本一致，
不表示整个镜像字节永远不变。

镜像还从 SQLite 官方源码构建固定版本 `3.53.1`，同时验证归档 SHA256 和官方
发布的 `sqlite3.c` SHA3-256；Python 运行时明确链接这份共享库，避免依赖旧系统
SQLite 对 [WAL-reset 问题](https://www.sqlite.org/wal.html#walreset) 的未知回补状态。
版本来源、校验值和 SQLite 的公共领域声明随镜像保存在 `/app/SQLITE_PROVENANCE.txt`。
SQLite 不改变项目采用的 MIT 许可或 mbelib 的 ISC 许可。
