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

    def add_bag(self, box=None, name="Titleist Stand Bag"):
        return JapanItem.objects.create(box=box or self.box, is_bag=True, type="Golf bag", item_name=name, qty=1)

    def ship(self, box=None):
        """Japan sender marks the box Shipped – only then can Myanmar see it."""
        box = box or self.box
        box.status = Box.Status.SHIPPED
        box.save()

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
    def setUp(self):
        super().setUp()
        self.bag = self.add_bag()

    def upload(self, text, **extra):
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("list.csv", text.encode("utf-8-sig"), content_type="text/csv")
        return self.client.post(reverse("csv_import"), {"file": f, **extra})

    def test_import_creates_boxes_and_items(self):
        r = self.upload(
            "box,type,item_name,flex,loft,qty,condition,price_jpy,customer,note\n"
            "GN-3,Putter,Newport 2,,,1,used,\"12,000\",,\n"
            "gn-5,Bag,Ping Hoofer,,,,,,,\n"
            "gn-5,Driver,Qi10,sr,10.5,2,new,,Ma Hnin,\n"
        )
        self.assertContains(r, "Imported 2 item(s) and 1 golf bag(s) into 2 box(es)")
        new_box = Box.objects.get(cargo=self.cargo, box_no=5)
        self.assertEqual(new_box.title, "GN-5 · Ping Hoofer")
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
        self.assertEqual(list(self.box.items.values_list("item_name", "is_bag")), [("Stand bag", True), ("Pro V1", False)])


class AddBoxModalTests(BaseTest):
    def post_data(self, **items):
        data = {"cargo": self.cargo.pk, "box_no": "9", "notes": "fragile",
                "bag-item_name": "Titleist Stand Bag", "bag-color": "Black", "bag-customer": "Ko Aung",
                "bag-note": "zip pocket",
                "items-TOTAL_FORMS": "3", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000"}
        data.update(items)
        return data

    def test_modal_opens_over_page(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'role="dialog"')
        self.assertContains(r, "Items inside")
        self.assertContains(r, 'name="items-0-club_option"')
        for gone in ("shaft_material", "brand", "model", "loft", "items-0-type"):
            self.assertNotContains(r, f'name="items-0-{gone}"' if "items-0" not in gone else f'name="{gone}"')
        self.assertNotContains(r, "<html")  # fragment, not a full page

    def test_add_item_card(self):
        self.client.force_login(self.japan)
        r = self.client.get(reverse("box_item_card"), {"items-TOTAL_FORMS": "2"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, 'name="items-2-item_name"')
        self.assertContains(r, 'id="id_items-TOTAL_FORMS" value="3" hx-swap-oob="true"')
        self.assertContains(r, "Delete")

    def test_create_box_with_items_skips_deleted_card(self):
        from .models import ClubOption
        graphite5 = ClubOption.objects.get(category__name="Iron", label="Graphite 5")
        # Card 1 was deleted in the browser: it is posted half-filled with DELETE=on.
        data = self.post_data(**{
            "items-1-item_name": "half typed", "items-1-DELETE": "on",
            "items-0-item_name": "Mizuno JPX 923", "items-0-club_option": str(graphite5.pk),
            "items-0-flex": "R", "items-0-qty": "1", "items-0-note": "with covers",
            "items-2-club_option": "other", "items-2-club_other": "Putter", "items-2-qty": "3",
        })
        r = self.htmx(self.japan, reverse("box_create"), data)
        box = Box.objects.get(cargo=self.cargo, box_no=9)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["HX-Redirect"], reverse("box_detail", args=[box.pk]))
        self.assertEqual(box.notes, "fragile")
        bag, iron, putter = box.items.all()   # the bag comes first
        self.assertEqual((bag.is_bag, bag.item_name, bag.color, bag.customer, bag.note, bag.type, bag.qty),
                         (True, "Titleist Stand Bag", "Black", "Ko Aung", "zip pocket", "Golf bag", 1))
        self.assertEqual(box.title, "GN-9 · Titleist Stand Bag")
        self.assertEqual((iron.type, iron.shaft_material, iron.club_number, iron.flex, iron.note),
                         ("Iron", "graphite", "5", "R", "with covers"))
        self.assertEqual(iron.club_label, "Iron · Graphite 5")
        self.assertEqual((putter.type, putter.item_name, putter.qty, putter.title), ("Putter", "", 3, "Putter"))

    def test_invalid_box_rerenders_modal(self):
        data = self.post_data(cargo="", **{"items-0-item_name": "Bag", "items-0-qty": "1"})
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
        self.assertTrue(template.startswith("box,kind,item_name,color,club_option,flex,qty,customer,note"))
        r = self.upload(template, replace="on")
        self.assertContains(r, "Imported 4 item(s) and 2 golf bag(s) into 2 box(es)")
        self.assertEqual(Box.objects.get(box_no=1).title, "KL-1 · Ping Hoofer")
        head = JapanItem.objects.get(item_name="", box__box_no=3)
        self.assertEqual((head.type, head.loft, head.head_only, head.qty, head.title), ("Driver", "9.5", True, 2, "Driver head 9.5°"))
        self.assertEqual(JapanItem.objects.get(item_name="Cleveland RTX 6").club_label, "Wedge 52°")


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
        for name, args in [("csv_import", []), ("csv_template", []), ("request_list", []),
                           ("request_edit", [req.pk]), ("request_attach", [req.pk])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        for name in ("request_status", "request_detach"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name, args=[req.pk]), {"status": "fulfilled"}).status_code, 403)
        f = SimpleUploadedFile("l.csv", b"box,type,item_name\nGN-3,Bag,Stand bag\n", content_type="text/csv")
        self.assertEqual(self.client.post(reverse("csv_import"), {"file": f}).status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, "open")
        self.assertFalse(JapanItem.objects.filter(item_name="Stand bag").exists())

    def test_japan_sidebar_and_dashboard_hide_links(self):
        CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2")
        self.client.force_login(self.japan)
        r = self.client.get(reverse("dashboard"))
        self.assertNotContains(r, reverse("csv_import"))
        self.assertNotContains(r, reverse("request_list"))
        self.assertNotContains(r, "Open requests")
        self.assertNotContains(r, "Ko Aung")
        self.assertContains(r, "Add box")  # still can add boxes

    def test_other_roles_keep_access(self):
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, reverse("request_list"))
        self.assertNotContains(r, reverse("csv_import"))
        self.assertEqual(self.client.get(reverse("request_list")).status_code, 200)
        self.client.force_login(self.owner)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, reverse("request_list"))
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
        self.assertEqual(cards, ["peach", "lavender", "green", "cream"])
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
        self.assertRegex(blocks[2], r'class="stat-total[^"]*text-stone-900/35[^"]*">/ 2</span>')
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
        # not offered when attaching customer requests
        r = self.client.get(reverse("request_attach", args=[req.pk]))
        self.assertNotContains(r, label)
        r = self.client.get(reverse("request_attach_items"), {"box": self.box.pk})
        self.assertNotContains(r, "TSR2")
        self.assertEqual(self.client.post(reverse("request_attach", args=[req.pk]),
                                          {"japan_item": self.item1.pk}).status_code, 404)

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
        f = SimpleUploadedFile("l.csv", b"box,type,item_name,customer\nGN-3,Bag,Stand bag,Ko Zaw\n", content_type="text/csv")
        self.client.post(reverse("csv_import"), {"file": f})
        names = set(self.Customer.objects.values_list("name", flat=True))
        self.assertTrue({"U Tun", "Daw Mya", "Ko Zaw"} <= names)
        CustomerRequest.objects.create(customer="ko aung", item_name="x")  # same person, different case
        self.assertEqual(self.Customer.objects.filter(name__iexact="ko aung").count(), 1)

    def test_customer_name_links(self):
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="Qi10")
        self.client.force_login(self.myanmar)
        r = self.client.get(reverse("request_list"))
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
        self.item1.customer = "ma hnin"                                                           # in a shipped box -> In box
        self.item1.save()
        CustomerRequest.objects.create(customer="U Tun", item_name="Putter", status="fulfilled")  # all done
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
        self.assertIn('crm-tag-in_box"><i></i>In box', html)
        self.assertIn('crm-tag-delivered"><i></i>All delivered', html)
        self.assertIn('crm-tag-none"><i></i>No orders', html)
        self.assertIn("just now", html)                                     # last update
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
        self.assertEqual(self.rows(orders="open")[0], ["Ko Aung"])
        self.assertEqual(self.rows(orders="none")[0], ["Ko Zaw", "Ma Hnin", "U Tun"])
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
        r = self.client.post(reverse("customer_new_order", args=[self.ko.pk]),
                             {"item_name": "Ping G430 Max", "type": "Driver", "flex": "SR", "loft": "10.5", "qty": "2"})
        self.assertRedirects(r, detail + "#orders")
        order = CustomerRequest.objects.get(item_name="Ping G430 Max")
        self.assertEqual((order.customer, order.status, order.qty, order.flex), ("Ko Aung", "open", 2, "SR"))
        r = self.client.get(detail)
        self.assertContains(r, "Ping G430 Max")
        row = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn("crm-tag-waiting", row)

    def test_new_order_validation_keeps_form_open(self):
        r = self.client.post(reverse("customer_new_order", args=[self.ko.pk]), {"item_name": "", "qty": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "This field is required")
        self.assertNotContains(r, 'id="new-order" method="post" action="/customers/%d/orders/new/" class="hidden' % self.ko.pk)
        self.assertFalse(CustomerRequest.objects.exists())

    def test_menu_has_new_order_and_japan_blocked(self):
        html = self.client.get(reverse("customer_list")).content.decode()
        self.assertIn(reverse("customer_detail", args=[self.ko.pk]) + "?new_order=1#orders", html)
        self.client.force_login(self.japan)
        r = self.client.post(reverse("customer_new_order", args=[self.ko.pk]), {"item_name": "X", "qty": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(CustomerRequest.objects.exists())


class AttachItemSelectTests(BaseTest):
    """Regression: items for "pick a box and item" must arrive inside a real <select>."""

    def setUp(self):
        super().setUp()
        self.ship()
        self.req = CustomerRequest.objects.create(customer="Ma Hnin", item_name="Wedge")
        self.client.force_login(self.myanmar)

    def test_item_response_is_a_select_with_all_options_inside(self):
        import re
        r = self.client.get(reverse("request_attach_items"), {"box": self.box.pk, "req": self.req.pk},
                            HTTP_HX_REQUEST="true")
        html = r.content.decode()
        m = re.fullmatch(r'\s*(?:\{#.*?#\})?\s*<select id="attach-items-%d" name="japan_item"[^>]*>(.*)</select>\s*' % self.req.pk,
                         html, re.S)
        self.assertIsNotNone(m, "response must be exactly one <select> element")
        inner = m.group(1)
        self.assertEqual(inner.count("<option"), 3)          # placeholder + 2 items
        self.assertIn(f'<option value="{self.item1.pk}">', inner)
        self.assertNotIn("</select>", inner)
        self.assertNotIn("disabled", html.split(">")[0])     # enabled once a box is picked

    def test_box_select_replaces_the_item_select(self):
        html = self.client.get(reverse("request_attach", args=[self.req.pk]), HTTP_HX_REQUEST="true").content.decode()
        box_select = html[html.index('<select name="box"'):].split(">")[0]
        self.assertIn(f'hx-target="#attach-items-{self.req.pk}"', box_select)
        self.assertIn('hx-swap="outerHTML"', box_select)      # explicit, not inherited from the form
        self.assertIn(f'id="attach-items-{self.req.pk}"', html)
        self.assertIn("Choose a box first", html)

    def test_empty_box_and_unknown_box(self):
        from .models import Box as BoxModel
        empty = BoxModel.objects.create(cargo=self.cargo, box_no=9, status="shipped")
        html = self.client.get(reverse("request_attach_items"), {"box": empty.pk, "req": self.req.pk}).content.decode()
        self.assertIn("No items in this box", html)
        self.assertIn("<select", html)
        html = self.client.get(reverse("request_attach_items"), {"box": 99999, "req": self.req.pk}).content.decode()
        self.assertIn("<select", html)
        self.assertIn("Choose a box first", html)

    def test_attach_links_request_and_sets_in_box(self):
        r = self.client.post(reverse("request_attach", args=[self.req.pk]),
                             {"box": self.box.pk, "japan_item": self.item2.pk}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "In box")
        self.req.refresh_from_db()
        self.assertEqual((self.req.status, self.req.japan_item, self.req.box), ("in_box", self.item2, self.box))


class DashboardHeroTests(BaseTest):
    def test_hero_for_every_role(self):
        for user in (self.owner, self.japan, self.myanmar):
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
        return self.client.post(reverse("item_add", args=[self.box.pk]), {"qty": "1", **fields}, HTTP_HX_REQUEST="true")

    def test_seeded_options_and_labels(self):
        self.assertEqual(len(self.opt), 36)
        for label in ("Driver 9°", "Driver 10.5°", "Driver head 8°", "Driver head 9.5°", "Iron · Graphite 5",
                      "Iron · Steel", "Wedge 48°", "Wedge 60°", "Wood 2W", "Wood 3W", "Wood 9W", "Hybrid 2H",
                      "Hybrid 4H", "Hybrid 7H", "Putter 32″", "Putter 34″", "Putter 38″"):
            self.assertIn(label, self.opt)

    def test_card_has_only_simple_fields_and_picker(self):
        html = self.client.get(reverse("item_add", args=[self.box.pk]), HTTP_HX_REQUEST="true").content.decode()
        for name in ("item_name", "customer", "club_option", "club_other", "flex", "qty", "note"):
            self.assertIn(f'name="{name}"', html)
        self.assertLess(html.index('name="item_name"'), html.index('name="customer"'))   # right below Item name
        self.assertLess(html.index('name="customer"'), html.index('name="club_option"'))
        for gone in ("type", "loft", "brand", "model", "shaft_material", "price_jpy"):
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
        cases = [("Iron · Graphite 5", dict(type="Iron", shaft_material="graphite", club_number="5", head_only=False)),
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
        r = self.add()
        self.assertContains(r, "Pick a club option or type an item name.")
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
        CustomerRequest.objects.create(customer="Ko Aung", item_name="TSR2", japan_item=self.item1, status="in_box")
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
        f = SimpleUploadedFile("l.csv", (
            "box,item_name,club_option,flex,qty\n"
            "GN-3,,iron graphite 5,R,1\n"
            "GN-3,Vokey,Wedge 56°,,2\n"
            "GN-3,Head,driver head 10,,1\n").encode(), content_type="text/csv")
        r = self.client.post(reverse("csv_import"), {"file": f})
        self.assertContains(r, "Imported 3 item(s)")
        labels = sorted(i.club_label for i in JapanItem.objects.filter(box=self.box).exclude(pk__in=[self.item1.pk, self.item2.pk, bag.pk]))
        self.assertEqual(labels, ["Driver head 10°", "Iron · Graphite 5", "Wedge 56°"])
        f = SimpleUploadedFile("l.csv", b"box,item_name,club_option\nGN-3,X,Wedge 99\n", content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Unknown club_option")

    def test_csv_wood_hybrid_putter(self):
        bag = self.add_bag()
        self.client.force_login(self.owner)
        rows = ["Wood 3W", "wood 3", "Hybrid 4", "4H", "Putter 34", "Putter 34″", "hybrid 4h", "3w"]
        f = SimpleUploadedFile("l.csv", ("box,club_option\n" + "".join(f"GN-3,{r}\n" for r in rows)).encode(),
                               content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 8 item(s)")
        items = JapanItem.objects.filter(box=self.box).exclude(pk__in=[self.item1.pk, self.item2.pk, bag.pk]).order_by("id")
        self.assertEqual([i.club_label for i in items],
                         ["Wood 3W", "Wood 3W", "Hybrid 4H", "Hybrid 4H", "Putter 34″", "Putter 34″", "Hybrid 4H",
                          "Wood 3W"])
        self.assertEqual((items[0].type, items[0].club_number), ("Wood", "3W"))
        self.assertEqual((items[4].type, items[4].length_in), ("Putter", "34"))
        f = SimpleUploadedFile("l.csv", b"box,club_option\nGN-3,Wood 8\n", content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Unknown club_option")
        f = SimpleUploadedFile("l.csv", b"box,type,item_name,loft,shaft_material\nGN-3,Club,Old style,10,steel\n",
                               content_type="text/csv")
        self.assertContains(self.client.post(reverse("csv_import"), {"file": f}), "Imported 1 item(s)")
        self.assertEqual(JapanItem.objects.get(item_name="Old style").shaft_material, "steel")


class ItemCardCustomerTests(BaseTest):
    def test_customer_field_autocomplete_and_save(self):
        from .models import Customer
        Customer.objects.create(name="Ko Aung")
        self.client.force_login(self.japan)
        # the Add box modal and the box page both carry the customer suggestions
        modal = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('list="customer-names"', modal)
        self.assertIn('<datalist id="customer-names"><option value="Ko Aung">', modal)
        page = self.client.get(reverse("box_detail", args=[self.box.pk])).content.decode()
        self.assertIn('<datalist id="customer-names"><option value="Ko Aung">', page)
        # optional: an item without a customer saves fine
        self.client.post(reverse("item_add", args=[self.box.pk]), {"item_name": "Bag", "qty": "1"}, HTTP_HX_REQUEST="true")
        self.assertEqual(JapanItem.objects.latest("id").customer, "")
        # a new name is saved on the item and creates the customer
        self.client.post(reverse("item_add", args=[self.box.pk]),
                         {"item_name": "TSR2", "customer": "  Ma   Hnin ", "qty": "1"}, HTTP_HX_REQUEST="true")
        self.assertEqual(JapanItem.objects.latest("id").customer, "Ma Hnin")
        self.assertTrue(Customer.objects.filter(name="Ma Hnin").exists())


class GolfBagTests(BaseTest):
    """Every box holds exactly one golf bag; all other items are packed inside it."""

    def modal_data(self, **extra):
        data = {"cargo": self.cargo.pk, "box_no": "12", "notes": "",
                "bag-item_name": "", "bag-color": "", "bag-customer": "", "bag-note": "",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
                "items-0-item_name": "Pro V1", "items-0-qty": "2"}
        data.update(extra)
        return data

    def csv(self, text, **extra):
        self.client.force_login(self.owner)
        f = SimpleUploadedFile("l.csv", text.encode(), content_type="text/csv")
        return self.client.post(reverse("csv_import"), {"file": f, **extra})

    # --- Add box modal -------------------------------------------------------
    def test_modal_has_required_bag_card_before_items(self):
        self.client.force_login(self.japan)
        html = self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true").content.decode()
        for name in ("bag-item_name", "bag-color", "bag-customer", "bag-note"):
            self.assertIn(f'name="{name}"', html)
        self.assertLess(html.index('name="bag-item_name"'), html.index("Items inside the bag"))
        self.assertLess(html.index("Items inside the bag"), html.index('name="items-0-club_option"'))
        self.assertIn('name="bag-item_name"', html)
        self.assertRegex(html, r'name="bag-item_name"[^>]*required')
        self.assertNotIn("allow_no_bag", html)               # no override for the Japan sender
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("box_create"), HTTP_HX_REQUEST="true"), 'name="allow_no_bag"')

    def test_box_needs_its_bag(self):
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data())
        self.assertContains(r, "Every box holds one golf bag")
        self.assertFalse(Box.objects.filter(box_no=12).exists())
        # the Japan sender can't use the owner override
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data(allow_no_bag="on"))
        self.assertFalse(Box.objects.filter(box_no=12).exists())
        r = self.htmx(self.japan, reverse("box_create"), self.modal_data(**{"bag-item_name": "Ping  Hoofer",
                                                                             "bag-color": "Navy"}))
        self.assertEqual(r.status_code, 204)
        box = Box.objects.get(box_no=12)
        self.assertEqual([(i.title, i.is_bag) for i in box.items.all()], [("Ping Hoofer", True), ("Pro V1", False)])

    def test_owner_override_saves_without_bag_with_warning(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse("box_create"), self.modal_data(allow_no_bag="on"), follow=True)
        box = Box.objects.get(box_no=12)
        self.assertFalse(box.items.filter(is_bag=True).exists())
        self.assertContains(r, "WITHOUT a golf bag")
        self.assertContains(r, "No bag recorded")
        # half-filled bag with the override: the name is still needed
        r = self.htmx(self.owner, reverse("box_create"), self.modal_data(box_no="13", allow_no_bag="on",
                                                                         **{"bag-color": "Red"}))
        self.assertContains(r, "Enter the bag&#x27;s name")

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
        self.assertIn("GN-3 · Titleist Stand Bag", html.replace('<span class="font-semibold text-emerald-100"> · ', " · "))
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
        self.assertContains(r, 'name="bag-item_name"')
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
        self.assertEqual(self.box.bag_name, "Owner fix")

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

    # --- Customer orders and attach ----------------------------------------
    def test_bag_attach_and_customer_orders(self):
        bag = self.add_bag()
        bag.customer = "Ko Aung"
        bag.save()
        req = CustomerRequest.objects.create(customer="Ko Aung", item_name="Stand bag")
        self.client.force_login(self.owner)
        r = self.client.get(reverse("request_attach_items"), {"box": self.box.pk, "req": req.pk})
        self.assertContains(r, f'<option value="{bag.pk}">Golf bag: Titleist Stand Bag')
        self.client.post(reverse("request_attach", args=[req.pk]), {"japan_item": bag.pk}, HTTP_HX_REQUEST="true")
        req.refresh_from_db()
        self.assertEqual((req.japan_item, req.status), (bag, CustomerRequest.Status.IN_BOX))
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
                     "GN-5,bag,Titleist Stand Bag,Black,,1,Ko Aung\n"
                     "GN-5,,TSR2,,Driver 10.5,1,\n"
                     "GN-5,item,Pro V1,,,2,\n")
        self.assertContains(r, "Imported 2 item(s) and 1 golf bag(s) into 1 box(es)")
        box = Box.objects.get(box_no=5)
        bag = box.items.get(is_bag=True)
        self.assertEqual((bag.item_name, bag.color, bag.customer, bag.type, bag.qty),
                         ("Titleist Stand Bag", "Black", "Ko Aung", "Golf bag", 1))
        self.assertEqual(box.items.first(), bag)
        # adding more items to a box that already has its bag needs no bag row
        self.assertContains(self.csv("box,item_name\nGN-5,Head cover\n"), "Imported 1 item(s) into 1 box(es)")

    def test_csv_rejects_zero_or_two_bags_with_row_numbers(self):
        r = self.csv("box,kind,item_name\n"
                     "GN-5,,TSR2\n"
                     "GN-5,,Pro V1\n"
                     "GN-6,bag,Bag one\n"
                     "GN-6,,Wedge\n"
                     "GN-6,bag,Bag two\n")
        self.assertContains(r, "Nothing was imported")
        self.assertContains(r, "Row 2: Box GN-5 has no golf bag – add a row with kind = bag (box GN-5 is on rows 2, 3)")
        self.assertContains(r, "Row 4: Box GN-6 has 2 bag rows (rows 4, 6) – a box holds exactly one golf bag")
        self.assertFalse(Box.objects.filter(box_no__in=[5, 6]).exists())
        # GN-3 has no bag yet: adding items without a bag row is rejected too
        self.assertContains(self.csv("box,item_name\nGN-3,Head cover\n"), "Box GN-3 has no golf bag")
        # GN-3 with its bag: a second bag row is rejected
        self.add_bag()
        self.assertContains(self.csv("box,kind,item_name\nGN-3,bag,Another\n"),
                            "Box GN-3 already has a golf bag (Titleist Stand Bag)")

    def test_csv_bag_row_checks(self):
        r = self.csv("box,kind,item_name,club_option,qty\n"
                     "GN-5,bag,,,1\n"
                     "GN-5,bag,X,Wedge 52,2\n"
                     "GN-5,box,Y,,1\n")
        for msg in ("Row 2: bag row: put the bag&#x27;s name in item_name", "Row 3: bag row: leave club_option empty",
                    "Row 3: bag row: qty must be 1", "Row 4: kind must be bag or item"):
            self.assertContains(r, msg)

    def test_csv_owner_override(self):
        r = self.csv("box,kind,item_name\nGN-5,,TSR2\n", allow_bag_exception="on")
        self.assertContains(r, "Imported 1 item(s) into 1 box(es)")
        self.assertContains(r, "Imported with the owner override")
        self.assertContains(r, "Row 2: Box GN-5 has no golf bag")
        self.assertTrue(Box.objects.filter(box_no=5).exists())

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
