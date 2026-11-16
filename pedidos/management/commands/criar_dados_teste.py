"""Orçamentos fictícios e pedidos de teste para o pessoal treinar no sistema.

Os pedidos passam pelas mesmas regras das telas (envio, aprovação do líder, classificação
estoque/compra), então cada um cai na fila certa. Todos têm a descrição começando por [TESTE]
e saem com --remover.
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cadastros.models import Produto
from estoque.models import SaldoEstoque
from pedidos.forms import almoxarifados_do_usuario
from pedidos.models import ItemPedido, Pedido, PrioridadePedido, StatusPedido
from pedidos.services import (
    aprovacao_pendente,
    aprovar_como_lider,
    cancelar_pedido,
    e_lider_do_proprio_setor,
    lideres_do_setor,
    tem_lider_disponivel,
    transicionar,
)
from tenancy.calendario import prazos_sla
from tenancy.context import definir_tenant_atual, limpar_tenant_atual
from tenancy.models import CentroCusto, Evento, ModoOrcamento, MotivoRequisicao, OrcamentoMes, Tenant, TipoCentroCusto

MARCA = "[TESTE]"
DESCRICOES = [
    "Material para atividade em sala",
    "Reposição do setor",
    "Preparação do evento da unidade",
    "Material de uso diário",
    "Projeto pedagógico do bimestre",
    "Manutenção preventiva",
    "Organização do almoxarifado da sala",
]
# Ciclo das situações finais dos pedidos (repete até completar a quantidade pedida).
ROTEIRO = ["enviado", "aprovado", "enviado", "aprovado", "aprovado", "recusado", "cancelado"]
VALORES_MENSAIS = [Decimal(v) for v in (1500, 2000, 3000, 4000, 5000, 8000, 12000)]


class Command(BaseCommand):
    help = (
        "Cria orçamentos fictícios nos centros de custo sem orçamento e pedidos de teste ([TESTE]) "
        "em várias etapas, usando os produtos e usuários já cadastrados. Sem --aplicar, só mostra a prévia. "
        "--remover apaga os pedidos de teste; --zerar-orcamentos volta todos os centros para 'sem orçamento'."
    )

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Grava (sem isso, é só prévia)")
        parser.add_argument("--pedidos", type=int, default=30, help="Quantos pedidos criar (padrão 30)")
        parser.add_argument("--usuarios", help="Logins dos requisitantes, separados por vírgula (padrão: todos os aptos)")
        parser.add_argument("--sem-orcamentos", action="store_true", help="Não mexe nos orçamentos")
        parser.add_argument("--sem-pedidos", action="store_true", help="Só os orçamentos")
        parser.add_argument("--remover", action="store_true", help="Apaga os pedidos de teste")
        parser.add_argument("--zerar-orcamentos", action="store_true", help="Volta todos os centros para 'sem orçamento'")
        parser.add_argument("--tenant", help="Slug do tenant (padrão: o primeiro)")
        parser.add_argument("--semente", type=int, help="Semente do sorteio, para repetir o mesmo resultado")

    def handle(self, *args, **o):
        tenant = Tenant.objects.filter(slug=o["tenant"]).first() if o["tenant"] else Tenant.objects.order_by("pk").first()
        if tenant is None:
            raise CommandError("Tenant não encontrado.")
        self.sorteio = random.Random(o["semente"])
        token = definir_tenant_atual(tenant.pk)
        try:
            with transaction.atomic():
                if o["remover"] or o["zerar_orcamentos"]:
                    if o["remover"]:
                        self.remover_pedidos()
                    if o["zerar_orcamentos"]:
                        self.zerar_orcamentos()
                else:
                    if not o["sem_orcamentos"]:
                        self.criar_orcamentos()
                    if not o["sem_pedidos"]:
                        self.criar_pedidos(tenant, o["pedidos"], o["usuarios"])
                if not o["aplicar"]:
                    transaction.set_rollback(True)
        finally:
            limpar_tenant_atual(token)
        self.stdout.write(
            self.style.SUCCESS("Aplicado.") if o["aplicar"]
            else self.style.WARNING("PRÉVIA — nada foi gravado. Use --aplicar para confirmar.")
        )

    # ------------------------------------------------------------------ orçamentos
    def criar_orcamentos(self):
        hoje = date.today()
        inicio = date(hoje.year, 1, 1)
        centros = CentroCusto.objects.filter(
            ativo=True, tipo=TipoCentroCusto.OPERACIONAL, orcamento_modo=ModoOrcamento.SEM_ORCAMENTO,
        ).select_related("filial", "pai")
        criados = 0
        for centro in centros:
            valor = self.sorteio.choice(VALORES_MENSAIS)
            if centro.pai_id:  # subconta: uma fatia menor
                valor = max(Decimal("500"), (valor / 3).quantize(Decimal("100")))
            centro.orcamento_modo = ModoOrcamento.MENSAL
            centro.orcamento_valor = valor
            centro.orcamento_inicio = inicio
            centro.orcamento_meses = 12
            centro.save(update_fields=["orcamento_modo", "orcamento_valor", "orcamento_inicio", "orcamento_meses"])
            # meses com valor próprio, para a grade mês a mês ter exemplo: volta às aulas e férias
            OrcamentoMes.objects.update_or_create(
                centro=centro, ano_mes=date(hoje.year, 2, 1), defaults={"valor": valor * Decimal("1.5"), "tenant": centro.tenant},
            )
            OrcamentoMes.objects.update_or_create(
                centro=centro, ano_mes=date(hoje.year, 7, 1), defaults={"valor": valor / 2, "tenant": centro.tenant},
            )
            criados += 1
        self.stdout.write(
            f"Orçamentos fictícios: {criados} centro(s) de custo sem orçamento receberam valor mensal "
            f"(jan a dez/{hoje.year}, fevereiro +50% e julho -50%)."
        )

    def zerar_orcamentos(self):
        centros = CentroCusto.objects.exclude(orcamento_modo=ModoOrcamento.SEM_ORCAMENTO)
        OrcamentoMes.objects.filter(centro__in=centros).delete()
        total = centros.update(
            orcamento_modo=ModoOrcamento.SEM_ORCAMENTO, orcamento_valor=None, orcamento_inicio=None, orcamento_meses=None,
        )
        self.stdout.write(f"Orçamentos zerados: {total} centro(s) voltaram para 'sem orçamento'.")

    # ------------------------------------------------------------------ pedidos
    def requisitantes(self, tenant, logins):
        Usuario = get_user_model()
        qs = Usuario.objects.filter(tenant=tenant, is_active=True, ativo_no_sistema=True, setor__isnull=False)
        if logins:
            nomes = [n.strip() for n in logins.split(",") if n.strip()]
            qs = qs.filter(username__in=nomes)
            faltando = set(nomes) - set(qs.values_list("username", flat=True))
            if faltando:
                raise CommandError(f"Usuário(s) não encontrado(s), inativo(s) ou sem setor: {', '.join(sorted(faltando))}")
        aptos, fora = [], []
        for u in qs.select_related("setor", "filial").order_by("username"):
            if not almoxarifados_do_usuario(u):
                fora.append(f"{u.username} (sem almoxarifado)")
            elif not (tem_lider_disponivel(u) or e_lider_do_proprio_setor(u)):
                fora.append(f"{u.username} (setor sem líder)")
            else:
                aptos.append(u)
        return aptos, fora

    def escolher_itens(self, almoxarifado, com_estoque):
        """Produtos do pedido: parte com saldo no almoxarifado (vai para separação), parte sem (vai para compra)."""
        itens = {}
        if com_estoque:
            saldos = list(
                SaldoEstoque.objects.filter(almoxarifado=almoxarifado, quantidade__gte=2, produto__ativo=True)
                .select_related("produto").order_by("?")[:3]
            )
            for s in saldos[: self.sorteio.randint(1, 3)]:
                itens[s.produto] = Decimal(self.sorteio.randint(1, max(1, min(5, int(s.quantidade) // 2))))
        for p in Produto.objects.filter(ativo=True).order_by("?")[: self.sorteio.randint(1, 3)]:
            itens.setdefault(p, Decimal(self.sorteio.randint(1, 10)))
        return itens

    def criar_pedidos(self, tenant, quantidade, logins):
        if not Produto.objects.filter(ativo=True).exists():
            raise CommandError("Não há produtos ativos. Importe os produtos antes (importar_produtos).")
        motivos = list(MotivoRequisicao.objects.filter(ativo=True))
        if not motivos:
            raise CommandError("Não há motivos de requisição ativos.")
        aptos, fora = self.requisitantes(tenant, logins)
        for linha in fora:
            self.stdout.write(f"  fora dos testes: {linha}")
        if not aptos:
            raise CommandError(
                "Nenhum usuário apto a pedir: é preciso ter setor com líder e almoxarifado. "
                "Cadastre os usuários e setores antes."
            )

        contagem = {}
        for n in range(quantidade):
            requisitante = aptos[n % len(aptos)]
            situacao = ROTEIRO[n % len(ROTEIRO)]
            almoxarifado = self.sorteio.choice(almoxarifados_do_usuario(requisitante))
            filial = almoxarifado.filial
            motivo = self.sorteio.choice(motivos)
            evento = None
            if motivo.exige_evento:
                evento = Evento.objects.filter(filial=filial, ativo=True).order_by("?").first()
                if evento is None:
                    motivo = next((m for m in motivos if not m.exige_evento), None)
                    if motivo is None:
                        continue
            centro = CentroCusto.objects.filter(filial=filial, ativo=True, tipo=TipoCentroCusto.OPERACIONAL).order_by("?").first()
            pedido = Pedido.objects.create(
                requisitante=requisitante, almoxarifado_destino=almoxarifado, centro_custo=centro,
                motivo=motivo, evento=evento,
                prioridade=self.sorteio.choice([PrioridadePedido.NORMAL] * 3 + [PrioridadePedido.ALTA, PrioridadePedido.URGENTE]),
                data_desejada=prazos_sla(filial)["compra"] + timedelta(days=self.sorteio.randint(0, 10)),
                observacao=f"{MARCA} {self.sorteio.choice(DESCRICOES)}",
            )
            for produto, qtd in self.escolher_itens(almoxarifado, com_estoque=situacao == "aprovado").items():
                ItemPedido.objects.create(pedido=pedido, produto=produto, quantidade=qtd)

            transicionar(pedido, StatusPedido.ENVIADO, requisitante)
            lideres = lideres_do_setor(requisitante)
            if pedido.status == StatusPedido.ENVIADO and lideres:
                if situacao == "aprovado":
                    for lider in lideres:
                        if not aprovacao_pendente(pedido):
                            break
                        aprovar_como_lider(pedido, lider, "Aprovado (pedido de teste).")
                elif situacao == "recusado":
                    transicionar(pedido, StatusPedido.RECUSADO, lideres[0], "Recusado para teste: rever as quantidades.")
            if situacao == "cancelado" and pedido.status == StatusPedido.ENVIADO:
                cancelar_pedido(pedido, requisitante, "Cancelado para teste: pedido feito em duplicidade.")
            pedido.refresh_from_db()
            rotulo = pedido.get_status_display()
            contagem[rotulo] = contagem.get(rotulo, 0) + 1

        self.stdout.write(
            f"Pedidos de teste: {sum(contagem.values())} para {len(aptos)} requisitante(s)"
            + "".join(f"\n  {rotulo}: {total}" for rotulo, total in sorted(contagem.items()))
        )

    def remover_pedidos(self):
        teste = Pedido.objects.filter(observacao__startswith=MARCA, almoxarifado_destino__tenant_id=self.tenant_id())
        presos = teste.filter(movimentacoes__isnull=False) | teste.filter(itens__itens_cotacao__isnull=False)
        presos_ids = set(presos.values_list("pk", flat=True))
        livres = teste.exclude(pk__in=presos_ids)
        total = livres.count()
        livres.delete()
        self.stdout.write(f"Pedidos de teste apagados: {total}.")
        if presos_ids:
            self.stdout.write(self.style.WARNING(
                f"{len(presos_ids)} pedido(s) de teste já têm cotação ou baixa de estoque e NÃO foram apagados "
                f"(cancele-os pela tela): {', '.join(f'#{pk}' for pk in sorted(presos_ids))}"
            ))

    def tenant_id(self):
        from tenancy.context import obter_tenant_atual_id
        return obter_tenant_atual_id()
