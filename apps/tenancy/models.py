"""
Hierarquia Tenant → Empresa → Filial → Almoxarifado (ver docs/06-Glossario-e-
Fora-de-Escopo.html). `Tenant` e `TenantModel` vivem em apps.core para evitar
dependência circular (TenantModel precisa de FK para Tenant; se Tenant
estivesse aqui, apps.core dependeria de apps.tenancy e apps.tenancy de
apps.core ao mesmo tempo). Decisão de organização não coberta explicitamente
pelos documentos — os models abaixo são fiéis ao modelo de dados definitivo.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class Empresa(TenantModel):
    """Pessoa jurídica (CNPJ). Quem compra: Fornecedor, OrdemCompra e
    NotaFiscal sempre se referem a uma Empresa, nunca a uma Filial
    diretamente. Hoje: 4 filiais numa Empresa, 1 filial em Empresa separada."""

    razao_social = models.CharField(max_length=200)
    nome_fantasia = models.CharField(max_length=200)
    cnpj = models.CharField(max_length=14)  # só dígitos
    codigo_prodados = models.CharField(max_length=50, blank=True)

    class Meta:
        unique_together = [("tenant", "cnpj")]

    def __str__(self) -> str:
        return self.nome_fantasia


class Filial(TenantModel):
    """Estabelecimento/unidade de negócio (Colégio Perfil, Villa Criar,
    Evolua, Poliana, AICE-BA). Dimensão gerencial — quem consome, não quem
    compra."""

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="filiais")
    nome = models.CharField(max_length=120)
    codigo_prodados = models.CharField(max_length=50, blank=True)
    ativo = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.nome


class Almoxarifado(TenantModel):
    """Saldo lógico de estoque de uma filial/segmento (ex. CP-Ed.Infantil,
    CSC/Matriz)."""

    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="almoxarifados")
    nome = models.CharField(max_length=120)
    eh_central = models.BooleanField(default=False)  # true só para o CSC/Matriz
    ativo = models.BooleanField(default=True)

    @property
    def empresa(self) -> Empresa:
        return self.filial.empresa

    def __str__(self) -> str:
        return f"{self.filial.nome} — {self.nome}"


class CentroCusto(TenantModel):
    """Definido por filial. Suporta um nível de subcentro (self-FK)."""

    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="centros_custo")
    nome = models.CharField(max_length=120)
    centro_pai = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="subcentros"
    )
    codigo_prodados = models.CharField(max_length=50, blank=True)
    ativo = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.nome


class MotivoRequisicao(TenantModel):
    """Reposição, Projeto/Ação Pedagógica, Eventos, Investimento, Conservação
    e Limpeza (ver docs/02-Requisitos-Funcionais.html, RF-06)."""

    nome = models.CharField(max_length=80)
    ativo = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.nome
