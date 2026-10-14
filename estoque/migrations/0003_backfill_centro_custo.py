from django.db import migrations


def preencher(apps, schema_editor):
    Movimentacao = apps.get_model("estoque", "Movimentacao")
    # entregas anteriores herdam o centro do pedido, quando ele tinha um
    for mov in Movimentacao.objects.filter(pedido__centro_custo__isnull=False, centro_custo__isnull=True):
        mov.centro_custo_id = mov.pedido.centro_custo_id
        mov.save(update_fields=["centro_custo"])


class Migration(migrations.Migration):
    dependencies = [
        ("estoque", "0002_movimentacao_centro_custo"),
        ("pedidos", "0006_sugestao_produto"),
    ]
    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
