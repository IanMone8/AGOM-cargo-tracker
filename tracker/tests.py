import io

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import roles
from .models import Box, Cargo, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck


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

    def htmx(self, user, url, data=None):
        self.client.force_login(user)
        return self.client.post(url, data or {}, HTTP_HX_REQUEST="true")


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
                           ("box_edit", [self.box.pk]), ("box_create", []), ("request_list", []),
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
    def test_attach_and_detach(self):
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2 driver", flex="S")
        self.client.force_login(self.owner)
        r = self.client.get(reverse("request_attach", args=[req.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(r, "GN-3")  # suggestion
        self.htmx(self.owner, reverse("request_attach", args=[req.pk]), {"japan_item": self.item1.pk})
        req.refresh_from_db()
        self.assertEqual((req.status, req.box, req.japan_item), ("in_box", self.box, self.item1))
        self.htmx(self.owner, reverse("request_detach", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual((req.status, req.box), ("ordered", None))


class CSVImportTests(BaseTest):
    def upload(self, text, **extra):
        self.client.force_login(self.japan)
        f = SimpleUploadedFile("list.csv", text.encode("utf-8-sig"), content_type="text/csv")
        return self.client.post(reverse("csv_import"), {"file": f, **extra})

    def test_import_creates_boxes_and_items(self):
        r = self.upload(
            "box,type,item_name,flex,loft,qty,condition,price_jpy,customer,note\n"
            "GN-3,Putter,Newport 2,,,1,used,\"12,000\",,\n"
            "gn-5,Driver,Qi10,sr,10.5,2,new,,Ma Hnin,\n"
        )
        self.assertContains(r, "Imported 2 item(s) into 2 box(es)")
        new_box = Box.objects.get(cargo=self.cargo, box_no=5)
        item = new_box.items.get()
        self.assertEqual((item.flex, item.qty, item.customer), ("SR", 2, "Ma Hnin"))
        self.assertEqual(self.box.items.get(item_name="Newport 2").price_jpy, 12000)

    def test_import_is_all_or_nothing(self):
        r = self.upload("box,type,item_name,flex,qty\nGN-3,Driver,X,S,1\nZZ-1,Driver,Y,XX,abc\n")
        self.assertContains(r, "Nothing was imported")
        self.assertContains(r, "Unknown cargo code")
        self.assertEqual(JapanItem.objects.count(), 2)

    def test_replace(self):
        self.upload("box,type,item_name\nGN-3,Bag,Stand bag\n", replace="on")
        self.assertEqual(list(self.box.items.values_list("item_name", flat=True)), ["Stand bag"])


class AddBoxModalTests(BaseTest):
    def post_data(self, **items):
        data = {"cargo": self.cargo.pk, "box_no": "9", "notes": "fragile",
                "items-TOTAL_FORMS": "3", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000"}
        data.update(items)
        return data

    def test_modal_opens_over_page(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'role="dialog"')
        self.assertContains(r, "Items inside")
        self.assertContains(r, 'name="items-0-shaft_material"')
        self.assertNotContains(r, "<html")  # fragment, not a full page

    def test_add_item_card(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_item_card"), {"items-TOTAL_FORMS": "2"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, 'name="items-2-item_name"')
        self.assertContains(r, 'id="id_items-TOTAL_FORMS" value="3" hx-swap-oob="true"')
        self.assertContains(r, "Delete")

    def test_create_box_with_items_skips_deleted_card(self):
        # Card 1 was deleted in the browser: it is posted half-filled with DELETE=on.
        data = self.post_data(**{
            "items-1-item_name": "half typed", "items-1-DELETE": "on",
            "items-0-item_name": "TSR2 Driver", "items-0-type": "Club", "items-0-qty": "1",
            "items-0-quantity_unit": "pcs", "items-0-brand": "Titleist", "items-0-model": "TSR2",
            "items-0-club_type": "Driver", "items-0-club_number": "1W", "items-0-loft": "10",
            "items-0-flex": "S", "items-0-shaft_material": "graphite", "items-0-specification": "45.5in",
            "items-0-color": "Black", "items-0-condition": "used", "items-0-price_jpy": "25000",
            "items-2-item_name": "Pro V1", "items-2-type": "Balls", "items-2-qty": "3",
            "items-2-quantity_unit": "dozen", "items-2-condition": "new",
        })
        r = self.htmx(self.japan, reverse("box_create"), data)
        box = Box.objects.get(cargo=self.cargo, box_no=9)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["HX-Redirect"], reverse("box_detail", args=[box.pk]))
        self.assertEqual(box.notes, "fragile")
        driver, balls = box.items.order_by("id")
        self.assertEqual((driver.brand, driver.club_number, driver.shaft_material, driver.price_jpy),
                         ("Titleist", "1W", "graphite", 25000))
        self.assertEqual((balls.qty, balls.quantity_unit, balls.price_jpy), (3, "dozen", None))

    def test_invalid_box_rerenders_modal(self):
        data = self.post_data(cargo="", **{"items-0-item_name": "No type", "items-0-qty": "1",
                                           "items-0-quantity_unit": "pcs", "items-0-condition": "new"})
        r = self.htmx(self.japan, reverse("box_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'role="dialog"')
        self.assertContains(r, "This field is required")
        self.assertFalse(Box.objects.filter(box_no=9).exists())

    def test_duplicate_box_id_rejected(self):
        r = self.htmx(self.japan, reverse("box_create"), self.post_data(box_no="3"))
        self.assertContains(r, "already exists")

    def test_myanmar_cannot_add_box(self):
        self.client.force_login(self.myanmar)
        self.assertEqual(self.client.get(reverse("box_create")).status_code, 403)


class MyanmarCheckFlowTests(BaseTest):
    def test_start_check(self):
        self.client.force_login(self.myanmar)
        detail = reverse("box_detail", args=[self.box.pk])
        r = self.client.get(detail)
        self.assertContains(r, "Start Myanmar check")
        self.assertNotContains(r, "✓ Matched")

        r = self.client.post(reverse("box_start_check", args=[self.box.pk]))
        self.assertRedirects(r, detail + "#items", fetch_redirect_response=False)
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
        item = self.box.items.get(item_name="Qi10 Driver")
        self.assertEqual(
            (item.brand, item.model, item.club_type, item.club_number, item.flex, item.shaft_material,
             item.specification, item.color, item.condition, item.price_jpy, item.quantity_unit),
            ("TaylorMade", "Qi10", "Driver", "1W", "X", "graphite", "Diamana 60", "Navy", "used", 30000, "pcs"),
        )

    def test_bad_unit_and_shaft(self):
        r = self.upload("box,type,item_name,quantity_unit,shaft_material\nGN-3,Club,X,bag,wood\n")
        self.assertContains(r, "quantity_unit must be one of")
        self.assertContains(r, "shaft_material must be steel or graphite")

    def test_template_round_trips(self):
        Cargo.objects.create(code="KL", name="KL Cargo")
        self.client.force_login(self.owner)
        template = self.client.get(reverse("csv_template")).content.decode()
        self.assertTrue(template.startswith("box,item_name,type,qty,quantity_unit,brand,model,club_type"))
        r = self.upload(template)
        self.assertContains(r, "Imported 3 item(s) into 2 box(es)")
        self.assertEqual(JapanItem.objects.get(item_name="Pro V1 balls").quantity_unit, "dozen")


class JapanSenderLockTests(BaseTest):
    def start_check(self):
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

    def test_csv_import_into_locked_box(self):
        self.start_check()
        csv_text = "box,type,item_name\nGN-3,Bag,Stand bag\n"
        self.client.force_login(self.japan)
        f = SimpleUploadedFile("l.csv", csv_text.encode(), content_type="text/csv")
        r = self.client.post(reverse("csv_import"), {"file": f})
        self.assertContains(r, "Box GN-3 is locked")
        self.assertFalse(JapanItem.objects.filter(item_name="Stand bag").exists())
        # a new box in the same kind of file is still fine
        f = SimpleUploadedFile("l.csv", b"box,type,item_name\nGN-8,Bag,Stand bag\n", content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 1 item(s)")
        # the owner can still import into the locked box
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", csv_text.encode(), content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 1 item(s)")
