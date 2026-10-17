from django.db import migrations, models

# Old item types that mean "the golf bag" (the old item card had a "Bag" type).
BAG_TYPES = {"bag", "golf bag"}


def mark_existing_bags(apps, schema_editor):
    """A box that already has exactly one item of type Bag gets it as its golf bag.

    Boxes with no bag item, or with several, are left alone; the box page shows
    "No bag recorded" so the owner can fix them.
    """
    JapanItem = apps.get_model("tracker", "JapanItem")
    by_box = {}
    for item in JapanItem.objects.only("id", "box_id", "type", "club_type"):
        if (item.type or "").strip().lower() in BAG_TYPES or (item.club_type or "").strip().lower() in BAG_TYPES:
            by_box.setdefault(item.box_id, []).append(item.pk)
    ids = [pks[0] for pks in by_box.values() if len(pks) == 1]
    JapanItem.objects.filter(pk__in=ids).update(is_bag=True)


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0007_club_categories"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="japanitem",
            options={"ordering": ["box", "-is_bag", "id"]},
        ),
        migrations.AddField(
            model_name="japanitem",
            name="is_bag",
            field=models.BooleanField(
                default=False,
                help_text="The box's golf bag (every box holds exactly one; the other items are packed inside it)",
                verbose_name="Golf bag",
            ),
        ),
        migrations.RunPython(mark_existing_bags, migrations.RunPython.noop),
    ]
