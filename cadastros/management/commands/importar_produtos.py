from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from openpyxl import load_workbook

from cadastros.models import Categoria, Produto, UnidadeMedida

COLUNAS_ESPERADAS = {"codigo": "código interno", "produto": "produto"}

MAPA_UNIDADES = {
    "UN": UnidadeMedida.UNIDADE,
    "CX": UnidadeMedida.CAIXA,
    "PC": UnidadeMedida.PACOTE,
    "RM": UnidadeMedida.RESMA,
    "KG": UnidadeMedida.KILO,
    "G": UnidadeMedida.GRAMA,
    "L": UnidadeMedida.LITRO,
    "ML": UnidadeMedida.MILILITRO,
    "M": UnidadeMedida.METRO,
    "RL": UnidadeMedida.ROLO,
    "PAR": UnidadeMedida.PAR,
    "KIT": UnidadeMedida.KIT,
}


class Command(BaseCommand):
    help = "Importa produtos de planilha XLSX. Sem --aplicar, roda apenas a prévia (nada é gravado)."

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="Caminho da planilha .xlsx")
        parser.add_argument("--aplicar", action="store_true", help="Grava as alterações no banco")

    def handle(self, *args, **opts):
        try:
            wb = load_workbook(opts["arquivo"], read_only=True, data_only=True)
        except FileNotFoundError:
            raise CommandError(f"Arquivo não encontrado: {opts['arquivo']}")
        ws = wb.active
        ws.reset_dimensions()

        linhas = ws.iter_rows(values_only=True)
        cabecalho = [str(c or "").strip().lower() for c in next(linhas)]

        def coluna(*termos):
            for i, nome in enumerate(cabecalho):
                if any(t in nome for t in termos):
                    return i
            return None

        col_codigo = coluna("código", "codigo")
        col_nome = coluna("produto", "nome")
        col_unidade = coluna("unidade")
        col_ativo = coluna("ativo")
        col_estoque = coluna("estoque")
        if col_codigo is None or col_nome is None:
            raise CommandError(f"Colunas de código/produto não encontradas. Cabeçalho: {cabecalho}")

        categoria_padrao, _ = Categoria.objects.get_or_create(nome="A classificar")

        criados, atualizados, erros, avisos = 0, 0, [], []
        nomes_vistos = {}
        com_estoque = []

        with transaction.atomic():
            for n, linha in enumerate(linhas, start=2):
                codigo = str(linha[col_codigo] or "").strip()
                nome = str(linha[col_nome] or "").strip()
                if not codigo and not nome:
                    continue
                if not codigo:
                    erros.append(f"linha {n}: sem código — '{nome}'")
                    continue
                if not nome:
                    erros.append(f"linha {n}: código {codigo} sem nome de produto")
                    continue

                if nome.lower() in nomes_vistos:
                    avisos.append(
                        f"linha {n}: '{nome}' ({codigo}) duplica o código "
                        f"{nomes_vistos[nome.lower()]} — importado com o código na especificação"
                    )
                    especificacao = f"cód. {codigo}"
                else:
                    especificacao = ""
                    nomes_vistos[nome.lower()] = codigo

                unidade_bruta = str(linha[col_unidade] or "UN").strip().upper() if col_unidade is not None else "UN"
                unidade = MAPA_UNIDADES.get(unidade_bruta)
                if unidade is None:
                    avisos.append(f"linha {n}: unidade '{unidade_bruta}' desconhecida — usado UN")
                    unidade = UnidadeMedida.UNIDADE

                ativo = True
                if col_ativo is not None:
                    ativo = str(linha[col_ativo] or "").strip().lower() in ("verdadeiro", "true", "sim", "1", "")

                if col_estoque is not None:
                    try:
                        if float(linha[col_estoque] or 0) > 0:
                            com_estoque.append(f"{codigo} — {nome}: {linha[col_estoque]}")
                    except (TypeError, ValueError):
                        avisos.append(f"linha {n}: estoque não numérico ('{linha[col_estoque]}') — ignorado")

                _, criado = Produto.objects.update_or_create(
                    codigo=codigo,
                    defaults={
                        "nome": nome,
                        "especificacao": especificacao,
                        "categoria": categoria_padrao,
                        "unidade_medida": unidade,
                        "ativo": ativo,
                    },
                )
                criados += criado
                atualizados += not criado

            if erros:
                self.stdout.write(self.style.ERROR(f"\n{len(erros)} erro(s) — nada foi gravado:"))
                for e in erros:
                    self.stdout.write(f"  {e}")
                transaction.set_rollback(True)
                return

            if not opts["aplicar"]:
                transaction.set_rollback(True)

        self.stdout.write(f"\nProdutos novos: {criados} | atualizados: {atualizados}")
        if avisos:
            self.stdout.write(self.style.WARNING(f"\n{len(avisos)} aviso(s):"))
            for a in avisos:
                self.stdout.write(f"  {a}")
        if com_estoque:
            self.stdout.write(
                f"\n{len(com_estoque)} produto(s) com estoque > 0 na planilha "
                "(saldos NÃO importados — usar a entrada por planilha da Fase 3):"
            )
            for item in com_estoque:
                self.stdout.write(f"  {item}")
        if not opts["aplicar"]:
            self.stdout.write(self.style.WARNING("\nPRÉVIA — nada foi gravado. Use --aplicar para confirmar."))
        else:
            self.stdout.write(self.style.SUCCESS("\nImportação aplicada."))
