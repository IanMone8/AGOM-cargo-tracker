from django import forms

from . import roles
from .models import Box, Cargo, CustomerRequest, ExtraItem, JapanItem

INPUT = (
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-base "
    "focus:border-emerald-600 focus:ring-emerald-600"
)


class StyledMixin:
    """Apply Tailwind classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "h-5 w-5")
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


class JapanItemForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = JapanItem
        fields = ["type", "item_name", "flex", "loft", "qty", "condition", "price_jpy", "customer", "note"]
        widgets = {
            "type": forms.TextInput(attrs={"list": "catalog-types"}),
            "item_name": forms.TextInput(attrs={"list": "catalog-models"}),
            "loft": forms.TextInput(attrs={"list": "catalog-lofts"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "price_jpy": forms.NumberInput(attrs={"inputmode": "numeric"}),
        }


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
