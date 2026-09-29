from django.db import migrations

CATEGORIAS = [
    "Escritório", "Eventos", "Pedagógico", "Manutenção", "Limpeza",
    "Enfermaria", "TI", "Alimentos e bebidas", "Fardamento",
]


def aplicar(apps, schema_editor):
    Categoria = apps.get_model("cadastros", "Categoria")
    for nome in CATEGORIAS:
        Categoria.objects.get_or_create(nome=nome)


def reverter(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
