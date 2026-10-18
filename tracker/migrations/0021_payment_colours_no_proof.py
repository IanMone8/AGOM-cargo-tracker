"""Payments: no proof photos any more (uploaded files stay on disk), a colour per payment method, and the automatic
"Carried over from earlier payments" note cleared (payments stay on the order they are on)."""
from django.db import migrations, models

CARRIED_NOTE = "Carried over from earlier payments"
COLOURS = {"cash": "green", "kpay": "blue", "kbz special": "navy", "aya pay": "red", "aya special": "maroon",
           "cb special": "teal"}


def method_colours(apps, schema_editor):
    PaymentMethod = apps.get_model("tracker", "PaymentMethod")
    for m in PaymentMethod.objects.all():
        PaymentMethod.objects.filter(pk=m.pk).update(color=COLOURS.get(m.name.strip().lower(), "grey"))


def clear_carried_note(apps, schema_editor):
    Payment = apps.get_model("tracker", "Payment")
    done = Payment.objects.filter(note=CARRIED_NOTE).update(note="")
    if done:
        print(f"\n  Cleared the automatic \"{CARRIED_NOTE}\" note on {done} payment(s).")
    return done


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0020_customerrequest_order_date"),
    ]

    operations = [
        migrations.RemoveField(model_name="payment", name="proof"),
        migrations.RemoveField(model_name="payment", name="proof_thumb"),
        migrations.AddField(
            model_name="paymentmethod",
            name="color",
            field=models.CharField(
                choices=[("green", "Green"), ("blue", "Blue"), ("navy", "Dark blue"), ("red", "Red"),
                         ("maroon", "Dark red"), ("teal", "Teal"), ("grey", "Grey")],
                default="grey", help_text="The pill colour for this method", max_length=10, verbose_name="Colour"),
        ),
        migrations.AlterModelOptions(name="payment", options={"ordering": ["-date", "-created_at"]}),
        migrations.RunPython(method_colours, migrations.RunPython.noop),
        migrations.RunPython(clear_carried_note, migrations.RunPython.noop),
    ]
