# deploy/school —— 学校版部署与本地运行

## 本地运行（当前可用）

Windows 本机，使用 SQLite 加本地文件存储，仅用于开发和演示：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\school\local\setup-local.ps1 -WithTests   # 首次
powershell -ExecutionPolicy Bypass -File deploy\school\local\run-local.ps1 -Seed          # 启动并创建演示数据
```

打开 <http://127.0.0.1:8080/>（演示账号密码 `Demo@2026`）和学院工作台 <http://127.0.0.1:8080/school/>。
多租户的设计、权限规则和测试见 [school_tenancy/README.md](../../label_studio/school_tenancy/README.md)。
教师在学院工作台按学生和项目设置功能，学生默认仅可标注。前端现已从源码构建，会隐藏未授权的业务入口；首次安装需要 Node/npm。

## 单台 Ubuntu 服务器部署（进行中，尚不可用）

目标形态：一台 Ubuntu 22.04/24.04 服务器，全部原生安装、不使用 Docker。

| 组件 | 方案 | 存放内容 |
|------|------|----------|
| PostgreSQL（PGDG 官方源） | Unix socket + 操作系统用户认证，只监听本机 | 账号、学院、项目、任务、学生标注结果 |
| Garage v2.4.1（固定版本，SHA-256 校验） | S3 接口，只监听 127.0.0.1 | 上传素材、头像、导出文件 |
| Label Studio（uWSGI + systemd） | `core.settings.school` | 应用本身，不保存业务数据 |
| nginx | HTTPS 入口 | — |
| systemd 定时任务 | 每日备份到**第二块独立磁盘** | 数据库转储 + 对象存储镜像（只增不删） |

已有文件：

- [install.sh](install.sh)、[school.conf.example](school.conf.example)：安装脚本初稿，支持 `--render-only` 模式，只生成配置文件供审阅。
- `templates/`、`systemd/`：Garage、PostgreSQL、uWSGI、nginx 的配置模板和服务单元。
- [bin/objectstore_backup.py](bin/objectstore_backup.py)：对象存储备份和恢复工具，已用模拟 S3 测试。
- `tests/`：生产配置测试和备份工具测试。

**尚未完成**：

- `install.sh` 引用的 `bin/` 运维脚本（`label-studio-manage`、`backup.sh`、`restore.sh`、`check.sh`、`create-admin.sh`）还没有写；
- 安装脚本仍按 master 的 uv 工作流编写，需要改为 1.23.2 的依赖和定制前端源码构建；
- 还没有在真实 Ubuntu 上做过安装、Garage 联调和备份恢复演练。

完成并验证之前，请不要用于生产数据。

## 测试

```powershell
.venv\Scripts\python -m unittest discover -s label-studio\deploy\school\tests -p "test_*.py"
```
