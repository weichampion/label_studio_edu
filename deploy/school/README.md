# 学校多租户版 Linux 单机部署与使用向导

> 本文是一份可以在一天内完成部署的单机指南。
> 目标环境：Ubuntu 22.04 / 24.04，单台 Linux 服务器，原生安装，不使用 Docker。
> 代码目录：`/opt/label-studio/src`
> 应用入口：nginx → gunicorn → Django
> 数据：PostgreSQL + MinIO（S3 兼容）

---

## 0. 先看这里

### 0.1 适用场景

- 一个学院或一个学校内部试点使用。
- 一台服务器，4 核 CPU、8 GB 内存、100 GB 磁盘起步。
- 对外提供 HTTPS 域名，例如 `label.example.edu.cn`。
- 不需要 K8s、不需要对象存储集群。

### 0.2 部署时间参考

| 阶段 | 预计时间 |
|---|---|
| 系统、Python、Node 依赖 | 20～40 分钟 |
| PostgreSQL | 10 分钟 |
| MinIO | 15 分钟 |
| 代码、Python 环境、前端构建 | 30～60 分钟 |
| 迁移、静态文件、gunicorn、nginx | 30 分钟 |
| 建管理员、学院、账号、验收 | 30～60 分钟 |
| **合计** | **约 2.5～4 小时** |

首次部署建议预留完整一天，包含网络、证书、学校防火墙和验收时间。

### 0.3 重要说明

- 本文使用 **手工部署**，不直接运行 `deploy/school/install.sh`。
  该脚本仍缺少部分运维脚本，尚未在真实 Ubuntu 上完整验证。
- 本地 SQLite 演示模式已验证；本文生产路径基于当前代码和 `deploy/school` 模板整理，
  正式上线前请先在测试机完整演练一次。
- 生产必须使用 `core.settings.school`，不能使用 `core.settings.label_studio`。
- 当前代码没有“学校 → 多学院”的学校层级；一个 `College` 就是一个独立租户。

---

## 1. 部署前准备

### 1.1 需要确认的信息

| 项目 | 示例 |
|---|---|
| 服务器 IP | `192.168.1.20` |
| 域名 | `label.example.edu.cn` |
| 管理员邮箱 | `admin@example.edu.cn` |
| 学院代码 | `cs` |
| 学院名称 | `计算机学院` |
| 数据目录 | `/var/lib/label-studio` |
| 代码目录 | `/opt/label-studio/src` |

### 1.2 DNS 和端口

- 域名 A 记录指向服务器 IP。
- 放行 80、443。
- 数据库 5432、MinIO 9000/9001 **只监听 127.0.0.1**，不要暴露公网。

### 1.3 系统更新与基础软件

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip python3-dev \
  build-essential libpq-dev \
  postgresql postgresql-contrib \
  nginx curl unzip git ca-certificates
```

检查版本：

```bash
python3 --version    # 建议 3.10～3.13
psql --version
nginx -v
```

### 1.4 创建应用用户

```bash
sudo useradd --system --create-home \
  --home-dir /var/lib/label-studio \
  --shell /usr/sbin/nologin labelstudio

sudo install -d -m 755 -o labelstudio -g labelstudio /opt/label-studio
sudo install -d -m 700 -o labelstudio -g labelstudio /var/lib/label-studio
```

> 应用不能以 root 运行。

---

## 2. 放代码

### 2.1 从 GitHub 拉取

```bash
sudo -u labelstudio git clone \
  https://github.com/weichampion/label_studio_edu.git \
  /opt/label-studio/src
```

如果服务器不能访问 GitHub，可以在开发机打包后上传：

```bash
# 开发机
tar czf label-studio-edu.tar.gz \
  --exclude=.git --exclude=node_modules \
  -C /app/label_studio_multi_user label-studio-edu

# 服务器
sudo tar xzf label-studio-edu.tar.gz -C /opt/label-studio/
sudo mv /opt/label-studio/label-studio-edu /opt/label-studio/src
sudo chown -R labelstudio:labelstudio /opt/label-studio/src
```

### 2.2 检查目录

```bash
ls /opt/label-studio/src/label_studio/manage.py
ls /opt/label-studio/src/web/package.json
```

---

## 3. PostgreSQL

### 3.1 创建数据库和用户

```bash
sudo -u postgres createuser --createdb labelstudio
sudo -u postgres createdb -O labelstudio labelstudio
```

### 3.2 使用 Unix socket + peer 认证

应用和数据库在同一台机器上，推荐：

```text
POSTGRE_HOST=/var/run/postgresql
POSTGRE_NAME=labelstudio
POSTGRE_USER=labelstudio
```

应用以 `labelstudio` 用户运行，PostgreSQL 的 peer 认证会匹配同名系统用户，不需要数据库密码。

### 3.3 验证

```bash
sudo -u labelstudio psql -d labelstudio -c 'select 1;'
```

如果失败，检查 `pg_hba.conf` 是否有：

```text
local   all   labelstudio   peer
```

然后：

```bash
sudo systemctl restart postgresql
```

---

## 4. MinIO 对象存储

学校版生产模式要求 S3 兼容对象存储。MinIO 单二进制即可。

### 4.1 下载 MinIO

```bash
sudo install -d -m 755 /opt/minio
sudo install -d -m 700 -o labelstudio -g labelstudio /var/lib/minio

sudo curl -L -o /opt/minio/minio \
  https://dl.min.io/server/minio/release/linux-amd64/minio
sudo chmod +x /opt/minio/minio

sudo curl -L -o /usr/local/bin/mc \
  https://dl.min.io/client/mc/release/linux-amd64/mc
sudo chmod +x /usr/local/bin/mc
```

> ARM64 服务器把 `linux-amd64` 换成 `linux-arm64`。

### 4.2 MinIO 环境文件

```bash
sudo tee /etc/default/minio >/dev/null <<'EOF'
MINIO_ROOT_USER=labelstudio
MINIO_ROOT_PASSWORD=请替换为至少16位强密码
MINIO_VOLUMES=/var/lib/minio
MINIO_OPTS=--address 127.0.0.1:9000 --console-address 127.0.0.1:9001
EOF
sudo chmod 600 /etc/default/minio
```

### 4.3 systemd

```bash
sudo tee /etc/systemd/system/minio.service >/dev/null <<'EOF'
[Unit]
Description=MinIO object storage
After=network.target

[Service]
User=labelstudio
Group=labelstudio
EnvironmentFile=/etc/default/minio
ExecStart=/opt/minio/minio server $MINIO_OPTS $MINIO_VOLUMES
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now minio
sudo systemctl status minio --no-pager
```

### 4.4 创建桶和访问密钥

```bash
mc alias set local http://127.0.0.1:9000 labelstudio '请替换为同一个强密码'
mc mb local/label-studio
mc anonymous set none local/label-studio
```

建议单独创建应用 Access Key，而不是直接用 root 用户。MinIO 控制台在
`http://127.0.0.1:9001`（通过 SSH 隧道访问），可在控制台创建 Access Key。

记录：

```text
Endpoint:   http://127.0.0.1:9000
Region:     us-east-1
Bucket:     label-studio
AccessKey:  ...
SecretKey:  ...
```

---

## 5. Python 环境与应用依赖

```bash
sudo -u labelstudio python3 -m venv /opt/label-studio/venv
sudo -u labelstudio /opt/label-studio/venv/bin/pip install --upgrade pip

sudo -u labelstudio /opt/label-studio/venv/bin/pip install -e /opt/label-studio/src
sudo -u labelstudio /opt/label-studio/venv/bin/pip install gunicorn
```

如果 `pip install -e .` 失败，可尝试：

```bash
cd /opt/label-studio/src
sudo -u labelstudio /opt/label-studio/venv/bin/pip install .
```

如果项目使用 Poetry/uv 锁定依赖，也可以按仓库锁文件安装，但必须确保安装的是
`/opt/label-studio/src` 这份源码，而不是 PyPI 上的 label-studio。

验证：

```bash
sudo -u labelstudio /opt/label-studio/venv/bin/python -c "import django; print(django.get_version())"
```

---

## 6. 构建前端

服务器内存建议 8 GB 以上；不足时增加 swap，或在开发机构建后拷贝 `web/dist`。

### 6.1 安装 Node

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs
node -v
npm -v
```

### 6.2 构建

```bash
cd /opt/label-studio/src/web

sudo -u labelstudio npm install \
  --ignore-scripts --legacy-peer-deps \
  --package-lock=false --no-audit --no-fund

sudo -u labelstudio env \
  NX_DAEMON=false \
  BUILD_NO_SERVER=true \
  node node_modules/nx/bin/nx.js build labelstudio --configuration=development
```

产物：

```text
/opt/label-studio/src/web/dist/apps/labelstudio/
```

> 如果服务器不能安装 Node，在开发机执行同样的构建，然后把 `web/dist` 整个目录
> 拷贝到服务器对应位置。

---

## 7. 环境文件

```bash
sudo install -d -m 750 -o root -g labelstudio /etc/label-studio
sudo tee /etc/label-studio/label-studio.env >/dev/null <<'EOF'
DJANGO_SETTINGS_MODULE=core.settings.school

# 必填：至少 50 字符随机串
SECRET_KEY=请替换为随机生成的至少50字符字符串

LABEL_STUDIO_HOST=https://label.example.edu.cn
ALLOWED_HOSTS=label.example.edu.cn
CSRF_TRUSTED_ORIGINS=https://label.example.edu.cn

LABEL_STUDIO_BASE_DATA_DIR=/var/lib/label-studio
HOME=/var/lib/label-studio
LOG_LEVEL=WARNING

# PostgreSQL（Unix socket + peer）
DJANGO_DB=postgresql
POSTGRE_HOST=/var/run/postgresql
POSTGRE_PORT=5432
POSTGRE_NAME=labelstudio
POSTGRE_USER=labelstudio

# MinIO / S3
STORAGE_TYPE=s3
STORAGE_AWS_ENDPOINT_URL=http://127.0.0.1:9000
STORAGE_AWS_REGION_NAME=us-east-1
STORAGE_AWS_BUCKET_NAME=label-studio
STORAGE_AWS_ACCESS_KEY_ID=请替换
STORAGE_AWS_SECRET_ACCESS_KEY=请替换
STORAGE_AWS_ADDRESSING_STYLE=path
STORAGE_AWS_S3_SIGNATURE_VERSION=s3v4
EOF

sudo chmod 640 /etc/label-studio/label-studio.env
sudo chown root:labelstudio /etc/label-studio/label-studio.env
```

生成随机 SECRET_KEY：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

安全要求：

- `ALLOWED_HOSTS` 不能写 `*`。
- `LABEL_STUDIO_HOST` 必须和浏览器访问地址一致。
- `CSRF_TRUSTED_ORIGINS` 必须包含 `https://域名`。
- 环境文件权限保持 `0640 root:labelstudio`。

---

## 8. 数据库迁移与静态文件

```bash
cd /opt/label-studio/src/label_studio

sudo -u labelstudio env \
  $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py migrate --noinput

sudo -u labelstudio env \
  $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py collectstatic --noinput
```

静态文件目录：

```text
/opt/label-studio/src/label_studio/core/static_build/
```

检查：

```bash
sudo -u labelstudio env \
  $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py check
```

---

## 9. gunicorn + systemd

### 9.1 systemd 服务

```bash
sudo tee /etc/systemd/system/label-studio.service >/dev/null <<'EOF'
[Unit]
Description=Label Studio school edition (gunicorn)
After=network.target postgresql.service minio.service
Wants=postgresql.service minio.service

[Service]
User=labelstudio
Group=labelstudio
EnvironmentFile=/etc/label-studio/label-studio.env
WorkingDirectory=/opt/label-studio/src/label_studio
ExecStart=/opt/label-studio/venv/bin/gunicorn core.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers 4 \
  --threads 2 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now label-studio
sudo systemctl status label-studio --no-pager
```

### 9.2 查看日志

```bash
sudo journalctl -u label-studio -f
```

### 9.3 本地验证

```bash
curl -I http://127.0.0.1:8000/
```

---

## 10. nginx + HTTPS

### 10.1 先配置 HTTP

先不要写 HTTPS 证书路径，否则 `nginx -t` 会因为证书不存在而失败。

```bash
sudo tee /etc/nginx/sites-available/label-studio >/dev/null <<'NGINX'
server {
    listen 80;
    server_name label.example.edu.cn;

    client_max_body_size 250m;
    server_tokens off;

    access_log /var/log/nginx/label-studio.access.log;
    error_log  /var/log/nginx/label-studio.error.log warn;

    location /static/ {
        alias /opt/label-studio/src/label_studio/core/static_build/;
        expires 7d;
    }

    location /react-app/ {
        alias /opt/label-studio/src/web/dist/apps/labelstudio/;
        expires 7d;
    }

    location /nginx_health {
        access_log off;
        return 200;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_connect_timeout 30s;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/label-studio /etc/nginx/sites-enabled/label-studio
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

此时可以先用 `http://label.example.edu.cn/` 验证页面是否正常。

### 10.2 申请证书并启用 HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d label.example.edu.cn
```

certbot 会自动修改 nginx 配置，添加 HTTPS 监听和 80→443 跳转。

验证：

```bash
curl -I https://label.example.edu.cn/
```

### 10.3 如果学校已有证书

不执行 10.2，直接把 10.1 的配置替换为下面这种 HTTPS 配置，
并把证书路径换成学校实际路径：

```nginx
server {
    listen 80;
    server_name label.example.edu.cn;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name label.example.edu.cn;

    ssl_certificate     /etc/ssl/school/label.example.edu.cn.crt;
    ssl_certificate_key /etc/ssl/school/label.example.edu.cn.key;

    client_max_body_size 250m;
    server_tokens off;

    location /static/ {
        alias /opt/label-studio/src/label_studio/core/static_build/;
        expires 7d;
    }

    location /react-app/ {
        alias /opt/label-studio/src/web/dist/apps/labelstudio/;
        expires 7d;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_connect_timeout 30s;
    }
}
```

---

## 11. 初始化系统管理员和学院

### 11.1 创建系统管理员

```bash
cd /opt/label-studio/src/label_studio

sudo -u labelstudio env \
  $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py createsuperuser
```

系统管理员可以：

- 登录 `/admin/`
- 访问 `/school/annotations/` 查看全部学院标注
- 不属于任何学院

### 11.2 创建学院

```bash
sudo -u labelstudio env \
  $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py school_create_college cs 计算机学院
```

### 11.3 创建学院管理员

```bash
sudo -u labelstudio env \
  $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py school_add_member \
  cs admin@example.edu.cn --role college_admin --name 学院管理员
```

按提示输入初始密码。

### 11.4 创建教师和学生

```bash
# 教师
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py school_add_member \
  cs teacher@example.edu.cn --role teacher --name 王老师

# 学生
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py school_add_member \
  cs student@example.edu.cn --role student --name 张同学
```

### 11.5 可选：演示数据

仅用于演示环境：

```bash
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py school_seed_demo
```

演示账号密码为 `Demo@2026`。生产环境不要执行。

---

## 12. 使用方式

### 12.1 登录

- 平台：`https://label.example.edu.cn/`
- 学院工作台：`https://label.example.edu.cn/school/`
- 所有标注数据：`https://label.example.edu.cn/school/annotations/`
- Django 后台：`https://label.example.edu.cn/admin/`

### 12.2 角色

| 角色 | 能做什么 |
|---|---|
| 系统管理员 | Django 后台、全部学院标注数据 |
| 学院管理员 | 本学院成员、全部项目、全部标注、授权 |
| 教师 | 自己创建或被指定负责的项目、学生分配与授权 |
| 学生 | 默认只标注分配给自己的项目 |

### 12.3 学院管理员

登录后进入 `/school/`：

1. 查看本学院项目和学生。
2. 添加成员、修改角色、移出成员。
3. 生成学院邀请链接，学生通过链接注册。
4. 指定项目负责教师。
5. 查看 `/school/annotations/` 中本学院全部标注数据。

### 12.4 教师

1. 在标注平台创建项目，或由学院管理员指定为负责教师。
2. 进入 `/school/`，在项目下分配学生。
3. 为学生勾选功能：
   - 标注
   - 导入
   - 导出
   - 任务管理
   - 项目配置
   - 删除项目
4. 为学生勾选可用标注模块：文本、图像、音频、视频、时间序列等。
5. 在“学生新建项目权限”中，按模板分类授权学生新建项目。

### 12.5 学生

- 默认只能看到分配给自己的项目，只能标注。
- 教师授权后，才出现导入、导出、设置、删除等入口。
- 被授权新建项目后，项目页出现 Create 按钮，Labeling Setup 只显示授权分类。
- 学生自建项目只有本人、授权教师、学院管理员可见。

### 12.6 全局标注数据

- 学院管理员：本学院全部标注。
- 系统管理员：全部学院，可按学院筛选。
- 教师、学生：403。
- JSON：

```text
/school/annotations/?format=json
/school/annotations/?format=json&project=12
/school/annotations/?format=json&annotator=student@example.edu.cn
```

### 12.7 多学院账号

一个用户可以属于多个学院。登录后在工作台切换当前学院，项目和数据随当前学院变化。

---

## 13. 日常运维

### 13.1 服务

```bash
sudo systemctl status label-studio
sudo systemctl restart label-studio
sudo systemctl status minio
sudo systemctl status postgresql
sudo nginx -t && sudo systemctl reload nginx
```

### 13.2 日志

```bash
sudo journalctl -u label-studio -f
sudo journalctl -u minio -f
sudo tail -f /var/log/nginx/label-studio.error.log
```

### 13.3 备份

数据库：

```bash
sudo -u postgres pg_dump -Fc labelstudio \
  > /backup/labelstudio-$(date +%F).dump
```

对象存储：

```bash
mc mirror --overwrite local/label-studio /backup/minio/label-studio
```

建议：

- 备份到第二块物理磁盘。
- 每天定时执行。
- 定期做恢复演练。
- 备份 `/etc/label-studio/label-studio.env` 和 MinIO 密钥。

### 13.4 恢复

```bash
sudo systemctl stop label-studio
sudo -u postgres dropdb labelstudio
sudo -u postgres createdb -O labelstudio labelstudio
sudo -u postgres pg_restore -d labelstudio /backup/labelstudio-YYYY-MM-DD.dump
mc mirror --overwrite /backup/minio/label-studio local/label-studio
sudo systemctl start label-studio
```

### 13.5 升级

```bash
# 1. 备份数据库和 MinIO
# 2. 更新代码
cd /opt/label-studio/src
sudo -u labelstudio git pull

# 3. 更新依赖
sudo -u labelstudio /opt/label-studio/venv/bin/pip install -e .

# 4. 构建前端
cd web
sudo -u labelstudio env NX_DAEMON=false BUILD_NO_SERVER=true \
  node node_modules/nx/bin/nx.js build labelstudio --configuration=development

# 5. 迁移和静态文件
cd ../label_studio
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py migrate --noinput
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py collectstatic --noinput

# 6. 重启
sudo systemctl restart label-studio
sudo systemctl reload nginx
```

---

## 14. 验收清单

部署完成后逐项检查：

- [ ] `https://域名/` 能打开
- [ ] `https://域名/user/login/` 能登录
- [ ] 系统管理员能登录 `/admin/`
- [ ] `/school/` 能打开
- [ ] 学院管理员能看到本学院项目
- [ ] 教师能看到自己创建/负责的项目
- [ ] 学生只看到分配给自己的项目
- [ ] 教师能给学生授权功能/模块
- [ ] 学生被授权后能看到对应入口
- [ ] 学生新建项目只显示授权模板分类
- [ ] 跨学院访问返回 403/404
- [ ] 学院管理员 `/school/annotations/` 只看到本学院
- [ ] 系统管理员 `/school/annotations/` 能看到全部学院
- [ ] 上传文件能正常保存到 MinIO
- [ ] 导出功能正常
- [ ] nginx 静态文件无 404
- [ ] 数据库和 MinIO 备份成功
- [ ] 重启服务器后服务自动恢复

---

## 15. 常见问题

### 15.1 登录后跳 `/school/no-college/`

账号没有有效学院成员关系。

```bash
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py school_add_member \
  cs user@example.edu.cn --role student
```

### 15.2 API 返回 403“未加入任何学院”

- 确认使用 `core.settings.school`。
- 确认用户有 `OrganizationMember` 和 `MemberRole`。
- 确认学院 `is_active=True`。

### 15.3 静态文件 404

```bash
sudo -u labelstudio env $(grep -v '^#' /etc/label-studio/label-studio.env | xargs) \
  /opt/label-studio/venv/bin/python manage.py collectstatic --noinput
```

检查 nginx alias 路径是否以 `/` 结尾。

### 15.4 上传失败 / 文件 404

检查：

```bash
sudo systemctl status minio
mc ls local/label-studio
```

确认 `STORAGE_AWS_*` 与 MinIO 配置一致。

### 15.5 数据库连接失败

```bash
sudo -u labelstudio psql -d labelstudio -c 'select 1;'
```

检查 `POSTGRE_HOST`、数据库用户、pg_hba。

### 15.6 前端构建内存不足

- 增加 swap；
- 或开发机构建 `web/dist` 后拷贝；
- 服务器只负责运行，不构建。

### 15.7 旧 orgext 数据

当前代码不会自动把旧 `Organization` / `Workspace` / `OrganizationMember.role`
转换成 `College` / `MemberRole`。旧库升级前需要先做数据迁移，否则用户会显示“未加入学院”。

---

## 16. 安全检查

- [ ] `DEBUG=False`
- [ ] `SECRET_KEY` 随机且 ≥ 50 字符
- [ ] `ALLOWED_HOSTS` 明确，不含 `*`
- [ ] 全站 HTTPS
- [ ] `CSRF_TRUSTED_ORIGINS` 正确
- [ ] PostgreSQL 只监听本机
- [ ] MinIO 只监听 127.0.0.1
- [ ] MinIO 桶私有
- [ ] 环境文件 `0640 root:labelstudio`
- [ ] 应用以 `labelstudio` 用户运行，不用 root
- [ ] 备份在第二块磁盘
- [ ] 系统管理员数量受控
- [ ] 公开注册关闭（学校版默认关闭）
- [ ] 定期检查 `/admin/` 和 nginx 日志

---

## 17. 附录：常用命令

```bash
# 进入项目环境
cd /opt/label-studio/src/label_studio
export $(grep -v '^#' /etc/label-studio/label-studio.env | xargs)

# Django 检查
/opt/label-studio/venv/bin/python manage.py check

# 数据库迁移
/opt/label-studio/venv/bin/python manage.py migrate

# 静态文件
/opt/label-studio/venv/bin/python manage.py collectstatic --noinput

# 创建学院
/opt/label-studio/venv/bin/python manage.py school_create_college cs 计算机学院

# 加成员
/opt/label-studio/venv/bin/python manage.py school_add_member cs user@example.edu.cn --role student

# 演示数据
/opt/label-studio/venv/bin/python manage.py school_seed_demo

# 服务
sudo systemctl restart label-studio
sudo systemctl restart minio
sudo systemctl restart postgresql
sudo systemctl reload nginx
```
