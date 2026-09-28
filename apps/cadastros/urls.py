from django.urls import path

from apps.cadastros import views

app_name = "cadastros"

urlpatterns = [
    path("categorias/", views.CategoriaProdutoListView.as_view(), name="categoria_lista"),
    path("categorias/nova/", views.CategoriaProdutoCreateView.as_view(), name="categoria_criar"),
    path("categorias/<int:pk>/editar/", views.CategoriaProdutoUpdateView.as_view(), name="categoria_editar"),
    path("produtos/", views.ProdutoListView.as_view(), name="produto_lista"),
    path("produtos/novo/", views.ProdutoCreateView.as_view(), name="produto_criar"),
    path("produtos/<int:pk>/editar/", views.ProdutoUpdateView.as_view(), name="produto_editar"),
    path("kits/", views.KitRequisicaoListView.as_view(), name="kit_lista"),
    path("kits/novo/", views.KitRequisicaoCreateView.as_view(), name="kit_criar"),
    path("kits/<int:pk>/editar/", views.KitRequisicaoUpdateView.as_view(), name="kit_editar"),
]
