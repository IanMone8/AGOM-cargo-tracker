# AGOM Cargo Tracker

A small, mobile-friendly web app for tracking golf items shipped from **Japan → Myanmar**
through several cargo companies (JP, GN, KL, KAG, …).

Built with **Django 5**, **HTMX** (one-tap checks, no page reloads) and **Tailwind** (CDN).
It uses SQLite in development and Postgres in production (via `DATABASE_URL`).

## Features

| Page | What it does |
| --- | --- |
| **Login** | Django's built-in auth |
| **Dashboard** | Pastel stat cards: Cargos · Total boxes · Checked in Myanmar (e.g. 3 / 4) · Needs review (boxes with differences), plus lists of boxes waiting for check or review, in transit and recently updated |
| **Shipments** | All boxes with cargo / status filters (live, via HTMX) and an **Add box** button |
| **Add box** (modal) | Cargo company, Box ID, box note, the required **Golf bag** card, then **Items inside the bag**. **Add item** adds another item card; each card has **Copy** and **Delete** |
| **Box detail** | "GN-3 · Titleist Stand Bag" header, the golf bag first, then the items inside it, **Start Myanmar check**, then one-tap **✓ Matched / ✗ Missing / ± Wrong qty**, undo, extra items and a live mismatch summary |
| **Cargo history** | One card per cargo company (boxes, in transit, checked, issues, average transit days) and its box history, newest first |
| **CSV import** | (Owner / Admin) Import Japan packing lists (several boxes per file), all-or-nothing validation, template download |
| **Admin** | Every model is registered at `/admin/` |

### Look and layout
Green golf theme with a left sidebar (Dashboard, Shipments, Cargo history, Customers, CSV import).
On phones the sidebar becomes a slide-in menu and **Add box** is the ＋ in the top bar.
Theme styles live in `static/css/agom.css`; Tailwind (CDN) handles layout.

**Colours:** one palette, defined once as CSS variables at the top of `static/css/agom.css`
(`--bg`, `--card`, `--card-border`, `--grad-from`/`--grad-to`, `--primary`/`--primary-hover`, `--text`/`--text-muted`,
stat tints `--sage`/`--mint`/`--cream`/`--sand`, and status `--ok`/`--warn`/`--danger`/`--info` with a `-bg` tint and an
`-ink` text shade). `static/js/tw-theme.js` exposes them to Tailwind, so templates write `text-ink-muted`, `bg-ok-bg`,
`border-l-warn`… instead of hard-coded colours. Status pills use the `badge` filter (`pill pill-ok` etc.). The `-ink`
shades exist because the status colours as given reach only ~4.1–4.35:1 as small text on their tints; the inks are the
same hues darkened just enough for WCAG AA (≥ 4.5:1). Main action buttons are solid `--primary` with white text; the light
gradient is for the menu, modal/page headers, the banner and Save buttons. Purple is only used for the Viber badge
(Viber brand colour). `PaletteTests` checks the tokens, AA contrast and that templates use no hard-coded colours.
The look is "3D-styled": raised cards with a light top edge and darker bottom edge, buttons that sit on a
thick edge and press down when tapped, sunken inputs, and stat/box cards that tilt toward the mouse with a
soft glare (desktop only; turned off when the device asks for reduced motion).

**Login page ("Fairway at dawn"):** an inline-SVG golf scene (sky, drifting clouds, three hills, a putting green
with cup and flag) drawn with the colour tokens only (`templates/registration/login.html`, `static/css/login.css`).
On load a ball flies in on a dotted arc, bounces twice, rolls and drops into the cup, the flag waves and a ripple
appears (~2.5 s, transform/opacity only); afterwards just the flag sways and clouds drift. With *reduce motion* the
finished scene is shown, and after a wrong password the intro doesn't replay. The card floats left-centre on desktop;
on phones the scene fills the top ~40 % and the card sits below. Password has a show/hide toggle.

**Logo:** `static/img/3D logo.png` (shown from its small copy in `static/img/display/`). The sidebar, phone header, login page and
favicon use it. Until the file exists the image is simply hidden.

**Myanmar checker's banner:** the Myanmar checker (only – not Owner/Admin) sees a Burmese message from
Miss Thaw Thaw instead of the English banner, centred, in Noto Sans Myanmar (Google Fonts, loaded only for them), with
one sticker bobbing gently in each corner – happy, stars, peace, ok (off with *reduce motion*). Put the originals in `static/img/stickers/`
(`ok.png`, `peace.png`, `stars.png`, `happy.png`) and run `python scripts/make_display_images.py` to make the
small WebP copies in `static/img/display/stickers/`; a sticker whose copy is missing is simply not shown.

**Dashboard icons:** `static/img/icons/plane.png`, `boxes.png`, `box-checked.png` and `warning.png`
(Cargos, Total boxes, Checked in Myanmar, Needs review). A missing icon is hidden.

### Myanmar check flow
The Myanmar checker opens a box and taps **Start Myanmar check**. That records today as the
Myanmar received date (status → Arrived) and shows the check buttons on every item.
The box becomes **Checked** once every item has a result.

### Box status rules
Status is `Packed → Shipped → Arrived → Checked`. It moves forward automatically:

* a Japan ship date sets **Shipped**
* a Myanmar received date (or **Start Myanmar check**) sets **Arrived**
* once every Japan item has a check result, the box becomes **Checked** (undoing a check moves it back to Arrived)

You can still pick a status by hand on the *Edit* page. A manual choice overrides the automatic rules.

### Roles (4 users)

| User | Role | Can do |
| --- | --- | --- |
| `admin` | Superuser | Everything, including `/admin/`, users and the catalog |
| `owner` | Group **Owner** (staff) | Everything in the app, plus tracker data in `/admin/` |
| `japan` | Group **Japan Sender** | Add boxes, edit boxes (Japan side, status Packed / Shipped only), add/delete Japan items – until the Myanmar check starts. No CSV import, no customers |
| `myanmar` | Group **Myanmar Checker** | Start Myanmar check, set the received date, receive checks, extra items, customers and their orders |

Everyone can view the dashboard, shipments and cargo history. **CSV import** is for Owner and Admin only.
There is no separate Customer requests page: customer orders are added and tracked on each customer's page
(**+ New order**, status, attach to a box item). Customers are for Owner, Admin and the Myanmar checker; the
Japan sender doesn't see them.

**Shipped boxes only for Myanmar:** the Myanmar checker doesn't see a box (dashboard, Shipments,
Cargo history, order attach lists, or by direct link) until the Japan sender sets it to **Shipped** – with the one-tap **Mark as shipped** button on the box page
(or Edit box → Status → Shipped → Save).

**Dates:** *Received in Myanmar* can't be before *Shipped from Japan* (same day is allowed) – checked in the
app and in Django admin. Calendars don't offer dates before today (a date already saved on a box stays as it is).
Marking a box Shipped without a date records today, and the Myanmar check can't start before the ship date.

**Customers:** (Owner, Admin, Myanmar checker – not the Japan sender) a CRM-style table: avatar + name,
tap-to-call phone, address, Viber pill, orders (requests + items packed), a status tag (Waiting / In box /
All delivered / No orders), last update and a favourite star (starred customers stay on top). Live search,
Viber / Orders filters, sortable Name / Orders / Last update, 25 rows then "Load more"; rows become compact
cards on phones. Each customer has a detail page with all their requests and packed items. Customer names
typed on requests, Japan items or CSV rows create the customer automatically.

**Item card:** the Japan sender fills only Item name (optional), Customer (optional, suggests existing customers), **Club option**, Flex, Qty and Note.
Club option is two taps – a category (Driver, Driver head, Wood, Hybrid, Iron, Wedge, Putter, Other), then a value
(Other = free text) – and is saved as structured data (e.g. "Iron · Graphite 5" → type Iron, shaft graphite, club number 5;
"Driver head 9.5" → type Driver, loft 9.5, head only; "Wood 3W" → type Wood, club number 3W; "Hybrid 4H" → type Hybrid,
club number 4H; "Putter 34″" → type Putter, length 34 in). The owner manages it all in `/admin/`:
**Club categories** (add one like "Chipper", set the button order, hide it, or tick *free text* to show a text box
like Other – values can be added on the same page) and **Club options** (the value buttons). A category with no
active options and no free text isn't shown. Each card has **Copy**, and saved items have **copy** on the box page.
Brand, model, price, etc. are kept for old data, CSV and admin.

**One golf bag per box:** every box holds exactly one golf bag and all golf items are packed inside it.
The bag (name, color, optional customer, note) is stored as a Japan item flagged `is_bag`, so it is
checked in Myanmar (its own Matched / Missing / Wrong qty row at the top), can be linked to customer orders
and attached to requests like any other item. A box only becomes **Checked** when the bag and every item are checked.
Box lists, Cargo history, requests and the box page show the bag next to the box ID ("JP-7 · Titleist Stand Bag").
The rule is enforced in the Add box modal, on the box page (the bag can be edited but not copied; only Owner/Admin
can delete it), in CSV import and in `/admin/`. Owner/Admin can override it (a tick box in the modal, CSV import
and admin) and then get a clear warning. A box without a bag shows **No bag recorded** on its page with an
**Add bag** button (Owner/Admin can always use it; the Japan sender until the Myanmar check starts).
When upgrading, the migration marks an existing box's single item of type *Bag* as its golf bag; boxes with no
or several bag items are left for the owner to fix.

**Locking:** once the Myanmar check has started on a box (it has a received date, or any item is checked),
the Japan sender can no longer edit that box or add or delete its items.
Owner and Admin can still correct it, and the Myanmar checker can still edit the Myanmar side.

## Run locally (development)

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py setup_roles --create-users   # creates the groups + 4 users, prints passwords
python manage.py seed_demo                    # cargo companies JP/GN/KL/KAG + starter catalog
# optional: python manage.py seed_demo --demo  # sample boxes and requests to play with

python manage.py runserver
```

Open http://127.0.0.1:8000 and log in with one of the printed accounts.
To change a password: `python manage.py changepassword owner` (or use `/admin/`).
Edit the cargo company names under **Admin → Cargos**.

To try it from a phone on the same Wi-Fi:
`ALLOWED_HOSTS=192.168.1.20,localhost python manage.py runserver 0.0.0.0:8000`
(use your computer's IP address).

Run the tests with `python manage.py test`.

## CSV format for Japan lists

```
box,kind,item_name,color,club_option,flex,qty,customer,note
GN-3,bag,Titleist Stand Bag,Black,,,1,Ko Aung,
GN-3,,Titleist TSR2,,Driver 10.5°,S,1,Ko Aung,with cover
GN-3,,,,Driver head 9.5,,2,,
GN-3,,Mizuno JPX 923,,Iron · Graphite 5,R,1,,
KL-1,bag,Ping Hoofer,Navy,,,1,,
KL-1,,Cleveland RTX 6,,Wedge 52,,1,,
```

* Required: `box`, plus `item_name` or `club_option` on each row. Other columns are optional.
* `kind` = `bag` marks the box's golf bag (bag name in `item_name`, plus `color`, `customer`, `note`; qty 1);
  empty (or `item`) is an item inside the bag. Each box needs **exactly one** bag row – or must already have its
  bag when you add items to it. A box with no bag or two bags is rejected with its row numbers, e.g.
  *Row 4: Box GN-6 has 2 bag rows (rows 4, 6)*. Older files with a row of `type` *Bag* count as the bag row.
  Owner/Admin can tick *Owner override* to import such boxes anyway; they are listed as warnings.
* `club_option` is one label from **Admin → Club options**, e.g. `Driver 10.5`, `Driver head 9.5`,
  `Iron · Graphite 5`, `Iron Steel`, `Wedge 52°`, `Wood 3W`, `Hybrid 4H`, `Putter 34` (the `°`, `·` and `″` are
  optional, case doesn't matter). Woods and hybrids also accept the number alone – `wood 3`, `Hybrid 4` – or just
  `3W` / `4H`. It fills the structured columns (type, loft, shaft, club number, length, head only) for you.
* Older files still work: `type`, `loft`, `brand`, `model`, `club_type`, `club_number`, `shaft_material`,
  `specification`, `color`, `condition`, `price_jpy`, `quantity_unit`; friendlier headers like `name`,
  `amount`, `unit` too. If a row has both, `club_option` wins.
* `box` is the cargo code plus the number (`GN-3`). The cargo company must already exist; missing boxes are created.
* `flex` is `L`, `A`, `R`, `SR`, `S`, `X` or empty. `price_jpy` may contain `¥` and commas.
* UTF-8 (including Excel's "CSV UTF-8") and Shift-JIS files are accepted.
* If any row has an error, **nothing is imported** and every bad row is listed.
* Tick *Replace existing Japan items* to re-import a corrected list. This deletes those boxes' current items and their checks first.

## Configuration (environment variables)

| Variable | Default | Notes |
| --- | --- | --- |
| `DEBUG` | `True` | Set to `False` in production |
| `SECRET_KEY` | dev key | **Required** when `DEBUG=False` |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | e.g. `postgres://user:pass@host:5432/agom` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma separated |
| `CSRF_TRUSTED_ORIGINS` | – | e.g. `https://agom.example.com` |
| `TIME_ZONE` | `Asia/Yangon` | |
| `SECURE_SSL_REDIRECT` | `True` (prod) | Set to `False` if TLS is terminated elsewhere and loops |

## Production

```bash
export DEBUG=False
export SECRET_KEY='a-long-random-string'
export DATABASE_URL='postgres://agom:password@db-host:5432/agom'
export ALLOWED_HOSTS='agom.example.com'
export CSRF_TRUSTED_ORIGINS='https://agom.example.com'

pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py setup_roles --create-users
python manage.py seed_demo
gunicorn agom.wsgi --bind 0.0.0.0:8000
```

Static files (the admin CSS) are served by WhiteNoise, so no extra web server config is needed.
This works on Render, Railway, Fly.io or a small VPS behind nginx/Caddy.

## Project layout

```
agom/            settings, urls
tracker/
  models.py      Cargo, Box, JapanItem, ReceiveCheck, ExtraItem, CustomerRequest, Catalog
  views.py       pages + HTMX endpoints
  roles.py       role helpers (Owner / Japan Sender / Myanmar Checker)
  csv_import.py  CSV parser/validator
  admin.py       Django admin registration
  management/commands/  setup_roles, seed_demo
templates/       base layout (sidebar), pages, HTMX partials (Add box modal, item cards…)
static/          css/agom.css (golf theme), img/ (logos, icons; small copies in img/display/)
```

## Data model notes

* `Box.box_no` is a number (the "Box ID" in the Add box form); the label (`GN-3`) is built from the cargo code.
* The golf bag is a Japan item with `is_bag = True` (type "Golf bag", qty 1); it sorts first in every list.
* Japan items store: name, type, qty, quantity unit, brand, model, club / item type, club number, loft, flex,
  shaft material, specification, color, Japan condition, amount (optional), customer and note.
* `ReceiveCheck` is one-to-one with `JapanItem`, so tapping a button again overwrites the check.
* `CustomerRequest` has optional `box` **and** `japan_item` links. Attaching sets the status to *In box*; detaching sets it back to *Ordered*.
* `Catalog` entries feed the type / model / loft suggestions in the item and request forms. Manage them in `/admin/`.
