"""Labeling template groups (the tabs of "Labeling Setup") used to grant students project creation."""

from functools import lru_cache

CUSTOM_TEMPLATE = '__custom__'
GROUP_LABELS = {
    'Computer Vision': '计算机视觉/图像',
    'Natural Language Processing': '自然语言处理/文本',
    'Audio/Speech Processing': '音频/语音处理',
    'Conversational AI': '对话式 AI',
    'Chat': '聊天',
    'Ranking & Scoring': '排序与评分',
    'Structured Data Parsing': '结构化数据解析',
    'Time Series Analysis': '时间序列分析',
    'Videos': '视频',
    'Generative AI': '生成式 AI',
    'Community Contributions': '社区贡献模板',
    CUSTOM_TEMPLATE: '自定义模板（Custom template，可写任意配置）',
}


def template_data():
    from projects.api import TemplateListAPI

    return TemplateListAPI.templates_and_groups


def available_groups():
    return list(template_data()['groups'])


def group_label(group):
    label = GROUP_LABELS.get(group)
    return f'{group}（{label}）' if label and group != CUSTOM_TEMPLATE else (label or group)


def config_pairs(label_config):
    """(control tag, data tags) pairs of a labeling config; None if it cannot be parsed."""
    from label_studio_sdk._extensions.label_studio_tools.core.label_config import parse_config

    try:
        parsed = parse_config(label_config)
    except Exception:
        return None
    return {
        (control['type'].lower(), tuple(sorted({item['type'].lower() for item in control.get('inputs') or []})))
        for control in parsed.values()
    }


OBJECT_TAGS = {
    'image', 'text', 'hypertext', 'audio', 'audioplus', 'video', 'paragraphs', 'table', 'timeseries', 'pdf',
    'list', 'chat', 'markdown',
}


def _data_types(label_config):
    """Every data (object) tag in the config, including display-only ones such as the image of a VQA template."""
    import defusedxml.ElementTree as SafeElementTree

    try:
        root = SafeElementTree.fromstring(label_config, forbid_dtd=True)
    except Exception:
        return frozenset()
    return frozenset(element.tag.lower() for element in root.iter() if element.tag.lower() in OBJECT_TAGS)


@lru_cache(maxsize=1)
def _signatures_by_group():
    """group -> {data tags of a template: labeling (control, data) pairs of templates with those data tags}."""
    signatures = {}
    for template in template_data()['templates']:
        config = template.get('config', '')
        by_types = signatures.setdefault(template.get('group'), {})
        by_types.setdefault(_data_types(config), set()).update(config_pairs(config) or set())
    return signatures


def config_allowed(label_config, groups):
    """A config must use the same data tags as a template of a granted group and only their labeling tools.

    Label values, names and layout are free, but e.g. a "Computer Vision" grant cannot be turned into a
    text-only task. None means the config cannot be parsed.
    """
    pairs = config_pairs(label_config)
    if pairs is None:
        return None
    if not pairs:
        return True
    allowed = set()
    for group in groups:
        allowed |= _signatures_by_group().get(group, {}).get(_data_types(label_config), set())
    return pairs <= allowed


def scope_templates(data, request):
    """settings.TEMPLATE_LIST_SCOPE: students only see the template groups a teacher granted them."""
    from school_tenancy.access import creation_template_groups

    groups = creation_template_groups(request.user)
    if groups is None:
        return data
    return {
        **data,
        'groups': [group for group in data['groups'] if group in groups],
        'templates': [template for template in data['templates'] if template.get('group') in groups],
        'school_custom_template': CUSTOM_TEMPLATE in groups,
    }
