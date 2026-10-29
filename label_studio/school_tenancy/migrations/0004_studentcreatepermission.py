import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0006_alter_organizationmember_deleted_at'),
        ('school_tenancy', '0003_studentprojectpermission_annotation_modules'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentCreatePermission',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('modules', models.JSONField(default=list)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'granted_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'member',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='school_create_permission',
                        to='organizations.organizationmember',
                    ),
                ),
            ],
        ),
    ]
