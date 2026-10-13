from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from . import roles
from .models import Box, Cargo, CustomerRequest, ExtraItem, JapanItem

INPUT = "field"  # styled in static/css/agom.css


class StyledMixin:
    """Apply Tailwind classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "h-5 w-5 accent-emerald-700")
            else:
                w.attrs.setdefault("class", INPUT)


class BoxForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Box
        fields = ["cargo", "box_no", "japan_ship_date", "myanmar_received_date", "status", "notes"]
        widgets = {
            "japan_ship_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "myanmar_received_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Japan sender cannot set the Myanmar side; Myanmar checker cannot
        # change which box it is or the Japan ship date.
        if user is not None and not roles.is_manager(user):
            if not roles.can_check(user):
                self.fields.pop("myanmar_received_date")
            if not roles.can_edit_japan(user):
                for name in ("cargo", "box_no", "japan_ship_date"):
                    self.fields.pop(name)
            if not roles.can_check(user):
                # Arrived / Checked belong to the Myanmar side.
                self.fields["status"].choices = [
                    (Box.Status.PACKED, Box.Status.PACKED.label),
                    (Box.Status.SHIPPED, Box.Status.SHIPPED.label),
                ]
        self._limit_calendars()

    def _limit_calendars(self):
        """Calendars can't pick a date before today, and the received date can't be
        before the ship date. A date already saved on the box stays selectable,
        otherwise the browser would refuse to submit the form."""
        today = timezone.localdate()
        ship = self.instance.japan_ship_date
        received = self.instance.myanmar_received_date
        if "japan_ship_date" in self.fields:
            low = min(today, ship) if ship else today
            self.fields["japan_ship_date"].widget.attrs["min"] = low.isoformat()
        if "myanmar_received_date" in self.fields:
            low = max(today, ship) if ship else today
            if received:
                low = min(low, received)
            attrs = self.fields["myanmar_received_date"].widget.attrs
            attrs["min"] = low.isoformat()
            attrs["data-today"] = today.isoformat()

    def clean(self):
        data = super().clean()
        today = timezone.localdate()
        for name in ("japan_ship_date", "myanmar_received_date"):
            value = data.get(name)
            if name in self.changed_data and value and value < today:
                self.add_error(name, "Choose today or a later date.")
        # Shipping without a date: record today, so the received date has something to follow.
        if ("japan_ship_date" in self.fields and not data.get("japan_ship_date")
                and data.get("status") and data["status"] != Box.Status.PACKED):
            data["japan_ship_date"] = today
            self.instance.japan_ship_date = today
        return data


class BoxCreateForm(StyledMixin, forms.ModelForm):
    """The "Add box" modal: box header fields; items come from ItemFormSet."""

    class Meta:
        model = Box
        fields = ["cargo", "box_no", "notes"]
        labels = {"cargo": "Cargo company", "box_no": "Box ID", "notes": "Box note"}
        widgets = {
            "box_no": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric", "placeholder": "e.g. 3"}),
            "notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cargo"].empty_label = "Choose cargo company…"
        self.fields["box_no"].help_text = "Number only – shown as CODE-number, e.g. GN-3"


class JapanItemForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = JapanItem
        fields = [
            "item_name", "type", "qty", "quantity_unit", "brand", "model", "club_type",
            "club_number", "loft", "flex", "shaft_material", "specification", "color",
            "condition", "price_jpy", "customer", "note",
        ]
        widgets = {
            "item_name": forms.TextInput(attrs={"list": "catalog-models", "placeholder": "e.g. Titleist TSR2 Driver"}),
            "type": forms.TextInput(attrs={"list": "item-categories", "placeholder": "Club, Bag, Balls…"}),
            "brand": forms.TextInput(attrs={"list": "brands"}),
            "club_type": forms.TextInput(attrs={"list": "catalog-types"}),
            "club_number": forms.TextInput(attrs={"placeholder": "1W, 7, 5-P"}),
            "loft": forms.TextInput(attrs={"list": "catalog-lofts", "inputmode": "decimal"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "price_jpy": forms.NumberInput(attrs={"inputmode": "numeric", "placeholder": "Optional"}),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("flex", "shaft_material"):
            choices = list(self.fields[name].choices)
            self.fields[name].choices = [("", "Select…")] + choices[1:]


ItemFormSet = inlineformset_factory(
    Box, JapanItem, form=JapanItemForm, extra=1, can_delete=True
)


class ExtraItemForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ExtraItem
        fields = ["description", "qty"]
        widgets = {"qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"})}


class CustomerRequestForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = CustomerRequest
        fields = ["customer", "type", "item_name", "flex", "loft", "qty", "note", "status"]
        widgets = {
            "type": forms.TextInput(attrs={"list": "catalog-types"}),
            "item_name": forms.TextInput(attrs={"list": "catalog-models"}),
            "loft": forms.TextInput(attrs={"list": "catalog-lofts"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
        }


class BoxFilterForm(StyledMixin, forms.Form):
    cargo = forms.ModelChoiceField(Cargo.objects.all(), required=False, empty_label="All cargo")
    status = forms.ChoiceField(choices=[("", "All statuses")] + Box.Status.choices, required=False)


class CSVImportForm(StyledMixin, forms.Form):
    file = forms.FileField(help_text="CSV with a header row. Download the template below.")
    replace = forms.BooleanField(
        required=False,
        label="Replace existing Japan items in the boxes in this file",
        help_text="Deletes those boxes' current items (and their receive checks) first.",
    )
