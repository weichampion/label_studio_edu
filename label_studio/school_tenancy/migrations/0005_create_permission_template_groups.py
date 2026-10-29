from django.db import migrations


def clear_module_grants(apps, schema_editor):
    # Grants used object types (text/image/...) before; they do not name template groups.
    apps.get_model('school_tenancy', 'StudentCreatePermission').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('school_tenancy', '0004_studentcreatepermission'),
    ]

    operations = [
        migrations.RenameField(model_name='studentcreatepermission', old_name='modules', new_name='template_groups'),
        migrations.RunPython(clear_module_grants, migrations.RunPython.noop),
    ]
