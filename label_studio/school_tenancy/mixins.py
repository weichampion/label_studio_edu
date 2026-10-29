from projects.mixins import ProjectMixin
from school_tenancy.access import can_access_project


class SchoolProjectMixin(ProjectMixin):
    def has_permission(self, user):
        user.project = self  # link for activity log
        return can_access_project(user, self)
