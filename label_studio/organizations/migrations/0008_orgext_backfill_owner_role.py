# orgext（教育企业版）数据迁移：把每个组织 created_by 对应的成员角色回填为 owner。
# 其余存量成员保持默认 annotator（按教务名单批量导入调整，见技术方案 §9.2/§12）。
from django.db import migrations


def backfill_owner_role(apps, schema_editor):
    OrganizationMember = apps.get_model('organizations', 'OrganizationMember')
    Organization = apps.get_model('organizations', 'Organization')

    owner_pairs = Organization.objects.filter(created_by__isnull=False).values_list('id', 'created_by_id')
    for org_id, user_id in owner_pairs.iterator():
        OrganizationMember.objects.filter(organization_id=org_id, user_id=user_id).update(role='owner')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('organizations', '0007_organizationmember_role'),
    ]

    operations = [
        migrations.RunPython(backfill_owner_role, noop),
    ]
