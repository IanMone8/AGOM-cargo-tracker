"""Create the cargo companies and a starter catalog; --demo adds sample boxes.

    python manage.py seed_demo          # cargo codes + catalog only
    python manage.py seed_demo --demo   # also sample boxes, items and requests
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from tracker.models import Box, Cargo, Catalog, CustomerRequest, JapanItem

CARGOS = [("JP", "JP Cargo"), ("GN", "GN Cargo"), ("KL", "KL Cargo"), ("KAG", "KAG Cargo")]
CATALOG = [
    ("Driver", "Titleist TSR2", "R,SR,S", "9,10,11"),
    ("Driver", "TaylorMade Qi10", "R,SR,S", "9,10.5,12"),
    ("Driver", "Ping G430 Max", "R,SR,S", "9,10.5,12"),
    ("Fairway wood", "Callaway Paradym", "R,SR,S", "15,18,21"),
    ("Utility", "Ping G430 Hybrid", "R,SR,S", "19,22,26"),
    ("Iron set", "Mizuno JPX 923 5-P", "R,SR,S", ""),
    ("Iron set", "Srixon ZX5 Mk II 5-P", "R,SR,S", ""),
    ("Wedge", "Cleveland RTX 6", "", "50,52,54,56,58,60"),
    ("Putter", "Scotty Cameron Newport 2", "", ""),
    ("Bag", "Titleist Stand Bag", "", ""),
    ("Balls", "Titleist Pro V1 (dozen)", "", ""),
]


class Command(BaseCommand):
    help = "Seed cargo companies and catalog (and optional demo data)."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Also create sample boxes")

    def handle(self, *args, **opts):
        for code, name in CARGOS:
            Cargo.objects.get_or_create(code=code, defaults={"name": name})
        for type_, model, flex, loft in CATALOG:
            Catalog.objects.get_or_create(
                type=type_, brand_model=model,
                defaults={"flex_options": flex, "loft_options": loft},
            )
        self.stdout.write(self.style.SUCCESS("Cargo companies and catalog ready."))
        if not opts["demo"]:
            return

        today = timezone.localdate()
        gn, kl, jp = (Cargo.objects.get(code=c) for c in ("GN", "KL", "JP"))
        boxes = [
            (gn, 3, today - datetime.timedelta(days=12), None),
            (gn, 4, today - datetime.timedelta(days=20), today - datetime.timedelta(days=2)),
            (kl, 1, None, None),
            (jp, 7, today - datetime.timedelta(days=5), None),
        ]
        for cargo, no, shipped, received in boxes:
            box, created = Box.objects.get_or_create(
                cargo=cargo, box_no=no,
                defaults={"japan_ship_date": shipped, "myanmar_received_date": received},
            )
            if created:
                JapanItem.objects.bulk_create([
                    JapanItem(box=box, type="Driver", item_name="Titleist TSR2", flex="S", loft="10", qty=1, condition="used", price_jpy=25000, customer="Ko Aung"),
                    JapanItem(box=box, type="Iron set", item_name="Mizuno JPX 923 5-P", flex="R", qty=1, price_jpy=98000),
                    JapanItem(box=box, type="Wedge", item_name="Cleveland RTX 6", loft="56", qty=2, condition="used"),
                    JapanItem(box=box, type="Balls", item_name="Titleist Pro V1 (dozen)", qty=5),
                ])
            box.refresh_status()
        CustomerRequest.objects.get_or_create(
            customer="Ma Hnin", item_name="Ping G430 Max",
            defaults={"type": "Driver", "flex": "SR", "loft": "10.5"},
        )
        CustomerRequest.objects.get_or_create(
            customer="U Tun", item_name="Cleveland RTX 6",
            defaults={"type": "Wedge", "loft": "56", "status": "ordered"},
        )
        self.stdout.write(self.style.SUCCESS("Demo boxes and requests created."))
