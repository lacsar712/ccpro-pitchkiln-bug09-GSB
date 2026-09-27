from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("floor/grid/", views.floor_grid_partial, name="floor_grid"),
    path("hearth/<int:pk>/drawer/", views.hearth_drawer, name="hearth_drawer"),
    path("hearth/<int:pk>/phase/", views.change_phase, name="change_phase"),
    path("hearth/<int:pk>/probe/", views.add_probe, name="add_probe"),
    path("probes/<int:pk>/edit/", views.edit_probe, name="edit_probe"),
    path("hearth/<int:pk>/open-run/", views.open_run, name="open_run"),
    path("hearth/<int:pk>/close-run/", views.close_run, name="close_run"),
    path("resin-lots/", views.resin_lot_feed, name="resin_lot_feed"),
]
