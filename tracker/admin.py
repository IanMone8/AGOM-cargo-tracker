from django.contrib import admin

from .models import Box, Cargo, Catalog, Customer, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck


@admin.register(Cargo)
class CargoAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]
    search_fields = ["code", "name"]


class JapanItemInline(admin.TabularInline):
    model = JapanItem
    extra = 0
    fields = ["type", "item_name", "flex", "loft", "qty", "condition", "price_jpy", "customer", "note"]


class ExtraItemInline(admin.TabularInline):
    model = ExtraItem
    extra = 0
    fields = ["description", "qty", "added_by"]


@admin.register(Box)
class BoxAdmin(admin.ModelAdmin):
    list_display = ["label", "cargo", "status", "japan_ship_date", "myanmar_received_date", "item_count"]
    list_filter = ["status", "cargo"]
    search_fields = ["cargo__code", "box_no", "notes"]
    date_hierarchy = "japan_ship_date"
    inlines = [JapanItemInline, ExtraItemInline]
    list_select_related = ["cargo"]

    @admin.display(description="Box", ordering="box_no")
    def label(self, obj):
        return obj.label

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.items.count()


class ReceiveCheckInline(admin.StackedInline):
    model = ReceiveCheck
    extra = 0
    fields = ["result", "received_qty", "note", "checked_by"]


@admin.register(JapanItem)
class JapanItemAdmin(admin.ModelAdmin):
    list_display = ["box", "type", "item_name", "flex", "loft", "qty", "condition", "price_jpy", "customer"]
    list_filter = ["box__cargo", "condition", "flex", "type"]
    search_fields = ["item_name", "customer", "note"]
    list_select_related = ["box__cargo"]
    autocomplete_fields = ["box"]
    inlines = [ReceiveCheckInline]


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
    list_display = ["name", "phone", "viber_member", "address"]
    list_filter = ["viber_member"]
    search_fields = ["name", "phone", "address"]
