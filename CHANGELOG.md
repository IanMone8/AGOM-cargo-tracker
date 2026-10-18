# Changelog

## v1.0.0 — 18 Oct 2026

The first release of AGOM Cargo Tracker: golf items shipped from Japan to Myanmar, packed by box, checked on arrival,
and sold to customers. Built with Django, HTMX and Tailwind; works on phones and desktops.

### Roles
- Four roles: **Admin** (site maintenance and /admin/), **Owner** (everything), **Japan Sender** (boxes and Japan
  lists) and **Myanmar Checker** (arrival and checks). Roles come from user groups, not usernames.
- The Japan sender never sees customers, customer orders or prices in Kyat, and can't use CSV import.
- The Myanmar checker only sees boxes once they are shipped, and never sees yen amounts.

### Boxes and items
- **Add box** window: cargo company, Box ID, cargo fee, a note, the box's **golf bag** (every box holds exactly one)
  and the items packed inside it, each on its own card with Copy and Delete.
- Item card: **Brand** dropdown (Bettinardi, Callaway, Cleveland Golf, Cobra, Mizuno, Odyssey, PING, PXG, Scotty
  Cameron, Srixon, TaylorMade, Titleist, Wilson Staff, XXIO, or Other), **Model** with suggestions of that brand's saved
  models, a two-tap **Club option** picker (Driver, Driver head, Wood, Hybrid, Iron, Wedge, Putter, Other – a chosen
  category needs a value), flex, qty and note. Brands and club options are editable in /admin/.
- Amounts in whole yen: price per piece, or **different prices** per piece, with live line totals and a
  Bag + Items + Cargo fee = Total line.
- **Mark as shipped** in one tap. Status moves Packed → Shipped → Arrived → Checked; the Japan sender can only set
  Packed or Shipped, and can't change a box once the Myanmar check has started. Owner / Admin can correct anything.
- Shipments list with cargo, status and brand filters; box page with brand chips; **Cargo history** per cargo
  company with transit days.
- **CSV import** of Japan packing lists (Owner / Admin): several boxes per file, one bag row per box, brand from the
  list, club option labels, piece prices, an optional status column; the whole file is checked before anything is
  saved, with row-numbered errors.

### Box ID format
- New boxes get a monthly ID per cargo company: `<MON><YY>-<CARGO>-<n>`, e.g. **OCT26-KB-3** (month in Japan time,
  numbering restarts each month). It fills in when the cargo company is chosen and moves to the next free number if
  taken in the meantime. Older boxes keep IDs like GN-3.

### Myanmar checks
- **Start Myanmar check** records the arrival date and sets the box to Arrived.
- One tap per item (bag first): **Matched**, **Missing** or **Wrong qty** (with the quantity received), plus
  **extra items** found in the box, and a live mismatch summary.
- When the bag and every item are checked the box becomes Checked; undoing a check sets it back to Arrived.
- The received date can't be before the ship date.

### Customers, orders and payments
- **Customers** table (Owner, Admin, Myanmar checker): search, Viber / orders / money filters, an order date range,
  sorting (name, orders, balance, last update), favourites, tap-to-call phone numbers, and a customer page.
- **Customer orders** with an order date (Myanmar time, no future dates), one or more items on the same item card as
  boxes but priced in **Myanmar Kyat**, and a hand-set status: Ordered, Arrived, Delivered or Cancelled. Orders are
  listed newest first, grouped by month.
- **Payments** belong to one order: date, amount, method (Cash, KPay, KBZ Special, AYA Pay, AYA Special, CB Special –
  coloured pills, editable in /admin/) and note. **Deposit 20%** button, no payment above the balance, and
  Total / Paid / Balance with Not paid / Deposit paid / Fully paid. Orders without a price show "—".
- Customer balances and "Owes X Ks" / "All clear" in the table, a payment history on the customer page,
  **Copy reminder** (Burmese + English), a warning before marking an owing order Delivered, and
  **Mark as paid (old order)** for orders paid before the app.

### Dashboard banners
- English banner with the AGOM badge for Owner and Admin.
- Burmese banner for the **Myanmar checker** with an animated sticker in each corner (still images with reduced
  motion).
- Burmese thank-you banner for the **Japan sender** with the girl and boy pictures.
- Dashboard stat cards: cargos, total boxes, boxes checked in Myanmar, and boxes that need review (differences).

### Look and login
- Green golf theme from one colour palette (WCAG AA contrast), raised 3D-style cards and pressable buttons, sidebar
  with a slide-in menu on phones, "© AGOM 2026" above the user card.
- Login page with an animated "Fairway at dawn" scene (ball rolls into the cup; reduced motion shows the finished
  scene), show/hide password.
- AGOM 3D logo as the site icon and the phone home-screen app icon; small display copies of all images
  (`scripts/make_display_images.py`).
