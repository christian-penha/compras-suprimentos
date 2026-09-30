from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from estoque import views as estoque_views
from pedidos import views as pedidos_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", auth_views.LoginView.as_view(), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("", pedidos_views.painel, name="painel"),
    path("catalogo/", pedidos_views.catalogo, name="catalogo"),
    path("carrinho/", pedidos_views.carrinho_ver, name="carrinho_ver"),
    path("carrinho/adicionar/", pedidos_views.carrinho_adicionar, name="carrinho_adicionar"),
    path("checkout/", pedidos_views.checkout, name="checkout"),
    path("pedidos/", pedidos_views.pedido_lista, name="pedido_lista"),
    path("pedidos/<int:pk>/", pedidos_views.pedido_detalhe, name="pedido_detalhe"),
    path("aprovacoes/", pedidos_views.aprovacoes, name="aprovacoes"),
    path("suprimentos/", pedidos_views.fila_suprimentos, name="fila_suprimentos"),
    path("estoque/entrada-planilha/", estoque_views.entrada_planilha, name="entrada_planilha"),
    path("estoque/posicao/", estoque_views.posicao_estoque, name="posicao_estoque"),
    path("configuracoes/", include("configuracoes.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
