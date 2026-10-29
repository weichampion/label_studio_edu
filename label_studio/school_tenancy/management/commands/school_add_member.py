import getpass
import sys

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from school_tenancy import services
from school_tenancy.models import College, Role
from users.models import User


class Command(BaseCommand):
    help = '把用户加入学院并设置角色；用户不存在时创建（交互输入初始密码）。'

    def add_arguments(self, parser):
        parser.add_argument('college', help='学院代码')
        parser.add_argument('email')
        parser.add_argument('--role', choices=Role.values, default=Role.STUDENT)
        parser.add_argument('--name', default='', help='新用户姓名')

    def handle(self, college, email, role, name, **options):
        college_obj = College.objects.select_related('organization').filter(code=college).first()
        if college_obj is None:
            raise CommandError(f'学院不存在：{college}')
        email = email.strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            if not sys.stdin.isatty():
                raise CommandError('新用户需要在交互式终端中输入初始密码')
            password = getpass.getpass(f'{email} 的初始密码：')
            if password != getpass.getpass('再次输入：'):
                raise CommandError('两次输入的密码不一致')
            try:
                validate_password(password)
            except ValidationError as exc:
                raise CommandError('；'.join(exc.messages))
            user = User.objects.create_user(email=email, password=password, username=email.split('@')[0], first_name=name)
        services.add_member(college_obj, user, role)
        self.stdout.write(self.style.SUCCESS(f'{email} → {college_obj}：{Role(role).label}'))
