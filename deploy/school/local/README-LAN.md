# 在 Linux 上启动测试环境（含 3D 点云模块）

适用于局域网测试，或在一台服务器上临时演示 `dev_3d` 分支的功能（3D 框、连续帧跟踪、语义分割、车道线等）。

- 数据库用 SQLite，文件存在本地，服务用 Django 开发服务器，**只用于测试**。
- 生产部署按 [`deploy/school/README.md`](../README.md)：PostgreSQL + MinIO + gunicorn + nginx。
- 一键脚本：[`run-lan.sh`](run-lan.sh)。下文是它的用法，以及小内存机器上分步执行的方法。

---

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| 系统 | Debian 12 / Ubuntu 22.04 及以上（其他发行版需自行安装同等软件包） |
| Python | 3.10–3.12，带 `venv` |
| Node.js | **20 以上（推荐 22）**，部分依赖不支持 18；以及 yarn 1（`sudo npm install -g yarn@1.22.22`，脚本缺少时会自动安装） |
| 内存 | 构建前端约需 8 GB（内存不足时用交换空间补足，见第 4 节）；运行时约 1 GB |
| 磁盘 | 约 6 GB（Python 依赖约 1 GB，`web/node_modules` 约 3 GB） |
| 端口 | 8080（Label Studio）、9090（3D 模型服务） |

安装系统软件：

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv python3-pip python3-dev build-essential libpq-dev net-tools xz-utils
# Node.js 22（系统自带的 nodejs 版本通常太旧）。二选一：
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash - && sudo apt-get install -y nodejs
# 或使用官方二进制包，装到 /opt/node22，使用前把它加到 PATH 前面：
#   curl -fsSLO https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-x64.tar.xz
#   sudo mkdir -p /opt/node22 && sudo tar -xJf node-v22.11.0-linux-x64.tar.xz -C /opt/node22 --strip-components=1
#   export PATH=/opt/node22/bin:$PATH
node -v     # v20 以上
sudo npm install -g yarn@1.22.22
```

## 2. 获取代码

```bash
git clone -b dev_3d https://github.com/weichampion/label_studio_edu.git
cd label_studio_edu
```

已有代码时更新：

```bash
git fetch origin && git checkout dev_3d && git pull
```

## 3. 一键启动（内存 8 GB 以上的机器）

```bash
SEED=1 bash deploy/school/local/run-lan.sh      # 第一次：装依赖、构建前端、建库、创建演示账号
bash deploy/school/local/run-lan.sh             # 之后再次启动
REBUILD=1 bash deploy/school/local/run-lan.sh   # 拉取新代码后：重新构建前端再启动
```

脚本依次完成：

1. 在仓库旁边创建 `.venv`，安装同版本 label-studio wheel 的依赖，然后让 venv 导入本仓库的代码。
2. `web/dist` 不存在（或 `REBUILD=1`）时，用 yarn 按 `yarn.lock` 安装前端依赖并构建。
3. 执行 `migrate`、`collectstatic`；`SEED=1` 时创建演示学院和账号。
4. 后台启动 3D 模型服务（`:9090`，日志 `<数据目录>/tracker.log`），前台启动 Label Studio（`0.0.0.0:8080`）。

可用的环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VENV` | `<仓库上级目录>/.venv` | Python 虚拟环境 |
| `DATA_DIR` | `<仓库上级目录>/local-data` | 数据库、上传文件、模型、日志 |
| `PORT` / `TRACKER_PORT` | `8080` / `9090` | 端口 |
| `LAN_IP` | 本机第一个 IP | 打印的访问地址，以及 `CSRF_TRUSTED_ORIGINS` |
| `SEED` | `0` | `1`：创建演示账号（默认密码 `Demo@2026`） |
| `REBUILD` | `0` | `1`：重新构建前端 |
| `TRACKER` | `1` | `0`：不启动 3D 模型服务 |
| `PYTHON` | `python3` | 用来创建 venv 的解释器，如 `python3.12` |
| `LS_VERSION` | `1.23.2` | 依赖来源的 label-studio 版本，与本仓库对应，一般不改 |

## 4. 小内存机器：分步执行

内存不足 8 GB 时，一键脚本中的前端构建会因内存不够失败。按下面的步骤分开执行，每一步失败可以单独重试。
示例中的路径与 `run-lan.sh` 的默认值一致：仓库在 `/app/llm/label_studio_edu`，venv 在 `/app/llm/.venv`，数据在 `/app/llm/local-data`，按实际情况替换。

### 4.1 交换空间（内存不足 8 GB 时）

```bash
sudo fallocate -l 8G /swapfile2 && sudo chmod 600 /swapfile2 && sudo mkswap /swapfile2
sudo swapon /swapfile2
free -m                                   # Swap 一行应有约 8 GB
```

重启后需要重新执行 `sudo swapon /swapfile2`。要开机自动启用，在 `/etc/fstab` 中加一行 `/swapfile2 none swap sw 0 0`。

### 4.2 Python 依赖

不需要单独执行：第 4.6 节运行 `run-lan.sh` 时，如果 venv 中还没有 Django，会自动安装（约 1 GB，十几分钟）。
想先确认依赖是否就绪：

```bash
/app/llm/.venv/bin/python -c "import django, label_studio; print(django.get_version(), label_studio.__file__)"
```

输出中的路径应指向本仓库的 `label_studio/__init__.py`。

### 4.3 前端依赖

耗时较长，建议在 `screen` 或 `tmux` 中执行：

```bash
cd /app/llm/label_studio_edu/web
rm -rf node_modules                      # 上一次没装完、或之前用 npm 装过时，必须先删除
command -v yarn || sudo npm install -g yarn@1.22.22
NODE_OPTIONS=--max-old-space-size=4096 yarn install --frozen-lockfile --ignore-scripts \
  --network-timeout 600000 --network-concurrency 4
ls node_modules/nx/bin/nx.js node_modules/three/package.json   # 两个文件都在才算装好
```

- **必须用 yarn 并带 `--frozen-lockfile`**：`yarn.lock` 把所有包要求的 webpack 统一锁定为同一个版本。用 npm 安装时会多出一份 webpack，
  构建会失败，报错 `Module not found: TypeError: Cannot read properties of undefined (reading 'tap')`。
- `NODE_OPTIONS=--max-old-space-size=4096`：Node 默认按物理内存决定堆大小，不会用到交换空间，小内存机器上会报 `JavaScript heap out of memory`。
- `--network-timeout` 和 `--network-concurrency`：机器频繁换页时，下载容易超时。
- 国内网络慢时加 `--registry https://registry.npmmirror.com`。

### 4.4 构建前端

```bash
cd /app/llm/label_studio_edu/web
NODE_OPTIONS=--max-old-space-size=6144 NX_DAEMON=false BUILD_NO_SERVER=true \
  node node_modules/nx/bin/nx.js build labelstudio --configuration=development
ls dist/apps/labelstudio                  # 有输出即构建成功
```

内存只有 1 GB 时，靠交换空间可能要构建一个小时以上。也可以在内存充足的电脑上用同一份代码构建，再把 `web/dist` 整个目录拷到服务器同样的位置。

### 4.5 数据库与演示账号（第一次）

```bash
cd /app/llm/label_studio_edu/label_studio
export LABEL_STUDIO_BASE_DATA_DIR=/app/llm/local-data DJANGO_SETTINGS_MODULE=core.settings.school_local PYTHONIOENCODING=utf-8
mkdir -p /app/llm/local-data
/app/llm/.venv/bin/python manage.py migrate --noinput
/app/llm/.venv/bin/python manage.py school_seed_demo --password '<自己设定的密码>'
```

不指定 `--password` 时，演示密码为公开的 `Demo@2026`。**机器能从公网访问时，必须设置自己的密码**，可以把它存到 `local-data` 里，例如：

```bash
PW=$(python3 -c "import secrets,string;a=string.ascii_letters+string.digits;print('Ls3d-'+''.join(secrets.choice(a) for _ in range(12)))")
echo "$PW" > /app/llm/local-data/demo_password.txt && chmod 600 /app/llm/local-data/demo_password.txt
/app/llm/.venv/bin/python manage.py school_seed_demo --password "$PW"
```

### 4.6 启动

```bash
cd /app/llm/label_studio_edu
VENV=/app/llm/.venv DATA_DIR=/app/llm/local-data LAN_IP=<本机 IP> bash deploy/school/local/run-lan.sh
```

已完成的步骤会被跳过：依赖已装好，`web/dist` 已存在，账号已创建。脚本只执行 `migrate`、`collectstatic`，然后启动两个服务。

退出终端后保持运行：

```bash
nohup env VENV=/app/llm/.venv DATA_DIR=/app/llm/local-data LAN_IP=<本机 IP> \
  bash deploy/school/local/run-lan.sh > /app/llm/local-data/server.log 2>&1 &
tail -f /app/llm/local-data/server.log    # 看到 "Starting development server at http://0.0.0.0:8080/" 即启动完成
```

## 5. 访问与使用

| 地址 | 内容 |
|---|---|
| `http://<本机 IP>:8080/` | Label Studio |
| `http://<本机 IP>:8080/school/` | 学院工作台 |
| `http://<本机 IP>:8080/admin/` | Django 后台（平台管理员） |

演示账号（密码为 `Demo@2026`，或第 4.5 节设定的密码）：

| 账号 | 角色 |
|---|---|
| `platform.admin@demo.edu.cn` | 平台管理员（不属于任何学院） |
| `cs.admin@demo.edu.cn` / `med.admin@demo.edu.cn` | 学院管理员 |
| `cs.teacher@demo.edu.cn` / `med.teacher@demo.edu.cn` | 教师 |
| `cs.student1@demo.edu.cn` / `cs.student2@demo.edu.cn` / `med.student1@demo.edu.cn` | 学生 |
| `shared.teacher@demo.edu.cn` | 两个学院的教师 |

试用 3D 功能：

1. 用教师账号新建项目，在 Labeling Setup 的 “3D Point Cloud” 分组中选一个模板：
   - 3D 检测；
   - 相机融合；
   - 连续帧跟踪；
   - 语义分割；
   - 车道线。
2. 导入数据：模板自带示例数据。也可以导入 `label_studio/core/static/samples/pointcloud/` 下带预标注的示例任务：
   - `scene_000001_tasks.json`：3D 框；
   - `sequence_0001_tasks.json`：连续帧；
   - `scene_000001_segmentation_tasks.json`：语义分割；
   - `scene_000001_lanes_tasks.json`：车道线。
3. 模型跟踪与训练：
   - 项目 Settings → Model → Connect Model，填 `http://127.0.0.1:9090`；
   - 打开 *Interactive preannotations*；
   - 训练好的模型保存在 `<数据目录>/tracker-models/`。
4. 各功能的操作说明见 [`label_studio/pointcloud3d/README.md`](../../../label_studio/pointcloud3d/README.md)。

## 6. 检查与停止

```bash
netstat -tlnp | grep -E ':8080|:9090'              # 两个端口都应处于 LISTEN
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/   # 302 或 200
curl -s http://127.0.0.1:9090/health               # 3D 模型服务健康检查
tail -f /app/llm/local-data/tracker.log            # 模型服务日志
```

停止：前台运行时按 Ctrl+C（模型服务会一起退出）。后台运行时：

```bash
pkill -f "manage.py runserver 0.0.0.0:8080"
pkill -f "pointcloud_tracker.*_wsgi.py|_wsgi.py"
```

防火墙开启时放行端口，例如 `sudo ufw allow 8080/tcp && sudo ufw allow 9090/tcp`。
模型服务只需要 Label Studio 本机能访问，可以不对外开放 9090。

## 7. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `npm ERR! ... JavaScript heap out of memory` | 加 `NODE_OPTIONS=--max-old-space-size=4096`，并确认交换空间已启用（4.1、4.3） |
| 构建报 `Module not found: TypeError: Cannot read properties of undefined (reading 'tap')` | 前端依赖是用 npm 装的，出现了两份 webpack：`rm -rf web/node_modules` 后改用 yarn 安装（4.3） |
| `error ... The engine "node" is incompatible with this module. Expected version "20 \|\| >=22"` | Node 版本太旧：安装 Node 22（第 1 节） |
| 构建时出现 `/bin/sh: 1: yarn: not found` | 没有安装 yarn：`sudo npm install -g yarn@1.22.22` |
| `npm ERR! code ENOTEMPTY ... rename .../node_modules/xxx` | 上一次安装中途失败留下的残留：`rm -rf web/node_modules` 后重装 |
| 依赖下载超时（`ERR_SOCKET_TIMEOUT`、`ESOCKETTIMEDOUT`） | 机器在频繁换页或网络慢：加大超时参数后重试，或换 npmmirror 镜像（4.3） |
| 构建时进程被系统杀掉（`Killed`） | 内存加交换空间仍不够：增大交换空间，或在别的电脑构建后拷贝 `web/dist`（4.4） |
| `ModuleNotFoundError: No module named 'label_studio'` | venv 没有指向本仓库：删除 venv 后重新运行脚本。旧版脚本在 Debian 上会把路径写到不存在的 `dist-packages`，已修复 |
| 局域网其他电脑登录或提交时 `CSRF verification failed` | `LAN_IP` 与浏览器地址栏中的地址不一致：把 `LAN_IP` 设为实际访问用的 IP 或域名后重启 |
| 打开页面空白或缺样式 | 前端没有构建或没有收集静态文件：确认 `web/dist/apps/labelstudio` 存在，重新运行脚本（会执行 `collectstatic`） |
| 点云不显示 | 前端是在加入 3D 模块之前构建的：`REBUILD=1` 重新构建 |
| 项目里连接模型服务失败 | `curl http://127.0.0.1:9090/health` 检查服务；查看 `<数据目录>/tracker.log` |

## 8. 安全提醒

- 开发服务器和 SQLite 只用于测试，不要存放真实数据。
- 机器有公网 IP 时，开放的端口任何人都能访问：
  - 演示账号必须使用自己设定的密码（4.5）；
  - 测试结束后停止服务（第 6 节）；
  - 或者只允许指定来源访问，例如 `sudo ufw allow from <你的 IP> to any port 8080`。
