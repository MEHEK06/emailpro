from django.urls import path

from leads import views

app_name = "leads"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload_csv, name="upload_csv"),
    path("template/save/", views.update_template, name="update_template"),
    path("leads/<int:pk>/delete/", views.delete_lead, name="delete_lead"),
    path("leads/<int:pk>/send/", views.send_individual, name="send_individual"),
    path("campaigns/", views.campaign_list, name="campaign_list"),
    path("campaigns/preview/", views.campaign_preview, name="campaign_preview"),
    path("campaigns/<int:pk>/", views.campaign_detail, name="campaign_detail"),
    # Public — no auth, clicked directly from email clients.
    path("unsubscribe/<str:token>/", views.unsubscribe_view, name="unsubscribe"),
]
