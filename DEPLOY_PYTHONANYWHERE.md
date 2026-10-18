# Putting AGOM Cargo Tracker online on PythonAnywhere (free plan)

This guide is for a first deployment. You will type a few commands into a black **Bash console** window in your
web browser and click through the **Web** tab. Copy each command exactly. Allow about 45 minutes.

At the end you will have:

- the site at **https://agom.pythonanywhere.com** (works on phones and computers),
- the database (one SQLite file) in `/home/agom/data/`, **outside** the code folder, so updating the code never
  touches your data,
- a backup command that keeps the last 14 copies of the database.

> **Never put passwords or the secret key in GitHub, in chat or in this file.** They go only into the `.env` file
> on PythonAnywhere (step 5) and into the website itself.

Free plan limits to know: one web app, about 512 MB of disk, and the site must be **renewed once a month**
(step 12). Everything in this guide works on the free plan.

---

## 1. Sign up

1. Go to <https://www.pythonanywhere.com> → **Pricing & signup** → **Create a Beginner account** (free).
2. Username: **agom** (this makes the address `agom.pythonanywhere.com`). Use your email and a strong password.
3. Confirm your email from the message PythonAnywhere sends you.

## 2. Open a Bash console

**Dashboard → Consoles → "Bash"**. A black window opens with a prompt like `09:30 ~ $`. Type (or paste) each
command below and press **Enter**. You can close the window any time and open it again later from **Consoles**.

## 3. Get the code (git clone)

```bash
cd ~
git clone https://github.com/IanMone8/AGOM-cargo-tracker.git
cd AGOM-cargo-tracker
git checkout main
```

If the repository is **private**, git asks for a username and password: type your GitHub username, and as the
password a GitHub **personal access token** (GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained → only this repository, permission *Contents: Read-only*). The token is shown once – keep it in your
password manager.

## 4. Virtualenv and packages

```bash
mkvirtualenv --python=/usr/bin/python3.12 agom-venv
pip install -r ~/AGOM-cargo-tracker/requirements.txt
```

The prompt now starts with `(agom-venv)`. Whenever you open a new console later, first type `workon agom-venv`.

## 5. The `.env` file (settings and secrets)

1. Make the folders for the data and the backups, and a secret key:

   ```bash
   mkdir -p ~/data/backups
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

   Copy the long random line it prints (select it with the mouse; it is copied).

2. Create the file:

   ```bash
   cd ~/AGOM-cargo-tracker
   nano .env
   ```

3. Paste this, and replace `PASTE-THE-LONG-RANDOM-LINE-HERE` with the line you copied:

   ```
   SECRET_KEY=PASTE-THE-LONG-RANDOM-LINE-HERE
   DEBUG=false
   ALLOWED_HOSTS=agom.pythonanywhere.com
   CSRF_TRUSTED_ORIGINS=https://agom.pythonanywhere.com
   SQLITE_PATH=/home/agom/data/agom.sqlite3
   BACKUP_DIR=/home/agom/data/backups
   STATIC_ROOT=/home/agom/AGOM-cargo-tracker/staticfiles
   TIME_ZONE=Asia/Yangon
   ```

4. Save: **Ctrl+O**, **Enter**, then **Ctrl+X** to leave nano. Make the file private:

   ```bash
   chmod 600 .env
   ```

`.env` is listed in `.gitignore`, so it is never sent to GitHub and `git pull` never changes it.

## 6. Create the database, static files and role groups

```bash
cd ~/AGOM-cargo-tracker
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py setup_roles
python manage.py check --deploy
```

- `migrate` creates `/home/agom/data/agom.sqlite3` with all its tables (many "Applying … OK" lines).
- `collectstatic` copies the CSS, pictures and icons into `staticfiles/` ("… static files copied").
- `setup_roles` creates the four groups: **Admin, Owner, Japan Sender, Myanmar Checker**.
- `check --deploy` should end with **"System check identified no issues (0 silenced)."**

Optional – the cargo companies JP, GN, KL, KAG and a starter catalog (do **not** add `--demo`, that makes sample
boxes): `python manage.py seed_demo`. You can also add cargo companies later in `/admin/` → **Cargos**.

## 7. Create the 4 users

### Your own Admin account (in the console)

```bash
python manage.py createsuperuser
```

Choose a username, leave the email empty if you like, and type a **strong password** twice (nothing shows while
you type – that is normal).

### The other three (after step 8, in the website)

1. Open `https://agom.pythonanywhere.com/admin/` and log in with the Admin account.
2. **Users → Add user**: username + strong password (twice) → **Save and continue editing**.
3. On the next page:
   - **Groups**: choose exactly one – `Owner`, `Japan Sender` or `Myanmar Checker`. The group decides what the
     person can see and do, and which dashboard banner they get.
   - **Staff status**: tick it **only for the Owner** (it opens `/admin/` for them).
   - **Time zone** (at the bottom): leave empty to use the role's zone – Japan Sender → Tokyo,
     Myanmar Checker → Yangon, Owner / Admin → Bangkok – or pick another.
4. **Save.** Repeat for each person.

### Strong passwords

- At least **12 characters** – best a few random words, e.g. `river-mango-tiger-42` – and different for each
  person. Keep them in a password manager (Apple Passwords / iCloud Keychain, Bitwarden, 1Password).
- Tell each person their password in person or by phone, not in a group chat.
- Change a password: `/admin/` → **Users** → the user → **Password: change**, or in the console
  `python manage.py changepassword <username>`.
- When someone leaves, untick **Active** on their user instead of deleting it (their history stays).

## 8. Set up the Web tab

1. **Web** tab → **Add a new web app** → **Next** → choose **Manual configuration** (not "Django") →
   **Python 3.12** → **Next**.
2. In the **Code** section:
   - **Source code**: `/home/agom/AGOM-cargo-tracker`
   - **Working directory**: `/home/agom/AGOM-cargo-tracker`
3. Click the **WSGI configuration file** link (`/var/www/agom_pythonanywhere_com_wsgi.py`). Delete **everything**
   in it, paste the following, and click **Save**:

   ```python
   import os
   import sys

   path = "/home/agom/AGOM-cargo-tracker"
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ["DJANGO_SETTINGS_MODULE"] = "agom.settings"   # settings read /home/agom/AGOM-cargo-tracker/.env

   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```

4. **Virtualenv** section: enter `/home/agom/.virtualenvs/agom-venv` and click the tick.
5. **Static files** section → **Enter URL** / **Enter path**:
   - URL: `/static/`
   - Directory: `/home/agom/AGOM-cargo-tracker/staticfiles`
6. **Security** section: turn **Force HTTPS** on.
7. Click the big green **Reload agom.pythonanywhere.com** button at the top.
8. Open **https://agom.pythonanywhere.com** – you should see the login page with the golf scene. Log in.

Something wrong? The **Web** tab has links to the **Error log** – the newest error is at the bottom.

## 9. Updating after a new release

When a new version has been merged into `main` on GitHub:

```bash
workon agom-venv
cd ~/AGOM-cargo-tracker
python manage.py backup_db                 # safety copy first
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py setup_roles
```

Then **Web** tab → **Reload agom.pythonanywhere.com**, open the site and check the dashboard and one box page.

If something is wrong after an update: go back to the previous version with `git log --oneline` (find the old
line) and `git checkout <that-id>`, then Reload; restore the database from a backup (step 10) if a migration
changed it. Ask for help before doing this the first time.

## 10. Backups

**Make a backup** (it takes a second; the site keeps working):

```bash
workon agom-venv
cd ~/AGOM-cargo-tracker
python manage.py backup_db
```

It saves `/home/agom/data/backups/agom-YYYY-MM-DD-HHMMSS.sqlite3` and keeps the **newest 14** (older ones are
deleted). Do it at least once a week and always before an update.

**Daily, automatically:** if your **Tasks** tab lets you create a scheduled task, add a **daily** task with
this command (one line):

```
/home/agom/.virtualenvs/agom-venv/bin/python /home/agom/AGOM-cargo-tracker/manage.py backup_db
```

**Download a backup to your Mac:** **Files** tab → open `data` → `backups` → click the newest
`agom-….sqlite3` → the **download** button. Keep a copy in iCloud Drive or on a USB stick once a month.

**Restore a backup** (only if something went badly wrong): in the console

```bash
cp ~/data/agom.sqlite3 ~/data/agom-before-restore.sqlite3
cp ~/data/backups/agom-YYYY-MM-DD-HHMMSS.sqlite3 ~/data/agom.sqlite3
```

then **Reload** in the Web tab.

## 11. Things to know

- The site's code is at `/home/agom/AGOM-cargo-tracker`, the data at `/home/agom/data` – don't delete `data`.
- Nothing is uploaded or stored as files by the app (CSV imports are read and not kept), so there is no
  "media" folder to set up or back up.
- `.env` holds the secret key; if it is ever shared by mistake, make a new one (step 5.1), put it in `.env`
  and Reload (everyone will need to log in again).

## 12. Every month: keep the site running

Free web apps stop unless they are renewed. Once a month (PythonAnywhere also sends a reminder email):

**Web** tab → **Run until 1 month from today** → click the button.

Put a monthly reminder in your calendar so the site never goes offline.
