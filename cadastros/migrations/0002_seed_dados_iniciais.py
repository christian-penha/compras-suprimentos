from django.db import migrations

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

CATEGORIAS = [
    "Escritório", "Eventos", "Pedagógico", "Manutenção", "Limpeza",
    "Enfermaria", "TI", "Alimentos e bebidas", "Fardamento",
]

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

ALCADAS = [
    ("0.00", "300.00", "AUTOMATICA"),
    ("300.01", "15000.00", "DIRETORIA_UNIDADE"),
    ("15000.01", "45000.00", "CFO"),
    ("45000.01", None, "CEO"),
]


def aplicar(apps, schema_editor):
    Filial = apps.get_model("cadastros", "Filial")
    Categoria = apps.get_model("cadastros", "Categoria")
    MotivoRequisicao = apps.get_model("cadastros", "MotivoRequisicao")
    CentroCusto = apps.get_model("cadastros", "CentroCusto")
    Almoxarifado = apps.get_model("estoque", "Almoxarifado")
    Alcada = apps.get_model("contas", "Alcada")

    filiais = {}
    for nome, sigla in FILIAIS:
        filiais[sigla], _ = Filial.objects.get_or_create(nome=nome, defaults={"sigla": sigla})

    for sigla, nomes in ALMOXARIFADOS.items():
        for nome in nomes:
            Almoxarifado.objects.get_or_create(
                filial=filiais[sigla], nome=nome,
                defaults={"central": nome == "Estoque Central"},
            )

    for nome in CATEGORIAS:
        Categoria.objects.get_or_create(nome=nome)

    for nome in MOTIVOS:
        MotivoRequisicao.objects.get_or_create(nome=nome)

    for filial in filiais.values():
        for nome in CENTROS_CUSTO:
            CentroCusto.objects.get_or_create(filial=filial, nome=nome)

    for valor_min, valor_max, papel in ALCADAS:
        Alcada.objects.get_or_create(
            valor_min=valor_min, valor_max=valor_max, defaults={"papel_aprovador": papel}
        )


def reverter(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0001_initial"),
        ("estoque", "0001_initial"),
        ("contas", "0002_alcada_vinculoaprovacao"),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
