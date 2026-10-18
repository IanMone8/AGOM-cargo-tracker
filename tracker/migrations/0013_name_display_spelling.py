"""Display spelling for item names: models in capitals for codes ("mp1400" -> "MP1400"), other words in
Title Case ("golf bag" -> "Golf Bag"); names with no known brand in Title Case. Same rules as new names
(brands.style_name), applied to the saved Brand + Model list and to the names on existing items."""
from django.db import migrations

from tracker.brands import match_key, style_name, tidy


def restyle(apps, schema_editor):
    BrandModel = apps.get_model("tracker", "BrandModel")
    JapanItem = apps.get_model("tracker", "JapanItem")
    spelled = {}
    for m in BrandModel.objects.select_related("brand"):
        new = style_name(m.display_name)
        if new != m.display_name:
            m.display_name = new
            m.save(update_fields=["display_name"])
        spelled[(m.brand.display_name, m.match_key)] = m.display_name
    for item in JapanItem.objects.exclude(item_name=""):
        if item.brand:
            model = spelled.get((item.brand, match_key(item.model))) or style_name(item.model)
            name = tidy(f"{item.brand} {model}")
        else:
            model, name = item.model, style_name(item.item_name)
        if (name, model) != (item.item_name, item.model):
            item.item_name, item.model = name[:200], model[:120]
            item.save(update_fields=["item_name", "model"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0012_piece_prices")]
    operations = [migrations.RunPython(restyle, migrations.RunPython.noop)]
