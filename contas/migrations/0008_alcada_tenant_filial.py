import django.db.models.deletion
from django.db import migrations, models


def atribuir_tenant(apps, schema_editor):
    """As faixas existentes eram globais; passam a pertencer ao tenant existente."""
    Alcada = apps.get_model("contas", "Alcada")
    Tenant = apps.get_model("tenancy", "Tenant")
    tenant = Tenant.objects.order_by("pk").first()
    if tenant is not None:
        Alcada.objects.filter(tenant__isnull=True).update(tenant=tenant)


class Migration(migrations.Migration):

    dependencies = [
        ("contas", "0007_aprovadoralcada"),
        ("tenancy", "0004_motivo_evento_pedagogico"),
    ]

    operations = [
        migrations.AddField(
            model_name="alcada",
            name="tenant",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+",
                to="tenancy.tenant",
            ),
        ),
        migrations.AddField(
            model_name="alcada",
            name="filial",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="alcadas", to="tenancy.filial",
            ),
        ),
        migrations.RunPython(atribuir_tenant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="alcada",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="+", to="tenancy.tenant",
            ),
        ),
    ]
