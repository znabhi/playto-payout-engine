from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("payouts.urls")),
    # Serve React frontend for any path not matching admin or api
    re_path(r"^.*$", TemplateView.as_view(template_name="index.html")),
]
