from django.contrib import admin
from school_tenancy.models import College, MemberRole


@admin.register(College)
class CollegeAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'is_active', 'member_count', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'organization__title')

    @admin.display(description='名称')
    def title(self, obj):
        return obj.organization.title

    @admin.display(description='成员数')
    def member_count(self, obj):
        return obj.organization.members.filter(deleted_at__isnull=True).count()


@admin.register(MemberRole)
class MemberRoleAdmin(admin.ModelAdmin):
    list_display = ('email', 'college', 'role', 'updated_at')
    list_filter = ('role', 'member__organization')
    search_fields = ('member__user__email',)
    raw_id_fields = ('member',)

    @admin.display(description='邮箱')
    def email(self, obj):
        return obj.member.user.email

    @admin.display(description='学院')
    def college(self, obj):
        return obj.member.organization.title
