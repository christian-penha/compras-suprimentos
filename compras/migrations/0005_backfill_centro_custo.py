from django.db import migrations


def preencher(apps, schema_editor):
    ItemCotacao = apps.get_model("compras", "ItemCotacao")
    for item in ItemCotacao.objects.filter(
        centro_custo__isnull=True, item_pedido__pedido__centro_custo__isnull=False
    ).select_related("item_pedido__pedido"):
        item.centro_custo_id = item.item_pedido.pedido.centro_custo_id
        item.save(update_fields=["centro_custo"])


class Migration(migrations.Migration):
    dependencies = [
        ("compras", "0004_itemcotacao_centro_custo"),
        ("pedidos", "0006_sugestao_produto"),
    ]
    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
