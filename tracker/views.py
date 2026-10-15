from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.templatetags.static import static
from django.views.decorators.http import require_POST

from . import roles
from .csv_import import import_japan_csv, template_csv
from .forms import (
    BoxCreateForm,
    BoxFilterForm,
    BoxForm,
    ItemFormSet,
    CSVImportForm,
    CustomerForm,
    CustomerOrderForm,
    CustomerRequestForm,
    ExtraItemForm,
    JapanItemForm,
)
from .models import Box, Cargo, Catalog, Customer, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck

ITEM_CATEGORIES = ["Club", "Club set", "Bag", "Balls", "Shoes", "Apparel", "Accessory"]
CLUB_TYPES = ["Driver", "Fairway wood", "Utility", "Iron set", "Iron", "Wedge", "Putter"]
BRANDS = [
    "Bridgestone", "Callaway", "Cleveland", "Cobra", "Honma", "Mizuno", "Odyssey", "Ping",
    "PRGR", "Scotty Cameron", "Srixon", "TaylorMade", "Titleist", "XXIO", "Yamaha", "Yonex",
]


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def catalog_context():
    """Values for the <datalist> dropdowns used by item / request forms."""
    entries = list(Catalog.objects.all())
    lofts = sorted({l for c in entries for l in c.loft_list}, key=lambda v: (len(v), v))
    return {
        "catalog_types": sorted({c.type for c in entries} | set(CLUB_TYPES)),
        "catalog_models": [c.brand_model for c in entries],
        "catalog_lofts": lofts,
        "item_categories": ITEM_CATEGORIES,
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


def boxes_with_counts(qs=None):
    qs = qs if qs is not None else Box.objects.all()
    return qs.select_related("cargo").annotate(
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
@login_required
def dashboard(request):
    boxes = visible_boxes(request.user)
    in_transit = boxes_with_counts(boxes.filter(status=Box.Status.SHIPPED))
    needs_review = boxes_with_counts(
        boxes.filter(
            Q(status=Box.Status.ARRIVED) | Q(pk__in=Box.with_mismatches().values("pk"))
        )
    )
    open_requests = CustomerRequest.objects.filter(status=CustomerRequest.Status.OPEN)
    return render(request, "tracker/dashboard.html", {
        "cargo_count": Cargo.objects.count(),
        "total_box_count": boxes.count(),
        "checked_box_count": boxes.filter(status=Box.Status.CHECKED).count(),
        "mismatch_count": boxes.filter(pk__in=Box.with_mismatches().values("pk")).count(),
        "in_transit": in_transit,
        "needs_review": needs_review,
        "open_requests": open_requests[:8],
        "open_request_count": open_requests.count(),
        "recent": boxes_with_counts(boxes.order_by("-updated_at"))[:5],
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
    ctx = {"form": form, "boxes": boxes_with_counts(qs)}
    if is_htmx(request) and request.headers.get("HX-Target") == "box-results":
        return render(request, "tracker/partials/box_results.html", ctx)
    return render(request, "tracker/box_list.html", ctx)


@login_required
@roles.require(roles.can_edit_japan)
def box_create(request):
    """"Add box" – a modal over the current page when opened via HTMX."""
    form = BoxCreateForm(request.POST or None)
    formset = ItemFormSet(request.POST or None, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            box = form.save()
            formset.instance = box
            formset.save()
            box.refresh_status()
        messages.success(request, f"Box {box.label} added with {box.items.count()} item(s).")
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("box_detail", args=[box.pk])
            return response
        return redirect("box_detail", pk=box.pk)
    ctx = {"form": form, "formset": formset, **catalog_context()}
    if is_htmx(request):
        return render(request, "tracker/partials/box_modal.html", ctx)
    return render(request, "tracker/box_create.html", ctx)


@login_required
@roles.require(roles.can_edit_japan)
def box_item_card(request):
    """Return one more empty item card for the "Add box" modal."""
    try:
        index = max(0, int(request.GET.get("items-TOTAL_FORMS", "0")))
    except ValueError:
        index = 0
    form = ItemFormSet(prefix="items").empty_form
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


@login_required
def box_detail(request, pk):
    box = get_object_or_404(visible_boxes(request.user).select_related("cargo"), pk=pk)
    items = box.items.select_related("receive_check").prefetch_related("requests")
    return render(request, "tracker/box_detail.html", {
        "box": box,
        "can_edit_this_box": roles.can_edit_box(request.user, box),
        "ships_later": not box.can_be_received_on(timezone.localdate()),
        "can_edit_items": roles.can_edit_box_items(request.user, box),
        "japan_locked": roles.can_edit_japan(request.user) and not roles.can_edit_box_items(request.user, box),
        "items": items,
        "extras": box.extra_items.all(),
        "summary": box.summary(),
        **catalog_context(),
    })


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
    messages.success(request, f"Myanmar check started for {box.label}. Tap each item below.")
    return redirect(reverse("box_detail", args=[box.pk]) + "#items")


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
    form = JapanItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.box = box
        item.save()
        box.refresh_status()
        return _box_fragments(request, box, "tracker/partials/item_added.html", {
            "item": item, "form": JapanItemForm(), **catalog_context(),
        })
    return render(request, "tracker/partials/item_form.html", {
        "box": box, "form": form, **catalog_context(),
    })


@login_required
@roles.require(roles.can_edit_japan)
@require_POST
def item_delete(request, pk):
    item = get_object_or_404(visible_items(request.user).select_related("box"), pk=pk)
    box = item.box
    _require_box_items(request.user, box)
    item.delete()
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


# --- Customer requests -------------------------------------------------------
@login_required
@roles.require(roles.can_use_requests)
def request_list(request):
    status = request.GET.get("status", "active")
    qs = CustomerRequest.objects.select_related("box__cargo", "japan_item")
    if status == "active":
        qs = qs.exclude(status=CustomerRequest.Status.FULFILLED)
    elif status in CustomerRequest.Status.values:
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(customer__icontains=q) | Q(item_name__icontains=q))

    form = CustomerRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        req = form.save()
        messages.success(request, f"Request for {req.customer} added.")
        return redirect("request_list")

    ctx = {
        "requests": qs,
        "form": form,
        "status": status,
        "q": q,
        "status_choices": CustomerRequest.Status.choices,
        "show_form": request.method == "POST",
        **catalog_context(),
    }
    if is_htmx(request) and request.headers.get("HX-Target") == "request-results":
        return render(request, "tracker/partials/request_results.html", ctx)
    return render(request, "tracker/request_list.html", ctx)


@login_required
@roles.require(roles.can_use_requests)
def request_edit(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    form = CustomerRequestForm(request.POST or None, instance=req)
    if form.is_valid():
        form.save()
        messages.success(request, "Request saved.")
        return redirect("request_list")
    return render(request, "tracker/request_form.html", {"form": form, "req": req, **catalog_context()})


@login_required
@roles.require(roles.can_use_requests)
@require_POST
def request_status(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    status = request.POST.get("status")
    if status in CustomerRequest.Status.values:
        req.status = status
        req.save(update_fields=["status", "updated_at"])
    return render(request, "tracker/partials/request_card.html", {
        "r": req, "status_choices": CustomerRequest.Status.choices,
    })


def _suggestions(req, user):
    """Japan items that look like this request and are not yet attached."""
    qs = visible_items(user).select_related("box__cargo").filter(requests__isnull=True)
    words = [w for w in req.item_name.split() if len(w) > 2][:3]
    cond = Q()
    for w in words:
        cond &= Q(item_name__icontains=w)
    if not words:
        return JapanItem.objects.none()
    qs = qs.filter(cond)
    if req.flex:
        qs = qs.filter(Q(flex=req.flex) | Q(flex=""))
    return qs.exclude(box__status=Box.Status.CHECKED).order_by("-box__id")[:5]


@login_required
@roles.require(roles.can_use_requests)
def request_attach(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    if request.method == "POST":
        item = get_object_or_404(visible_items(request.user), pk=request.POST.get("japan_item"))
        req.attach(item)
        return render(request, "tracker/partials/request_card.html", {
            "r": req, "status_choices": CustomerRequest.Status.choices,
        })
    boxes = visible_boxes(request.user).select_related("cargo").exclude(status=Box.Status.CHECKED)
    return render(request, "tracker/partials/request_attach.html", {
        "r": req, "suggestions": _suggestions(req, request.user), "boxes": boxes,
    })


@login_required
@roles.require(roles.can_use_requests)
def request_attach_items(request):
    box = visible_boxes(request.user).filter(pk=request.GET.get("box") or 0).first()
    req_pk = request.GET.get("req", "")
    return render(request, "tracker/partials/attach_item_options.html", {
        "items": list(box.items.all()) if box else None,
        "req_pk": req_pk if req_pk.isdigit() else "",
    })


@login_required
@roles.require(roles.can_use_requests)
@require_POST
def request_detach(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    req.detach()
    return render(request, "tracker/partials/request_card.html", {
        "r": req, "status_choices": CustomerRequest.Status.choices,
    })


# --- Customers ------------------------------------------------------------------
CUSTOMER_PAGE_SIZE = 25
CUSTOMER_SORTS = {"name", "orders", "updated"}


def _name_key(name):
    return " ".join((name or "").split()).lower()


def customer_rows(user, q="", viber="", orders=""):
    """Customers with their order numbers, status tag and last update.

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
    for r in CustomerRequest.objects.exclude(customer="").values("customer", "status", "updated_at"):
        reqs.setdefault(_name_key(r["customer"]), []).append(r)
    for i in visible_items(user).exclude(customer="").values("customer", "box__status", "box__updated_at"):
        items.setdefault(_name_key(i["customer"]), []).append(i)

    for c in customers:
        r, it = reqs.get(_name_key(c.name), []), items.get(_name_key(c.name), [])
        c.request_count, c.item_count = len(r), len(it)
        c.order_count = len(r) + len(it)
        c.open_count = sum(x["status"] != CustomerRequest.Status.FULFILLED for x in r)
        c.in_box_count = sum(x["box__status"] != Box.Status.CHECKED for x in it)
        if c.open_count:
            c.tag = ("waiting", "Waiting")
        elif c.in_box_count:
            c.tag = ("in_box", "In box")
        elif c.order_count:
            c.tag = ("delivered", "All delivered")
        else:
            c.tag = ("none", "No orders")
        stamps = [x["updated_at"] for x in r] + [x["box__updated_at"] for x in it]
        c.last_update = max(stamps) if stamps else None

    if orders == "open":
        customers = [c for c in customers if c.open_count]
    elif orders == "none":
        customers = [c for c in customers if not c.open_count]
    return customers


def sort_customers(customers, sort, direction):
    desc = direction == "desc"
    if sort == "orders":
        customers.sort(key=lambda c: (c.order_count, c.name.lower()), reverse=desc)
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
    rows = sort_customers(customer_rows(request.user, params["q"], params["viber"], params["orders"]),
                          sort, direction)
    try:
        page = max(1, int(params.get("page") or 1))
    except ValueError:
        page = 1
    shown_to = page * CUSTOMER_PAGE_SIZE
    base = {k: params[k] for k in ("q", "viber", "orders") if params[k]}

    def url(**extra):
        query = urlencode({**base, **{k: v for k, v in extra.items() if v}})
        return reverse("customer_list") + (f"?{query}" if query else "")

    sort_urls = {}
    for col in CUSTOMER_SORTS:
        # click the current column again to flip the direction; a new column starts sensible
        if col == sort:
            new_dir = "asc" if direction == "desc" else "desc"
        else:
            new_dir = "desc" if col in ("orders", "updated") else "asc"
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
    }


def _customer_params(source):
    return {
        "q": source.get("q", "").strip(),
        "viber": source.get("viber", "") if source.get("viber", "") in ("yes", "no", "1") else "",
        "orders": source.get("orders", "") if source.get("orders", "") in ("open", "none") else "",
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
def customer_detail(request, pk, order_form=None):
    customer = get_object_or_404(Customer, pk=pk)
    reqs = customer.requests().select_related("box__cargo", "japan_item")
    items = (visible_items(request.user).filter(customer__iexact=customer.name)
             .select_related("box__cargo", "receive_check").order_by("-box__created_at", "-id"))
    return render(request, "tracker/customer_detail.html", {
        "customer": customer,
        "requests": reqs,
        "items": items,
        "open_count": reqs.exclude(status=CustomerRequest.Status.FULFILLED).count(),
        "fulfilled_count": reqs.filter(status=CustomerRequest.Status.FULFILLED).count(),
        "status_choices": CustomerRequest.Status.choices,
        "order_form": order_form or CustomerOrderForm(),
        "show_order_form": order_form is not None or request.GET.get("new_order") == "1",
        **catalog_context(),
    })


@login_required
@roles.require(roles.can_use_customers)
@require_POST
def customer_new_order(request, pk):
    """"+ New order" on the customer page: a customer request already linked to them."""
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerOrderForm(request.POST)
    if form.is_valid():
        order = form.save(commit=False)
        order.customer = customer.name
        order.status = CustomerRequest.Status.OPEN
        order.save()
        messages.success(request, f"New order for {customer.name}: {order.item_name} ×{order.qty}.")
        return redirect(reverse("customer_detail", args=[customer.pk]) + "#orders")
    return customer_detail(request, pk, order_form=form)


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
        result = import_japan_csv(form.cleaned_data["file"], replace=form.cleaned_data["replace"])
        if not result.errors:
            messages.success(
                request,
                f"Imported {result.created_items} item(s) into {len(result.boxes)} box(es).",
            )
    return render(request, "tracker/csv_import.html", {"form": form, "result": result})


@login_required
@roles.require(roles.can_import_csv)
def csv_template(request):
    response = HttpResponse(template_csv(), content_type="text/csv")
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
