from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Box, Cargo, Catalog, ClubCategory, ClubOption, Customer, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck


@admin.register(Cargo)
class CargoAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]
    search_fields = ["code", "name"]


OVERRIDE_HELP = ("Every box holds exactly one golf bag. Tick this only to save a box with no bag or with "
                 "two bags on purpose (e.g. fixing old data) – you'll see a warning after saving.")


def bag_warning(request, box, admin_site):
    n = box.items.filter(is_bag=True).count()
    if n != 1:
        admin_site.message_user(
            request, f"⚠ Box {box.label} now has {n} golf bag(s) – every box should hold exactly one "
                     "(saved with the owner override).", messages.WARNING)


class BagInlineFormSet(BaseInlineFormSet):
    """Exactly one item per box is the golf bag, unless the owner override is ticked."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        bags = sum(1 for f in self.forms
                   if f.cleaned_data and not f.cleaned_data.get("DELETE") and f.cleaned_data.get("is_bag"))
        if bags != 1 and not self.data.get("allow_bag_exception"):
            raise ValidationError(
                f"A box holds exactly one golf bag, but this box would have {bags}. Tick “Golf bag” on one item, "
                "or tick the owner override at the top of the page to save anyway.")


class BoxAdminForm(forms.ModelForm):
    allow_bag_exception = forms.BooleanField(
        required=False, label="Owner override: allow this box without exactly one golf bag", help_text=OVERRIDE_HELP)

    class Meta:
        model = Box
        fields = "__all__"


class JapanItemInline(admin.TabularInline):
    model = JapanItem
    formset = BagInlineFormSet
    extra = 0
    fields = ["is_bag", "type", "item_name", "color", "flex", "loft", "qty", "condition", "price_jpy", "customer",
              "note"]


class ExtraItemInline(admin.TabularInline):
    model = ExtraItem
    extra = 0
    fields = ["description", "qty", "added_by"]


@admin.register(Box)
class BoxAdmin(admin.ModelAdmin):
    form = BoxAdminForm
    list_display = ["label", "bag", "cargo", "status", "japan_ship_date", "myanmar_received_date", "item_count"]
    list_filter = ["status", "cargo"]
    search_fields = ["code", "cargo__code", "box_no", "notes"]
    date_hierarchy = "japan_ship_date"
    inlines = [JapanItemInline, ExtraItemInline]
    list_select_related = ["cargo"]

    @admin.display(description="Box", ordering="id")
    def label(self, obj):
        return obj.label

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.items.count()

    @admin.display(description="Golf bag")
    def bag(self, obj):
        n = obj.bag_count
        return obj.bag_name if n == 1 else ("⚠ No bag recorded" if not n else f"⚠ {n} bags")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        bag_warning(request, form.instance, self)


class ReceiveCheckInline(admin.StackedInline):
    model = ReceiveCheck
    extra = 0
    fields = ["result", "received_qty", "note", "checked_by"]


class JapanItemAdminForm(forms.ModelForm):
    allow_bag_exception = forms.BooleanField(
        required=False, label="Owner override: allow the box to have no bag or a second bag", help_text=OVERRIDE_HELP)

    class Meta:
        model = JapanItem
        fields = "__all__"

    def clean(self):
        data = super().clean()
        box, item = data.get("box"), self.instance
        if box and not data.get("allow_bag_exception"):
            others = box.items.filter(is_bag=True).exclude(pk=item.pk).count()
            if data.get("is_bag") and others:
                self.add_error("is_bag", f"Box {box.label} already has a golf bag – a box holds exactly one.")
            elif item.is_bag and not data.get("is_bag") and not others:
                self.add_error("is_bag", f"This is box {box.label}'s only golf bag – every box needs one.")
        return data


@admin.register(JapanItem)
class JapanItemAdmin(admin.ModelAdmin):
    form = JapanItemAdminForm
    list_display = ["box", "__str__", "is_bag", "type", "loft", "head_only", "flex", "qty", "customer"]
    list_filter = ["is_bag", "box__cargo", "condition", "flex", "type"]
    search_fields = ["item_name", "customer", "note"]
    list_select_related = ["box__cargo"]
    autocomplete_fields = ["box"]
    inlines = [ReceiveCheckInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        bag_warning(request, obj.box, self)

    def delete_model(self, request, obj):
        box = obj.box
        super().delete_model(request, obj)
        bag_warning(request, box, self)


@admin.register(ReceiveCheck)
class ReceiveCheckAdmin(admin.ModelAdmin):
    list_display = ["japan_item", "result", "received_qty", "checked_by", "checked_at"]
    list_filter = ["result", "japan_item__box__cargo"]
    search_fields = ["japan_item__item_name", "note"]
    list_select_related = ["japan_item__box__cargo", "checked_by"]
    raw_id_fields = ["japan_item"]


@admin.register(ExtraItem)
class ExtraItemAdmin(admin.ModelAdmin):
    list_display = ["box", "description", "qty", "added_by", "created_at"]
    list_filter = ["box__cargo"]
    search_fields = ["description"]
    autocomplete_fields = ["box"]


@admin.register(CustomerRequest)
class CustomerRequestAdmin(admin.ModelAdmin):
    list_display = ["customer", "type", "item_name", "flex", "loft", "qty", "status", "box", "created_at"]
    list_filter = ["status", "type"]
    search_fields = ["customer", "item_name", "note"]
    autocomplete_fields = ["box"]
    raw_id_fields = ["japan_item"]


@admin.register(Catalog)
class CatalogAdmin(admin.ModelAdmin):
    list_display = ["type", "brand_model", "flex_options", "loft_options"]
    list_filter = ["type"]
    search_fields = ["brand_model"]


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "viber_member", "favourite", "address"]
    list_filter = ["viber_member", "favourite"]
    search_fields = ["name", "phone", "address"]


class ClubOptionInline(admin.TabularInline):
    model = ClubOption
    extra = 1
    fields = ["label", "sort_order", "active", "type", "loft", "shaft_material", "club_number", "length_in",
              "head_only"]


@admin.register(ClubCategory)
class ClubCategoryAdmin(admin.ModelAdmin):
    """Category buttons of the "Club option" picker (Driver, Wood, Putter…). Add one here, e.g. "Chipper"."""

    list_display = ["name", "sort_order", "active", "free_text", "option_count"]
    list_editable = ["sort_order", "active", "free_text"]
    ordering = ["sort_order", "id"]
    inlines = [ClubOptionInline]

    @admin.display(description="Options")
    def option_count(self, obj):
        return obj.options.count()


@admin.register(ClubOption)
class ClubOptionAdmin(admin.ModelAdmin):
    """Value buttons for the "Club option" picker on the item card."""

    list_display = ["category", "label", "full_label", "sort_order", "active", "type", "loft", "shaft_material",
                    "club_number", "length_in", "head_only"]
    list_display_links = ["label"]
    list_editable = ["sort_order", "active"]
    list_filter = ["category", "active"]
    list_select_related = ["category"]
    ordering = ["category__sort_order", "sort_order", "id"]
    fieldsets = [
        ("Button", {"fields": ["category", "label", "sort_order", "active"]}),
        ("Saved on the item", {"fields": ["type", "loft", "shaft_material", "club_number", "length_in", "head_only"],
                               "description": 'e.g. Wedge 52°: type "Wedge", loft "52". '
                                              'Iron Graphite 5: type "Iron", shaft graphite, club number "5". '
                                              'Wood 3W: type "Wood", club number "3W". '
                                              'Putter 34″: type "Putter", length "34".'}),
    ]

    @admin.display(description="Shown as")
    def full_label(self, obj):
        return obj.full_label
