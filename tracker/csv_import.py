"""Import Japan packing lists from CSV.

One file may contain several boxes; the ``box`` column holds the label,
e.g. ``GN-3``. The cargo code must already exist; missing boxes are created.
The whole file is validated first and nothing is saved if any row is bad.
"""
import csv
import io
import re
from dataclasses import dataclass, field

from django.db import transaction

from .models import Box, Cargo, JapanItem

COLUMNS = [
    "box", "item_name", "type", "qty", "quantity_unit", "brand", "model", "club_type",
    "club_number", "loft", "flex", "shaft_material", "specification", "color",
    "condition", "price_jpy", "customer", "note",
]
REQUIRED = ["box", "type", "item_name"]
# Friendlier header names that map onto the real columns.
ALIASES = {"name": "item_name", "amount": "price_jpy", "unit": "quantity_unit",
           "japan_condition": "condition", "shaft": "shaft_material"}
BOX_RE = re.compile(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$")

TEMPLATE_ROWS = [
    ["GN-3", "Titleist TSR2 Driver", "Club", "1", "pcs", "Titleist", "TSR2", "Driver", "1W",
     "10", "S", "graphite", "Tour AD UB-6, 45.5in", "Black", "used", "25000", "Ko Aung", "with cover"],
    ["GN-3", "Mizuno JPX 923 Irons", "Club", "1", "set", "Mizuno", "JPX 923", "Iron set", "5-P",
     "", "R", "steel", "N.S.PRO 950", "Silver", "new", "98000", "", ""],
    ["KL-1", "Pro V1 balls", "Balls", "2", "dozen", "Titleist", "Pro V1", "", "",
     "", "", "", "", "White", "new", "", "", ""],
]


@dataclass
class ImportResult:
    created_items: int = 0
    created_boxes: list = field(default_factory=list)
    boxes: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # (row number, message)


def template_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return buf.getvalue()


def _parse_box(label):
    m = BOX_RE.match(label or "")
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2))


def _parse_int(value, name, errors, row, default=None, minimum=0):
    value = (value or "").replace(",", "").replace("¥", "").strip()
    if value == "":
        return default
    try:
        number = int(float(value))
    except ValueError:
        errors.append((row, f"{name} must be a number, got '{value}'"))
        return default
    if number < minimum:
        errors.append((row, f"{name} must be at least {minimum}"))
    return number


def import_japan_csv(uploaded_file, replace=False):
    result = ImportResult()
    raw = uploaded_file.read()
    try:
        text = raw.decode("utf-8-sig")  # handles Excel's BOM
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp932")  # Japanese Excel default
        except UnicodeDecodeError:
            result.errors.append((0, "File is not UTF-8 or Shift-JIS text."))
            return result

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower().replace(" ", "_") for h in (reader.fieldnames or [])]
    headers = [ALIASES.get(h, h) for h in headers]
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        result.errors.append((1, "Missing column(s): " + ", ".join(missing)))
        return result
    reader.fieldnames = headers

    cargos = {c.code: c for c in Cargo.objects.all()}
    flex_ok = set(JapanItem.Flex.values)
    cond_ok = set(JapanItem.Condition.values)
    unit_ok = set(JapanItem.Unit.values)
    shaft_ok = set(JapanItem.Shaft.values)
    parsed = []  # (cargo_code, box_no, item kwargs)

    for row_no, row in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(row.values()):
            continue
        errors = result.errors
        box = _parse_box(row.get("box"))
        if not box:
            errors.append((row_no, f"Box '{row.get('box')}' should look like GN-3"))
            continue
        code, box_no = box
        if code not in cargos:
            errors.append((row_no, f"Unknown cargo code '{code}' – add it in admin first"))
        for req in ("type", "item_name"):
            if not row.get(req):
                errors.append((row_no, f"{req} is required"))
        flex = row.get("flex", "").upper()
        if flex and flex not in flex_ok:
            errors.append((row_no, f"flex must be one of {', '.join(JapanItem.Flex.values)}, got '{flex}'"))
        condition = (row.get("condition") or "new").lower()
        if condition not in cond_ok:
            errors.append((row_no, f"condition must be new or used, got '{condition}'"))
        unit = (row.get("quantity_unit") or "pcs").lower()
        if unit not in unit_ok:
            errors.append((row_no, f"quantity_unit must be one of {', '.join(sorted(unit_ok))}, got '{unit}'"))
        shaft = (row.get("shaft_material") or "").lower()
        if shaft and shaft not in shaft_ok:
            errors.append((row_no, f"shaft_material must be steel or graphite, got '{shaft}'"))
        qty = _parse_int(row.get("qty"), "qty", errors, row_no, default=1, minimum=1)
        price = _parse_int(row.get("price_jpy"), "price_jpy", errors, row_no)
        parsed.append((code, box_no, dict(
            type=row.get("type", "")[:50],
            item_name=row.get("item_name", "")[:200],
            flex=flex,
            loft=row.get("loft", "")[:20],
            qty=qty,
            quantity_unit=unit,
            brand=row.get("brand", "")[:60],
            model=row.get("model", "")[:120],
            club_type=row.get("club_type", "")[:50],
            club_number=row.get("club_number", "")[:20],
            shaft_material=shaft,
            specification=row.get("specification", "")[:255],
            color=row.get("color", "")[:40],
            condition=condition,
            price_jpy=price,
            customer=row.get("customer", "")[:100],
            note=row.get("note", "")[:255],
        )))

    if not parsed and not result.errors:
        result.errors.append((0, "The file has no data rows."))
    if result.errors:
        return result

    with transaction.atomic():
        boxes = {}
        for code, box_no, _ in parsed:
            key = (code, box_no)
            if key in boxes:
                continue
            box, created = Box.objects.get_or_create(cargo=cargos[code], box_no=box_no)
            if created:
                result.created_boxes.append(box)
            elif replace:
                box.items.all().delete()
            boxes[key] = box
        JapanItem.objects.bulk_create(
            JapanItem(box=boxes[(code, no)], **kwargs) for code, no, kwargs in parsed
        )
        result.created_items = len(parsed)
        result.boxes = list(boxes.values())
        for box in result.boxes:
            box.refresh_status()
    return result
