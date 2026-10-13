from django.db import migrations

NOME = "Evento Pedagógico"


def criar(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Motivo = apps.get_model("tenancy", "MotivoRequisicao")
    for tenant in Tenant.objects.all():
        Motivo.objects.update_or_create(
            tenant=tenant, nome=NOME, defaults={"exige_evento": True, "ativo": True}
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0003_motivorequisicao_exige_evento_evento"),
    ]

    operations = [
        migrations.RunPython(criar, migrations.RunPython.noop),
    ]
