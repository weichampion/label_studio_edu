"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license."""

import logging

from core.permissions import all_permissions
from data_manager.actions import DataManagerAction
from data_manager.functions import filters_ordering_selected_items_exist
from orgext.queue import filter_assigned
from projects.functions.next_task import get_next_task
from rest_framework.exceptions import NotFound
from tasks.serializers import NextTaskSerializer

logger = logging.getLogger(__name__)


def next_task(project, queryset, **kwargs):
    """Generate next task for labeling stream

    :param project: project
    :param queryset: task ids to sample from
    :param kwargs: arguments from api request
    """

    request = kwargs['request']
    dm_queue = filters_ordering_selected_items_exist(request.data)
    # orgext: 手动分配模式下仅取派给当前用户的任务（技术方案 §5.2）
    queryset, assigned_flag = filter_assigned(queryset, request.user, project)
    next_task, queue_info = get_next_task(request.user, queryset, project, dm_queue, assigned_flag=assigned_flag)

    if next_task is None:
        raise NotFound(f'There are no tasks for {request.user}')

    # serialize task
    context = {'request': request, 'project': project, 'resolve_uri': True, 'annotations': False}
    serializer = NextTaskSerializer(next_task, context=context)
    response = serializer.data
    response['queue'] = queue_info
    return response


actions: list[DataManagerAction] = [
    {
        'entry_point': next_task,
        'permission': all_permissions.projects_view,
        'title': 'Generate Next Task',
        'order': 0,
        'hidden': True,
    }
]
