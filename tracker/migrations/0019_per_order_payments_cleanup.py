"""Payments now belong to one order: drop the customer-level fields (customer, kind), the allocations and the
order's single price (prices live on the order lines since 0018)."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0018_order_lines_and_order_payments")]

    operations = [
        migrations.DeleteModel(name="PaymentAllocation"),
        migrations.RemoveField(model_name="payment", name="customer"),
        migrations.RemoveField(model_name="payment", name="kind"),
        migrations.RemoveField(model_name="customerrequest", name="price_ks"),
        migrations.AlterField(
            model_name="payment", name="order",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payments",
                                    to="tracker.customerrequest"),
        ),
        migrations.AlterModelOptions(name="payment", options={"ordering": ["date", "created_at"]}),
    ]
