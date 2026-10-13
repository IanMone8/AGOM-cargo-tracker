from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("boxes/", views.box_list, name="box_list"),
    path("boxes/new/", views.box_create, name="box_create"),
    path("boxes/<int:pk>/", views.box_detail, name="box_detail"),
    path("boxes/<int:pk>/edit/", views.box_edit, name="box_edit"),
    path("boxes/item-card/", views.box_item_card, name="box_item_card"),
    path("boxes/<int:pk>/ship/", views.box_mark_shipped, name="box_mark_shipped"),
    path("boxes/<int:pk>/start-check/", views.box_start_check, name="box_start_check"),
    path("boxes/<int:pk>/items/add/", views.item_add, name="item_add"),
    path("boxes/<int:pk>/extras/add/", views.extra_add, name="extra_add"),
    path("items/<int:pk>/", views.item_row, name="item_row"),
    path("items/<int:pk>/check/", views.item_check, name="item_check"),
    path("items/<int:pk>/check/clear/", views.item_check_clear, name="item_check_clear"),
    path("items/<int:pk>/check/wrong-qty/", views.item_wrong_qty_form, name="item_wrong_qty"),
    path("items/<int:pk>/delete/", views.item_delete, name="item_delete"),
    path("extras/<int:pk>/delete/", views.extra_delete, name="extra_delete"),
    path("cargo-history/", views.cargo_history, name="cargo_history"),
    path("requests/", views.request_list, name="request_list"),
    path("requests/attach-items/", views.request_attach_items, name="request_attach_items"),
    path("requests/<int:pk>/edit/", views.request_edit, name="request_edit"),
    path("requests/<int:pk>/status/", views.request_status, name="request_status"),
    path("requests/<int:pk>/attach/", views.request_attach, name="request_attach"),
    path("requests/<int:pk>/detach/", views.request_detach, name="request_detach"),
    path("import/", views.csv_import, name="csv_import"),
    path("import/template.csv", views.csv_template, name="csv_template"),
]
