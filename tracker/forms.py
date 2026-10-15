from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from . import roles
from .models import Box, Cargo, ClubOption, Customer, CustomerRequest, ExtraItem, JapanItem

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
    """Japan sender's item card: name, two-tap club option, flex, qty, note.

    Other columns (brand, model, shaft, price, customer…) stay in the database
    for old data, CSV import and /admin/, but aren't on the card.
    """

    club_option = forms.CharField(required=False, widget=forms.HiddenInput)
    club_other = forms.CharField(required=False, max_length=50, label="Other club / item",
                                 widget=forms.TextInput(attrs={"placeholder": "e.g. Putter, Golf bag"}))

    class Meta:
        model = JapanItem
        fields = ["item_name", "flex", "qty", "note"]
        labels = {"item_name": "Item name"}
        widgets = {
            "item_name": forms.TextInput(attrs={"list": "catalog-models", "placeholder": "Optional, e.g. Titleist TSR2"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["flex"].choices = [("", "—")] + list(self.fields["flex"].choices)[1:]
        self._club, self._other = None, ""
        options = list(ClubOption.objects.filter(active=True))
        order = [c for c, _ in ClubOption.Category.choices]
        self.club_groups = [
            {"key": key, "name": name, "options": sorted((o for o in options if o.category == key),
                                                         key=lambda o: (o.sort_order, o.pk))}
            for key, name in ClubOption.Category.choices
        ]
        self.club_groups.sort(key=lambda g: order.index(g["key"]))

    # what the picker shows as selected (also after a validation error)
    def _picked(self):
        value = (self["club_option"].value() or "").strip()
        if value == "other":
            return "other", self["club_other"].value() or ""
        for g in self.club_groups:
            for o in g["options"]:
                if str(o.pk) == value:
                    return g["key"], o.full_label
        return "", ""

    @property
    def selected_category(self):
        return self._picked()[0]

    @property
    def selected_label(self):
        return self._picked()[1]

    def clean(self):
        data = super().clean()
        value = (data.get("club_option") or "").strip()
        if value == "other":
            self._other = " ".join((data.get("club_other") or "").split())
            if not self._other:
                self.add_error("club_other", "Type the club or item.")
        elif value:
            try:
                self._club = ClubOption.objects.get(pk=int(value))
            except (ValueError, ClubOption.DoesNotExist):
                self.add_error(None, "That club option no longer exists – pick another.")
        if not (data.get("item_name") or value):
            self.add_error(None, "Pick a club option or type an item name.")
        return data

    def _post_clean(self):
        super()._post_clean()
        if self._club:
            self._club.apply_to(self.instance)
        elif self._other:
            self.instance.type, self.instance.club_type = self._other, ""
            self.instance.loft = self.instance.shaft_material = self.instance.club_number = ""
            self.instance.head_only = False


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


class CustomerForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "viber_member", "address", "note"]
        widgets = {
            # digits only: number keypad on phones, anything else is removed while typing
            "phone": forms.TextInput(attrs={
                "inputmode": "numeric", "autocomplete": "tel", "placeholder": "09771234567",
                "pattern": "[0-9]{6,15}", "maxlength": "15", "title": "Digits only, e.g. 09771234567",
                "oninput": "this.value = this.value.replace(/\\D/g, '')",
            }),
            "address": forms.Textarea(attrs={"rows": 2}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_phone(self):
        phone = "".join(self.cleaned_data["phone"].split())  # tolerate pasted spaces
        if phone and not phone.isdigit():
            raise forms.ValidationError("Use digits only, e.g. 09771234567.")
        return phone

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        clash = Customer.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A customer with this name already exists.")
        return name


class CustomerOrderForm(StyledMixin, forms.ModelForm):
    """New order (customer request) started from a customer's page."""

    class Meta:
        model = CustomerRequest
        fields = ["item_name", "type", "flex", "loft", "qty", "note"]
        labels = {"item_name": "Item"}
        widgets = {
            "item_name": forms.TextInput(attrs={"list": "catalog-models", "placeholder": "e.g. Ping G430 Max Driver"}),
            "type": forms.TextInput(attrs={"list": "catalog-types", "placeholder": "Driver, Iron set…"}),
            "loft": forms.TextInput(attrs={"list": "catalog-lofts"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }
