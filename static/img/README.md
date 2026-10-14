Originals live here; the app shows small copies from `display/` (made by
`python scripts/make_display_images.py`, needs `pip install Pillow`). After replacing an original,
run the script again and commit the new `display/` files.

Images used by the app:

- `3D logo.png` – the AGOM logo: sidebar, phone header, login page and browser-tab icon
- `3D cover logo.png` – the app icon when the site is added to a phone's home screen (iPhone and Android);
  a square PNG of about 512×512 works best
- `icons/plane.png`, `icons/boxes.png`, `icons/box-checked.png`, `icons/warning.png` –
  dashboard stat card icons (Cargos, Total boxes, Checked in Myanmar, Needs review)

- `icons/house.png`, `icons/flying-box.png`, `icons/calendar.png` – sidebar icons for
  Dashboard, Shipments and Cargo history (a built-in line icon is shown if a file is missing)

Any other file that is missing is simply hidden.
