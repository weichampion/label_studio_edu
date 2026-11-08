# 学校版演示账号

> 更新时间：2026-11-07
> 演示地址：`http://192.168.1.17:8094/`
> 统一密码：`Demo@2026`

## 账号列表

| 用户名 | 身份 | 所属学院 |
|---|---|---|
| `cs.admin@demo.edu.cn` | 学院管理员 | 计算机学院 |
| `cs.teacher@demo.edu.cn` | 教师 | 计算机学院 |
| `cs.student1@demo.edu.cn` | 学生 | 计算机学院 |
| `cs.student2@demo.edu.cn` | 学生 | 计算机学院 |
| `med.admin@demo.edu.cn` | 学院管理员 | 医学院 |
| `med.teacher@demo.edu.cn` | 教师 | 医学院 |
| `med.student1@demo.edu.cn` | 学生 | 医学院 |
| `shared.teacher@demo.edu.cn` | 教师 | 计算机学院 + 医学院 |
| `platform.admin@demo.edu.cn` | 系统管理员 | 不属于任何学院 |

所有 demo 账号密码均为 `Demo@2026`，已逐一验证。

## 学生新建项目授权

`cs.student1@demo.edu.cn` 当前被 `cs.admin@demo.edu.cn` 授权：

- Audio/Speech Processing
- Chat
- Computer Vision
- Conversational AI
- Natural Language Processing

因此该学生登录后会看到 Create 按钮，Labeling Setup 只显示以上分类。
未授权 Custom template；`cs.student2` 和 `med.student1` 当前没有新建项目授权。

## 标注数据权限

| 账号 | 可见范围 |
|---|---|
| `cs.admin@demo.edu.cn` | 计算机学院全部标注 |
| `med.admin@demo.edu.cn` | 医学院全部标注 |
| `platform.admin@demo.edu.cn` | 全部学院全部标注 |
| 教师 / 学生 | `/school/annotations/` 返回 403 |

全局标注页面：`http://192.168.1.17:8094/school/annotations/`

当前演示数据：计算机学院 2 条标注，医学院 1 条标注，共 3 条。

## 其他

- `platform.admin@demo.edu.cn` 仅用于 `/admin/` 和全量标注数据。
- `shared.teacher@demo.edu.cn` 属于两个学院，可在工作台切换。
- 学院系统账号 `college-cs@system.invalid`、`college-med@system.invalid` 不能登录。
- 旧教育版账号 `teacher@edu.test`、`student@edu.test`、`rev@edu.test` 没有加入任何学院，
  学校模式下不可用。
- 代码目录：`/app/label_studio_multi_user/label-studio-edu`
- GitHub：`https://github.com/weichampion/label_studio_edu`
