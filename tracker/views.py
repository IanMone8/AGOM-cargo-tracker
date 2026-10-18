import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, NullIf
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.views.decorators.http import require_POST

from . import roles
from .csv_import import import_japan_csv, template_csv
from .forms import (
    BagForm,
    BoxCreateForm,
    BoxFilterForm,
    BoxForm,
    ItemFormSet,
    CSVImportForm,
    CustomerForm,
    CustomerOrderForm,
    OrderEditForm,
    OrderLineFormSet,
    OrderPaymentForm,
    ExtraItemForm,
    JapanItemForm,
)
from .models import (Box, Cargo, Catalog, Customer, CustomerRequest, ExtraItem, JapanItem, Payment, ReceiveCheck,
                     add_order_money, customer_money, mark_old_orders_paid, money_status, order_pay_state, order_totals,
                     yangon_today)

ITEM_CATEGORIES = ["Club", "Club set", "Bag", "Balls", "Shoes", "Apparel", "Accessory"]
CLUB_TYPES = ["Driver", "Fairway wood", "Utility", "Iron set", "Iron", "Wedge", "Putter"]
BRANDS = [
    "Bridgestone", "Callaway", "Cleveland", "Cobra", "Honma", "Mizuno", "Odyssey", "Ping",
    "PRGR", "Scotty Cameron", "Srixon", "TaylorMade", "Titleist", "XXIO", "Yamaha", "Yonex",
]


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def catalog_context(user=None):
    """Values for the <datalist> dropdowns used by item / request forms.
    Customer names only for people who may see customers (never the Japan sender)."""
    entries = list(Catalog.objects.all())
    lofts = sorted({l for c in entries for l in c.loft_list}, key=lambda v: (len(v), v))
    return {
        "catalog_types": sorted({c.type for c in entries} | set(CLUB_TYPES)),
        "catalog_models": [c.brand_model for c in entries],
        "catalog_lofts": lofts,
        "item_categories": ITEM_CATEGORIES,
        "customer_names": (list(Customer.objects.values_list("name", flat=True))
                           if user is not None and roles.can_use_customers(user) else []),
        "brands": BRANDS,
    }


def visible_boxes(user):
    """Boxes this user may see: the Myanmar checker only sees a box once it is shipped."""
    qs = Box.objects.all()
    if not roles.sees_unshipped_boxes(user):
        qs = qs.exclude(status=Box.Status.PACKED)
    return qs


def visible_items(user):
    qs = JapanItem.objects.all()
    if not roles.sees_unshipped_boxes(user):
        qs = qs.exclude(box__status=Box.Status.PACKED)
    return qs


def with_bag(qs):
    """Annotate each box with its golf bag's name (for "JP-7 · Titleist Stand Bag") and bag count."""
    bag = JapanItem.objects.filter(box=OuterRef("pk"), is_bag=True).order_by("id")
    return qs.annotate(
        bag_title=Subquery(bag.annotate(t=Coalesce(NullIf("item_name", Value("")), "type")).values("t")[:1]),
        bags_annotated=Subquery(bag.order_by().values("box").annotate(n=Count("id")).values("n")[:1]),
    )


def with_money(qs):
    """Annotate money_total = Σ line totals + cargo fee, and has_money (any amount or fee entered)."""
    lines = (JapanItem.objects.filter(box=OuterRef("pk"), line_total_jpy__isnull=False).order_by().values("box")
             .annotate(t=Sum("line_total_jpy")).values("t")[:1])
    return qs.annotate(
        items_money=Coalesce(Subquery(lines, output_field=IntegerField()), Value(0)),
        money_total=F("items_money") + Coalesce(F("cargo_fee_jpy"), Value(0)),
    )


def boxes_with_counts(qs=None):
    qs = qs if qs is not None else Box.objects.all()
    return with_money(with_bag(qs)).select_related("cargo").annotate(
        item_count=Count("items", distinct=True),
        checked_count=Count("items__receive_check", distinct=True),
        problem_count=Count(
            "items__receive_check",
            filter=~Q(items__receive_check__result=ReceiveCheck.Result.MATCHED),
            distinct=True,
        ),
        extra_count=Count("extra_items", distinct=True),
    )


# --- Dashboard ---------------------------------------------------------------
# Stickers on the Myanmar checker's banner, one per corner. Each is animated (an animated WebP made by
# scripts/make_animated_stickers.py, with the original GIF as fallback) and has a still picture for people
# who ask for reduced motion (the small copy of the PNG made by scripts/make_display_images.py).
STICKERS = [
    {"name": "confetti", "corner": "top-left", "still": "happy"},
    {"name": "stars", "corner": "top-right", "still": "stars"},
    {"name": "peace", "corner": "bottom-left", "still": "peace"},
    {"name": "ok", "corner": "bottom-right", "still": "ok"},
]


def banner_stickers():
    """Stickers whose animation exists (WebP or GIF) – a missing one is simply not shown."""
    found = []
    for s in STICKERS:
        webp = f"img/display/stickers/animated_{s['name']}.webp"
        gif = f"img/stickers/animated_{s['name']}.gif"
        still = f"img/display/stickers/{s['still']}.webp"
        has_webp, has_gif = bool(finders.find(webp)), bool(finders.find(gif))
        if not (has_webp or has_gif):
            continue
        found.append({**s, "webp": webp if has_webp else "", "gif": gif if has_gif else webp,
                      "still": still if finders.find(still) else ""})
    return found


@login_required
def dashboard(request):
    boxes = visible_boxes(request.user)
    in_transit = boxes_with_counts(boxes.filter(status=Box.Status.SHIPPED))
    needs_review = boxes_with_counts(
        boxes.filter(
            Q(status=Box.Status.ARRIVED) | Q(pk__in=Box.with_mismatches().values("pk"))
        )
    )
    return render(request, "tracker/dashboard.html", {
        "cargo_count": Cargo.objects.count(),
        "total_box_count": boxes.count(),
        "checked_box_count": boxes.filter(status=Box.Status.CHECKED).count(),
        "mismatch_count": boxes.filter(pk__in=Box.with_mismatches().values("pk")).count(),
        "in_transit": in_transit,
        "needs_review": needs_review,
        "recent": boxes_with_counts(boxes.order_by("-updated_at"))[:5],
        # the Myanmar checker gets their own (Burmese) banner
        "myanmar_banner": roles.is_myanmar_checker(request.user),
        "stickers": banner_stickers() if roles.is_myanmar_checker(request.user) else [],
    })


# --- Boxes -------------------------------------------------------------------
@login_required
def box_list(request):
    form = BoxFilterForm(request.GET or None)
    if not roles.sees_unshipped_boxes(request.user):
        form.fields["status"].choices = [
            c for c in form.fields["status"].choices if c[0] != Box.Status.PACKED
        ]
    qs = visible_boxes(request.user)
    if form.is_valid():
        if form.cleaned_data["cargo"]:
            qs = qs.filter(cargo=form.cleaned_data["cargo"])
        if form.cleaned_data["status"]:
            qs = qs.filter(status=form.cleaned_data["status"])
        if form.cleaned_data["brand"]:
            qs = qs.filter(pk__in=JapanItem.objects.filter(brand__iexact=form.cleaned_data["brand"]).values("box"))
    ctx = {"form": form, "boxes": boxes_with_counts(qs)}
    if is_htmx(request) and request.headers.get("HX-Target") == "box-results":
        return render(request, "tracker/partials/box_results.html", ctx)
    return render(request, "tracker/box_list.html", ctx)


@login_required
@roles.require(roles.can_edit_japan)
def box_create(request):
    """"Add box" – a modal over the current page when opened via HTMX.

    The box, its golf bag (required: every box holds exactly one) and the items inside the bag.
    """
    data = request.POST or None
    override = bool(data and roles.is_manager(request.user) and data.get("allow_no_bag"))
    form = BoxCreateForm(data, user=request.user)
    customers = roles.can_use_customers(request.user)
    bag_form = BagForm(data, prefix="bag", optional=override, customers=customers)
    formset = ItemFormSet(data, prefix="items", form_kwargs={"customers": customers})
    if request.method == "POST" and all([form.is_valid(), bag_form.is_valid(), formset.is_valid()]):
        wanted = form.instance.code
        with transaction.atomic():
            box = save_with_free_code(form)      # savepoints inside: a clash moves to the next free number
            if not bag_form.is_blank:
                bag = bag_form.save(commit=False)
                bag.box = box
                bag.save()
            formset.instance = box
            formset.save()
            box.refresh_status()
        if box.code != wanted:
            messages.info(request, f"{wanted} was taken in the meantime, so this box is {box.code}.")
        inside = box.items.filter(is_bag=False).count()
        if bag_form.is_blank:
            messages.warning(request, f"Box {box.label} saved WITHOUT a golf bag (owner override) with "
                                      f"{inside} item(s). Every box should hold exactly one bag – add it on the box page.")
        else:
            messages.success(request, f"Box {box.title} added: the bag and {inside} item(s) inside it.")
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("box_detail", args=[box.pk])
            return response
        return redirect("box_detail", pk=box.pk)
    ctx = {"form": form, "bag_form": bag_form, "formset": formset, **catalog_context(request.user),
           **box_id_hint(form.cleaned_data.get("cargo") if form.is_bound and hasattr(form, "cleaned_data") else None)}
    if is_htmx(request):
        return render(request, "tracker/partials/box_modal.html", ctx)
    return render(request, "tracker/box_create.html", ctx)


def save_with_free_code(form, tries=5):
    """Save a new box; if its Box ID was taken meanwhile, move it to the next free number for that cargo/month."""
    box = form.instance
    for _ in range(tries):
        if Box.objects.filter(Q(code=box.code) | Q(cargo=box.cargo, period=box.period, seq=box.seq)).exists():
            box.seq = Box.next_seq(box.cargo, box.period)
            box.code = f"{box.period}-{box.cargo.code}-{box.seq}"
        try:
            with transaction.atomic():
                return form.save()
        except IntegrityError:          # someone saved the same number a moment ago: try the next one
            continue
    raise IntegrityError("Could not find a free Box ID")


@login_required
@roles.require(roles.can_edit_japan)
def box_next_id(request):
    """HTMX: the Box ID field filled with the next monthly ID for the chosen cargo company."""
    cargo = Cargo.objects.filter(pk=request.GET.get("cargo") or 0).first()
    form = BoxCreateForm(initial={"box_id": Box.next_code(cargo)[0]} if cargo else None)
    return render(request, "tracker/partials/box_id_field.html", {"form": form, **box_id_hint(cargo)})


def box_id_hint(cargo):
    if not cargo:
        return {"box_id_hint": "Choose a cargo company – the Box ID fills in by itself."}
    return {"box_id_hint": "Auto: " + Box.next_code(cargo)[3]}


@login_required
@roles.require(lambda u: roles.can_edit_japan(u) or roles.can_use_requests(u))   # item cards and order lines
def item_suggest(request):
    """HTMX, for the "Item (brand + model)" combo box: the dropdown list plus how the text will be saved.

    One list, divided by a line: above it the names typed on the other cards of the open, unsaved box
    (sent by base.html newest first, normalised exactly like saving – nothing is written); below it every
    saved item name A → Z. Typing filters both; the ▾ button asks for the full list (all=1).
    When nothing matches, the last row offers "+ Add '<text>' as new item".
    """
    from .brands import match_key, matches, sort_key
    from .models import parse_item, saved_item_names
    q = next((v for k, v in request.GET.items() if k.endswith("item_name")), "")
    add = next((v for k, v in request.GET.items() if k.endswith("add_brand")), "")
    show_all = request.GET.get("all") == "1"
    plain = parse_item(q)
    parsed = parse_item(q, add_brand=True) if add == "yes" else plain
    new_brand = plain.unknown_brand if add == "yes" else ""     # "Yes, add brand" tapped, saved with the item
    here = {}
    for raw in request.GET.getlist("in_box")[:100]:
        shown = parse_item(raw[:200]).display
        if shown:
            here.setdefault(match_key(shown), shown)
    saved = {k: v for k, v in saved_item_names().items() if k not in here}
    above = [d for k, d in here.items() if matches(q, k)]
    below = sorted((d for k, d in saved.items() if matches(q, k)), key=sort_key)
    add_new = parsed.display if q.strip() and not above and not below else ""
    if show_all:
        above, below = list(here.values()), sorted(saved.values(), key=sort_key)
    did_you_mean = ""
    if parsed.brand_guess:                         # "xxip mp1400" -> "XXIO mp1400"
        did_you_mean = f"{parsed.brand_guess} {q.split(None, 1)[1] if len(q.split()) > 1 else ''}".strip()
    elif parsed.model_guess:
        did_you_mean = f"{parsed.brand} {parsed.model_guess}"
    if did_you_mean:                               # show it the way it would be saved
        did_you_mean = parse_item(did_you_mean).display
    return render(request, "tracker/partials/item_assist.html", {
        "parsed": parsed, "above": above, "below": below, "add_new": add_new, "typed": " ".join(q.split()),
        "did_you_mean": did_you_mean, "add_brand": add, "new_brand": new_brand,
    })


@login_required
@roles.require(roles.can_edit_japan)
def box_item_card(request):
    """Return one more empty item card for the "Add box" modal."""
    try:
        index = max(0, int(request.GET.get("items-TOTAL_FORMS", "0")))
    except ValueError:
        index = 0
    form = ItemFormSet(prefix="items", form_kwargs={"customers": roles.can_use_customers(request.user)}).empty_form
    form.prefix = f"items-{index}"
    return render(request, "tracker/partials/item_card_new.html", {
        "item_form": form, "index": index, "next_total": index + 1,
    })


@login_required
def box_edit(request, pk):
    box = get_object_or_404(visible_boxes(request.user), pk=pk)
    if not roles.can_edit_box(request.user, box):
        raise PermissionDenied("This box can no longer be edited – the Myanmar check has started.")
    form = BoxForm(request.POST or None, instance=box, user=request.user)
    if form.is_valid():
        status_changed = "status" in form.changed_data
        box = form.save()
        if not status_changed:  # a manual status choice wins over the automatic rules
            box.refresh_status()
        messages.success(request, f"Box {box.label} saved.")
        return redirect("box_detail", pk=box.pk)
    return render(request, "tracker/box_form.html", {"form": form, "box": box})


def bag_context(box):
    bags = list(box.items.filter(is_bag=True).select_related("receive_check").prefetch_related("requests"))
    return {"bags": bags, "bag_problem": "none" if not bags else ("many" if len(bags) > 1 else "")}


@login_required
def box_detail(request, pk):
    box = get_object_or_404(with_bag(visible_boxes(request.user)).select_related("cargo"), pk=pk)
    items = box.items.filter(is_bag=False).select_related("receive_check").prefetch_related("requests")
    return render(request, "tracker/box_detail.html", {
        "box": box,
        "can_edit_this_box": roles.can_edit_box(request.user, box),
        "ships_later": not box.can_be_received_on(timezone.localdate()),
        "can_edit_items": roles.can_edit_box_items(request.user, box),
        "japan_locked": roles.can_edit_japan(request.user) and not roles.can_edit_box_items(request.user, box),
        "items": items,
        "item_brands": sorted({i.brand for i in box.items.exclude(brand="")}, key=str.lower),
        **bag_context(box),
        "extras": box.extra_items.all(),
        "summary": box.summary(),
        **catalog_context(request.user),
    })


@login_required
@roles.require(roles.can_edit_japan)
def box_bag(request, pk):
    """Add the golf bag to a box that has none (e.g. an old box), or edit its bag."""
    box = get_object_or_404(visible_boxes(request.user), pk=pk)
    _require_box_items(request.user, box)
    bag = box.items.filter(is_bag=True).first()
    form = BagForm(request.POST or None, instance=bag, prefix="bag", customers=roles.can_use_customers(request.user))
    if request.method == "POST" and form.is_valid():
        bag = form.save(commit=False)
        bag.box = box
        bag.save()
        box.refresh_status()
        return _box_fragments(request, box, "tracker/partials/bag_saved.html", {
            **bag_context(box), "can_edit_items": True,
        })
    response = render(request, "tracker/partials/bag_form.html", {"box": box, "bag_form": form, "bag": bag})
    if request.method == "POST":  # show the errors in the open form, not over the bag section
        response["HX-Retarget"], response["HX-Reswap"] = "#bag-form-slot", "innerHTML"
    return response


class ReceivedBeforeShipped(Exception):
    pass


def _start_check(box):
    if not box.myanmar_received_date and not box.can_be_received_on(timezone.localdate()):
        raise ReceivedBeforeShipped(
            f"{box.label} ships from Japan on {box.japan_ship_date.day} {box.japan_ship_date:%b %Y} – "
            "it can't be received in Myanmar before that."
        )
    if not box.myanmar_received_date:
        box.myanmar_received_date = timezone.localdate()
        box.save(update_fields=["myanmar_received_date", "updated_at"])
    box.refresh_status()


@login_required
@roles.require(roles.can_edit_japan)
@require_POST
def box_mark_shipped(request, pk):
    """Japan sender: one tap to send the box. From now on Myanmar can see it."""
    box = get_object_or_404(visible_boxes(request.user), pk=pk)
    _require_box_items(request.user, box)
    if box.status == Box.Status.PACKED:
        box.status = Box.Status.SHIPPED
        if not box.japan_ship_date:
            box.japan_ship_date = timezone.localdate()
        box.save(update_fields=["status", "japan_ship_date", "updated_at"])
        messages.success(request, f"{box.label} marked as shipped – Myanmar can now see it.")
    return redirect("box_detail", pk=box.pk)


@login_required
@roles.require(roles.can_check)
@require_POST
def box_start_check(request, pk):
    """Myanmar checker opens the box: records today's arrival and shows the check buttons."""
    box = get_object_or_404(visible_boxes(request.user), pk=pk)
    try:
        _start_check(box)
    except ReceivedBeforeShipped as exc:
        messages.error(request, str(exc))
        return redirect("box_detail", pk=box.pk)
    messages.success(request, f"Myanmar check started for {box.label}. Check the golf bag, then each item inside it.")
    return redirect(reverse("box_detail", args=[box.pk]) + "#bag-section")


def _box_fragments(request, box, main_template, main_ctx):
    """Render a main fragment plus out-of-band summary/status updates."""
    box.refresh_from_db()
    ctx = {
        **main_ctx,
        "box": box,
        "summary": box.summary(),
        "oob": True,
    }
    return render(request, main_template, ctx)


# --- Receive checks (HTMX) ---------------------------------------------------
@login_required
@roles.require(roles.can_check)
@require_POST
def item_check(request, pk):
    item = get_object_or_404(visible_items(request.user).select_related("box__cargo"), pk=pk)
    result = request.POST.get("result")
    if result not in ReceiveCheck.Result.values:
        return HttpResponse("Bad result", status=400)
    try:
        _start_check(item.box)
    except ReceivedBeforeShipped as exc:
        return HttpResponse(str(exc), status=409)
    if result == ReceiveCheck.Result.MATCHED:
        received = item.qty
    elif result == ReceiveCheck.Result.MISSING:
        received = 0
    else:
        try:
            received = max(0, int(request.POST.get("received_qty", "")))
        except ValueError:
            return render(request, "tracker/partials/item_wrong_qty.html", {
                "item": item, "error": "Enter the quantity you received.",
            })
        if received == item.qty:
            result = ReceiveCheck.Result.MATCHED
    ReceiveCheck.objects.update_or_create(
        japan_item=item,
        defaults={
            "result": result,
            "received_qty": received,
            "note": request.POST.get("note", "")[:255],
            "checked_by": request.user,
        },
    )
    item.box.refresh_status()
    item = JapanItem.objects.select_related("receive_check").get(pk=item.pk)
    return _box_fragments(request, item.box, "tracker/partials/item_row.html", {"item": item})


@login_required
@roles.require(roles.can_check)
@require_POST
def item_check_clear(request, pk):
    item = get_object_or_404(visible_items(request.user).select_related("box"), pk=pk)
    ReceiveCheck.objects.filter(japan_item=item).delete()
    item.box.refresh_status()
    item = JapanItem.objects.get(pk=item.pk)
    return _box_fragments(request, item.box, "tracker/partials/item_row.html", {"item": item})


@login_required
@roles.require(roles.can_check)
def item_wrong_qty_form(request, pk):
    item = get_object_or_404(visible_items(request.user), pk=pk)
    return render(request, "tracker/partials/item_wrong_qty.html", {"item": item})


@login_required
def item_row(request, pk):
    item = get_object_or_404(visible_items(request.user).select_related("receive_check"), pk=pk)
    return render(request, "tracker/partials/item_row.html", {"item": item, "box": item.box})


# --- Japan items (manual add / delete) ------------------------------------------
def _require_box_items(user, box):
    if not roles.can_edit_box_items(user, box):
        raise PermissionDenied("This box is locked – the Myanmar check has started.")


@login_required
@roles.require(roles.can_edit_japan)
def item_add(request, pk):
    box = get_object_or_404(visible_boxes(request.user), pk=pk)
    _require_box_items(request.user, box)
    customers = roles.can_use_customers(request.user)
    form = JapanItemForm(request.POST or None, customers=customers)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.box = box
        item.save()
        box.refresh_status()
        return _box_fragments(request, box, "tracker/partials/item_added.html", {
            "item": item, "form": JapanItemForm(customers=customers), **catalog_context(request.user),
        })
    return render(request, "tracker/partials/item_form.html", {
        "box": box, "form": form, **catalog_context(request.user),
    })


@login_required
@roles.require(roles.can_edit_japan)
@require_POST
def item_copy(request, pk):
    """Duplicate a Japan list item (without its check or linked requests)."""
    item = get_object_or_404(visible_items(request.user).select_related("box"), pk=pk)
    box = item.box
    _require_box_items(request.user, box)
    if item.is_bag:
        return HttpResponse("A box holds exactly one golf bag – it can't be copied.", status=400)
    item.pk = None
    item._state.adding = True
    item.save()
    box.refresh_status()
    new = JapanItem.objects.select_related("box").get(pk=item.pk)
    return _box_fragments(request, box, "tracker/partials/item_copied.html", {"item": new})


@login_required
@roles.require(roles.can_edit_japan)
@require_POST
def item_delete(request, pk):
    item = get_object_or_404(visible_items(request.user).select_related("box"), pk=pk)
    box = item.box
    _require_box_items(request.user, box)
    if item.is_bag and not roles.is_manager(request.user):
        raise PermissionDenied("Every box holds its golf bag – edit the bag instead of deleting it.")
    item.delete()
    if item.is_bag:  # owner removed the bag: refresh the bag section too
        return _box_fragments(request, box, "tracker/partials/bag_saved.html", {
            **bag_context(box), "can_edit_items": True, "bag_form_closed": True,
        })
    box.refresh_status()
    return _box_fragments(request, box, "tracker/partials/oob_only.html", {})


# --- Extra items (HTMX) ------------------------------------------------------
@login_required
@roles.require(roles.can_check)
def extra_add(request, pk):
    box = get_object_or_404(visible_boxes(request.user), pk=pk)
    form = ExtraItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        extra = form.save(commit=False)
        extra.box = box
        extra.added_by = request.user
        extra.save()
        return _box_fragments(request, box, "tracker/partials/extra_added.html", {"extra": extra})
    return render(request, "tracker/partials/extra_form.html", {"box": box, "form": form})


@login_required
@roles.require(roles.can_check)
@require_POST
def extra_delete(request, pk):
    extra = get_object_or_404(
        ExtraItem.objects.select_related("box").filter(box__in=visible_boxes(request.user)), pk=pk
    )
    box = extra.box
    extra.delete()
    return _box_fragments(request, box, "tracker/partials/oob_only.html", {})


# --- Customer orders (requests, managed from the customer page) --------------
def render_order_card(request, req, **extra):
    """One order card (partials/request_card.html): items, status, and – for people who may see payments – its
    Total / Paid / Balance, payments and "+ Add payment"."""
    add_order_money([req])
    return render(request, "tracker/partials/request_card.html", {
        "r": req, "status_choices": CustomerRequest.Status.choices, **extra,
    })


def _order(pk):
    return get_object_or_404(CustomerRequest.objects.prefetch_related("lines", "payments__method",
                                                                      "payments__recorded_by"), pk=pk)


@login_required
@roles.require(roles.can_use_payments)
def order_payment_add(request, pk):
    """"+ Add payment" on an order card: GET shows the form inside the card, POST saves and redraws the card."""
    req = _order(pk)
    add_order_money([req])
    form = OrderPaymentForm(request.POST or None, order=req)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.order, payment.recorded_by = req, request.user
        payment.save()
        return render_order_card(request, _order(pk))
    response = render(request, "tracker/partials/order_payment_form.html", {"r": req, "form": form})
    if request.method == "POST":                      # errors: show them in the open form, not over the card
        response["HX-Retarget"], response["HX-Reswap"] = f"#pay-slot-{pk}", "innerHTML"
    return response


@login_required
@roles.require(roles.can_use_requests)
def request_status(request, pk):
    """The order card's status dropdown: Ordered / Arrived / Delivered / Cancelled, set by hand.
    GET just returns the card (Cancel on the Edit form)."""
    req = get_object_or_404(CustomerRequest, pk=pk)
    status = request.POST.get("status")
    if request.method == "POST" and status in CustomerRequest.Status.values:
        req.status = status
        req.save(update_fields=["status", "updated_at"])
    return render_order_card(request, req)


@login_required
@roles.require(roles.can_use_requests)
def order_edit(request, pk):
    """"Edit" on an order card: its items (+ Add item, remove) and note. Prices only for people who see payments."""
    req = _order(pk)
    form = OrderEditForm(request.POST or None, instance=req)
    lines = OrderLineFormSet(request.POST or None, instance=req, prefix="lines")
    if req.lines.all():
        lines.extra = 0                                     # no empty row under existing items; "+ Add item" adds one
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        with transaction.atomic():
            form.save()
            _save_lines(lines, request.user)
            req.sync_from_lines()
        return render_order_card(request, _order(pk))
    return render(request, "tracker/partials/order_edit.html", {"r": req, "form": form, "lines": lines})


def _save_lines(lines, user):
    """Save an order's item lines; people who can't see prices never change them."""
    keep_prices = not roles.can_use_payments(user)
    for f in lines.forms:
        if keep_prices and f.instance.pk and "price_ks" in f.changed_data:
            f.instance.price_ks = f.initial.get("price_ks")
    lines.save()


# --- Customers ------------------------------------------------------------------
CUSTOMER_PAGE_SIZE = 25
CUSTOMER_SORTS = {"name", "orders", "updated", "balance"}


def _name_key(name):
    return " ".join((name or "").split()).lower()


def customer_rows(user, q="", viber="", orders="", money="", date_from=None, date_to=None):
    """Customers with their order numbers, status tag and last update (latest order date).

    date_from / date_to (order date range, either may be None): only customers with an order on those days.

    Orders are matched by name (requests + Japan items). Computed in Python:
    a shop has dozens/hundreds of customers, so two queries cover everything.
    """
    qs = Customer.objects.all()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(address__icontains=q))
    if viber in ("yes", "1"):
        qs = qs.filter(viber_member=True)
    elif viber == "no":
        qs = qs.filter(viber_member=False)
    customers = list(qs)

    reqs, items = {}, {}
    all_reqs = list(CustomerRequest.objects.exclude(customer="").values("pk", "customer", "status", "order_date",
                                                                      "paid_old_order"))
    sums = order_totals([x["pk"] for x in all_reqs])
    for r in all_reqs:
        reqs.setdefault(_name_key(r["customer"]), []).append(r)
    for i in visible_items(user).exclude(customer="").values("customer", "box__status", "box__updated_at"):
        items.setdefault(_name_key(i["customer"]), []).append(i)

    for c in customers:
        r, it = reqs.get(_name_key(c.name), []), items.get(_name_key(c.name), [])
        c.request_count, c.item_count = len(r), len(it)
        c.order_count = len(r) + len(it)
        S = CustomerRequest.Status
        c.open_count = sum(x["status"] in (S.ORDERED, S.ARRIVED) for x in r)      # not delivered (or cancelled) yet
        if any(x["status"] == S.ORDERED for x in r):
            c.tag = ("waiting", "Waiting")
        elif any(x["status"] == S.ARRIVED for x in r):
            c.tag = ("arrived", "Arrived")
        elif r:
            c.tag = ("delivered", "All delivered")
        else:
            c.tag = ("none", "No orders")
        # last update = the latest order date; customers with only box items fall back to the box's last change
        days = [x["order_date"] for x in r] or [timezone.localtime(x["box__updated_at"]).date() for x in it]
        c.last_update = max(days) if days else None
        c.in_range = any((not date_from or x["order_date"] >= date_from) and (not date_to or x["order_date"] <= date_to)
                         for x in r)
        # money (Ks): the sum of the order balances, old orders included
        c.balance_ks = unpriced = 0
        for x in r:
            total, priced, paid = sums[x["pk"]]
            key, balance = order_pay_state(total, priced, paid, x["paid_old_order"], x["status"] == S.CANCELLED)
            c.balance_ks += balance
            unpriced += key == "no_price"
        c.money = money_status(c.balance_ks, unpriced, bool(r))

    if orders == "open":
        customers = [c for c in customers if c.open_count]
    elif orders == "none":
        customers = [c for c in customers if not c.open_count]
    if money == "owes":
        customers = [c for c in customers if c.balance_ks > 0]
    elif money == "clear":
        customers = [c for c in customers if c.money[0] == "clear"]
    if date_from or date_to:
        customers = [c for c in customers if c.in_range]
    return customers


def sort_customers(customers, sort, direction):
    desc = direction == "desc"
    if sort == "orders":
        customers.sort(key=lambda c: (c.order_count, c.name.lower()), reverse=desc)
    elif sort == "balance":
        customers.sort(key=lambda c: (c.balance_ks, c.name.lower()), reverse=desc)
    elif sort == "updated":
        dated = sorted((c for c in customers if c.last_update), key=lambda c: c.last_update, reverse=desc)
        customers[:] = dated + [c for c in customers if not c.last_update]  # never-updated last
    else:
        customers.sort(key=lambda c: c.name.lower(), reverse=desc)
    customers.sort(key=lambda c: not c.favourite)  # stable: favourites first, order kept
    return customers


def _customer_table_context(request, params):
    from urllib.parse import urlencode

    sort = params["sort"] if params["sort"] in CUSTOMER_SORTS else "name"
    direction = "desc" if params["dir"] == "desc" else "asc"
    rows = sort_customers(customer_rows(request.user, params["q"], params["viber"], params["orders"],
                                        params["money"], _day(params["from"]), _day(params["to"])), sort, direction)
    try:
        page = max(1, int(params.get("page") or 1))
    except ValueError:
        page = 1
    shown_to = page * CUSTOMER_PAGE_SIZE
    base = {k: params[k] for k in ("q", "viber", "orders", "money", "from", "to") if params[k]}

    def url(**extra):
        query = urlencode({**base, **{k: v for k, v in extra.items() if v}})
        return reverse("customer_list") + (f"?{query}" if query else "")

    sort_urls = {}
    for col in CUSTOMER_SORTS:
        # click the current column again to flip the direction; a new column starts sensible
        if col == sort:
            new_dir = "asc" if direction == "desc" else "desc"
        else:
            new_dir = "desc" if col in ("orders", "updated", "balance") else "asc"
        sort_urls[col] = url(sort=col, dir=new_dir)
    return {
        **params,  # raw filter values first; computed values below win
        "customers": rows[shown_to - CUSTOMER_PAGE_SIZE:shown_to],
        "match_count": len(rows),
        "shown_count": min(shown_to, len(rows)),
        "more_url": url(sort=sort, dir=direction, page=page + 1) if shown_to < len(rows) else "",
        "sort": sort,
        "dir": direction,
        "sort_urls": sort_urls,
        "page": page,
        "date_from": params["from"],
        "date_to": params["to"],
        "today": yangon_today(),
    }


def _day(value):
    """'2026-10-18' → date, anything else → None."""
    try:
        return datetime.date.fromisoformat(value or "")
    except ValueError:
        return None


def _customer_params(source):
    return {
        "q": source.get("q", "").strip(),
        "viber": source.get("viber", "") if source.get("viber", "") in ("yes", "no", "1") else "",
        "orders": source.get("orders", "") if source.get("orders", "") in ("open", "none") else "",
        "money": source.get("money", "") if source.get("money", "") in ("owes", "clear") else "",
        "from": source.get("from", "") if _day(source.get("from", "")) else "",       # order date range
        "to": source.get("to", "") if _day(source.get("to", "")) else "",
        "sort": source.get("sort", ""),
        "dir": source.get("dir", ""),
        "page": source.get("page", ""),
    }


@login_required
@roles.require(roles.can_use_customers)
def customer_list(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, f"Customer {customer.name} added.")
        return redirect("customer_detail", pk=customer.pk)

    ctx = _customer_table_context(request, _customer_params(request.GET))
    if is_htmx(request) and ctx["page"] > 1:
        return render(request, "tracker/partials/customer_rows.html", ctx)  # "Load more"
    if is_htmx(request) and request.headers.get("HX-Target") == "customer-results":
        return render(request, "tracker/partials/customer_results.html", ctx)
    ctx.update(form=form, show_form=request.method == "POST", total_count=Customer.objects.count())
    return render(request, "tracker/customer_list.html", ctx)


@login_required
@roles.require(roles.can_use_customers)
@require_POST
def customer_favourite(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    customer.favourite = not customer.favourite
    customer.save(update_fields=["favourite", "updated_at"])
    params = _customer_params(request.POST)
    params["page"] = ""
    return render(request, "tracker/partials/customer_results.html", _customer_table_context(request, params))


@login_required
@roles.require(roles.can_use_customers)
def customer_detail(request, pk, order_form=None, line_forms=None):
    customer = get_object_or_404(Customer, pk=pk)
    reqs = add_order_money(customer.requests().prefetch_related("lines", "payments__method", "payments__recorded_by"))
    items = (visible_items(request.user).filter(customer__iexact=customer.name)
             .select_related("box__cargo", "receive_check").order_by("-box__created_at", "-id"))
    return render(request, "tracker/customer_detail.html", {
        "customer": customer,
        "requests": reqs,
        "items": items,
        "open_count": sum(r.status in (CustomerRequest.Status.ORDERED, CustomerRequest.Status.ARRIVED) for r in reqs),
        "money": customer_money(reqs),
        "payments": sorted((p for r in reqs for p in r.payments.all()), key=lambda p: (p.date, p.created_at), reverse=True),
        "status_choices": CustomerRequest.Status.choices,
        "order_form": order_form or CustomerOrderForm(),
        "line_forms": line_forms or OrderLineFormSet(prefix="lines"),
        "show_order_form": order_form is not None or request.GET.get("new_order") == "1",
        **catalog_context(request.user),
    })


@login_required
@roles.require(roles.can_use_customers)
@require_POST
def customer_new_order(request, pk):
    """"+ New order" on the customer page: one or more item lines (+ Add item)."""
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerOrderForm(request.POST)
    lines = OrderLineFormSet(request.POST, prefix="lines")
    if form.is_valid() and lines.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            order.customer, order.status = customer.name, CustomerRequest.Status.ORDERED
            order.item_name = "…"
            order.save()
            lines.instance = order
            if not roles.can_use_payments(request.user):
                for f in lines.forms:
                    f.instance.price_ks = None
            lines.save()
            order.sync_from_lines()
        messages.success(request, f"New order for {customer.name}: {order.item_name}.")
        return redirect(reverse("customer_detail", args=[customer.pk]) + "#orders")
    return customer_detail(request, pk, order_form=form, line_forms=lines)


@login_required
@roles.require(roles.can_edit_payments)
@require_POST
def payment_delete(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    order = payment.order
    payment.delete()
    if is_htmx(request):
        return render_order_card(request, _order(order.pk))
    messages.success(request, f"Deleted the payment of {payment.amount_ks:,} Ks.")
    customer = Customer.ensure(order.customer)
    return redirect(reverse("customer_detail", args=[customer.pk]) + "#orders")


@login_required
@roles.require(roles.can_edit_payments)
@require_POST
def customer_settle(request, pk):
    """Owner / admin: "Mark as paid (old order)" for the ticked orders."""
    customer = get_object_or_404(Customer, pk=pk)
    ids = [int(x) for x in request.POST.getlist("orders") if x.isdigit()]
    done = mark_old_orders_paid(customer.requests().filter(pk__in=ids))
    if done:
        messages.success(request, f"Marked {done} order(s) as paid (old order).")
    else:
        messages.info(request, "Tick the old orders to mark as paid.")
    return redirect(reverse("customer_detail", args=[customer.pk]) + "#orders")


@login_required
@roles.require(roles.can_use_customers)
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    old_name = customer.name
    form = CustomerForm(request.POST or None, instance=customer)
    if form.is_valid():
        with transaction.atomic():
            customer = form.save()
            if customer.name != old_name:  # keep their orders linked after a rename
                CustomerRequest.objects.filter(customer__iexact=old_name).update(customer=customer.name)
                JapanItem.objects.filter(customer__iexact=old_name).update(customer=customer.name)
        messages.success(request, f"{customer.name} saved.")
        return redirect("customer_detail", pk=customer.pk)
    return render(request, "tracker/customer_form.html", {"form": form, "customer": customer})


@login_required
@roles.require(roles.can_use_customers)
def customer_by_name(request):
    """Links from requests / items, which only know the customer's name."""
    customer = Customer.ensure(request.GET.get("name", ""))
    if not customer:
        return redirect("customer_list")
    return redirect("customer_detail", pk=customer.pk)


# --- Cargo history ------------------------------------------------------------
@login_required
def cargo_history(request):
    visible = visible_boxes(request.user)
    shown = Q() if roles.sees_unshipped_boxes(request.user) else ~Q(boxes__status=Box.Status.PACKED)
    cargos = list(Cargo.objects.annotate(
        box_total=Count("boxes", filter=shown, distinct=True),
        in_transit=Count("boxes", filter=Q(boxes__status=Box.Status.SHIPPED), distinct=True),
        checked=Count("boxes", filter=Q(boxes__status=Box.Status.CHECKED), distinct=True),
    ))
    problem_ids = set(Box.with_mismatches().values_list("pk", flat=True))
    for cargo in cargos:
        dated = visible.filter(cargo=cargo).exclude(japan_ship_date=None).exclude(myanmar_received_date=None)
        days = [(b.myanmar_received_date - b.japan_ship_date).days for b in dated]
        cargo.avg_days = round(sum(days) / len(days)) if days else None
        cargo.issues = visible.filter(cargo=cargo, pk__in=problem_ids).count()

    selected = Cargo.objects.filter(code=request.GET.get("cargo", "").upper()).first()
    boxes = boxes_with_counts(visible.filter(cargo=selected) if selected else visible)
    boxes = boxes.order_by("-japan_ship_date", "-created_at")
    for box in boxes:
        box.transit_days = (
            (box.myanmar_received_date - box.japan_ship_date).days
            if box.japan_ship_date and box.myanmar_received_date else None
        )
    ctx = {"cargos": cargos, "selected": selected, "boxes": boxes}
    if is_htmx(request) and request.headers.get("HX-Target") == "history-results":
        return render(request, "tracker/partials/history_results.html", ctx)
    return render(request, "tracker/cargo_history.html", ctx)


# --- CSV import --------------------------------------------------------------
@login_required
@roles.require(roles.can_import_csv)
def csv_import(request):
    form = CSVImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        result = import_japan_csv(form.cleaned_data["file"], replace=form.cleaned_data["replace"],
                                  allow_bag_exception=form.cleaned_data["allow_bag_exception"],
                                  customers=roles.can_use_customers(request.user))
        if not result.errors:
            bags = f" and {result.created_bags} golf bag(s)" if result.created_bags else ""
            messages.success(
                request,
                f"Imported {result.created_items} item(s){bags} into {len(result.boxes)} box(es).",
            )
            if result.warnings:
                messages.warning(request, f"{len(result.warnings)} box(es) don't have exactly one golf bag "
                                          "(owner override) – see the list below.")
    return render(request, "tracker/csv_import.html", {"form": form, "result": result})


@login_required
@roles.require(roles.can_import_csv)
def csv_template(request):
    response = HttpResponse(template_csv(customers=roles.can_use_customers(request.user)), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="japan_list_template.csv"'
    return response


# --- Phone home-screen app ---------------------------------------------------
def web_manifest(request):
    """Lets phones add the tracker to the home screen with the 3D cover logo as app icon."""
    data = {
        "name": "AGOM Cargo Tracker",
        "short_name": "AGOM Cargo",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f4f6ef",
        "theme_color": "#C4E6C9",
        "icons": [
            {"src": static("img/display/app-icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("img/display/app-icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    }
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Content-Type"] = "application/manifest+json"
    return response
