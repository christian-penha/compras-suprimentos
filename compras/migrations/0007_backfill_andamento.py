from django.db import migrations


def preencher(apps, schema_editor):
    OrdemCompra = apps.get_model("compras", "OrdemCompra")
    OrdemCompra.objects.filter(status="CONCLUIDA").update(andamento="RECEBIDA")


class Migration(migrations.Migration):
    dependencies = [("compras", "0006_andamento_ordem")]
    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
