from rest_framework import serializers
from school_tenancy.access import active_role, can_create_projects
from school_tenancy.models import Role
from school_tenancy.permissions import STUDENT_PERMISSIONS
from users.serializers import BaseWhoAmIUserSerializer


class SchoolWhoAmISerializer(BaseWhoAmIUserSerializer):
    school_role = serializers.SerializerMethodField()

    class Meta(BaseWhoAmIUserSerializer.Meta):
        fields = BaseWhoAmIUserSerializer.Meta.fields + ('school_role',)

    def get_school_role(self, user):
        role = active_role(user)
        if role is None and user.is_superuser:
            return 'system_admin'
        return role

    def get_permissions(self, user):
        from school_tenancy.permissions import VIEW_ALL_ANNOTATIONS

        if active_role(user) == Role.STUDENT:
            # Project-specific grants are returned on each project, never as global abilities.
            permissions = STUDENT_PERMISSIONS - {'annotations.create', 'annotations.change', 'annotations.delete'}
            if can_create_projects(user):
                permissions = permissions | {'projects.create'}
            permissions = set(permissions)
        else:
            permissions = {permission for permission in super().get_permissions(user) if user.has_perm(permission)}
        if user.has_perm(VIEW_ALL_ANNOTATIONS):
            permissions.add(VIEW_ALL_ANNOTATIONS)
        return sorted(permissions)
