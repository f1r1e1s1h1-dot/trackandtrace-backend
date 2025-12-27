from django.urls import path
from . import views

urlpatterns = [
    path("scan/", views.scan_roll),
    path("inward/", views.inward),
    path("flexo/start/", views.flexo_start),
    path("flexo/end/", views.flexo_end),
    path("dispatch/", views.dispatch),
    path("receiver/", views.receiver_scan),

    # timeline
    path("timeline/", views.timeline),
    path("timeline/previous/", views.timeline_previous),

    # ✅ Admin
    path("admin/summary/", views.admin_summary),   # Flutter calls this
    path("admin/overview/", views.admin_summary),  # alias (safe)
    path("admin/active/", views.admin_active),
    path("admin/search/", views.admin_search),
]
