# Modelo: centro de custo em dois eixos (RN-17)

Dois eixos que **nunca se somam**, cada um definido por um setor:

| Eixo | Pergunta | Quem define | Fato gerador | Valor | Relatório |
|---|---|---|---|---|---|
| Desembolso | Quanto saiu do caixa, e para quê? | **Compras** | Nota fiscal registrada | Valor da nota (itens + frete rateado) | RF-38 |
| Consumo | Quanto cada centro consumiu? | **Suprimentos** | Entrega/baixa do estoque | Quantidade × custo médio na baixa | RF-39 |

Decisão (v2): **evento não é centro de custo**. Não há derivação automática por evento,
setor ou filial. O requisitante não escolhe nada; compras define o centro na cotação e
suprimentos define o centro na entrega. A automação pode vir depois, sem mudar o modelo.

## 1. Cadastro

- `CentroCusto.tipo`: `OPERACIONAL` (padrão) | `ESTOQUE_CENTRAL`. Um `ESTOQUE_CENTRAL`
  por tenant, criado por migration; recebe o desembolso das compras para reposição (RF-24).
- Nenhum campo novo em Setor, Evento ou Filial.

## 2. Eixo desembolso — compras

- `ItemCotacao.centro_custo` (FK, opcional na criação, **obrigatório para gerar a OC**).
- Escolhido pelo comprador na tela de cotação, por item (com opção "aplicar a todos").
  Subcentro opcional.
- Frete rateado entre centros proporcionalmente ao subtotal dos itens de cada centro.
- Ao registrar a nota, o total real (itens + frete da nota) é distribuído pelos centros na
  proporção da cotação; divergência fica registrada.
- Data do relatório = emissão da nota (RF-38).

## 3. Eixo consumo — suprimentos

- `Movimentacao.centro_custo` (+ `subcentro`), nulos, preenchidos em `entregar()`.
- Escolhido por suprimentos na tela do pedido, antes de entregar. O `Pedido.centro_custo`
  existente passa a ser o campo de trabalho de suprimentos (o requisitante não o vê).
- **A entrega exige centro de custo definido** (evita custo sem dono).
- Valor = `quantidade × valor_unitario` da movimentação (custo médio da baixa, já gravado).
  Ledger imutável (RN-03); devolução herda o centro da movimentação original.
- Alteração do centro antes da entrega gera `EventoPedido` (quem, de → para).

## 4. Telas

- Cotação (compras): coluna "Centro de custo" por item.
- Pedido (suprimentos): campo "Centro de custo" + subcentro; entrega bloqueada sem ele.
- Relatórios: "Desembolso por centro" e "Consumo por centro", lado a lado, sem total geral.

## 5. Migração

1. Campos novos nulos (sem quebra); criar `ESTOQUE_CENTRAL` por tenant.
2. Backfill opcional: `Movimentacao.centro_custo` ← `pedido.centro_custo` quando existir.
   O que não tiver aparece como "Sem centro".

## 6. Testes mínimos

- OC não é gerada com item sem centro; entrega não ocorre sem centro no pedido.
- Entrega grava centro e valor a custo médio na movimentação.
- Cotação com itens de dois centros: frete rateado fecha com o total.
- Relatórios não somam os dois eixos.
- Alteração de centro registra evento do pedido.

## 7. Fica para depois

- Sugestão automática de centro (por setor/evento/filial) quando o uso mostrar o padrão.
- Centro padrão por setor.
