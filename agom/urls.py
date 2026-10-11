from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "AGOM Cargo Tracker admin"
admin.site.site_title = "AGOM Cargo Tracker"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("tracker.urls")),
]
