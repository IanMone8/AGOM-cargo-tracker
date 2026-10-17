import re

from django import forms
from django.forms import inlineformset_factory
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from . import roles
from .models import Box, Cargo, choose_option_message, parse_box_code, parse_item, remember_item, ClubCategory, ClubOption, Customer, CustomerRequest, ExtraItem, JapanItem

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


WHOLE_YEN = "Whole yen only, e.g. 12500"
_YEN_TEXT = re.compile(r"[0-9]+")


WHOLE_KS = "Whole Kyat only, e.g. 450000"


def parse_whole_yen(value):
    """ "12500", "12,500", "¥12,500" (or "450,000 Ks") -> 12500; "" -> None.
    Decimals, negatives and other text raise ValueError."""
    text = str(value or "").replace("¥", "").replace("￥", "").replace(",", "").replace(" ", "").strip()
    text = re.sub(r"(?i)(ks|kyats?)$", "", text)
    if not text:
        return None
    if not _YEN_TEXT.fullmatch(text):
        raise ValueError(value)
    return int(text)


class YenField(forms.IntegerField):
    """Whole yen, optional, >= 0. Accepts "12500", "12,500" and "¥12,500"; stored as an integer.
    Decimals ("12500.5"), negatives and other text get "Whole yen only, e.g. 12500". In the browser the box
    only takes digits and shows 12,500 once you leave it (static/js/numbers.js)."""
    message = WHOLE_YEN
    marker = "data-yen"

    def __init__(self, *, label, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("min_value", 0)
        kwargs.setdefault("widget", forms.TextInput(attrs={
            "inputmode": "numeric", "pattern": "[0-9]*", "autocomplete": "off", "placeholder": "0",
            self.marker: "", "data-digits": self.message,
        }))
        super().__init__(label=label, **kwargs)

    def to_python(self, value):
        if isinstance(value, int):
            return value
        try:
            return parse_whole_yen(value)
        except ValueError:
            raise forms.ValidationError(self.message + ".", code="invalid")


class KsField(YenField):
    """Whole Myanmar Kyat (no exchange rate): "450000", "450,000" or "450,000 Ks"."""
    message = WHOLE_KS
    marker = "data-ks"


class BoxForm(StyledMixin, forms.ModelForm):
    cargo_fee_jpy = YenField(label="Cargo fee (JP¥)")

    class Meta:
        model = Box
        fields = ["cargo", "code", "box_no", "cargo_fee_jpy", "japan_ship_date", "myanmar_received_date", "status",
                  "notes"]
        labels = {"box_no": "Box number"}
        widgets = {
            "code": forms.TextInput(attrs={"autocapitalize": "characters", "autocomplete": "off", "spellcheck": "false"}),
            "japan_ship_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "myanmar_received_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Monthly Box ID (OCT26-KB-3) for new boxes; older boxes keep editing their number.
        if self.instance.code:
            self.fields.pop("box_no")
            self.fields["code"].required = True
        else:
            self.fields.pop("code")
            self.fields["box_no"].required = True
        # Japan sender cannot set the Myanmar side; Myanmar checker cannot
        # change which box it is or the Japan ship date.
        if user is not None and not roles.can_see_money(user):
            self.fields.pop("cargo_fee_jpy")      # the Myanmar checker never sees money
        if user is not None and not roles.is_manager(user):
            if not roles.can_check(user):
                self.fields.pop("myanmar_received_date")
            if not roles.can_edit_japan(user):
                for name in ("cargo", "code", "box_no", "japan_ship_date"):
                    self.fields.pop(name, None)
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

    def clean_code(self):
        parsed = parse_box_code(self.cleaned_data["code"])
        if not parsed:
            raise forms.ValidationError("Box ID looks like OCT26-KB-3 – month, year, cargo code, number.")
        return parsed[3]

    def clean(self):
        data = super().clean()
        if data.get("code"):
            period, cargo_code, seq, _ = parse_box_code(data["code"])
            cargo = data.get("cargo") or self.instance.cargo
            if cargo and cargo_code != cargo.code:
                self.add_error("code", f"This box is for {cargo.code}, so its ID must have {cargo.code} in it.")
            else:
                self.instance.period, self.instance.seq = period, seq
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


ITEM_TEXT_ATTRS = {
    # combo box list + "Did you mean…?" / "Is 'Honma' a brand?" while typing (views.item_suggest)
    "hx-get": reverse_lazy("item_suggest"), "hx-trigger": "input changed delay:200ms, focus, refresh", "hx-sync": "this:replace",
    "aria-expanded": "false",
    "hx-include": "closest [data-item-field]", "hx-target": "next .item-assist", "hx-swap": "innerHTML",
    "autocomplete": "off", "autocapitalize": "words", "spellcheck": "false", "data-item-text": "",
}


class ItemTextMixin:
    """The "Item (brand + model)" box: parsed on save into brand + model ("xxio mp 1400" -> "XXIO MP1400").
    New brands (after "Yes, add brand") and new models are only created when the item is saved."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "yes" after "Yes, add brand", "no" after "No" (set by the buttons under the box)
        self.fields["add_brand"] = forms.CharField(required=False, widget=forms.HiddenInput(attrs={"data-add-brand": ""}))

    def clean_item_name(self):
        text = self.cleaned_data.get("item_name") or ""
        add = (self.data.get(self.add_prefix("add_brand")) or "") == "yes"
        self._parsed = parse_item(text, add_brand=add)
        return self._parsed.display[:200]

    def _post_clean(self):
        super()._post_clean()
        parsed = getattr(self, "_parsed", None)
        if parsed is not None:
            self.instance.brand, self.instance.model = (parsed.brand, parsed.model) if parsed.text else ("", "")

    def save(self, commit=True):
        remember_item(getattr(self, "_parsed", None))
        return super().save(commit)


class CustomerFieldMixin:
    """customers=False (the Japan sender) drops the Customer field: it isn't shown, and a posted
    value is ignored, so an item's existing customer link stays as it is."""

    def __init__(self, *args, customers=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not customers:
            self.fields.pop("customer", None)

    @property
    def has_customer(self):
        return "customer" in self.fields


BAG_WORDS = {"bag", "golf bag", "caddie bag", "caddy bag", "stand bag", "cart bag"}
BAG_OVERRIDE_LABEL = "Owner override: save even though this box doesn't have exactly one golf bag"


def is_bag_word(text):
    """True for a typed type like "Golf bag" – that belongs in the box's Golf bag section."""
    return " ".join((text or "").lower().split()) in BAG_WORDS


class BoxCreateForm(StyledMixin, forms.ModelForm):
    """The "Add box" modal: box header fields; the bag comes from BagForm, the items from ItemFormSet."""

    allow_no_bag = forms.BooleanField(
        required=False, label="Save without a golf bag (owner override)",
        help_text="Every box should hold exactly one golf bag. Only use this to fix old or unusual boxes.")

    cargo_fee_jpy = YenField(label="Cargo fee (JP¥)")
    # Filled in automatically when a cargo company is chosen (views.box_next_id); stays editable.
    # Not the model field itself: a number taken in the meantime is bumped on save, not rejected.
    box_id = forms.CharField(label="Box ID", max_length=30, required=False, widget=forms.TextInput(attrs={
        "placeholder": "Choose a cargo company first", "autocapitalize": "characters", "autocomplete": "off",
        "spellcheck": "false"}))

    class Meta:
        model = Box
        fields = ["cargo", "cargo_fee_jpy", "notes"]
        labels = {"cargo": "Cargo company", "notes": "Box note"}
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional"}),
        }

    field_order = ["cargo", "box_id", "cargo_fee_jpy", "notes"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is None or not roles.is_manager(user):
            self.fields.pop("allow_no_bag")  # the Japan sender must always record the bag
        self.fields["cargo"].empty_label = "Choose cargo company…"
        # the cargo select fills in the Box ID (HTMX); explicit target/swap so the form's own aren't inherited
        self.fields["cargo"].widget.attrs.update({
            "hx-get": reverse("box_next_id"), "hx-trigger": "change", "hx-include": "this",
            "hx-target": "#box-id-field", "hx-swap": "outerHTML"})

    def clean(self):
        data = super().clean()
        cargo, raw = data.get("cargo"), (data.get("box_id") or "").strip()
        if not cargo:
            return data
        if not raw:                                    # no JavaScript / left empty: number it now
            raw = Box.next_code(cargo)[0]
        parsed = parse_box_code(raw)
        if not parsed:
            self.add_error("box_id", f"Box ID looks like {Box.next_code(cargo)[0]} – month, year, cargo code, number.")
        elif parsed[1] != cargo.code:
            self.add_error("box_id", f"This box is for {cargo.code}, so its ID must have {cargo.code} in it "
                                     f"(e.g. {Box.next_code(cargo)[0]}).")
        else:
            self.instance.set_code(parsed[3])
            data["box_id"] = parsed[3]
        return data


class BagForm(CustomerFieldMixin, ItemTextMixin, StyledMixin, forms.ModelForm):
    """The box's golf bag (required): stored as a JapanItem with is_bag=True, so it is
    checked in Myanmar, linked to customer orders and attachable like any other item."""

    price_jpy = YenField(label="Amount (JP¥)")

    class Meta:
        model = JapanItem
        fields = ["item_name", "color", "customer", "price_jpy", "note"]
        labels = {"item_name": "Bag (brand + model)", "color": "Color", "customer": "Customer"}
        widgets = {
            "item_name": forms.TextInput(attrs={"placeholder": "e.g. Titleist Stand Bag", **ITEM_TEXT_ATTRS}),
            "color": forms.TextInput(attrs={"placeholder": "e.g. Black / white"}),
            "customer": forms.TextInput(attrs={"list": "customer-names", "placeholder": "Optional – who it's for",
                                               "autocomplete": "off"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, optional=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.optional = optional  # owner override: the box may be saved without a bag
        self.fields["item_name"].required = not optional
        self.fields["item_name"].error_messages["required"] = "Every box holds one golf bag – enter the bag's name."

    @property
    def is_blank(self):
        return not any((self.cleaned_data.get(f) or "").strip() for f in ("item_name", "color", "customer", "note")) \
            and self.cleaned_data.get("price_jpy") is None

    def clean(self):
        data = super().clean()
        for name in ("item_name", "color", "customer", "note"):
            if data.get(name):
                data[name] = " ".join(data[name].split())
        if self.optional and not data.get("item_name") and (any(data.get(f) for f in ("color", "customer", "note"))
                                                             or data.get("price_jpy") is not None):
            self.add_error("item_name", "Enter the bag's name (or clear the bag fields).")
        return data

    def _post_clean(self):
        super()._post_clean()
        item = self.instance
        item.is_bag, item.type, item.club_type, item.qty = True, JapanItem.BAG_TYPE, "", 1
        item.loft = item.shaft_material = item.club_number = item.length_in = item.flex = ""
        item.head_only = False


class JapanItemForm(CustomerFieldMixin, ItemTextMixin, StyledMixin, forms.ModelForm):
    """Japan sender's item card: name, two-tap club option, flex, qty, note.

    Other columns (brand, model, shaft, price, customer…) stay in the database
    for old data, CSV import and /admin/, but aren't on the card.
    """

    MAX_PIECES = 50  # "Different prices" shows one ¥ box per piece, up to this many

    club_option = forms.CharField(required=False, widget=forms.HiddenInput)
    # the category tapped in the picker (its pk): a category needs one of its values (or text, for "Other")
    club_category = forms.CharField(required=False, widget=forms.HiddenInput)
    price_jpy = YenField(label="Amount (JP¥)")
    different_prices = forms.BooleanField(required=False, label="Different prices",
                                          widget=forms.CheckboxInput(attrs={"data-diff-prices": ""}))
    club_other = forms.CharField(required=False, max_length=50, label="Other club / item",
                                 widget=forms.TextInput(attrs={"placeholder": "e.g. Head cover, Chipper"}))

    class Meta:
        model = JapanItem
        fields = ["item_name", "customer", "flex", "qty", "price_jpy", "note"]
        labels = {"item_name": "Item (brand + model)", "customer": "Customer"}
        widgets = {
            "item_name": forms.TextInput(attrs={"placeholder": "Optional, e.g. XXIO MP1400", **ITEM_TEXT_ATTRS}),
            "customer": forms.TextInput(attrs={"list": "customer-names", "placeholder": "Optional – who it's for",
                                               "autocomplete": "off"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["flex"].choices = [("", "—")] + list(self.fields["flex"].choices)[1:]
        self._club, self._other = None, ""
        self._pieces = []
        if not self.is_bound and self.instance.piece_prices:
            self.initial["different_prices"] = True
        # Categories and options come from /admin/, so a new category (e.g. "Chipper") needs no code.
        options = list(ClubOption.objects.filter(active=True, category__active=True)
                       .order_by("sort_order", "id"))
        self.club_groups = []
        for cat in ClubCategory.objects.filter(active=True).order_by("sort_order", "id"):
            opts = [o for o in options if o.category_id == cat.pk]
            if opts or cat.free_text:
                self.club_groups.append({"key": str(cat.pk), "name": cat.name,
                                         "free_text": cat.free_text, "options": opts})

    # what the picker shows as selected (also after a validation error)
    def _picked(self):
        value = (self["club_option"].value() or "").strip()
        if value == "other":
            free = next((g for g in self.club_groups if g["free_text"]), None)
            return (free["key"] if free else ""), self["club_other"].value() or ""
        for g in self.club_groups:
            for o in g["options"]:
                if str(o.pk) == value:
                    return g["key"], o.full_label
        return "", ""

    # "Different prices": one ¥ box per piece, named <prefix>-piece_price (several values)
    @property
    def piece_values(self):
        if self.is_bound:
            return self.data.getlist(self.add_prefix("piece_price")) if hasattr(self.data, "getlist") else []
        return [f"{p:,}" for p in self.instance.piece_prices or []]

    @property
    def qty_number(self):
        try:
            return int(self["qty"].value() or 1)
        except (TypeError, ValueError):
            return 1

    @property
    def piece_name(self):
        return self.add_prefix("piece_price")

    @property
    def shows_pieces(self):
        return bool(self["different_prices"].value())

    def _clean_pieces(self, data):
        """Different prices ticked and qty >= 2: one whole-yen price per piece, all filled (or all empty)."""
        qty = data.get("qty") or 1
        if not data.get("different_prices") or qty < 2:
            return []
        if qty > self.MAX_PIECES:
            self.add_error("different_prices", f"Different prices works up to {self.MAX_PIECES} pieces.")
            return []
        raw = [v.strip() for v in self.piece_values]
        if not any(raw):
            return []
        if len(raw) != qty or not all(raw):
            self.add_error("different_prices", f"Enter a price for every piece ({qty}).")
            return []
        try:
            return [parse_whole_yen(v) for v in raw]
        except ValueError as bad:
            n = raw.index(bad.args[0]) + 1
            self.add_error("different_prices", f"Piece {n}: {WHOLE_YEN[0].lower()}{WHOLE_YEN[1:]}.")
            return []

    @property
    def has_free_text(self):
        return any(g["free_text"] for g in self.club_groups)

    @property
    def selected_free(self):
        return (self["club_option"].value() or "").strip() == "other"

    @property
    def selected_category(self):
        # after "Choose a Driver option", the tapped category stays selected
        return self._picked()[0] or (self["club_category"].value() or "").strip()

    @property
    def selected_label(self):
        return self._picked()[1]

    def _check_category(self, value):
        """Club option as a whole is optional, but a tapped category needs a value: "Choose a Driver option"."""
        group = next((g for g in self.club_groups if g["key"] == (self.cleaned_data.get("club_category") or "").strip()),
                     None)
        if group is None or value == "other":
            return
        if group["free_text"]:
            self.add_error("club_other", f"Type the {group['name']} item.")
        elif not value:
            self.add_error("club_option", choose_option_message(group["name"]))

    def clean(self):
        data = super().clean()
        value = (data.get("club_option") or "").strip()
        self._check_category(value)
        if value == "other":
            self._other = " ".join((data.get("club_other") or "").split())
            if not self._other:
                self.add_error("club_other", "Type the club or item.")
            elif is_bag_word(self._other):
                self.add_error("club_other", "The golf bag goes in the Golf bag section – a box holds one bag.")
        elif value:
            try:
                self._club = ClubOption.objects.get(pk=int(value))
            except (ValueError, ClubOption.DoesNotExist):
                self.add_error(None, "That club option no longer exists – pick another.")
        if data.get("customer"):
            data["customer"] = " ".join(data["customer"].split())
        if not (data.get("item_name") or value):
            self.add_error(None, "Pick a club option or type an item name.")
        self._pieces = self._clean_pieces(data)
        return data

    def _post_clean(self):
        super()._post_clean()
        self.instance.piece_prices = self._pieces   # line total = their sum (JapanItem.save)
        if self._pieces:
            self.instance.price_jpy = self._pieces[0]
        if self._club:
            self._club.apply_to(self.instance)
        elif self._other:
            self.instance.type, self.instance.club_type = self._other, ""
            self.instance.loft = self.instance.shaft_material = self.instance.club_number = ""
            self.instance.length_in = ""
            self.instance.head_only = False


ItemFormSet = inlineformset_factory(
    Box, JapanItem, form=JapanItemForm, extra=1, can_delete=True
)


class ExtraItemForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ExtraItem
        fields = ["description", "qty"]
        widgets = {"qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"})}


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
    brand = forms.ChoiceField(required=False, label="Brand", widget=forms.Select(attrs={"aria-label": "Brand"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Brand
        # brands that are actually on items, so the list stays short
        used = set(JapanItem.objects.exclude(brand="").values_list("brand", flat=True))
        self.fields["brand"].choices = [("", "All brands")] + [
            (b.display_name, b.display_name) for b in Brand.objects.filter(display_name__in=used)]


class CSVImportForm(StyledMixin, forms.Form):
    file = forms.FileField(help_text="CSV with a header row. Download the template below.")
    replace = forms.BooleanField(
        required=False,
        label="Replace existing Japan items in the boxes in this file",
        help_text="Deletes those boxes' current items (and their receive checks) first.",
    )
    allow_bag_exception = forms.BooleanField(
        required=False,
        label="Owner override: import boxes that don't have exactly one golf bag",
        help_text="Normally each box needs exactly one bag row. With this ticked, such boxes are imported "
                  "and listed as warnings so you can fix them.",
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

    price_ks = KsField(label="Price (Ks)")

    class Meta:
        model = CustomerRequest
        fields = ["item_name", "type", "flex", "loft", "qty", "price_ks", "note"]
        labels = {"item_name": "Item"}
        widgets = {
            "item_name": forms.TextInput(attrs={"list": "catalog-models", "placeholder": "e.g. Ping G430 Max Driver"}),
            "type": forms.TextInput(attrs={"list": "catalog-types", "placeholder": "Driver, Iron set…"}),
            "loft": forms.TextInput(attrs={"list": "catalog-lofts"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }


class OrderPriceForm(StyledMixin, forms.Form):
    """Set or change an order's price on its card."""
    price_ks = KsField(label="Price (Ks)")


class PaymentForm(StyledMixin, forms.Form):
    """"+ Add payment" for a customer: the amount is split over their orders (oldest unpaid first unless the
    split is typed in), whatever isn't put on an order is kept as credit. Optionally the customer's existing
    credit is used first. Refunds take money back from orders and/or credit.

    Posted split: alloc_<order pk> per order and alloc_credit; empty everywhere = automatic split.
    """

    kind = forms.ChoiceField(choices=[("payment", "Payment"), ("refund", "Refund")], initial="payment",
                             widget=forms.RadioSelect)
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    amount_ks = KsField(label="Amount (Ks)")
    method = forms.ModelChoiceField(queryset=None, required=False, empty_label="Choose…")
    proof = forms.ImageField(label="Proof photo", required=False,
                             widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))
    note = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"placeholder": "Optional"}))
    use_credit = forms.BooleanField(required=False, initial=True, label="Use the customer's credit first")
    for_order = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, customer, orders, credit, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import PaymentMethod
        self.customer, self.orders, self.credit = customer, list(orders), credit
        self.fields["method"].queryset = PaymentMethod.objects.filter(active=True)
        self.fields["date"].initial = timezone.localdate()
        self.plan = []

    # --- the split ---------------------------------------------------------------------------------------
    def posted_split(self):
        """{order pk or None: Kyat} as typed, or None when nothing was typed (= automatic split)."""
        split, typed = {}, False
        keys = [(o.pk, f"alloc_{o.pk}") for o in self.orders] + [(None, "alloc_credit")]
        for pk, name in keys:
            raw = (self.data.get(name) or "").strip()
            if not raw:
                continue
            typed = True
            try:
                value = parse_whole_yen(raw)
            except ValueError:
                raise forms.ValidationError(f"{WHOLE_KS} – got '{raw}'.")
            if value:
                split[pk] = value
        return split if typed else None

    def clean(self):
        from .models import suggest_allocation
        data = super().clean()
        if self.errors:
            return data
        kind, amount = data["kind"], data.get("amount_ks") or 0
        by_pk = {o.pk: o for o in self.orders}
        only = by_pk.get(data.get("for_order")) if data.get("for_order") else None
        if data.get("for_order") and only is None:
            raise forms.ValidationError("That order no longer belongs to this customer.")
        balances = {o.pk: o.balance_ks for o in self.orders}
        plan = []

        # 1. credit first (payments only): no money changes hands, it just moves onto the orders
        if kind == "payment" and data.get("use_credit") and self.credit > 0:
            targets = [only] if only else self.orders
            room = sum(balances[o.pk] for o in targets)
            use = min(self.credit, room)
            if use:
                split, _ = suggest_allocation(targets, use)
                if only:
                    split = [(only, use)]
                plan.append(("credit", use, [(o.pk, ks) for o, ks in split] + [(None, -use)]))
                for o, ks in split:
                    balances[o.pk] -= ks

        if amount <= 0:
            if not plan:
                self.add_error("amount_ks", "Enter the amount.")
            self.plan = plan
            return data
        if not data.get("method"):
            self.add_error("method", "Choose how it was paid.")

        # 2. the money itself
        typed = self.posted_split()
        if typed is None:                                    # automatic split
            if kind == "payment":
                todo = [_Left(o, balances[o.pk]) for o in self.orders]
                split, left = suggest_allocation(todo, amount, only=_Left(only, 0) if only else None)
                typed = {s.order.pk: ks for s, ks in split}
                if left:
                    typed[None] = left
            else:                                            # refund: from credit first, then the latest paid orders
                typed, left = {}, amount
                from_credit = min(max(self.credit, 0), left)
                if from_credit:
                    typed[None], left = from_credit, left - from_credit
                for o in sorted(self.orders, key=lambda o: (o.created_at, o.pk), reverse=True):
                    if left <= 0:
                        break
                    take = min(max(o.paid_ks, 0), left)
                    if take:
                        typed[o.pk], left = take, left - take
                if left:
                    raise forms.ValidationError(
                        f"Only {amount - left:,} Ks can be refunded (paid orders + credit); type the split if needed.")
        total = sum(typed.values())
        if total != amount:
            raise forms.ValidationError(
                f"The split ({total:,} Ks) must equal the {'refund' if kind == 'refund' else 'payment'} amount "
                f"({amount:,} Ks).")
        if kind == "refund":
            if typed.get(None, 0) > max(self.credit, 0):
                raise forms.ValidationError(f"Only {max(self.credit, 0):,} Ks credit can be refunded.")
            for pk, ks in typed.items():
                if pk is not None and ks > by_pk[pk].paid_ks:
                    raise forms.ValidationError(f"{by_pk[pk].item_name}: only {by_pk[pk].paid_ks:,} Ks was paid.")
        sign = -1 if kind == "refund" else 1
        plan.append((kind, amount, [(pk, sign * ks) for pk, ks in typed.items()]))
        self.plan = plan
        return data

    def save(self, user):
        from django.db import transaction
        from .models import Payment, PaymentAllocation
        data, made = self.cleaned_data, []
        with transaction.atomic():
            for kind, amount, split in self.plan:
                money = kind in ("payment", "refund")
                p = Payment.objects.create(
                    customer=self.customer, kind=kind, date=data["date"], amount_ks=amount,
                    method=data.get("method") if money else None, note=data.get("note", "") if money else "",
                    recorded_by=user, proof=data.get("proof") if money else None)
                PaymentAllocation.objects.bulk_create(
                    PaymentAllocation(payment=p, order_id=pk, amount_ks=ks) for pk, ks in split if ks)
                made.append(p)
        return made


class _Left:
    """An order with what's still owed after credit was used (for suggest_allocation)."""

    def __init__(self, order, balance):
        self.order, self.balance_ks = order, balance
        self.created_at, self.pk = (order.created_at, order.pk) if order else (None, None)
