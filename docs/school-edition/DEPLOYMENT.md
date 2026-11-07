# 学校多租户版部署向导

> 代码目录：`/app/label_studio_multi_user/label-studio-edu`
> 当前代码基线：Label Studio `1.24.0.dev0` + `school_tenancy`
> 文档日期：2026-11-07

---

## 0. 部署状态说明

| 部署方式 | 状态 | 说明 |
|---|---|---|
| Linux 本地演示（SQLite + 本地文件） | ✅ 已验证 | 当前 8094 演示环境就是这个模式 |
| Windows 本地演示 | ✅ 已验证 | `deploy/school/local/*.ps1` |
| Ubuntu + PostgreSQL + Garage + nginx + uWSGI | ⚠️ 参考方案，未完整实测 | `deploy/school/install.sh` 仍缺少部分运维脚本，且脚本按 1.23.2/uv 流程编写 |
| Docker / Kubernetes | ⚠️ 未针对学校版验证 | 上游 Dockerfile 可用，但学校版生产配置未做镜像化 |

**结论：**

- 演示、开发、验收：使用本文第 1～3 节的 SQLite 模式。
- 生产：可以按第 5～8 节手工部署，但必须先在一台测试 Ubuntu 上完整演练。
  在 `deploy/school/install.sh` 补齐并实测前，不要直接用于生产数据。

---

## 1. 当前演示环境（8094）

### 1.1 启动

```bash
cd /app/label_studio_multi_user/label-studio-edu/label_studio

LABEL_STUDIO_BASE_DATA_DIR=/root/.local/share/label-studio-edu \
DJANGO_SETTINGS_MODULE=core.settings.school_local \
nohup python3 manage.py runserver 0.0.0.0:8094 --noreload \
  > /tmp/label-studio-8094.log 2>&1 &
```

### 1.2 访问

| 用途 | 地址 |
|---|---|
| 平台 | `http://192.168.1.17:8094/` |
| 登录 | `http://192.168.1.17:8094/user/login/` |
| 学院工作台 | `http://192.168.1.17:8094/school/` |
| 所有标注数据 | `http://192.168.1.17:8094/school/annotations/` |
| Django 后台 | `http://192.168.1.17:8094/admin/` |

### 1.3 演示账号

统一密码：`Demo@2026`

| 账号 | 身份 |
|---|---|
| `cs.admin@demo.edu.cn` | 计算机学院管理员 |
| `cs.teacher@demo.edu.cn` | 计算机学院教师 |
| `cs.student1@demo.edu.cn` | 计算机学院学生 |
| `cs.student2@demo.edu.cn` | 计算机学院学生 |
| `med.admin@demo.edu.cn` | 医学院管理员 |
| `med.teacher@demo.edu.cn` | 医学院教师 |
| `med.student1@demo.edu.cn` | 医学院学生 |
| `shared.teacher@demo.edu.cn` | 跨学院教师 |
| `platform.admin@demo.edu.cn` | 系统管理员（`/admin/`） |

### 1.4 停止 / 日志

```bash
pkill -f "manage.py runserver 0.0.0.0:8094"
tail -f /tmp/label-studio-8094.log
```

---

## 2. 本地从零部署（Linux / SQLite）

### 2.1 前置条件

- Python 3.10～3.13
- Node.js + npm（仅首次构建前端需要）
- 建议 4 GB 以上内存（前端构建建议 8 GB 或 swap）

### 2.2 安装 Python 依赖

```bash
cd /app/label_studio_multi_user/label-studio-edu

python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

如果 `pip install -e .` 受 Poetry 配置影响失败，可使用项目已有环境，或：

```bash
pip install . 
```

> 生产环境建议固定 Python 3.12，并在虚拟环境中锁定依赖版本。

### 2.3 构建前端

```bash
cd web
NX_DAEMON=false BUILD_NO_SERVER=true \
  node node_modules/nx/bin/nx.js build labelstudio --configuration=development
```

如果首次没有 `node_modules`：

```bash
npm install --ignore-scripts --legacy-peer-deps --package-lock=false --no-audit --no-fund
```

构建产物在：

```text
web/dist/apps/labelstudio/
web/dist/libs/datamanager/
web/dist/libs/editor/
```

### 2.4 初始化数据

```bash
cd label_studio

export LABEL_STUDIO_BASE_DATA_DIR=/app/label_studio_multi_user/local-data
export DJANGO_SETTINGS_MODULE=core.settings.school_local

python3 manage.py migrate --noinput
python3 manage.py collectstatic --noinput
python3 manage.py school_seed_demo     # 可选：创建演示学院/账号/项目
```

`collectstatic` 输出到 `label_studio/core/static_build/`。

### 2.5 启动

```bash
python3 manage.py runserver 0.0.0.0:8094 --noreload
```

---

## 3. 常用管理命令

```bash
cd /app/label_studio_multi_user/label-studio-edu/label_studio
export DJANGO_SETTINGS_MODULE=core.settings.school_local
export LABEL_STUDIO_BASE_DATA_DIR=/root/.local/share/label-studio-edu

# 创建学院（租户）
python3 manage.py school_create_college cs 计算机学院

# 添加成员/创建账号（交互输入密码）
python3 manage.py school_add_member cs teacher@example.edu.cn --role teacher --name 王老师
python3 manage.py school_add_member cs student@example.edu.cn --role student --name 张同学

# 演示数据
python3 manage.py school_seed_demo

# 数据库迁移 / 静态文件
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# Django 检查
python3 manage.py check
```

---

## 4. 生产目标架构

```text
                 HTTPS
浏览器 ───────────────▶ nginx
                         │  /static/  -> label_studio/core/static_build/
                         │  /react-app/ -> web/dist/apps/labelstudio/
                         │  / -> 127.0.0.1:8000
                         ▼
                    uWSGI (Label Studio)
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
   PostgreSQL 17                  Garage S3
   账号/学院/项目/任务/标注          素材/头像/导出
```

| 组件 | 建议 |
|---|---|
| OS | Ubuntu 22.04 / 24.04 |
| Python | 3.12 |
| 数据库 | PostgreSQL 17（PGDG） |
| 对象存储 | Garage v2.4.1 或 MinIO，S3 兼容 |
| 应用服务器 | uWSGI + systemd |
| 入口 | nginx + TLS |
| 备份 | 第二块独立磁盘，数据库 + 对象存储 |

参考配置模板：

```text
deploy/school/school.conf.example
deploy/school/templates/label-studio.env
deploy/school/templates/uwsgi.ini
deploy/school/templates/nginx-label-studio.conf
deploy/school/templates/postgresql.conf
deploy/school/templates/garage.toml
deploy/school/systemd/*.service
deploy/school/systemd/*.timer
```

可以用 `install.sh --render-only` 预览渲染后的配置，但**不要直接运行完整安装脚本用于生产**：

```bash
bash deploy/school/install.sh --render-only deploy/school/school.conf.example /tmp/ls-render
```

---

## 5. 生产环境变量

生产 settings 是 `core.settings.school`，必须提供以下环境变量。

### 5.1 基础

| 变量 | 必填 | 说明 |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | ✅ | 固定 `core.settings.school` |
| `SECRET_KEY` | ✅ | ≥ 50 字符，随机生成 |
| `LABEL_STUDIO_BASE_DATA_DIR` | ✅ | 例如 `/var/lib/label-studio` |
| `LABEL_STUDIO_HOST` | ✅ | 例如 `https://label.example.edu.cn` |
| `ALLOWED_HOSTS` | ✅ | 逗号分隔，不能为 `*` |
| `CSRF_TRUSTED_ORIGINS` | ✅ | 例如 `https://label.example.edu.cn` |
| `LOG_LEVEL` | 可选 | 默认 `WARNING` |
| `DEBUG` | 可选 | 生产必须 `False` |

### 5.2 PostgreSQL

| 变量 | 说明 |
|---|---|
| `DJANGO_DB=postgresql` | 必须 |
| `POSTGRE_HOST` | 本机可用 `/var/run/postgresql`（Unix socket + peer） |
| `POSTGRE_PORT` | 默认 5432 |
| `POSTGRE_NAME` | 数据库名 |
| `POSTGRE_USER` | 数据库用户 |
| `POSTGRE_PASSWORD` | TCP 连接时必填 |
| `PGSSLROOTCERT` | 非 loopback 且 TLS 校验时需要 |

### 5.3 对象存储

| 变量 | 说明 |
|---|---|
| `STORAGE_TYPE=s3` | 必须 |
| `STORAGE_AWS_ENDPOINT_URL` | 例如 `http://127.0.0.1:3900` |
| `STORAGE_AWS_REGION_NAME` | 例如 `garage` |
| `STORAGE_AWS_BUCKET_NAME` | 桶名 |
| `STORAGE_AWS_ACCESS_KEY_ID` | Access Key |
| `STORAGE_AWS_SECRET_ACCESS_KEY` | Secret Key |
| `STORAGE_AWS_ADDRESSING_STYLE=path` | Garage/MinIO 建议 |
| `STORAGE_AWS_S3_SIGNATURE_VERSION=s3v4` | 建议 |
| `STORAGE_AWS_S3_VERIFY` | HTTPS 证书校验；不要关闭 |

生产 settings 会强制：

- 不允许 legacy `MINIO_STORAGE_*`
- 不允许 `STORAGE_TYPE` 非 S3
- 不允许 `ALLOWED_HOSTS=*`
- 不允许 HTTP 对象存储用于非 loopback
- 不允许关闭 HTTPS 证书校验（非 loopback）

---

## 6. 手工生产部署步骤（参考）

> 以下步骤用于测试 Ubuntu 和正式部署前的演练。每一步完成后先验证，再继续。

### 6.1 系统用户与目录

```bash
sudo useradd --system --create-home --home-dir /var/lib/label-studio --shell /usr/sbin/nologin labelstudio
sudo install -d -m 755 -o labelstudio -g labelstudio /opt/label-studio/src
sudo install -d -m 700 -o labelstudio -g labelstudio /var/lib/label-studio
```

把代码放到 `/opt/label-studio/src`（不能放在 `/root` 或 `/home`，systemd 服务读不到）：

```bash
sudo rsync -a --exclude .git --exclude node_modules \
  /app/label_studio_multi_user/label-studio-edu/ /opt/label-studio/src/
sudo chown -R labelstudio:labelstudio /opt/label-studio/src
```

### 6.2 PostgreSQL

```bash
sudo apt install -y postgresql-17 postgresql-client-17
sudo -u postgres createuser --createdb labelstudio
sudo -u postgres createdb -O labelstudio labelstudio
```

本机推荐 Unix socket + peer 认证，只监听 `localhost`：

```text
listen_addresses = 'localhost'
```

`deploy/school/templates/postgresql.conf` 可作为参考。

### 6.3 Garage / S3

参考 `deploy/school/templates/garage.toml`：

- `rpc_bind_addr = 127.0.0.1:3901`
- `api_bind_addr = 127.0.0.1:3900`
- `data_dir` / `metadata_dir` 放在 `DATA_ROOT`
- 只监听 loopback

创建桶和访问密钥：

```bash
garage bucket create label-studio
garage key create label-studio-key
garage bucket allow --read --write --owner label-studio label-studio-key
```

把 Access Key / Secret 写入环境文件。

### 6.4 Python 环境

```bash
sudo -u labelstudio python3.12 -m venv /opt/label-studio/venv
sudo -u labelstudio /opt/label-studio/venv/bin/pip install --upgrade pip
sudo -u labelstudio /opt/label-studio/venv/bin/pip install -e /opt/label-studio/src
```

如果使用 Poetry/uv，请按仓库锁文件安装，并确认安装的是当前源码而不是 PyPI 的 label-studio。

### 6.5 环境文件

```bash
sudo install -d -m 750 -o root -g labelstudio /etc/label-studio
sudo install -m 640 -o root -g labelstudio \
  deploy/school/templates/label-studio.env /etc/label-studio/label-studio.env
sudo editor /etc/label-studio/label-studio.env
```

至少修改：

```dotenv
DJANGO_SETTINGS_MODULE=core.settings.school
SECRET_KEY=<至少50字符随机串>
LABEL_STUDIO_HOST=https://label.example.edu.cn
ALLOWED_HOSTS=label.example.edu.cn
CSRF_TRUSTED_ORIGINS=https://label.example.edu.cn
LABEL_STUDIO_BASE_DATA_DIR=/var/lib/label-studio
DJANGO_DB=postgresql
POSTGRE_HOST=/var/run/postgresql
POSTGRE_NAME=labelstudio
POSTGRE_USER=labelstudio
STORAGE_TYPE=s3
STORAGE_AWS_ENDPOINT_URL=http://127.0.0.1:3900
STORAGE_AWS_REGION_NAME=garage
STORAGE_AWS_BUCKET_NAME=label-studio
STORAGE_AWS_ACCESS_KEY_ID=...
STORAGE_AWS_SECRET_ACCESS_KEY=...
```

### 6.6 迁移、静态文件

```bash
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py migrate --noinput

sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py collectstatic --noinput
```

`manage.py` 在 `/opt/label-studio/src/label_studio/manage.py`，执行前先 `cd` 到该目录。

### 6.7 uWSGI + systemd

参考 `deploy/school/templates/uwsgi.ini` 和
`deploy/school/systemd/label-studio.service`：

- `chdir = /opt/label-studio/src/label_studio`
- `module = core.wsgi:application`
- `http-socket = 127.0.0.1:8000`
- `EnvironmentFile=/etc/label-studio/label-studio.env`
- `User=labelstudio`

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now label-studio
sudo systemctl status label-studio
```

### 6.8 nginx + TLS

参考 `deploy/school/templates/nginx-label-studio.conf`：

```nginx
location /static/ {
    alias /opt/label-studio/src/label_studio/core/static_build/;
}

location /react-app/ {
    alias /opt/label-studio/src/web/dist/apps/labelstudio/;
}

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header X-Forwarded-Proto $scheme;
    ...
}
```

配置证书后：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 6.9 创建系统管理员和学院

```bash
# Django 后台系统管理员
sudo -u labelstudio env ... /opt/label-studio/venv/bin/python manage.py createsuperuser

# 学院
sudo -u labelstudio env ... /opt/label-studio/venv/bin/python manage.py school_create_college cs 计算机学院

# 学院管理员
sudo -u labelstudio env ... /opt/label-studio/venv/bin/python manage.py school_add_member cs admin@example.edu.cn --role college_admin
```

---

## 7. 备份与恢复

### 7.1 需要备份

| 内容 | 位置 |
|---|---|
| PostgreSQL 数据库 | `pg_dump` |
| Garage/对象存储 | 数据目录或 S3 同步 |
| `.env` / 密钥 | `/etc/label-studio`、`/etc/garage` |
| 前端构建产物 | 可重建，不必备份 |

### 7.2 建议策略

- 数据库：每日 `pg_dump`，保留 30 天。
- 对象存储：只增不删镜像到第二块磁盘。
- 备份目录必须在**另一块物理磁盘**。
- 定期做恢复演练，不要只验证备份文件存在。

参考：

```bash
deploy/school/bin/objectstore_backup.py
deploy/school/systemd/label-studio-backup.service
deploy/school/systemd/label-studio-backup.timer
```

`install.sh` 引用的 `backup.sh` / `restore.sh` 尚未补齐，生产前必须实现并测试。

---

## 8. 升级与回滚

### 8.1 升级

```bash
# 1. 备份数据库和对象存储
# 2. 拉取/覆盖新代码到 /opt/label-studio/src
# 3. 更新依赖
# 4. 构建前端
# 5. collectstatic
# 6. migrate
# 7. 重启服务
sudo systemctl restart label-studio
sudo systemctl reload nginx
```

### 8.2 回滚

- 代码：切回上一个 git tag/release。
- 数据库：使用升级前 `pg_dump` 恢复。
- 前端：切回上一版 `web/dist` 并重新 `collectstatic`。
- 对象存储：一般不需要回滚；应用版本需兼容已有对象。

---

## 9. 健康检查

```bash
# 应用进程
systemctl status label-studio

# 本地端口
curl -I http://127.0.0.1:8000/

# 外部 HTTPS
curl -I https://label.example.edu.cn/

# 数据库
sudo -u labelstudio psql -d labelstudio -c 'select 1;'

# 对象存储
curl -I http://127.0.0.1:3900/
```

登录后检查：

- `/school/` 是否正常打开。
- 学院管理员是否能看本学院项目。
- 跨学院账号是否被拒绝。
- `/school/annotations/` 是否只显示授权范围。
- 学生是否只看到被分配项目/授权功能。

---

## 10. 常见问题

### 10.1 登录后跳转 `/school/no-college/`

原因：当前账号没有任何有效学院成员关系。

处理：

```bash
python manage.py school_add_member <college_code> <email> --role student
```

或检查学院是否 `is_active=True`、成员是否被软删除。

### 10.2 页面 403 “当前账号未加入任何学院”

- 确认使用 `core.settings.school_local` / `core.settings.school`。
- 确认用户有有效 `OrganizationMember` + `MemberRole`。
- 系统管理员访问 `/school/annotations/` 不受学院限制。

### 10.3 静态文件 404

```bash
python manage.py collectstatic --noinput
```

确认 nginx：

```nginx
location /static/ { alias /opt/label-studio/src/label_studio/core/static_build/; }
location /react-app/ { alias /opt/label-studio/src/web/dist/apps/labelstudio/; }
```

### 10.4 前端构建内存不足

- 增加 swap；
- 或先在构建机构建 `web/dist`，再拷贝到服务器；
- 设置 `SKIP_FRONTEND_BUILD=yes`（仅限已有构建产物时）。

### 10.5 端口被占用

```bash
ss -lntp | grep 8094
```

换端口或停止占用进程。

### 10.6 旧 orgext 数据库升级

当前代码不会自动把旧 `Organization` / `Workspace` / `OrganizationMember.role` 转换成
`College` / `MemberRole`。需要：

1. 为每个旧 Organization 创建 College。
2. 为每个成员写入 MemberRole。
3. 必要时把旧 Workspace/Assignment 数据归档。

在完成数据迁移前，学校模式下旧组织会被视为“未加入学院”。

---

## 11. 安全检查清单

- [ ] `DEBUG=False`
- [ ] `SECRET_KEY` 随机且 ≥ 50 字符
- [ ] `ALLOWED_HOSTS` 明确列表，不含 `*`
- [ ] 全站 HTTPS，`CSRF_TRUSTED_ORIGINS` 正确
- [ ] PostgreSQL 只监听本机 / 受防火墙保护
- [ ] Garage/S3 只监听 loopback，桶私有
- [ ] 环境文件权限 `0640 root:labelstudio`
- [ ] 备份目录在第二块磁盘
- [ ] 系统管理员数量受控
- [ ] 学院管理员账号使用强密码
- [ ] 公开注册关闭（`DISABLE_SIGNUP_WITHOUT_LINK=True`）
- [ ] 定期检查 `/admin/` 访问日志
- [ ] 定期演练数据库和对象存储恢复

---

## 12. 附：当前代码常用路径

```text
代码：/app/label_studio_multi_user/label-studio-edu
兼容路径：/app/label-studio-edu（软链接）
演示数据：/root/.local/share/label-studio-edu
演示日志：/tmp/label-studio-8094.log
静态文件：label_studio/core/static_build
前端产物：web/dist/apps/labelstudio
学校配置：label_studio/core/settings/school*.py
部署模板：deploy/school/
```
