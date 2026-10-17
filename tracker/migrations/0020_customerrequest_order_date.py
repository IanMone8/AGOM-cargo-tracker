from django.db import migrations, models

import tracker.models


def from_created(apps, schema_editor):
    """Existing orders get the day they were created (Myanmar time) as their order date."""
    from zoneinfo import ZoneInfo

    yangon = ZoneInfo("Asia/Yangon")
    CustomerRequest = apps.get_model("tracker", "CustomerRequest")
    for order in CustomerRequest.objects.only("pk", "created_at"):
        CustomerRequest.objects.filter(pk=order.pk).update(order_date=order.created_at.astimezone(yangon).date())


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0019_per_order_payments_cleanup"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerrequest",
            name="order_date",
            field=models.DateField(default=tracker.models.yangon_today,
                                   help_text="The day the customer ordered (Myanmar time); not in the future",
                                   verbose_name="Order date"),
        ),
        migrations.RunPython(from_created, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="customerrequest",
            options={"ordering": ["-order_date", "-created_at"]},
        ),
    ]
