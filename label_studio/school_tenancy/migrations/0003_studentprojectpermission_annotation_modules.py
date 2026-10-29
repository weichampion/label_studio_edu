from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('school_tenancy', '0002_studentprojectpermission_projectteacher'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentprojectpermission',
            name='annotation_modules',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
