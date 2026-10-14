from django.urls import path

from . import views

urlpatterns = [
    path("", views.fila_compras, name="fila_compras"),
    path("cotacoes/", views.cotacao_lista, name="cotacao_lista"),
    path("cotacoes/nova/", views.cotacao_nova, name="cotacao_nova"),
    path("cotacoes/<int:pk>/", views.cotacao_detalhe, name="cotacao_detalhe"),
    path("comparacoes/<int:rodada>/", views.rodada_comparar, name="rodada_comparar"),
    path("aprovacoes/", views.aprovacoes_compra, name="aprovacoes_compra"),
    path("ordens/", views.ordem_lista, name="ordem_lista"),
    path("ordens/<int:pk>/", views.ordem_detalhe, name="ordem_detalhe"),
    path("ordens/<int:pk>/receber/", views.recebimento, name="recebimento"),
    path("ordens/<int:pk>/receber/<int:nota_pk>/", views.recebimento, name="recebimento"),
    path("notas/<int:pk>/xml/", views.nota_xml, name="nota_xml"),
]
