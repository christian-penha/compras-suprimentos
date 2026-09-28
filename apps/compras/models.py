"""
App completo na Fase 2 (ver docs/08-Prompt-Inicial-do-Projeto.html). Só
`Fornecedor` existe já na Fase 0, porque `cadastros.Produto.fornecedor_padrao`
depende dele. Cotacao, OrdemCompra, NotaFiscal, Recebimento e
CorrespondenciaProdutoFornecedor entram junto com o restante do fluxo de
compra.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class Fornecedor(TenantModel):
    razao_social = models.CharField(max_length=200)
    cnpj = models.CharField(max_length=14, blank=True)  # pode ser pessoa física/MEI sem CNPJ
    codigo_prodados = models.CharField(max_length=50, blank=True)
    cadastrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fornecedores_cadastrados",
    )
    ativo = models.BooleanField(default=True)
    # Governança: quem cadastra PODE aprovar compra do mesmo fornecedor —
    # risco aceito, mitigado por relatório de exceções destacando compras de
    # fornecedores cadastrados há menos de 30 dias (Fase 2/3).

    def __str__(self) -> str:
        return self.razao_social
