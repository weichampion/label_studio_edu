"""分配队列过滤与自动分配策略。

详见 docs/edu-enterprise/技术方案.md §5。
接入点：projects/api.py ProjectNextTaskAPI 与 data_manager/actions/next_task.py，
把 prepared_tasks 过滤为「分配给当前用户的任务」并让 get_next_task 走
OSS 已预留的 assigned_flag=True（Manually assigned queue）分支。
"""
import logging
from itertools import cycle

from django.db import transaction

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ('assigned', 'in_progress')


def filter_assigned(prepared_tasks, user, project):
    """手动分配模式下，把候选任务收敛到「派给我的」。

    返回 (queryset, assigned_flag)：
    - 手动分配模式 → (仅本人被分配的任务, True)，OSS 队列会忽略 overlap/is_labeled；
    - 其他情况 → 原样返回 (prepared_tasks, None)，保持 OSS 抢单行为。

    两阶段队列（技术方案 §5.3）：
    1. 还有待标注的 AN 分配 → 只吐这些；
    2. 没有 AN 任务了 → 吐已标注任务的 RE（审核）分配。
    """
    from orgext.models import Assignment

    if getattr(project, 'assignment_mode', None) != 'manual':
        return prepared_tasks, None

    an_ids = Assignment.objects.filter(
        user=user,
        task__project=project,
        type=Assignment.Type.ANNOTATOR,
        status__in=ACTIVE_STATUSES,
    ).values_list('task_id', flat=True)

    if an_ids.exists():
        return prepared_tasks.filter(pk__in=an_ids), True

    re_ids = Assignment.objects.filter(
        user=user,
        task__project=project,
        type=Assignment.Type.REVIEWER,
        status__in=ACTIVE_STATUSES,
        task__is_labeled=True,
    ).values_list('task_id', flat=True)

    return prepared_tasks.filter(pk__in=re_ids), True


def bulk_assign(task_ids, user_ids, assigned_by, assignment_type='AN'):
    """把一批任务分配给一批用户（每个任务派给所有指定用户）。"""
    from orgext.models import Assignment

    existing = set(
        Assignment.objects.filter(task_id__in=task_ids, user_id__in=user_ids, type=assignment_type).values_list(
            'task_id', 'user_id'
        )
    )
    objs = [
        Assignment(task_id=t, user_id=u, type=assignment_type, assigned_by=assigned_by)
        for t in task_ids
        for u in user_ids
        if (t, u) not in existing
    ]
    return Assignment.objects.bulk_create(objs, batch_size=1000)


def auto_distribute(task_ids, user_ids, assigned_by, overlap=1, assignment_type='AN'):
    """自动分配：轮询均分，每个任务派给 overlap 个人。"""
    from orgext.models import Assignment

    if not user_ids:
        return []
    overlap = max(1, min(overlap, len(user_ids)))
    users = cycle(user_ids)
    pairs = set()
    for t in task_ids:
        for _ in range(overlap):
            pairs.add((t, next(users)))

    existing = set(
        Assignment.objects.filter(task_id__in=task_ids, user_id__in=user_ids, type=assignment_type).values_list(
            'task_id', 'user_id'
        )
    )
    objs = [
        Assignment(task_id=t, user_id=u, type=assignment_type, assigned_by=assigned_by)
        for (t, u) in pairs
        if (t, u) not in existing
    ]
    with transaction.atomic():
        return Assignment.objects.bulk_create(objs, batch_size=1000)
