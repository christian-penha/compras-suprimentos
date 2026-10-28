from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from contas import views_google
from estoque import views as estoque_views
from pedidos import views as pedidos_views
from pedidos import views_produtos as produtos_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", views_google.LoginComGoogleOpcional.as_view(), name="login"),
    # A barra final do callback precisa bater com o URI autorizado no console do Google.
    path("auth/login/google/", views_google.login_google, name="auth_google_login"),
    path("auth/authorize/google/", views_google.auth_google_callback, name="auth_google_callback"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("", pedidos_views.painel, name="painel"),
    path("catalogo/", pedidos_views.catalogo, name="catalogo"),
    path("catalogo/sugerir/", produtos_views.sugerir_produto, name="sugerir_produto"),
    path("suprimentos/produtos/", produtos_views.produto_lista, name="produto_lista"),
    path("suprimentos/produtos/novo/", produtos_views.produto_form, name="produto_novo"),
    path("suprimentos/produtos/<int:pk>/", produtos_views.produto_form, name="produto_editar"),
    path("suprimentos/sugestoes/", produtos_views.sugestoes_lista, name="sugestoes_lista"),
    path("suprimentos/sugestoes/<int:pk>/recusar/", produtos_views.sugestao_recusar, name="sugestao_recusar"),
    path("carrinho/", pedidos_views.carrinho_ver, name="carrinho_ver"),
    path("carrinho/adicionar/", pedidos_views.carrinho_adicionar, name="carrinho_adicionar"),
    path("checkout/", pedidos_views.checkout, name="checkout"),
    path("pedidos/", pedidos_views.pedido_lista, name="pedido_lista"),
    path("pedidos/<int:pk>/", pedidos_views.pedido_detalhe, name="pedido_detalhe"),
    path("aprovacoes/", pedidos_views.aprovacoes, name="aprovacoes"),
    path("suprimentos/", pedidos_views.fila_suprimentos, name="fila_suprimentos"),
    path("suprimentos/pedidos/<int:pk>/mover/", pedidos_views.pedido_mover, name="pedido_mover"),
    path("suprimentos/pedidos/<int:pk>/entrega/", pedidos_views.pedido_entrega_modal, name="pedido_entrega_modal"),
    path("estoque/entrada-planilha/", estoque_views.entrada_planilha, name="entrada_planilha"),
    path("estoque/entrada-planilha/modelo/", estoque_views.entrada_modelo, name="entrada_modelo"),
    path("estoque/posicao/", estoque_views.posicao_estoque, name="posicao_estoque"),
    path("relatorios/centros-custo/", pedidos_views.relatorio_centros, name="relatorio_centros"),
    path("relatorios/eventos/", pedidos_views.relatorio_eventos, name="relatorio_eventos"),
    path("compras/", include("compras.urls")),
    path("configuracoes/", include("configuracoes.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
