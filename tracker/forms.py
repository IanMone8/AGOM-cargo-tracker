import re

from django import forms
from django.forms import inlineformset_factory
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from . import roles
from .models import (Box, Cargo, OrderLine, Payment, brand_names, choose_option_message, parse_box_code, set_brand_model,
                     ClubCategory, ClubOption, Customer, CustomerRequest, ExtraItem, JapanItem)

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

    def prepare_value(self, value):
        """A saved amount shows as 12,500 (what numbers.js shows after typing)."""
        return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else value

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
                self.fields["status"].help_text = "From Japan: Packed or Shipped. Arrived and Checked come only from the Myanmar check."
        self.user = user
        self._limit_calendars()

    def clean_status(self):
        status = self.cleaned_data["status"]
        if self.user is not None and not roles.can_set_box_status(self.user, status):
            raise forms.ValidationError("Arrived and Checked are set by the Myanmar check, not from Japan.")
        return status

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


MODEL_ATTRS = {
    # the Model box lists the models saved for the chosen brand while typing (views.item_suggest, base.html combo)
    "hx-get": reverse_lazy("item_suggest"), "hx-trigger": "input changed delay:200ms, focus, refresh", "hx-sync": "this:replace",
    "aria-expanded": "false",
    "hx-include": "closest [data-item-field]", "hx-target": "next .item-assist", "hx-swap": "innerHTML",
    "autocomplete": "off", "spellcheck": "false", "data-item-text": "",
}


class BrandModelMixin:
    """Brand (dropdown: the Brand list A → Z, then "Other") + Model (text, suggestions of that brand's saved models).
    Saved as brand / model / item_name = "XXIO MP1400"; with "Other" the model box is free text ("Honma TW757")."""

    brand_required = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .brands import OTHER
        names = brand_names()
        self.fields["brand"] = forms.ChoiceField(
            label="Brand", required=False, choices=[("", "Choose brand…")] + [(n, n) for n in names] + [(OTHER, OTHER)],
            widget=forms.Select(attrs={"class": INPUT, "data-brand-select": ""}))
        self.fields["model"] = forms.CharField(
            label="Model", required=False, max_length=120,
            widget=forms.TextInput(attrs={"class": INPUT, "placeholder": "e.g. MP1400", **MODEL_ATTRS}))
        item = self.instance
        if item.pk and not self.is_bound:
            if item.brand in names:
                self.initial.update(brand=item.brand, model=item.model)
            elif item.item_name or item.brand or item.model:   # no list brand: "Other" with the whole name as model
                self.initial.update(brand=OTHER, model=item.item_name or " ".join(x for x in (item.brand, item.model) if x))

    def clean(self):
        data = super().clean()
        data["model"] = " ".join((data.get("model") or "").split())
        if self.brand_required and not data.get("brand"):
            self.add_error("brand", "Choose the brand (or Other).")
        return data

    def _post_clean(self):
        super()._post_clean()
        if hasattr(self, "cleaned_data") and "brand" in self.cleaned_data:
            set_brand_model(self.instance, self.cleaned_data["brand"], self.cleaned_data.get("model"), save_names=False)

    def save(self, commit=True):
        if "brand" in self.cleaned_data:          # a new model of a list brand joins its suggestions
            set_brand_model(self.instance, self.cleaned_data["brand"], self.cleaned_data.get("model"))
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


class BagForm(CustomerFieldMixin, BrandModelMixin, StyledMixin, forms.ModelForm):
    """The box's golf bag (required): stored as a JapanItem with is_bag=True, so it is
    checked in Myanmar like any other item. Brand dropdown + Model like the item cards."""

    price_jpy = YenField(label="Amount (JP¥)")

    class Meta:
        model = JapanItem
        fields = ["brand", "model", "color", "customer", "price_jpy", "note"]
        labels = {"color": "Color", "customer": "Customer"}
        widgets = {
            "color": forms.TextInput(attrs={"placeholder": "e.g. Black / white"}),
            "customer": forms.TextInput(attrs={"list": "customer-names", "placeholder": "Optional – who it's for",
                                               "autocomplete": "off"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, optional=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.optional = optional  # owner override: the box may be saved without a bag
        self.fields["model"].widget.attrs["placeholder"] = "e.g. Stand Bag"
        self.fields["brand"].required = not optional     # the * on the label; the check itself is in clean()

    @property
    def is_blank(self):
        return not any((self.cleaned_data.get(f) or "").strip() for f in ("brand", "model", "color", "customer", "note")) \
            and self.cleaned_data.get("price_jpy") is None

    @property
    def brand_required(self):
        return not self.optional

    def clean(self):
        data = super().clean()
        for name in ("color", "customer", "note"):
            if data.get(name):
                data[name] = " ".join(data[name].split())
        if "brand" in self.errors and not self.optional:
            self.errors["brand"] = self.error_class(["Every box holds one golf bag – choose the bag's brand (or Other)."])
        if self.optional and not data.get("brand") and not self.is_blank:
            self.add_error("brand", "Choose the bag's brand (or clear the bag fields).")
        return data

    def _post_clean(self):
        super()._post_clean()
        item = self.instance
        item.is_bag, item.type, item.club_type, item.qty = True, JapanItem.BAG_TYPE, "", 1
        item.loft = item.shaft_material = item.club_number = item.length_in = item.flex = ""
        item.head_only = False


class ItemCardMixin(BrandModelMixin):
    """ONE item card for a box item (JapanItemForm, JP¥) and a customer order line (OrderLineForm, Ks):
    Brand + Model, the two-tap club option (a tapped category needs a value), flex, qty, price per piece
    (or "Different prices", one per piece) and note. Rendered by templates/tracker/partials/item_card.html.
    The concrete form sets the price field and its currency; everything else is shared."""

    MAX_PIECES = 50  # "Different prices" shows one price box per piece, up to this many
    price_name, currency, whole_message = "price_jpy", "¥", WHOLE_YEN

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["club_option"] = forms.CharField(required=False, widget=forms.HiddenInput)
        # the category tapped in the picker (its pk): a category needs one of its values (or text, for "Other")
        self.fields["club_category"] = forms.CharField(required=False, widget=forms.HiddenInput)
        self.fields["club_other"] = forms.CharField(required=False, max_length=50, label="Other club / item")
        self.fields["different_prices"] = forms.BooleanField(
            required=False, label="Different prices",
            widget=forms.CheckboxInput(attrs={"data-diff-prices": "", "class": "h-5 w-5 accent-emerald-700"}))
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
        item = self.instance
        if item.pk:                       # editing: the saved club option is picked (also the "changed?" baseline)
            kind = item.club_type or item.type
            fields = (kind, item.loft, item.shaft_material, item.club_number, item.length_in, item.head_only)
            saved = next((o for o in options if (o.type, o.loft, o.shaft_material, o.club_number, o.length_in,
                                                 o.head_only) == fields), None)
            if saved:
                self.initial.update(club_option=str(saved.pk), club_category=str(saved.category_id))
            elif kind and self.has_free_text:
                self.initial.update(club_option="other", club_other=kind)

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

    @property
    def price_field(self):
        return self[self.price_name]

    @property
    def is_order_line(self):
        return self.currency == "Ks"

    # "Different prices": one price box per piece, named <prefix>-piece_price (several values)
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
        """Different prices ticked and qty >= 2: one whole-number price per piece, all filled (or all empty)."""
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
            self.add_error("different_prices", f"Piece {n}: {self.whole_message[0].lower()}{self.whole_message[1:]}.")
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
        self._pieces = self._clean_pieces(data)
        return data

    def _post_clean(self):
        super()._post_clean()
        self.instance.piece_prices = self._pieces   # line total = their sum (model save)
        if self._pieces:
            setattr(self.instance, self.price_name, self._pieces[0])
        if self._club:
            self._club.apply_to(self.instance)
        elif hasattr(self, "cleaned_data") and not (self.cleaned_data.get("club_option") or "").strip():
            self.instance.type = self.instance.club_type = ""          # no club option picked (or cleared)
            self.instance.loft = self.instance.shaft_material = self.instance.club_number = ""
            self.instance.length_in = ""
            self.instance.head_only = False
        elif self._other:
            self.instance.type, self.instance.club_type = self._other, ""
            self.instance.loft = self.instance.shaft_material = self.instance.club_number = ""
            self.instance.length_in = ""
            self.instance.head_only = False


class JapanItemForm(CustomerFieldMixin, ItemCardMixin, StyledMixin, forms.ModelForm):
    """A box item card (Add box modal, Add item, Edit item). Other columns (shaft, specification, condition…) stay in
    the database for old data, CSV import and /admin/, but aren't on the card."""

    price_jpy = YenField(label="Amount (JP¥)")

    class Meta:
        model = JapanItem
        fields = ["brand", "model", "customer", "flex", "qty", "price_jpy", "note"]
        labels = {"customer": "Customer"}
        widgets = {
            "customer": forms.TextInput(attrs={"list": "customer-names", "placeholder": "Optional – who it's for",
                                               "autocomplete": "off"}),
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }


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
    """An order (customer request): its date and note here, its items in OrderLineFormSet."""

    class Meta:
        model = CustomerRequest
        fields = ["order_date", "note"]
        widgets = {
            "order_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, **kwargs):
        from .models import yangon_today
        super().__init__(*args, **kwargs)
        day = self.fields["order_date"]
        day.help_text, day.show_hidden_initial, day.required = "", False, False       # left empty → today / unchanged
        if not self.instance.pk:
            day.initial = yangon_today()
        day.widget.attrs.update(max=yangon_today().isoformat(), required=True)      # no future dates

    def clean_order_date(self):
        from .models import yangon_today
        day = self.cleaned_data["order_date"] or (self.instance.order_date if self.instance.pk else yangon_today())
        if day > yangon_today():
            raise forms.ValidationError("The order date can't be in the future.")
        return day


OrderEditForm = CustomerOrderForm


class OrderLineForm(ItemCardMixin, StyledMixin, forms.ModelForm):
    """One item of a customer order: the same item card as a box item, with the price in Myanmar Kyat
    (no customer field – it's the order's – and no yen, bag or cargo fee)."""

    price_name, currency, whole_message = "price_ks", "Ks", WHOLE_KS
    price_ks = KsField(label="Price (Ks)")

    class Meta:
        model = OrderLine
        fields = ["brand", "model", "flex", "qty", "price_ks", "note"]
        widgets = {
            "qty": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    @property
    def has_customer(self):
        return False


class _OrderLineFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(f.cleaned_data and not f.cleaned_data.get("DELETE") for f in self.forms):
            raise forms.ValidationError("Add at least one item.")


OrderLineFormSet = inlineformset_factory(CustomerRequest, OrderLine, form=OrderLineForm, formset=_OrderLineFormSet,
                                         extra=1, can_delete=True)


class OrderPaymentForm(StyledMixin, forms.ModelForm):
    """"+ Add payment" on an order card: never more than the order still owes."""

    amount_ks = KsField(label="Amount (Ks)", required=True)

    class Meta:
        model = Payment
        fields = ["date", "amount_ks", "method", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "method": forms.RadioSelect,                    # shown as coloured method pills
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import PaymentMethod
        self.order = order                                  # with add_order_money() applied
        self.fields["method"].queryset = PaymentMethod.objects.filter(active=True)
        self.fields["method"].required = True
        self.fields["method"].empty_label = "Choose…"
        self.fields["date"].initial = timezone.localdate()
        self.fields["amount_ks"].widget.attrs["data-pay-amount"] = ""

    def clean_amount_ks(self):
        amount = self.cleaned_data["amount_ks"]
        o = self.order
        if not o.priced and not o.paid_old_order:
            raise forms.ValidationError("Set the price of every item first.")
        if amount <= 0:
            raise forms.ValidationError("Enter the amount.")
        if amount > o.balance_ks:
            raise forms.ValidationError(f"More than this order still owes ({o.balance_ks:,} Ks).")
        return amount
