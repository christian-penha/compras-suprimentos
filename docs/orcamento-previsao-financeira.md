# Orçamento e previsão financeira — estado e próximos passos

Origem: conversa com o CEO (mais controle sobre as contas dos centros de custo). Palavras-chave:
previsão financeira, gestão de orçamento e, mais adiante, controle de patrimônio.

## Decisões tomadas

- **Base do gasto:** desembolso (valor das cotações aprovadas no mês), não o consumo do estoque.
- **Estouro do limite do mês:** permitido com justificativa e aprovação pela alçada configurada.
- **Subcontas:** um nível; orçamento próprio; a soma das subcontas não passa do orçamento da conta pai; o gasto da subconta sobe para a conta pai.
- **Recálculo:** só o estouro redistribui. R$ 1.200 em 12 meses = R$ 100/mês; gastou R$ 100 até o 7º mês e R$ 200 no 8º, os 4 meses restantes passam a R$ 75. Mês que gastou menos não devolve saldo.

## Já implementado (etapa 1)

| O quê | Onde |
|---|---|
| `CentroCusto.pai` (subconta, um nível) e `Cotacao.aviso_orcamento` (migrações `tenancy/0008`, `compras/0008`) | `tenancy/models.py`, `compras/models.py` |
| Motor: `gasto_do_mes`, `plano` (mês a mês, com recálculo), `limite_do_mes`, `avisos_de_estouro` | `tenancy/previsao.py` |
| Estouro exige justificativa (campo observação) e sobe para a menor faixa de alçada com aprovador | `compras/services.py` (`criar_cotacao`, `_alcada_do_estouro`), `compras/rodadas.py` (`fechar_rodada`) |
| Formulário do centro: conta pai com filtro por filial, validação de filial e da soma das subcontas | `configuracoes/forms.py`, `templates/configuracoes/centro_form.html` |
| Aviso laranja no detalhe da cotação | `templates/compras/cotacao_detalhe.html` |
| Tela Relatórios → Orçamento (mês a mês, limite recalculado, saldo) | `pedidos/views.py::relatorio_orcamento`, `templates/pedidos/relatorio_orcamento.html`, rota `relatorio_orcamento` |
| Testes | `compras/tests.py::OrcamentoEExtrapolacaoTest` |

`tenancy/orcamento.py` (orçamento base, eixo consumo) continua como estava; a previsão nova fica em `tenancy/previsao.py`.

## Limites conhecidos

- O gasto só conta cotações **aprovadas**: duas compras pendentes de alçada podem, juntas, passar do limite sem aviso em nenhuma.
- Frete fora do gasto (não tem centro de custo).
- Na comparação entre fornecedores (`fechar_rodada`) a justificativa não é exigida; o estouro vai para a alçada com o aviso.
- A soma das subcontas é conferida só no mês atual.
- A tela de orçamento ainda não foi vista no navegador; os testes cobrem a lógica.

## Falta fazer

1. **Orçamento mês a mês específico** (meses de maior custo): hoje só há valor total dividido ou mensal fixo. Ideia: tabela `OrcamentoMes(centro, ano, mes, valor)` que sobrepõe o mês; os meses sem valor próprio dividem o restante.
2. **Comprador do setor (cartão):** o setor pode ou não ter a função de comprador; esse usuário lança a compra feita, com método de pagamento e nota fiscal anexada. A compra lançada deve contar no gasto do centro. Ainda sem desenho de telas.
3. **Previsão financeira:** projeção do ano, gráfico, alertas (ex.: 80% do limite).
4. **Controle de patrimônio:** mais adiante.
5. Considerar contar cotações pendentes de alçada como "comprometido".

## Outras pendências do projeto

- Chat por pedido: primeira versão feita (`MensagemPedido`, seção "Conversa do pedido"); sem notificação, anexo ou atualização automática.
- Pacote do notebook: `instalar_notebook.ps1` + pasta `privado/apresentacao/` (não vai para o GitHub). O dump é anterior às migrações `pedidos/0008`, `tenancy/0008` e `compras/0008`: rodar `manage.py migrate` depois de restaurar.
- Kanban da fila: arrastar para OC/cotações não foi feito; arrastar só funciona com mouse.
