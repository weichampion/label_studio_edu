from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from school_tenancy import services
from school_tenancy.models import College, Role
from users.functions import login


def save_user(request, next_page, user_form):
    """settings.SAVE_USER: a college invite link registers a student of that college only."""
    token = request.GET.get('token')
    college = (
        College.objects.select_related('organization').filter(organization__token=token, is_active=True).first()
        if token
        else None
    )
    if college is None:
        raise PermissionDenied('邀请链接无效或学院已停用')

    user = user_form.save()
    user.username = user.email.split('@')[0]
    user.save()
    services.add_member(college, user, Role.STUDENT)

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect(next_page or reverse('projects:project-index'))
