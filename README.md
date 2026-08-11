# SMB Account Software

Accounting software for a small Indian kirana (grocery) shop — sales entry,
inventory, product pricing, discounts, GST-inclusive billing, customer
credit (khata), and P&L reporting. It runs on one computer, stores all data
in a single local file, and does not need the internet.

This guide is written for whoever runs the shop's computer day to day —
you do not need to know how to write code to use it.

---

## 1. Installing and running the app

You need Python and [Poetry](https://python-poetry.org/) installed once.
After that, from inside this project folder:

```bash
# One-time setup — installs everything the app needs
poetry install

# Start the app
poetry run python -m src.main
```

The app opens a full-screen text menu. Use the arrow keys or the letter
shown next to each option (for example `s` for "Record a sale") to move
around, and `q` to quit.

---

## 2. Where your data is kept

Everything you enter — products, prices, stock levels, discounts — is
stored in one file:

```
~/.smb-accounting/data.db
```

(`~` means your home folder.) You do not need to open or edit this file
directly — the app does that for you.

If you ever need to point the app at a different data file (for example,
to test something without touching your real shop data), set the
`SMB_DB_PATH` environment variable before starting the app:

```bash
SMB_DB_PATH=/path/to/other-data.db poetry run python -m src.main
```

---

## 3. Automatic backups

Every time you start the app, it checks how old the most recent backup is.
If it has been more than a day since the last backup (or there isn't one
yet), it automatically saves a fresh copy of your data before the menu
opens. You don't have to do anything for this to happen.

Backup files are saved next to your data, in:

```
~/.smb-accounting/backups/
```

The app keeps the 7 most recent backups and automatically deletes older
ones, so this folder does not grow forever.

If a backup fails for any reason (for example, the disk is full), the app
will still start normally — a failed backup never stops you from using the
app.

---

## 4. Manual backup and restore

You can also take a backup, list your backups, or restore your data from a
backup at any time, using the command line:

```bash
# Take a backup right now
python -m src.db.backup backup

# See what backups you have (oldest first)
python -m src.db.backup list

# Restore your data from a specific backup file
python -m src.db.backup restore /path/to/backups/data-20260802T065112542397Z.db
```

**Before you restore:** make sure the app is completely closed. Restoring
while the app is still running can leave your data in a mixed, inconsistent
state. Close the app first, run the `restore` command, then start the app
again.

Restoring replaces your current data with the contents of the backup file
you choose — anything entered after that backup was taken will be lost.
If you're not sure which backup to restore, run `python -m src.db.backup
list` first and pick the most recent one that you know was still good.

### When to use restore

- You entered something wrong and it can't be fixed through the app itself.
- The data file was lost, damaged, or accidentally deleted.
- You want to move your shop's data to a different computer (copy a backup
  file over, then restore it there).

---

## 5. Getting help

If something goes wrong, note down exactly what you were doing and any
message shown on screen, and pass that along with your most recent backup
file to your support technician.
