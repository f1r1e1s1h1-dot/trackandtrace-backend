from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path"", health
    path('admin/', admin.site.urls),
    path('api/', include('track_api.urls')),   # ← THIS LINE WAS MISSING
]
