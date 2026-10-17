"""The Iron option "Graphite 5" becomes "Graphite" (Iron · Graphite, no club number).
Items saved from it (Iron, graphite shaft, club number 5, nothing else) lose the 5 as well."""
from django.db import migrations, models


def rename(apps, schema_editor):
    ClubOption = apps.get_model("tracker", "ClubOption")
    JapanItem = apps.get_model("tracker", "JapanItem")
    ClubOption.objects.filter(category__name="Iron", label="Graphite 5").update(label="Graphite", club_number="")
    (JapanItem.objects.filter(type__iexact="Iron", shaft_material="graphite", club_number="5", head_only=False,
                              loft="", length_in="")
     .update(club_number=""))


def unrename(apps, schema_editor):
    ClubOption = apps.get_model("tracker", "ClubOption")
    ClubOption.objects.filter(category__name="Iron", label="Graphite").update(label="Graphite 5", club_number="5")


class Migration(migrations.Migration):
    dependencies = [("tracker", "0013_name_display_spelling")]
    operations = [
        migrations.AlterField(
            model_name="cluboption",
            name="label",
            field=models.CharField(help_text='Button text, e.g. "10.5", "3W", "Graphite", "52°", "34"', max_length=30),
        ),
        migrations.RunPython(rename, unrename),
    ]
