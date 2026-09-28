from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView, View

from apps.cadastros.forms import (
    CategoriaProdutoForm,
    KitRequisicaoForm,
    ProdutoForm,
    item_kit_formset_factory,
)
from apps.cadastros.models import CategoriaProduto, KitRequisicao, Produto


class TenantRequiredMixin(LoginRequiredMixin):
    """Bloqueia acesso de usuários sem tenant vinculado — cadastro de negócio
    não existe sem tenant (TenantModel.tenant não é opcional)."""

    def get(self, request, *args, **kwargs):
        self._checar_tenant(request)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self._checar_tenant(request)
        return super().post(request, *args, **kwargs)

    @staticmethod
    def _checar_tenant(request):
        if request.user.is_authenticated and getattr(request.user, "tenant_id", None) is None:
            raise PermissionDenied("Seu usuário não está vinculado a nenhum tenant.")


class CategoriaProdutoListView(TenantRequiredMixin, ListView):
    model = CategoriaProduto
    template_name = "cadastros/categoria_lista.html"
    context_object_name = "categorias"
    paginate_by = 25

    def get_queryset(self):
        return CategoriaProduto.objects.select_related("categoria_pai").order_by("nome")


class CategoriaProdutoCreateView(TenantRequiredMixin, CreateView):
    model = CategoriaProduto
    form_class = CategoriaProdutoForm
    template_name = "cadastros/categoria_form.html"
    success_url = reverse_lazy("cadastros:categoria_lista")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tenant"] = self.request.user.tenant
        return kwargs

    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, "Categoria cadastrada com sucesso.")
        return super().form_valid(form)


class CategoriaProdutoUpdateView(TenantRequiredMixin, UpdateView):
    model = CategoriaProduto
    form_class = CategoriaProdutoForm
    template_name = "cadastros/categoria_form.html"
    success_url = reverse_lazy("cadastros:categoria_lista")

    def get_queryset(self):
        return CategoriaProduto.objects.all()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tenant"] = self.request.user.tenant
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Categoria atualizada com sucesso.")
        return super().form_valid(form)


class ProdutoListView(TenantRequiredMixin, ListView):
    model = Produto
    template_name = "cadastros/produto_lista.html"
    context_object_name = "produtos"
    paginate_by = 25

    def get_queryset(self):
        qs = Produto.objects.select_related("categoria").order_by("nome")
        termo = self.request.GET.get("q", "").strip()
        if termo:
            qs = qs.filter(nome__icontains=termo)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        return context


class ProdutoCreateView(TenantRequiredMixin, CreateView):
    model = Produto
    form_class = ProdutoForm
    template_name = "cadastros/produto_form.html"
    success_url = reverse_lazy("cadastros:produto_lista")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tenant"] = self.request.user.tenant
        return kwargs

    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, "Produto cadastrado com sucesso.")
        return super().form_valid(form)


class ProdutoUpdateView(TenantRequiredMixin, UpdateView):
    model = Produto
    form_class = ProdutoForm
    template_name = "cadastros/produto_form.html"
    success_url = reverse_lazy("cadastros:produto_lista")

    def get_queryset(self):
        return Produto.objects.all()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tenant"] = self.request.user.tenant
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Produto atualizado com sucesso.")
        return super().form_valid(form)


class KitRequisicaoListView(TenantRequiredMixin, ListView):
    model = KitRequisicao
    template_name = "cadastros/kit_lista.html"
    context_object_name = "kits"
    paginate_by = 25

    def get_queryset(self):
        return KitRequisicao.objects.order_by("nome")


class KitRequisicaoCreateView(TenantRequiredMixin, View):
    template_name = "cadastros/kit_form.html"

    def get(self, request, *args, **kwargs):
        form = KitRequisicaoForm()
        FormSet = item_kit_formset_factory(request.user.tenant)
        formset = FormSet()
        return self._render(request, form, formset)

    def post(self, request, *args, **kwargs):
        form = KitRequisicaoForm(request.POST)
        FormSet = item_kit_formset_factory(request.user.tenant)
        formset = FormSet(request.POST)
        if form.is_valid():
            kit = form.save(commit=False)
            kit.tenant = request.user.tenant
            formset = FormSet(request.POST, instance=kit)
            if formset.is_valid():
                kit.save()
                formset.instance = kit
                formset.save()
                messages.success(request, "Kit de requisição cadastrado com sucesso.")
                return redirect("cadastros:kit_lista")
        return self._render(request, form, formset)

    def _render(self, request, form, formset):
        return render(request, self.template_name, {"form": form, "formset": formset})


class KitRequisicaoUpdateView(TenantRequiredMixin, View):
    template_name = "cadastros/kit_form.html"

    def get(self, request, pk, *args, **kwargs):
        kit = get_object_or_404(KitRequisicao, pk=pk)
        form = KitRequisicaoForm(instance=kit)
        FormSet = item_kit_formset_factory(request.user.tenant)
        formset = FormSet(instance=kit)
        return self._render(request, form, formset)

    def post(self, request, pk, *args, **kwargs):
        kit = get_object_or_404(KitRequisicao, pk=pk)
        form = KitRequisicaoForm(request.POST, instance=kit)
        FormSet = item_kit_formset_factory(request.user.tenant)
        formset = FormSet(request.POST, instance=kit)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Kit de requisição atualizado com sucesso.")
            return redirect("cadastros:kit_lista")
        return self._render(request, form, formset)

    def _render(self, request, form, formset):
        return render(request, self.template_name, {"form": form, "formset": formset})
