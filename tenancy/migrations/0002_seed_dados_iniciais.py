from django.db import migrations

from tenancy.constants import TENANT_PADRAO_NOME, TENANT_PADRAO_SLUG

FILIAIS = [
    ("Colégio Perfil", "CP"),
    ("Escola Villa Criar", "VC"),
    ("Colégio Evolua", "CE"),
    ("Escola Poliana", "EP"),
    ("AICE-BA (Matriz)", "CSC"),
]

ALMOXARIFADOS = {
    "CP": ["Geral", "Ed. Infantil", "Anos Iniciais", "Anos Finais", "Ensino Médio",
           "Fardamento", "Enfermaria", "PLA", "DEEF"],
    "VC": ["Geral", "Ed. Infantil", "Anos Iniciais", "Fardamento"],
    "CE": ["Geral", "Anos Iniciais", "Anos Finais", "Ensino Médio", "Fardamento"],
    "EP": ["Geral", "Ed. Infantil", "Fardamento"],
    "CSC": ["Estoque Central"],
}

MOTIVOS = [
    "Reposição de material",
    "Projeto/Ação Pedagógica",
    "Eventos",
    "Investimento",
    "Conservação e limpeza",
]

CENTROS_CUSTO = [
    "Conservação e Limpeza", "Manutenção", "Pedagógico", "Eventos", "Investimento", "Outros",
]


def aplicar(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Empresa = apps.get_model("tenancy", "Empresa")
    Filial = apps.get_model("tenancy", "Filial")
    Almoxarifado = apps.get_model("tenancy", "Almoxarifado")
    CentroCusto = apps.get_model("tenancy", "CentroCusto")
    MotivoRequisicao = apps.get_model("tenancy", "MotivoRequisicao")

    tenant, _ = Tenant.objects.get_or_create(
        slug=TENANT_PADRAO_SLUG, defaults={"nome": TENANT_PADRAO_NOME}
    )
    empresa, _ = Empresa.objects.get_or_create(
        tenant=tenant, cnpj="00.000.000/0001-00",
        defaults={"razao_social": TENANT_PADRAO_NOME, "nome_fantasia": TENANT_PADRAO_NOME},
    )

    filiais = {}
    for nome, sigla in FILIAIS:
        filiais[sigla], _ = Filial.objects.get_or_create(
            tenant=tenant, sigla=sigla, defaults={"nome": nome, "empresa": empresa}
        )

    for sigla, nomes in ALMOXARIFADOS.items():
        for nome in nomes:
            Almoxarifado.objects.get_or_create(
                tenant=tenant, filial=filiais[sigla], nome=nome,
                defaults={"central": nome == "Estoque Central"},
            )

    for nome in MOTIVOS:
        MotivoRequisicao.objects.get_or_create(tenant=tenant, nome=nome)

    for filial in filiais.values():
        for nome in CENTROS_CUSTO:
            CentroCusto.objects.get_or_create(tenant=tenant, filial=filial, nome=nome)


def reverter(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
