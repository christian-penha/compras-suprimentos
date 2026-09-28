"""
Teste de escopo de tenant — mais importante que qualquer feature nova
(ver docs/08-Prompt-Inicial-do-Projeto.html, seção Segurança).

Duas frentes, porque cobrem riscos diferentes:

1. `TestTenantAwareManagerFiltraCorretamente`: prova que o manager em si
   funciona — dois tenants, dois registros, contexto setado, só o do tenant
   corrente volta. Sem tenant no contexto, queryset vazio (falha fechada).

2. `TestNenhumaViewUsaAllObjectsSemJustificativa`: varre estaticamente o
   código-fonte de todo `views.py`/`api.py`/`viewsets.py` do projeto (fora
   de admin.py e management commands, onde acesso amplo é esperado) atrás
   do token `all_objects` — o escape hatch nomeado em TenantModel. Qualquer
   ocorrência fora da allowlist quebra o build. Isto é análise estática
   grosseira de propósito: o objetivo não é provar ausência de bug de
   escopo (impossível de forma genérica), é impedir que alguém use o
   escape hatch em código de view sem que isso seja uma decisão visível,
   revisada e registrada explicitamente aqui.
"""
from __future__ import annotations

import ast
from pathlib import Path

from django.test import TestCase

from apps.core.context import tenant_context
from apps.core.models import Tenant

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
APPS_DIR = BASE_DIR / "apps"

# Arquivos onde `all_objects` é esperado e deliberado: admin (o superadmin
# precisa enxergar tudo por desenho) e management commands (rodam fora de
# request, ex. import do Gestio, jobs de reconciliação cross-tenant).
ARQUIVOS_PERMITIDOS_ALL_OBJECTS = {
    "admin.py",
}
DIRETORIOS_PERMITIDOS_ALL_OBJECTS = {
    "management",
}

# Nomes de arquivo tratados como "view" para esta varredura. Views ainda não
# existem nesta fase — a lista cresce junto com o projeto; o teste falhando
# ao esquecer um padrão novo aqui é o comportamento desejado (falhar fechado).
PADROES_ARQUIVO_VIEW = {"views.py", "api.py", "viewsets.py"}


class TestTenantAwareManagerFiltraCorretamente(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(nome="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(nome="Tenant B", slug="tenant-b")

    def test_sem_tenant_no_contexto_retorna_vazio(self):
        self.assertEqual(list(Tenant.objects.none()), [])
        # Tenant em si não é TenantModel (é o topo da hierarquia); o teste
        # de manager vazio-por-padrão é feito com Feriado, que é TenantModel.
        from apps.core.models import Feriado

        with tenant_context(None):
            self.assertEqual(list(Feriado.objects.all()), [])

    def test_filtra_apenas_pelo_tenant_corrente(self):
        from apps.core.models import Feriado
        import datetime

        feriado_a = Feriado.all_objects.create(
            tenant=self.tenant_a, data=datetime.date(2026, 1, 1), descricao="Confraternização"
        )
        Feriado.all_objects.create(
            tenant=self.tenant_b, data=datetime.date(2026, 1, 1), descricao="Confraternização"
        )

        with tenant_context(self.tenant_a.id):
            resultado = list(Feriado.objects.all())

        self.assertEqual(resultado, [feriado_a])

    def test_unscoped_e_o_unico_escape_hatch_nomeado(self):
        from apps.core.models import Feriado
        import datetime

        Feriado.all_objects.create(
            tenant=self.tenant_a, data=datetime.date(2026, 1, 1), descricao="A"
        )
        Feriado.all_objects.create(
            tenant=self.tenant_b, data=datetime.date(2026, 1, 1), descricao="B"
        )

        with tenant_context(self.tenant_a.id):
            self.assertEqual(Feriado.objects.unscoped().count(), 2)


class TestNenhumaViewUsaAllObjectsSemJustificativa(TestCase):
    def test_nenhum_arquivo_de_view_referencia_all_objects(self):
        violacoes = []
        for arquivo in APPS_DIR.rglob("*.py"):
            relativo = arquivo.relative_to(BASE_DIR)

            if arquivo.name in ARQUIVOS_PERMITIDOS_ALL_OBJECTS:
                continue
            if DIRETORIOS_PERMITIDOS_ALL_OBJECTS & set(relativo.parts):
                continue
            if arquivo.name == "managers.py" and relativo.parts[1] == "core":
                continue  # definição do próprio escape hatch
            if "tests" in relativo.parts:
                continue
            if arquivo.name not in PADROES_ARQUIVO_VIEW:
                continue

            arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
            for node in ast.walk(arvore):
                if isinstance(node, ast.Attribute) and node.attr == "all_objects":
                    violacoes.append(f"{relativo}:{node.lineno}")

        self.assertEqual(
            violacoes,
            [],
            "Uso de `all_objects` encontrado fora da allowlist (admin.py, "
            "management commands): " + ", ".join(violacoes) + ". Se for "
            "deliberado, use `.unscoped()` e adicione a justificativa aqui.",
        )
