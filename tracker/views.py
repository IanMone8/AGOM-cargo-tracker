from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import roles
from .csv_import import import_japan_csv, template_csv
from .forms import (
    BoxFilterForm,
    BoxForm,
    CSVImportForm,
    CustomerRequestForm,
    ExtraItemForm,
    JapanItemForm,
)
from .models import Box, Catalog, CustomerRequest, ExtraItem, JapanItem, ReceiveCheck


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def catalog_context():
    """Values for the <datalist> dropdowns used by item / request forms."""
    entries = list(Catalog.objects.all())
    lofts = sorted({l for c in entries for l in c.loft_list}, key=lambda v: (len(v), v))
    return {
        "catalog_types": sorted({c.type for c in entries}),
        "catalog_models": [c.brand_model for c in entries],
        "catalog_lofts": lofts,
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
    arrived = boxes_with_counts(Box.objects.filter(status=Box.Status.ARRIVED))
    mismatched = boxes_with_counts(Box.with_mismatches())
    open_requests = CustomerRequest.objects.filter(status=CustomerRequest.Status.OPEN)
    return render(request, "tracker/dashboard.html", {
        "in_transit": in_transit,
        "arrived": arrived,
        "mismatched": mismatched,
        "open_requests": open_requests[:10],
        "open_request_count": open_requests.count(),
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
    form = BoxForm(request.POST or None, user=request.user)
    if form.is_valid():
        box = form.save()
        box.refresh_status()
        messages.success(request, f"Box {box.label} created.")
        return redirect("box_detail", pk=box.pk)
    return render(request, "tracker/box_form.html", {"form": form})


@login_required
def box_edit(request, pk):
    box = get_object_or_404(Box, pk=pk)
    if not (roles.can_edit_japan(request.user) or roles.can_check(request.user)):
        raise PermissionDenied("Your role cannot edit boxes.")
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
        "items": items,
        "extras": box.extra_items.all(),
        "summary": box.summary(),
        **catalog_context(),
    })


@login_required
@roles.require(roles.can_check)
@require_POST
def box_mark_arrived(request, pk):
    box = get_object_or_404(Box, pk=pk)
    if not box.myanmar_received_date:
        box.myanmar_received_date = timezone.localdate()
        box.save(update_fields=["myanmar_received_date", "updated_at"])
    box.refresh_status()
    messages.success(request, f"Box {box.label} marked as arrived.")
    return redirect("box_detail", pk=box.pk)


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
@login_required
@roles.require(roles.can_edit_japan)
def item_add(request, pk):
    box = get_object_or_404(Box, pk=pk)
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
def request_edit(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    form = CustomerRequestForm(request.POST or None, instance=req)
    if form.is_valid():
        form.save()
        messages.success(request, "Request saved.")
        return redirect("request_list")
    return render(request, "tracker/request_form.html", {"form": form, "req": req, **catalog_context()})


@login_required
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
def request_attach_items(request):
    box = Box.objects.filter(pk=request.GET.get("box") or 0).first()
    items = box.items.all() if box else []
    return render(request, "tracker/partials/attach_item_options.html", {"items": items})


@login_required
@require_POST
def request_detach(request, pk):
    req = get_object_or_404(CustomerRequest, pk=pk)
    req.detach()
    return render(request, "tracker/partials/request_card.html", {
        "r": req, "status_choices": CustomerRequest.Status.choices,
    })


# --- CSV import --------------------------------------------------------------
@login_required
@roles.require(roles.can_edit_japan)
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
def csv_template(request):
    response = HttpResponse(template_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="japan_list_template.csv"'
    return response
