"""Import Japan packing lists from CSV.

One file may contain several boxes. The ``box`` column holds the Box ID:
- empty: a new box gets the next monthly Box ID (e.g. OCT26-KB-3) for the company in the ``cargo``
  column. Each bag row (kind = bag) with an empty box starts a new box; the item rows below it go in it.
- a monthly ID like ``OCT26-KB-3``: that box (created if new).
- an older ID like ``GN-3``: only for boxes that already exist.
The cargo code must already exist.
Each box holds exactly one golf bag: a row with ``kind`` = ``bag``; the other
rows are the items inside it.
Optional ``status`` column (packed / shipped / arrived / checked) sets the box's status after the import.
Arrived and Checked come only from the Myanmar side: a Japan-side import (myanmar_status=False) gets a row error.
The whole file is validated first and nothing is saved if any row is bad.
"""
import csv
import io
import re
from dataclasses import dataclass, field

from django.db import transaction

from .forms import is_bag_word, parse_whole_yen
from .models import (Box, Cargo, ClubOption, Customer, JapanItem, choose_option_message, club_value_problem,
                     parse_box_code, parse_item, remember_item)

# Template columns (simple). Older files may also use: type, quantity_unit, brand, model,
# club_type, club_number, loft, shaft_material, specification, color, condition, price_jpy.
COLUMNS = ["box", "cargo", "kind", "item", "color", "club_option", "flex", "qty", "amount_jpy", "cargo_fee_jpy",
           "customer", "note"]
KINDS = {"": "item", "item": "item", "bag": "bag", "golf bag": "bag"}
REQUIRED = []          # box or cargo – checked per row
# Friendlier header names that map onto the real columns.
ALIASES = {"name": "item_name", "item": "item_name", "amount": "price_jpy", "amount_jpy": "price_jpy", "unit": "quantity_unit",
           "japan_condition": "condition", "shaft": "shaft_material", "row_type": "kind", "row": "kind"}
BOX_RE = re.compile(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$")

# amount_jpy is per piece ("45000;41500": a different price for each piece); cargo_fee_jpy goes on the bag row.
# box left empty: each bag row starts a new box with the next monthly Box ID for its cargo company.
TEMPLATE_ROWS = [
    ["", "GN", "bag", "Titleist Stand Bag", "Black", "", "", "1", "8000", "12,500", "Ko Aung", ""],
    ["", "GN", "", "titleist tsr2", "", "Driver 10.5°", "S", "1", "¥25,000", "", "Ko Aung", "with cover"],
    ["", "GN", "", "", "", "Driver head 9.5", "", "2", "6500;5800", "", "", ""],  # different piece prices
    ["", "GN", "", "Mizuno JPX 923", "", "Iron · Graphite", "R", "1", "", "", "", ""],
    ["", "KL", "bag", "Ping Hoofer", "Navy", "", "", "1", "", "9000", "", ""],
    ["", "KL", "", "Cleveland RTX 6", "", "Wedge 52", "", "1", "4200", "", "", ""],
]


def _norm_label(text):
    """'Iron · Graphite', 'iron graphite', 'Wedge 52°' / 'wedge 52', 'Putter 34″' / 'putter 34' compare equal."""
    for ch in "°·-″\"":
        text = text.replace(ch, " ")
    return " ".join(text.lower().split())


def club_option_lookup():
    """Label → ClubOption. Also accepts 'wood 3' / 'hybrid 4' (number without the W/H) and a bare '3W' / '4H'."""
    lookup = {}
    options = ClubOption.objects.filter(active=True, category__active=True).select_related("category")
    for o in options:
        lookup.setdefault(_norm_label(o.full_label), o)
        lookup.setdefault(_norm_label(f"{o.category.name} {o.label}"), o)
    for o in options:
        m = re.fullmatch(r"(\d+)\s*([a-z]+)", o.club_number.lower())
        if m and not o.shaft_material:
            lookup.setdefault(_norm_label(f"{o.type} {m.group(1)}"), o)
            lookup.setdefault(_norm_label(f"{o.category.name} {m.group(1)}"), o)
            lookup.setdefault(m.group(1) + m.group(2), o)
    # older spelling: the Iron option was "Graphite 5" (now "Graphite", no club number)
    graphite = lookup.get(_norm_label("Iron · Graphite"))
    if graphite:
        lookup.setdefault(_norm_label("Iron · Graphite 5"), graphite)
    return lookup


def category_examples():
    """Normalised category name → (name, an example label) for categories with value buttons: "Driver" alone
    is a category without a value, so the row is rejected with an example to use instead."""
    out = {}
    for o in (ClubOption.objects.filter(active=True, category__active=True, category__free_text=False)
              .select_related("category").order_by("category__sort_order", "sort_order", "id")):
        out.setdefault(_norm_label(o.category.name), (o.category.name, o.full_label))
    return out


@dataclass
class ImportResult:
    created_items: int = 0  # items inside the bags
    created_bags: int = 0
    created_boxes: list = field(default_factory=list)
    boxes: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # (row number, message)
    warnings: list = field(default_factory=list)  # (row number, message) – owner override of the one-bag rule


class _Fields:
    """Lets ClubOption.apply_to() write into a plain dict of item fields."""

    def __init__(self, data):
        object.__setattr__(self, "_data", data)

    def __setattr__(self, name, value):
        self._data[name] = value


def template_csv(customers=True):
    """The example file. customers=False (the Japan sender) leaves out the customer column."""
    keep = [i for i, c in enumerate(COLUMNS) if customers or c != "customer"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([COLUMNS[i] for i in keep])
    writer.writerows([r[i] for i in keep] for r in TEMPLATE_ROWS)
    return buf.getvalue()


def _parse_box(label):
    m = BOX_RE.match(label or "")
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2))


def _box_key(row, row_no, is_bag, cargos, auto_open, errors):
    """Which box a row belongs to: ("code", "OCT26-KB-3"), ("legacy", "GN", 3) or ("auto", "KB", n)."""
    raw, cargo_col = row.get("box", ""), row.get("cargo", "").upper()
    if cargo_col and cargo_col not in cargos:
        errors.append((row_no, f"Unknown cargo code '{cargo_col}' – add it in admin first"))
        return None
    if not raw:                                   # new box, numbered automatically
        if not cargo_col:
            errors.append((row_no, "Put the Box ID in box, or leave box empty and give the cargo code in cargo "
                                   "to get a new Box ID"))
            return None
        if is_bag:                                # each bag row starts a new box
            auto_open[cargo_col] = ("auto", cargo_col, row_no)
        elif cargo_col not in auto_open:
            errors.append((row_no, f"No box for this {cargo_col} item – put its bag row (kind = bag) above it, "
                                   "or fill in box"))
            return None
        return auto_open[cargo_col]
    parsed = parse_box_code(raw)
    if parsed:
        period, code, seq, normal = parsed
        if code not in cargos:
            errors.append((row_no, f"Unknown cargo code '{code}' in Box ID '{raw}' – add it in admin first"))
            return None
        if cargo_col and cargo_col != code:
            errors.append((row_no, f"Box ID '{raw}' is a {code} box but cargo says {cargo_col}"))
            return None
        clash = Box.objects.filter(cargo=cargos[code], period=period, seq=seq).exclude(code=normal).first()
        if clash:
            errors.append((row_no, f"Box ID '{raw}' clashes with {clash.label}"))
            return None
        return ("code", normal)
    legacy = _parse_box(raw)                      # an older ID like GN-3: only for boxes that already exist
    if legacy and legacy[0] not in cargos:
        errors.append((row_no, f"Unknown cargo code '{legacy[0]}' – add it in admin first"))
        return None
    if legacy and Box.objects.filter(cargo=cargos[legacy[0]], box_no=legacy[1]).exists():
        return ("legacy", legacy[0], legacy[1])
    errors.append((row_no, f"Box '{raw}' should look like OCT26-KB-3 (or leave it empty to get a new Box ID)"))
    return None


def _parse_yen(value, name, errors, row):
    """Whole yen: "12500", "12,500" or "¥12,500" -> 12500; empty -> None."""
    try:
        return parse_whole_yen(value)
    except ValueError:                     # "12500.5", "-500", "abc"
        errors.append((row, f"{name}: whole yen only, e.g. 12500 (12,500 and ¥12,500 work too) – got '{value}'"))
        return None


def _parse_amount(value, qty, errors, row):
    """amount_jpy: one price per piece ("25000"), or different piece prices "45000;41500" (one per piece).
    Returns (amount, piece prices); the amount is the first piece's price when they differ."""
    if ";" not in (value or ""):
        return _parse_yen(value, "amount_jpy", errors, row), []
    parts = [p for p in value.split(";")]
    before = len(errors)
    pieces = [_parse_yen(p, "amount_jpy", errors, row) for p in parts]
    if len(errors) > before:
        return None, []
    if None in pieces:
        errors.append((row, f"amount_jpy '{value}': every piece needs a price (empty part between the ;)"))
        return None, []
    if len(pieces) != qty:
        errors.append((row, f"amount_jpy has {len(pieces)} piece price(s) ('{value}') but qty is {qty} – "
                            "give one price per piece, separated by ;"))
        return None, []
    if len(pieces) == 1:
        return pieces[0], []
    return pieces[0], pieces


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


def _rows(numbers):
    return ("row " if len(numbers) == 1 else "rows ") + ", ".join(map(str, numbers))


def _existing_box(key, cargos):
    """The box a key points at, if it already exists."""
    if key[0] == "code":
        return Box.objects.filter(code=key[1]).first()
    if key[0] == "legacy":
        return Box.objects.filter(cargo=cargos.get(key[1]), box_no=key[2]).first()
    return None                                   # ("auto", ...) is always a new box


def _key_label(key, rows):
    if key[0] == "code":
        return key[1]
    if key[0] == "legacy":
        return f"{key[1]}-{key[2]}"
    return f"new {key[1]} box ({_rows(rows[:1])})"


def _check_bags(parsed, cargos, replace):
    """Every box must end up with exactly one golf bag. Returns [(row, message)]."""
    rows, bag_rows = {}, {}
    for key, kwargs, row_no in parsed:
        rows.setdefault(key, []).append(row_no)
        if kwargs["is_bag"]:
            bag_rows.setdefault(key, []).append(row_no)
    problems = []
    for key, numbers in rows.items():
        label, bags = _key_label(key, numbers), bag_rows.get(key, [])
        existing = None
        if not replace:
            box = _existing_box(key, cargos)
            existing = box.items.filter(is_bag=True).first() if box else None
        if len(bags) > 1:
            problems.append((bags[0], f"Box {label} has {len(bags)} bag rows ({_rows(bags)}) – "
                                      "a box holds exactly one golf bag"))
        elif bags and existing:
            problems.append((bags[0], f"Box {label} already has a golf bag ({existing.title}) – remove the bag row "
                                      f"({_rows(bags)}) or tick “Replace existing Japan items”"))
        elif not bags and not existing:
            problems.append((numbers[0], f"Box {label} has no golf bag – add a row with kind = bag "
                                         f"(box {label} is on {_rows(numbers)})"))
    return problems


def import_japan_csv(uploaded_file, replace=False, allow_bag_exception=False, customers=True, myanmar_status=True):
    """customers=False (the Japan sender): a customer column is ignored.
    myanmar_status=False (the Japan sender): a status of arrived / checked is a row error."""
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
    club_options = club_option_lookup()
    categories = category_examples()
    flex_ok = set(JapanItem.Flex.values)
    cond_ok = set(JapanItem.Condition.values)
    unit_ok = set(JapanItem.Unit.values)
    shaft_ok = set(JapanItem.Shaft.values)
    parsed = []     # (box key, item kwargs, row number)
    fees = {}       # box key -> cargo fee from the bag row
    auto_open = {}  # cargo code -> the new (auto-numbered) box the next item rows go into
    names = []      # parsed brand + model per row, remembered (new brands/models) once the file is valid
    statuses = {}   # box key -> (status, row) from the optional status column

    for row_no, row in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(row.values()):
            continue
        # "item" (one text, parsed like the form: "xxio mp 1400" -> XXIO MP1400) or separate brand / model columns.
        # A brand in its own column counts as a brand even when it's new (there's no "Is it a brand?" in a file).
        columns = " ".join(x for x in (row.get("brand", ""), row.get("model", "")) if x)
        item_parsed = parse_item(row.get("item_name", ""))
        if columns and not item_parsed.brand:       # no brand in the item text: use the brand / model columns
            item_parsed = parse_item(columns, add_brand=bool(row.get("brand")))
        row["item_name"] = item_parsed.display
        errors = result.errors
        kind = KINDS.get(" ".join(row.get("kind", "").lower().split()))
        if kind is None:
            errors.append((row_no, f"kind must be bag or item (or empty for an item), got '{row['kind']}'"))
        elif kind == "item" and not row.get("kind") and is_bag_word(row.get("type")) and not row.get("club_option"):
            kind = "bag"  # older files: an item of type "Bag" is the box's golf bag
        is_bag = kind == "bag"
        key = _box_key(row, row_no, is_bag, cargos, auto_open, errors)
        if key is None:
            continue
        status = row.get("status", "").lower()
        if status and status not in Box.Status.values:
            allowed = "packed, shipped, arrived or checked" if myanmar_status else "packed or shipped"
            errors.append((row_no, f"status must be {allowed}, got '{row['status']}'"))
        elif status in ("arrived", "checked") and not myanmar_status:
            errors.append((row_no, f"status '{row['status']}': Arrived and Checked are set by the Myanmar check, "
                                   "not from Japan – use packed or shipped"))
        elif status:
            if key in statuses and statuses[key][0] != status:
                errors.append((row_no, f"status '{status}' differs from '{statuses[key][0]}' on row {statuses[key][1]} "
                                       "for the same box"))
            statuses.setdefault(key, (status, row_no))
        if is_bag:
            if not row.get("item_name"):
                errors.append((row_no, "bag row: put the bag's name in item, e.g. Titleist Stand Bag"))
            if row.get("club_option"):
                errors.append((row_no, "bag row: leave club_option empty (the bag is not a club)"))
            if row.get("qty", "") not in ("", "1"):
                errors.append((row_no, "bag row: qty must be 1 – a box holds one golf bag"))
        option = None
        if row.get("club_option") and not is_bag:
            option = club_options.get(_norm_label(row["club_option"]))
            category = categories.get(_norm_label(row["club_option"]))
            if option is None and category:
                errors.append((row_no, f"club_option '{row['club_option']}': {choose_option_message(category[0])[:-1]} "
                                       f"– give its value too, e.g. '{category[1]}'"))
            elif option is None:
                errors.append((row_no, f"Unknown club_option '{row['club_option']}' – use a label like 'Wedge 52°' "
                                       "(see Admin → Club options)"))
        if not (row.get("item_name") or row.get("club_option") or row.get("type")):
            errors.append((row_no, "item (brand + model) or club_option is required"))
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
        price, pieces = _parse_amount(row.get("price_jpy"), 1 if is_bag else (qty or 1), errors, row_no)
        fee = _parse_yen(row.get("cargo_fee_jpy"), "cargo_fee_jpy", errors, row_no)
        if fee is not None and not is_bag:
            errors.append((row_no, "cargo_fee_jpy goes on the box's bag row (kind = bag)"))
        elif fee is not None:
            fees[key] = fee
        names.append(item_parsed)
        parsed.append((key, dict(
            type=row.get("type", "")[:50],
            item_name=row.get("item_name", "")[:200],
            flex=flex,
            loft=row.get("loft", "")[:20],
            qty=qty,
            quantity_unit=unit,
            brand=item_parsed.brand[:60],
            model=item_parsed.model[:120],
            club_type=row.get("club_type", "")[:50],
            club_number=row.get("club_number", "")[:20],
            shaft_material=shaft,
            specification=row.get("specification", "")[:255],
            color=row.get("color", "")[:40],
            condition=condition,
            price_jpy=price,
            piece_prices=pieces,
            customer=row.get("customer", "")[:100] if customers else "",
            note=row.get("note", "")[:255],
            head_only=False,
            is_bag=is_bag,
        ), row_no))
        if is_bag:
            parsed[-1][1].update(type=JapanItem.BAG_TYPE, qty=1, flex="", loft="", club_type="", club_number="",
                                 shaft_material="")
        elif option:  # the single label wins over the old separate columns
            option.apply_to(_Fields(parsed[-1][1]))
        kw = parsed[-1][1]               # bulk_create skips save(): store the line total here
        if not is_bag and not option:    # older columns: a type like "Driver" with no loft / number is not enough
            problem = club_value_problem(kw["type"], loft=kw["loft"], shaft=kw["shaft_material"],
                                         club_number=kw["club_number"], length_in=kw.get("length_in", ""),
                                         head_only=kw["head_only"])
            if problem and not row.get("club_option"):
                errors.append((row_no, f"type '{kw['type']}': {problem[:-1]} – use club_option, e.g. "
                                       f"'{categories.get(_norm_label(kw['type']), ('', kw['type']))[1]}'"))
        kw["line_total_jpy"] = JapanItem.compute_line_total(kw["price_jpy"], kw["qty"], kw["piece_prices"])

    if not parsed and not result.errors:
        result.errors.append((0, "The file has no data rows."))
    if result.errors:
        return result
    if not myanmar_status:                 # Japan side: a box Myanmar has started is no longer theirs to change
        seen = set()
        for key, _, row_no in parsed:
            box = None if key in seen else _existing_box(key, cargos)
            seen.add(key)
            if box and box.myanmar_started:
                result.errors.append((row_no, f"Box {box.label} is {box.get_status_display()} – the Myanmar check has "
                                              "started, so its Japan list can no longer change"))
        if result.errors:
            return result
    bag_problems = _check_bags(parsed, cargos, replace)
    if allow_bag_exception:
        result.warnings = bag_problems
    else:
        result.errors = bag_problems
        if result.errors:
            return result

    with transaction.atomic():
        boxes = {}
        for key, _, _ in parsed:
            if key in boxes:
                continue
            box = _existing_box(key, cargos)
            if box is None:
                if key[0] == "code":
                    box = Box(cargo=cargos[parse_box_code(key[1])[1]])
                    box.set_code(key[1])
                else:                             # ("auto", cargo, n): next monthly Box ID
                    box = Box(cargo=cargos[key[1]])
                    box.set_code(Box.next_code(box.cargo)[0])
                box.save()
                result.created_boxes.append(box)
            elif replace:
                box.items.all().delete()
            if key in fees:
                box.cargo_fee_jpy = fees[key]
                box.save(update_fields=["cargo_fee_jpy", "updated_at"])
            boxes[key] = box
        for item_parsed in names:
            remember_item(item_parsed)
        JapanItem.objects.bulk_create(JapanItem(box=boxes[key], **kwargs) for key, kwargs, _ in parsed)
        for name in {kwargs["customer"] for _, kwargs, _ in parsed if kwargs["customer"]}:
            Customer.ensure(name)  # bulk_create skips save(), so add customers here
        result.created_bags = sum(kwargs["is_bag"] for _, kwargs, _ in parsed)
        result.created_items = len(parsed) - result.created_bags
        result.boxes = list(boxes.values())
        for key, box in boxes.items():
            if key in statuses:                   # a status from the file wins over the automatic rules
                box.status = statuses[key][0]
                box.save(update_fields=["status", "updated_at"])
            else:
                box.refresh_status()
    return result
