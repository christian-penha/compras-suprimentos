from django.db import migrations


def criar(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Filial = apps.get_model("tenancy", "Filial")
    Almoxarifado = apps.get_model("tenancy", "Almoxarifado")
    CentroCusto = apps.get_model("tenancy", "CentroCusto")
    for tenant in Tenant.objects.all():
        central = Almoxarifado.objects.filter(tenant=tenant, central=True).first()
        filial = central.filial if central else Filial.objects.filter(tenant=tenant).first()
        if filial is None:
            continue
        CentroCusto.objects.get_or_create(
            tenant=tenant, filial=filial, nome="Estoque central",
            defaults={"tipo": "ESTOQUE_CENTRAL"},
        )


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0005_centrocusto_tipo")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
