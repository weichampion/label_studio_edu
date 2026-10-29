"""补齐 core.middleware 里 SessionTimeoutMiddleware 依赖的 session['last_login']。

OSS 只有走 users/functions/common.py::login() 的产品登录页会写入这个字段；
Django 自带的 /admin/login/（以及其他直接调用 django.contrib.auth.login 的路径）
不会写入，导致下一个请求里 SessionTimeoutMiddleware 把 last_login 当作 0（1970年）
算出“太久没活动”，直接把刚登录的会话登出。这里用 user_logged_in 信号统一补齐，
覆盖所有登录入口，不改内核文件。
"""
from time import time

from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from orgext.models import Assignment
from tasks.models import Annotation


@receiver(user_logged_in)
def set_last_login_session_key(sender, request, user, **kwargs):
    if hasattr(request, 'session'):
        request.session['last_login'] = time()


def _update_status(task_id, user_id, status, only_from=None):
    qs = Assignment.objects.filter(task_id=task_id, user_id=user_id)
    if only_from is not None:
        qs = qs.filter(status=only_from)
    qs.exclude(status=status).update(status=status, updated_at=timezone.now())


@receiver(post_save, sender=Annotation)
def update_assignment_on_annotation_save(sender, instance, **kwargs):
    if not instance.task_id:
        return
    if instance.completed_by_id:
        # 提交的标注/审核：该 (task, user) 的 AN 与 RE 分配都算完成
        _update_status(instance.task_id, instance.completed_by_id, Assignment.Status.COMPLETED)
    elif instance.updated_by_id and not instance.was_cancelled:
        # 无 completed_by 的标注（导入/编辑）：仅从 assigned 提升为 in_progress
        _update_status(
            instance.task_id,
            instance.updated_by_id,
            Assignment.Status.IN_PROGRESS,
            only_from=Assignment.Status.ASSIGNED,
        )


@receiver(post_delete, sender=Annotation)
def update_assignment_on_annotation_delete(sender, instance, **kwargs):
    if not instance.task_id or not instance.completed_by_id:
        return
    remaining = Annotation.objects.filter(task_id=instance.task_id, completed_by_id=instance.completed_by_id).exists()
    if not remaining:
        _update_status(instance.task_id, instance.completed_by_id, Assignment.Status.ASSIGNED)
