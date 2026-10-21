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
    path("setores/", views.setor_lista, name="config_setor_lista"),
    path("setores/novo/", views.setor_form, name="config_setor_novo"),
    path("setores/<int:pk>/editar/", views.setor_form, name="config_setor_editar"),
    path("centros-custo/", views.centro_lista, name="config_centro_lista"),
    path("centros-custo/novo/", views.centro_form, name="config_centro_novo"),
    path("centros-custo/<int:pk>/editar/", views.centro_form, name="config_centro_editar"),
    path("eventos/", views.evento_lista, name="config_evento_lista"),
    path("eventos/novo/", views.evento_form, name="config_evento_novo"),
    path("eventos/<int:pk>/editar/", views.evento_form, name="config_evento_editar"),
    path("alcadas/", views.alcada_lista, name="config_alcada_lista"),
    path("alcadas/<int:pk>/", views.alcada_form, name="config_alcada_editar"),
    path("fornecedores/", views.fornecedor_lista, name="config_fornecedor_lista"),
    path("fornecedores/novo/", views.fornecedor_form, name="config_fornecedor_novo"),
    path("fornecedores/<int:pk>/editar/", views.fornecedor_form, name="config_fornecedor_editar"),
    path("filiais/", views.filial_lista, name="config_filial_lista"),
    path("filiais/nova/", views.filial_form, name="config_filial_nova"),
    path("filiais/<int:pk>/editar/", views.filial_form, name="config_filial_editar"),
    path("almoxarifados/", views.almoxarifado_lista, name="config_almoxarifado_lista"),
    path("almoxarifados/novo/", views.almoxarifado_form, name="config_almoxarifado_novo"),
    path("almoxarifados/<int:pk>/editar/", views.almoxarifado_form, name="config_almoxarifado_editar"),
    path("almoxarifados/<int:pk>/excluir/", views.almoxarifado_excluir, name="config_almoxarifado_excluir"),
]
