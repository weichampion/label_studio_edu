from django.urls import path
from school_tenancy import views

app_name = 'school_tenancy'

urlpatterns = [
    path('school/', views.workbench, name='workbench'),
    path('school/no-college/', views.no_college, name='no-college'),
    path('school/annotations/', views.all_annotations, name='all-annotations'),
    path('school/switch/', views.switch_college, name='switch'),
    path('school/projects/<int:project_id>/students/', views.project_students_add, name='project-students-add'),
    path(
        'school/projects/<int:project_id>/students/<int:user_id>/features/',
        views.student_features,
        name='student-features',
    ),
    path('school/projects/<int:project_id>/teachers/', views.project_teacher, name='project-teacher'),
    path(
        'school/projects/<int:project_id>/students/<int:user_id>/remove/',
        views.project_student_remove,
        name='project-student-remove',
    ),
    path('school/members/', views.member_add, name='member-add'),
    path(
        'school/members/<int:member_id>/create-templates/',
        views.student_create_templates,
        name='student-create-templates',
    ),
    path('school/members/<int:member_id>/role/', views.member_role, name='member-role'),
    path('school/members/<int:member_id>/remove/', views.member_remove, name='member-remove'),
    path('api/school/me/', views.CurrentCollegeAPI.as_view(), name='api-me'),
    path('api/school/switch-college/', views.SwitchCollegeAPI.as_view(), name='api-switch-college'),
]
