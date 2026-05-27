from django.urls import path

from . import views

app_name = "mixtapes"

urlpatterns = [
    path("", views.latest, name="latest"),
    path("mixtapes/", views.mixtape_list, name="list"),
    path("mixtapes/<int:pk>/", views.mixtape_detail, name="detail"),
    path("tracks/", views.track_search, name="tracks"),
]
