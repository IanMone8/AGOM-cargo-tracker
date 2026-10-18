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
| **Box detail** | "OCT26-KB-3 · Titleist Stand Bag" header, the golf bag first, then the items inside it, **Start Myanmar check**, then one-tap **✓ Matched / ✗ Missing / ± Wrong qty**, undo, extra items and a live mismatch summary |
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
Miss Thaw Thaw (her name in green at the end of the last line) instead of the English banner, centred, in
Noto Sans Myanmar (Google Fonts, loaded only for them), with an animated sticker in each corner – confetti, stars,
peace, ok. The GIFs (`static/img/stickers/animated_*.gif`, ~2 MB each, white background) are shown as small
animated WebPs that keep every frame, with the white cut out: run `python scripts/make_animated_stickers.py`
after replacing a GIF (don't use `make_display_images.py` for them – that would freeze them). The GIF is the
fallback for browsers without animated WebP. With *reduce motion* the still PNG copies (happy, stars, peace, ok,
made by `make_display_images.py`) are shown instead. A sticker whose files are missing is not shown.

**Japan sender's banner:** the Japan sender (only – not Owner/Admin) sees a Burmese thank-you in the same card: the
girl picture on the left, the two-line message centred in Noto Sans Myanmar (line height 1.9), the boy picture on the
right, both standing on the card's bottom edge (about 190px tall on wide screens, 160px on medium, 92px on phones,
same order). Put the originals at `static/img/stickers/girl.png` and `boy.png` and run
`python scripts/make_display_images.py`: it crops them and writes `static/img/display/stickers/girl|boy.webp` + `.png`,
which the page uses. Until a copy exists its place stays empty. Both banners are chosen by **group**
(Japan Sender / Myanmar Checker), never by username, so users can be renamed.

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
**Box status:** the Japan sender sets only Packed or Shipped (Edit box, Mark as shipped); Arrived and Checked come
only from the Myanmar side – "Start Myanmar check" → Arrived, the bag and every item checked → Checked, undoing a
check → Arrived – and are rejected from the Japan role on the server too. Japan still sees them as read-only pills
(the Shipments filter keeps all four). Owner / Admin keep all four for corrections. CSV files may have an optional
`status` column (packed / shipped / arrived / checked; a Japan-side import gets a row error for arrived / checked).
There is no separate Customer requests page: customer orders are added and tracked on each customer's page
(**+ New order**). Each order has an **Order date** (default today in Myanmar time, Asia/Yangon; past dates allowed,
future dates rejected; editable under Edit; existing orders took the day they were created). The customer page lists
orders newest first, grouped by month; a card starts with "Order #12 · 18 Oct 2026" and shows the customer, items and note, a status dropdown set by hand –
**Ordered → Arrived → Delivered**, or **Cancelled** (sage / teal / green / grey) – and **Edit** (plus prices, payments and
money). Setting Delivered while the order still owes money asks first. Orders are not attached to box items any more
(old links stay in the database but aren't used). Customers are for Owner, Admin and the Myanmar checker; the
Japan sender doesn't see them.

**Shipped boxes only for Myanmar:** the Myanmar checker doesn't see a box (dashboard, Shipments,
Cargo history, or by direct link) until the Japan sender sets it to **Shipped** – with the one-tap **Mark as shipped** button on the box page
(or Edit box → Status → Shipped → Save).

**Dates:** *Received in Myanmar* can't be before *Shipped from Japan* (same day is allowed) – checked in the
app and in Django admin. Calendars don't offer dates before today (a date already saved on a box stays as it is).
Marking a box Shipped without a date records today, and the Myanmar check can't start before the ship date.

**Customers:** (Owner, Admin, Myanmar checker – not the Japan sender) a CRM-style table: avatar + name,
tap-to-call phone, address, Viber pill, orders (requests + items packed), a status tag from the orders (Waiting = has Ordered orders, Arrived = has Arrived
orders not yet delivered, All delivered, No orders), last update and a favourite star (starred customers stay on top). Live search,
Viber / Orders filters, sortable Name / Orders / Last update, 25 rows then "Load more"; rows become compact
cards on phones. Each customer has a detail page with all their requests and packed items. Customer names
typed on requests, Japan items or CSV rows create the customer automatically.

**Item card:** the Japan sender fills only Item name (optional), **Club option**, Flex, Qty, Amount and Note. Owner and
Admin also get Customer (optional, suggests existing customers). The Japan sender never sees customers: no Customer
field, no customer names on the box page, item or bag rows, and no customer names in their autocomplete (the server
leaves them out; a customer posted anyway is ignored, so an item's existing customer link stays as it is).
Club option is two taps – a category (Driver, Driver head, Wood, Hybrid, Iron, Wedge, Putter, Other), then a value
(Other = free text) – and is saved as structured data (e.g. "Iron · Graphite" → type Iron, shaft graphite;
"Driver head 9.5" → type Driver, loft 9.5, head only; "Wood 3W" → type Wood, club number 3W; "Hybrid 4H" → type Hybrid,
club number 4H; "Putter 34″" → type Putter, length 34 in). Club option as a whole is optional (bags and accessories
can have none), but a chosen category needs a value: Save is blocked with "Choose a Driver option" under the picker
(or "Type the Other item" for a free-text category) and the first card with the problem is highlighted. Tap the
selected category again to clear it. The same rule is checked on the server, in the CSV import ("Driver" alone is a row
error) and in `/admin/` for new or changed club fields. The owner manages it all in `/admin/`:
**Club categories** (add one like "Chipper", set the button order, hide it, or tick *free text* to show a text box
like Other – values can be added on the same page) and **Club options** (the value buttons). A category with no
active options and no free text isn't shown. Each card has **Copy**, and saved items have **copy** on the box page.
Brand, model, price, etc. are kept for old data, CSV and admin.

**One golf bag per box:** every box holds exactly one golf bag and all golf items are packed inside it.
The bag (name, color, optional customer, note) is stored as a Japan item flagged `is_bag`, so it is
checked in Myanmar (its own Matched / Missing / Wrong qty row at the top) like any other item. A box only becomes **Checked** when the bag and every item are checked.
Box lists, Cargo history, requests and the box page show the bag next to the box ID ("JP-7 · Titleist Stand Bag").
The rule is enforced in the Add box modal, on the box page (the bag can be edited but not copied; only Owner/Admin
can delete it), in CSV import and in `/admin/`. Owner/Admin can override it (a tick box in the modal, CSV import
and admin) and then get a clear warning. A box without a bag shows **No bag recorded** on its page with an
**Add bag** button (Owner/Admin can always use it; the Japan sender until the Myanmar check starts).
When upgrading, the migration marks an existing box's single item of type *Bag* as its golf bag; boxes with no
or several bag items are left for the owner to fix.

**Brand + Model:** item cards (Add box modal, Add item, the Golf bag card and customer order lines) have a **Brand**
dropdown – Bettinardi, Callaway, Cleveland Golf, Cobra, Mizuno, Odyssey, PING, PXG, Scotty Cameron, Srixon,
TaylorMade, Titleist, Wilson Staff, XXIO (A → Z), then **Other** – and a **Model** box. Brand is required, model is
optional. The Model box lists the models already saved for the chosen brand (models typed on the other cards of the open
box for the same brand first), matching ignoring case, spaces and punctuation; changing the brand refreshes the list; ↑ ↓
Enter Esc work on a keyboard and a one-tap "Did you mean …?" helps with a model 1–2 letters off. A new model is saved
for its brand when the item is saved, spelled with model codes in capitals ("mp 1400" → "MP 1400"). With **Other** the
model box is free text ("Honma TW757"). Shown everywhere as "<Brand> <Model>" ("XXIO MP1400"). The brand list is the
`Brand` table (**Admin → Brands**): Owner / Admin can add brands (they appear in the dropdown) or switch them off.
Migration 0022 seeds the list, maps old names (Cleveland → Cleveland Golf, Wilson → Wilson Staff, Ping → PING,
Parsons Xtreme Golf → PXG; their models move along), switches other brands off (kept, not deleted) and turns items of
those brands into Other with the same name. Shipments has a Brand filter and the box page has brand chips.

**One item card for boxes and orders:** customer order lines use the same card as the Add box modal
(`partials/item_card.html` + `forms.ItemCardMixin`): Brand, Model, the two-tap Club option (a value is required once a
category is chosen), Flex, Qty, Note, Copy, Delete, **+ Add item** and **Different prices** per piece. Only the money
differs: box items are in JP¥ ("Amount (JP¥)", ¥ in front), order lines in Myanmar Kyat ("Price (Ks)", Ks after),
whole numbers with the same digits-only box; price per piece × qty (or the piece prices added up) = line total, and the
order total is the sum of the lines (Total / Paid / Balance and "Deposit 20%"). The order form has no yen, bag card,
cargo fee or customer field. Order cards and the customer page show lines as
"XXIO MP1400 · Driver 10.5° · S ×1 — 1,400,000 Ks". The Japan sender never sees customer orders or Ks prices.

**Box ID:** new boxes get a monthly ID per cargo company, `<MON><YY>-<CARGO>-<n>` (e.g. `OCT26-KB-3`): the month
in Japan time, the cargo code, and the next number for that company that month (highest so far + 1, so deleting a box
never causes a duplicate; it starts at 1 each month). In the Add box modal it fills in as soon as a cargo company is
chosen ("Auto: 3rd KB box in October 2026") and stays editable; on save it is checked again and, if the number was taken
in the meantime, moved to the next free one with a notice. Boxes made before this keep their old `GN-3` style IDs.

**Money (JP¥):** the box has an optional **Cargo fee** (next to Box ID) and the bag and every item have an
optional **Amount** – the price per piece, **whole yen only** (`12500`, `12,500` and `¥12,500` all work; phones get the
number keypad). Every yen box (cargo fee, item and bag amount, each Different prices piece) takes digits only while you
type – a decimal point or minus is dropped with a short "Whole yen only, e.g. 12500" note, and a pasted `12,500.50`
keeps just the 12500 – and shows 12,500 once you leave it. The server rejects decimals, negatives and other text with
"Whole yen only, e.g. 12500"; the CSV import does the same per row. Item cards show the line total (amount × qty) when qty > 1. With qty ≥ 2 a **Different prices** box gives one
¥ box per piece (Piece 1, Piece 2, …, following qty); the line total is their sum and the box page shows
`¥45,000 + ¥41,500 = ¥86,500`. Unticking goes back to one price (the first piece's). Piece prices are stored on the item
(`piece_prices`) with the computed `line_total_jpy`, which box totals add up. Copy keeps the amount(s), and the Add box modal has a
live **Bag + Items + Cargo fee = Total** line above Save. The box page shows the same line; Shipments shows each box's
total. Amounts, cargo fees and totals are for Owner, Admin and the Japan sender only – the Myanmar checker never sees
them (pages, HTMX updates and the money script are all left out for them).

**Customer payments (Myanmar Kyat, whole numbers, no exchange rate, no credit):** an order has one or more item
lines (`OrderLine`, entered on the shared item card above; **+ Add item** in the New order form and in Edit).
Order total = sum of the line totals. Each payment belongs to one order (`Payment`: date,
amount, method, note, recorded by; no proof photos): **+ Add payment** on the order card, with a
**Deposit 20%** button that fills 20% of the total (rounded to whole Kyat, still editable). A payment can't be more
than the order's balance. Methods: Cash, KPay, KBZ Special, AYA Pay, AYA Special, CB Special (Admin → Payment
methods), each shown as a coloured pill (green, blue, dark blue, red, dark red, teal; the colour is stored on the
method and editable in /admin/, new methods are grey until one is picked). Payments are listed newest first, date
first ("17 Oct 2026   100,000 Ks   [AYA Pay]", then "recorded by … · note") on the order card and in the customer
page's Payment history. An order without a price shows "—" for Total and Balance (Paid shows what was paid, or
"—") and is never Fully paid. The card shows Total / Paid / Balance, the
payment list and a status: Not paid / Deposit paid / Fully paid (Price not set when a line has no price, Paid (old
order) for settled old orders). **Copy reminder** copies a Burmese + English message with the items, total, paid and
balance; setting Delivered while the order still owes money asks first. Customers table: Balance (sum of the order
balances), Money status (All clear / Owes X Ks / Price not set), a Money filter (All / Owes money / All clear) and
sort by Balance, plus an **Ordered from – to** date range (customers with an order in that range). "Last update" is
the customer's latest order date. The Copy reminder names the order number and date. The customer page lists Total / Paid / Balance per order. Owner / Admin can delete payments, edit
them in /admin/ (order inlines), and tick old orders → **Mark as paid (old order)** (also an /admin/ action). Owner,
Admin and the Myanmar checker see and add payments; the Japan sender never sees customer prices or payments
(templates and endpoints). Migration 0018 turns each old single-item order into one line and keeps every old
payment on the order it was put on (no credit, nothing carried between orders); 0021 drops the proof photo fields
(uploaded files stay in `media/`) and clears the old automatic "Carried over from earlier payments" note.

**Customer icon:** `static/img/icons/customer.png` (display WebP copy made by `scripts/make_display_images.py`,
background cut out) is the sidebar's **Customers** menu icon, like the Shipments and Cargo history icons (24px, the
same in the phone menu; the line icon shows instead if the file is missing). Customer names themselves have no icon
or emoji; the Customers list keeps its coloured initials circles.

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
python manage.py setup_roles                  # the 4 role groups: Admin, Owner, Japan Sender, Myanmar Checker
python manage.py createsuperuser              # your own Admin account (choose the username and password)
python manage.py seed_demo                    # cargo companies JP/GN/KL/KAG + starter catalog
# optional: python manage.py seed_demo --demo  # sample boxes and requests to play with

python manage.py runserver
```

Open http://127.0.0.1:8000 and log in. Add the other people in `/admin/` → Users (and put each one in their
group). To change a password: `python manage.py changepassword <username>` (or use `/admin/`).
Edit the cargo company names under **Admin → Cargos**.

To try it from a phone on the same Wi-Fi:
`ALLOWED_HOSTS=192.168.1.20,localhost python manage.py runserver 0.0.0.0:8000`
(use your computer's IP address).

Run the tests with `python manage.py test`.

## CSV format for Japan lists

```
box,cargo,kind,brand,model,color,club_option,flex,qty,amount_jpy,cargo_fee_jpy,customer,note
,GN,bag,Titleist,Stand Bag,Black,,,1,8000,"12,500",Ko Aung,
,GN,,Titleist,tsr2,,Driver 10.5°,S,1,"¥25,000",,Ko Aung,with cover
,GN,,Other,,,Driver head 9.5,,2,6500;5800,,,
,GN,,Mizuno,JPX 923,,Iron · Graphite,R,1,,,,
,KL,bag,PING,Hoofer,Navy,,,1,,9000,,
,KL,,Cleveland Golf,RTX 6,,Wedge 52,,1,4200,,,
```

* Required: `box` (or `cargo` for a new box) and `brand` on each row. Other columns are optional.
* `brand` must be a name from the Brand list (case doesn't matter; the old names Cleveland, Wilson, Ping and Parsons
  Xtreme Golf are accepted) or `Other`, otherwise the row is an error; `model` is free text. The old one-text `item`
  column is no longer read (a row using it gets an error asking for brand + model).
* `amount_jpy` is the price per piece (`45000;41500` = a different price for each piece; the count must equal qty); `cargo_fee_jpy` goes on the box's bag row. Both are whole yen and accept
  `12500`, `12,500` or `¥12,500`.
* `kind` = `bag` marks the box's golf bag (its `brand` and `model`, plus `color`, `customer`, `note`; qty 1);
  empty (or `item`) is an item inside the bag. Each box needs **exactly one** bag row – or must already have its
  bag when you add items to it. A box with no bag or two bags is rejected with its row numbers, e.g.
  *Row 4: Box GN-6 has 2 bag rows (rows 4, 6)*. Older files with a row of `type` *Bag* count as the bag row.
  Owner/Admin can tick *Owner override* to import such boxes anyway; they are listed as warnings.
* `club_option` is one label from **Admin → Club options**, e.g. `Driver 10.5`, `Driver head 9.5`,
  `Iron · Graphite` (the older `Iron Graphite 5` still works), `Iron Steel`, `Wedge 52°`, `Wood 3W`, `Hybrid 4H`, `Putter 34` (the `°`, `·` and `″` are
  optional, case doesn't matter). Woods and hybrids also accept the number alone – `wood 3`, `Hybrid 4` – or just
  `3W` / `4H`. It fills the structured columns (type, loft, shaft, club number, length, head only) for you.
* Older columns still work: `type`, `loft`, `club_type`, `club_number`, `shaft_material`,
  `specification`, `color`, `condition`, `price_jpy`, `quantity_unit`; friendlier headers like `amount`, `unit` too. If a row has both, `club_option` wins.
* `box`: leave it **empty** and put the cargo code in `cargo` to get new Box IDs (each bag row starts a new box, the
  item rows below it go in it), or give a Box ID like `OCT26-KB-3`. Older IDs like `GN-3` only work for boxes that
  already exist. The cargo company must already exist.
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
python manage.py setup_roles
python manage.py seed_demo
gunicorn agom.wsgi --bind 0.0.0.0:8000
```

Static files are served by WhiteNoise (compressed, with hashed names for cache-busting), so no extra web
server config is needed. **Render (Singapore) is set up in `render.yaml` – step by step in [DEPLOY.md](DEPLOY.md).**
The app stores no uploaded files (CSV imports are read, not kept), so no media storage is needed.

**Time zones:** each user sees dates and times in their own zone – Japan Sender: Asia/Tokyo, Myanmar Checker:
Asia/Yangon, Owner / Admin: Asia/Bangkok, or the zone set on the user in `/admin/` (shown in the user card,
e.g. "Japan Sender · JST"). Business rules don't follow the viewer: Box IDs use Japan time, order dates and the
date checks use the site's `TIME_ZONE` (Asia/Yangon).

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

* New boxes store `Box.code` (`OCT26-KB-3`) plus `period` (`OCT26`) and `seq` (3); (cargo, period, seq) and `code`
  are unique. Boxes made before monthly IDs keep `box_no` and their `GN-3` label.
* The golf bag is a Japan item with `is_bag = True` (type "Golf bag", qty 1); it sorts first in every list.
* Japan items store: name, type, qty, quantity unit, brand, model, club / item type, club number, loft, flex,
  shaft material, specification, color, Japan condition, amount (optional), customer and note.
* `ReceiveCheck` is one-to-one with `JapanItem`, so tapping a button again overwrites the check.
* `CustomerRequest` still has optional `box` and `japan_item` links from the old "Attach to box item" (kept so no data
  is lost; not used or shown). Its status is set by hand: Ordered / Arrived / Delivered / Cancelled (migration 0017
  mapped Open/Ordered → Ordered, In box → Arrived if its box had arrived, else Ordered, Fulfilled → Delivered).
  The customer page's "Items packed for <name>" lists Japan items whose Customer field (set by Owner / Admin) is
  that name – it doesn't use those links.
* `Catalog` entries feed the type / model / loft suggestions in the item and request forms. Manage them in `/admin/`.
