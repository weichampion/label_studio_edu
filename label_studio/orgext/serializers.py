"""orgext 序列化器。"""
from orgext.models import Assignment, Workspace, WorkspaceMember
from orgext.roles import Role
from rest_framework import serializers
from users.models import User


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ['id', 'organization', 'title', 'parent', 'is_archived', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['organization', 'created_by', 'created_at', 'updated_at']

    def validate_parent(self, parent):
        if parent is not None:
            request = self.context.get('request')
            if request and parent.organization_id != request.user.active_organization_id:
                raise serializers.ValidationError('Parent workspace must belong to the same organization')
        return parent


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    email = serializers.EmailField(write_only=True, required=False)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMember
        fields = ['id', 'workspace', 'user', 'user_email', 'user_name', 'email', 'role', 'created_at', 'updated_at']
        read_only_fields = ['workspace', 'created_at', 'updated_at']

    def get_user_name(self, obj):
        name = f'{obj.user.first_name or ""} {obj.user.last_name or ""}'.strip()
        return name or obj.user.username or obj.user.email

    def validate(self, attrs):
        if not attrs.get('user') and not attrs.get('email'):
            raise serializers.ValidationError('Either user or email is required')
        return attrs


class WorkspaceMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)


class UserAccountCreateSerializer(serializers.Serializer):
    """管理员创建账号（老师等）：org 角色 + 可选学院绑定。"""

    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=256, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=256, required=False, allow_blank=True, default='')
    password = serializers.CharField(min_length=8, required=False, allow_blank=True, default='')
    role = serializers.ChoiceField(
        choices=[Role.MANAGER, Role.REVIEWER, Role.ANNOTATOR, Role.ADMIN], default=Role.MANAGER
    )
    workspace = serializers.IntegerField(required=False, allow_null=True, default=None)


class OrganizationMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)


class AssignmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    project = serializers.IntegerField(source='task.project_id', read_only=True)

    class Meta:
        model = Assignment
        fields = [
            'id', 'task', 'project', 'user', 'user_email', 'type', 'status',
            'assigned_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['assigned_by', 'created_at', 'updated_at']


class BulkAssignSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    task_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=None)
    type = serializers.ChoiceField(choices=Assignment.Type.choices, default=Assignment.Type.ANNOTATOR)


class AutoAssignSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    task_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=None)
    overlap = serializers.IntegerField(min_value=1, default=1)
    type = serializers.ChoiceField(choices=Assignment.Type.choices, default=Assignment.Type.ANNOTATOR)
