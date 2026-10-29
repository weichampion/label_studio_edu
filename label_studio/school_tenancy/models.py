from django.db import models


class Role(models.TextChoices):
    COLLEGE_ADMIN = 'college_admin', '学院管理员'
    TEACHER = 'teacher', '教师'
    STUDENT = 'student', '学生'


MANAGER_ROLES = (Role.COLLEGE_ADMIN, Role.TEACHER)


class College(models.Model):
    """A college (学院) is one tenant; it extends exactly one Label Studio organization."""

    organization = models.OneToOneField(
        'organizations.Organization', on_delete=models.CASCADE, related_name='college', verbose_name='组织'
    )
    code = models.SlugField('学院代码', max_length=32, unique=True)
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '学院'
        verbose_name_plural = '学院'
        ordering = ['code']

    def __str__(self):
        return f'{self.organization.title}（{self.code}）'


class MemberRole(models.Model):
    """Role of an organization member inside its college. Members without a row are students."""

    member = models.OneToOneField(
        'organizations.OrganizationMember', on_delete=models.CASCADE, related_name='school_role', verbose_name='成员'
    )
    role = models.CharField('角色', max_length=32, choices=Role.choices, default=Role.STUDENT)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '成员角色'
        verbose_name_plural = '成员角色'

    def __str__(self):
        return f'{self.member.user.email} @ {self.member.organization.title}: {self.get_role_display()}'


PROJECT_FEATURES = (
    ('annotate', '标注（提交、修改和删除自己的标注）'),
    ('import_tasks', '导入素材和任务'),
    ('export_data', '导出标注数据'),
    ('manage_tasks', '修改、删除任务和管理预标注'),
    ('configure_project', '修改项目名称和标注配置'),
    ('delete_project', '删除项目（危险操作）'),
)


ANNOTATION_MODULES = (
    ('text', '文本标注'),
    ('image', '图像标注'),
    ('audio', '音频标注'),
    ('video', '视频标注'),
    ('timeseries', '时间序列标注'),
    ('other', '其他标注'),
)
MODULE_OBJECT_TYPES = {
    'text': {'text', 'hypertext', 'paragraphs', 'table', 'markdown'},
    'image': {'image'},
    'audio': {'audio', 'audioplus'},
    'video': {'video'},
    'timeseries': {'timeseries'},
}


class StudentProjectPermission(models.Model):
    membership = models.OneToOneField(
        'projects.ProjectMember', on_delete=models.CASCADE, related_name='student_permissions'
    )
    # None allows every annotation module of the project, including modules added to the config later.
    annotation_modules = models.JSONField(null=True, blank=True, default=None)
    annotate = models.BooleanField(default=True)
    import_tasks = models.BooleanField(default=False)
    export_data = models.BooleanField(default=False)
    manage_tasks = models.BooleanField(default=False)
    configure_project = models.BooleanField(default=False)
    delete_project = models.BooleanField(default=False)


class ProjectTeacher(models.Model):
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='school_teachers')
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='school_projects')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='school_unique_project_teacher')]


class StudentCreatePermission(models.Model):
    """Lets a student create projects in one college from the granted labeling template groups."""

    member = models.OneToOneField(
        'organizations.OrganizationMember', on_delete=models.CASCADE, related_name='school_create_permission'
    )
    # Template group names (the tabs of "Labeling Setup"), plus CUSTOM_TEMPLATE for custom configs.
    template_groups = models.JSONField(default=list)
    # Becomes the responsible teacher of projects the student creates.
    granted_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
