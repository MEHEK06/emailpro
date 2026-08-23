from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload_csv, name="upload_csv"),
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("contacts/", views.contacts_list, name="contacts_list"),
    path("classification/start/", views.start_classification, name="start_classification"),
    path("classification/", views.classification_run_list, name="classification_run_list"),
    path("classification/<int:pk>/", views.classification_run_detail, name="classification_run_detail"),
    path("campaigns/", views.campaign_list, name="campaign_list"),
    path("campaigns/new/", views.campaign_create, name="campaign_create"),
    path("campaigns/<int:pk>/confirm/", views.campaign_confirm, name="campaign_confirm"),
    path("campaigns/<int:pk>/", views.campaign_detail, name="campaign_detail"),
    path("settings/", views.settings_status, name="settings_status"),
]
