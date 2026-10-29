"""Local demo data: two colleges with administrators, teachers, students and assigned projects.

For local development only. All demo accounts share one password, printed at the end.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from projects.models import Project
from school_tenancy import services
from school_tenancy.models import College, Role
from tasks.models import Task
from users.models import User

SENTIMENT_CONFIG = """<View>
  <Text name="text" value="$text"/>
  <Choices name="sentiment" toName="text" choice="single" showInline="true">
    <Choice value="正面"/>
    <Choice value="负面"/>
    <Choice value="中性"/>
  </Choices>
</View>"""

TOPIC_CONFIG = """<View>
  <Text name="text" value="$text"/>
  <Choices name="topic" toName="text" choice="single">
    <Choice value="科技"/>
    <Choice value="体育"/>
    <Choice value="财经"/>
    <Choice value="教育"/>
  </Choices>
</View>"""

INTENT_CONFIG = """<View>
  <Text name="text" value="$text"/>
  <Choices name="intent" toName="text" choice="single">
    <Choice value="挂号预约"/>
    <Choice value="症状咨询"/>
    <Choice value="用药咨询"/>
    <Choice value="报告解读"/>
  </Choices>
</View>"""

COLLEGES = [('cs', '计算机学院'), ('med', '医学院')]

# email, name, {college code: role}
USERS = [
    ('cs.admin@demo.edu.cn', '计算机学院管理员', {'cs': Role.COLLEGE_ADMIN}),
    ('cs.teacher@demo.edu.cn', '王老师', {'cs': Role.TEACHER}),
    ('cs.student1@demo.edu.cn', '张同学', {'cs': Role.STUDENT}),
    ('cs.student2@demo.edu.cn', '李同学', {'cs': Role.STUDENT}),
    ('med.admin@demo.edu.cn', '医学院管理员', {'med': Role.COLLEGE_ADMIN}),
    ('med.teacher@demo.edu.cn', '赵老师', {'med': Role.TEACHER}),
    ('med.student1@demo.edu.cn', '陈同学', {'med': Role.STUDENT}),
    ('shared.teacher@demo.edu.cn', '跨院刘老师', {'cs': Role.TEACHER, 'med': Role.TEACHER}),
]

# college, title, label config, creator, assigned students, task texts
PROJECTS = [
    (
        'cs',
        '作业1：评论情感分类',
        SENTIMENT_CONFIG,
        'cs.teacher@demo.edu.cn',
        ['cs.student1@demo.edu.cn', 'cs.student2@demo.edu.cn'],
        ['这家餐厅的菜很好吃，服务也周到。', '快递太慢了，等了一个星期。', '手机还行，就是电池一般。',
         '课程内容很实用，老师讲得清楚。', '电影剧情拖沓，看得想睡觉。'],
    ),
    (
        'cs',
        '作业2：新闻主题分类',
        TOPIC_CONFIG,
        'cs.teacher@demo.edu.cn',
        ['cs.student2@demo.edu.cn'],
        ['国产大模型发布新版本，推理能力显著提升。', '国足在世预赛中以二比一取胜。', '央行宣布下调存款准备金率。',
         '教育部发布新一轮高校招生政策。'],
    ),
    (
        'med',
        '问诊意图标注',
        INTENT_CONFIG,
        'med.teacher@demo.edu.cn',
        ['med.student1@demo.edu.cn'],
        ['想挂下周一上午的心内科专家号。', '最近总是头晕乏力，是什么原因？', '阿莫西林饭前吃还是饭后吃？',
         '体检报告里尿酸偏高要紧吗？'],
    ),
]


class Command(BaseCommand):
    help = '创建本地演示用的学院、账号、项目和任务（可重复执行）'

    def add_arguments(self, parser):
        parser.add_argument('--password', default='Demo@2026', help='所有演示账号的密码')

    @transaction.atomic
    def handle(self, password, **options):
        colleges = {}
        for code, title in COLLEGES:
            college = College.objects.filter(code=code).first()
            if college is None:
                college = services.create_college(code, title)
            else:
                services.ensure_system_account(college)
            colleges[code] = college

        admin, created = User.objects.get_or_create(
            email='platform.admin@demo.edu.cn', defaults={'username': 'platform.admin', 'first_name': '平台管理员'}
        )
        admin.is_staff = admin.is_superuser = True
        admin.set_password(password)
        admin.save()

        users = {}
        for email, name, roles in USERS:
            user, _ = User.objects.get_or_create(email=email, defaults={'username': email.split('@')[0], 'first_name': name})
            user.set_password(password)
            user.save()
            for code, role in roles.items():
                services.add_member(colleges[code], user, role)
            users[email] = user

        for code, title, config, creator, students, texts in PROJECTS:
            organization = colleges[code].organization
            project = Project.objects.filter(organization=organization, title=title).first()
            if project is None:
                project = Project.objects.create(
                    title=title, label_config=config, organization=organization, created_by=users[creator]
                )
                tasks = [Task.objects.create(project=project, data={'text': text}) for text in texts]
                project.summary.update_data_columns(tasks)
            services.assign_students(project, [users[s] for s in students])
            # The cross-college demo teacher is explicitly responsible for these projects.
            admin = users[f'{code}.admin@demo.edu.cn']
            services.set_project_teacher(admin, project, users['shared.teacher@demo.edu.cn'])

        self.stdout.write(self.style.SUCCESS('演示数据已就绪。所有账号密码：' + password))
        self.stdout.write('  平台管理员（Django 后台 /admin/，不属于任何学院）：platform.admin@demo.edu.cn')
        for email, _, roles in USERS:
            desc = '，'.join(f'{colleges[c].organization.title}-{Role(r).label}' for c, r in roles.items())
            self.stdout.write(f'  {email:<30} {desc}')
