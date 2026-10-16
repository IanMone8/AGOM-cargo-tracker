import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q


class Cargo(models.Model):
    """A cargo / forwarding company, e.g. JP, GN, KL, KAG."""

    code = models.CharField(max_length=10, unique=True, help_text="Short code, e.g. GN")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["code"]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} – {self.name}"


MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
JAPAN_TZ = ZoneInfo("Asia/Tokyo")
BOX_CODE_RE = re.compile(r"^(%s)(\d{2})-([A-Z0-9]+)-0*(\d+)$" % "|".join(MONTHS))


def box_period(when=None):
    """("OCT26", <Japan-time datetime>) – the month a box is created in, in Japan time."""
    from django.utils import timezone
    jp = (when or timezone.now()).astimezone(JAPAN_TZ)
    return f"{MONTHS[jp.month - 1]}{jp:%y}", jp


def parse_box_code(text):
    """"oct26-kb-03" -> ("OCT26", "KB", 3, "OCT26-KB-3"), or None if it isn't a box code."""
    m = BOX_CODE_RE.match(" ".join((text or "").upper().split()).replace(" ", ""))
    if not m:
        return None
    period, cargo, seq = m.group(1) + m.group(2), m.group(3), int(m.group(4))
    return period, cargo, seq, f"{period}-{cargo}-{seq}"


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class Box(models.Model):
    class Status(models.TextChoices):
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        ARRIVED = "arrived", "Arrived"
        CHECKED = "checked", "Checked"

    cargo = models.ForeignKey(Cargo, on_delete=models.PROTECT, related_name="boxes")
    # Box ID. New boxes: "OCT26-KB-3" = month in Japan time, cargo code, number for that cargo that month.
    # Older boxes keep their "KB-3" style ID (cargo code + box_no) and have no code.
    code = models.CharField("Box ID", max_length=30, unique=True, null=True, blank=True)
    period = models.CharField(max_length=5, blank=True, help_text='Month of the Box ID, e.g. "OCT26"')
    seq = models.PositiveIntegerField(null=True, blank=True, help_text="Number in the Box ID (per cargo company per month)")
    box_no = models.PositiveIntegerField(null=True, blank=True, help_text="Only for boxes made before monthly Box IDs")
    japan_ship_date = models.DateField(null=True, blank=True)
    myanmar_received_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PACKED)
    cargo_fee_jpy = models.PositiveIntegerField("Cargo fee (JP¥)", null=True, blank=True,
                                                help_text="Whole yen, what the cargo company charges for this box")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cargo__code", "id"]
        verbose_name_plural = "boxes"
        constraints = [
            models.UniqueConstraint(fields=["cargo", "box_no"], name="unique_box_per_cargo"),
            models.UniqueConstraint(fields=["cargo", "period", "seq"], name="unique_monthly_box_number"),
        ]

    def __str__(self):
        return self.label

    def clean(self):
        super().clean()
        ship, received = self.japan_ship_date, self.myanmar_received_date
        if ship and received and received < ship:
            raise ValidationError({
                "myanmar_received_date": (
                    f"Received in Myanmar can't be before the ship date ({ship.day} {ship:%b %Y})."
                )
            })

    def can_be_received_on(self, day):
        """A box can't arrive in Myanmar before it left Japan."""
        return not self.japan_ship_date or day >= self.japan_ship_date

    @property
    def label(self):
        return self.code or f"{self.cargo.code}-{self.box_no}"

    # --- monthly Box IDs -----------------------------------------------------
    @classmethod
    def next_seq(cls, cargo, period):
        """Highest number used by this cargo company this month + 1 (a deleted box never causes a duplicate)."""
        top = cls.objects.filter(cargo=cargo, period=period).aggregate(m=models.Max("seq"))["m"]
        return (top or 0) + 1

    @classmethod
    def next_code(cls, cargo, when=None):
        """The next Box ID for this cargo company now, e.g. ("OCT26-KB-3", "OCT26", 3, "3rd KB box in October 2026")."""
        period, jp = box_period(when)
        seq = cls.next_seq(cargo, period)
        hint = f"{ordinal(seq)} {cargo.code} box in {MONTH_NAMES[jp.month - 1]} {jp.year}"
        return f"{period}-{cargo.code}-{seq}", period, seq, hint

    def set_code(self, code):
        period, _cargo, seq, normal = parse_box_code(code)
        self.code, self.period, self.seq = normal, period, seq

    # --- the golf bag (every box holds exactly one) ---------------------------
    @property
    def bag_name(self):
        """Name of the box's golf bag ("" when none is recorded).
        Lists annotate ``bag_title`` (see views.boxes_with_counts) to avoid a query per box."""
        if hasattr(self, "bag_title"):
            return self.bag_title or ""
        bag = self.items.filter(is_bag=True).first()
        return bag.title if bag else ""

    @property
    def bag_count(self):
        if hasattr(self, "bags_annotated"):
            return self.bags_annotated
        return self.items.filter(is_bag=True).count()

    def money(self, items=None):
        """Yen totals: bag + items inside + cargo fee. Each line is amount × qty, or the sum of its piece prices."""
        items = list(self.items.all()) if items is None else items
        bag = sum(i.line_total or 0 for i in items if i.is_bag)
        inside = sum(i.line_total or 0 for i in items if not i.is_bag)
        fee = self.cargo_fee_jpy or 0
        return {"bag": bag, "items": inside, "cargo_fee": fee, "total": bag + inside + fee,
                "any": any(i.price_jpy is not None for i in items) or self.cargo_fee_jpy is not None}

    @property
    def title(self):
        """ "JP-7 · Titleist Stand Bag" – the box ID with its bag's name."""
        return f"{self.label} · {self.bag_name}" if self.bag_name else self.label

    @property
    def myanmar_started(self):
        """True once the box is received in Myanmar or any item has been checked.

        From then on the Japan sender can no longer change the box or its list.
        """
        return bool(
            self.myanmar_received_date
            or self.status in (self.Status.ARRIVED, self.Status.CHECKED)
            or ReceiveCheck.objects.filter(japan_item__box=self).exists()
        )

    # --- check helpers -------------------------------------------------
    def problem_checks(self):
        return ReceiveCheck.objects.filter(japan_item__box=self).exclude(
            result=ReceiveCheck.Result.MATCHED
        )

    def summary(self):
        """Counts used for the mismatch summary on the box page."""
        items = list(self.items.select_related("receive_check"))
        checks = [i.receive_check for i in items if hasattr(i, "receive_check")]
        bags = sum(i.is_bag for i in items)
        return {
            "money": self.money(items),
            "total": len(items),
            "bags": bags,
            "inside": len(items) - bags,
            "checked": len(checks),
            "unchecked": len(items) - len(checks),
            "matched": sum(c.result == ReceiveCheck.Result.MATCHED for c in checks),
            "missing": sum(c.result == ReceiveCheck.Result.MISSING for c in checks),
            "wrong_qty": sum(c.result == ReceiveCheck.Result.WRONG_QTY for c in checks),
            "extras": self.extra_items.count(),
            "problems": [
                c for c in checks if c.result != ReceiveCheck.Result.MATCHED
            ],
        }

    def refresh_status(self):
        """Move the status forward automatically; users can still override by hand.

        - a Myanmar received date moves Packed/Shipped -> Arrived
        - when every Japan item has a check result the box becomes Checked
        - if a check is cleared on a Checked box it goes back to Arrived
        """
        new = self.status
        if self.myanmar_received_date and new in (self.Status.PACKED, self.Status.SHIPPED):
            new = self.Status.ARRIVED
        elif self.japan_ship_date and new == self.Status.PACKED:
            new = self.Status.SHIPPED
        total = self.items.count()
        checked = ReceiveCheck.objects.filter(japan_item__box=self).count()
        if total and checked == total:
            new = self.Status.CHECKED
        elif new == self.Status.CHECKED and checked < total:
            new = self.Status.ARRIVED
        if new != self.status:
            self.status = new
            self.save(update_fields=["status", "updated_at"])
        return self.status

    @classmethod
    def with_mismatches(cls):
        """Boxes that have a missing / wrong-qty check or any extra item."""
        return cls.objects.filter(
            Q(items__receive_check__result__in=[
                ReceiveCheck.Result.MISSING, ReceiveCheck.Result.WRONG_QTY
            ])
            | Q(extra_items__isnull=False)
        ).distinct()


def club_label(type_, *, head_only=False, loft="", shaft="", club_number="", length_in=""):
    """One short label for a club pick, e.g. "Driver head 9.5°", "Iron · Graphite 5", "Wedge 52°",
    "Wood 3W", "Hybrid 4H", "Putter 34″"."""
    base = f"{type_} head" if (type_ and head_only) else (type_ or "")
    shaft_txt = {"steel": "Steel", "graphite": "Graphite"}.get(shaft, shaft or "")
    if shaft_txt:
        extra = " ".join(x for x in (shaft_txt, club_number) if x)
        return f"{base} · {extra}" if base else extra
    if club_number:
        return f"{base} {club_number}".strip()
    if length_in:
        length_in = length_in.rstrip('″"')
        return f"{base} {length_in}″".strip()
    if loft:
        loft = loft if loft.endswith("°") else f"{loft}°"
        return f"{base} {loft}".strip()
    return base


class JapanItem(models.Model):
    class Flex(models.TextChoices):
        L = "L", "L"
        A = "A", "A"
        R = "R", "R"
        SR = "SR", "SR"
        S = "S", "S"
        X = "X", "X"

    class Condition(models.TextChoices):
        NEW = "new", "New"
        USED = "used", "Used"

    class Unit(models.TextChoices):
        PCS = "pcs", "pcs"
        SET = "set", "set"
        PAIR = "pair", "pair"
        DOZEN = "dozen", "dozen"
        BOX = "box", "box"

    class Shaft(models.TextChoices):
        STEEL = "steel", "Steel"
        GRAPHITE = "graphite", "Graphite"

    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name="items")
    item_name = models.CharField("Name", max_length=200, blank=True)
    type = models.CharField(max_length=50, blank=True, help_text="Driver, Iron, Wedge… (set by the club option)")
    qty = models.PositiveIntegerField(default=1)
    quantity_unit = models.CharField(max_length=5, choices=Unit.choices, default=Unit.PCS)
    brand = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=120, blank=True)
    club_type = models.CharField(
        "Club / item type", max_length=50, blank=True, help_text="e.g. Driver, Iron set, Putter"
    )
    club_number = models.CharField(max_length=20, blank=True, help_text="e.g. 1W, 3W, 7, 5-P")
    loft = models.CharField(max_length=20, blank=True)
    flex = models.CharField(max_length=3, choices=Flex.choices, blank=True)
    shaft_material = models.CharField(max_length=10, choices=Shaft.choices, blank=True)
    head_only = models.BooleanField(default=False, help_text="Club head only (no shaft)")
    length_in = models.CharField("Length (inches)", max_length=10, blank=True, help_text="e.g. putter length 34")
    specification = models.CharField(max_length=255, blank=True, help_text="Shaft model, length, grip…")
    color = models.CharField(max_length=40, blank=True)
    condition = models.CharField(
        "Japan condition", max_length=5, choices=Condition.choices, default=Condition.NEW
    )
    price_jpy = models.PositiveIntegerField("Amount (JP¥)", null=True, blank=True, help_text="Whole yen, per piece")
    piece_prices = models.JSONField(
        "Piece prices (JP¥)", default=list, blank=True,
        help_text="Different prices per piece, e.g. [45000, 41500] – one per piece (count = qty). "
                  "Empty: every piece costs the amount above.")
    line_total_jpy = models.PositiveIntegerField(
        "Line total (JP¥)", null=True, blank=True, editable=False,
        help_text="Amount × qty, or the sum of the piece prices (kept up to date on save)")
    customer = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)
    is_bag = models.BooleanField(
        "Golf bag", default=False,
        help_text="The box's golf bag (every box holds exactly one; the other items are packed inside it)")

    BAG_TYPE = "Golf bag"

    class Meta:
        ordering = ["box", "-is_bag", "id"]  # the bag first, then the items inside it

    def __str__(self):
        return f"{self.box.label}: {self.title}" + (f" {self.flex}" if self.flex else "") + f" ×{self.qty}"

    @staticmethod
    def compute_line_total(price, qty, pieces):
        """Sum of the piece prices when they differ, else amount × qty (the amount is per piece)."""
        if pieces:
            return sum(pieces)
        return None if price is None else price * qty

    @property
    def line_total(self):
        return self.compute_line_total(self.price_jpy, self.qty, self.piece_prices)

    @property
    def has_piece_prices(self):
        return bool(self.piece_prices)

    def _tidy_prices(self):
        """Piece prices only make sense with one per piece; the first also fills the single amount."""
        pieces = [int(p) for p in (self.piece_prices or []) if p is not None]
        if len(pieces) != self.qty or self.qty < 2:
            pieces = []
        self.piece_prices = pieces
        if pieces:
            self.price_jpy = pieces[0]
        self.line_total_jpy = self.line_total

    @property
    def club_label(self):
        return club_label(self.club_type or self.type, head_only=self.head_only, loft=self.loft,
                          shaft=self.shaft_material, club_number=self.club_number, length_in=self.length_in)

    @property
    def title(self):
        """What the item is called on screen: its name, else its club label (a bag: its name, else "Golf bag")."""
        return self.item_name or self.club_label or "Item"

    def save(self, *args, **kwargs):
        self._tidy_prices()
        super().save(*args, **kwargs)
        if self.customer:
            Customer.ensure(self.customer)

    @property
    def spec(self):
        return " · ".join(b for b in [self.club_number, self.flex, self.loft] if b)

    @property
    def details(self):
        """Short one-line description for cards."""
        bits = [self.brand, self.model, self.flex and f"Flex {self.flex}", self.color]  # club label shows as a chip
        return " · ".join(b for b in bits if b)


class ReceiveCheck(models.Model):
    """Result of checking one Japan item in Myanmar (one check per item)."""

    class Result(models.TextChoices):
        MATCHED = "matched", "Matched"
        MISSING = "missing", "Missing"
        WRONG_QTY = "wrong_qty", "Wrong qty"

    japan_item = models.OneToOneField(
        JapanItem, on_delete=models.CASCADE, related_name="receive_check"
    )
    result = models.CharField(max_length=10, choices=Result.choices)
    received_qty = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    checked_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.japan_item} → {self.get_result_display()}"


class ExtraItem(models.Model):
    """Found in the box in Myanmar but not on the Japan list."""

    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name="extra_items")
    description = models.CharField(max_length=255)
    qty = models.PositiveIntegerField(default=1)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["box", "id"]

    def __str__(self):
        return f"{self.box.label}: {self.description} ×{self.qty}"


class CustomerRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ORDERED = "ordered", "Ordered"
        IN_BOX = "in_box", "In box"
        FULFILLED = "fulfilled", "Fulfilled"

    customer = models.CharField(max_length=100)
    type = models.CharField(max_length=50, blank=True)
    item_name = models.CharField(max_length=200)
    flex = models.CharField(max_length=3, choices=JapanItem.Flex.choices, blank=True)
    loft = models.CharField(max_length=20, blank=True)
    qty = models.PositiveIntegerField(default=1)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    box = models.ForeignKey(
        Box, on_delete=models.SET_NULL, null=True, blank=True, related_name="requests"
    )
    japan_item = models.ForeignKey(
        JapanItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="requests"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer}: {self.item_name} ×{self.qty} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        Customer.ensure(self.customer)

    def attach(self, item):
        self.japan_item = item
        self.box = item.box
        if self.status in (self.Status.OPEN, self.Status.ORDERED):
            self.status = self.Status.IN_BOX
        self.save()

    def detach(self):
        self.japan_item = None
        self.box = None
        if self.status == self.Status.IN_BOX:
            self.status = self.Status.ORDERED
        self.save()


class Catalog(models.Model):
    """Known products; drives the dropdown suggestions in forms."""

    type = models.CharField(max_length=50)
    brand_model = models.CharField("Brand / model", max_length=200)
    flex_options = models.CharField(
        max_length=100, blank=True, help_text="Comma separated, e.g. R,SR,S"
    )
    loft_options = models.CharField(
        max_length=200, blank=True, help_text="Comma separated, e.g. 9,10.5,12"
    )

    class Meta:
        ordering = ["type", "brand_model"]
        verbose_name_plural = "catalog"

    def __str__(self):
        return f"{self.type}: {self.brand_model}"

    @staticmethod
    def _split(value):
        return [v.strip() for v in value.split(",") if v.strip()]

    @property
    def flex_list(self):
        return self._split(self.flex_options)

    @property
    def loft_list(self):
        return self._split(self.loft_options)


class Customer(models.Model):
    """A customer in Myanmar. Orders are linked by name: customer requests and
    Japan items whose `customer` field matches this name (case-insensitive)."""

    name = models.CharField(max_length=100, unique=True)
    address = models.TextField(blank=True)
    phone = models.CharField(
        max_length=20, blank=True,
        validators=[RegexValidator(r"^\d{6,15}$", "Phone number: digits only (6–15), e.g. 09771234567.")],
        help_text="Digits only, e.g. 09771234567",
    )
    viber_member = models.BooleanField("Viber channel member", default=False)
    favourite = models.BooleanField(default=False, help_text="Starred customers sort to the top")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        super().save(*args, **kwargs)

    @classmethod
    def ensure(cls, name):
        """Get or create the customer for a name typed on a request or item."""
        name = " ".join((name or "").split())
        if not name:
            return None
        existing = cls.objects.filter(name__iexact=name).first()
        return existing or cls.objects.create(name=name)

    def requests(self):
        return CustomerRequest.objects.filter(customer__iexact=self.name)

    def items(self):
        return JapanItem.objects.filter(customer__iexact=self.name)


class ClubCategory(models.Model):
    """First tap of the Club option picker (Driver, Wood, Putter, …). Add new ones in /admin/."""

    name = models.CharField(max_length=40, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    free_text = models.BooleanField(
        default=False, help_text="Show a text box instead of value buttons (like “Other”)")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name_plural = "club categories"

    def __str__(self):
        return self.name


class ClubOption(models.Model):
    """Second tap of the Club option picker. Editable in /admin/.

    The structured values are what gets saved on the item.
    """

    category = models.ForeignKey(ClubCategory, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=30, help_text='Button text, e.g. "10.5", "3W", "Graphite 5", "52°", "34"')
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    # structured values copied onto the item
    type = models.CharField(max_length=50, help_text="Saved as the item type, e.g. Driver, Wood, Hybrid, Putter")
    loft = models.CharField(max_length=20, blank=True)
    shaft_material = models.CharField(max_length=10, choices=JapanItem.Shaft.choices, blank=True)
    club_number = models.CharField(max_length=20, blank=True, help_text='e.g. "3W", "4H", "5"')
    length_in = models.CharField("Length (inches)", max_length=10, blank=True)
    head_only = models.BooleanField(default=False)

    class Meta:
        ordering = ["category__sort_order", "sort_order", "id"]

    def __str__(self):
        return self.full_label

    @property
    def full_label(self):
        return club_label(self.type, head_only=self.head_only, loft=self.loft, shaft=self.shaft_material,
                          club_number=self.club_number, length_in=self.length_in)

    def apply_to(self, item):
        item.type, item.club_type = self.type, ""
        item.loft, item.shaft_material = self.loft, self.shaft_material
        item.club_number, item.head_only = self.club_number, self.head_only
        item.length_in = self.length_in


class Brand(models.Model):
    """Known brands for the "Item (brand + model)" field. Edit in /admin/."""

    display_name = models.CharField(max_length=60, unique=True, help_text='As it should be shown, e.g. "XXIO", "TaylorMade"')
    match_key = models.CharField(max_length=60, unique=True, editable=False,
                                 help_text="Lowercase, no spaces or punctuation – used to recognise the brand")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        from .brands import match_key, tidy
        self.display_name = tidy(self.display_name)
        self.match_key = match_key(self.display_name)
        super().save(*args, **kwargs)


class BrandModel(models.Model):
    """A model of a brand ("MP1400" of XXIO). Created automatically the first time it's typed."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="models")
    display_name = models.CharField(max_length=120)
    match_key = models.CharField(max_length=120, editable=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["brand__display_name", "display_name"]
        verbose_name = "model"
        constraints = [models.UniqueConstraint(fields=["brand", "match_key"], name="unique_model_per_brand")]

    def __str__(self):
        return f"{self.brand.display_name} {self.display_name}"

    def save(self, *args, **kwargs):
        from .brands import match_key, tidy
        self.display_name = tidy(self.display_name)
        self.match_key = match_key(self.display_name)
        super().save(*args, **kwargs)


def brand_choices():
    return [(b.match_key, b.display_name) for b in Brand.objects.filter(active=True)]


def models_for(brand_display):
    return [(m.match_key, m.display_name)
            for m in BrandModel.objects.filter(brand__display_name=brand_display, active=True)]


def parse_item(text, add_brand=False):
    """Parse "xxio mp 1400" against the saved brands/models (see brands.parse). Nothing is written.

    add_brand: the user said yes to "Is 'Honma' a brand?" – the first word counts as a (new) brand."""
    from . import brands
    known = brand_choices()
    parsed = brands.parse(text, known, models_for)
    if parsed.unknown_brand and add_brand:
        new = brands.style_name(parsed.unknown_brand)          # "ryoma" -> "Ryoma"
        parsed = brands.parse(text, known + [(brands.match_key(new), new)], models_for)
    return parsed


def saved_item_names():
    """Every saved item name, {match_key: display}: the Brand + Model list, then names on items in any box
    (which also covers names with no brand, e.g. "Golf Bag"). One entry per match key."""
    from .brands import match_key
    names = {}
    for m in BrandModel.objects.filter(active=True, brand__active=True).select_related("brand"):
        names.setdefault(match_key(str(m)), str(m))
    for name in JapanItem.objects.exclude(item_name="").order_by().values_list("item_name", flat=True).distinct():
        names.setdefault(match_key(name), name)
    names.pop("", None)
    return names


def remember_item(parsed):
    """Save a new brand / model the first time it's used (called when the item is saved)."""
    from .brands import match_key
    if not parsed or not parsed.brand:
        return
    brand, _ = Brand.objects.get_or_create(match_key=match_key(parsed.brand),
                                           defaults={"display_name": parsed.brand})
    if parsed.model:
        BrandModel.objects.get_or_create(brand=brand, match_key=match_key(parsed.model),
                                         defaults={"display_name": parsed.model})


def apply_item_text(item, text, add_brand=False, save_names=True):
    """Set item.brand / item.model / item.item_name ("XXIO MP1400") from the typed text. Returns the Parsed result."""
    parsed = parse_item(text, add_brand)
    item.brand, item.model = (parsed.brand, parsed.model) if parsed.text else ("", "")
    item.item_name = parsed.display[:200]
    if save_names:
        remember_item(parsed)
    return parsed
