"""Item cards get a Brand dropdown + Model box.

- Brands: exactly the list in tracker.brands.BRAND_LIST (Cleveland -> Cleveland Golf, Wilson -> Wilson Staff,
  Ping -> PING, Parsons Xtreme Golf -> PXG; their saved models move along). Other brands are kept but inactive.
- Items and order lines: renamed brands are renamed on them too ("Ping G430" -> "PING G430"); a brand that is not on
  the list becomes "Other" (no brand) with the whole name as the model ("Honma TW757"), so the name stays the same.
  A name with no brand that starts with a list brand is split ("XXIO MP1400" -> XXIO + MP1400).
- Order lines get the same fields as box items (club option, flex, note, piece prices): a chosen club option is
  copied in, an old text label ("Driver · S · 10.5°") is kept as free text.
"""
from django.db import migrations, models

import tracker.models
from tracker.brands import BRAND_ALIASES, BRAND_LIST, match_key, split_brand


def brands_and_names(apps, schema_editor):
    Brand = apps.get_model("tracker", "Brand")
    BrandModel = apps.get_model("tracker", "BrandModel")
    listed = {match_key(n): n for n in BRAND_LIST}
    for old_key, new_name in BRAND_ALIASES.items():
        old = Brand.objects.filter(match_key=old_key).first()
        if not old:
            continue
        target = Brand.objects.filter(match_key=match_key(new_name)).exclude(pk=old.pk).first()
        if target:                                            # both exist: models move to the listed one
            for m in BrandModel.objects.filter(brand=old):
                if BrandModel.objects.filter(brand=target, match_key=m.match_key).exists():
                    m.delete()
                else:
                    BrandModel.objects.filter(pk=m.pk).update(brand=target)
            old.active = False
            old.save()
        else:
            Brand.objects.filter(pk=old.pk).update(display_name=new_name, match_key=match_key(new_name))
    for key, name in listed.items():
        b = Brand.objects.filter(match_key=key).first()
        if b:
            Brand.objects.filter(pk=b.pk).update(display_name=name, active=True)
        else:
            Brand.objects.create(display_name=name, match_key=key, active=True)
    Brand.objects.exclude(match_key__in=list(listed)).update(active=False)

    renamed = {k: v for k, v in BRAND_ALIASES.items()}
    names = {key: name for key, name in listed.items()}
    known = list(names.items()) + list(renamed.items())          # for splitting "XXIO MP1400" / "Ping G430"
    for model_name in ("JapanItem", "OrderLine"):
        Model = apps.get_model("tracker", model_name)
        for item in Model.objects.all().only("pk", "brand", "model", "item_name"):
            key = match_key(item.brand)
            if not key:
                found, rest = split_brand(item.item_name, known)
                if found:                                     # "XXIO MP1400" typed without a brand
                    name = " ".join(x for x in (found, rest) if x)[:200]
                    Model.objects.filter(pk=item.pk).update(brand=found, model=rest[:120], item_name=name)
                elif item.item_name and item.model != item.item_name:      # "Other": the whole name is the model
                    Model.objects.filter(pk=item.pk).update(model=item.item_name[:120])
                continue
            brand = names.get(key) or renamed.get(key)
            if brand:
                name = " ".join(x for x in (brand, item.model) if x)[:200]
                Model.objects.filter(pk=item.pk).update(brand=brand, item_name=name)
            else:                                             # not on the list -> Other, same name
                Model.objects.filter(pk=item.pk).update(brand="", model=(item.item_name or item.model)[:120])


def order_line_club(apps, schema_editor):
    OrderLine = apps.get_model("tracker", "OrderLine")
    for line in OrderLine.objects.select_related("club_option"):
        o = line.club_option
        if o:
            OrderLine.objects.filter(pk=line.pk).update(
                type=o.type, club_type="", loft=o.loft, shaft_material=o.shaft_material,
                club_number=o.club_number, length_in=o.length_in, head_only=o.head_only)
        elif line.club_label:
            OrderLine.objects.filter(pk=line.pk).update(type=line.club_label[:50])


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0021_payment_colours_no_proof"),
    ]

    operations = [
        migrations.AddField(model_name="orderline", name="type", field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name="orderline", name="club_type", field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name="orderline", name="loft", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="orderline", name="shaft_material", field=models.CharField(
            blank=True, choices=[("steel", "Steel"), ("graphite", "Graphite")], max_length=10)),
        migrations.AddField(model_name="orderline", name="club_number", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="orderline", name="length_in", field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name="orderline", name="head_only", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="orderline", name="flex", field=models.CharField(
            blank=True, choices=[("L", "L"), ("A", "A"), ("R", "R"), ("SR", "SR"), ("S", "S"), ("X", "X")], max_length=3)),
        migrations.AddField(model_name="orderline", name="note", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="orderline", name="piece_prices", field=models.JSONField(
            blank=True, default=list, validators=[tracker.models.validate_piece_prices], verbose_name="Piece prices (Ks)",
            help_text="Different prices per piece, e.g. [450000, 420000] – one per piece (count = qty)")),
        migrations.AlterField(model_name="orderline", name="item_name", field=models.CharField(
            blank=True, max_length=200, verbose_name="Item (brand + model)")),
        migrations.RunPython(order_line_club, migrations.RunPython.noop),
        migrations.RunPython(brands_and_names, migrations.RunPython.noop),
        migrations.RemoveField(model_name="orderline", name="club_option"),
        migrations.RemoveField(model_name="orderline", name="club_label"),
    ]
