# school_tenancy —— 学院多租户（本地可运行版）

> **当前仓库状态（合并后）**
>
> 本模块已合并进 `label-studio-edu`（1.24.0.dev0）。原 `orgext` 的 Workspace / Assignment /
> 角色矩阵已下线：`orgext` app 仅保留模型和历史迁移，用于兼容已有数据库；运行期权限、项目可见性、
> 成员范围和前端入口全部由 `school_tenancy` 接管。默认的 `core.settings.label_studio` 不启用学校模式，
> 学校部署必须使用 `core.settings.school_local`（本地）或 `core.settings.school`（服务器）。
>
> 旧的 `orgext` 数据不会自动转换成学院：已有 Organization 需要先建立对应 `College` 并给成员写入
> `MemberRole`，否则学校模式下这些组织会被视为“未加入学院”。

基于 Label Studio **1.23.2**（正式版）二次开发：**一个学院 = 一个租户**（复用上游 `Organization`），
在学院内区分 **学院管理员 / 教师 / 学生** 三种角色。

## 本地运行（Windows，SQLite + 本地文件）

```powershell
# 首次：创建虚拟环境、安装依赖、用 Node/npm 构建定制前端（无需 uv / bun / Docker）
powershell -ExecutionPolicy Bypass -File deploy\school\local\setup-local.ps1 -WithTests
# 启动（-Seed 创建演示学院和账号，可重复执行）
powershell -ExecutionPolicy Bypass -File deploy\school\local\run-local.ps1 -Seed
```

- 标注平台：<http://127.0.0.1:8080/>　学院工作台：<http://127.0.0.1:8080/school/>
- 数据：`<工作区>\local-data`（SQLite 数据库 `label_studio.sqlite3` 和上传文件），仅用于本地开发。
- 配置：`core.settings.school_local`；服务器部署使用 `core.settings.school`（PostgreSQL + 对象存储）。

演示账号（密码均为 `Demo@2026`）：

| 账号 | 身份 |
|------|------|
| cs.admin@demo.edu.cn | 计算机学院 学院管理员 |
| cs.teacher@demo.edu.cn | 计算机学院 教师 |
| cs.student1@demo.edu.cn | 计算机学院 学生（分配：作业1、作业3） |
| cs.student2@demo.edu.cn | 计算机学院 学生（分配：作业1、作业2、作业3） |
| med.admin / med.teacher / med.student1 @demo.edu.cn | 医学院 对应角色（med.student1 分配：问诊意图标注） |
| shared.teacher@demo.edu.cn | 计算机学院和医学院的教师，用于演示切换学院 |
| platform.admin@demo.edu.cn | 平台管理员（Django 后台 `/admin/`，不属于任何学院，看不到学院数据） |

## 隔离与权限规则

| | 学院管理员 | 教师 | 学生 |
|---|---|---|---|
| 看到的项目 | 本学院全部 | 自己创建或指定负责的项目 | 仅分配给自己的 |
| 新建项目 | ✓ | ✓ | 默认无；教师可按模板分类授权，自建项目仅本人、授权教师和学院管理员可见 |
| 导入、导出、修改配置、删除项目 | ✓ | ✓（仅负责的项目） | 默认无，负责教师可逐项目授权 |
| 存储、ML、Webhook、成员授权 | ✓ | ✓（仅项目级，不能管理学院成员） | ✗，项目功能授权不等于教师角色 |
| 标注（创建/修改自己的标注） | ✓ | ✓（仅负责的项目） | 默认有，可逐项目关闭 |
| 修改/删除他人的标注和草稿 | ✓ | ✓ | ✗ |
| 用户列表 `/api/users` | 本学院全部 | 本学院全部（只读） | 仅自己 |
| 成员与角色管理、邀请链接 | ✓ | ✗ | ✗ |
| 其他学院的任何数据 | ✗ | ✗ | ✗ |

- 用户可以属于多个学院，但**一次只在一个学院中工作**。在工作台中切换学院，或调用 `POST /api/school/switch-college/`。
- 学院停用、成员被移出后，相应访问立即失效。没有有效学院的账号会被引导到 `/school/no-college/`，API 返回 403。
- 学院邀请链接（`/user/signup/?token=…`）注册的账号只会成为该学院的学生。

### 教师端配置学生功能

1. 教师登录后进入 `/school/`，或点击标注平台顶部“学院工作台”“学生功能授权”。
2. 在自己创建或负责的项目下分配学生；默认只勾选“标注”。
3. 对每个学生分别勾选并保存：**标注、导入素材和任务、导出数据、管理任务和预标注、修改项目配置、删除项目**。
4. 学生刷新页面后，导入/导出、设置、删除项目和标注入口按该项目的权限显示。取消全部勾选意味着仅可查看；标注选择控件只读，不显示提交按钮。
5. 学院管理员可以在同一页面指定或取消项目负责教师。普通教师不能给自己或别人增加项目管理权。

工作台是独立的 Django 页面，前端的“学院工作台”“学生功能授权 / 我的功能权限”链接使用 `data-external` 退出异步页面导航，避免点击后出现 `innerHTML` 运行时错误。学校版顶部为工作台入口保留宽度，并裁剪过长的面包屑，避免窄窗口下入口竖排或被遮挡。

授权以“学生 × 项目”保存，不影响同一个学生的其他项目或其他学生。未授权接口一律拒绝，撤权对下一次请求立即生效，不依赖学生是否刷新界面。
标注功能只允许改删自己的答案；批量改删同学答案不会随该授权开放。任务管理和删除项目具有破坏性，应谨慎勾选。
本次演示验证后已将 `cs.student1@demo.edu.cn` 的作业1权限恢复为默认“仅标注”。

### 标注模块权限（文本 / 图像 / 音频 / 视频 …）

同一表单下方的“可使用的标注模块”只列出该项目标注配置中实际包含的模块，按学生 × 项目保存：

| 模块 | 对应的数据（对象）标签 |
|---|---|
| 文本标注 | `Text`、`HyperText`、`Paragraphs`、`Table`、`Markdown` |
| 图像标注 | `Image` |
| 音频标注 | `Audio`、`AudioPlus` |
| 视频标注 | `Video` |
| 时间序列标注 | `TimeSeries` |
| 其他标注 | 以上之外的数据标签 |

- **默认允许全部模块**；全部勾选时保存为“不限制”，以后项目新增的模块也自动可用。
- 取消某个模块后，学生打开任务时看不到该模块的数据标签、标注控件，以及只包含它们的 `<View>` 分组（例如分组标题）。
- 服务器同时校验：学生提交、修改标注或保存草稿时，如果结果包含未授权模块的控件（`from_name`），返回 403。
- 一个模块都不勾选时，学生在该项目中不能标注（等同于取消“标注”功能）。教师和学院管理员不受模块限制。
- 一个控件关联多个数据标签时，需要授权它涉及的全部模块。
- 演示项目“作业3：图文多模态标注”（#4）同时包含文本和图像，可用于体验：教师取消 student1 的“文本标注”后，student1 只能框选图片。演示后已恢复为全部模块。

### 学生新建项目（按模板分类授权）

学生默认**不能**新建项目，只标注教师分配的项目。教师或学院管理员可以在工作台“学生新建项目权限”中，按学生勾选“新建项目 → Labeling Setup”**左侧的模板分类**（Computer Vision、Natural Language Processing、Audio/Speech Processing、Videos 等），以及可选的“自定义模板”：

- 勾选后，学生的首页和项目页出现 Create 按钮；Labeling Setup 左侧**只显示已授权的分类**。未授权“自定义模板”时不显示 Custom template 按钮。自己项目的 Settings → Labeling Interface → Browse Templates 也一样。
- 服务器同时校验：新建项目或修改自己项目的标注配置时，配置的数据标签（图像、文本、音频等）必须和已授权分类中某个模板一致，标注工具也必须来自这些模板。改标签值、名称和布局不受影响。直接调用接口提交其他分类的配置会返回 403（“此标注配置不属于教师授权的模板分类”）。例如只授权 Computer Vision 时，不能把配置改成文本分类或命名实体识别。
- 少数不同分类的模板结构完全相同，服务器无法区分，会被视为同一类配置，例如 Ranking & Scoring 中的文本分类和 Natural Language Processing 中的文本分类。界面上仍只显示已授权的分类。
- 授权“自定义模板”后，学生可以写任意标注配置。
- 学生自建项目只有**本人、授权教师和学院管理员**可见；同学和其他学院不可见。授权教师自动成为这些项目的负责教师（授权人是学院管理员时不额外指定，管理员本来就能看到全院项目）。
- 学生在自己的项目里拥有导入、导出、任务管理、修改配置、删除项目等全部项目功能，但仍不是教师：看不到模型、存储、Webhook 设置，也不能分配同学或给别人授权。
- 取消全部分类即撤销授权：不能再新建，已建项目仍归本人所有。学生被移出学院或改为其他角色时，授权自动清除。
- 授权按学院保存，本学院任一教师都可修改；最后保存的人记为授权人。

## 实现要点

- `models.py`：`College`（学院）、`MemberRole`（学院角色）、`ProjectTeacher`（负责教师）、`StudentProjectPermission`（每个项目成员的功能授权，未配置时默认仅标注；`annotation_modules` 为空值表示全部标注模块）、`StudentCreatePermission`（学生可用于新建项目的模板分类及授权人）。
- `template_groups.py`：模板分类列表、学生可见模板的过滤（`TEMPLATE_LIST_SCOPE` 钩子）和标注配置是否属于已授权分类的校验。
- `signals.py`：学生新建项目后，把授权教师设为该项目负责教师。
- `access.py`：校验并修复当前学院、按角色计算项目和用户的可见范围。结果缓存在每次请求的用户对象上。
- `middleware.TenantMiddleware`：替换上游的 `DummyGetSessionMiddleware`（它会把所有人都放进第一个组织）。
- `permissions.py`：
  - 为上游每个权限名注册角色谓词（基于 `rules`）。
  - **社区版只声明 `permission_required`、从不执行**，这里在 `APIView.check_permissions` 中统一执行；在对象级检查中补充“学生只能修改自己的标注和草稿”。
  - 把序列化器中指向项目、任务、标注的外键字段限制在可访问范围内，防止通过 id 写入其他学院的数据。
  - 解析 URL、查询参数、请求体中的项目/任务引用，再逐个检查该项目的功能授权；批量操作与下一任务、预标注删除也不能绕过。
- `mixins.SchoolProjectMixin`：项目对象级权限，任务、标注、导出、文件下载等都通过它判断。
- `services.py`：学院、成员、角色、学生分配的增删改，包含“不能移除学院唯一管理员”等保护。
- 每个学院有一个**学院系统账号**（`college-<代码>@system.invalid`，无法登录），作为组织创建人。上游的导出和存储同步依赖 `organization.created_by`。
- 管理命令：`school_create_college`、`school_add_member`、`school_seed_demo`。

### 对上游代码的改动（均标注 `SCHOOL-TENANCY`，未启用学校配置时行为不变）

| 文件 | 改动 |
|------|------|
| `core/settings/base.py` | 新增钩子 `PROJECT_QUERYSET_SCOPE`、`USER_QUERYSET_SCOPE`（默认 `None`） |
| `projects/models.py`、`projects/api.py` | 项目列表、计数、详情和 `for_user` 都经过项目范围钩子 |
| `data_manager/api.py` | 视图按项目过滤；项目状态携带真实请求上下文，不再向学生返回写死为 True 的管理能力 |
| `projects/serializers.py` | 学校版返回项目功能与是否可授权，以及按学生模块权限裁剪后的 `school_label_config`；修改项目配置不能变更所属学院或创建者 |
| `webhooks/api.py` | 教师只能查询自己创建或负责项目的 Webhook；学院管理员保留全院范围 |
| `tasks/api.py` | **修复**：`POST /api/tasks/` 原本可以按 id 写入任意项目 |
| `users/api.py` | 用户列表允许成员读取，并经过用户范围钩子缩小结果 |
| `users/views.py` | 注册按邀请码匹配学院（原来只认第一个组织）；登录不再强制切到第一条成员关系 |
| `core/urls.py` | 安装了 `school_tenancy` 应用时挂载工作台路由 |

## 测试

```powershell
cd label-studio\label_studio
$env:LABEL_STUDIO_BASE_DATA_DIR = "$PWD\..\..\local-test-data"
..\..\.venv\Scripts\python -m pytest school_tenancy/tests --ds=core.settings.school_local --reuse-db -q
```

共 35 项后端测试，覆盖原有隔离规则及按学生/项目授权、撤权、只读模式、创建教师与指定教师范围、越权授权拒绝、配置不能移动学院、数据管理器状态权限，标注模块的识别、界面配置裁剪、提交/草稿/修改校验和教师表单保存，以及学生按模板分类新建项目、模板列表过滤、自定义模板、自建项目可见范围和授权撤销等。
另有 3 项前端权限辅助函数测试，源码 webpack 构建通过；浏览器验证了教师勾选导出后学生显示导出入口、撤权后接口 403，以及只读模式无提交入口。
页面回归还包括教师和学生从顶部入口进入工作台、项目内授权入口跳转、620px 窗口下长面包屑不遮挡工作台入口，以及学生标注提交和获授权后的项目配置保存。测试新增的标注已清理，演示学生恢复为仅标注权限。
请在大写盘符路径（如 `C:\...`）下运行 pytest，小写盘符会导致 conftest 被重复注册。

### 整体回归（2026-10-27）

| 测试 | 结果 |
|---|---|
| 学校版后端 + 部署脚本测试（`school_tenancy/tests`、`deploy/school/tests`） | 45 项通过（含学生按模板分类建项目 7 项） |
| 上游 Label Studio 回归子集（原版设置，未启用学校模块） | 319 项通过，75 项按上游条件跳过 |
| 前端权限辅助函数（jest） | 3 项通过 |
| 端到端冒烟（`deploy/school/tests/e2e_smoke.py`，各角色真实 HTTP 流程） | 165 项通过（含学生按模板分类建项目 19 项） |
| 浏览器界面（学生/教师/学院管理员/多学院教师） | 通过 |

本轮修复：
- 无效邀请码注册（未开启“仅邀请注册”时）会被接受 → 改为 403，由上游 `test_invites` 发现，已补充学校版测试。
- 学生直接访问 `/projects/<id>/data/export` 出现运行时错误、访问 `/data/import` 能打开导入弹窗 → 按项目功能权限拦截并跳回数据页；导出页在接口失败时不再崩溃。
- 部署测试兼容仓库锁定的 moto 4.x 与 moto 5。

上游测试需要测试依赖（版本以 `poetry.lock` 为准，moto 需为 4.2.7）：

```powershell
..\..\.venv\Scripts\python -m pip install "mock>=5.1.0" "fakeredis~=2.26.1" "freezegun~=1.5.1" "factory-boy>=3.3.3,<4" "pytest-xdist==3.6.1" "moto==4.2.7"
$env:LABEL_STUDIO_BASE_DATA_DIR = "$PWD\..\..\upstream-test-data"
..\..\.venv\Scripts\python -m pytest tests/test_endpoints.py tests/test_api.py tests/test_project.py tests/test_annotations.py tests/test_export.py tests/test_next_task.py tests/test_organizations.py tests/test_invites.py tests/test_tasks_upload.py projects tasks data_manager users webhooks organizations -n 4 -q --ds=core.settings.label_studio
```

端到端冒烟测试会创建账号、项目和标注，必须在数据副本上运行：

```powershell
Copy-Item ..\..\local-data ..\..\e2e-data -Recurse   # 可排除 backups 目录
powershell -ExecutionPolicy Bypass -File ..\deploy\school\local\run-local.ps1 -DataDir ..\..\e2e-data -Address 127.0.0.1:8090
..\..\.venv\Scripts\python ..\deploy\school\tests\e2e_smoke.py http://127.0.0.1:8090
```

前端修改后要重新构建，不能重新解压官方 wheel 覆盖定制界面：

```powershell
cd web
$env:NX_DAEMON = 'false'
$env:BUILD_NO_SERVER = 'true'
node node_modules\nx\bin\nx.js build labelstudio --configuration=development
node node_modules\jest\bin\jest.js --config libs\core\jest.config.ts --runInBand --runTestsByPath libs\core\src\lib\utils\project-permissions.test.ts
```

依赖缺失时按项目清单安装：`npm.cmd install --ignore-scripts --legacy-peer-deps --package-lock=false --no-audit --no-fund`。
此处使用原项目的 JavaScript/Babel/webpack 构建链，不运行本机策略禁止的 esbuild、uv 或 bun 程序。

## 尚未实现（后续阶段）

- LDAP 登录与学院、角色属性映射；课程/教学班层级；作业截止、审核、评分、成绩导出。
- 同一项目内的学生仍可**读取**同学的标注（例如任务详情会返回所有标注），但不能修改或删除。完全互不可见属于教学阶段的防抄袭工作；学生也可以通过 `organizations.view` 读取本学院成员列表。
- 学院级配额、审计日志。
