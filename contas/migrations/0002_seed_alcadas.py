from django.db import migrations

ALCADAS = [
    ("0.00", "300.00", "AUTOMATICA"),
    ("300.01", "15000.00", "DIRETORIA_UNIDADE"),
    ("15000.01", "45000.00", "CFO"),
    ("45000.01", None, "CEO"),
]


def aplicar(apps, schema_editor):
    Alcada = apps.get_model("contas", "Alcada")
    for valor_min, valor_max, papel in ALCADAS:
        Alcada.objects.get_or_create(
            valor_min=valor_min, valor_max=valor_max, defaults={"papel_aprovador": papel}
        )


def reverter(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("contas", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
