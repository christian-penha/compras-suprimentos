from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from contas.models import Alcada, PapelAlcada
from estoque.models import SaldoEstoque
from tenancy.models import Almoxarifado, Filial
from tenancy.testing import TenantTestCase

from .models import Categoria, Produto

Usuario = get_user_model()


class SeedDadosIniciaisTest(TenantTestCase):
    def test_filiais_criadas(self):
        self.assertEqual(Filial.objects.count(), 5)
        self.assertTrue(Filial.objects.filter(sigla="CSC").exists())

    def test_almoxarifados_criados_com_um_central(self):
        self.assertEqual(Almoxarifado.objects.count(), 22)
        self.assertEqual(Almoxarifado.objects.filter(central=True).count(), 1)

    def test_alcadas_seed(self):
        self.assertEqual(Alcada.objects.count(), 4)


class AlcadaParaValorTest(TenantTestCase):
    def test_faixa_automatica(self):
        alcada = Alcada.para_valor(Decimal("299.99"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.AUTOMATICA)

    def test_limite_exato_300(self):
        alcada = Alcada.para_valor(Decimal("300.00"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.AUTOMATICA)

    def test_faixa_diretoria(self):
        alcada = Alcada.para_valor(Decimal("300.01"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.DIRETORIA_UNIDADE)

    def test_faixa_cfo(self):
        alcada = Alcada.para_valor(Decimal("20000.00"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.CFO)

    def test_faixa_ceo_sem_teto(self):
        alcada = Alcada.para_valor(Decimal("1000000.00"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.CEO)


class ConstraintsTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.categoria = Categoria.objects.get(nome="Escritório")
        self.produto = Produto.objects.create(
            nome="Papel A4", especificacao="75g branco — resma", categoria=self.categoria
        )
        self.central = Almoxarifado.objects.get(central=True)

    def test_produto_duplicado_rejeitado(self):
        with self.assertRaises(IntegrityError):
            Produto.objects.create(
                nome="Papel A4", especificacao="75g branco — resma", categoria=self.categoria
            )

    def test_segundo_almoxarifado_central_rejeitado(self):
        filial = Filial.objects.get(sigla="CP")
        with self.assertRaises(IntegrityError):
            Almoxarifado.objects.create(
                tenant=self.tenant, filial=filial, nome="Outro Central", central=True
            )

    def test_saldo_negativo_rejeitado(self):
        with self.assertRaises(IntegrityError):
            SaldoEstoque.objects.create(
                produto=self.produto, almoxarifado=self.central, quantidade=Decimal("-1")
            )



# ============================================================ banco de imagens: exportar / importar
import io as _io  # noqa: E402
import shutil as _shutil  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import zipfile as _zipfile  # noqa: E402
from unittest import mock as _mock  # noqa: E402

from django.core.files.base import ContentFile as _ContentFile  # noqa: E402
from django.core.management import call_command as _call_command  # noqa: E402
from django.test import override_settings as _override_settings  # noqa: E402
from PIL import Image as _Image  # noqa: E402


def _png(cor="red"):
    buf = _io.BytesIO()
    _Image.new("RGB", (4, 4), cor).save(buf, "PNG")
    return buf.getvalue()


class _RespostaFalsa:
    def __init__(self, conteudo):
        self.conteudo = conteudo

    def raise_for_status(self):
        pass

    def iter_content(self, tamanho):
        yield self.conteudo


class BancoDeImagensTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.media = _tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, self.media, ignore_errors=True)
        ajuste = _override_settings(MEDIA_ROOT=self.media)
        ajuste.enable()
        self.addCleanup(ajuste.disable)
        self.categoria = Categoria.objects.first()

    def produto(self, codigo, nome, **extra):
        return Produto.objects.create(codigo=codigo, nome=nome, especificacao="un", categoria=self.categoria, **extra)

    def rodar(self, comando, *args):
        saida = _io.StringIO()
        _call_command(comando, *args, stdout=saida)
        return saida.getvalue()

    def sessao_falsa(self, conteudo):
        sessao = _mock.Mock()
        sessao.get.return_value = _RespostaFalsa(conteudo)
        return _mock.patch("requests.Session", return_value=sessao)

    def test_exporta_anexo_e_link_e_importa_em_outro_banco_pelo_codigo(self):
        anexo = self.produto("PAPOFI-001", "Papel ofício")
        anexo.imagem.save("qualquer.png", _ContentFile(_png("blue")))
        self.produto("CONT REMOT", "Controle remoto", imagem_url="https://glide.example/foto.png")
        self.produto(None, "Sem código", imagem_url="https://glide.example/x.png")
        zip_path = f"{self.media}/imagens.zip"
        with self.sessao_falsa(_png("green")):
            saida = self.rodar("exportar_imagens", zip_path)
        self.assertIn("2 imagem(ns) exportada(s)", saida)
        self.assertIn("SEM código", saida)
        with _zipfile.ZipFile(zip_path) as zf:
            manifesto = zf.read("manifesto.csv").decode("utf-8-sig")
        self.assertIn("CONT REMOT", manifesto)  # o código exato vai no manifesto
        self.assertIn("CONT_REMOT.png", manifesto)  # o nome do arquivo é seguro

        # "banco novo": mesmos códigos, sem imagem
        Produto.objects.all().delete()
        novo_papel = self.produto("PAPOFI-001", "Papel ofício")
        novo_controle = self.produto("CONT REMOT", "Controle remoto")

        previa = self.rodar("importar_imagens", zip_path)
        self.assertIn("a gravar: 2", previa)
        novo_papel.refresh_from_db()
        self.assertFalse(novo_papel.imagem)  # prévia não grava

        self.rodar("importar_imagens", zip_path, "--aplicar")
        novo_papel.refresh_from_db()
        novo_controle.refresh_from_db()
        self.assertTrue(novo_papel.imagem.name.startswith("produtos/PAPOFI-001"))
        self.assertTrue(novo_controle.imagem)
        with novo_papel.imagem.open("rb") as f:
            self.assertEqual(_Image.open(f).getpixel((0, 0)), (0, 0, 255))

    def test_nao_substitui_sem_flag_e_recusa_arquivo_que_nao_e_imagem(self):
        p = self.produto("TESO-001", "Tesoura")
        p.imagem.save("antiga.png", _ContentFile(_png("red")))
        self.produto("COLA-001", "Cola")
        zip_path = f"{self.media}/manual.zip"
        with _zipfile.ZipFile(zip_path, "w") as zf:  # zip montado à mão: nome = código
            zf.writestr("fotos/TESO-001.png", _png("green"))
            zf.writestr("COLA-001.jpg", b"isto nao e imagem")
            zf.writestr("NAOEXISTE-9.png", _png())
        saida = self.rodar("importar_imagens", zip_path, "--aplicar")
        self.assertIn("já tinha foto: 1", saida)
        self.assertIn("sem produto com o código: 1", saida)
        self.assertIn("inválidas: 1", saida)
        p.refresh_from_db()
        with p.imagem.open("rb") as f:
            self.assertEqual(_Image.open(f).getpixel((0, 0)), (255, 0, 0))  # continua a antiga

        self.rodar("importar_imagens", zip_path, "--aplicar", "--substituir")
        p.refresh_from_db()
        with p.imagem.open("rb") as f:
            self.assertEqual(_Image.open(f).getpixel((0, 0)), (0, 128, 0))

    def test_baixar_links_traz_a_imagem_para_o_sistema(self):
        p = self.produto("EVA-001", "EVA", imagem_url="https://glide.example/eva.png")
        self.produto("EVA-002", "EVA 2", imagem_url="http://inseguro.example/eva.png")
        with self.sessao_falsa(_png("green")):
            saida = self.rodar("importar_imagens", "--baixar-links", "--aplicar")
        self.assertIn("gravadas: 1 (das quais 1 baixadas de links)", saida)
        p.refresh_from_db()
        self.assertTrue(p.imagem)
        self.assertEqual(p.imagem_exibicao, p.imagem.url)  # o arquivo local passa a ter prioridade


class ImportarImagensGlidePorCodigoTest(TenantTestCase):
    def test_de_para_xlsx_casa_pelo_codigo_e_pula_codigo_em_branco(self):
        from openpyxl import Workbook

        categoria = Categoria.objects.first()
        p = Produto.objects.create(codigo="ABAC-001", nome="Abacaxi fruta", especificacao="un", categoria=categoria)
        Produto.objects.create(codigo="UVA-001", nome="Uva", especificacao="un", categoria=categoria)
        pasta = _tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, pasta, ignore_errors=True)
        caminho = f"{pasta}/depara.xlsx"
        wb = Workbook()
        wb.active.append(["Código", "Produto (Gestio)", "Material (Glide)", "Foto", "Situação"])
        wb.active.append(["ABAC-001", "Abacaxi fruta", "Abacaxi", "https://glide.example/a.png", "OK"])
        wb.active.append([None, "", "Uva Roxa Caixa", "https://glide.example/u.png", "CONFERIR"])
        wb.active.append(["NAO-EXISTE", "", "X", "https://glide.example/x.png", "OK"])
        wb.save(caminho)

        saida = _io.StringIO()
        _call_command("importar_imagens_glide", caminho, "--aplicar", stdout=saida)
        self.assertIn("Links a gravar: 1", saida.getvalue())
        self.assertIn("código em branco (pulados): 1", saida.getvalue())
        self.assertIn("NAO-EXISTE", saida.getvalue())
        p.refresh_from_db()
        self.assertEqual(p.imagem_url, "https://glide.example/a.png")
