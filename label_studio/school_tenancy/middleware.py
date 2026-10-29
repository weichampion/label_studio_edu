from django.http import JsonResponse
from django.shortcuts import redirect
from school_tenancy.access import ensure_active_college

# Reachable without an active college, so users can read the explanation and sign out.
EXEMPT_PREFIXES = (
    '/static/',
    '/react-app/',
    '/health',
    '/favicon.ico',
    '/logout',
    '/user/login',
    '/school/no-college/',
    '/api/school/me',
)


class TenantMiddleware:
    """Validate the active college of every authenticated request (replaces DummyGetSessionMiddleware)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and ensure_active_college(user) is None:
            path = request.path
            exempt = path.startswith(EXEMPT_PREFIXES) or (user.is_superuser and path.startswith('/admin/'))
            if not exempt:
                if path.startswith('/api/'):
                    return JsonResponse({'detail': '当前账号未加入任何学院，请联系学院管理员。'}, status=403)
                return redirect('school_tenancy:no-college')
        return self.get_response(request)
