from django.conf import settings
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


class Box(models.Model):
    class Status(models.TextChoices):
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        ARRIVED = "arrived", "Arrived"
        CHECKED = "checked", "Checked"

    cargo = models.ForeignKey(Cargo, on_delete=models.PROTECT, related_name="boxes")
    box_no = models.PositiveIntegerField()
    japan_ship_date = models.DateField(null=True, blank=True)
    myanmar_received_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PACKED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cargo__code", "box_no"]
        verbose_name_plural = "boxes"
        constraints = [
            models.UniqueConstraint(fields=["cargo", "box_no"], name="unique_box_per_cargo"),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"{self.cargo.code}-{self.box_no}"

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
        return {
            "total": len(items),
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
    item_name = models.CharField("Name", max_length=200)
    type = models.CharField(max_length=50, help_text="Category, e.g. Club, Bag, Balls, Accessory")
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
    specification = models.CharField(max_length=255, blank=True, help_text="Shaft model, length, grip…")
    color = models.CharField(max_length=40, blank=True)
    condition = models.CharField(
        "Japan condition", max_length=5, choices=Condition.choices, default=Condition.NEW
    )
    price_jpy = models.PositiveIntegerField("Amount (JPY)", null=True, blank=True)
    customer = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["box", "id"]

    def __str__(self):
        bits = [self.item_name, self.flex, self.loft]
        return f"{self.box.label}: " + " ".join(b for b in bits if b) + f" ×{self.qty}"

    @property
    def spec(self):
        return " · ".join(b for b in [self.club_number, self.flex, self.loft] if b)

    @property
    def details(self):
        """Short one-line description for cards."""
        bits = [self.brand, self.model, self.club_type or self.type, self.club_number,
                self.flex, f"{self.loft}°" if self.loft else "",
                self.get_shaft_material_display() if self.shaft_material else "", self.color]
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
