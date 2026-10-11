from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import roles
from .csv_import import import_japan_csv, template_csv
from .forms import (
    BoxCreateForm,
    BoxFilterForm,
    BoxForm,
    ItemFormSet,
    CSVImportForm,
    CustomerRequestForm,
    ExtraItemForm,
    JapanItemForm,
)
from .models import Box, Cargo, Catalog, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck

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
    in_transit = boxes_with_counts(Box.objects.filter(status=Box.Status.SHIPPED))
    needs_review = boxes_with_counts(
        Box.objects.filter(
            Q(status=Box.Status.ARRIVED) | Q(pk__in=Box.with_mismatches().values("pk"))
        )
    )
    open_requests = CustomerRequest.objects.filter(status=CustomerRequest.Status.OPEN)
    return render(request, "tracker/dashboard.html", {
        "cargo_count": Cargo.objects.count(),
        "total_box_count": Box.objects.count(),
        "checked_box_count": Box.objects.filter(status=Box.Status.CHECKED).count(),
        "mismatch_count": Box.with_mismatches().count(),
        "in_transit": in_transit,
        "needs_review": needs_review,
        "open_requests": open_requests[:8],
        "open_request_count": open_requests.count(),
        "recent": boxes_with_counts(Box.objects.order_by("-updated_at"))[:5],
    })


# --- Boxes -------------------------------------------------------------------
@login_required
def box_list(request):
    form = BoxFilterForm(request.GET or None)
    qs = Box.objects.all()
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
    box = get_object_or_404(Box, pk=pk)
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
    box = get_object_or_404(Box.objects.select_related("cargo"), pk=pk)
    items = box.items.select_related("receive_check").prefetch_related("requests")
    return render(request, "tracker/box_detail.html", {
        "box": box,
        "can_edit_this_box": roles.can_edit_box(request.user, box),
        "can_edit_items": roles.can_edit_box_items(request.user, box),
        "japan_locked": roles.can_edit_japan(request.user) and not roles.can_edit_box_items(request.user, box),
        "items": items,
        "extras": box.extra_items.all(),
        "summary": box.summary(),
        **catalog_context(),
    })


def _start_check(box):
    if not box.myanmar_received_date:
        box.myanmar_received_date = timezone.localdate()
        box.save(update_fields=["myanmar_received_date", "updated_at"])
    box.refresh_status()


@login_required
@roles.require(roles.can_check)
@require_POST
def box_start_check(request, pk):
    """Myanmar checker opens the box: records today's arrival and shows the check buttons."""
    box = get_object_or_404(Box, pk=pk)
    _start_check(box)
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
    item = get_object_or_404(JapanItem.objects.select_related("box__cargo"), pk=pk)
    result = request.POST.get("result")
    if result not in ReceiveCheck.Result.values:
        return HttpResponse("Bad result", status=400)
    _start_check(item.box)
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
    item = get_object_or_404(JapanItem.objects.select_related("box"), pk=pk)
    ReceiveCheck.objects.filter(japan_item=item).delete()
    item.box.refresh_status()
    item = JapanItem.objects.get(pk=item.pk)
    return _box_fragments(request, item.box, "tracker/partials/item_row.html", {"item": item})


@login_required
@roles.require(roles.can_check)
def item_wrong_qty_form(request, pk):
    item = get_object_or_404(JapanItem, pk=pk)
    return render(request, "tracker/partials/item_wrong_qty.html", {"item": item})


@login_required
def item_row(request, pk):
    item = get_object_or_404(JapanItem.objects.select_related("receive_check"), pk=pk)
    return render(request, "tracker/partials/item_row.html", {"item": item, "box": item.box})


# --- Japan items (manual add / delete) ------------------------------------------
def _require_box_items(user, box):
    if not roles.can_edit_box_items(user, box):
        raise PermissionDenied("This box is locked – the Myanmar check has started.")


@login_required
@roles.require(roles.can_edit_japan)
def item_add(request, pk):
    box = get_object_or_404(Box, pk=pk)
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
    item = get_object_or_404(JapanItem.objects.select_related("box"), pk=pk)
    box = item.box
    _require_box_items(request.user, box)
    item.delete()
    box.refresh_status()
    return _box_fragments(request, box, "tracker/partials/oob_only.html", {})


# --- Extra items (HTMX) ------------------------------------------------------
@login_required
@roles.require(roles.can_check)
def extra_add(request, pk):
    box = get_object_or_404(Box, pk=pk)
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
    extra = get_object_or_404(ExtraItem.objects.select_related("box"), pk=pk)
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


def _suggestions(req):
    """Japan items that look like this request and are not yet attached."""
    qs = JapanItem.objects.select_related("box__cargo").filter(requests__isnull=True)
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
        item = get_object_or_404(JapanItem, pk=request.POST.get("japan_item"))
        req.attach(item)
        return render(request, "tracker/partials/request_card.html", {
            "r": req, "status_choices": CustomerRequest.Status.choices,
        })
    boxes = Box.objects.select_related("cargo").exclude(status=Box.Status.CHECKED)
    return render(request, "tracker/partials/request_attach.html", {
        "r": req, "suggestions": _suggestions(req), "boxes": boxes,
    })


@login_required
@roles.require(roles.can_use_requests)
def request_attach_items(request):
    box = Box.objects.filter(pk=request.GET.get("box") or 0).first()
    items = box.items.all() if box else []
    return render(request, "tracker/partials/attach_item_options.html", {"items": items})


@login_required
@roles.require(roles.can_use_requests)
@require_POST
def request_detach(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    req.detach()
    return render(request, "tracker/partials/request_card.html", {
        "r": req, "status_choices": CustomerRequest.Status.choices,
    })


# --- Cargo history ------------------------------------------------------------
@login_required
def cargo_history(request):
    cargos = list(Cargo.objects.annotate(
        box_total=Count("boxes", distinct=True),
        in_transit=Count("boxes", filter=Q(boxes__status=Box.Status.SHIPPED), distinct=True),
        checked=Count("boxes", filter=Q(boxes__status=Box.Status.CHECKED), distinct=True),
    ))
    problem_ids = set(Box.with_mismatches().values_list("pk", flat=True))
    for cargo in cargos:
        dated = cargo.boxes.exclude(japan_ship_date=None).exclude(myanmar_received_date=None)
        days = [(b.myanmar_received_date - b.japan_ship_date).days for b in dated]
        cargo.avg_days = round(sum(days) / len(days)) if days else None
        cargo.issues = cargo.boxes.filter(pk__in=problem_ids).count()

    selected = Cargo.objects.filter(code=request.GET.get("cargo", "").upper()).first()
    boxes = boxes_with_counts(Box.objects.filter(cargo=selected) if selected else Box.objects.all())
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
