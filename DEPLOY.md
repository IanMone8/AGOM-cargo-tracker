# Putting AGOM Cargo Tracker online (Render, Singapore)

This guide is written for a first deployment. You click through the Render website; the repository already
contains everything Render needs (`render.yaml`). Allow about 30 minutes.

What you will have at the end:

- a web address like `https://agom-cargo-tracker.onrender.com` that works on phones and computers,
- a PostgreSQL database in Singapore (close to Myanmar and Japan) with automatic backups,
- the app updating itself every time a new version is merged into `main` on GitHub.

> **Never put passwords or secret keys in GitHub, in chat or in this file.** Render creates the secret key
> itself, and you type passwords only into the website.

---

## 1. Create a Render account

1. Go to <https://render.com> and click **Get Started**.
2. Choose **Sign up with GitHub** and log in with the GitHub account that owns `IanMone8/AGOM-cargo-tracker`.
3. Render asks to access your GitHub repositories. Choose **Only select repositories**, pick
   **AGOM-cargo-tracker**, and approve.
4. Add a payment card under **Account settings → Billing**. The setup uses two small paid plans
   (see "Costs" below), because the free database is deleted after 30 days and has no backups.

### Costs

`render.yaml` asks for:

| Part | Plan | Why |
|---|---|---|
| Web service `agom-cargo-tracker` | **Starter** | always on (free web services fall asleep), and it gives you the **Shell** tab used in step 4 |
| Database `agom-db` | **Basic-256mb** (PostgreSQL 16) | kept for good, with automatic backups |

Check today's prices on <https://render.com/pricing> before you click **Apply**. You can change a plan later
in the Render dashboard (service → **Settings → Instance type**).

## 2. Connect the repository (Blueprint)

1. In the Render dashboard click **New +** → **Blueprint**.
2. Pick the repository **IanMone8/AGOM-cargo-tracker** and the branch **main**.
3. Render reads `render.yaml` and shows two things to create:
   - **agom-cargo-tracker** – Web Service, region **Singapore**
   - **agom-db** – PostgreSQL, region **Singapore**
4. Give the Blueprint a name (e.g. `AGOM`) and click **Apply**.

Render now creates the database first, then builds and starts the web service.

## 3. Environment variables

`render.yaml` sets these for you – you don't have to type anything for the first deploy:

| Variable | Value | Notes |
|---|---|---|
| `SECRET_KEY` | made up by Render | long random value; never copy it anywhere |
| `DATABASE_URL` | link to `agom-db` | filled in automatically |
| `DEBUG` | `false` | must stay `false` online |
| `TIME_ZONE` | `Asia/Yangon` | the business time zone (order dates, date checks) |
| `PYTHON_VERSION` | `3.12.8` | |

The web address (`…onrender.com`) is allowed automatically. **Only if you add your own domain later**
(e.g. `cargo.agom.com`, under **Settings → Custom Domains**), open the web service → **Environment** and add:

- `ALLOWED_HOSTS` = `cargo.agom.com`
- `CSRF_TRUSTED_ORIGINS` = `https://cargo.agom.com`

then click **Save Changes** (Render redeploys by itself).

## 4. First deploy

1. Open the **agom-cargo-tracker** service and click **Logs**. You will see, in order:
   - `pip install -r requirements.txt`
   - `collectstatic` – "… static files copied"
   - `migrate` – "Applying tracker.00… OK" lines (the empty database gets all its tables)
   - `setup_roles` – "Created group Admin / Owner / Japan Sender / Myanmar Checker"
   - then gunicorn starts and Render shows **Your service is live 🎉**.
2. Click the web address at the top of the page (e.g. `https://agom-cargo-tracker.onrender.com`). You should see
   the login page with the golf scene. If the name `agom-cargo-tracker` was already taken, Render adds a few
   letters – use the address it shows.

If the build fails, the log shows the reason in red near the end. The previous version (if any) keeps running.

## 5. Create the people who will use it

### Your own Admin account

1. In the web service, open the **Shell** tab.
2. Type:

   ```bash
   python manage.py createsuperuser
   ```

3. Answer the questions: a username (your own choice), an email (can be empty) and a **strong password**
   (it isn't shown while you type – that is normal). Type it twice.

### Everyone else (in the website)

1. Open `https://<your address>/admin/` and log in with the account you just made.
2. **Users → Add user**: type a username and a strong password (twice) → **Save and continue editing**.
3. On the next page:
   - **Groups**: choose exactly one – `Owner`, `Admin`, `Japan Sender` or `Myanmar Checker`.
     Groups decide what each person can see and do (and which dashboard banner they get).
   - **Staff status**: tick it only for Owner / Admin (lets them open `/admin/`).
   - **Time zone** (bottom): leave empty to use the role's zone (Japan Sender → Tokyo, Myanmar Checker → Yangon,
     Owner / Admin → Bangkok), or pick another one.
4. **Save.** Give each person their username and password in person or by phone – not in a group chat.

### Strong passwords

- At least **12 characters**, best a few random words (`river-mango-tiger-42`), different for every person.
- Keep them in a password manager (Apple Passwords / iCloud Keychain, Bitwarden, 1Password).
- To change someone's password: `/admin/` → **Users** → the user → **Password: change** (or in the Shell:
  `python manage.py changepassword <username>`).
- When someone leaves, untick **Active** on their user instead of deleting it (their history stays).

### Cargo companies

Add the cargo companies in `/admin/` → **Cargos** (code such as `KB`, and a name). Or, once, in the Shell:
`python manage.py seed_demo` adds JP, GN, KL and KAG plus a starter catalog (do **not** use `--demo` online –
that adds sample boxes).

## 6. Backups

- The **Basic** database plan makes **automatic daily backups** with point-in-time recovery. Find them in the
  dashboard: **agom-db → Recovery** (the exact number of days kept depends on the plan – shown there).
- To restore: **agom-db → Recovery → Restore** to a time before the problem. Render creates a *new* database from
  it; then point the web service at it (web service → **Environment** → `DATABASE_URL` → the new database's
  **Internal Database URL**). Ask for help before doing this the first time.
- Extra copy once a month (recommended): **agom-db → Recovery → Export** (or **Backups → Download**, depending
  on the dashboard version) and keep the file on your Mac / iCloud Drive.

## 7. Updating after a new release

1. Merge the new pull request into `main` on GitHub.
2. Render sees the change and deploys by itself (`autoDeploy: true`): install → collectstatic → **migrate**
   (database changes are applied automatically) → start. Watch **Logs** until **Your service is live**.
3. Open the site and check the dashboard and one box page.
4. Something wrong? Web service → **Events** → pick the previous deploy → **Rollback**. (A rollback doesn't undo
   database changes; for that, see Backups above.)
5. Tag the release on GitHub as usual (e.g. `v1.1.0`) so you know which version is online.

## 8. If something doesn't work

| You see | What to do |
|---|---|
| **Bad Request (400)** on your own domain | add the domain to `ALLOWED_HOSTS` (step 3) |
| **CSRF verification failed** when logging in on your own domain | add `https://your-domain` to `CSRF_TRUSTED_ORIGINS` |
| The site looks unstyled | the build log should show `collectstatic`; redeploy with **Manual Deploy → Clear build cache & deploy** |
| "SECRET_KEY environment variable is required" | the `SECRET_KEY` variable is missing – add it in **Environment** with **Generate** |
| A page shows **Server Error (500)** | open **Logs** – the error is printed there |
