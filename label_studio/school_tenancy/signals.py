from django.db.models.signals import post_save
from django.dispatch import receiver
from projects.models import Project
from school_tenancy import access
from school_tenancy.models import ProjectTeacher, Role, StudentCreatePermission


@receiver(post_save, sender=Project, dispatch_uid='school_student_project_teacher')
def assign_granting_teacher(sender, instance, created, **kwargs):
    """The teacher who allowed a student to create projects becomes responsible for them."""
    creator = instance.created_by
    if not created or creator is None or access.role_in(creator, instance.organization_id) != Role.STUDENT:
        return
    grant = (
        StudentCreatePermission.objects.filter(member__user=creator, member__organization_id=instance.organization_id)
        .select_related('granted_by')
        .first()
    )
    teacher = grant.granted_by if grant else None
    if teacher is not None and access.role_in(teacher, instance.organization_id) == Role.TEACHER:
        ProjectTeacher.objects.get_or_create(project=instance, user=teacher)
