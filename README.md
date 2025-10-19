# CS2 Containers Price Web Scraper → Google Sheets

Keep your CS2 container holdings and market prices synced into a Google Sheet.  
Supports single or multiple accounts via per-account CSVs and protects existing sheets with a strict layout guard.

## ✨ Features

**Single-account mode**  
Columns: `Case Name | Quantity | Unit Price | Total Value`

**Multi-account mode** (auto-expands columns)  
Columns: `Case Name | <ACC> Quantity … | Total Quantity | Unit Price | <ACC> Value … | Total Value`

**Live formulas per row**
- Total Value per row multiplies Quantity × Unit Price (or per-account quantities in multi mode)
- Bottom TOTAL row (bold) sums every numeric column, including per-account totals

**Layout guard**  
Prevents overwriting a sheet with a different set of accounts. Exact names & order must match.

**Self-initializing**  
Creates the tab (worksheet) and bold headers if needed; seeds rows from your CSVs.

---

## 📦 Project Structure

```
.
├─ main.py
├─ .env.example             # copy to .env and fill
├─ requirements.txt
├─ .gitignore
├─ secrets/                 # your Google service account JSON lives here (ignored)
│   └─ service_account.json (you add this)
└─ watchlists/              # one CSV per account
    ├─ MAIN.csv.example     # example format
    └─ <YOUR_ACCOUNT>.csv   # e.g., PRM.csv, WYH.csv (you add these)
```

## 🔧 Prerequisites

- **Python 3.10+**
- **Google Cloud service account JSON** with Sheets access
- A **Google Sheet** you own/edit and its **Sheet ID** (from the sheet URL)
- **Microsoft Edge** installed (Selenium uses Edge; Selenium ≥ 4.18 usually manages the driver automatically)

---

## 🚀 Setup

### 1) Install dependencies

```bash
pip install -r requirements.txt
```

### 2) Add `.env`

Copy and edit:

```bash
cp .env.example .env
```

`.env` should look like:

```env
GOOGLE_CREDENTIALS_PATH=secrets/service_account.json
SHEET_ID=your_google_sheet_id_here
```

### 3) Put your service account JSON

- Save the key as `secrets/service_account.json` (or match the path in `.env`)
- Share your Google Sheet with the service account email (**Editor** permission)

### 4) Create watchlists

- Place CSVs in `watchlists/`
- File name (without extension) becomes the account name (uppercased).
- CSV headers (required):

```csv
Case Name,Quantity
```

**Examples**

Single account:
- `watchlists/MAIN.csv`

Multiple accounts:
- `watchlists/PRM.csv`
- `watchlists/WYH.csv`

Use `watchlists/MAIN.csv.example` as a template.

### 5) Run it

```bash
python main.py
```

---

## 🧠 How it behaves

### First run on an empty tab

- **1 CSV** → creates generic 4-column layout (`Case Name | Quantity | Unit Price | Total Value`)
- **2+ CSVs** → creates multi-account layout using exact account names from filenames

### Future runs

- **Generic sheet (single account):** only allowed if you still provide exactly one CSV
- **Multi-account sheet:** only allowed if names & order of accounts exactly match the existing header
- If the guard fails, the script exits with a clear error (use another tab or fix your CSV set)

### Data updates

- Adds any new cases from CSVs
- **Does not overwrite quantities** you manually edit in the sheet  
  (It only fills blanks; you own the numbers after the first write.)
- Always refreshes **Unit Price** from scraping
- **Per-row formulas:**
  - **Single:** `D = IF(OR(B="",C=""),"",B*C)`
  - **Multi:**
    - Total Quantity = `SUM(<per-account qty cells in row>)`
    - Each `<ACC> Value` = `IF(OR(<ACC> Qty="", Unit Price=""), "", <ACC> Qty * Unit Price)`
    - Total Value = `SUM(<per-account value cells in row>)`
- **Bottom TOTAL row (bold):**  
  Sums every quantity/value column: per-account, overall totals

---

## 🧭 Choosing the target tab

Edit `TARGET_TAB_TITLE` at the top of `main.py` (default: `"Sheet3"`)

- The script will create the tab if it doesn't exist
- **Tip:** Use a fresh tab if you're changing account sets (e.g., adding `WYH.csv` later).

---

## 🕒 Scheduling (optional)

**Linux/macOS (cron):**

```bash
0 * * * * /usr/bin/python3 /path/to/main.py >> /path/to/log.txt 2>&1
```

**Windows Task Scheduler:**

- Action: `python` with `main.py` as argument (or call a `.bat` that activates venv and runs `main.py`)

---

## 🧰 Troubleshooting

**"No watchlists found in ./watchlists/"**  
Add at least one CSV (`Case Name,Quantity`) to `watchlists/`.

**Layout mismatch error**  
Your sheet's header doesn't match the accounts you supplied.
- **Option A:** Use another tab (rename `TARGET_TAB_TITLE`) and re-run
- **Option B:** Make your watchlist CSVs match the tab's account names & order

**Formulas show as plain text**  
The script uses `USER_ENTERED`—if you pasted something manually, re-run once.

**Selenium/driver issues**  
Update Selenium to ≥ 4.18. Ensure Edge is installed.  
Uncomment `--headless=new` in the Selenium options if you need headless runs.

**403/permissions**  
Make sure your Google Sheet is shared with the service account email (**Editor**).

---

## 🔐 Security

- **Don't commit** your real `.env` or service account JSON
- `.gitignore` should include:

```gitignore
.env
secrets/*.json
watchlists/*.csv
__pycache__/
*.pyc
.DS_Store
```


## 🧱 Extending

- **More sources:** Add more URLs & parsers in `main.py`
- **Different browser:** Switch Selenium options to Chrome/Firefox if you prefer
- **Custom columns:** You can add additional computed columns; just keep the header guard logic in sync

---

## 📄 License

This project is licensed under the MIT License.

You’re free to use, modify, and distribute this code, provided that the original copyright notice and permission notice remain included. The software is provided “as is”, without warranty of any kind.
