# IACC Waybill Management — database-backed deployment

This package is prepared for a Render web service with an external PostgreSQL database. It has **not** been deployed. The existing SQLite database file was not included in this package, so no waybill/user/customer/driver/audit data has been migrated yet.

## Included
- Flask application with login, user management, customer and driver masters, waybill creation/edit/search, PDF output, and audit history.
- PostgreSQL URL compatibility for `postgres://` and `postgresql://` URLs.
- Secure session-cookie settings for HTTPS hosting.
- IACC logo and port background image for the app (PDF waybills remain print-friendly).
- `migrate_sqlite_to_postgres.py`: one-time migration utility that opens SQLite read-only, refuses a non-empty PostgreSQL target, copies user/customer/driver/waybill/audit rows in dependency order, preserves IDs and hashes where the schemas overlap, and resets PostgreSQL ID sequences.

## Before deploying
1. Locate your current SQLite file, usually `iacc_waybills.db` beside the existing app. If the existing system is already using a hosted database, identify that provider and back it up using its native export/backup method instead; this script is specifically for SQLite-to-PostgreSQL migration.
2. Make a separate SQLite backup. Do not upload `.db` files, database passwords, `.env` files, or exports to GitHub.
3. Test the migration using a new empty PostgreSQL database before switching staff to the hosted app.
4. Keep a verified backup and confirm source/target row counts before using the hosted database as the official record.

## Free-tier notes
The web service is configured for Render's free web plan. For the database, use a free PostgreSQL provider such as Neon if its current free plan fits your needs. Free-tier availability, storage, compute, inactivity behaviour, backup guarantees, and quotas can change; check provider terms. Free tiers are not a guarantee of production-grade availability. Export company data regularly to secure company-controlled storage.

## 1. Create a PostgreSQL database
1. Create a database with your selected PostgreSQL provider (for example https://neon.tech/).
2. Copy the PostgreSQL connection string privately. Do not send it in chat or commit it to Git.
3. Use a database in a region reasonably close to the Render web service.

## 2. Upload code to a private GitHub repository
1. Extract this ZIP.
2. Create a **private** repository at https://github.com/new.
3. Upload the extracted application files to the repository root.
4. Do not upload the local database or backups. The `.gitignore` excludes common SQLite and environment files.

## 3. Deploy on Render
1. Open https://dashboard.render.com/blueprints.
2. Create a Blueprint instance and connect the private repository.
3. Render reads `render.yaml`; the web service start command is `waitress-serve --listen=0.0.0.0:$PORT app:app`.
4. Set `DATABASE_URL` to the PostgreSQL connection string in Render's environment settings.
5. Set `IACC_SECRET_KEY` to a long random secret. Render's Blueprint can generate this automatically.
6. Set `IACC_ADMIN_PASSWORD` to a strong, unique initial admin password before first startup. The production app refuses to create the initial admin if this is missing.
7. Keep `IACC_SECURE_COOKIES=true` for HTTPS.
8. Deploy and review the build/runtime logs. The service name is `iacc-waybill`, but the final hostname is determined by Render and may differ if the name is unavailable.

## 4. Migrate an existing local SQLite database
**Do not run migration against a target database containing live data.** The script intentionally requires the destination application tables to be empty. Use a fresh, empty PostgreSQL database for the migration.

On the Windows computer that has `iacc_waybills.db`:
1. Install Python 3.11+ if needed.
2. Extract this package to a local folder and open Command Prompt in that folder.
3. Create/activate a virtual environment and install requirements:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Set the database URL in the current Command Prompt session only. Replace the placeholder with your private provider connection string; do not include the actual password in a screenshot or message:

   ```bat
   set DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
   ```
5. Run the migration using the path to the backed-up database file:

   ```bat
   python migrate_sqlite_to_postgres.py "C:\path\to\iacc_waybills.db"
   ```

The script reads the SQLite file in read-only mode and copies rows directly to PostgreSQL over the TLS-enabled connection string. It does not send data to GitHub. If the target is not empty or a schema/table is missing, it stops rather than attempting a risky merge.

If your current system already uses PostgreSQL or another hosted database, do not use this SQLite migration script. Use a provider-native backup/export and a migration tailored to that database instead.

## 5. Verify before users switch over
- Compare row counts for user, customer, driver, waybill, and audit tables.
- Test admin and existing-user login with existing passwords (password hashes are preserved by the SQLite migration).
- Create, edit, search, retrieve, and print a test waybill.
- Confirm waybill numbering, driver/customer lookups, and audit entries.
- Verify HTTPS and confirm saved records are not accessible without login.
- Keep the original database unchanged until validation is complete and approved.

## Permissions
Authenticated users can edit saved waybills; changing the waybill number remains admin-only. The original creator/issuer identity is retained for audit context. Do not share the admin credentials with all users.
