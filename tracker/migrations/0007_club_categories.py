"""Club option categories become editable rows (ClubCategory), plus Wood / Hybrid / Putter.

Old ClubOption.category codes are moved onto ClubCategory rows, then the code
column is dropped. Category order: Driver, Driver head, Wood, Hybrid, Iron,
Wedge, Putter, Other.
"""
import django.db.models.deletion
from django.db import migrations, models

CATEGORIES = [  # code used before this migration, name, free text
    ("driver", "Driver", False), ("driver_head", "Driver head", False), (None, "Wood", False),
    (None, "Hybrid", False), ("iron", "Iron", False), ("wedge", "Wedge", False),
    (None, "Putter", False), ("other", "Other", True),
]
NEW_OPTIONS = {
    "Wood": [dict(label=n, type="Wood", club_number=n) for n in ("2W", "3W", "4W", "5W", "6W", "7W", "9W")],
    "Hybrid": [dict(label=n, type="Hybrid", club_number=f"{n}H") for n in ("2", "3", "4", "5", "6", "7")],
    "Putter": [dict(label=n, type="Putter", length_in=n) for n in ("32", "33", "34", "35", "36", "38")],
}


def forwards(apps, schema_editor):
    ClubCategory = apps.get_model("tracker", "ClubCategory")
    ClubOption = apps.get_model("tracker", "ClubOption")
    by_code, by_name = {}, {}
    for order, (code, name, free) in enumerate(CATEGORIES):
        cat, _ = ClubCategory.objects.get_or_create(name=name, defaults={"sort_order": order * 10, "free_text": free})
        by_name[name] = cat
        if code:
            by_code[code] = cat
    for option in ClubOption.objects.all():
        option.category_ref = by_code.get(option.category) or by_name["Other"]
        option.save(update_fields=["category_ref"])
    for name, rows in NEW_OPTIONS.items():
        for order, row in enumerate(rows):
            ClubOption.objects.get_or_create(category="", category_ref=by_name[name], label=row["label"],
                                             defaults={**row, "sort_order": order})


def backwards(apps, schema_editor):
    ClubOption = apps.get_model("tracker", "ClubOption")
    codes = {name: code for code, name, _ in CATEGORIES if code}
    for option in ClubOption.objects.select_related("category_ref"):
        option.category = codes.get(option.category_ref.name, "other")
        option.save(update_fields=["category"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0006_club_options")]

    operations = [
        migrations.CreateModel(
            name="ClubCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=40, unique=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("active", models.BooleanField(default=True)),
                ("free_text", models.BooleanField(default=False, help_text="Show a text box instead of value buttons (like “Other”)")),
            ],
            options={"ordering": ["sort_order", "id"], "verbose_name_plural": "club categories"},
        ),
        migrations.AddField(model_name="japanitem", name="length_in", field=models.CharField(
            blank=True, help_text="e.g. putter length 34", max_length=10, verbose_name="Length (inches)")),
        migrations.AddField(model_name="cluboption", name="length_in", field=models.CharField(
            blank=True, max_length=10, verbose_name="Length (inches)")),
        migrations.AddField(model_name="cluboption", name="category_ref", field=models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="tracker.clubcategory")),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="cluboption", name="category"),
        migrations.RenameField(model_name="cluboption", old_name="category_ref", new_name="category"),
        migrations.AlterField(model_name="cluboption", name="category", field=models.ForeignKey(
            on_delete=django.db.models.deletion.CASCADE, related_name="options", to="tracker.clubcategory")),
        migrations.AlterField(model_name="cluboption", name="club_number", field=models.CharField(
            blank=True, help_text='e.g. "3W", "4H", "5"', max_length=20)),
        migrations.AlterField(model_name="cluboption", name="label", field=models.CharField(
            help_text='Button text, e.g. "10.5", "3W", "Graphite 5", "52°", "34"', max_length=30)),
        migrations.AlterField(model_name="cluboption", name="type", field=models.CharField(
            help_text="Saved as the item type, e.g. Driver, Wood, Hybrid, Putter", max_length=50)),
        migrations.AlterModelOptions(name="cluboption", options={"ordering": ["category__sort_order", "sort_order", "id"]}),
    ]
