# 学校多租户版架构文档

> 代码基线：`label-studio-edu`（Label Studio `1.24.0.dev0` + `school_tenancy`）
> 文档日期：2026-11-07
> 适用范围：当前 `/app/label_studio_multi_user/label-studio-edu` 代码库

---

## 1. 系统定位

本系统是在 Label Studio 基础上二次开发的高校标注平台，核心目标是：

1. **一个学院 = 一个租户**：学院之间数据、用户、项目、标注完全隔离。
2. **学院内三种角色**：学院管理员、教师、学生。
3. **教师可精细授权学生**：按项目授权功能，按学生 × 项目授权标注模块。
4. **学生可被授权新建项目**：按模板分类授权，而不是默认开放。
5. **学院管理员可查看本学院全部标注数据**；系统管理员可跨学院查看全部数据。

当前系统没有“学校”实体。顶层租户是 **College（学院）**，一个 College 对应一个 Label Studio `Organization`。
如果需要“学校 → 多个学院”的层级，需要新增 `School` 模型并扩展权限，当前版本不支持。

---

## 2. 总体架构

```text
┌──────────────────────────────────────────────────────────────────────┐
│                              浏览器                                  │
│  Label Studio React 前端（项目/数据管理器/设置/标注界面）              │
│  学院工作台 Django 页面（/school/、/school/annotations/）              │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTPS
┌───────────────────────────────▼──────────────────────────────────────┐
│ nginx（生产） / Django runserver（本地）                              │
│  - TLS、静态文件、反向代理                                             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTP / uWSGI
┌───────────────────────────────▼──────────────────────────────────────┐
│ Django 应用                                                          │
│                                                                      │
│  TenantMiddleware ── 校验 active_organization 是否是有效学院           │
│  Authentication / Session                                            │
│  school_tenancy.permissions ── 角色谓词 + API 权限执行 + 外键收口       │
│  QuerySet Scope ── 项目 / 用户 / 模板按学院过滤                        │
│  DRF ViewSet / Django Views                                          │
│                                                                      │
│  核心模型：Organization / Project / Task / Annotation                 │
│  学校模型：College / MemberRole / ProjectTeacher                      │
│            StudentProjectPermission / StudentCreatePermission         │
└───────────────┬───────────────────────────────┬──────────────────────┘
                │                               │
┌───────────────▼──────────────┐  ┌─────────────▼──────────────────────┐
│ PostgreSQL（生产）            │  │ S3 / Garage（生产）                 │
│ 账号、学院、项目、任务、标注   │  │ 上传素材、头像、导出文件             │
│ SQLite（本地演示）            │  │ 本地文件（本地演示）                 │
└──────────────────────────────┘  └────────────────────────────────────┘
```

### 2.1 运行模式

| 模式 | settings | 数据库 | 文件存储 | 用途 |
|---|---|---|---|---|
| 本地演示 | `core.settings.school_local` | SQLite | `LABEL_STUDIO_BASE_DATA_DIR` 本地目录 | 开发、演示、测试 |
| 生产单机 | `core.settings.school` | PostgreSQL | S3 兼容对象存储（Garage/MinIO） | 正式部署 |
| 上游兼容 | `core.settings.label_studio` | 按上游配置 | 按上游配置 | 不启用学校模式 |

> 生产部署必须使用 `core.settings.school`，不能继续使用 `core.settings.label_studio`。

---

## 3. 代码目录

```text
label-studio-edu/
├── label_studio/
│   ├── core/settings/
│   │   ├── school_base.py        # 学校版公共配置
│   │   ├── school_local.py       # 本地 SQLite 模式
│   │   └── school.py             # 生产 PostgreSQL + S3 模式
│   ├── school_tenancy/           # 学校多租户核心应用
│   │   ├── models.py             # College / MemberRole / 授权模型
│   │   ├── access.py             # 当前学院、角色、项目/用户/标注范围
│   │   ├── permissions.py        # 角色谓词、API 权限执行、外键范围
│   │   ├── services.py           # 学院、成员、角色、学生授权业务操作
│   │   ├── middleware.py         # 租户校验中间件
│   │   ├── mixins.py             # 项目对象级权限
│   │   ├── serializers.py        # whoami 学校字段
│   │   ├── signup.py             # 学院邀请链接注册
│   │   ├── template_groups.py    # 新建项目模板分类授权
│   │   ├── views.py / urls.py    # 学院工作台、标注数据页、授权表单
│   │   ├── templates/            # 工作台、无学院提示、标注数据页
│   │   ├── management/commands/  # 建学院、加成员、演示数据
│   │   └── tests/                # 40 项学校版后端测试
│   ├── projects/                 # 上游项目模块（含学校范围钩子）
│   ├── tasks/                    # 上游任务/标注模块
│   ├── data_manager/             # 数据管理器
│   ├── users/                    # 登录、注册、whoami
│   ├── webhooks/                 # Webhook
│   └── orgext/                   # 旧教育版扩展，仅保留模型/迁移兼容
├── web/
│   ├── apps/labelstudio/src/     # 主前端
│   └── libs/core/src/lib/utils/project-permissions.ts
├── deploy/school/                # 学校版部署脚本/模板/测试
├── docs/school-edition/          # 本文档与部署向导
└── ...
```

### 3.1 关于 `orgext`

原教育企业版的 `orgext`（Workspace / Assignment / 角色矩阵）已下线运行期能力：

- `orgext` app 仍保留在 `INSTALLED_APPS`，只为历史迁移和旧表兼容。
- `OrgextConfig.ready()` 为空，不注册权限、不注册分配信号。
- `orgext` 的 API 路由、前端 Workspaces/Assignments 页面已移除。
- `Project.workspace`、`Project.assignment_mode`、`OrganizationMember.role` 字段保留，用于旧库兼容，但学校版不再使用。
- 旧库数据不会自动转换成 `College` / `MemberRole`，需要单独迁移。

---

## 4. 多租户数据模型

### 4.1 核心关系

```text
User ──M:N── OrganizationMember ──1:1── MemberRole
                    │
                    │ organization
                    ▼
              Organization ──1:1── College
                    │
                    ├──1:N── Project ──1:N── Task ──1:N── Annotation
                    │              │
                    │              ├──1:N── ProjectMember ──1:1── StudentProjectPermission
                    │              └──1:N── ProjectTeacher
                    │
                    └──1:N── Webhook / View / ML / Storage ...

OrganizationMember ──1:1── StudentCreatePermission
```

### 4.2 学校版模型

| 模型 | 作用 | 关键字段 |
|---|---|---|
| `College` | 学院租户 | `organization`（OneToOne）、`code`、`is_active` |
| `MemberRole` | 学院内角色 | `member`（OneToOne）、`role`：college_admin / teacher / student |
| `ProjectTeacher` | 项目负责教师 | `project`、`user`，唯一约束 |
| `StudentProjectPermission` | 学生 × 项目功能授权 | `membership`、`annotation_modules`、6 个功能布尔字段 |
| `StudentCreatePermission` | 学生新建项目授权 | `member`、`template_groups`、`granted_by` |

### 4.3 组织与学院

- 每个 `College` 必须有且仅有一个 `Organization`。
- 每个学院有一个**学院系统账号**：`college-<code>@system.invalid`，作为 `Organization.created_by`。
  该账号不能登录、不能改角色、不能移除。
- 系统账号用于满足上游导出、存储同步、Feature Flag 等依赖 `organization.created_by` 的逻辑。

### 4.4 用户与学院

- 一个用户可以属于多个学院。
- `User.active_organization` 表示“当前学院”，一次只能在一个学院工作。
- 当前学院不是永久信任值：`ensure_active_college()` 每次请求校验成员关系、学院是否启用；
  无效时自动修复或返回 `None`。
- 无有效学院的账号会被重定向到 `/school/no-college/`，API 返回 403。

---

## 5. 角色与权限

### 5.1 角色定义

| 角色 | 代码 | 说明 |
|---|---|---|
| 学院管理员 | `college_admin` | 管理本学院成员、全部项目、全部标注数据 |
| 教师 | `teacher` | 管理自己创建或被指定负责的项目 |
| 学生 | `student` | 默认仅标注分配给自己的项目 |

系统管理员不是学院角色，而是 Django `is_superuser`。

### 5.2 权限矩阵

| 能力 | 学院管理员 | 教师 | 学生 |
|---|---|---|---|
| 查看本学院项目 | ✓ 全部 | ✓ 自己创建/负责 | ✓ 分配给自己的 |
| 新建项目 | ✓ | ✓ | 默认 ✗，按模板分类授权 |
| 导入任务 | ✓ | ✓ 负责项目 | 逐项目授权 |
| 导出数据 | ✓ | ✓ 负责项目 | 逐项目授权 |
| 修改项目配置 | ✓ | ✓ 负责项目 | 逐项目授权（自建项目默认全部） |
| 删除项目 | ✓ | ✓ 负责项目 | 逐项目授权（自建项目默认全部） |
| 管理任务/预标注 | ✓ | ✓ 负责项目 | 逐项目授权 |
| 标注 | ✓ | ✓ 负责项目 | 默认允许，可逐项目关闭 |
| 标注模块 | 不限制 | 不限制 | 按学生 × 项目授权文本/图像/音频/视频等 |
| 修改/删除他人标注 | ✓ | ✓ 负责项目 | ✗ |
| 成员与角色管理 | ✓ | ✗ | ✗ |
| 指定项目负责教师 | ✓ | ✗ | ✗ |
| 用户列表 `/api/users` | 本学院全部 | 本学院全部（只读） | 仅自己 |
| 全学院标注数据 `/school/annotations/` | ✓ 本学院 | ✗ | ✗ |
| 全部学院标注数据 | ✗ | ✗ | ✗ |

系统管理员（superuser）：

- 拥有全部权限谓词。
- `/school/` 自动跳转到 `/school/annotations/`。
- `/school/annotations/` 可查看全部学院，并可按 `college` 筛选。
- Django 后台 `/admin/` 可管理全部数据。

### 5.3 权限实现

`school_tenancy/permissions.py` 做三件事：

1. **角色谓词注册**
   - 遍历 `core.permissions.all_permissions`，把每个权限点绑定到学校角色谓词。
   - 所有权限点都叠加 `rules.is_superuser`，保证系统管理员全通过。
   - 额外注册 `annotations.view_all`：学院管理员和系统管理员可用。

2. **API 权限统一执行**
   - 社区版只声明 `permission_required` 但不执行。
   - 这里 monkey-patch `APIView.check_permissions` / `check_object_permissions`，统一执行：
     - 无有效学院 → 403（superuser 除外）
     - 项目功能校验（导入/导出/标注/配置/删除/任务管理）
     - 学生标注模块校验
     - 学生模板分类校验
     - 学生只能改自己的标注/草稿
     - URL/query/body 中的项目、任务、标注、视图 ID 收口

3. **序列化器外键收口**
   - 把所有指向 Project / Task / Annotation 的 DRF `RelatedField` queryset 限制在用户可访问范围，
     防止通过 id 写入其他学院的数据。

### 5.4 项目可见性

`access.scope_projects()`：

```text
学院管理员：本学院全部项目
教师：      created_by = 自己 OR 被 ProjectTeacher 指定
学生：      created_by = 自己 OR ProjectMember.enabled = True
其他：      空
```

`access.can_access_project()` 是对象级对应实现。

### 5.5 项目功能授权

学生默认功能：`annotate`。

教师/学院管理员可按“学生 × 项目”设置：

| 功能 | 代码 | 说明 |
|---|---|---|
| 标注 | `annotate` | 创建/修改/删除自己的标注 |
| 导入 | `import_tasks` | 导入素材和任务 |
| 导出 | `export_data` | 导出标注数据 |
| 任务管理 | `manage_tasks` | 修改/删除任务、管理预标注 |
| 项目配置 | `configure_project` | 修改标题、描述、标注配置 |
| 删除项目 | `delete_project` | 删除项目 |

学生自建项目：默认拥有全部项目功能，但仍不是教师，不能管理模型/存储/Webhook/成员。

### 5.6 标注模块授权

模块来自项目标注配置中的控件标签与实际数据标签：

| 模块 | 数据标签 |
|---|---|
| `text` | Text、HyperText、Paragraphs、Table、Markdown |
| `image` | Image |
| `audio` | Audio、AudioPlus |
| `video` | Video |
| `timeseries` | TimeSeries |
| `other` | 以上之外 |

- `StudentProjectPermission.annotation_modules = None` 表示全部模块（默认，包含以后新增的模块）。
- 取消模块后：
  - 后端裁剪 `school_label_config`，前端看不到对应控件和只包含它们的 `<View>`。
  - 提交标注/草稿时如果包含未授权 `from_name`，返回 403。
- 模块全部取消时，学生在该项目不能标注。

### 5.7 学生新建项目授权

- `StudentCreatePermission.template_groups` 保存授权的模板分类（Labeling Setup 左侧分类）。
- 支持 `CUSTOM_TEMPLATE`，允许学生写任意配置。
- 默认无授权 → 学生没有 `projects.create`，前端不显示 Create。
- 有授权后：
  - `whoami.permissions` 出现 `projects.create`。
  - `/api/templates/` 只返回授权分类。
  - 新建/修改自己项目的 `label_config` 必须属于授权分类的模板签名。
- 授权教师自动成为学生自建项目的负责教师。
- 学生自建项目仅本人、授权教师、学院管理员可见。

---

## 6. 请求处理流程

### 6.1 普通 API 请求

```text
Request
  │
  ├─ TenantMiddleware
  │    ensure_active_college(user)
  │    ├─ 有效学院 → 继续
  │    ├─ superuser + /admin/、/school/、/api/current-user/ → 继续
  │    ├─ API → 403
  │    └─ 页面 → /school/no-college/
  │
  ├─ Authentication / Session
  │
  ├─ DRF APIView.check_permissions（学校版增强）
  │    ├─ 原 DRF 权限
  │    ├─ 无学院检查（superuser 除外）
  │    ├─ required_permission → user.has_perm
  │    ├─ required_feature → 项目功能
  │    └─ request_projects → 逐个校验项目访问/功能/模块/模板
  │
  ├─ ViewSet.get_queryset
  │    └─ Project.objects.for_user / Task.objects.for_user / scope_*
  │
  ├─ check_object_permissions
  │    ├─ 项目访问
  │    └─ 学生只能改自己的标注/草稿
  │
  └─ Response
```

### 6.2 学院工作台请求

```text
GET /school/
  └─ workbench()
       ├─ 确定当前学院、角色
       ├─ 教师/管理员：项目、学生、功能授权、模块授权、负责教师
       ├─ 学生：我的项目、已授权功能、已授权模板分类
       └─ 学院管理员：成员管理、邀请链接、所有标注数据入口
```

### 6.3 全学院标注数据

```text
GET /school/annotations/
  ├─ 权限：annotations.view_all
  ├─ 学院管理员：project.organization = 当前学院
  ├─ 系统管理员：全部学院，可按 college 筛选
  ├─ 教师/学生：403
  └─ 支持 ?format=json、project、annotator、college、page
```

---

## 7. 关键页面与接口

### 7.1 页面

| 路径 | 说明 |
|---|---|
| `/` | Label Studio 主入口 |
| `/user/login/` | 登录 |
| `/user/signup/?token=...` | 学院邀请注册（仅邀请链接，公开注册关闭） |
| `/school/` | 学院工作台 |
| `/school/no-college/` | 无有效学院提示 |
| `/school/annotations/` | 全学院/全局标注数据 |
| `/projects/` | 项目列表 |
| `/projects/<id>/data` | 数据管理器 |
| `/projects/<id>/settings` | 项目设置 |

### 7.2 学校版接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/school/me/` | 当前学院信息 |
| POST | `/api/school/switch-college/` | 切换当前学院 |
| GET | `/school/annotations/?format=json` | 标注数据 JSON |
| POST | `/school/members/` | 添加成员 |
| POST | `/school/members/<id>/role/` | 修改角色 |
| POST | `/school/members/<id>/remove/` | 移出学院 |
| POST | `/school/members/<id>/create-templates/` | 学生新建项目模板授权 |
| POST | `/school/projects/<id>/students/` | 分配学生 |
| POST | `/school/projects/<id>/students/<uid>/features/` | 保存学生功能/模块权限 |
| POST | `/school/projects/<id>/students/<uid>/remove/` | 取消分配 |
| POST | `/school/projects/<id>/teachers/` | 指定/取消负责教师 |

### 7.3 上游接口的学校版增强

| 接口/模块 | 增强 |
|---|---|
| `GET /api/projects/`、`/api/projects/<id>/` | 项目范围过滤；返回 `school_features`、`school_can_manage`、`school_label_config` |
| `POST /api/projects/` | 学生新建项目需模板分类授权；配置校验 |
| `PATCH /api/projects/<id>/` | 不能修改所属学院/创建者；配置/功能权限校验 |
| `/api/tasks/` | 不能按 id 写入其他学院项目 |
| `/api/tasks/<id>/annotations/` | 项目访问 + 学生标注模块校验 |
| `/api/users/` | 用户范围过滤 |
| `/api/templates/` | 学生只看到授权分类 |
| `/api/dm/` | 视图按项目范围过滤；项目状态返回真实权限 |
| `/api/webhooks/` | 教师只看自己创建/负责项目的 Webhook |
| `/api/current-user/whoami` | 返回 `school_role`、`annotations.view_all` |

---

## 8. 前端架构

### 8.1 项目权限判断

`web/libs/core/src/lib/utils/project-permissions.ts`：

```ts
hasProjectFeature(project, feature)
  - project.school_features 不是数组 → 社区版，全部允许
  - 是数组 → 判断是否包含 feature

canManageProject(project)
  - project.school_can_manage !== false
```

### 8.2 主要改动

| 文件 | 作用 |
|---|---|
| `components/Menubar/Menubar.jsx` | 学院工作台 / 系统数据入口 |
| `pages/Home/HomePage.tsx` | 按 `projects.create` / `organizations.invite` 显示新建/邀请 |
| `pages/Projects/Projects.jsx` | 请求 `school_features`、`school_can_manage` |
| `pages/Projects/ProjectsList.jsx` | 项目卡片菜单按功能权限显示 |
| `pages/DataManager/DataManager.jsx` | 导入/导出/标注/设置入口拦截；学生授权入口 |
| `pages/Settings/index.jsx` | 设置菜单按功能权限过滤；越权跳转 |
| `pages/Settings/DangerZone.jsx` | 删除项目按权限显示 |
| `pages/CreateProject/Config/TemplatesList.jsx` | 学生只显示授权模板分类 |
| `libs/datamanager/src/sdk/lsf-sdk.js` | 无标注权限时只读 |
| `libs/datamanager/src/stores/AppStore.js` | 轮询携带学校字段；使用裁剪后的配置 |

### 8.3 工作台

学院工作台是 Django 模板页面，不依赖 React 构建：

- 学院管理员/教师：项目与学生分配、学生功能/模块授权、新建项目模板授权、成员管理、负责教师。
- 学生：我的项目、已授权功能、已授权模块、可新建的模板分类。
- 学院管理员：邀请链接、所有标注数据入口。

---

## 9. 配置与扩展点

### 9.1 学校版 settings 覆盖

`school_base.py` 覆盖的关键配置：

| 配置 | 值 |
|---|---|
| `INSTALLED_APPS` | 追加 `school_tenancy` |
| `MIDDLEWARE` | 追加 `TenantMiddleware`、活动时间、会话超时 |
| `ORGANIZATION_MIXIN` | OSS 实现 |
| `USER_MIXIN` | OSS 实现 |
| `ORGANIZATION_MEMBER_MIXIN` | OSS 实现 |
| `PROJECT_MIXIN` | `school_tenancy.mixins.SchoolProjectMixin` |
| `PROJECT_QUERYSET_SCOPE` | `school_tenancy.access.scope_projects` |
| `USER_QUERYSET_SCOPE` | `school_tenancy.access.scope_users` |
| `TEMPLATE_LIST_SCOPE` | `school_tenancy.template_groups.scope_templates` |
| `SAVE_USER` | `school_tenancy.signup.save_user` |
| `MEMBER_PERM` | `school_tenancy.permissions.CollegeAdminWritePermission` |
| `WHOAMI_USER_SERIALIZER` | `school_tenancy.serializers.SchoolWhoAmISerializer` |
| `DATA_MANAGER_CHECK_ACTION_PERMISSION` | `school_tenancy.permissions.check_action_permission` |
| `DISABLE_SIGNUP_WITHOUT_LINK` | `True` |

### 9.2 上游扩展点

`core/settings/base.py` 新增：

- `PROJECT_QUERYSET_SCOPE = None`
- `USER_QUERYSET_SCOPE = None`
- `TEMPLATE_LIST_SCOPE = None`

未启用学校 settings 时保持 `None`，行为与上游一致。

---

## 10. 隔离边界与已知限制

### 10.1 已隔离

- 跨学院项目、任务、标注、导出、导入、Webhook、用户列表。
- 跨学院通过 id 写入。
- 跨学院标注数据总览。
- 学院停用/移出成员后的下一次请求。
- 邀请链接只能注册到对应学院。

### 10.2 已知限制

1. **没有学校层级**：College 是最高租户；同一学校多个学院之间也互相隔离，没有校级管理员统一视图。
2. **同项目学生可见同学标注**：学生可以读取同项目/同任务的其他学生标注，但不能修改/删除。
   如果要做防抄袭式“学生完全互不可见”，需要额外改标注查询。
3. **旧 orgext 数据不自动迁移**：已有 Organization 需要建立 College、写入 MemberRole。
4. **生产 install.sh 未完成**：详见部署向导。
5. **Django 后台系统管理员**：`/admin/` 可看全部，但普通 API 项目列表仍受当前学院约束；全量标注请用 `/school/annotations/`。

---

## 11. 测试与验证

| 测试 | 命令/范围 | 当前结果 |
|---|---|---|
| 学校版后端 | `pytest school_tenancy/tests` | 40 passed |
| 部署脚本测试 | `pytest deploy/school/tests` | 10 passed, 14 subtests |
| 上游核心回归 | project/org/invite/projects/users/organizations | 84 passed |
| 上游扩展回归 | endpoints/api/annotations/export/next_task/tasks_upload/data_manager/webhooks | 196 passed, 53 skipped |
| 前端权限 | `jest project-permissions.test.ts` | 3 passed |
| 前端构建 | `nx build labelstudio --configuration=development` | 成功 |
| Django check | school + 默认 settings | 无问题 |

---

## 12. 后续建议

1. 增加 `School` 模型，支持“学校 → 多个学院”。
2. 增加校级管理员角色，跨学院只读/审计。
3. 将 orgext 旧数据迁移做成管理命令（Organization → College、role → MemberRole）。
4. 学生标注互相隔离（按需）。
5. 生产部署补齐 `install.sh` 缺失的运维脚本，并在 Ubuntu 上做真实演练。
6. 增加审计日志、配额、课程/教学班、作业截止与成绩导出。
