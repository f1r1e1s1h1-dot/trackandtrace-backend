from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

urlpatterns = [
    path('', lambda request: JsonResponse({"status":"ok"})),
    path('admin/', admin.site.urls),
    path('api/', include('track_api.urls')),   # ← THIS LINE WAS MISSING
]
