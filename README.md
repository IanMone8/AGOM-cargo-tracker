# AGOM Cargo Tracker

A small, mobile-friendly web app for tracking golf items shipped from **Japan → Myanmar**
through several cargo companies (JP, GN, KL, KAG, …).

Built with **Django 5**, **HTMX** (one-tap checks, no page reloads) and **Tailwind** (CDN).
It uses SQLite in development and Postgres in production (via `DATABASE_URL`).

## Features

| Page | What it does |
| --- | --- |
| **Login** | Django's built-in auth |
| **Dashboard** | Boxes in transit · arrived but not checked · with mismatches · open customer requests |
| **Boxes** | List with cargo / status filters (live, via HTMX) |
| **Box detail** | Japan list with one-tap **✓ Matched / ✗ Missing / ± Wrong qty**, undo, add extra items, a live mismatch summary, and an "Arrived today" button |
| **Requests** | Customer requests: create, change status inline, **attach to a Japan box item** (suggested matches or pick box → item) |
| **CSV import** | Import Japan packing lists (several boxes per file), all-or-nothing validation, template download |
| **Admin** | Every model is registered at `/admin/` |

### Box status rules
Status is `Packed → Shipped → Arrived → Checked`. It moves forward automatically:

* a Japan ship date sets **Shipped**
* a Myanmar received date (or the *Arrived today* button) sets **Arrived**
* once every Japan item has a check result, the box becomes **Checked** (undoing a check moves it back to Arrived)

You can still pick a status by hand on the *Edit* page. A manual choice overrides the automatic rules.

### Roles (4 users)

| User | Role | Can do |
| --- | --- | --- |
| `admin` | Superuser | Everything, including `/admin/`, users and the catalog |
| `owner` | Group **Owner** (staff) | Everything in the app, plus tracker data in `/admin/` |
| `japan` | Group **Japan Sender** | Create/edit boxes (Japan side), add/delete Japan items, CSV import |
| `myanmar` | Group **Myanmar Checker** | Mark arrived, set the received date, receive checks, extra items |

Everyone can view all pages and manage customer requests.

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
box,type,item_name,flex,loft,qty,condition,price_jpy,customer,note
GN-3,Driver,Titleist TSR2,S,10,1,used,25000,Ko Aung,with cover
GN-3,Iron set,Mizuno JPX 923 5-P,R,,1,new,98000,,
KL-1,Putter,Scotty Cameron Newport 2,,,2,used,,,34 inch
```

* Required: `box`, `type`, `item_name`. The other columns are optional (`qty` defaults to 1, `condition` to `new`).
* `box` is the cargo code plus the number (`GN-3`). The cargo company must already exist; missing boxes are created.
* `flex` is `R`, `SR`, `S` or empty. `price_jpy` may contain `¥` and commas.
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
templates/       base layout, pages, HTMX partials
```

## Data model notes

* `Box.box_no` is a number; the label (`GN-3`) is built from the cargo code.
* `ReceiveCheck` is one-to-one with `JapanItem`, so tapping a button again overwrites the check.
* `CustomerRequest` has optional `box` **and** `japan_item` links. Attaching sets the status to *In box*; detaching sets it back to *Ordered*.
* `Catalog` entries feed the type / model / loft suggestions in the item and request forms. Manage them in `/admin/`.
