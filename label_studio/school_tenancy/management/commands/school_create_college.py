from django.core.management.base import BaseCommand, CommandError
from school_tenancy import services
from school_tenancy.models import College


class Command(BaseCommand):
    help = '创建学院（租户）。例：school_create_college cs 计算机学院'

    def add_arguments(self, parser):
        parser.add_argument('code', help='学院代码（字母、数字、- 或 _）')
        parser.add_argument('title', help='学院名称')

    def handle(self, code, title, **options):
        if College.objects.filter(code=code).exists():
            raise CommandError(f'学院代码已存在：{code}')
        college = services.create_college(code, title)
        self.stdout.write(self.style.SUCCESS(f'已创建学院 {college}（organization id={college.organization_id}）'))
