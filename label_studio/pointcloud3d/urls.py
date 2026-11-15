from django.urls import path
from pointcloud3d import api

app_name = 'pointcloud3d'

urlpatterns = [
    path('api/pointcloud3d/track/', api.PointCloudTrackAPI.as_view(), name='track'),
]
