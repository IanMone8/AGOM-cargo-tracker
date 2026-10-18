import datetime
import io

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import roles
from .models import Box, Cargo, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck


def order_data(*lines, note="", existing=0):
    """POST data for the New order / Edit order forms: lines = (item, qty, price, club option pk[, line id, delete]).
    item: ("XXIO", "MP1400") for brand + model, or one text (brand "Other", the text as the model)."""
    data = {"note": note, "lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": str(existing),
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000"}
    for i, line in enumerate(lines):
        item, qty, price, club = (list(line) + ["", "", ""])[:4]
        brand, model = item if isinstance(item, tuple) else ("Other", item)   # ("XXIO", "MP1400") or one text
        data.update({f"lines-{i}-brand": brand, f"lines-{i}-model": model, f"lines-{i}-qty": str(qty),
                     f"lines-{i}-price_ks": str(price or ""), f"lines-{i}-club_option": str(club or "")})
        if len(line) > 4:
            data[f"lines-{i}-id"] = str(line[4])
        if len(line) > 5 and line[5]:
            data[f"lines-{i}-DELETE"] = "on"
    return data


def brand_columns(text):
    """Older test CSVs have one "item" column for brand + model. Every row now needs a brand from the list, so a file
    without a brand column gets brand "Other" with the item text as the model – the saved name stays the same."""
    import csv
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return text
    head = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    if "brand" in head:
        return text
    named = next((i for i, h in enumerate(head) if h in ("item", "item_name", "name")), None)
    if named is not None and "model" not in head:
        rows[0][named] = "model"
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(rows[0] + ["brand"])
    for row in rows[1:]:
        writer.writerow(row + (["Other"] if any(c.strip() for c in row) else []))
    return out.getvalue()


def brand_fields(data):
    """Item card posts written for the old single "Item (brand + model)" box: every card (Add box modal "items-N-",
    golf bag "bag-", the box page's Add item form) gets brand "Other" with the typed text as its model."""
    import re
    if not isinstance(data, dict):
        return data
    data = dict(data)
    prefixes = set()
    for key in list(data):
        m = re.fullmatch(r"((?:items|lines)-\d+-|bag-)?(item_name|qty|club_option|club_other|color)", key)
        if m:
            prefixes.add(m.group(1) or "")
    for pre in prefixes:
        if pre == "" and "cargo" in data:                     # a box form, not an item
            continue
        if f"{pre}item_name" in data:
            data.setdefault(f"{pre}model", data.pop(f"{pre}item_name"))
        if f"{pre}brand" not in data and (pre != "bag-" or data.get(f"{pre}model")):
            data[f"{pre}brand"] = "Other"
    return data


def make_order(customer, *lines, **extra):
    """An order with item lines: lines = (item, qty, price per piece)."""
    from .models import OrderLine
    o = CustomerRequest.objects.create(customer=customer, item_name=lines[0][0], **extra)
    for item, qty, price in lines:
        OrderLine.objects.create(order=o, item_name=item, qty=qty, price_ks=price)
    o.sync_from_lines()
    return o


class BaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=io.StringIO())
        cls.cargo = Cargo.objects.create(code="gn", name="GN Cargo")

        def make(name, group):
            u = User.objects.create_user(name, password="pw-12345-x")
            u.groups.add(Group.objects.get(name=group))
            return u

        cls.owner = make("owner", roles.OWNER)
        cls.japan = make("japan", roles.JAPAN)
        cls.myanmar = make("myanmar", roles.MYANMAR)

    def setUp(self):
        self.box = Box.objects.create(cargo=self.cargo, box_no=3)
        self.item1 = JapanItem.objects.create(box=self.box, type="Driver", item_name="TSR2", flex="S", qty=1)
        self.item2 = JapanItem.objects.create(box=self.box, type="Wedge", item_name="RTX 6", qty=3)

    def add_bag(self, box=None, name="Titleist Stand Bag"):
        return JapanItem.objects.create(box=box or self.box, is_bag=True, type="Golf bag", item_name=name, qty=1)

    def ship(self, box=None):
        """Japan sender marks the box Shipped – only then can Myanmar see it."""
        box = box or self.box
        box.status = Box.Status.SHIPPED
        box.save()

    def htmx(self, user, url, data=None):
        self.client.force_login(user)
        return self.client.post(url, brand_fields(data or {}), HTTP_HX_REQUEST="true")


class ModelTests(BaseTest):
    def test_label_and_code_uppercase(self):
        self.assertEqual(self.box.label, "GN-3")
        self.assertEqual(self.cargo.code, "GN")

    def test_status_flow(self):
        self.box.japan_ship_date = "2026-10-01"
        self.box.save()
        self.assertEqual(self.box.refresh_status(), Box.Status.SHIPPED)
        self.box.myanmar_received_date = "2026-10-10"
        self.box.save()
        self.assertEqual(self.box.refresh_status(), Box.Status.ARRIVED)


class CheckTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.ship()

    def test_one_tap_checks_and_auto_checked(self):
        url1 = reverse("item_check", args=[self.item1.pk])
        r = self.htmx(self.myanmar, url1, {"result": "matched"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="summary" hx-swap-oob="true"')
        self.assertEqual(self.item1.receive_check.received_qty, 1)

        r = self.htmx(self.myanmar, reverse("item_check", args=[self.item2.pk]),
                      {"result": "wrong_qty", "received_qty": "2", "note": "one short"})
        self.assertEqual(r.status_code, 200)
        check = ReceiveCheck.objects.get(japan_item=self.item2)
        self.assertEqual((check.result, check.received_qty), ("wrong_qty", 2))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.CHECKED)
        self.assertIn(self.box, Box.with_mismatches())

        # undo goes back to Arrived
        self.htmx(self.myanmar, reverse("item_check_clear", args=[self.item2.pk]))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ARRIVED)

    def test_wrong_qty_equal_to_expected_is_matched(self):
        self.htmx(self.myanmar, reverse("item_check", args=[self.item2.pk]),
                  {"result": "wrong_qty", "received_qty": "3"})
        self.assertEqual(ReceiveCheck.objects.get(japan_item=self.item2).result, "matched")

    def test_japan_sender_cannot_check(self):
        r = self.htmx(self.japan, reverse("item_check", args=[self.item1.pk]), {"result": "matched"})
        self.assertEqual(r.status_code, 403)

    def test_extra_item(self):
        r = self.htmx(self.myanmar, reverse("extra_add", args=[self.box.pk]),
                      {"description": "Head cover", "qty": "2"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Head cover")
        self.assertEqual(ExtraItem.objects.get().added_by, self.myanmar)
        self.assertIn(self.box, Box.with_mismatches())


class PageTests(BaseTest):
    def test_login_required(self):
        r = self.client.get(reverse("dashboard"))
        self.assertRedirects(r, reverse("login") + "?next=/")

    def test_pages_render(self):
        self.client.force_login(self.owner)
        for name, args in [("dashboard", []), ("box_list", []), ("box_detail", [self.box.pk]),
                           ("box_edit", [self.box.pk]), ("box_create", []),
                           ("csv_import", []), ("csv_template", []), ("cargo_history", [])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_box_filters(self):
        Box.objects.create(cargo=Cargo.objects.create(code="KL", name="KL"), box_no=1, status="shipped")
        self.client.force_login(self.owner)
        r = self.client.get(reverse("box_list"), {"status": "shipped"})
        self.assertContains(r, "KL-1")
        self.assertNotContains(r, "GN-3")

    def test_myanmar_cannot_import(self):
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.get(reverse("csv_import")).status_code, 403)


class RequestTests(BaseTest):
    def test_status_is_set_by_hand(self):
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2 driver", flex="S")
        self.assertEqual(req.status, "ordered")                                       # new orders start Ordered
        for status in ("arrived", "delivered", "cancelled", "ordered"):
            r = self.htmx(self.owner, reverse("request_status", args=[req.pk]), {"status": status})
            req.refresh_from_db()
            self.assertEqual(req.status, status)
            self.assertContains(r, f'<option value="{status}" selected>')
        self.htmx(self.owner, reverse("request_status", args=[req.pk]), {"status": "in_box"})   # old values: ignored
        req.refresh_from_db()
        self.assertEqual(req.status, "ordered")
        self.assertEqual([v for v, _ in CustomerRequest.Status.choices], ["ordered", "arrived", "delivered", "cancelled"])


class CSVImportTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.bag = self.add_bag()

    def upload(self, text, **extra):
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("list.csv", brand_columns(text).encode("utf-8-sig"), content_type="text/csv")
        return self.client.post(reverse("csv_import"), {"file": f, **extra})

    def test_import_creates_boxes_and_items(self):
        r = self.upload(
            "box,type,item_name,flex,loft,qty,condition,price_jpy,customer,note\n"
            "GN-3,,Newport 2,,,1,used,\"12,000\",,\n"
            "oct26-gn-5,Bag,Ping Hoofer,,,,,,,\n"
            "oct26-gn-5,Driver,Qi10,sr,10.5,2,new,,Ma Hnin,\n"
        )
        self.assertContains(r, "Imported 2 item(s) and 1 golf bag(s) into 2 box(es)")
        new_box = Box.objects.get(code="OCT26-GN-5")
        self.assertEqual(new_box.title, "OCT26-GN-5 · Ping Hoofer")
        item = new_box.items.get(is_bag=False)
        self.assertEqual((item.flex, item.qty, item.customer), ("SR", 2, "Ma Hnin"))
        self.assertEqual(self.box.items.get(item_name="Newport 2").price_jpy, 12000)

    def test_import_is_all_or_nothing(self):
        r = self.upload("box,type,item_name,flex,qty\nGN-3,Driver,X,S,1\nZZ-1,Driver,Y,XX,abc\n")
        self.assertContains(r, "Nothing was imported")
        self.assertContains(r, "Unknown cargo code")
        self.assertEqual(JapanItem.objects.count(), 3)   # the two items + GN-3's bag, nothing new

    def test_replace(self):
        self.upload("box,type,item_name\nGN-3,Bag,Stand bag\nGN-3,Balls,Pro V1\n", replace="on")
        self.assertEqual(list(self.box.items.values_list("item_name", "is_bag")), [("Stand Bag", True), ("Pro V1", False)])


class AddBoxModalTests(BaseTest):
    def post_data(self, **items):
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-9", "notes": "fragile",
                "bag-item_name": "Titleist Stand Bag", "bag-color": "Black", "bag-customer": "Ko Aung",
                "bag-note": "zip pocket",
                "items-TOTAL_FORMS": "3", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000"}
        data.update(items)
        return brand_fields(data)

    def test_modal_opens_over_page(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'role="dialog"')
        self.assertContains(r, "Items inside")
        self.assertContains(r, 'name="items-0-club_option"')
        self.assertContains(r, 'name="items-0-brand"')
        self.assertContains(r, 'name="items-0-model"')
        for gone in ("shaft_material", "item_name", "loft", "items-0-type"):
            self.assertNotContains(r, f'name="items-0-{gone}"' if "items-0" not in gone else f'name="{gone}"')
        self.assertNotContains(r, "<html")  # fragment, not a full page

    def test_add_item_card(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_item_card"), {"items-TOTAL_FORMS": "2"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, 'name="items-2-brand"')
        self.assertContains(r, 'id="id_items-TOTAL_FORMS" value="3" hx-swap-oob="true"')
        self.assertContains(r, "Delete")

    def test_create_box_with_items_skips_deleted_card(self):
        from .models import ClubOption
        graphite5 = ClubOption.objects.get(category__name="Iron", label="Graphite")
        # Card 1 was deleted in the browser: it is posted half-filled with DELETE=on.
        data = self.post_data(**{
            "items-1-item_name": "half typed", "items-1-DELETE": "on",
            "items-0-item_name": "Mizuno JPX 923", "items-0-club_option": str(graphite5.pk),
            "items-0-flex": "R", "items-0-qty": "1", "items-0-note": "with covers",
            "items-2-club_option": "other", "items-2-club_other": "Putter", "items-2-qty": "3",
        })
        r = self.htmx(self.japan, reverse("box_create"), data)
        box = Box.objects.get(code="OCT26-GN-9")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["HX-Redirect"], reverse("box_detail", args=[box.pk]))
        self.assertEqual(box.notes, "fragile")
        bag, iron, putter = box.items.all()   # the bag comes first
        self.assertEqual((bag.is_bag, bag.item_name, bag.color, bag.customer, bag.note, bag.type, bag.qty),
                         (True, "Titleist Stand Bag", "Black", "", "zip pocket", "Golf bag", 1))  # customer: not the Japan sender's
        self.assertEqual(box.title, "OCT26-GN-9 · Titleist Stand Bag")
        self.assertEqual((iron.type, iron.shaft_material, iron.club_number, iron.flex, iron.note),
                         ("Iron", "graphite", "", "R", "with covers"))
        self.assertEqual(iron.club_label, "Iron · Graphite")
        self.assertEqual((putter.type, putter.item_name, putter.qty, putter.title), ("Putter", "", 3, "Putter"))

    def test_invalid_box_rerenders_modal(self):
        data = self.post_data(cargo="", **{"items-0-item_name": "Bag", "items-0-qty": "1"})
        r = self.htmx(self.japan, reverse("box_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'role="dialog"')
        self.assertContains(r, "This field is required")
        self.assertFalse(Box.objects.filter(code="OCT26-GN-9").exists())

    def test_taken_box_id_moves_to_next_free_number(self):
        Box.objects.create(cargo=self.cargo, code="OCT26-GN-9", period="OCT26", seq=9)
        self.client.force_login(self.japan)
        r = self.client.post(reverse("box_create"), self.post_data(**{"bag-item_name": "Stand bag", "items-TOTAL_FORMS": "1",
                             "items-0-item_name": "TSR2", "items-0-qty": "1"}), follow=True)
        self.assertTrue(Box.objects.filter(code="OCT26-GN-10").exists())
        self.assertContains(r, "OCT26-GN-9 was taken in the meantime, so this box is OCT26-GN-10.")

    def test_myanmar_cannot_add_box(self):
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.get(reverse("box_create")).status_code, 403)


class MyanmarCheckFlowTests(BaseTest):
    def test_start_check(self):
        self.ship()
        self.client.force_login(self.myanmar)
        detail = reverse("box_detail", args=[self.box.pk])
        r = self.client.get(detail)
        self.assertContains(r, "Start Myanmar check")
        self.assertNotContains(r, "✓ Matched")

        r = self.client.post(reverse("box_start_check", args=[self.box.pk]))
        self.assertRedirects(r, detail + "#bag-section", fetch_redirect_response=False)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ARRIVED)
        self.assertIsNotNone(self.box.myanmar_received_date)

        r = self.client.get(detail)
        self.assertNotContains(r, "Start Myanmar check")
        self.assertContains(r, "✓ Matched")

    def test_japan_sender_cannot_start_check(self):
        self.client.force_login(self.japan)
        r = self.client.post(reverse("box_start_check", args=[self.box.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(self.client.get(reverse("box_detail", args=[self.box.pk])), "Start Myanmar check")


class CargoHistoryTests(BaseTest):
    def test_history_and_filter(self):
        import datetime
        self.box.japan_ship_date = datetime.date(2026, 9, 1)
        self.box.myanmar_received_date = datetime.date(2026, 9, 15)
        self.box.save()
        other = Cargo.objects.create(code="KL", name="KL Cargo")
        Box.objects.create(cargo=other, box_no=1)
        self.client.force_login(self.owner)
        r = self.client.get(reverse("cargo_history"))
        self.assertContains(r, "14 days")
        r = self.client.get(reverse("cargo_history"), {"cargo": "kl"},
                            HTTP_HX_REQUEST="true", HTTP_HX_TARGET="history-results")
        self.assertContains(r, "KL-1")
        self.assertNotContains(r, "GN-3")


class CSVNewFieldsTests(CSVImportTests):
    def test_new_columns_and_aliases(self):
        r = self.upload(
            "Box,Name,Type,Qty,Unit,Brand,Model,Club type,Club number,Loft,Flex,Shaft material,"
            "Specification,Color,Japan condition,Amount\n"
            "GN-3,Qi10 Driver,Club,1,pcs,TaylorMade,Qi10,Driver,1W,10.5,x,Graphite,Diamana 60,Navy,used,30000\n"
        )
        self.assertContains(r, "Imported 1 item(s)")
        item = self.box.items.get(item_name="TaylorMade QI10")    # brand / model columns win; model codes in capitals
        self.assertEqual(
            (item.brand, item.model, item.club_type, item.club_number, item.flex, item.shaft_material,
             item.specification, item.color, item.condition, item.price_jpy, item.quantity_unit),
            ("TaylorMade", "QI10", "Driver", "1W", "X", "graphite", "Diamana 60", "Navy", "used", 30000, "pcs"),
        )

    def test_bad_unit_and_shaft(self):
        r = self.upload("box,type,item_name,quantity_unit,shaft_material\nGN-3,Club,X,bag,wood\n")
        self.assertContains(r, "quantity_unit must be one of")
        self.assertContains(r, "shaft_material must be steel or graphite")

    def test_template_round_trips(self):
        Cargo.objects.create(code="KL", name="KL Cargo")
        self.client.force_login(self.owner)
        template = self.client.get(reverse("csv_template")).content.decode()
        self.assertTrue(template.startswith("box,cargo,kind,brand,model,color,club_option,flex,qty,amount_jpy,"
                                            "cargo_fee_jpy,customer,note"))
        r = self.upload(template, replace="on")
        self.assertContains(r, "Imported 4 item(s) and 2 golf bag(s) into 2 box(es)")
        self.assertEqual(Box.objects.get(cargo__code="KL", code__isnull=False).bag_name, "PING Hoofer")
        head = JapanItem.objects.get(item_name="", box__cargo__code="GN", box__code__isnull=False)
        self.assertEqual((head.type, head.loft, head.head_only, head.qty, head.title), ("Driver", "9.5", True, 2, "Driver head 9.5°"))
        self.assertEqual(JapanItem.objects.get(item_name="Cleveland Golf RTX 6").club_label, "Wedge 52°")
        self.assertEqual(JapanItem.objects.get(item_name="Titleist TSR2").brand, "Titleist")


class JapanSenderLockTests(BaseTest):
    def start_check(self):
        self.ship()
        self.client.force_login(self.myanmar)
        self.client.post(reverse("box_start_check", args=[self.box.pk]))
        self.client.logout()

    def test_japan_status_choices_only_packed_and_shipped(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_edit", args=[self.box.pk]))
        choices = [v for v, _ in r.context["form"].fields["status"].choices]
        self.assertEqual(choices, ["packed", "shipped"])
        self.assertNotContains(r, 'value="arrived"')
        self.assertNotContains(r, 'value="checked"')
        # posting a Myanmar status is rejected
        r = self.client.post(reverse("box_edit", args=[self.box.pk]),
                             {"cargo": self.cargo.pk, "box_no": 3, "status": "checked"})
        self.assertContains(r, "Select a valid choice")
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.PACKED)

    def test_myanmar_and_owner_keep_all_statuses(self):
        self.ship()
        for user in (self.myanmar, self.owner):
            self.client.force_login(user)
            r = self.client.get(reverse("box_edit", args=[self.box.pk]))
            self.assertEqual(len(r.context["form"].fields["status"].choices), 4)

    def test_japan_locked_after_myanmar_check_starts(self):
        self.client.force_login(self.japan)
        detail = reverse("box_detail", args=[self.box.pk])
        add_url = reverse("item_add", args=[self.box.pk])
        r = self.client.get(detail)
        self.assertContains(r, "Edit box")
        self.assertContains(r, add_url)
        self.assertContains(r, ">delete<")
        self.start_check()

        self.client.force_login(self.japan)
        r = self.client.get(detail)
        self.assertNotContains(r, "Edit box")
        self.assertNotContains(r, add_url)
        self.assertNotContains(r, ">delete<")
        self.assertContains(r, "Locked.")
        self.assertEqual(self.client.get(reverse("box_edit", args=[self.box.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("item_add", args=[self.box.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("item_delete", args=[self.item1.pk])).status_code, 403)
        self.assertTrue(JapanItem.objects.filter(pk=self.item1.pk).exists())

    def test_one_checked_item_locks_box(self):
        ReceiveCheck.objects.create(japan_item=self.item1, result="matched", received_qty=1)
        self.assertTrue(self.box.myanmar_started)
        self.client.force_login(self.japan)
        self.assertEqual(self.client.get(reverse("box_edit", args=[self.box.pk])).status_code, 403)

    def test_owner_and_myanmar_can_still_edit_locked_box(self):
        self.start_check()
        for user in (self.owner, self.myanmar):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("box_edit", args=[self.box.pk])).status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("item_add", args=[self.box.pk])).status_code, 200)


class JapanSenderHiddenPagesTests(BaseTest):
    def test_japan_cannot_use_csv_or_requests(self):
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2")
        self.client.force_login(self.japan)
        for name, args in [("csv_import", []), ("csv_template", []), ("order_edit", [req.pk])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        for name in ("request_status", "order_edit"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name, args=[req.pk]), {"status": "delivered"}).status_code, 403)
        f = SimpleUploadedFile("l.csv", brand_columns("box,type,item_name\nGN-3,Bag,Stand bag\n").encode(), content_type="text/csv")
        self.assertEqual(self.client.post(reverse("csv_import"), {"file": f}).status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, "ordered")
        self.assertFalse(JapanItem.objects.filter(item_name="Stand bag").exists())

    def test_japan_sidebar_and_dashboard_hide_links(self):
        CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2")
        self.client.force_login(self.japan)
        r = self.client.get(reverse("dashboard"))
        self.assertNotContains(r, reverse("csv_import"))
        self.assertNotContains(r, "Customer requests")
        self.assertNotContains(r, "Ko Aung")
        self.assertContains(r, "Add box")  # still can add boxes

    def test_other_roles_keep_access(self):
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, reverse("customer_list"))
        self.assertNotContains(r, reverse("csv_import"))
        self.client.force_login(self.owner)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, reverse("customer_list"))
        self.assertContains(r, reverse("csv_import"))


class DashboardStatCardTests(BaseTest):
    def test_stat_cards(self):
        import re
        checked = Box.objects.create(cargo=self.cargo, box_no=4, status=Box.Status.CHECKED)
        ExtraItem.objects.create(box=checked, description="Head cover")  # a difference
        Cargo.objects.create(code="KL", name="KL Cargo")
        self.client.force_login(self.japan)
        r = self.client.get(reverse("dashboard"))
        html = r.content.decode()
        self.assertEqual((r.context["cargo_count"], r.context["total_box_count"],
                          r.context["checked_box_count"], r.context["mismatch_count"]), (2, 2, 1, 1))

        self.assertNotIn("{#", html)  # no template comment leaking into the page
        self.assertNotIn("Dashboard stat card", html)
        cards = re.findall(r'<a href="[^"]*" data-stat="(\w+)".*?</a>', html, re.S)
        self.assertEqual(cards, ["sage", "mint", "cream", "sand"])
        expected = [("Cargos", "2", "Sent from Japan", "plane"),
                    ("Total boxes", "2", "Across all cargos", "boxes"),
                    ("Checked in Myanmar", "1", "Finished box checks", "box-checked"),
                    ("Needs review", "1", "Boxes with differences", "warning")]
        blocks = re.findall(r'data-stat="\w+".*?</a>', html, re.S)
        for block, (title, value, caption, icon) in zip(blocks, expected):
            with self.subTest(title=title):
                self.assertRegex(block, rf'class="stat-title[^"]*">{title}</span>')
                self.assertRegex(block, rf'class="stat-value[^"]*">{value}(<|\s*</span>)')
                self.assertRegex(block, rf'class="stat-caption[^"]*">{caption}</span>')
                # icon: fixed size, contained, hidden if the file is missing
                self.assertIn(f"/static/img/display/icons/{icon}.webp", block)
                self.assertIn('width="70" height="70"', block)
                self.assertIn("object-contain", block)
                self.assertIn("onerror=\"this.style.display='none'\"", block)
        # "1 / 2" with a smaller, muted total only on the Checked card
        self.assertRegex(blocks[2], r'class="stat-total[^"]*text-ink-muted[^"]*">/ 2</span>')
        self.assertNotIn("stat-total", blocks[0] + blocks[1] + blocks[3])


class SidebarIconTests(BaseTest):
    def test_sidebar_uses_png_icons_with_fallback(self):
        import re
        self.client.force_login(self.japan)
        html = self.client.get(reverse("dashboard")).content.decode()
        for label, png in [("Dashboard", "house"), ("Shipments", "flying-box"), ("Cargo history", "calendar")]:
            with self.subTest(label=label):
                link = re.search(rf'<a [^>]*class="sidebar-link[^"]*">(?:(?!</a>).)*{label}</a>', html, re.S).group(0)
                self.assertIn(f"/static/img/display/icons/{png}.webp", link)
                self.assertIn('width="24" height="24"', link)
                self.assertIn("onerror=", link)
                self.assertIn("<svg", link)  # line-icon fallback if the PNG is missing


class FaviconTests(BaseTest):
    def test_website_icon_is_3d_logo(self):
        for url in (reverse("login"), reverse("dashboard")):
            if url == reverse("dashboard"):
                self.client.force_login(self.owner)
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertContains(r, '<link rel="icon" type="image/png" href="/static/img/display/3d-logo.png')
                self.assertContains(r, '<link rel="apple-touch-icon" sizes="180x180" href="/static/img/display/app-icon-180.png')
                self.assertContains(r, '<link rel="manifest" href="/manifest.webmanifest">')

    def test_manifest_uses_3d_cover_logo(self):
        r = self.client.get(reverse("web_manifest"))  # no login needed: phones fetch it directly
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/manifest+json")
        data = r.json()
        self.assertEqual(data["short_name"], "AGOM Cargo")
        self.assertEqual([(i["src"], i["sizes"]) for i in data["icons"]],
                         [("/static/img/display/app-icon-192.png", "192x192"),
                          ("/static/img/display/app-icon-512.png", "512x512")])


class MyanmarSeesShippedOnlyTests(BaseTest):
    """A packed box stays hidden from the Myanmar checker until it is shipped."""

    def test_packed_box_hidden_everywhere(self):
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2 driver", flex="S")
        self.client.force_login(self.myanmar)
        label = self.box.label
        self.assertNotContains(self.client.get(reverse("box_list")), label)
        self.assertNotContains(self.client.get(reverse("box_list"), {"status": "packed"}), label)
        self.assertNotContains(self.client.get(reverse("box_list")), '<option value="packed"')
        self.assertNotContains(self.client.get(reverse("cargo_history")), label)
        dash = self.client.get(reverse("dashboard"))
        self.assertNotContains(dash, label)
        self.assertEqual(dash.context["total_box_count"], 0)
        # direct links don't work either
        for name, pk in [("box_detail", self.box.pk), ("box_edit", self.box.pk), ("item_row", self.item1.pk),
                         ("item_wrong_qty", self.item1.pk), ("extra_add", self.box.pk)]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 404)
        for name, pk, data in [("box_start_check", self.box.pk, {}),
                               ("item_check", self.item1.pk, {"result": "matched"})]:
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name, args=[pk]), data).status_code, 404)
        self.assertFalse(ReceiveCheck.objects.exists())
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.PACKED)

    def test_visible_after_japan_sender_ships(self):
        self.client.force_login(self.japan)
        self.client.post(reverse("box_edit", args=[self.box.pk]),
                         {"cargo": self.cargo.pk, "box_no": 3, "status": "shipped"})
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.SHIPPED)
        self.client.force_login(self.myanmar)
        self.assertContains(self.client.get(reverse("box_list")), self.box.label)
        self.assertContains(self.client.get(reverse("dashboard")), self.box.label)
        self.assertContains(self.client.get(reverse("box_detail", args=[self.box.pk])), "Start Myanmar check")

    def test_owner_and_japan_still_see_packed_boxes(self):
        for user in (self.owner, self.japan):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertContains(self.client.get(reverse("box_list")), self.box.label)
                self.assertEqual(self.client.get(reverse("box_detail", args=[self.box.pk])).status_code, 200)


class MarkShippedTests(BaseTest):
    def test_one_tap_ship_makes_box_visible_to_myanmar(self):
        self.client.force_login(self.japan)
        detail = reverse("box_detail", args=[self.box.pk])
        self.assertContains(self.client.get(detail), "Mark as shipped")
        r = self.client.post(reverse("box_mark_shipped", args=[self.box.pk]))
        self.assertRedirects(r, detail)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.SHIPPED)
        self.assertIsNotNone(self.box.japan_ship_date)
        self.assertNotContains(self.client.get(detail), "Mark as shipped")

        self.client.force_login(self.myanmar)
        self.assertContains(self.client.get(reverse("dashboard")), self.box.label)
        self.assertContains(self.client.get(reverse("box_list")), self.box.label)

    def test_keeps_existing_ship_date(self):
        import datetime
        self.box.japan_ship_date = datetime.date(2026, 10, 1)
        self.box.save()
        self.client.force_login(self.japan)
        self.client.post(reverse("box_mark_shipped", args=[self.box.pk]))
        self.box.refresh_from_db()
        self.assertEqual(self.box.japan_ship_date, datetime.date(2026, 10, 1))

    def test_only_japan_side_can_ship(self):
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.post(reverse("box_mark_shipped", args=[self.box.pk])).status_code, 403)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.PACKED)


class DateRuleTests(BaseTest):
    """Received in Myanmar can't be before Shipped from Japan; calendars start today."""

    def setUp(self):
        super().setUp()
        import datetime
        from django.utils import timezone
        self.today = timezone.localdate()
        self.day = datetime.timedelta(days=1)

    def edit(self, user, **data):
        self.client.force_login(user)
        post = {"cargo": self.cargo.pk, "box_no": 3, "status": self.box.status, "notes": ""}
        post.update({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in data.items()})
        return self.client.post(reverse("box_edit", args=[self.box.pk]), post)

    def test_received_before_shipped_rejected(self):
        self.box.japan_ship_date = self.today + self.day
        self.box.status = Box.Status.SHIPPED
        self.box.save()
        r = self.edit(self.owner, japan_ship_date=self.today + self.day, myanmar_received_date=self.today)
        self.assertContains(r, "can&#x27;t be before the ship date")
        self.box.refresh_from_db()
        self.assertIsNone(self.box.myanmar_received_date)
        # same day or later is fine
        r = self.edit(self.owner, japan_ship_date=self.today + self.day, myanmar_received_date=self.today + self.day)
        self.assertEqual(r.status_code, 302)

    def test_myanmar_form_also_checks_ship_date(self):
        self.box.japan_ship_date = self.today + self.day * 2
        self.box.status = Box.Status.SHIPPED
        self.box.save()
        r = self.edit(self.myanmar, myanmar_received_date=self.today + self.day)
        self.assertContains(r, "can&#x27;t be before the ship date")

    def test_past_dates_rejected_but_existing_past_dates_kept(self):
        r = self.edit(self.japan, japan_ship_date=self.today - self.day)
        self.assertContains(r, "Choose today or a later date.")
        # a box that already has a past date can still be saved unchanged
        self.box.japan_ship_date = self.today - self.day * 30
        self.box.save()
        r = self.edit(self.japan, japan_ship_date=self.today - self.day * 30, notes="changed")
        self.assertEqual(r.status_code, 302)
        self.box.refresh_from_db()
        self.assertEqual(self.box.notes, "changed")

    def test_calendar_min_attributes(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse("box_edit", args=[self.box.pk]))
        form = r.context["form"]
        self.assertEqual(form.fields["japan_ship_date"].widget.attrs["min"], self.today.isoformat())
        self.assertEqual(form.fields["myanmar_received_date"].widget.attrs["min"], self.today.isoformat())
        self.box.japan_ship_date = self.today + self.day * 3
        self.box.save()
        form = self.client.get(reverse("box_edit", args=[self.box.pk])).context["form"]
        self.assertEqual(form.fields["myanmar_received_date"].widget.attrs["min"],
                         (self.today + self.day * 3).isoformat())
        self.assertIn('min="', r.content.decode())

    def test_shipping_without_date_records_today(self):
        r = self.edit(self.japan, status="shipped")
        self.assertEqual(r.status_code, 302)
        self.box.refresh_from_db()
        self.assertEqual((self.box.status, self.box.japan_ship_date), ("shipped", self.today))

    def test_start_check_blocked_before_ship_date(self):
        self.box.japan_ship_date = self.today + self.day
        self.box.status = Box.Status.SHIPPED
        self.box.save()
        self.client.force_login(self.myanmar)
        detail = reverse("box_detail", args=[self.box.pk])
        r = self.client.get(detail)
        self.assertContains(r, "Not shipped yet.")
        self.assertNotContains(r, "Start Myanmar check")
        r = self.client.post(reverse("box_start_check", args=[self.box.pk]), follow=True)
        self.assertContains(r, "can&#x27;t be received in Myanmar before that")
        r = self.client.post(reverse("item_check", args=[self.item1.pk]), {"result": "matched"})
        self.assertEqual(r.status_code, 409)
        self.box.refresh_from_db()
        self.assertIsNone(self.box.myanmar_received_date)
        self.assertFalse(ReceiveCheck.objects.exists())

    def test_model_rule_covers_admin(self):
        from django.core.exceptions import ValidationError
        self.box.japan_ship_date = self.today
        self.box.myanmar_received_date = self.today - self.day
        with self.assertRaises(ValidationError) as ctx:
            self.box.full_clean()
        self.assertIn("myanmar_received_date", ctx.exception.message_dict)


class CustomerPageTests(BaseTest):
    def setUp(self):
        super().setUp()
        from .models import Customer
        self.Customer = Customer
        self.ship()
        self.ko = Customer.objects.create(name="Ko Aung", address="No. 12, Bahan, Yangon",
                                          phone="0977123456", viber_member=True)

    def test_japan_sender_has_no_customer_pages(self):
        self.client.force_login(self.japan)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("customer_list"))
        for url in (reverse("customer_list"), reverse("customer_detail", args=[self.ko.pk]),
                    reverse("customer_edit", args=[self.ko.pk]), reverse("customer_by_name") + "?name=Ko"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("customer_list"), {"name": "X"}).status_code, 403)

    def test_search_and_viber_filter(self):
        self.Customer.objects.create(name="Ma Hnin", phone="09111222", viber_member=False)
        self.client.force_login(self.owner)
        r = self.client.get(reverse("customer_list"), {"q": "bahan"}, HTTP_HX_REQUEST="true",
                            HTTP_HX_TARGET="customer-results")
        self.assertContains(r, "Ko Aung")
        self.assertNotContains(r, "Ma Hnin")
        r = self.client.get(reverse("customer_list"), {"viber": "yes"})
        self.assertContains(r, "Ko Aung")
        self.assertNotContains(r, "Ma Hnin")

    def test_detail_shows_orders_and_items(self):
        CustomerRequest.objects.create(customer="Ko Aung", item_name="Qi10 Driver")
        self.item1.customer = "KO AUNG"
        self.item1.save()
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("customer_detail", args=[self.ko.pk]))
        for text in ("Ko Aung", "No. 12, Bahan, Yangon", "Viber channel member", "Qi10 Driver", "TSR2", "GN-3"):
            self.assertContains(r, text)

    def test_detail_hides_items_in_unshipped_boxes_from_myanmar(self):
        self.box.status = Box.Status.PACKED
        self.box.save()
        self.item1.customer = "Ko Aung"
        self.item1.save()
        self.client.force_login(self.myanmar)
        self.assertNotContains(self.client.get(reverse("customer_detail", args=[self.ko.pk])), "TSR2")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("customer_detail", args=[self.ko.pk])), "TSR2")

    def test_create_edit_and_rename_keeps_orders(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse("customer_list"), {"name": "  Ma   Hnin ", "phone": "09222333", "viber_member": "on"})
        ma = self.Customer.objects.get(name="Ma Hnin")
        self.assertRedirects(r, reverse("customer_detail", args=[ma.pk]))
        self.assertTrue(ma.viber_member)
        r = self.client.post(reverse("customer_list"), {"name": "ma hnin"})
        self.assertContains(r, "already exists")
        CustomerRequest.objects.create(customer="Ma Hnin", item_name="G430")
        self.client.post(reverse("customer_edit", args=[ma.pk]), {"name": "Daw Hnin", "phone": "09222333"})
        ma.refresh_from_db()
        self.assertEqual(ma.name, "Daw Hnin")
        self.assertEqual(CustomerRequest.objects.get(item_name="G430").customer, "Daw Hnin")

    def test_names_on_requests_items_and_csv_create_customers(self):
        CustomerRequest.objects.create(customer="U Tun", item_name="Putter")
        self.item2.customer = "Daw Mya"
        self.item2.save()
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", brand_columns("box,type,item_name,customer\nGN-3,Bag,Stand bag,Ko Zaw\n").encode(), content_type="text/csv")
        self.client.post(reverse("csv_import"), {"file": f})
        names = set(self.Customer.objects.values_list("name", flat=True))
        self.assertTrue({"U Tun", "Daw Mya", "Ko Zaw"} <= names)
        CustomerRequest.objects.create(customer="ko aung", item_name="x")  # same person, different case
        self.assertEqual(self.Customer.objects.filter(name__iexact="ko aung").count(), 1)

    def test_customer_name_links(self):
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="Qi10")
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("customer_detail", args=[self.ko.pk]))   # orders are on the customer page
        self.assertContains(r, reverse("customer_by_name") + "?name=Ko%20Aung")
        r = self.client.get(reverse("customer_by_name"), {"name": "Ko Aung"})
        self.assertRedirects(r, reverse("customer_detail", args=[self.ko.pk]))


class DisplayImageTests(TestCase):
    """The app must show the small copies, and every file it points to must exist."""

    def test_display_copies_exist_and_are_small(self):
        from pathlib import Path
        from django.conf import settings
        root = Path(settings.BASE_DIR) / "static" / "img" / "display"
        names = [f"icons/{n}.webp" for n in ("plane", "boxes", "box-checked", "warning", "house", "flying-box", "calendar")]
        names += ["3d-logo.png", "app-icon-180.png", "app-icon-192.png", "app-icon-512.png"]
        for name in names:
            with self.subTest(name=name):
                f = root / name
                self.assertTrue(f.exists())
                self.assertLess(f.stat().st_size, 200 * 1024)


class LogoTests(BaseTest):
    def test_sidebar_header_and_login_use_3d_logo(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, 'src="/static/img/display/3d-logo.png')
        self.assertNotContains(r, "agom-logo")
        self.client.force_login(self.owner)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertEqual(html.count('src="/static/img/display/3d-logo.png'), 2)  # sidebar + phone header
        self.assertNotIn("agom-logo", html)


class CustomerTableTests(BaseTest):
    """CRM-style customer table: columns, status tags, sort, filters, favourites, load more."""

    def setUp(self):
        super().setUp()
        from .models import Customer
        self.Customer = Customer
        self.ship()
        self.ko = Customer.objects.create(name="Ko Aung", address="No. 12, Inya Road, Bahan Township, Yangon",
                                          phone="0977123456", viber_member=True)
        self.ma = Customer.objects.create(name="Ma Hnin", phone="09222333")
        self.tun = Customer.objects.create(name="U Tun")
        self.zaw = Customer.objects.create(name="Ko Zaw")
        CustomerRequest.objects.create(customer="Ko Aung", item_name="Qi10")                      # open -> Waiting
        CustomerRequest.objects.create(customer="Ma Hnin", item_name="Bag", status="arrived")     # arrived -> Arrived
        CustomerRequest.objects.create(customer="U Tun", item_name="Putter", status="delivered")  # all done
        self.client.force_login(self.myanmar)

    def rows(self, **params):
        import re
        html = self.client.get(reverse("customer_list"), params).content.decode()
        return re.findall(r'class="crm-link" title="([^"]+)"', html), html

    def test_columns_and_status_tags(self):
        names, html = self.rows()
        self.assertIn("4 customers", html)
        self.assertIn('class="crm-avatar"', html)
        self.assertIn(">KA</span>", html)                                   # initials
        self.assertIn('href="tel:0977123456"', html)                        # tap to call
        self.assertIn('title="No. 12, Inya Road, Bahan Township, Yangon"', html)  # full address on hover
        self.assertIn("Viber member", html)
        self.assertIn("Not member", html)
        self.assertIn('crm-tag-waiting"><i></i>Waiting', html)
        self.assertIn('crm-tag-arrived"><i></i>Arrived', html)
        self.assertIn('crm-tag-delivered"><i></i>All delivered', html)
        self.assertIn('crm-tag-none"><i></i>No orders', html)
        self.assertIn('>Today</div>', html)                                 # last update = latest order date
        for label in (">Open<", ">Edit<", ">Call 0977123456<"):
            self.assertIn(label, html)

    def test_orders_count_and_sorting(self):
        CustomerRequest.objects.create(customer="Ko Aung", item_name="Bag")
        names, html = self.rows()
        self.assertEqual(names, ["Ko Aung", "Ko Zaw", "Ma Hnin", "U Tun"])   # default: name A–Z
        self.assertIn('aria-sort="ascending"', html)
        names, _ = self.rows(sort="name", dir="desc")
        self.assertEqual(names, ["U Tun", "Ma Hnin", "Ko Zaw", "Ko Aung"])
        names, html = self.rows(sort="orders", dir="desc")
        self.assertEqual(names[0], "Ko Aung")                               # 2 requests
        self.assertEqual(names[-1], "Ko Zaw")                               # 0 orders
        self.assertIn("▼", html)
        names, _ = self.rows(sort="updated", dir="desc")
        self.assertEqual(names[-1], "Ko Zaw")                               # never updated goes last

    def test_filters(self):
        self.assertEqual(self.rows(viber="yes")[0], ["Ko Aung"])
        self.assertNotIn("Ko Aung", self.rows(viber="no")[0])
        self.assertEqual(self.rows(orders="open")[0], ["Ko Aung", "Ma Hnin"])        # not delivered yet
        self.assertEqual(self.rows(orders="none")[0], ["Ko Zaw", "U Tun"])
        self.assertEqual(self.rows(q="inya")[0], ["Ko Aung"])

    def test_favourite_toggle_sorts_to_top(self):
        r = self.client.post(reverse("customer_favourite", args=[self.tun.pk]), {"sort": "name", "dir": "asc"},
                             HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.tun.refresh_from_db()
        self.assertTrue(self.tun.favourite)
        self.assertIn('aria-pressed="true"', r.content.decode())
        self.assertEqual(self.rows()[0][0], "U Tun")
        self.assertEqual(self.rows(sort="name", dir="desc")[0][0], "U Tun")
        self.client.post(reverse("customer_favourite", args=[self.tun.pk]))
        self.tun.refresh_from_db()
        self.assertFalse(self.tun.favourite)
        self.client.force_login(self.japan)
        self.assertEqual(self.client.post(reverse("customer_favourite", args=[self.tun.pk])).status_code, 403)

    def test_load_more_after_25(self):
        for n in range(30):
            self.Customer.objects.create(name=f"Test Customer {n:02d}")
        names, html = self.rows()
        self.assertEqual(len(names), 25)
        self.assertIn("Load more", html)
        self.assertIn("Showing <span id=\"crm-shown\">25</span> of 34", html)
        r = self.client.get(reverse("customer_list"), {"page": 2}, HTTP_HX_REQUEST="true")
        page2 = r.content.decode()
        self.assertEqual(page2.count('class="crm-link"'), 9)
        self.assertNotIn("Load more", page2)
        self.assertIn('<span id="crm-shown" hx-swap-oob="true">34</span>', page2)


class CustomerPhoneAndOrderTests(BaseTest):
    def setUp(self):
        super().setUp()
        from .models import Customer
        self.Customer = Customer
        self.ko = Customer.objects.create(name="Ko Aung", phone="0977123456")
        self.client.force_login(self.myanmar)

    def test_phone_digits_only(self):
        r = self.client.post(reverse("customer_list"), {"name": "Ma Su", "phone": "09-77 abc"})
        self.assertContains(r, "Use digits only")
        r = self.client.post(reverse("customer_list"), {"name": "Ma Su", "phone": "12345"})
        self.assertContains(r, "digits only (6–15)")
        self.assertFalse(self.Customer.objects.filter(name="Ma Su").exists())
        self.client.post(reverse("customer_list"), {"name": "Ma Su", "phone": " 09 4455 1122 "})  # pasted spaces
        self.assertEqual(self.Customer.objects.get(name="Ma Su").phone, "0944551122")
        r = self.client.post(reverse("customer_edit", args=[self.ko.pk]), {"name": "Ko Aung", "phone": "+95 9 7712"})
        self.assertContains(r, "Use digits only")
        html = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn('inputmode="numeric"', html)
        self.assertIn('pattern="[0-9]{6,15}"', html)

    def test_model_rejects_non_digits_for_admin_too(self):
        from django.core.exceptions import ValidationError
        self.ko.phone = "09 7712"
        with self.assertRaises(ValidationError):
            self.ko.full_clean()

    def test_new_order_from_customer_page(self):
        detail = reverse("customer_detail", args=[self.ko.pk])
        r = self.client.get(detail)
        self.assertContains(r, "New order")
        self.assertContains(r, reverse("customer_new_order", args=[self.ko.pk]))
        from .models import ClubOption
        driver = ClubOption.objects.get(category__name="Driver", label="10.5")
        r = self.client.post(reverse("customer_new_order", args=[self.ko.pk]),
                             order_data(("ping g430 max", 2, "", driver.pk)))
        self.assertRedirects(r, detail + "#orders")
        order = CustomerRequest.objects.get(lines__item_name="Ping G430 Max")
        line = order.lines.get()
        self.assertEqual((order.customer, order.status, order.qty, line.club_label), ("Ko Aung", "ordered", 2, "Driver 10.5°"))
        r = self.client.get(detail)
        self.assertContains(r, "Ping G430 Max")
        row = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn("crm-tag-waiting", row)

    def test_new_order_validation_keeps_form_open(self):
        r = self.client.post(reverse("customer_new_order", args=[self.ko.pk]), order_data((("", ""), 1, "")))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Add at least one item.")
        self.assertNotContains(r, 'id="new-order" method="post" action="/customers/%d/orders/new/" class="hidden' % self.ko.pk)
        self.assertFalse(CustomerRequest.objects.exists())

    def test_menu_has_new_order_and_japan_blocked(self):
        html = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn(reverse("customer_detail", args=[self.ko.pk]) + "?new_order=1#orders", html)
        self.client.force_login(self.japan)
        r = self.client.post(reverse("customer_new_order", args=[self.ko.pk]), {"item_name": "X", "qty": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(CustomerRequest.objects.exists())


class DashboardHeroTests(BaseTest):
    def test_hero_for_every_role(self):
        for user in (self.owner,):   # the Myanmar checker and the Japan sender have their own banners
            self.client.force_login(user)
            html = self.client.get(reverse("dashboard")).content.decode()
            with self.subTest(user=user.username):
                for text in ("Golf stock route", "Take with Trust.", "Play with Confidence.", "Get Achievements.",
                             "Track every golf bag, club, and accessory by cargo and box."):
                    self.assertIn(text, html)
                self.assertIn('src="/static/img/display/agom-badge.webp', html)
                self.assertIn("onerror=\"this.style.display='none'\"", html)
                self.assertIn('class="hero-hill"', html)
                # banner comes before the four stat cards
                self.assertLess(html.index('class="hero"'), html.index("data-stat="))


class ClubOptionTests(BaseTest):
    """Simplified item card: two-tap club option saved as structured data."""

    def setUp(self):
        super().setUp()
        from .models import ClubOption
        self.opt = {o.full_label: o for o in ClubOption.objects.all()}
        self.client.force_login(self.japan)

    def add(self, **fields):
        return self.client.post(reverse("item_add", args=[self.box.pk]), brand_fields({"qty": "1", **fields}),
                                HTTP_HX_REQUEST="true")

    def test_seeded_options_and_labels(self):
        self.assertEqual(len(self.opt), 36)
        for label in ("Driver 9°", "Driver 10.5°", "Driver head 8°", "Driver head 9.5°", "Iron · Graphite",
                      "Iron · Steel", "Wedge 48°", "Wedge 60°", "Wood 2W", "Wood 3W", "Wood 9W", "Hybrid 2H",
                      "Hybrid 4H", "Hybrid 7H", "Putter 32″", "Putter 34″", "Putter 38″"):
            self.assertIn(label, self.opt)

    def test_card_has_only_simple_fields_and_picker(self):
        html = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        for name in ("brand", "model", "club_option", "club_other", "flex", "qty", "note"):
            self.assertIn(f'name="{name}"', html)
        self.assertNotIn('name="customer"', html)                 # no customers for the Japan sender
        self.client.force_login(self.owner)
        owner = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertLess(owner.index('name="model"'), owner.index('name="customer"'))   # right below Brand + Model
        self.assertLess(owner.index('name="customer"'), owner.index('name="club_option"'))
        self.client.force_login(self.japan)
        for gone in ("type", "loft", "item_name", "shaft_material"):
            self.assertNotIn(f'name="{gone}"', html)
        cats = ["Driver", "Driver head", "Wood", "Hybrid", "Iron", "Wedge", "Putter", "Other"]
        positions = [html.index(f">{cat}</button>") for cat in cats]
        self.assertEqual(positions, sorted(positions))          # shown in this order
        self.assertEqual(html.count('data-club-other'), 1)     # one free-text box
        self.assertIn(">3W</button>", html)
        self.assertIn('data-club-label="Putter 34″"', html)
        self.assertIn('data-club-label="Wedge 52°"', html)
        self.assertIn(">52°</button>", html)

    def test_structured_save(self):
        cases = [("Iron · Graphite", dict(type="Iron", shaft_material="graphite", club_number="", head_only=False)),
                 ("Wedge 52°", dict(type="Wedge", loft="52", head_only=False)),
                 ("Driver head 9.5°", dict(type="Driver", loft="9.5", head_only=True)),
                 ("Driver 10.5°", dict(type="Driver", loft="10.5", head_only=False)),
                 ("Wood 3W", dict(type="Wood", club_number="3W", loft="", length_in="")),
                 ("Hybrid 4H", dict(type="Hybrid", club_number="4H", shaft_material="")),
                 ("Putter 34″", dict(type="Putter", length_in="34", club_number="", loft=""))]
        for label, expected in cases:
            with self.subTest(label=label):
                self.add(club_option=str(self.opt[label].pk))
                item = JapanItem.objects.latest("id")
                for field, value in expected.items():
                    self.assertEqual(getattr(item, field), value)
                self.assertEqual(item.club_label, label)
                self.assertEqual(item.title, label)       # no name typed: label is the title

    def test_other_free_text_and_name_only(self):
        self.add(club_option="other", club_other="  Head   cover ")
        item = JapanItem.objects.latest("id")
        self.assertEqual((item.type, item.club_label, item.loft, item.length_in), ("Head cover", "Head cover", "", ""))
        before = JapanItem.objects.count()
        r = self.add(club_option="other", club_other="Golf bag")   # the bag has its own section
        self.assertContains(r, "goes in the Golf bag section")
        self.assertEqual(JapanItem.objects.count(), before)
        self.add(item_name="Titleist Pro V1")
        self.assertEqual(JapanItem.objects.latest("id").title, "Titleist Pro V1")

    def test_validation(self):
        before = JapanItem.objects.count()
        r = self.client.post(reverse("item_add", args=[self.box.pk]), {"qty": "1"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "Choose the brand (or Other).")
        r = self.add(club_option="other", club_other="")
        self.assertContains(r, "Type the club or item.")
        r = self.add(club_option="999999")
        self.assertContains(r, "no longer exists")
        self.assertEqual(JapanItem.objects.count(), before)

    def test_label_shown_on_box_page(self):
        self.add(item_name="Ping G430", club_option=str(self.opt["Driver head 9.5°"].pk), flex="SR")
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn("Ping G430", html)
        self.assertIn("Driver head 9.5°", html)
        self.add(club_option=str(self.opt["Wedge 52°"].pk))   # no name: the label is the title, shown once
        item = JapanItem.objects.latest("id")
        row = self.client.get(reverse("item_row", args=[item.pk])).content.decode()
        self.assertEqual(row.count("Wedge 52°"), 1)

    def test_admin_can_add_option_without_code(self):
        from .models import ClubOption
        ClubOption.objects.create(category=self.opt["Wedge 52°"].category, label="62°", type="Wedge", loft="62",
                                  sort_order=99)
        ClubOption.objects.filter(category__name="Driver", label="9").update(active=False)
        html = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('data-club-label="Wedge 62°"', html)
        self.assertNotIn('data-club-label="Driver 9°"', html)
        self.client.force_login(self.owner)
        self.owner.is_staff = True
        self.owner.save()
        self.assertEqual(self.client.get("/admin/tracker/cluboption/").status_code, 200)
        self.assertEqual(self.client.get("/admin/tracker/clubcategory/").status_code, 200)

    def test_admin_can_add_category_without_code(self):
        from .models import ClubCategory, ClubOption
        chipper = ClubCategory.objects.create(name="Chipper", sort_order=55)        # between Wedge and Putter
        ClubCategory.objects.create(name="Empty", sort_order=56)
        opt = ClubOption.objects.create(category=chipper, label="35", type="Chipper", length_in="35")
        ClubCategory.objects.filter(name="Driver head").update(active=False)
        html = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertLess(html.index(">Wedge</button>"), html.index(">Chipper</button>"))
        self.assertLess(html.index(">Chipper</button>"), html.index(">Putter</button>"))
        self.assertIn('data-club-label="Chipper 35″"', html)
        self.assertNotIn(">Empty</button>", html)                # no options yet: no button
        self.assertNotIn(">Driver head</button>", html)          # hidden category
        self.add(club_option=str(opt.pk))
        item = JapanItem.objects.latest("id")
        self.assertEqual((item.type, item.length_in, item.club_label), ("Chipper", "35", "Chipper 35″"))
        self.client.force_login(self.owner)
        self.owner.is_staff = True
        self.owner.save()
        r = self.client.post("/admin/tracker/clubcategory/add/", {
            "name": "Fairway finder", "sort_order": "95", "active": "on",
            "options-TOTAL_FORMS": "1", "options-INITIAL_FORMS": "0",
            "options-0-label": "5", "options-0-sort_order": "0", "options-0-active": "on", "options-0-type": "Wood",
            "options-0-club_number": "5W"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context.get("errors"))
        self.assertTrue(ClubOption.objects.filter(category__name="Fairway finder", club_number="5W").exists())

    def test_copy_saved_item(self):
        self.item1.loft, self.item1.head_only, self.item1.customer = "9.5", True, "Ko Aung"
        self.item1.save()
        CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2", japan_item=self.item1)   # old link
        r = self.client.post(reverse("item_copy", args=[self.item1.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        copy = JapanItem.objects.latest("id")
        self.assertNotEqual(copy.pk, self.item1.pk)
        self.assertEqual((copy.box, copy.item_name, copy.loft, copy.head_only, copy.customer),
                         (self.box, "TSR2", "9.5", True, "Ko Aung"))
        self.assertFalse(copy.requests.exists())               # linked requests stay on the original
        self.assertIn(f'id="item-{copy.pk}"', r.content.decode())
        self.assertIn('id="summary" hx-swap-oob="true"', r.content.decode())

    def test_copy_blocked_for_myanmar_and_locked_box(self):
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.post(reverse("item_copy", args=[self.item1.pk])).status_code, 403)
        self.ship()
        self.box.myanmar_received_date = self.box.created_at.date()
        self.box.status = Box.Status.ARRIVED
        self.box.save()
        self.client.force_login(self.japan)
        self.assertEqual(self.client.post(reverse("item_copy", args=[self.item1.pk])).status_code, 403)

    def test_csv_club_option_and_old_columns(self):
        bag = self.add_bag()
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", brand_columns(
            "box,item_name,club_option,flex,qty\n"
            "GN-3,,iron graphite 5,R,1\n"
            "GN-3,Vokey,Wedge 56°,,2\n"
            "GN-3,Head,driver head 10,,1\n").encode(), content_type="text/csv")
        r = self.client.post(reverse("csv_import"), {"file": f})
        self.assertContains(r, "Imported 3 item(s)")
        labels = sorted(i.club_label for i in JapanItem.objects.filter(box=self.box).exclude(pk__in=[self.item1.pk, self.item2.pk, bag.pk]))
        self.assertEqual(labels, ["Driver head 10°", "Iron · Graphite", "Wedge 56°"])   # old spelling still works
        f = SimpleUploadedFile("l.csv", brand_columns("box,item_name,club_option\nGN-3,X,Wedge 99\n").encode(), content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Unknown club_option")

    def test_csv_wood_hybrid_putter(self):
        bag = self.add_bag()
        self.client.force_login(self.owner)
        rows = ["Wood 3W", "wood 3", "Hybrid 4", "4H", "Putter 34", "Putter 34″", "hybrid 4h", "3w"]
        f = SimpleUploadedFile("l.csv", brand_columns("box,club_option\n" + "".join(f"GN-3,{r}\n" for r in rows)).encode(),
                               content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 8 item(s)")
        items = JapanItem.objects.filter(box=self.box).exclude(pk__in=[self.item1.pk, self.item2.pk, bag.pk]).order_by("id")
        self.assertEqual([i.club_label for i in items],
                         ["Wood 3W", "Wood 3W", "Hybrid 4H", "Hybrid 4H", "Putter 34″", "Putter 34″", "Hybrid 4H",
                          "Wood 3W"])
        self.assertEqual((items[0].type, items[0].club_number), ("Wood", "3W"))
        self.assertEqual((items[4].type, items[4].length_in), ("Putter", "34"))
        f = SimpleUploadedFile("l.csv", brand_columns("box,club_option\nGN-3,Wood 8\n").encode(), content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Unknown club_option")
        f = SimpleUploadedFile("l.csv", brand_columns("box,type,item_name,loft,shaft_material\nGN-3,Club,Old style,10,steel\n").encode(),
                               content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 1 item(s)")
        self.assertEqual(JapanItem.objects.get(item_name="Old Style").shaft_material, "steel")   # no brand: Title Case


class ItemCardCustomerTests(BaseTest):
    def test_customer_field_autocomplete_and_save(self):
        from .models import Customer
        Customer.objects.create(name="Ko Aung")
        self.client.force_login(self.owner)
        # the Add box modal and the box page both carry the customer suggestions
        modal = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('list="customer-names"', modal)
        self.assertIn('<datalist id="customer-names"><option value="Ko Aung">', modal)
        page = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn('<datalist id="customer-names"><option value="Ko Aung">', page)
        # optional: an item without a customer saves fine
        self.client.post(reverse("item_add", args=[self.box.pk]), brand_fields({"item_name": "Bag", "qty": "1"}),
                         HTTP_HX_REQUEST="true")
        self.assertEqual(JapanItem.objects.latest("id").customer, "")
        # a new name is saved on the item and creates the customer
        self.client.post(reverse("item_add", args=[self.box.pk]),
                         brand_fields({"item_name": "TSR2", "customer": "  Ma   Hnin ", "qty": "1"}), HTTP_HX_REQUEST="true")
        self.assertEqual(JapanItem.objects.latest("id").customer, "Ma Hnin")
        self.assertTrue(Customer.objects.filter(name="Ma Hnin").exists())


class GolfBagTests(BaseTest):
    """Every box holds exactly one golf bag; all other items are packed inside it."""

    def modal_data(self, **extra):
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-12", "notes": "",
                "bag-item_name": "", "bag-color": "", "bag-customer": "", "bag-note": "",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
                "items-0-item_name": "Pro V1", "items-0-qty": "2"}
        data.update(extra)
        return brand_fields(data)

    def csv(self, text, **extra):
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", brand_columns(text).encode(), content_type="text/csv")
        return self.client.post(reverse("csv_import"), {"file": f, **extra})

    # --- Add box modal -------------------------------------------------------
    def test_modal_has_required_bag_card_before_items(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        for name in ("bag-brand", "bag-model", "bag-color", "bag-note"):
            self.assertIn(f'name="{name}"', html)
        self.assertNotIn('name="bag-customer"', html)            # no customers for the Japan sender
        self.assertLess(html.index('name="bag-brand"'), html.index("Items inside the bag"))
        self.assertLess(html.index("Items inside the bag"), html.index('name="items-0-club_option"'))
        self.assertRegex(html, r'Brand <span class="text-primary">\*</span>\s*<div class="mt-1"><select name="bag-brand"')
        self.assertNotIn("allow_no_bag", html)               # no override for the Japan sender
        self.client.force_login(self.owner)
        owner = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true")
        self.assertContains(owner, 'name="allow_no_bag"')
        self.assertContains(owner, 'name="bag-customer"')

    def test_box_needs_its_bag(self):
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data())
        self.assertContains(r, "Every box holds one golf bag")
        self.assertFalse(Box.objects.filter(code="OCT26-GN-12").exists())
        # the Japan sender can't use the owner override
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data(allow_no_bag="on"))
        self.assertFalse(Box.objects.filter(code="OCT26-GN-12").exists())
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data(**{"bag-item_name": "Ping  Hoofer",
                                                                             "bag-color": "Navy"}))
        self.assertEqual(r.status_code, 204)
        box = Box.objects.get(code="OCT26-GN-12")
        self.assertEqual([(i.title, i.is_bag) for i in box.items.all()], [("Ping Hoofer", True), ("Pro V1", False)])

    def test_owner_override_saves_without_bag_with_warning(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse("box_create"), self.modal_data(allow_no_bag="on"), follow=True)
        box = Box.objects.get(code="OCT26-GN-12")
        self.assertFalse(box.items.filter(is_bag=True).exists())
        self.assertContains(r, "WITHOUT a golf bag")
        self.assertContains(r, "No bag recorded")
        # half-filled bag with the override: the name is still needed
        r = self.htmx(self.owner, reverse("box_create"), self.modal_data(box_id="OCT26-GN-13", allow_no_bag="on",
                                                                         **{"bag-color": "Red"}))
        self.assertContains(r, "Choose the bag&#x27;s brand (or clear the bag fields).")

    def test_bag_word_rejected_as_item_inside(self):
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data(**{
            "bag-item_name": "Stand bag", "items-0-item_name": "", "items-0-club_option": "other",
            "items-0-club_other": "Golf bag"}))
        self.assertContains(r, "goes in the Golf bag section")

    # --- Box page and lists ------------------------------------------------
    def test_box_page_shows_bag_first_and_in_title(self):
        bag = self.add_bag(name="Titleist Stand Bag")
        bag.color = "Black"
        bag.save()
        self.client.force_login(self.owner)
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn("GN-3 · Titleist Stand Bag", html.replace('<span class="font-semibold text-primary-hover"> · ', " · "))
        self.assertLess(html.index(f'id="item-{bag.pk}"'), html.index(f'id="item-{self.item1.pk}"'))
        self.assertIn("data-bag", html)
        self.assertIn("Items inside the bag", html)
        self.assertNotIn("No bag recorded", html)
        self.assertNotIn(reverse("item_copy", args=[bag.pk]), html)   # one bag per box: no copy
        for url in (reverse("box_list"), reverse("dashboard"), reverse("cargo_history")):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "Titleist Stand Bag")

    def test_no_bag_and_two_bags_notes(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("box_detail", args=[self.box.pk])), "No bag recorded")
        self.add_bag(name="A")
        self.add_bag(name="B")
        self.assertContains(self.client.get(reverse("box_detail", args=[self.box.pk])), "2 golf bags recorded")

    def test_add_and_edit_bag_on_box_page(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_bag", args=[self.box.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'name="bag-brand"')
        r = self.htmx(self.japan, reverse("box_bag", args=[self.box.pk]), {"bag-item_name": ""})
        self.assertEqual(r["HX-Retarget"], "#bag-form-slot")
        r = self.htmx(self.japan, reverse("box_bag", args=[self.box.pk]),
                      {"bag-item_name": "Ping Hoofer", "bag-color": "Navy"})
        self.assertContains(r, 'id="bag-section"')
        self.assertContains(r, 'id="summary" hx-swap-oob="true"')
        bag = self.box.items.get(is_bag=True)
        self.htmx(self.japan, reverse("box_bag", args=[self.box.pk]), {"bag-item_name": "Ping Hoofer Lite"})
        self.assertEqual(self.box.items.filter(is_bag=True).get().pk, bag.pk)   # edited, not a second bag
        self.assertEqual(Box.objects.get(pk=self.box.pk).title, "GN-3 · Ping Hoofer Lite")

    def test_bag_cannot_be_copied_or_deleted_by_japan_sender(self):
        bag = self.add_bag()
        self.assertEqual(self.htmx(self.japan, reverse("item_copy", args=[bag.pk])).status_code, 400)
        self.assertEqual(self.htmx(self.japan, reverse("item_delete", args=[bag.pk])).status_code, 403)
        r = self.htmx(self.owner, reverse("item_delete", args=[bag.pk]))
        self.assertContains(r, "No bag recorded")
        self.assertFalse(JapanItem.objects.filter(pk=bag.pk).exists())

    def test_locked_box_bag_only_owner(self):
        self.ship()
        self.box.myanmar_received_date = self.box.created_at.date()
        self.box.save()
        self.assertEqual(self.htmx(self.japan, reverse("box_bag", args=[self.box.pk]),
                                   {"bag-item_name": "X"}).status_code, 403)
        self.htmx(self.owner, reverse("box_bag", args=[self.box.pk]), {"bag-item_name": "Owner fix"})
        self.assertEqual(self.box.bag_name, "Owner Fix")

    # --- Myanmar check -----------------------------------------------------
    def test_bag_checked_like_an_item(self):
        bag = self.add_bag()
        self.ship()
        self.client.force_login(self.myanmar)
        self.client.post(reverse("box_start_check", args=[self.box.pk]))
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn(reverse("item_check", args=[bag.pk]), html)
        self.assertLess(html.index(reverse("item_check", args=[bag.pk])), html.index(reverse("item_check", args=[self.item1.pk])))
        for item in (self.item1, self.item2):
            self.htmx(self.myanmar, reverse("item_check", args=[item.pk]), {"result": "matched"})
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ARRIVED)          # the bag isn't checked yet
        r = self.htmx(self.myanmar, reverse("item_check", args=[bag.pk]), {"result": "matched"})
        self.assertContains(r, "3/3 checked")
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.CHECKED)
        self.htmx(self.myanmar, reverse("item_check", args=[bag.pk]), {"result": "missing"})
        r = self.client.get(reverse("box_detail", args=[self.box.pk]))
        self.assertContains(r, "Mismatch summary")
        self.assertContains(r, "<b>Titleist Stand Bag</b>")

    # --- Customer orders ------------------------------------------------------
    def test_bag_for_a_customer_shows_on_their_page(self):
        bag = self.add_bag()
        bag.customer = "Ko Aung"
        bag.save()
        self.client.force_login(self.owner)
        from .models import Customer
        r = self.client.get(reverse("customer_detail", args=[Customer.objects.get(name="Ko Aung").pk]))
        self.assertContains(r, "Titleist Stand Bag")

    # --- Migration of existing boxes -----------------------------------------
    def test_migration_marks_single_bag_item(self):
        import importlib
        from django.apps import apps
        mark = importlib.import_module("tracker.migrations.0008_golf_bag").mark_existing_bags
        one = JapanItem.objects.create(box=self.box, type="Bag", item_name="Old stand bag")
        two = Box.objects.create(cargo=self.cargo, box_no=20)
        JapanItem.objects.create(box=two, type="Bag", item_name="A")
        JapanItem.objects.create(box=two, type="bag", item_name="B")
        none = Box.objects.create(cargo=self.cargo, box_no=21)
        JapanItem.objects.create(box=none, type="Driver", item_name="Qi10")
        mark(apps, None)
        self.assertEqual(list(JapanItem.objects.filter(is_bag=True)), [JapanItem.objects.get(pk=one.pk)])
        self.client.force_login(self.owner)
        for box in (two, none):
            self.assertContains(self.client.get(reverse("box_detail", args=[box.pk])), "No bag recorded")

    # --- CSV -----------------------------------------------------------------
    def test_csv_bag_rows(self):
        r = self.csv("box,kind,item_name,color,club_option,qty,customer\n"
                     "OCT26-GN-5,bag,Titleist Stand Bag,Black,,1,Ko Aung\n"
                     "OCT26-GN-5,,TSR2,,Driver 10.5,1,\n"
                     "OCT26-GN-5,item,Pro V1,,,2,\n")
        self.assertContains(r, "Imported 2 item(s) and 1 golf bag(s) into 1 box(es)")
        box = Box.objects.get(code="OCT26-GN-5")
        bag = box.items.get(is_bag=True)
        self.assertEqual((bag.item_name, bag.color, bag.customer, bag.type, bag.qty),
                         ("Titleist Stand Bag", "Black", "Ko Aung", "Golf bag", 1))
        self.assertEqual(box.items.first(), bag)
        # adding more items to a box that already has its bag needs no bag row
        self.assertContains(self.csv("box,item_name\nOCT26-GN-5,Head cover\n"), "Imported 1 item(s) into 1 box(es)")

    def test_csv_rejects_zero_or_two_bags_with_row_numbers(self):
        r = self.csv("box,kind,item_name\n"
                     "OCT26-GN-5,,TSR2\n"
                     "OCT26-GN-5,,Pro V1\n"
                     "OCT26-GN-6,bag,Bag one\n"
                     "OCT26-GN-6,,Wedge\n"
                     "OCT26-GN-6,bag,Bag two\n")
        self.assertContains(r, "Nothing was imported")
        self.assertContains(r, "Row 2: Box OCT26-GN-5 has no golf bag – add a row with kind = bag (box OCT26-GN-5 is on rows 2, 3)")
        self.assertContains(r, "Row 4: Box OCT26-GN-6 has 2 bag rows (rows 4, 6) – a box holds exactly one golf bag")
        self.assertFalse(Box.objects.filter(code__in=["OCT26-GN-5", "OCT26-GN-6"]).exists())
        # GN-3 has no bag yet: adding items without a bag row is rejected too
        self.assertContains(self.csv("box,item_name\nGN-3,Head cover\n"), "Box GN-3 has no golf bag")
        # GN-3 with its bag: a second bag row is rejected
        self.add_bag()
        self.assertContains(self.csv("box,kind,item_name\nGN-3,bag,Another\n"),
                            "Box GN-3 already has a golf bag (Titleist Stand Bag)")

    def test_csv_bag_row_checks(self):
        r = self.csv("box,kind,item_name,club_option,qty\n"
                     "OCT26-GN-5,bag,,,1\n"
                     "OCT26-GN-5,bag,X,Wedge 52,2\n"
                     "OCT26-GN-5,box,Y,,1\n")
        for msg in ("Row 3: bag row: leave club_option empty",
                    "Row 3: bag row: qty must be 1", "Row 4: kind must be bag or item"):
            self.assertContains(r, msg)

    def test_csv_owner_override(self):
        r = self.csv("box,kind,item_name\nOCT26-GN-5,,TSR2\n", allow_bag_exception="on")
        self.assertContains(r, "Imported 1 item(s) into 1 box(es)")
        self.assertContains(r, "Imported with the owner override")
        self.assertContains(r, "Row 2: Box OCT26-GN-5 has no golf bag")
        self.assertTrue(Box.objects.filter(code="OCT26-GN-5").exists())

    # --- Admin -------------------------------------------------------------
    def admin_post(self, bags, **extra):
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        data = {"cargo": self.cargo.pk, "box_no": "3", "status": "packed", "notes": "",
                "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "2", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "extra_items-TOTAL_FORMS": "0", "extra_items-INITIAL_FORMS": "0",
                "extra_items-MIN_NUM_FORMS": "0", "extra_items-MAX_NUM_FORMS": "1000"}
        for i, item in enumerate((self.item1, self.item2)):
            data.update({f"items-{i}-id": item.pk, f"items-{i}-box": self.box.pk, f"items-{i}-type": item.type,
                         f"items-{i}-item_name": item.item_name, f"items-{i}-qty": item.qty,
                         f"items-{i}-condition": "new"})
            if i < bags:
                data[f"items-{i}-is_bag"] = "on"
        data.update(extra)
        return self.client.post(f"/admin/tracker/box/{self.box.pk}/change/", data, follow=True)

    def test_admin_enforces_one_bag_with_override(self):
        self.assertContains(self.admin_post(0), "A box holds exactly one golf bag, but this box would have 0")
        self.assertContains(self.admin_post(2), "this box would have 2")
        self.assertFalse(JapanItem.objects.filter(is_bag=True).exists())
        r = self.admin_post(1)
        self.assertNotContains(r, "golf bag(s)")
        self.assertEqual(list(JapanItem.objects.filter(is_bag=True)), [self.item1])
        r = self.admin_post(2, allow_bag_exception="on")
        self.assertContains(r, "Box GN-3 now has 2 golf bag(s)")
        self.assertEqual(JapanItem.objects.filter(is_bag=True).count(), 2)
        self.assertContains(self.client.get("/admin/tracker/box/"), "2 bags")

    def test_admin_item_second_bag_rejected(self):
        self.add_bag()
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        data = {"box": self.box.pk, "type": "Wedge", "item_name": "RTX 6", "qty": 3, "condition": "new",
                "quantity_unit": "pcs", "is_bag": "on",
                "receive_check-TOTAL_FORMS": "0", "receive_check-INITIAL_FORMS": "0",
                "receive_check-MIN_NUM_FORMS": "0", "receive_check-MAX_NUM_FORMS": "1"}
        r = self.client.post(f"/admin/tracker/japanitem/{self.item2.pk}/change/", data, follow=True)
        self.assertContains(r, "already has a golf bag")
        r = self.client.post(f"/admin/tracker/japanitem/{self.item2.pk}/change/", {**data, "allow_bag_exception": "on"},
                             follow=True)
        self.assertContains(r, "now has 2 golf bag(s)")


class CustomerRequestsPageRemovedTests(BaseTest):
    """The separate Customer requests page is gone; orders live on each customer's page."""

    def test_no_requests_page_menu_or_dashboard_section(self):
        CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2")
        for user in (self.owner, self.myanmar):
            self.client.force_login(user)
            r = self.client.get(reverse("dashboard"))
            self.assertNotContains(r, "Customer requests")
            self.assertNotContains(r, "Open customer requests")
            self.assertEqual(self.client.get("/requests/").status_code, 404)

    def test_orders_still_work_on_customer_page(self):
        from .models import Customer
        self.client.force_login(self.owner)
        ko = Customer.ensure("Ko Aung")
        self.client.post(reverse("customer_new_order", args=[ko.pk]), order_data(("Qi10", 1, "")))
        req = CustomerRequest.objects.get(item_name="QI10")                     # display spelling
        r = self.client.get(reverse("customer_detail", args=[ko.pk]))
        self.assertContains(r, f'id="request-{req.pk}"')
        self.assertNotContains(r, "Attach to box item")


class PaletteTests(TestCase):
    """One colour palette: tokens in agom.css, used everywhere, readable (WCAG AA)."""

    BASE = __import__("pathlib").Path(__file__).resolve().parent.parent

    def tokens(self):
        import re
        css = (self.BASE / "static/css/agom.css").read_text()
        root = css[css.index(":root {"):css.index("}", css.index(":root {"))]
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", root))

    @staticmethod
    def contrast(a, b):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        hi, lo = sorted([lum(a), lum(b)], reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    def test_requested_tokens(self):
        t = {k: v.upper() for k, v in self.tokens().items()}
        expected = {"bg": "#F7F9F0", "card": "#FFFFFF", "card-border": "#E3EEDB", "grad-from": "#F4F7E9",
                    "grad-to": "#C4E6C9", "primary": "#3A7D52", "primary-hover": "#2F6B45", "text": "#1F3A2B",
                    "text-muted": "#5B6B60", "sage": "#EEF5E3", "mint": "#E3F1E6", "cream": "#F6F4E6",
                    "sand": "#F6ECDD", "ok": "#3A7D52", "ok-bg": "#E3F1E6", "warn": "#9A6B22", "warn-bg": "#F7EEDB",
                    "danger": "#B4473B", "danger-bg": "#F6E3E0", "info": "#2F7A72", "info-bg": "#E1F0EE"}
        for name, value in expected.items():
            self.assertEqual(t.get(name), value, name)

    def test_text_contrast_aa(self):
        t = self.tokens()
        white = "#FFFFFF"
        pairs = {
            # pills and tinted check buttons: ink on tint
            "ok pill": ("ok-ink", "ok-bg"), "warn pill": ("warn-ink", "warn-bg"),
            "danger pill": ("danger-ink", "danger-bg"), "info pill": ("info-ink", "info-bg"),
            "neutral pill": ("text-muted", "sage"), "viber pill": ("viber-ink", "viber-bg"),
            # body text
            "text on bg": ("text", "bg"), "muted on card": ("text-muted", "card"), "muted on bg": ("text-muted", "bg"),
            "link on card": ("primary", "card"), "link on bg": ("primary", "bg"),
            "muted on stat tints": ("text-muted", "sand"), "text on gradient": ("text", "grad-to"),
            "soft button": ("primary-hover", "mint"),
        }
        for name, (fg, bg) in pairs.items():
            with self.subTest(name):
                self.assertGreaterEqual(self.contrast(t[fg], t[bg]), 4.5)
        for fill in ("primary", "primary-hover", "ok", "warn", "danger", "info"):   # white text on solid buttons
            with self.subTest(fill):
                self.assertGreaterEqual(self.contrast(white, t[fill]), 4.5)

    def test_templates_use_tokens_not_hard_coded_colours(self):
        import re
        bad = re.compile(r"(?<![\w-])(?:bg|text|border|border-l|ring|from|to|via|divide|accent)-"
                         r"(?:emerald|red|violet|purple|amber|orange|sky|stone|slate|rose|indigo|pink|blue)-\d")
        for path in (self.BASE / "templates").rglob("*.html"):
            html = path.read_text()
            with self.subTest(path.name):
                self.assertIsNone(bad.search(html))
                hexes = [h for h in re.findall(r"#[0-9A-Fa-f]{6}\b", html) if path.name != "base.html"]
                self.assertEqual(hexes, [])
        tags = (self.BASE / "tracker/templatetags/tracker_tags.py").read_text()
        self.assertNotRegex(tags, r"violet|purple|indigo|#[0-9a-fA-F]{6}")

    def test_badges_use_status_pills(self):
        from .templatetags.tracker_tags import badge, order_badge
        self.assertEqual(badge("missing"), "pill pill-danger")
        self.assertEqual(badge("checked"), "pill pill-ok")
        # customer orders: Ordered sage, Arrived teal, Delivered green, Cancelled grey
        self.assertEqual([order_badge(s) for s in ("ordered", "arrived", "delivered", "cancelled")],
                         ["pill-neutral", "pill-info", "pill-ok", "pill-grey"])


class LoginPageTests(BaseTest):
    """Login page: "Fairway at dawn" scene (inline SVG + CSS), floating card, same login logic."""

    def css(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "static/css/login.css").read_text()

    def test_scene_and_card(self):
        r = self.client.get(reverse("login"))
        html = r.content.decode()
        for part in ('class="login-scene" aria-hidden="true"', 'class="putting-green"', 'class="cup"', 'class="flag"',
                     'class="ball"', 'class="trail"', 'class="ripple"', "cloud-a", "hill-back", "hill-mid", "hill-front",
                     'login-page scene-animate', "css/login.css"):
            self.assertIn(part, html)
        self.assertIn('src="/static/img/display/3d-logo.png', html)
        self.assertIn("AGOM Cargo Tracker", html)
        self.assertIn("Japan → Myanmar golf shipments", html)
        self.assertIn("Take with Trust · Play with Confidence · Get Achievements", html)
        self.assertIn('class="pw-toggle" aria-label="Show password" aria-pressed="false" aria-controls="id_password"', html)
        self.assertIn('autocapitalize="none"', html)          # phones don't capitalise the username
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn('class="btn btn-primary w-full', html)
        self.assertNotRegex(html, r"<img[^>]+src=\"http")     # no external images

    def test_wrong_password_shows_danger_message_without_replaying_intro(self):
        r = self.client.post(reverse("login"), {"username": "owner", "password": "nope"})
        html = r.content.decode()
        self.assertIn('role="alert"', html)
        self.assertIn("bg-danger-bg", html)
        self.assertIn("text-danger-ink", html)
        self.assertIn("Wrong username or password.", html)
        self.assertNotIn("scene-animate", html)               # scene shown finished, no distraction
        self.assertIn('value="owner"', html)

    def test_login_still_works_and_redirects(self):
        r = self.client.post(reverse("login") + "?next=/boxes/", {"username": "owner", "password": "pw-12345-x",
                                                                  "next": "/boxes/"})
        self.assertRedirects(r, "/boxes/", fetch_redirect_response=False)

    def test_animation_only_transform_and_opacity_and_reduced_motion(self):
        import re
        css = self.css()
        self.assertIn("@media (prefers-reduced-motion: no-preference)", css)
        animated = css[css.index("@media (prefers-reduced-motion: no-preference)"):]
        for block in re.findall(r"@keyframes [\w-]+ \{(.*?)\n\}", animated, re.S):
            props = set(re.findall(r"([a-z-]+)\s*:", re.sub(r"\d+(\.\d+)?%|from|to", "", block)))
            self.assertLessEqual(props, {"transform", "opacity"}, props)
        # the default (no-motion) state is the final scene: ball in the cup
        self.assertIn(".ball { transform: translate(1120px, 666px) scale(.82); }", css)
        self.assertNotRegex(css, r"#[0-9A-Fa-f]{3,6}\b")    # colours only from the agom.css tokens


class MyanmarBannerTests(BaseTest):
    """The Myanmar checker sees a Burmese banner with stickers; Owner / Admin keep the English one."""

    LINES = [
        "ပျော်ရွှင်စရာလုပ်ငန်းခွင်က အချိန်တိုင်းမရနိုင်ပါ။",
        "သို့ပေမဲ့ သင်ဟာ ရုပ်ချောနေတာ တစ်ခုတည်းနဲ့ ပျော်ရွှင်ဖို့ထိုက်တန်ပါတယ်။",
        "ပြုံးလိုက်ပါ ရယ်မောလိုက်ပါ။",
        "သာယာရွှင်လန်းပြီးအလုပ်လုပ်ရတဲ့နေ့လေးကိုပိုင်ဆိုင်နိုင်ပါစေ Miss Thaw Thaw။",
    ]

    def dashboard(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("dashboard")).content.decode()

    def test_myanmar_checker_sees_burmese_banner(self):
        html = self.dashboard(self.myanmar)
        self.assertIn('class="hero hero-mm"', html)
        self.assertIn('lang="my"', html)
        import re
        text = html[html.index('<div class="hero-text mm-text" lang="my">'):]
        text = text[:text.index("</div>")]
        paras = [re.sub(r"<[^>]+>", "", p).strip() for p in re.findall(r"<p[^>]*>(.*?)</p>", text, re.S)]
        self.assertEqual(paras, self.LINES)              # exactly these four lines, nothing else
        self.assertIn('class="mm-lead">' + self.LINES[0], html)
        self.assertIn('<span class="mm-name" lang="en">Miss Thaw Thaw</span>။</p>', html)
        self.assertNotIn("— Miss Thaw Thaw", html)
        self.assertNotIn("mm-sign", html)
        self.assertIn("family=Noto+Sans+Myanmar:wght@400;600", html)
        self.assertNotIn("Golf stock route", html)

    def test_other_roles_keep_english_banner(self):
        superuser = User.objects.create_superuser("admin2", password="pw-12345-x")
        for user in (self.owner, superuser):                      # the Japan sender has their own (JapanBannerTests)
            with self.subTest(user=user.username):
                html = self.dashboard(user)
                self.assertIn("Golf stock route", html)
                self.assertIn("Take with Trust.", html)
                self.assertNotIn("hero-mm", html)
                self.assertNotIn('lang="my"', html)
                self.assertNotIn("Noto+Sans+Myanmar", html)
                self.assertNotIn(self.LINES[0], html)

    def test_owner_who_is_also_in_myanmar_group_keeps_english_banner(self):
        self.owner.groups.add(Group.objects.get(name=roles.MYANMAR))
        self.assertIn("Golf stock route", self.dashboard(self.owner))

    def test_missing_stickers_are_hidden(self):
        from unittest import mock
        from . import views
        with mock.patch.object(views.finders, "find", return_value=None):
            html = self.dashboard(self.myanmar)
        self.assertNotIn('class="stickers"', html)
        # only the GIFs of "confetti" and "ok" exist: those two show (GIF only, no WebP source)
        present = {"img/stickers/animated_confetti.gif", "img/stickers/animated_ok.gif"}
        with mock.patch.object(views.finders, "find", side_effect=lambda p: p if p in present else None):
            html = self.dashboard(self.myanmar)
        self.assertIn("sticker-confetti", html)
        self.assertIn("sticker-ok", html)
        self.assertNotIn("sticker-peace", html)
        self.assertNotIn("sticker-stars", html)
        self.assertIn('<img src="/static/img/stickers/animated_confetti.gif', html)
        self.assertNotIn("animated_confetti.webp", html)

    def test_animated_stickers_in_corners_with_still_pictures_for_reduced_motion(self):
        import re
        html = self.dashboard(self.myanmar)                     # the real files are in the repo
        corners = {"confetti": ("top-left", "happy"), "stars": ("top-right", "stars"),
                   "peace": ("bottom-left", "peace"), "ok": ("bottom-right", "ok")}
        for name, (corner, still) in corners.items():
            with self.subTest(name):
                block = re.search(r'<picture class="sticker sticker-%s" data-corner="%s">(.*?)</picture>' % (name, corner),
                                  html, re.S).group(1)
                # reduced motion first (the first matching <source> wins), then animated WebP, then the GIF
                self.assertRegex(block, r'<source media="\(prefers-reduced-motion: reduce\)" '
                                        r'srcset="/static/img/display/stickers/%s\.webp[^"]*" type="image/webp">' % still)
                self.assertRegex(block, r'<source srcset="/static/img/display/stickers/animated_%s\.webp' % name)
                self.assertLess(block.index("prefers-reduced-motion"), block.index("animated_%s.webp" % name))
                self.assertRegex(block, r'<img src="/static/img/stickers/animated_%s\.gif[^"]*" alt="" width="120" '
                                        r'height="120" loading="lazy"' % name)

    def test_animated_webps_keep_every_frame_and_are_small(self):
        from pathlib import Path
        from PIL import Image
        img = Path(__file__).resolve().parent.parent / "static/img"
        for name in ("confetti", "stars", "peace", "ok"):
            with self.subTest(name):
                webp, gif = img / f"display/stickers/animated_{name}.webp", img / f"stickers/animated_{name}.gif"
                self.assertLess(webp.stat().st_size, 400 * 1024)
                with Image.open(webp) as w, Image.open(gif) as g:
                    self.assertEqual(w.n_frames, g.n_frames)      # every frame kept
                    self.assertTrue(getattr(w, "is_animated", False))
                    self.assertEqual(w.mode, "RGBA")              # white background cut out

    def test_corner_layout_and_no_css_bob(self):
        import re
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent
        css = (base / "static/css/agom.css").read_text()
        self.assertRegex(css, r"\.mm-text \{[^}]*text-align: center")
        corners = {"confetti": ("top", "left"), "stars": ("top", "right"), "peace": ("bottom", "left"),
                   "ok": ("bottom", "right")}
        for name, (v, h) in corners.items():
            rule = re.search(r"\.sticker-%s \{([^}]*)\}" % name, css).group(1)
            self.assertIn(f"{v}:", rule)
            self.assertIn(f"{h}:", rule)
        self.assertNotIn("sticker-bob", css)                   # the stickers animate themselves
        script = (base / "scripts/make_display_images.py").read_text()
        self.assertNotIn("animated_", script)                  # it would freeze the GIFs into one frame


class YenAmountTests(BaseTest):
    """Japanese yen: cargo fee per box, amount per piece on the bag and items, totals – never shown to Myanmar."""

    def modal_data(self, **extra):
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-21", "notes": "", "cargo_fee_jpy": "12500",
                "bag-item_name": "Titleist Stand Bag", "bag-color": "", "bag-customer": "", "bag-note": "",
                "bag-price_jpy": "¥8,000",
                "items-TOTAL_FORMS": "3", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-item_name": "TSR2", "items-0-qty": "2", "items-0-price_jpy": "12,500",
                "items-1-item_name": "Pro V1", "items-1-qty": "1", "items-1-price_jpy": "3000",
                "items-2-item_name": "No price", "items-2-qty": "1", "items-2-price_jpy": ""}
        data.update(extra)
        return data

    def test_modal_has_yen_fields_and_live_summary(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        for name in ("cargo_fee_jpy", "bag-price_jpy", "items-0-price_jpy"):
            self.assertRegex(html, r'<span class="yen-sign" aria-hidden="true">¥</span><input type="text" name="%s" '
                                   r'inputmode="numeric"' % name)
        self.assertLess(html.index('name="box_id"'), html.index('name="cargo_fee_jpy"'))   # next to Box ID
        self.assertIn("data-money-summary", html)
        self.assertIn('data-sum="total"', html)
        self.assertIn("data-line-total", html)

    def test_amounts_saved_and_totals(self):
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data())
        self.assertEqual(r.status_code, 204)
        box = Box.objects.get(code="OCT26-GN-21")
        self.assertEqual(box.cargo_fee_jpy, 12500)
        bag, tsr2, prov1, none = box.items.all()
        self.assertEqual((bag.price_jpy, tsr2.price_jpy, prov1.price_jpy, none.price_jpy), (8000, 12500, 3000, None))
        self.assertEqual(tsr2.line_total, 25000)                 # amount is per piece
        self.assertEqual(box.money(), {"bag": 8000, "items": 28000, "cargo_fee": 12500, "total": 48500, "any": True})
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_detail", args=[box.pk])).content.decode()
        self.assertIn("Bag <b>¥8,000</b>", html)
        self.assertIn("Items <b>¥28,000</b>", html)
        self.assertIn("Cargo fee <b>¥12,500</b>", html)
        self.assertIn("Total <b>¥48,500</b>", html)
        self.assertIn("¥12,500 × 2 = ¥25,000", html)              # line total on the item row
        self.assertIn("data-box-total>¥48,500<", self.client.get(reverse("box_list")).content.decode())

    def test_bad_amounts_rejected(self):
        for bad in ("-5", "abc", "12.5"):
            with self.subTest(bad=bad):
                r = self.htmx(self.japan, reverse("box_create"), self.modal_data(**{"items-0-price_jpy": bad}))
                self.assertEqual(r.status_code, 200)
                self.assertFalse(Box.objects.filter(code="OCT26-GN-21").exists())

    def test_copy_keeps_amount(self):
        self.item1.price_jpy = 9900
        self.item1.save()
        self.htmx(self.japan, reverse("item_copy", args=[self.item1.pk]))
        self.assertEqual(JapanItem.objects.latest("id").price_jpy, 9900)

    def test_hidden_from_myanmar_checker(self):
        self.box.cargo_fee_jpy = 7000
        self.box.save()
        self.item1.price_jpy = 12500
        self.item1.save()
        self.ship()
        self.client.force_login(self.owner)
        self.assertIn("¥12,500", self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode())
        self.client.force_login(self.myanmar)
        for url in (reverse("box_detail", args=[self.box.pk]), reverse("box_list"), reverse("dashboard"),
                    reverse("box_edit", args=[self.box.pk])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn("¥", html)
                self.assertNotIn("cargo_fee_jpy", html)
                self.assertNotIn("Cargo fee", html)
        self.client.post(reverse("box_start_check", args=[self.box.pk]))
        r = self.htmx(self.myanmar, reverse("item_check", args=[self.item1.pk]), {"result": "matched"})  # HTMX parts too
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("¥", r.content.decode())
        self.assertNotIn("¥", self.client.get(reverse("item_row", args=[self.item1.pk])).content.decode())
        # a Myanmar edit of the box can't wipe the fee
        self.client.post(reverse("box_edit", args=[self.box.pk]), {"status": "arrived", "notes": "x",
                                                                   "myanmar_received_date": ""})
        self.box.refresh_from_db()
        self.assertEqual(self.box.cargo_fee_jpy, 7000)

    def test_csv_amounts_and_cargo_fee(self):
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", brand_columns(
            "box,kind,item_name,qty,amount_jpy,cargo_fee_jpy\n"
            "OCT26-GN-8,bag,Ping Hoofer,1,8000,\"¥12,500\"\n"
            "OCT26-GN-8,,TSR2,2,\"12,500\",\n"
            "OCT26-GN-8,,Pro V1,1,¥3000,\n").encode(), content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 2 item(s)")
        box = Box.objects.get(code="OCT26-GN-8")
        self.assertEqual(box.cargo_fee_jpy, 12500)
        self.assertEqual(sorted(box.items.values_list("price_jpy", flat=True)), [3000, 8000, 12500])
        self.assertEqual(box.money()["total"], 8000 + 25000 + 3000 + 12500)
        f = SimpleUploadedFile("l.csv", brand_columns(
            "box,kind,item_name,amount_jpy,cargo_fee_jpy\n"
            "OCT26-GN-9,bag,Bag,,\n"
            "OCT26-GN-9,,TSR2,12.5,500\n").encode(), content_type="text/csv")
        r = self.client.post(reverse("csv_import"), {"file": f})
        self.assertContains(r, "amount_jpy: whole yen only, e.g. 12500")
        self.assertContains(r, "cargo_fee_jpy goes on the box&#x27;s bag row")

    def test_customer_name_without_icon(self):
        self.item1.customer = "Ko Aung"
        self.item1.save()
        self.client.force_login(self.owner)
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertRegex(html, r'class="customer-chip[^"]*">Ko Aung</a>')     # the name alone
        self.assertNotIn("👤", html)


class MonthlyBoxIdTests(BaseTest):
    """Box IDs like OCT26-KB-3: month (Japan time) + cargo code + next number for that cargo that month."""

    def setUp(self):
        super().setUp()
        self.kb = Cargo.objects.create(code="KB", name="KB Cargo")
        self.ws = Cargo.objects.create(code="WS", name="WS Cargo")

    @staticmethod
    def at(y, m, d, h=12):
        import datetime
        return datetime.datetime(y, m, d, h, tzinfo=datetime.timezone.utc)

    def make(self, cargo, when):
        from .models import Box as B
        box = B(cargo=cargo)
        box.set_code(B.next_code(cargo, when)[0])
        box.save()
        return box

    def test_numbering_per_company_and_month(self):
        oct_ = self.at(2026, 10, 15)
        self.assertEqual([self.make(self.kb, oct_).code for _ in range(3)], ["OCT26-KB-1", "OCT26-KB-2", "OCT26-KB-3"])
        self.assertEqual(self.make(self.ws, oct_).code, "OCT26-WS-1")              # its own counter
        self.assertEqual(Box.next_code(self.kb, oct_)[3], "4th KB box in October 2026")
        self.assertEqual(self.make(self.kb, self.at(2026, 11, 2)).code, "NOV26-KB-1")   # new month starts at 1
        self.assertEqual(self.make(self.kb, self.at(2026, 12, 31)).code, "DEC26-KB-1")
        self.assertEqual(self.make(self.kb, self.at(2027, 1, 1)).code, "JAN27-KB-1")    # new year too
        self.assertEqual(self.make(self.kb, oct_).code, "OCT26-KB-4")              # October keeps counting

    def test_month_is_japan_time(self):
        # 31 Oct 16:30 UTC is already 1 Nov 01:30 in Tokyo (and still October in Yangon)
        self.assertEqual(Box.next_code(self.kb, self.at(2026, 10, 31, 16))[0][:5], "NOV26")
        self.assertEqual(Box.next_code(self.kb, self.at(2026, 10, 31, 14))[0][:5], "OCT26")

    def test_gaps_after_delete_never_duplicate(self):
        oct_ = self.at(2026, 10, 15)
        b1, b2, b3 = (self.make(self.kb, oct_) for _ in range(3))
        b2.delete()
        self.assertEqual(Box.next_code(self.kb, oct_)[0], "OCT26-KB-4")            # highest + 1, gap stays
        b3.delete()
        self.assertEqual(Box.next_code(self.kb, oct_)[0], "OCT26-KB-2")            # free again, still unique
        self.assertFalse(Box.objects.filter(code="OCT26-KB-2").exists())

    def test_existing_boxes_keep_their_ids(self):
        self.assertEqual(self.box.label, "GN-3")
        self.assertIsNone(self.box.code)

    def test_parse_and_normalise(self):
        from .models import parse_box_code
        self.assertEqual(parse_box_code(" oct26-kb-03 "), ("OCT26", "KB", 3, "OCT26-KB-3"))
        for bad in ("GN-3", "OCT-KB-3", "XYZ26-KB-3", "OCT26KB3", ""):
            self.assertIsNone(parse_box_code(bad), bad)

    def test_next_id_endpoint_fills_the_field(self):
        from unittest import mock
        self.make(self.kb, self.at(2026, 10, 3))
        self.make(self.kb, self.at(2026, 10, 4))
        self.client.force_login(self.japan)
        with mock.patch("django.utils.timezone.now", return_value=self.at(2026, 10, 16)):
            r = self.client.get(reverse("box_next_id"), {"cargo": self.kb.pk}, HTTP_HX_REQUEST="true")
        self.assertContains(r, 'id="box-id-field"')
        self.assertContains(r, 'value="OCT26-KB-3"')
        self.assertContains(r, "Auto: 3rd KB box in October 2026")
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.get(reverse("box_next_id"), {"cargo": self.kb.pk}).status_code, 403)

    def test_modal_wiring_and_no_old_helper(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertRegex(html, r'<select name="cargo"[^>]*hx-get="/boxes/next-id/"[^>]*hx-target="#box-id-field"')
        self.assertIn('name="box_id"', html)
        self.assertNotIn("Number only", html)
        self.assertNotIn('name="box_no"', html)

    def modal(self, **extra):
        data = {"cargo": self.kb.pk, "box_id": "", "notes": "", "bag-item_name": "Bag",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000", "items-0-item_name": "TSR2", "items-0-qty": "1"}
        data.update(extra)
        return self.htmx(self.japan, reverse("box_create"), data)

    def test_save_checks_again_and_validates(self):
        from .models import box_period
        period = box_period()[0]
        self.assertEqual(self.modal().status_code, 204)                          # empty Box ID: numbered on save
        self.assertTrue(Box.objects.filter(code=f"{period}-KB-1").exists())
        self.modal(box_id=f"{period}-KB-1")                                       # taken meanwhile -> next free
        self.assertTrue(Box.objects.filter(code=f"{period}-KB-2").exists())
        r = self.modal(box_id=f"{period}-WS-9")
        self.assertContains(r, "This box is for KB, so its ID must have KB in it")
        r = self.modal(box_id="hello")
        self.assertContains(r, "Box ID looks like")
        self.modal(box_id="sep26-kb-7")                                           # editable: another month is fine
        self.assertTrue(Box.objects.filter(code="SEP26-KB-7", period="SEP26", seq=7).exists())

    def csv(self, text):
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", brand_columns(text).encode(), content_type="text/csv")
        return self.client.post(reverse("csv_import"), {"file": f})

    def test_csv_empty_box_gets_new_ids(self):
        from .models import box_period
        period = box_period()[0]
        self.make(self.kb, None)                                                  # KB-1 already used this month
        r = self.csv("box,cargo,kind,item_name\n"
                     ",KB,bag,Bag A\n"
                     ",KB,,TSR2\n"
                     ",KB,bag,Bag B\n"
                     ",KB,,Pro V1\n"
                     ",WS,bag,Bag C\n")
        self.assertContains(r, "Imported 2 item(s) and 3 golf bag(s) into 3 box(es)")
        self.assertEqual(Box.objects.get(code=f"{period}-KB-2").bag_name, "Bag A")
        self.assertEqual(list(Box.objects.get(code=f"{period}-KB-3").items.values_list("item_name", flat=True)),
                         ["Bag B", "Pro V1"])
        self.assertEqual(Box.objects.get(code=f"{period}-WS-1").bag_name, "Bag C")

    def test_csv_given_ids_validated(self):
        r = self.csv("box,cargo,kind,item_name\n"
                     ",,bag,No cargo\n"
                     ",KB,,Item before any bag\n"
                     "OCT26-ZZ-1,,bag,Unknown cargo\n"
                     "OCT26-KB-5,WS,bag,Wrong cargo\n"
                     "GN-77,,bag,Old style that doesn't exist\n"
                     "hello,,bag,Garbage\n")
        for msg in ("Row 2: Put the Box ID in box", "Row 3: No box for this KB item",
                    "Row 4: Unknown cargo code &#x27;ZZ&#x27;", "Row 5: Box ID &#x27;OCT26-KB-5&#x27; is a KB box but cargo says WS",
                    "Row 6: Box &#x27;GN-77&#x27; should look like OCT26-KB-3", "Row 7: Box &#x27;hello&#x27; should look like"):
            self.assertContains(r, msg)
        self.assertFalse(Box.objects.filter(code__isnull=False).exists())
        # a given monthly ID creates that box; an existing old-style box still takes more items
        self.add_bag()
        r = self.csv("box,kind,item_name\noct26-kb-05,bag,Bag\nOCT26-KB-5,,TSR2\nGN-3,,Head cover\n")
        self.assertContains(r, "into 2 box(es)")
        self.assertEqual(Box.objects.get(code="OCT26-KB-5").items.count(), 2)
        self.assertTrue(self.box.items.filter(item_name="Head Cover").exists())


class BrandModelItemTests(BaseTest):
    """Item cards: Brand dropdown (the Brand list A → Z, then Other) + Model box, shown as "XXIO MP1400"."""

    LIST = ["Bettinardi", "Callaway", "Cleveland Golf", "Cobra", "Mizuno", "Odyssey", "PING", "PXG",
            "Scotty Cameron", "Srixon", "TaylorMade", "Titleist", "Wilson Staff", "XXIO"]

    def add(self, brand, model="", **extra):
        return self.htmx(self.japan, reverse("item_add", args=[self.box.pk]),
                         {"brand": brand, "model": model, "qty": "1", **extra})

    def latest(self):
        return JapanItem.objects.latest("id")

    def test_brand_list_and_order(self):
        import re
        from .models import Brand, brand_names
        self.assertEqual(brand_names(), self.LIST)
        self.assertFalse(Brand.objects.get(display_name="Honma").active)           # kept, not deleted
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        select = html[html.index('name="items-0-brand"'):]
        select = select[:select.index("</select>")]
        self.assertEqual(re.findall(r'<option value="([^"]*)"', select), [""] + self.LIST + ["Other"])
        self.assertNotIn("Honma", select)
        Brand.objects.create(display_name="Honma Golf")                             # owner adds one in /admin/
        self.assertIn("Honma Golf", brand_names())
        self.assertEqual(brand_names()[4:6], ["Honma Golf", "Mizuno"])

    def test_brand_and_model_saved_and_shown(self):
        from .models import BrandModel
        self.add("XXIO", "mp 1400")
        item = self.latest()
        self.assertEqual((item.brand, item.model, item.item_name), ("XXIO", "MP 1400", "XXIO MP 1400"))
        for typed in ("MP1400", "mp-1400", "  Mp 1400 "):                         # same model: saved spelling
            self.add("XXIO", typed)
            self.assertEqual(self.latest().item_name, "XXIO MP 1400")
        self.assertEqual(BrandModel.objects.filter(brand__display_name="XXIO").count(), 1)
        self.add("TaylorMade", "qi10 max")
        self.assertEqual(self.latest().item_name, "TaylorMade QI10 Max")
        self.add("PING")                                                            # model optional
        self.assertEqual((self.latest().brand, self.latest().model, self.latest().item_name), ("PING", "", "PING"))

    def test_other_brand_model_is_free_text(self):
        from .models import Brand
        self.add("Other", "honma tw757")
        item = self.latest()
        self.assertEqual((item.brand, item.model, item.item_name), ("", "Honma TW757", "Honma TW757"))
        self.assertFalse(Brand.objects.get(display_name="Honma").active)           # no brand created or switched on
        from .forms import JapanItemForm
        form = JapanItemForm(instance=item)
        self.assertEqual((form["brand"].value(), form["model"].value()), ("Other", "Honma TW757"))

    def test_brand_required_model_optional(self):
        r = self.add("", "MP1400")
        self.assertContains(r, "Choose the brand (or Other).")
        r = self.add("Kasco", "Zeus")                                              # not on the list
        self.assertContains(r, "Select a valid choice")
        self.assertEqual(self.box.items.count(), 2)

    def test_no_free_text_brand_parsing(self):
        self.add("Other", "XXIO MP1400")                                           # typed brand words stay text
        self.assertEqual((self.latest().brand, self.latest().model, self.latest().item_name),
                         ("", "XXIO MP1400", "XXIO MP1400"))
        self.client.force_login(self.japan)
        html = self.client.get(reverse("item_suggest"), {"brand": "", "model": "Ryoma D1"}).content.decode()
        self.assertNotIn("a brand?", html)
        self.assertNotIn("add_brand", html)

    def test_model_suggestions_per_brand(self):
        import re
        from .models import Brand, BrandModel
        xxio, titleist = Brand.objects.get(display_name="XXIO"), Brand.objects.get(display_name="Titleist")
        for name in ("MP1400", "MP1200", "Zeus 12", "Zeus 7"):
            BrandModel.objects.create(brand=xxio, display_name=name)
        BrandModel.objects.create(brand=titleist, display_name="TSR2")
        self.client.force_login(self.japan)

        def rows(**params):
            html = self.client.get(reverse("item_suggest"), params, HTTP_HX_REQUEST="true").content.decode()
            return re.findall(r'data-pick="([^"]*)" data-section="(\w+)"', html), html
        got, _ = rows(**{"items-0-brand": "XXIO", "items-0-model": ""})
        self.assertEqual([p for p, _ in got], ["MP1200", "MP1400", "Zeus 7", "Zeus 12"])     # A → Z, numbers by value
        got, _ = rows(**{"items-0-brand": "XXIO", "items-0-model": "mp-14"})                # ignores case/punctuation
        self.assertEqual(got, [("MP1400", "saved")])
        got, _ = rows(**{"items-0-brand": "Titleist", "items-0-model": ""})                  # another brand
        self.assertEqual(got, [("TSR2", "saved")])
        got, html = rows(**{"items-0-brand": "Other", "items-0-model": "honma tw757"})        # free text: no list
        self.assertEqual(got, [])
        self.assertIn("Saved as <b>Honma TW757</b>", html)
        got, html = rows(**{"items-0-brand": "", "items-0-model": "mp"})
        self.assertEqual(got, [])
        self.assertIn("Choose the brand first", html)
        # models typed on other cards of the open box (same brand) come first, newest first
        got, html = rows(**{"items-1-brand": "XXIO", "items-1-model": "", "in_box": ["zeus 2", "mp1400"]})
        self.assertEqual(got[:2], [("Zeus 2", "box"), ("MP1400", "box")])
        self.assertNotIn(("MP1400", "saved"), got)
        got, html = rows(**{"items-0-brand": "XXIO", "items-0-model": "mp1410"})
        self.assertIn('data-pick="MP1400">MP1400</button>', html)                          # did you mean
        self.assertIn("Saved as <b>XXIO MP1410</b> <span class=\"item-tag\">new model</span>", html)

    def test_suggestions_permissions(self):
        self.client.force_login(self.myanmar)                                       # order lines use it too
        self.assertEqual(self.client.get(reverse("item_suggest"), {"brand": "XXIO"}).status_code, 200)
        self.client.force_login(User.objects.create_user("viewer", password="pw-12345-x"))
        self.assertEqual(self.client.get(reverse("item_suggest"), {"brand": "XXIO"}).status_code, 403)

    def test_cards_share_the_brand_and_model_fields(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        for name in ("bag-brand", "bag-model", "items-0-brand", "items-0-model"):
            self.assertIn(f'name="{name}"', html)
        self.assertNotIn("item_name", html)
        self.assertEqual(html.count("data-combo-toggle"), 2)                       # bag + first item card
        self.assertIn('hx-get="/items/suggest/"', html)
        self.assertIn('hx-include="closest [data-item-field]"', html)
        page_form = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('name="brand"', page_form)
        self.assertIn("data-combo-toggle", page_form)

    def test_migration_converts_existing_names(self):
        import importlib
        from django.apps import apps
        convert = importlib.import_module("tracker.migrations.0011_brands_and_models").seed_and_convert
        a = JapanItem.objects.create(box=self.box, item_name="titleist  tsr-2", brand="", model="")
        b = JapanItem.objects.create(box=self.box, item_name="Golf balls", brand="", model="")
        convert(apps, None)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.brand, a.model, a.item_name), ("Titleist", "TSR-2", "Titleist TSR-2"))
        self.assertEqual((b.brand, b.model, b.item_name), ("", "", "Golf balls"))          # no brand: unchanged

    def test_migration_maps_old_brand_names(self):
        import importlib
        from django.apps import apps
        from .models import Brand, BrandModel, OrderLine
        mig = importlib.import_module("tracker.migrations.0022_brand_list_and_order_line_cards")
        Brand.objects.filter(display_name="PING").update(display_name="Ping", match_key="ping")
        Brand.objects.filter(display_name="Cleveland Golf").update(display_name="Cleveland", match_key="cleveland")
        Brand.objects.filter(display_name="PXG").update(display_name="Parsons Xtreme Golf", match_key="parsonsxtremegolf")
        Brand.objects.filter(display_name="Honma").update(active=True)
        g430 = BrandModel.objects.create(brand=Brand.objects.get(display_name="Ping"), display_name="G430")
        items = [JapanItem.objects.create(box=self.box, brand=b, model=m, item_name=f"{b} {m}".strip())
                 for b, m in (("Ping", "G430"), ("Cleveland", "RTX 6"), ("Wilson", "Staff Model"),
                              ("Parsons Xtreme Golf", "0311"), ("Honma", "TW757"), ("", "Golf Balls"))]
        order = make_order("Ko Aung", ("XXIO MP1400", 1, 1000))                    # typed before brands existed
        mig.brands_and_names(apps, None)
        got = [(i.brand, i.model, i.item_name) for i in JapanItem.objects.filter(pk__in=[i.pk for i in items]).order_by("id")]
        self.assertEqual(got, [("PING", "G430", "PING G430"), ("Cleveland Golf", "RTX 6", "Cleveland Golf RTX 6"),
                               ("Wilson Staff", "Staff Model", "Wilson Staff Staff Model"), ("PXG", "0311", "PXG 0311"),
                               ("", "Honma TW757", "Honma TW757"), ("", "Golf Balls", "Golf Balls")])
        self.assertEqual(sorted(Brand.objects.filter(active=True).values_list("display_name", flat=True), key=str.lower),
                         self.LIST)
        self.assertFalse(Brand.objects.get(display_name="Honma").active)
        g430.refresh_from_db()
        self.assertEqual(g430.brand.display_name, "PING")                          # its models move along
        line = OrderLine.objects.get(order=order)
        self.assertEqual((line.brand, line.model, line.item_name), ("XXIO", "MP1400", "XXIO MP1400"))

    def test_brand_filters(self):
        other = Box.objects.create(cargo=self.cargo, box_no=4)
        self.add("XXIO", "mp1400")
        JapanItem.objects.create(box=other, item_name="Titleist TSR2", brand="Titleist", model="TSR2")
        self.client.force_login(self.owner)
        r = self.client.get(reverse("box_list"), {"brand": "XXIO"})
        self.assertContains(r, "GN-3")
        self.assertNotContains(r, "GN-4")
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn('data-brand-filter="XXIO"', html)
        self.assertIn('data-brand="XXIO"', html)

    def csv(self, text):
        from .csv_import import import_japan_csv
        return import_japan_csv(SimpleUploadedFile("l.csv", text.encode(), content_type="text/csv"))

    def test_csv_brand_must_be_on_the_list(self):
        self.add_bag()
        r = self.csv("box,brand,model\n"
                     "GN-3,xxio,mp 1400\n"
                     "GN-3,ping,g430\n"                         # old spelling accepted
                     "GN-3,Cleveland,RTX 6\n"
                     "GN-3,other,Honma TW757\n"
                     "GN-3,PXG,\n")
        self.assertEqual(r.errors, [])
        got = list(self.box.items.filter(is_bag=False).exclude(pk__in=[self.item1.pk, self.item2.pk])
                   .order_by("id").values_list("brand", "model", "item_name"))
        self.assertEqual(got, [("XXIO", "MP 1400", "XXIO MP 1400"), ("PING", "G430", "PING G430"),
                               ("Cleveland Golf", "RTX 6", "Cleveland Golf RTX 6"), ("", "Honma TW757", "Honma TW757"),
                               ("PXG", "", "PXG")])
        r = self.csv("box,brand,model\nGN-3,Kasco,Zeus 1\nGN-3,,TSR2\n")
        self.assertEqual([row for row, _ in r.errors], [2, 3])
        self.assertIn("Unknown brand 'Kasco' – use one of: Bettinardi, Callaway", r.errors[0][1])
        self.assertIn("brand is required", r.errors[1][1])
        r = self.csv("box,item\nGN-3,xxio mp1400\n")                               # the old one-box column
        self.assertIn("put the brand in the brand column and the model in the model column", r.errors[0][1])


class JapanSenderNoCustomerTests(BaseTest):
    """The Japan sender never sees customers; Owner / Admin keep the Customer field and names."""

    def setUp(self):
        super().setUp()
        from .models import Customer
        Customer.objects.create(name="Ko Aung")
        self.item1.customer = "Ko Aung"
        self.item1.save()
        self.bag = JapanItem.objects.create(box=self.box, is_bag=True, type="Golf bag", item_name="Ping Hoofer",
                                            qty=1, customer="Ma Hnin")

    def page(self, user, url):
        self.client.force_login(user)
        return self.client.get(url, HTTP_HX_REQUEST="true").content.decode()

    def test_box_page_rows_and_autocomplete(self):
        html = self.page(self.japan, reverse("box_detail", args=[self.box.pk]))
        for gone in ("Ko Aung", "Ma Hnin", "customer-chip", "icons/customer"):
            self.assertNotIn(gone, html)
        html = self.page(self.owner, reverse("box_detail", args=[self.box.pk]))
        for shown in ("Ko Aung", "Ma Hnin", "customer-chip", '<datalist id="customer-names"><option value="Ko Aung">'):
            self.assertIn(shown, html)

    def test_forms_have_no_customer_field_for_japan(self):
        for url in (reverse("box_create"), reverse("item_add", args=[self.box.pk]), reverse("box_bag", args=[self.box.pk]),
                    reverse("box_item_card") + "?items-TOTAL_FORMS=3"):
            html = self.page(self.japan, url)
            self.assertNotIn("customer", html.replace("customer-names", ""), url)   # no field, label or names
            self.assertNotIn("Ko Aung", html, url)
            self.assertIn("customer", self.page(self.owner, url), url)

    def test_posted_customer_is_ignored_and_links_stay(self):
        from .models import Customer
        r = self.htmx(self.japan, reverse("item_add", args=[self.box.pk]),
                      {"item_name": "TSR3", "qty": "1", "customer": "Sneaky"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(JapanItem.objects.latest("id").customer, "")
        self.assertFalse(Customer.objects.filter(name="Sneaky").exists())
        # editing the bag keeps its existing customer link
        self.htmx(self.japan, reverse("box_bag", args=[self.box.pk]),
                  {"bag-item_name": "Ping Hoofer Lite", "bag-customer": "Other"})
        self.bag.refresh_from_db()
        self.assertEqual((self.bag.item_name, self.bag.customer), ("Ping Hoofer Lite", "Ma Hnin"))
        # the owner can still set it
        self.htmx(self.owner, reverse("item_add", args=[self.box.pk]), {"item_name": "TSR4", "qty": "1", "customer": "Ko Aung"})
        self.assertEqual(JapanItem.objects.latest("id").customer, "Ko Aung")

    def test_copy_output_hides_customer(self):
        r = self.htmx(self.japan, reverse("item_copy", args=[self.item1.pk]))
        self.assertNotContains(r, "Ko Aung")
        self.assertEqual(JapanItem.objects.latest("id").customer, "Ko Aung")    # the link itself is unchanged
        r = self.htmx(self.owner, reverse("item_copy", args=[self.item1.pk]))
        self.assertContains(r, "Ko Aung")

    def test_customer_pages_and_csv_refuse_japan(self):
        from .csv_import import template_csv
        self.client.force_login(self.japan)
        for url in (reverse("customer_list"), reverse("customer_by_name") + "?name=Ko+Aung", reverse("csv_template")):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertNotIn("customer", template_csv(customers=False))
        self.assertNotIn("Ko Aung", template_csv(customers=False))
        self.client.force_login(self.owner)
        self.assertIn("customer", self.client.get(reverse("csv_template")).content.decode().splitlines()[0])

    def test_csv_import_without_customers_ignores_column(self):
        from .csv_import import import_japan_csv
        f = SimpleUploadedFile("l.csv", brand_columns("box,item,qty,customer\nGN-3,Titleist TSR2,1,Ko Aung\n").encode())
        result = import_japan_csv(f, customers=False)
        self.assertEqual(result.errors, [])
        self.assertEqual(JapanItem.objects.latest("id").customer, "")


class PiecePriceTests(BaseTest):
    """Different prices for the same item: one price per piece, line total = their sum."""

    def setUp(self):
        super().setUp()
        self.add_bag()

    def add(self, user=None, **data):
        return self.htmx(user or self.japan, reverse("item_add", args=[self.box.pk]),
                         {"item_name": "Mizuno JPX", "qty": "2", **data})

    def test_different_prices_sum(self):
        self.add(different_prices="on", piece_price=["45000", "41,500"])
        item = JapanItem.objects.latest("id")
        self.assertEqual((item.piece_prices, item.price_jpy, item.line_total, item.line_total_jpy),
                         ([45000, 41500], 45000, 86500, 86500))
        self.assertEqual(self.box.money()["items"], 86500)
        from .views import with_money
        self.assertEqual(with_money(Box.objects.filter(pk=self.box.pk)).get().money_total, 86500)
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn("¥45,000 + ¥41,500 = ¥86,500", html)
        # the Myanmar checker never sees amounts
        self.ship()
        self.client.force_login(self.myanmar)
        html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        for gone in ("45,000", "86,500", "piece_price", "Different prices"):
            self.assertNotIn(gone, html)

    def test_single_price_is_default_and_untick_ignores_pieces(self):
        self.add(price_jpy="12,000", piece_price=["45000", "41500"])   # not ticked: one price per piece
        item = JapanItem.objects.latest("id")
        self.assertEqual((item.piece_prices, item.line_total_jpy), ([], 24000))
        self.add(qty="1", price_jpy="5000", different_prices="on", piece_price=["45000"])  # qty 1: one price
        item = JapanItem.objects.latest("id")
        self.assertEqual((item.piece_prices, item.price_jpy, item.line_total_jpy), ([], 5000, 5000))

    def test_piece_count_must_follow_qty(self):
        r = self.add(qty="3", different_prices="on", piece_price=["45000", "41500"])
        self.assertContains(r, "Enter a price for every piece (3).")
        self.assertContains(r, 'value="41500"')                     # typed prices are kept
        self.assertFalse(JapanItem.objects.filter(item_name="Mizuno JPX").exists())
        r = self.add(qty="2", different_prices="on", piece_price=["45000", "abc"])
        self.assertContains(r, "Piece 2: whole yen only, e.g. 12500.")
        # qty changed later (e.g. in /admin/): the pieces no longer fit, so back to one price
        self.add(different_prices="on", piece_price=["45000", "41500"])
        item = JapanItem.objects.latest("id")
        item.qty = 3
        item.save()
        self.assertEqual((item.piece_prices, item.price_jpy, item.line_total_jpy), ([], 45000, 135000))

    def test_card_markup(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('data-diff-prices', html)
        self.assertRegex(html, r'data-diff-toggle\s+hidden')       # qty 1: no checkbox yet
        self.assertIn('data-piece-name="piece_price"', html)

    def test_add_box_modal_and_copy(self):
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-12", "notes": "", "bag-item_name": "Ping Hoofer",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000", "items-0-item_name": "Pro V1", "items-0-qty": "3",
                "items-0-different_prices": "on", "items-0-piece_price": ["1000", "1200", "1500"]}
        r = self.htmx(self.japan, reverse("box_create"), data)
        self.assertEqual(r.status_code, 204)
        item = Box.objects.get(code="OCT26-GN-12").items.get(is_bag=False)
        self.assertEqual((item.piece_prices, item.line_total_jpy), ([1000, 1200, 1500], 3700))
        r = self.htmx(self.japan, reverse("item_copy", args=[item.pk]))
        self.assertContains(r, "¥1,000 + ¥1,200 + ¥1,500 = ¥3,700")
        copy = JapanItem.objects.latest("id")
        self.assertEqual((copy.piece_prices, copy.line_total_jpy), ([1000, 1200, 1500], 3700))

    def csv(self, text):
        from .csv_import import import_japan_csv
        return import_japan_csv(SimpleUploadedFile("l.csv", brand_columns(text).encode()))

    def test_csv_piece_prices(self):
        result = self.csv('box,item,qty,amount_jpy\nGN-3,Titleist TSR2,2,"45000;41,500"\nGN-3,Ping G430,2,9000\n')
        self.assertEqual(result.errors, [])
        tsr, ping = JapanItem.objects.order_by("-id")[1], JapanItem.objects.latest("id")
        self.assertEqual((tsr.piece_prices, tsr.price_jpy, tsr.line_total_jpy), ([45000, 41500], 45000, 86500))
        self.assertEqual((ping.piece_prices, ping.line_total_jpy), ([], 18000))
        result = self.csv("box,item,qty,amount_jpy\nGN-3,Titleist TSR2,3,45000;41500\n")
        self.assertEqual(result.errors, [(2, "amount_jpy has 2 piece price(s) ('45000;41500') but qty is 3 – "
                                             "give one price per piece, separated by ;")])
        result = self.csv("box,item,qty,amount_jpy\nGN-3,Titleist TSR2,2,45000;\n")
        self.assertIn("every piece needs a price", result.errors[0][1])
        result = self.csv("box,item,qty,amount_jpy\nGN-3,Titleist TSR2,2,45000;x\n")
        self.assertIn("amount_jpy: whole yen only, e.g. 12500", result.errors[0][1])

    def test_migration_fills_line_totals(self):
        import importlib
        from django.apps import apps
        JapanItem.objects.filter(pk=self.item2.pk).update(price_jpy=4200, line_total_jpy=None)
        importlib.import_module("tracker.migrations.0012_piece_prices").fill_line_totals(apps, None)
        self.item2.refresh_from_db()
        self.assertEqual(self.item2.line_total_jpy, 12600)              # 4200 × 3


class DisplaySpellingTests(BaseTest):
    """Brand in its own spelling, model codes in capitals, names with no known brand in Title Case."""

    def test_style_rules(self):
        from .brands import style_name
        for typed, shown in (("mp 1400", "MP 1400"), ("qi10", "QI10"), ("tsr 2", "TSR 2"), ("jpx 923", "JPX 923"),
                             ("golf bag", "Golf Bag"), ("pro v1", "Pro V1"), ("phantom 5", "Phantom 5"),
                             ("TourEdge exs", "TourEdge Exs")):
            self.assertEqual(style_name(typed), shown)
        from .models import model_display
        self.assertEqual(model_display("XXIO", "mp1400"), "MP1400")
        self.assertEqual(model_display("", "titleist golf bag"), "Titleist Golf Bag")

    def test_migration_restyles_saved_names(self):
        import importlib
        from django.apps import apps
        from .models import Brand, BrandModel
        xxio = Brand.objects.get(display_name="XXIO")
        m = BrandModel.objects.create(brand=xxio, display_name="mp1400")
        a = JapanItem.objects.create(box=self.box, item_name="XXIO mp1400", brand="XXIO", model="mp1400", qty=1)
        b = JapanItem.objects.create(box=self.box, item_name="titleist golf bag", qty=1)
        c = JapanItem.objects.create(box=self.box, item_name="TaylorMade qi10", brand="TaylorMade", model="qi10", qty=1)
        importlib.import_module("tracker.migrations.0013_name_display_spelling").restyle(apps, None)
        for o in (m, a, b, c):
            o.refresh_from_db()
        self.assertEqual(m.display_name, "MP1400")
        self.assertEqual((a.item_name, a.model), ("XXIO MP1400", "MP1400"))
        self.assertEqual(b.item_name, "Titleist Golf Bag")
        self.assertEqual((c.item_name, c.model), ("TaylorMade QI10", "QI10"))


class ClubCategoryValueTests(BaseTest):
    """Club option stays optional, but a chosen category needs one of its values (or text, for Other)."""

    def setUp(self):
        super().setUp()
        from .models import ClubCategory, ClubOption
        self.add_bag()
        self.cat = {c.name: c for c in ClubCategory.objects.all()}
        self.graphite = ClubOption.objects.get(category__name="Iron", label="Graphite")
        self.client.force_login(self.japan)

    def add(self, **data):
        return self.htmx(self.japan, reverse("item_add", args=[self.box.pk]), {"qty": "1", **data})

    def test_iron_graphite_renamed(self):
        o = self.graphite
        self.assertEqual((o.label, o.type, o.shaft_material, o.club_number, o.full_label),
                         ("Graphite", "Iron", "graphite", "", "Iron · Graphite"))
        html = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertRegex(html, r'data-club-label="Iron · Graphite"\s+aria-pressed="false">Graphite</button>')
        self.assertNotIn("Graphite 5", html)

    def test_migration_clears_the_old_number(self):
        import importlib
        from django.apps import apps
        old = JapanItem.objects.create(box=self.box, type="Iron", shaft_material="graphite", club_number="5", qty=1)
        steel = JapanItem.objects.create(box=self.box, type="Iron", shaft_material="steel", club_number="5", qty=1)
        importlib.import_module("tracker.migrations.0014_iron_graphite").rename(apps, None)
        old.refresh_from_db(); steel.refresh_from_db()
        self.assertEqual((old.club_number, old.club_label), ("", "Iron · Graphite"))
        self.assertEqual(steel.club_number, "5")                                   # other items untouched

    def test_category_without_value_is_rejected(self):
        r = self.add(item_name="Ping G430", club_category=str(self.cat["Driver"].pk))
        self.assertContains(r, "Choose a Driver option.")
        self.assertRegex(r.content.decode(), r'data-club-cat="%d"[^>]*aria-pressed="true"' % self.cat["Driver"].pk)
        self.assertFalse(JapanItem.objects.filter(item_name="Ping G430").exists())
        self.assertContains(self.add(item_name="X", club_category=str(self.cat["Iron"].pk)), "Choose an Iron option.")
        r = self.add(item_name="X", club_option="other", club_other=" ", club_category=str(self.cat["Other"].pk))
        self.assertFalse(JapanItem.objects.filter(item_name="X").exists())
        self.assertContains(r, "data-club-warn")
        # with a value it saves; with the category cleared (nothing tapped) there's no club option at all
        self.add(item_name="Ping G430", club_category=str(self.cat["Iron"].pk), club_option=str(self.graphite.pk))
        self.assertEqual(JapanItem.objects.get(item_name="Ping G430").club_label, "Iron · Graphite")
        self.add(item_name="Head Cover", club_category="", club_option="")
        self.assertEqual(JapanItem.objects.get(item_name="Head Cover").type, "")

    def test_add_box_modal_formset(self):
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-12", "notes": "", "bag-item_name": "Ping Hoofer",
                "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000", "items-0-item_name": "Pro V1", "items-0-qty": "1",
                "items-1-item_name": "Srixon ZX5", "items-1-qty": "1", "items-1-club_category": str(self.cat["Wedge"].pk)}
        r = self.htmx(self.japan, reverse("box_create"), data)
        self.assertContains(r, "Choose a Wedge option.")
        self.assertFalse(Box.objects.filter(code="OCT26-GN-12").exists())

    def test_card_markup(self):
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('name="items-0-club_category"', html)
        self.assertIn("data-club-warn", html)

    def csv(self, rows):
        from .csv_import import import_japan_csv
        return import_japan_csv(SimpleUploadedFile("l.csv", brand_columns(rows).encode()))

    def test_csv_old_and_new_spelling_and_category_alone(self):
        result = self.csv("box,item,club_option\nGN-3,A,Iron Graphite\nGN-3,B,Iron · Graphite 5\nGN-3,C,iron graphite\n")
        self.assertEqual(result.errors, [])
        self.assertEqual({i.club_label for i in JapanItem.objects.filter(item_name__in=["A", "B", "C"])}, {"Iron · Graphite"})
        result = self.csv("box,item,club_option\nGN-3,D,Driver\n")
        self.assertEqual(result.errors, [(2, "club_option 'Driver': Choose a Driver option – give its value too, "
                                             "e.g. 'Driver 9°'")])
        result = self.csv("box,item,type\nGN-3,E,Driver\n")                        # older type column alone
        self.assertIn("Choose a Driver option", result.errors[0][1])
        self.assertFalse(JapanItem.objects.filter(item_name__in=["D", "E"]).exists())

    def test_admin(self):
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        data = {"box": self.box.pk, "type": "Driver", "item_name": "New", "qty": 1, "condition": "new",
                "quantity_unit": "pcs", "piece_prices": "[]",
                "receive_check-TOTAL_FORMS": "0", "receive_check-INITIAL_FORMS": "0",
                "receive_check-MIN_NUM_FORMS": "0", "receive_check-MAX_NUM_FORMS": "1"}
        r = self.client.post("/admin/tracker/japanitem/add/", data)
        self.assertContains(r, "Choose a Driver option.")
        self.client.post("/admin/tracker/japanitem/add/", {**data, "loft": "10.5"})
        self.assertTrue(JapanItem.objects.filter(item_name="New", loft="10.5").exists())


class CustomerIconSidebarTests(BaseTest):
    """The customer icon is the sidebar's "Customers" menu icon – never next to customer names."""
    ICON = "/static/img/display/icons/customer.webp"

    def setUp(self):
        super().setUp()
        from .models import Customer
        self.customer = Customer.objects.create(name="Ko Aung")
        self.item1.customer = "Ko Aung"
        self.item1.save()
        self.bag = JapanItem.objects.create(box=self.box, is_bag=True, type="Golf bag", item_name="Ping Hoofer",
                                            qty=1, customer="Ko Aung")
        self.req = CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2", qty=1)
        self.client.force_login(self.owner)

    def test_sidebar_customers_icon_like_the_other_menu_icons(self):
        import re
        html = self.client.get(reverse("customer_list")).content.decode()
        link = re.search(r'<a href="/customers/" class="sidebar-link active">(.*?)</a>', html, re.S)
        self.assertIsNotNone(link)                                                  # active on the customer pages
        self.assertIn(f'<img src="{self.ICON}', link.group(1))
        self.assertIn('class="h-6 w-6 object-contain"', link.group(1))              # same as Shipments / Cargo history
        self.assertIn("this.nextElementSibling.hidden=false;this.remove()", link.group(1))   # missing file: line icon
        self.assertTrue(link.group(1).rstrip().endswith("Customers"))
        shipments = re.search(r'<a href="/boxes/" class="sidebar-link ">(.*?)</a>', html, re.S).group(1)
        self.assertEqual(re.sub(r"[\w-]+\.webp|Shipments|<path d=\"[^\"]*\"", "", shipments),
                         re.sub(r"[\w-]+\.webp|Customers|<path d=\"[^\"]*\"", "", link.group(1)))
        box = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertRegex(box, r'<a href="/customers/" class="sidebar-link ">')    # not active elsewhere
        self.client.force_login(self.japan)                                         # no Customers menu for Japan
        self.assertNotIn(self.ICON, self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode())

    def test_no_icon_next_to_customer_names(self):
        pages = [reverse("box_detail", args=[self.box.pk]), reverse("customer_list"),
                 reverse("customer_detail", args=[self.customer.pk])]
        for url in pages:
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count(self.ICON), 1, url)                         # the sidebar's only
            self.assertNotIn('alt="Customer"', html, url)
        partials = [reverse("order_edit", args=[self.req.pk]), reverse("box_create"),
                    reverse("item_add", args=[self.box.pk])]
        for url in partials:
            html = self.client.get(url, HTTP_HX_REQUEST="true").content.decode()
            self.assertNotIn("customer.webp", html, url)
        listing = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn('<span class="crm-avatar"', listing)                         # initials circles kept
        self.assertIn(">KA</span>", listing)

    def test_no_person_emoji_left_in_templates(self):
        from pathlib import Path
        from django.conf import settings
        for path in Path(settings.BASE_DIR, "templates").rglob("*.html"):
            text = path.read_text()
            self.assertNotIn("👤", text, path)
            self.assertNotIn("🙋", text, path)

    def test_display_webp_exists(self):
        from django.contrib.staticfiles import finders
        self.assertTrue(finders.find("img/display/icons/customer.webp"))
        self.assertIn('customer.webp', Path_text("scripts/make_display_images.py"))


def Path_text(rel):
    from pathlib import Path
    from django.conf import settings
    return Path(settings.BASE_DIR, rel).read_text()


class WholeYenTests(BaseTest):
    """Amounts are whole yen only: digits (with optional 12,500 / ¥12,500), never decimals or negatives."""

    def test_yen_field(self):
        from django import forms as djforms
        from .forms import YenField
        f = YenField(label="x")
        for ok in ("12500", "12,500", "¥12,500", " ¥ 12,500 ", "0"):
            self.assertIn(f.clean(ok), (12500, 0), ok)
        self.assertIsNone(f.clean(""))
        for bad in ("12500.5", "12500.0", "-500", "abc", "1e3", "12.500"):
            with self.assertRaisesMessage(djforms.ValidationError, "Whole yen only, e.g. 12500."):
                f.clean(bad)

    def test_forms_reject_decimals_and_negatives(self):
        self.add_bag()
        r = self.htmx(self.japan, reverse("item_add", args=[self.box.pk]),
                      {"item_name": "Ping G430", "qty": "1", "price_jpy": "12500.5"})
        self.assertContains(r, "Whole yen only, e.g. 12500.")
        self.assertFalse(JapanItem.objects.filter(item_name="Ping G430").exists())
        r = self.htmx(self.japan, reverse("item_add", args=[self.box.pk]),
                      {"item_name": "Ping G430", "qty": "2", "different_prices": "on", "piece_price": ["45,000", "-41500"]})
        self.assertContains(r, "Piece 2: whole yen only, e.g. 12500.")
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-12", "notes": "", "cargo_fee_jpy": "-500",
                "bag-item_name": "Ping Hoofer", "bag-price_jpy": "1.5",
                "items-TOTAL_FORMS": "0", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000"}
        r = self.htmx(self.japan, reverse("box_create"), data)
        self.assertEqual(r.content.decode().count("Whole yen only, e.g. 12500."), 2)    # cargo fee + bag amount
        self.assertFalse(Box.objects.filter(code="OCT26-GN-12").exists())
        r = self.htmx(self.japan, reverse("box_create"), {**data, "cargo_fee_jpy": "¥12,500", "bag-price_jpy": "8,000"})
        box = Box.objects.get(code="OCT26-GN-12")
        self.assertEqual((box.cargo_fee_jpy, box.items.get(is_bag=True).price_jpy), (12500, 8000))

    def test_inputs_are_numeric(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        for name in ("cargo_fee_jpy", "bag-price_jpy", "items-0-price_jpy"):
            self.assertRegex(html, rf'name="{name}"[^>]*inputmode="numeric"[^>]*pattern="\[0-9\]\*"')
        from django.template.loader import render_to_string
        piece = render_to_string("tracker/_piece_price.html", {"n": 1, "value": "", "name": "piece_price"})
        self.assertIn('inputmode="numeric" pattern="[0-9]*"', piece)

    def test_model_piece_prices_must_be_whole_yen(self):
        from django.core.exceptions import ValidationError
        item = JapanItem(box=self.box, item_name="X", qty=2, piece_prices=[45000.5, 100])
        with self.assertRaisesMessage(ValidationError, "Whole yen only"):
            item.full_clean()
        item.piece_prices = [45000, -1]
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_csv(self):
        from .csv_import import import_japan_csv
        self.add_bag()
        def csv(text):
            return import_japan_csv(SimpleUploadedFile("l.csv", brand_columns(text).encode()))
        r = csv('box,item,amount_jpy\nGN-3,A,12500\nGN-3,B,"12,500"\nGN-3,C,"¥12,500"\n')
        self.assertEqual(r.errors, [])
        self.assertEqual(set(JapanItem.objects.filter(item_name__in="ABC").values_list("price_jpy", flat=True)), {12500})
        for bad in ("12500.5", "-500"):
            r = csv(f"box,item,amount_jpy\nGN-3,D,{bad}\n")
            self.assertEqual(r.errors, [(2, f"amount_jpy: whole yen only, e.g. 12500 (12,500 and ¥12,500 work too) – got '{bad}'")])
        self.assertFalse(JapanItem.objects.filter(item_name="D").exists())


class SimpleOrderFlowTests(BaseTest):
    """Customer orders: no attaching to box items; one status set by hand (Ordered → Arrived → Delivered, Cancelled)."""

    def setUp(self):
        super().setUp()
        from .models import Customer
        self.customer = Customer.objects.create(name="Ko Aung")
        self.order = make_order("Ko Aung", ("XXIO MP1400", 2, 150000), note="black grip")      # total 300,000

    def test_status_migration(self):
        import importlib
        from django.apps import apps
        arrived_box = Box.objects.create(cargo=self.cargo, box_no=8, status=Box.Status.ARRIVED)
        checked_box = Box.objects.create(cargo=self.cargo, box_no=9, status=Box.Status.CHECKED)
        self.ship()
        made = {}
        for key, old, box in (("open", "open", None), ("ordered", "ordered", None), ("in_arrived", "in_box", arrived_box),
                              ("in_checked", "in_box", checked_box), ("in_shipped", "in_box", self.box),
                              ("fulfilled", "fulfilled", None), ("cancelled", "cancelled", None)):
            o = CustomerRequest.objects.create(customer="X", item_name=key, box=box)
            CustomerRequest.objects.filter(pk=o.pk).update(status=old)
            made[key] = o
        importlib.import_module("tracker.migrations.0017_simple_order_status").simplify(apps, None)
        got = {k: CustomerRequest.objects.get(pk=o.pk).status for k, o in made.items()}
        self.assertEqual(got, {"open": "ordered", "ordered": "ordered", "in_arrived": "arrived", "in_checked": "arrived",
                               "in_shipped": "ordered", "fulfilled": "delivered", "cancelled": "cancelled"})
        self.assertEqual(CustomerRequest.objects.get(pk=made["in_shipped"].pk).box, self.box)   # the link is kept

    def test_attach_is_gone(self):
        from django.urls import NoReverseMatch
        for name in ("request_attach", "request_attach_items", "request_detach"):
            with self.assertRaises(NoReverseMatch):
                reverse(name, args=[self.order.pk]) if name != "request_attach_items" else reverse(name)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f"/requests/{self.order.pk}/attach/").status_code, 404)
        self.order.japan_item, self.order.box = self.item1, self.box        # an old link: kept but not shown
        self.order.save()
        page = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        for gone in ("Attach to box item", "📦", "detach", "Request:"):
            self.assertNotIn(gone, page)
        box_page = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertNotIn("Request:", box_page)

    def test_card_shows_the_simple_order(self):
        self.client.force_login(self.myanmar)
        page = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        card = page[page.index(f'id="request-{self.order.pk}"'):]
        card = card[:card.index('</section>')]
        for shown in ("Ko Aung", "XXIO MP1400", "×2", "black grip", 'name="status"', ">Edit</button>",
                      "300,000 Ks", "Add payment"):
            self.assertIn(shown, card)
        self.assertIn("status-select pill-neutral", card)                   # Ordered: sage
        self.assertEqual([o for o in ("Ordered", "Arrived", "Delivered", "Cancelled") if f">{o}</option>" in card],
                         ["Ordered", "Arrived", "Delivered", "Cancelled"])
        r = self.htmx(self.myanmar, reverse("request_status", args=[self.order.pk]), {"status": "arrived"})
        self.assertContains(r, "status-select pill-info")                   # Arrived: teal
        r = self.htmx(self.myanmar, reverse("request_status", args=[self.order.pk]), {"status": "delivered"})
        self.assertContains(r, "status-select pill-ok")
        r = self.htmx(self.myanmar, reverse("request_status", args=[self.order.pk]), {"status": "cancelled"})
        self.assertContains(r, "status-select pill-grey")

    def test_edit_order(self):
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("order_edit", args=[self.order.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'value="XXIO MP1400"')
        line = self.order.lines.get()
        r = self.htmx(self.myanmar, reverse("order_edit", args=[self.order.pk]),
                      order_data((("XXIO", "mp1500"), 3, "150000", "", line.pk), ("Titleist Bag", 1, "500,000"), existing=1))
        self.assertContains(r, "XXIO MP1500")
        self.order.refresh_from_db()
        self.assertEqual((self.order.item_name, self.order.qty, self.order.status),
                         ("XXIO MP1500 ×3, Titleist Bag", 4, "ordered"))
        self.assertEqual(list(self.order.lines.values_list("item_name", "qty", "price_ks")),
                         [("XXIO MP1500", 3, 150000), ("Titleist Bag", 1, 500000)])
        self.htmx(self.myanmar, reverse("order_edit", args=[self.order.pk]),         # remove a line
                  order_data(("XXIO MP1500", 3, "150000", "", line.pk),
                             ("Titleist Bag", 1, "500000", "", self.order.lines.last().pk, True), existing=2))
        self.assertEqual(self.order.lines.count(), 1)
        r = self.client.get(reverse("request_status", args=[self.order.pk]), HTTP_HX_REQUEST="true")   # Cancel
        self.assertContains(r, f'id="request-{self.order.pk}"')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ordered")

    def test_delivered_warning_and_cancelled_orders_owe_nothing(self):
        from .models import add_order_money, customer_money
        self.client.force_login(self.myanmar)
        page = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn('data-balance="300000" data-balance-text="300,000 Ks" data-was="ordered"', page)
        self.order.status = "cancelled"
        self.order.save()
        orders = add_order_money(self.customer.requests())
        money = customer_money(orders)
        self.assertEqual((money["total"], money["balance"], money["status"][0]), (0, 0, "clear"))

    def test_items_packed_section_uses_the_customer_name_on_items(self):
        self.item1.customer = "ko aung"
        self.item1.save()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn("Items packed for Ko Aung", page)
        self.assertIn("TSR2", page[page.index("Items packed for Ko Aung"):])


class OrderPaymentTests(BaseTest):
    """Per-order payments in whole Kyat: orders have item lines, payments belong to one order, no credit or splits."""

    def setUp(self):
        super().setUp()
        from .models import Customer, PaymentMethod
        self.customer = Customer.objects.create(name="Ko Aung")
        self.order = make_order("Ko Aung", ("XXIO MP1400", 2, 150000), ("Titleist Bag", 1, 500000))   # 800,000
        self.kpay = PaymentMethod.objects.get(name="KPay")

    def pay(self, amount, order=None, user=None, **extra):
        return self.htmx(user or self.myanmar, reverse("order_payment_add", args=[(order or self.order).pk]),
                         {"date": "2026-10-17", "amount_ks": amount, "method": self.kpay.pk, **extra})

    def money(self, order=None):
        from .models import add_order_money
        o = add_order_money([CustomerRequest.objects.get(pk=(order or self.order).pk)])[0]
        return o.total_ks, o.paid_ks, o.balance_ks, o.pay_status[0]

    def test_methods_seeded(self):
        from .models import PaymentMethod
        self.assertEqual(list(PaymentMethod.objects.values_list("name", flat=True)),
                         ["Cash", "KPay", "KBZ Special", "AYA Pay", "AYA Special", "CB Special"])

    def test_total_from_lines_and_new_order_with_several_items(self):
        self.assertEqual(self.money(), (800000, 0, 800000, "unpaid"))
        self.client.force_login(self.myanmar)
        r = self.client.post(reverse("customer_new_order", args=[self.customer.pk]),
                             order_data(("ping g430", 1, "450,000"), ("pro v1", 3, "20000"), note="for Sunday"))
        self.assertEqual(r.status_code, 302)
        o = CustomerRequest.objects.get(note="for Sunday")
        self.assertEqual(list(o.lines.values_list("item_name", "qty", "price_ks")),
                         [("Ping G430", 1, 450000), ("Pro V1", 3, 20000)])
        self.assertEqual(self.money(o), (510000, 0, 510000, "unpaid"))
        unpriced = make_order("Ko Aung", ("Old Putter", 1, None))
        self.assertEqual(self.money(unpriced), (0, 0, 0, "no_price"))
        html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn("data-add-line", html)                                   # "+ Add item"
        self.assertIn('data-line-template', html)
        self.assertIn("Price not set", html)

    def test_deposit_20_percent(self):
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("order_payment_add", args=[self.order.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'data-deposit="160000">Deposit 20% · 160,000 Ks')
        odd = make_order("Ko Aung", ("Grip", 1, 333333))
        r = self.client.get(reverse("order_payment_add", args=[odd.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'data-deposit="66667"')                        # rounded to whole Kyat
        self.pay("160,000")
        self.assertEqual(self.money(), (800000, 160000, 640000, "deposit"))

    def test_overpay_and_unpriced_rejected(self):
        from .models import Payment
        r = self.pay("900000")
        self.assertContains(r, "More than this order still owes (800,000 Ks).")
        self.assertEqual(r["HX-Retarget"], f"#pay-slot-{self.order.pk}")
        self.pay("800000")
        self.assertContains(self.pay("1"), "More than this order still owes (0 Ks).")
        unpriced = make_order("Ko Aung", ("Old Putter", 1, None))
        self.assertContains(self.pay("1000", order=unpriced), "Set the price of every item first.")
        self.assertContains(self.pay("12.5"), "Whole Kyat only, e.g. 450000.")
        self.assertEqual(Payment.objects.count(), 1)

    def test_statuses_and_card(self):
        self.assertEqual(self.money()[3], "unpaid")
        r = self.pay("300000")
        self.assertContains(r, 'data-pay-status="deposit">Deposit paid')
        self.assertContains(r, "Owes 500,000 Ks")
        self.assertContains(r, "300,000 Ks")                                   # in the payment list
        r = self.pay("500000")
        self.assertContains(r, 'data-pay-status="paid">Fully paid')
        self.assertNotContains(r, "Add payment")
        self.assertEqual(self.money(), (800000, 800000, 0, "paid"))

    def test_customer_balance_sum_table_and_page(self):
        from .models import Customer
        second = make_order("Ko Aung", ("Ping G430", 1, 450000))
        self.pay("300000")
        Customer.objects.create(name="Ma Hnin")
        make_order("Ma Hnin", ("TSR2", 1, 100000))
        self.htmx(self.myanmar, reverse("order_payment_add", args=[CustomerRequest.objects.get(customer="Ma Hnin").pk]),
                  {"date": "2026-10-17", "amount_ks": "100000", "method": self.kpay.pk})
        self.client.force_login(self.owner)
        html = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn('data-money-status="owes">Owes 950,000 Ks', html)          # 500,000 + 450,000
        self.assertIn('data-money-status="clear">All clear', html)
        self.assertNotIn("Paid (Ks)", html)
        names = lambda q: __import__("re").findall(r'class="crm-link" title="([^"]+)"', self.client.get(reverse("customer_list") + q).content.decode())
        self.assertEqual(names("?money=owes"), ["Ko Aung"])
        self.assertEqual(names("?money=clear"), ["Ma Hnin"])
        self.assertEqual(names("?sort=balance&dir=desc"), ["Ko Aung", "Ma Hnin"])
        page = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        for value in ("1,250,000 Ks", "300,000 Ks", "950,000 Ks"):
            self.assertIn(value, page[page.index('id="money"'):page.index('id="orders"')])
        self.assertIn(f'data-order-money="{second.pk}"', page)

    def test_old_orders_marked_paid(self):
        old = make_order("Ko Aung", ("Old Putter", 1, None))
        self.pay("800000")
        from .models import add_order_money, customer_money
        self.assertEqual(customer_money(add_order_money(self.customer.requests()))["status"][0], "no_price")
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.post(reverse("customer_settle", args=[self.customer.pk]),
                                          {"orders": [old.pk]}).status_code, 403)
        self.client.force_login(self.owner)
        self.client.post(reverse("customer_settle", args=[self.customer.pk]), {"orders": [old.pk]})
        self.assertEqual(self.money(old)[2:], (0, "old"))
        self.assertEqual(customer_money(add_order_money(self.customer.requests()))["status"][:2], ("clear", "All clear"))
        # the /admin/ action does the same
        other = make_order("Ko Aung", ("Old Bag", 1, 90000))
        self.owner.is_staff = True
        self.owner.save()
        r = self.client.post("/admin/tracker/customerrequest/", {"action": "mark_paid_old_order",
                                                                 "_selected_action": [other.pk]}, follow=True)
        self.assertContains(r, "Marked 1 order(s) as paid (old order).")
        self.assertEqual(self.money(other)[3], "old")

    def test_delivered_warning_and_reminder(self):
        from .models import add_order_money, reminder_text
        self.pay("160000")
        self.client.force_login(self.myanmar)
        html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn('data-balance="640000" data-balance-text="640,000 Ks"', html)
        CustomerRequest.objects.filter(pk=self.order.pk).update(order_date=datetime.date(2026, 10, 3))
        o = add_order_money([CustomerRequest.objects.get(pk=self.order.pk)])[0]
        n = self.order.pk
        self.assertEqual(reminder_text(o),
                         f"Ko Aung ရှင့် အော်ဒါ #{n} (3 Oct 2026) – XXIO MP1400 ×2, Titleist Bag ×1\n"
                         "စုစုပေါင်း 800,000 Ks၊ ပေးပြီး 160,000 Ks၊ ကျန်ငွေ 640,000 Ks ပါ။\n"
                         f"Order #{n} (3 Oct 2026): XXIO MP1400 ×2, Titleist Bag ×1 / Total: 800,000 Ks, Paid: 160,000 Ks, "
                         "Balance due: 640,000 Ks.")
        self.assertIn("data-copy-reminder=", html)

    def test_permissions(self):
        from .models import Payment
        self.assertEqual(self.pay("1000", user=self.japan).status_code, 403)
        self.client.force_login(self.japan)
        for url in (reverse("customer_detail", args=[self.customer.pk]), reverse("order_payment_add", args=[self.order.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertFalse(Payment.objects.exists())
        self.pay("1000")                                                       # Myanmar checker may add
        p = Payment.objects.get()
        self.assertEqual(p.recorded_by, self.myanmar)
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.post(reverse("payment_delete", args=[p.pk])).status_code, 403)
        self.assertNotIn(reverse("payment_delete", args=[p.pk]),
                         self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode())
        self.client.force_login(self.japan)
        self.assertEqual(self.client.post(reverse("payment_delete", args=[p.pk])).status_code, 403)
        r = self.htmx(self.owner, reverse("payment_delete", args=[p.pk]))
        self.assertContains(r, f'id="request-{self.order.pk}"')
        self.assertFalse(Payment.objects.exists())

    def test_admin_payment_edit_cannot_overpay(self):
        self.pay("700000")
        from .models import Payment
        p = Payment.objects.get()
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        data = {"order": self.order.pk, "date": "2026-10-17", "amount_ks": "900000", "method": self.kpay.pk, "note": ""}
        r = self.client.post(f"/admin/tracker/payment/{p.pk}/change/", data)
        self.assertContains(r, "This order only owes 800,000 Ks.")
        self.client.post(f"/admin/tracker/payment/{p.pk}/change/", {**data, "amount_ks": "800000"})
        p.refresh_from_db()
        self.assertEqual(p.amount_ks, 800000)

    def test_no_proof_photo(self):
        from django.urls import NoReverseMatch
        from .forms import OrderPaymentForm
        from .models import Payment
        self.assertNotIn("proof", [f.name for f in Payment._meta.get_fields()])
        self.assertNotIn("proof", OrderPaymentForm.base_fields)
        with self.assertRaises(NoReverseMatch):
            reverse("payment_proof", args=[1])
        self.client.force_login(self.myanmar)
        form = self.client.get(reverse("order_payment_add", args=[self.order.pk])).content.decode()
        self.assertNotIn('type="file"', form)
        self.assertNotIn("Proof", form)

    def test_unpriced_order_shows_dashes_and_is_never_fully_paid(self):
        import re
        from .models import OrderLine, Payment
        o = make_order("Ko Aung", ("Old Putter", 1, None))
        self.assertEqual(self.money(o), (0, 0, 0, "no_price"))
        self.client.force_login(self.myanmar)

        def card(order):
            html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
            block = html[html.index(f'id="request-{order.pk}"'):]
            block = block[:block.index("data-pay-slot")]
            return re.findall(r"<dt>(\w+)</dt><dd>([^<]*)</dd>", block), block
        self.assertEqual(card(o)[0], [("Total", "—"), ("Paid", "—"), ("Balance", "—")])
        Payment.objects.create(order=o, date="2026-10-01", amount_ks=50000, method=self.kpay)   # paid, then price removed
        dl, block = card(o)
        self.assertEqual(dl, [("Total", "—"), ("Paid", "50,000 Ks"), ("Balance", "—")])
        self.assertIn("Price not set", block)
        self.assertNotIn("Fully paid", block)
        self.assertEqual(self.money(o)[3], "no_price")
        OrderLine.objects.filter(order=o).update(price_ks=50000)               # priced: now it can be fully paid
        self.assertEqual(self.money(o), (50000, 50000, 0, "paid"))

    def test_payment_rows_date_first_newest_first_with_method_pill(self):
        from .models import PaymentMethod
        aya = PaymentMethod.objects.get(name="AYA Pay")
        self.pay("100000", date="2026-10-17", method=aya.pk, note="slip 42")
        self.pay("50000", date="2026-10-02")
        self.client.force_login(self.myanmar)
        html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        card = html[html.index(f'id="request-{self.order.pk}"'):]
        self.assertIn('<span class="pay-main"><span data-pay-date>17 Oct 2026</span><span data-pay-amount-text>100,000 Ks</span>'
                      '<span class="pm pm-red" data-method-pill="red">AYA Pay</span>', card)
        self.assertIn("recorded by myanmar · slip 42", card)
        self.assertLess(card.index("17 Oct 2026"), card.index("2 Oct 2026"))  # newest first
        history = html[html.index("data-pay-history"):]
        self.assertIn(f"Order #{self.order.pk}</a> · recorded by myanmar · slip 42", history)
        self.assertLess(history.index("17 Oct 2026"), history.index("2 Oct 2026"))
        self.assertIn('class="pm pm-blue" data-method-pill="blue">KPay', history)

    def test_method_colours_stored_on_the_method(self):
        from .models import PaymentMethod
        self.assertEqual(dict(PaymentMethod.objects.values_list("name", "color")),
                         {"Cash": "green", "KPay": "blue", "KBZ Special": "navy", "AYA Pay": "red",
                          "AYA Special": "maroon", "CB Special": "teal"})
        wave = PaymentMethod.objects.create(name="Wave Pay", sort_order=9)
        self.assertEqual(wave.color, "grey")                                   # admin-added: grey until chosen
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        self.client.post(f"/admin/tracker/paymentmethod/{wave.pk}/change/",
                         {"name": "Wave Pay", "color": "teal", "sort_order": 9, "active": "on"})
        wave.refresh_from_db()
        self.assertEqual(wave.color, "teal")
        self.client.force_login(self.myanmar)
        form = self.client.get(reverse("order_payment_add", args=[self.order.pk])).content.decode()
        self.assertIn('type="radio" name="method"', form)
        for cls, name in (("green", "Cash"), ("navy", "KBZ Special"), ("maroon", "AYA Special"), ("teal", "Wave Pay")):
            self.assertIn(f'class="pm pm-{cls}" data-method-pill="{cls}">{name}</span>', form)

    def test_carried_over_note_cleared_payment_stays_on_its_order(self):
        import importlib
        from django.apps import apps
        from .models import Payment
        mig = importlib.import_module("tracker.migrations.0021_payment_colours_no_proof")
        other = make_order("Ko Aung", ("Bag", 1, 300000))
        p = Payment.objects.create(order=self.order, date="2026-10-01", amount_ks=200000, method=self.kpay,
                                   note=mig.CARRIED_NOTE)
        keep = Payment.objects.create(order=other, date="2026-10-01", amount_ks=1000, method=self.kpay, note="cash slip")
        self.assertEqual(mig.clear_carried_note(apps, None), 1)
        p.refresh_from_db(), keep.refresh_from_db()
        self.assertEqual((p.order_id, p.amount_ks, p.note), (self.order.pk, 200000, ""))
        self.assertEqual((keep.order_id, keep.note), (other.pk, "cash slip"))


class OrderDateTests(BaseTest):
    """Order date: default today (Myanmar), editable, no future dates; card, month groups, last update, filter."""

    def setUp(self):
        super().setUp()
        from .models import Customer
        self.customer = Customer.objects.create(name="Ko Aung")
        self.client.force_login(self.myanmar)

    def new_order(self, day=None):
        data = order_data(("XXIO MP1400", 1, "100000"))
        if day is not None:
            data["order_date"] = day
        return self.client.post(reverse("customer_new_order", args=[self.customer.pk]), data)

    def test_default_today_in_yangon(self):
        from unittest import mock
        from django.utils import timezone
        from .models import yangon_today
        # 20:00 UTC on 17 Oct is already 18 Oct (02:30) in Yangon
        with mock.patch.object(timezone, "now", return_value=datetime.datetime(2026, 10, 17, 20, 0, tzinfo=datetime.timezone.utc)):
            self.assertEqual(yangon_today(), datetime.date(2026, 10, 18))
            self.assertEqual(CustomerRequest.objects.create(customer="Ko Aung", item_name="X").order_date,
                             datetime.date(2026, 10, 18))
        html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn(f'name="order_date" value="{yangon_today().isoformat()}"', html)
        self.assertIn(f'max="{yangon_today().isoformat()}"', html)

    def test_past_allowed_future_rejected(self):
        from .models import yangon_today
        self.assertEqual(self.new_order("2026-01-05").status_code, 302)
        self.assertEqual(CustomerRequest.objects.get().order_date, datetime.date(2026, 1, 5))
        future = (yangon_today() + datetime.timedelta(days=1)).isoformat()
        r = self.new_order(future)
        self.assertContains(r, "The order date can&#x27;t be in the future.")
        self.assertEqual(CustomerRequest.objects.count(), 1)
        self.new_order()                                                   # left empty -> today
        self.assertEqual(CustomerRequest.objects.order_by("-pk")[0].order_date, yangon_today())

    def test_edit_changes_date_and_rejects_future(self):
        from .models import yangon_today
        o = make_order("Ko Aung", ("XXIO MP1400", 1, 100000))
        url = reverse("order_edit", args=[o.pk])
        data = order_data(("XXIO MP1400", 1, "100000", "", o.lines.get().pk), existing=1)
        r = self.client.post(url, {**data, "order_date": (yangon_today() + datetime.timedelta(days=3)).isoformat()})
        self.assertContains(r, "can&#x27;t be in the future")
        self.client.post(url, {**data, "order_date": "2025-12-31"})
        o.refresh_from_db()
        self.assertEqual(o.order_date, datetime.date(2025, 12, 31))
        self.assertContains(self.client.get(reverse("customer_detail", args=[self.customer.pk])),
                            f"Order #{o.pk} · 31 Dec 2025")

    def test_customer_page_newest_first_grouped_by_month(self):
        for day in ("2026-09-02", "2026-10-05", "2026-09-20"):
            o = make_order("Ko Aung", ("Bag " + day, 1, 1000))
            CustomerRequest.objects.filter(pk=o.pk).update(order_date=day)
        html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertLess(html.index("October 2026"), html.index("September 2026"))
        self.assertLess(html.index("Bag 2026-10-05"), html.index("Bag 2026-09-20"))
        self.assertLess(html.index("Bag 2026-09-20"), html.index("Bag 2026-09-02"))
        self.assertIn("September 2026 <span", html)
        self.assertIn("· 2 orders", html)

    def test_last_update_is_latest_order_date_and_range_filter(self):
        import re
        from .models import Customer
        Customer.objects.create(name="Ma Hnin")
        for name, day in (("Ko Aung", "2026-03-01"), ("Ko Aung", "2026-05-10"), ("Ma Hnin", "2026-08-15")):
            o = CustomerRequest.objects.create(customer=name, item_name="Bag")
            CustomerRequest.objects.filter(pk=o.pk).update(order_date=day)

        def names(**params):
            html = self.client.get(reverse("customer_list"), params).content.decode()
            return re.findall(r'class="crm-link" title="([^"]+)"', html), html
        found, html = names(sort="updated", dir="desc")
        self.assertEqual(found, ["Ma Hnin", "Ko Aung"])
        self.assertIn('title="Latest order 10 May 2026">10 May 2026<', html)
        self.assertEqual(names(**{"from": "2026-04-01", "to": "2026-06-30"})[0], ["Ko Aung"])
        self.assertEqual(names(**{"from": "2026-08-01"})[0], ["Ma Hnin"])
        self.assertEqual(names(to="2026-03-01")[0], ["Ko Aung"])
        self.assertEqual(names(**{"from": "2027-01-01"})[0], [])
        found, html = names(**{"from": "bad"})                             # ignored
        self.assertEqual(found, ["Ko Aung", "Ma Hnin"])

    def test_migration_uses_created_date_in_yangon(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module("tracker.migrations.0020_customerrequest_order_date")
        o = CustomerRequest.objects.create(customer="Ko Aung", item_name="Old")
        CustomerRequest.objects.filter(pk=o.pk).update(
            created_at=datetime.datetime(2025, 6, 30, 18, 0, tzinfo=datetime.timezone.utc), order_date="2026-01-01")
        mig.from_created(apps, None)
        o.refresh_from_db()
        self.assertEqual(o.order_date, datetime.date(2025, 7, 1))           # 00:30 on 1 Jul in Yangon


class JapanNeverSetsMyanmarStatusTests(BaseTest):
    """Arrived / Checked come only from the Myanmar side; the Japan sender sets Packed / Shipped only."""

    def edit_data(self, box, **extra):
        return {"cargo": box.cargo_id, "box_no": box.box_no, "status": box.status, "notes": box.notes, **extra}

    def csv(self, text, **kwargs):
        from .csv_import import import_japan_csv
        return import_japan_csv(SimpleUploadedFile("l.csv", brand_columns(text).encode(), content_type="text/csv"), **kwargs)

    def test_edit_page_offers_packed_and_shipped_only(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_edit", args=[self.box.pk])).content.decode()
        select = html[html.index('name="status"'):]
        select = select[:select.index("</select>")]
        self.assertIn('value="packed"', select)
        self.assertIn('value="shipped"', select)
        self.assertNotIn('value="arrived"', select)
        self.assertNotIn('value="checked"', select)
        self.assertIn("Arrived and Checked come only from the Myanmar check", html)

    def test_direct_post_of_arrived_or_checked_is_rejected(self):
        self.client.force_login(self.japan)
        for status in ("arrived", "checked"):
            r = self.client.post(reverse("box_edit", args=[self.box.pk]), self.edit_data(self.box, status=status))
            self.assertEqual(r.status_code, 200)                       # form shown again with the error
            self.assertContains(r, "valid choice")
            self.box.refresh_from_db()
            self.assertEqual(self.box.status, Box.Status.PACKED)
        self.client.post(reverse("box_edit", args=[self.box.pk]), self.edit_data(self.box, status="shipped"))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.SHIPPED)

    def test_form_rule_itself_rejects_myanmar_statuses_for_japan(self):
        from .forms import BoxForm
        form = BoxForm(self.edit_data(self.box, status="arrived"), instance=self.box, user=self.japan)
        form.fields["status"].choices = Box.Status.choices             # even if the choices were tampered with
        self.assertFalse(form.is_valid())
        self.assertIn("Arrived and Checked are set by the Myanmar check, not from Japan.", form.errors["status"])
        self.assertTrue(roles.can_set_box_status(self.japan, "shipped"))
        self.assertFalse(roles.can_set_box_status(self.japan, "checked"))
        self.assertTrue(roles.can_set_box_status(self.myanmar, "checked"))
        self.assertTrue(roles.can_set_box_status(self.owner, "arrived"))

    def test_add_box_ignores_a_posted_status(self):
        data = {"cargo": self.cargo.pk, "box_id": "OCT26-GN-9", "status": "arrived", "bag-item_name": "Stand Bag",
                "items-TOTAL_FORMS": "0", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000"}
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn('name="status"', html)
        self.htmx(self.japan, reverse("box_create"), data)
        self.assertEqual(Box.objects.get(code="OCT26-GN-9").status, Box.Status.PACKED)

    def test_japan_cannot_touch_an_arrived_or_checked_box(self):
        self.add_bag()
        for status in ("arrived", "checked"):
            Box.objects.filter(pk=self.box.pk).update(status=status)
            self.client.force_login(self.japan)
            self.assertEqual(self.client.get(reverse("box_edit", args=[self.box.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("box_edit", args=[self.box.pk]),
                                              self.edit_data(self.box, status="shipped")).status_code, 403)
            self.client.post(reverse("box_mark_shipped", args=[self.box.pk]))
            self.client.post(reverse("item_delete", args=[self.item1.pk]))
            self.box.refresh_from_db()
            self.assertEqual(self.box.status, status)
            self.assertTrue(JapanItem.objects.filter(pk=self.item1.pk).exists())
            html = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
            self.assertIn(f">{status.title()}<", html)                      # still sees it, read-only
            self.assertNotIn("Mark as shipped", html)
            self.assertIn(f">{status.title()}<", self.client.get(reverse("box_list")).content.decode())

    def test_csv_japan_side_rejects_arrived_and_checked(self):
        r = self.csv("box,kind,item,status\nOCT26-GN-7,bag,Stand Bag,arrived\nOCT26-GN-7,,Qi10,Checked\n",
                     myanmar_status=False)
        self.assertEqual([row for row, _ in r.errors], [2, 3])
        self.assertIn("Arrived and Checked are set by the Myanmar check", r.errors[0][1])
        self.assertFalse(Box.objects.filter(code="OCT26-GN-7").exists())
        r = self.csv("box,kind,item,status\nOCT26-GN-7,bag,Stand Bag,shipped\n", myanmar_status=False)
        self.assertEqual(r.errors, [])
        self.assertEqual(Box.objects.get(code="OCT26-GN-7").status, Box.Status.SHIPPED)

    def test_csv_japan_side_cannot_change_a_box_myanmar_started(self):
        self.box.set_code("OCT26-GN-3")
        self.box.status = Box.Status.ARRIVED
        self.box.save()
        r = self.csv("box,kind,item\nOCT26-GN-3,,Extra wedge\n", myanmar_status=False)
        self.assertIn("the Myanmar check has started", r.errors[0][1])
        self.assertEqual(self.box.items.count(), 2)

    def test_csv_page_is_owner_admin_only_and_owner_may_set_any_status(self):
        f = SimpleUploadedFile("l.csv", brand_columns("box,kind,item,status\nOCT26-GN-8,bag,Stand Bag,arrived\n").encode(), content_type="text/csv")
        self.client.force_login(self.japan)
        self.assertEqual(self.client.post(reverse("csv_import"), {"file": f}).status_code, 403)
        f.seek(0)
        self.client.force_login(self.owner)
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 0 item(s) and 1 golf bag(s)")
        self.assertEqual(Box.objects.get(code="OCT26-GN-8").status, Box.Status.ARRIVED)
        r = self.csv("box,kind,item,status\nOCT26-GN-9,bag,Stand Bag,lost\n")
        self.assertIn("status must be packed, shipped, arrived or checked", r.errors[0][1])

    def test_owner_keeps_all_four_statuses(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("box_edit", args=[self.box.pk])).content.decode()
        for value in Box.Status.values:
            self.assertIn(f'value="{value}"', html)
        self.client.post(reverse("box_edit", args=[self.box.pk]), self.edit_data(self.box, status="checked"))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.CHECKED)

    def test_myanmar_actions_set_arrived_and_checked(self):
        bag = self.add_bag()
        Box.objects.filter(pk=self.box.pk).update(status="shipped", japan_ship_date=datetime.date(2026, 1, 5))
        self.client.force_login(self.myanmar)
        self.client.post(reverse("box_start_check", args=[self.box.pk]))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ARRIVED)
        self.assertIsNotNone(self.box.myanmar_received_date)
        for item in (bag, self.item1, self.item2):
            self.htmx(self.myanmar, reverse("item_check", args=[item.pk]), {"result": "matched"})
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.CHECKED)
        self.htmx(self.myanmar, reverse("item_check_clear", args=[self.item2.pk]))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ARRIVED)

    def test_shipments_filter_keeps_all_four_for_viewing(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_list")).content.decode()
        for label in ("Packed", "Shipped", "Arrived", "Checked"):
            self.assertIn(f">{label}</option>", html)


class SharedItemCardTests(BaseTest):
    """Customer order lines use the SAME item card as the Add box modal (partials/item_card.html + ItemCardMixin);
    only the money differs: JP¥ on box items, Myanmar Kyat on order lines."""

    def setUp(self):
        super().setUp()
        from .models import ClubOption, Customer
        self.customer = Customer.objects.create(name="Ko Aung")
        self.driver = ClubOption.objects.get(category__name="Driver", label="10.5")

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()

    def test_one_template_and_form_for_both(self):
        from .forms import ItemCardMixin, JapanItemForm, OrderLineForm
        self.assertTrue(issubclass(JapanItemForm, ItemCardMixin) and issubclass(OrderLineForm, ItemCardMixin))
        lines = open("templates/tracker/partials/order_lines.html").read()
        self.assertIn('include "tracker/partials/item_card.html"', lines)
        self.assertFalse(__import__("os").path.exists("templates/tracker/partials/order_line.html"))

    def test_box_card_in_yen_order_card_in_kyat(self):
        self.client.force_login(self.owner)
        box = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('data-currency="¥"', box)
        self.assertIn('Amount (JP¥)', box)
        self.assertIn('<span class="yen-sign" aria-hidden="true">¥</span><input type="text" name="items-0-price_jpy"', box)
        order = self.page(self.myanmar)
        form = order[order.index('id="new-order"'):order.index("</form>", order.index('id="new-order"'))]
        self.assertIn('data-currency="Ks"', form)
        self.assertIn("Price (Ks)", form)
        self.assertRegex(form, r'name="lines-0-price_ks"[^>]*data-ks[^>]*>\s*<span class="ks-unit" aria-hidden="true">Ks</span>|'
                               r'name="lines-0-price_ks"[^>]*>\s*<span class="ks-unit"')
        for shared in ('name="lines-0-brand"', 'name="lines-0-model"', 'name="lines-0-club_option"', "data-picker",
                       'name="lines-0-flex"', 'name="lines-0-qty"', 'name="lines-0-note"', "data-diff-prices",
                       "Different prices", "copyItemCard(this)", "deleteItemCard(this)", "data-add-line"):
            self.assertIn(shared, form)
        for japan_only in ("¥", "price_jpy", "bag-", "cargo_fee", "customer-names", 'name="lines-0-customer"'):
            self.assertNotIn(japan_only, form)

    def test_order_totals_with_piece_prices_and_display(self):
        data = order_data((("XXIO", "mp1400"), 1, "1,400,000", self.driver.pk), (("Titleist", "TSR2"), 2, ""))
        data.update({"lines-0-flex": "S", "lines-1-different_prices": "on",
                     "lines-1-piece_price": ["450000", "420,000"], "lines-1-note": "with cover"})
        self.client.force_login(self.myanmar)
        r = self.client.post(reverse("customer_new_order", args=[self.customer.pk]), data)
        self.assertEqual(r.status_code, 302)
        order = CustomerRequest.objects.get()
        a, b = order.lines.all()
        self.assertEqual((a.title, a.line_total), ("XXIO MP1400 · Driver 10.5° · S", 1400000))
        self.assertEqual((b.piece_prices, b.price_ks, b.line_total, b.note), ([450000, 420000], 450000, 870000, "with cover"))
        from .models import add_order_money
        o = add_order_money([order])[0]
        self.assertEqual((o.total_ks, o.balance_ks, o.deposit_ks), (2270000, 2270000, 454000))
        html = self.page(self.myanmar)
        self.assertIn("<b>XXIO MP1400 · Driver 10.5° · S</b> ×1 — <b class=\"tabular-nums\">1,400,000 Ks</b>", html)
        self.assertIn("<b>Titleist TSR2</b> ×2 — <b class=\"tabular-nums\">870,000 Ks</b>", html)
        self.assertIn("(450,000 + 420,000 Ks)", html)
        self.assertIn("Deposit 20% · 454,000 Ks", self.client.get(reverse("order_payment_add", args=[order.pk])).content.decode())
        r = self.client.get(reverse("order_edit", args=[order.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('name="lines-1-piece_price" value="450,000"', r)          # edit shows the same card

    def test_club_option_value_required_once_a_category_is_chosen(self):
        from .models import ClubCategory
        driver_cat = ClubCategory.objects.get(name="Driver")
        data = order_data((("PING", "G430"), 1, "100000"))
        data["lines-0-club_category"] = str(driver_cat.pk)
        self.client.force_login(self.myanmar)
        r = self.client.post(reverse("customer_new_order", args=[self.customer.pk]), data)
        self.assertContains(r, "Choose a Driver option.")
        self.assertFalse(CustomerRequest.objects.exists())

    def test_japan_never_sees_orders_or_kyat(self):
        make_order("Ko Aung", ("XXIO MP1400", 1, 1400000))
        self.client.force_login(self.japan)
        self.assertEqual(self.client.get(reverse("customer_detail", args=[self.customer.pk])).status_code, 403)
        box = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn("Ks", box.replace("Kasco", ""))
        self.assertNotIn("1,400,000", self.client.get(reverse("box_list")).content.decode())

    def test_edit_shows_saved_club_option_and_can_clear_it(self):
        self.client.force_login(self.myanmar)
        self.client.post(reverse("customer_new_order", args=[self.customer.pk]),
                         order_data((("XXIO", "MP1400"), 1, "1400000", self.driver.pk)))
        order = CustomerRequest.objects.get()
        line = order.lines.get()
        html = self.client.get(reverse("order_edit", args=[order.pk]), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn(f'name="lines-0-club_option" value="{self.driver.pk}"', html)
        self.assertIn("✓ Driver 10.5°", html)
        self.assertIn('value="1,400,000"', html)                                    # saved price with commas
        data = order_data((("XXIO", "MP1400"), 1, "1400000", self.driver.pk, line.pk), existing=1)
        self.htmx(self.myanmar, reverse("order_edit", args=[order.pk]), data)
        line.refresh_from_db()
        self.assertEqual(line.club_label, "Driver 10.5°")                           # kept when saved again
        data["lines-0-club_option"] = ""
        self.htmx(self.myanmar, reverse("order_edit", args=[order.pk]), data)
        line.refresh_from_db()
        self.assertEqual((line.club_label, line.title), ("", "XXIO MP1400"))       # cleared


class JapanBannerTests(BaseTest):
    """The Japan sender (by group, never by username) gets a Burmese thank-you banner with the girl and boy pictures;
    the Myanmar checker's banner is chosen by group too."""

    LINES = ["အမြဲအလုပ်ကြိုးစားပေးတဲ့ အစ်ကို့ကို AGOM Business Partner အနေနဲ့ အထူး ကျေးဇူးတင်လျက်ပါ။",
             "သာယာရွှင်လန်းသောနေ့ရက်လေး ဖြစ်ပါစေကြောင်း ဆုမွန်ကောင်းတောင်းပါတယ်…"]

    def dashboard(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("dashboard")).content.decode()

    def test_japan_sender_sees_the_banner_text_exactly(self):
        import re
        html = self.dashboard(self.japan)
        self.assertIn('class="hero hero-jp"', html)
        self.assertNotIn("hero-mm", html)
        text = html[html.index('<div class="hero-text mm-text jp-text" lang="my">'):]
        text = text[:text.index("</div>")]
        paras = [re.sub(r"<[^>]+>", "", p).strip() for p in re.findall(r"<p>(.*?)</p>", text, re.S)]
        self.assertEqual(paras, self.LINES)                # exactly these two lines
        self.assertIn('<span class="ph">AGOM</span> <span class="ph">Business</span>', text)   # wraps between phrases
        self.assertIn("family=Noto+Sans+Myanmar", html)
        girl, text_at, boy = html.index('data-person="girl"'), html.index("jp-text"), html.index('data-person="boy"')
        self.assertLess(girl, text_at)                     # girl – text – boy
        self.assertLess(text_at, boy)

    def test_pictures_use_display_copies_with_empty_alt(self):
        from unittest import mock
        from . import views
        with mock.patch.object(views.finders, "find", return_value="/x"):
            html = self.dashboard(self.japan)
        for name in ("girl", "boy"):
            self.assertIn(f'<source srcset="/static/img/display/stickers/{name}.webp', html)
            self.assertRegex(html, rf'<img src="/static/img/display/stickers/{name}\.png[^"]*" alt=""')
        with mock.patch.object(views.finders, "find", return_value=None):      # not made yet: no broken image
            html = self.dashboard(self.japan)
        self.assertNotIn("stickers/girl", html)
        self.assertIn('data-person="girl"', html)

    def test_chosen_by_group_not_username(self):
        from django.contrib.auth.models import Group
        renamed = User.objects.create_user("tanaka", password="pw-12345-x")       # any username in the group
        renamed.groups.add(Group.objects.get(name=roles.JAPAN))
        self.assertIn("hero-jp", self.dashboard(renamed))
        named_japan = User.objects.create_user("japan2", password="pw-12345-x")   # a "japan"-like name, no group
        self.assertNotIn("hero-jp", self.dashboard(named_japan))
        mm = User.objects.create_user("thaw", password="pw-12345-x")
        mm.groups.add(Group.objects.get(name=roles.MYANMAR))
        self.assertIn("hero-mm", self.dashboard(mm))
        User.objects.filter(pk=self.myanmar.pk).update(username="someone-else")    # renaming keeps the banner
        self.myanmar.refresh_from_db()
        self.assertIn("hero-mm", self.dashboard(self.myanmar))

    def test_others_do_not_see_it(self):
        for user in (self.owner, self.myanmar):
            self.assertNotIn("hero-jp", self.dashboard(user))
        self.assertIn("hero-mm", self.dashboard(self.myanmar))                    # unchanged

    def test_display_script_makes_the_copies(self):
        script = open("scripts/make_display_images.py").read()
        self.assertIn('PEOPLE = ["girl", "boy"]', script)
        self.assertIn('/ "stickers" / f"{name}.webp"', script)
        self.assertIn('/ "stickers" / f"{name}.png"', script)


class SidebarCopyrightTests(BaseTest):
    def test_copyright_above_user_card_for_every_role(self):
        for user in (self.owner, self.japan, self.myanmar):
            self.client.force_login(user)
            html = self.client.get(reverse("dashboard")).content.decode()
            with self.subTest(user=user.username):
                self.assertEqual(html.count("© AGOM 2026"), 1)                 # one sidebar: desktop and phone menu
                self.assertIn('text-center text-xs text-primary-hover" data-copyright>© AGOM 2026</p>', html)
                self.assertLess(html.index("data-copyright"), html.index('aria-label="Log out"'))
                self.assertGreater(html.index("data-copyright"), html.index("</nav>"))


class UserTimeZoneTests(BaseTest):
    """Dates and times are SHOWN in each user's zone; business rules keep their fixed zones."""

    # 31 Oct 2026 15:30 UTC: Tokyo is already 1 Nov (00:30), Yangon still 31 Oct (22:00), Bangkok 31 Oct (22:30)
    MOMENT = datetime.datetime(2026, 10, 31, 15, 30, tzinfo=datetime.timezone.utc)

    def test_default_zone_by_group_and_override(self):
        from django.contrib.auth.models import Group
        from .models import UserProfile, user_time_zone
        admin_group = User.objects.create_user("ad", password="pw-12345-x")
        admin_group.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        superuser = User.objects.create_superuser("root2", password="pw-12345-x")
        nobody = User.objects.create_user("nobody", password="pw-12345-x")
        for user, zone in ((self.japan, "Asia/Tokyo"), (self.myanmar, "Asia/Yangon"), (self.owner, "Asia/Bangkok"),
                           (admin_group, "Asia/Bangkok"), (superuser, "Asia/Bangkok"), (nobody, "Asia/Yangon")):
            with self.subTest(user=user.username):
                self.assertEqual(user_time_zone(User.objects.get(pk=user.pk)), zone)
        UserProfile.objects.create(user=self.japan, time_zone="Asia/Singapore")          # chosen in /admin/
        self.assertEqual(user_time_zone(User.objects.get(pk=self.japan.pk)), "Asia/Singapore")

    def test_middleware_activates_the_users_zone(self):
        from django.test import RequestFactory
        from django.utils import timezone
        from .middleware import UserTimeZoneMiddleware
        seen = []
        mw = UserTimeZoneMiddleware(lambda r: seen.append(timezone.get_current_timezone_name()) or "ok")
        for user, zone in ((self.japan, "Asia/Tokyo"), (self.myanmar, "Asia/Yangon"), (self.owner, "Asia/Bangkok")):
            request = RequestFactory().get("/")
            request.user = User.objects.get(pk=user.pk)
            mw(request)
            self.assertEqual(seen[-1], zone)
        self.assertEqual(timezone.get_current_timezone_name(), "Asia/Yangon")              # reset after the request

    def test_times_shown_in_own_zone_and_zone_in_user_card(self):
        from django.contrib.auth.models import Group
        from django.template import Context, Template
        from django.utils import timezone
        for user, label in ((self.japan, ">Japan Sender</span>"), (self.myanmar, ">Myanmar Checker</span>"),
                            (self.owner, ">Owner</span>")):
            self.client.force_login(user)
            html = self.client.get(reverse("dashboard")).content.decode()
            self.assertIn(label, html)
        self.client.force_login(self.japan)
        self.assertIn('title="Dates and times are shown in this time zone">· JST</span>',
                      self.client.get(reverse("dashboard")).content.decode())
        self.client.force_login(self.myanmar)
        self.assertIn(">· MMT</span>", self.client.get(reverse("dashboard")).content.decode())
        render = Template('{{ t|date:"j M H:i" }}').render
        with timezone.override("Asia/Tokyo"):
            self.assertEqual(render(Context({"t": self.MOMENT})), "1 Nov 00:30")
        with timezone.override("Asia/Bangkok"):
            self.assertEqual(render(Context({"t": self.MOMENT})), "31 Oct 22:30")

    def test_business_rules_keep_their_zones(self):
        from unittest import mock
        from django.utils import timezone
        from .forms import BoxForm, CustomerOrderForm
        from .models import Box, CustomerRequest, box_period, site_today, yangon_today
        with mock.patch.object(timezone, "now", return_value=self.MOMENT):
            for viewer in ("Asia/Tokyo", "Asia/Bangkok", "Asia/Yangon", "UTC"):
                with self.subTest(viewer=viewer), timezone.override(viewer):
                    self.assertEqual(box_period()[0], "NOV26")                                 # Box IDs: Japan time
                    self.assertEqual(Box.next_code(self.cargo)[0][:5], "NOV26")
                    self.assertEqual(yangon_today(), datetime.date(2026, 10, 31))             # order dates: Myanmar
                    self.assertEqual(CustomerRequest(customer="x", item_name="y").order_date, datetime.date(2026, 10, 31))
                    self.assertEqual(site_today(), datetime.date(2026, 10, 31))               # date rules: site zone
                    form = CustomerOrderForm()
                    self.assertEqual(form.fields["order_date"].widget.attrs["max"], "2026-10-31")
                    self.assertEqual(BoxForm(instance=self.box).fields["japan_ship_date"].widget.attrs["min"], "2026-10-31")
            # end to end: a Japan sender (Tokyo) and an owner (Bangkok) get the same business dates
            self.ship()
            self.client.force_login(self.myanmar)
            self.client.post(reverse("box_start_check", args=[self.box.pk]))
            self.box.refresh_from_db()
            self.assertEqual(self.box.myanmar_received_date, datetime.date(2026, 10, 31))
            from .models import Customer
            ko = Customer.objects.create(name="Ko Aung")
            self.client.force_login(self.owner)
            self.client.post(reverse("customer_new_order", args=[ko.pk]), order_data((("XXIO", "MP1400"), 1, "1000")))
            self.assertEqual(CustomerRequest.objects.get(customer="Ko Aung").order_date, datetime.date(2026, 10, 31))
            self.client.force_login(self.japan)
            future = self.client.post(reverse("customer_new_order", args=[ko.pk]), order_data((("XXIO", "MP1400"), 1, "1")))
            self.assertEqual(future.status_code, 403)                                         # (never sees orders)

    def test_setup_roles_creates_four_groups_and_no_users(self):
        from django.contrib.auth.models import Group
        Group.objects.all().delete()
        before = User.objects.count()
        call_command("setup_roles", stdout=io.StringIO())
        self.assertEqual(sorted(Group.objects.values_list("name", flat=True)),
                         ["Admin", "Japan Sender", "Myanmar Checker", "Owner"])
        call_command("setup_roles", stdout=io.StringIO())                                     # safe to re-run
        self.assertEqual(Group.objects.count(), 4)
        self.assertEqual(User.objects.count(), before)
        src = open("tracker/management/commands/setup_roles.py").read()
        self.assertNotIn("password", src.split('"""', 2)[2].lower())


class BackupDbTests(TestCase):
    def test_dated_copy_and_keeps_the_newest_14(self):
        import sqlite3, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agom.sqlite3"
            con = sqlite3.connect(db)
            con.execute("create table t (x)"); con.execute("insert into t values (42)"); con.commit(); con.close()
            folder = Path(tmp) / "backups"
            folder.mkdir()
            for day in range(1, 16):                                         # 15 older backups already there
                (folder / f"agom-2026-09-{day:02d}-000000.sqlite3").write_text("old")
            (folder / "notes.txt").write_text("not a backup")
            out = io.StringIO()
            call_command("backup_db", source=str(db), dir=str(folder), stdout=out)
            backups = sorted(p.name for p in folder.glob("agom-*.sqlite3"))
            self.assertEqual(len(backups), 14)
            self.assertNotIn("agom-2026-09-01-000000.sqlite3", backups)          # oldest removed
            newest = folder / backups[-1]
            self.assertRegex(newest.name, r"^agom-\d{4}-\d{2}-\d{2}-\d{6}\.sqlite3$")
            self.assertEqual(sqlite3.connect(newest).execute("select x from t").fetchone(), (42,))   # a real copy
            self.assertTrue((folder / "notes.txt").exists())
            self.assertIn("Backup saved", out.getvalue())

    def test_env_file_loader(self):
        import os, tempfile
        from pathlib import Path
        from agom.settings import load_env_file
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("# comment\nAGOM_T1=plain\nexport AGOM_T2='quoted value'\nAGOM_T3=\"a=b\"\n\nAGOM_T4=set\n")
            os.environ["AGOM_T4"] = "real"                                     # real environment wins
            try:
                load_env_file(env)
                self.assertEqual([os.environ.get(k) for k in ("AGOM_T1", "AGOM_T2", "AGOM_T3", "AGOM_T4")],
                                 ["plain", "quoted value", "a=b", "real"])
            finally:
                for k in ("AGOM_T1", "AGOM_T2", "AGOM_T3", "AGOM_T4"):
                    os.environ.pop(k, None)
            load_env_file(Path(tmp) / "missing.env")                           # no file: nothing happens


class CsrfFailureTests(BaseTest):
    """A second tap on "Log in" (or any form sent from an out-of-date page) gets a friendly answer, never weaker CSRF."""

    def csrf_client(self):
        from django.test import Client
        return Client(enforce_csrf_checks=True)

    def login_twice(self, nxt=None):
        c = self.csrf_client()
        c.get(reverse("login"))
        old = c.cookies["csrftoken"].value
        data = {"username": "owner", "password": "pw-12345-x", "csrfmiddlewaretoken": old}
        if nxt is not None:
            data["next"] = nxt
        first = c.post(reverse("login"), data)                        # logs in – Django replaces the CSRF token
        self.assertEqual(first.status_code, 302)
        self.assertNotEqual(c.cookies["csrftoken"].value, old)
        c.cookies["csrftoken"] = old                                   # the second tap still carries the old one
        return c, c.post(reverse("login"), data)

    def test_double_tap_login_goes_to_the_dashboard_not_403(self):
        c, second = self.login_twice()
        self.assertRedirects(second, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(c.get(reverse("dashboard")).status_code, 200)            # still logged in

    def test_double_tap_login_keeps_a_safe_next(self):
        _, second = self.login_twice(nxt="/boxes/?status=shipped")
        self.assertRedirects(second, "/boxes/?status=shipped", fetch_redirect_response=False)

    def test_unsafe_next_is_ignored(self):
        for bad in ("https://evil.example/steal", "//evil.example/", "javascript:alert(1)"):
            with self.subTest(next=bad):
                _, second = self.login_twice(nxt=bad)
                self.assertRedirects(second, reverse("dashboard"), fetch_redirect_response=False)

    def test_not_logged_in_stale_token_back_to_login_with_message(self):
        c = self.csrf_client()
        c.get(reverse("login"))
        r = c.post(reverse("login"), {"username": "owner", "password": "pw-12345-x",
                                      "csrfmiddlewaretoken": "x" * 64, "next": "/customers/"})
        self.assertRedirects(r, reverse("login") + "?next=/customers/", fetch_redirect_response=False)
        page = c.get(r["Location"])
        self.assertContains(page, "Please log in again.")
        self.assertContains(page, 'name="next" value="/customers/"')
        self.assertNotIn("_auth_user_id", c.session)                               # nothing was acted on
        r = c.post(reverse("login"), {"username": "owner", "password": "pw-12345-x",
                                      "csrfmiddlewaretoken": "x" * 64, "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)    # unsafe next dropped

    def test_other_pages_out_of_date_page_403_and_nothing_saved(self):
        c = self.csrf_client()
        c.force_login(self.japan)
        c.get(reverse("dashboard"))
        r = c.post(reverse("box_mark_shipped", args=[self.box.pk]), {"csrfmiddlewaretoken": "x" * 64},
                   HTTP_REFERER="http://testserver/boxes/%d/" % self.box.pk)
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "This page was out of date – please try again", status_code=403)
        self.assertContains(r, 'href="http://testserver/boxes/%d/" class="btn btn-primary' % self.box.pk, status_code=403)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.PACKED)                      # CSRF still blocks the change
        r = c.post(reverse("box_mark_shipped", args=[self.box.pk]), {"csrfmiddlewaretoken": "x" * 64},
                   HTTP_REFERER="https://evil.example/")
        self.assertContains(r, 'href="/" class="btn btn-primary', status_code=403)  # unsafe referer: dashboard

    def test_htmx_out_of_date_reloads_the_page(self):
        c = self.csrf_client()
        c.force_login(self.myanmar)
        r = c.post(reverse("item_check", args=[self.item1.pk]), {"result": "matched"},
                   HTTP_HX_REQUEST="true", HTTP_X_CSRFTOKEN="x" * 64)
        self.assertEqual((r.status_code, r["HX-Refresh"]), (403, "true"))
        anon = self.csrf_client()
        r = anon.post(reverse("item_check", args=[self.item1.pk]), {"result": "matched"},
                      HTTP_HX_REQUEST="true", HTTP_X_CSRFTOKEN="x" * 64)
        self.assertEqual((r.status_code, r["HX-Redirect"]), (403, reverse("login")))

    def test_csrf_still_enforced_with_a_good_token(self):
        c = self.csrf_client()
        c.get(reverse("login"))
        r = c.post(reverse("login"), {"username": "owner", "password": "pw-12345-x",
                                      "csrfmiddlewaretoken": c.cookies["csrftoken"].value})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)
        from django.conf import settings
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", settings.MIDDLEWARE)
        self.assertEqual(settings.CSRF_FAILURE_VIEW, "tracker.views.csrf_failure")


class SubmitGuardTests(BaseTest):
    """static/js/submit-guard.js (one tap = one submit) is on every page that has a form."""

    def test_script_on_every_page_with_a_form(self):
        self.client.logout()
        login = self.client.get(reverse("login")).content.decode()
        self.assertIn("/static/js/submit-guard.js", login)
        self.assertIn('data-busy-text="Logging in…">Log in</button>', login)
        from .models import Customer
        ko = Customer.objects.create(name="Ko Aung")
        pages = [("japan", reverse("dashboard")), ("japan", reverse("box_list")), ("japan", reverse("box_detail", args=[self.box.pk])),
                 ("japan", reverse("box_edit", args=[self.box.pk])), ("myanmar", reverse("customer_list")),
                 ("myanmar", reverse("customer_detail", args=[ko.pk])), ("owner", reverse("csv_import")),
                 ("owner", reverse("cargo_history"))]
        for who, url in pages:
            with self.subTest(url=url):
                self.client.force_login(getattr(self, who))
                html = self.client.get(url).content.decode()
                self.assertIn("<form", html)
                self.assertEqual(html.count("/static/js/submit-guard.js"), 1)
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        for url in ("/admin/", "/admin/tracker/box/%d/change/" % self.box.pk):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "/static/js/submit-guard.js")
        self.client.logout()
        self.assertContains(self.client.get("/admin/login/"), "/static/js/submit-guard.js")

    def test_script_rules(self):
        js = open("static/js/submit-guard.js").read()
        for hook in ("addEventListener('submit'", "stopImmediatePropagation", "htmx:beforeRequest", "htmx:afterRequest",
                     "pageshow", "e.persisted", "data-busy-text", "Saving…", "busy-spin", "e.submitter"):
            self.assertIn(hook, js)
        self.assertNotIn("import ", js)                                           # no libraries
