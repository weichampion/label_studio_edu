"""Tenant (college) resolution and role checks shared by middleware, permissions and querysets.

The active college is ``User.active_organization``, but it is only trusted after it has been
validated against the user's current memberships; results are cached on the user object, which is
created per request.
"""

from django.db.models import Q
from organizations.models import OrganizationMember
from school_tenancy.models import ANNOTATION_MODULES, MODULE_OBJECT_TYPES, PROJECT_FEATURES, ProjectTeacher, Role

_UNSET = object()
_ACTIVE_ATTR = '_school_active_college'
_ROLES_ATTR = '_school_roles'


def _is_authenticated(user):
    return user is not None and getattr(user, 'is_authenticated', False)


def valid_memberships(user):
    """Memberships that grant access: not removed, and the college exists and is enabled."""
    return OrganizationMember.objects.filter(
        user=user, deleted_at__isnull=True, organization__college__is_active=True
    ).order_by('organization_id')


def college_ids_for(user):
    return list(valid_memberships(user).values_list('organization_id', flat=True))


def invalidate(user):
    # getattr/setattr (not __dict__) so the cache also works through request.user's SimpleLazyObject
    for attr in (_ACTIVE_ATTR, _ROLES_ATTR):
        if hasattr(user, attr):
            delattr(user, attr)


def ensure_active_college(user):
    """Return the id of the user's validated active college, repairing the stored value if needed."""
    if not _is_authenticated(user):
        return None
    cached = getattr(user, _ACTIVE_ATTR, _UNSET)
    if cached is not _UNSET:
        return cached
    valid = college_ids_for(user)
    current = user.active_organization_id
    if current not in valid:
        current = valid[0] if valid else None
        user.active_organization_id = current
        user.save(update_fields=['active_organization'])
    setattr(user, _ACTIVE_ATTR, current)
    return current


def switch_college(user, organization_id):
    if organization_id not in college_ids_for(user):
        return False
    invalidate(user)
    user.active_organization_id = organization_id
    user.save(update_fields=['active_organization'])
    setattr(user, _ACTIVE_ATTR, organization_id)
    return True


def role_in(user, organization_id):
    if not organization_id or not _is_authenticated(user):
        return None
    roles = getattr(user, _ROLES_ATTR, None)
    if roles is None:
        roles = {}
        setattr(user, _ROLES_ATTR, roles)
    if organization_id not in roles:
        member = valid_memberships(user).filter(organization_id=organization_id).select_related('school_role').first()
        if member is None:
            roles[organization_id] = None
        else:
            role_row = getattr(member, 'school_role', None)
            roles[organization_id] = role_row.role if role_row else Role.STUDENT
    return roles[organization_id]


def active_role(user):
    return role_in(user, ensure_active_college(user))


def can_access_project(user, project):
    """Access is limited to the active college and project responsibility/assignment."""
    org_id = ensure_active_college(user)
    if org_id is None or project.organization_id != org_id:
        return False
    role = role_in(user, org_id)
    if role == Role.COLLEGE_ADMIN:
        return True
    if role == Role.TEACHER:
        return project.created_by_id == user.pk or ProjectTeacher.objects.filter(project=project, user=user).exists()
    if role == Role.STUDENT:
        from projects.models import ProjectMember

        if project.created_by_id == user.pk:
            return True
        return ProjectMember.objects.filter(project_id=project.pk, user=user, enabled=True).exists()
    return False


def can_view_all_annotations(user):
    """College administrators see every annotation of their college; superusers see every college."""
    if not _is_authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    org_id = ensure_active_college(user)
    return role_in(user, org_id) == Role.COLLEGE_ADMIN


def scope_all_annotations(user):
    """Annotation queryset for the college-wide annotation data view."""
    from tasks.models import Annotation

    if not _is_authenticated(user):
        return Annotation.objects.none()
    if getattr(user, 'is_superuser', False):
        return Annotation.objects.all()
    org_id = ensure_active_college(user)
    if role_in(user, org_id) == Role.COLLEGE_ADMIN:
        return Annotation.objects.filter(project__organization_id=org_id)
    return Annotation.objects.none()


def scope_projects(queryset, user):
    """Queryset counterpart of can_access_project (settings.PROJECT_QUERYSET_SCOPE)."""
    org_id = ensure_active_college(user)
    if org_id is None:
        return queryset.none()
    queryset = queryset.filter(organization_id=org_id)
    role = role_in(user, org_id)
    if role == Role.COLLEGE_ADMIN:
        return queryset
    if role == Role.TEACHER:
        return queryset.filter(
            Q(created_by=user) | Q(id__in=ProjectTeacher.objects.filter(user=user).values('project_id'))
        )
    if role == Role.STUDENT:
        from projects.models import ProjectMember

        # A subquery instead of a join keeps the per-project count annotations correct.
        return queryset.filter(
            Q(created_by=user) | Q(id__in=ProjectMember.objects.filter(user=user, enabled=True).values('project_id'))
        )
    return queryset.none()


def can_manage_project(user, project):
    return active_role(user) in (Role.COLLEGE_ADMIN, Role.TEACHER) and can_access_project(user, project)


def owns_project(user, project):
    """A student who created the project uses every project feature, but never gains teacher authority."""
    return active_role(user) == Role.STUDENT and project.created_by_id == user.pk and can_access_project(user, project)


def project_features(user, project):
    if not can_access_project(user, project):
        return []
    if can_manage_project(user, project) or owns_project(user, project):
        return [name for name, _ in PROJECT_FEATURES]
    permission = _student_grant(user, project)
    if permission is None:
        return ['annotate']
    features = [name for name, _ in PROJECT_FEATURES if getattr(permission, name)]
    controls = control_modules(project)
    if 'annotate' in features and controls and not set(controls) - hidden_controls(user, project):
        # Annotating is meaningless when every annotation module of the project is withheld.
        features.remove('annotate')
    return features


def has_project_feature(user, project, feature):
    return feature in project_features(user, project)


def _student_grant(user, project):
    from projects.models import ProjectMember

    membership = ProjectMember.objects.select_related('student_permissions').get(
        project=project, user=user, enabled=True
    )
    return getattr(membership, 'student_permissions', None)


def module_of(object_type):
    object_type = (object_type or '').lower()
    for name, types in MODULE_OBJECT_TYPES.items():
        if object_type in types:
            return name
    return 'other'


def _control_modules(parsed):
    return {
        name: {module_of(item.get('type')) for item in control.get('inputs') or []} or {'other'}
        for name, control in (parsed or {}).items()
    }


def control_modules(project):
    """Annotation modules (by object tag type) that each control tag of the labeling config writes to."""
    return _control_modules(project.get_parsed_config())


def creation_template_groups(user):
    """Template groups the user may create projects from in the active college; None means unrestricted."""
    from school_tenancy.models import StudentCreatePermission

    org_id = ensure_active_college(user)
    role = role_in(user, org_id)
    if role in (Role.COLLEGE_ADMIN, Role.TEACHER):
        return None
    if role != Role.STUDENT:
        return set()
    grant = StudentCreatePermission.objects.filter(
        member__user=user, member__organization_id=org_id, member__deleted_at__isnull=True
    ).first()
    return set(grant.template_groups) if grant else set()


def can_create_projects(user):
    groups = creation_template_groups(user)
    return groups is None or bool(groups)


def project_modules(project):
    used = set().union(*control_modules(project).values())
    return [name for name, _ in ANNOTATION_MODULES if name in used]


def allowed_modules(user, project):
    """Annotation modules a user may use in the project; None means unrestricted."""
    if not can_access_project(user, project):
        return set()
    if can_manage_project(user, project) or owns_project(user, project):
        return None
    permission = _student_grant(user, project)
    if permission is None or permission.annotation_modules is None:
        return None
    return set(permission.annotation_modules)


def hidden_controls(user, project):
    allowed = allowed_modules(user, project)
    if allowed is None:
        return set()
    return {name for name, modules in control_modules(project).items() if not modules <= allowed}


def student_label_config(user, project):
    """Labeling config without withheld modules, or None when the user sees the full config."""
    allowed = allowed_modules(user, project)
    hidden = hidden_controls(user, project)
    if allowed is None or not hidden:
        return None
    import xml.etree.ElementTree as ElementTree

    import defusedxml.ElementTree as SafeElementTree

    parsed = project.get_parsed_config() or {}
    objects = {name for control in parsed.values() for name in control.get('to_name') or []}
    root = SafeElementTree.fromstring(project.label_config, forbid_dtd=True)
    parents = {child: parent for parent in root.iter() for child in parent}
    emptied = set()
    for element in list(root.iter()):
        name = element.get('name')
        withheld_object = name in objects and module_of(element.tag) not in allowed
        if name in hidden or withheld_object:
            parent = parents.get(element)
            if parent is not None:
                parent.remove(element)
                emptied.add(parent)
    # Drop layout containers such as <View><Header/>...</View> left with no labeling tag (innermost first).
    for container in reversed(list(root.iter())):
        if container is root or container not in emptied or container.tag.lower() != 'view':
            continue
        if not any(child.get('name') for child in container.iter()):
            parents[container].remove(container)
            emptied.add(parents[container])
    return ElementTree.tostring(root, encoding='unicode')


def scope_users(queryset, request):
    """settings.USER_QUERYSET_SCOPE: students only see themselves; only college admins edit other users."""
    user = request.user
    role = active_role(user)
    if role == Role.COLLEGE_ADMIN or (role == Role.TEACHER and request.method in ('GET', 'HEAD', 'OPTIONS')):
        return queryset
    return queryset.filter(pk=user.pk)
