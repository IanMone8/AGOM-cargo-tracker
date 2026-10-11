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
                           ("csv_import", []), ("csv_template", [])]:
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
