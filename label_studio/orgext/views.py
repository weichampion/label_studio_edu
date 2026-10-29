"""orgext 页面视图：为 SPA 路由提供壳页面（与 organizations/projects 的做法一致）。"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def workspaces_page(request, pk=None):
    return render(request, 'orgext/workspaces.html')
