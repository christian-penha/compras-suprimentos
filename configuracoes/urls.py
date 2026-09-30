from django.urls import path

from . import views

urlpatterns = [
    path("", views.painel, name="config_painel"),
    path("usuarios/", views.usuario_lista, name="config_usuario_lista"),
    path("usuarios/novo/", views.usuario_form, name="config_usuario_novo"),
    path("usuarios/<int:pk>/editar/", views.usuario_form, name="config_usuario_editar"),
    path("empresas/", views.empresa_lista, name="config_empresa_lista"),
    path("empresas/nova/", views.empresa_form, name="config_empresa_nova"),
    path("empresas/<int:pk>/editar/", views.empresa_form, name="config_empresa_editar"),
    path("filiais/", views.filial_lista, name="config_filial_lista"),
    path("filiais/nova/", views.filial_form, name="config_filial_nova"),
    path("filiais/<int:pk>/editar/", views.filial_form, name="config_filial_editar"),
]
