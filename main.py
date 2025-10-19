#!/usr/bin/env python3
"""
CS2 containers price scraper → Google Sheet (multi-account + layout guard, no CLI)

.env (required)
  GOOGLE_CREDENTIALS_PATH=/abs/path/to/service_account.json
  SHEET_ID=your_google_sheet_id

Watchlists (REQUIRED)
- Place per-account CSVs in: ./watchlists/
  Each CSV must have headers: Case Name, Quantity
  Account name = filename stem (uppercased). Example:
      watchlists/PRM.csv, watchlists/WYH.csv

Behavior & Guards
- If target sheet tab is EMPTY:
    - 1 CSV → initialize GENERIC headers: Case Name | Quantity | Unit Price | Total Value
    - 2+ CSVs → initialize MULTI headers:
        Case Name | <ACC> Quantity ... | Total Quantity | Unit Price | <ACC> Value ... | Total Value
- If sheet already has GENERIC headers → only 1 CSV allowed; else error & exit.
- If sheet already has MULTI headers → account names & order must match exactly; else error & exit.
- Per-row formulas:
    Single-account: D = IF(OR(B="",C=""),"",B*C)
    Multi-account:
        Total Quantity (row) = SUM(per-account quantities on that row)
        Each <ACC> Value (row) = IF(OR(<ACC>Qty="",UnitPrice=""),"",<ACC>Qty*UnitPrice)
        Total Value (row) = SUM(per-account values on that row)
- Bottom TOTAL row (bold):
    Single-account: sums for Quantity and Total Value
    Multi-account: sums for each account's Quantity, Total Quantity, each account's Value, Total Value
"""

import os
import glob
import sys
import time
from typing import Dict, List, Tuple, Optional

import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# Selenium (Edge)
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound


# ---------- Hardcoded settings ---------- #

TARGET_TAB_TITLE = "Sheet1"  # change if you want another tab name

# URLs to scrape
URL_LISTING = "https://stash.clash.gg/containers/skin-cases"
URL_ITEM = "https://stash.clash.gg/stickers/capsule/294/CS20-Sticker-Capsule"

# Watchlists directory
WATCHLISTS_DIR = "watchlists"


# ---------- Small utilities ---------- #

def col_idx_to_letter(n: int) -> str:
    """1 -> A, 2 -> B, ..."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def fetch_html(driver: webdriver.Edge, url: str, wait_css: str, timeout: int = 15) -> str:
    driver.get(url)
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_css))
        )
    except Exception:
        pass
    return driver.page_source


def parse_listing_page(html: str) -> Dict[str, float]:
    soup = BeautifulSoup(html, "html.parser")
    results: Dict[str, float] = {}
    for card in soup.select("div.well.result-box.nomargin"):
        title_el = card.find("h4")
        price_wrap = card.find("div", class_="price margin-top-sm")
        price_p = price_wrap.find("p") if price_wrap else None
        if not title_el or not price_p:
            continue
        title = title_el.get_text(strip=True)
        price_txt = price_p.get_text(strip=True).replace("$", "").replace(",", "")
        try:
            results[title] = float(price_txt)
        except ValueError:
            continue
    return results


def parse_item_page(html: str) -> Dict[str, float]:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one("div.col-lg-12.text-center.col-widen.content-header h1")
    title = header.get_text(strip=True) if header else None
    buy_btn = soup.select_one("a.btn.btn-default.market-button-item")
    if not (title and buy_btn):
        return {}
    first_token = (buy_btn.get_text(strip=True).split()[0]).replace("$", "").replace(",", "")
    try:
        price = float(first_token)
        return {title: price}
    except ValueError:
        return {}


def read_watchlist_csv(path: str) -> Dict[str, float]:
    """
    Expects CSV headers: Case Name, Quantity
    Returns: {name: qty}
    """
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}
    name_col = cols.get("case name")
    qty_col = cols.get("quantity")
    if not name_col or not qty_col:
        raise RuntimeError(f"{path} must have headers 'Case Name' and 'Quantity'. Got: {list(df.columns)}")
    out: Dict[str, float] = {}
    for _, row in df[[name_col, qty_col]].dropna(subset=[name_col]).iterrows():
        name = str(row[name_col]).strip()
        try:
            qty = float(row[qty_col])
        except (ValueError, TypeError):
            qty = 0
        if name:
            out[name] = qty
    return out


def discover_accounts() -> List[Tuple[str, str]]:
    """
    Returns list of (account_name, csv_path) from ./watchlists/*.csv only.
    Account name derived from filename stem (uppercased).
    """
    pairs: List[Tuple[str, str]] = []
    if os.path.isdir(WATCHLISTS_DIR):
        for path in sorted(glob.glob(os.path.join(WATCHLISTS_DIR, "*.csv"))):
            stem = os.path.splitext(os.path.basename(path))[0].strip()
            account = stem.upper()
            pairs.append((account, path))
    return pairs


# ---------- Header building & detection ---------- #

GENERIC_HEADERS = ["Case Name", "Quantity", "Unit Price", "Total Value"]


def build_multi_headers(accounts: List[str]) -> List[str]:
    headers = ["Case Name"]
    headers += [f"{acc} Quantity" for acc in accounts]
    headers += ["Total Quantity", "Unit Price"]
    headers += [f"{acc} Value" for acc in accounts]
    headers += ["Total Value"]
    return headers


def detect_sheet_layout(existing_headers: List[str]) -> Tuple[str, List[str]]:
    """
    Detect current sheet layout.
    Returns:
      ("empty", []) if no header,
      ("generic", []) if generic single-account header,
      ("multi", accounts) if multi layout and parsed accounts,
      ("unknown", []) otherwise.
    """
    hdr = [h.strip() for h in existing_headers if str(h).strip() != ""]
    if not hdr:
        return "empty", []
    if hdr == GENERIC_HEADERS:
        return "generic", []

    # Try to parse multi layout:
    # Case Name | [<ACC> Quantity x N] | Total Quantity | Unit Price | [<ACC> Value x N] | Total Value
    if len(hdr) >= 6 and hdr[0] == "Case Name" and hdr[-1] == "Total Value":
        try:
            tq_idx = hdr.index("Total Quantity")
        except ValueError:
            return "unknown", []
        qty_headers = hdr[1:tq_idx]
        if not qty_headers or any(not h.endswith(" Quantity") for h in qty_headers):
            return "unknown", []
        accounts_q = [h[:-len(" Quantity")] for h in qty_headers]

        if tq_idx + 1 >= len(hdr) or hdr[tq_idx + 1] != "Unit Price":
            return "unknown", []
        val_headers = hdr[tq_idx + 2:-1]
        if len(val_headers) != len(accounts_q) or any(not h.endswith(" Value") for h in val_headers):
            return "unknown", []
        accounts_v = [h[:-len(" Value")] for h in val_headers]
        if accounts_q != accounts_v:
            return "unknown", []

        return "multi", accounts_q

    return "unknown", []


# ---------- Google Sheets helpers ---------- #

def ensure_worksheet(sh, title: str):
    try:
        ws = sh.worksheet(title)
        created = False
    except WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=2000, cols=40)
        created = True
    return ws, created


def format_bold(ws, a1_range: str):
    try:
        ws.format(a1_range, {"textFormat": {"bold": True}})
    except Exception:
        pass


# ---------- GENERIC (single-account) path ---------- #

def generic_get_map(ws) -> Tuple[Dict[str, int], Dict[str, Optional[float]]]:
    values = ws.get_all_values()
    name_to_row: Dict[str, int] = {}
    sheet_qty: Dict[str, Optional[float]] = {}
    for idx, row in enumerate(values[1:], start=2):
        if not row:
            continue
        name = (row[0].strip() if len(row) >= 1 else "")
        if not name or name.lower() == "total":
            continue
        name_to_row[name] = idx
        qv = None
        if len(row) >= 2 and row[1].strip() != "":
            try:
                qv = float(row[1].strip())
            except ValueError:
                qv = None
        sheet_qty[name] = qv
    return name_to_row, sheet_qty


def generic_append_rows(ws, names: List[str], qty_map: Dict[str, float], prices: Dict[str, float]) -> int:
    rows = []
    for n in names:
        q = qty_map.get(n, 0)
        p = prices.get(n, "")
        rows.append([n, q, p, ""])  # D = formula later
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


def generic_write_totals(ws, last_data_row: int):
    desired_row = max(1, last_data_row) + 1
    # Clear any existing TOTAL row elsewhere
    col_a = ws.col_values(1)
    for i, v in enumerate(col_a, start=1):
        if i == 1:
            continue
        if str(v).strip().lower() == "total" and i != desired_row:
            ws.update(f"A{i}:D{i}", [["", "", "", ""]], value_input_option="RAW")

    qty_sum = f"=SUM(B2:B{last_data_row})"
    val_sum = f"=SUM(D2:D{last_data_row})"
    ws.update(
        f"A{desired_row}:D{desired_row}",
        [["TOTAL", qty_sum, "", val_sum]],
        value_input_option="USER_ENTERED",
    )
    format_bold(ws, f"A{desired_row}:D{desired_row}")


def run_generic(ws, prices: Dict[str, float], single_qty: Dict[str, float]):
    # Ensure headers
    if ws.row_values(1) != GENERIC_HEADERS:
        ws.update("A1:D1", [GENERIC_HEADERS])
    format_bold(ws, "A1:D1")

    name_to_row, sheet_qty = generic_get_map(ws)
    appended = 0

    # Seed empty sheet
    union_names = sorted(single_qty.keys())
    if not name_to_row and union_names:
        appended = generic_append_rows(ws, union_names, single_qty, prices)
        name_to_row, sheet_qty = generic_get_map(ws)

    # Append missing names
    missing = [n for n in single_qty.keys() if n not in name_to_row]
    if missing:
        appended += generic_append_rows(ws, sorted(missing), single_qty, prices)
        name_to_row, sheet_qty = generic_get_map(ws)

    # Updates: fill blank qty, set unit price, per-row total formula
    updates = []
    for name, row in name_to_row.items():
        qty = sheet_qty.get(name)
        if qty is None and name in single_qty:
            updates.append({"range": f"B{row}", "values": [[single_qty[name]]]})
        price = prices.get(name)
        if price is not None:
            updates.append({"range": f"C{row}", "values": [[price]]})
        formula = f'=IF(OR(B{row}="",C{row}=""),"",B{row}*C{row})'
        updates.append({"range": f"D{row}", "values": [[formula]]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    # Totals row
    last_row = max(name_to_row.values(), default=1)
    generic_write_totals(ws, last_row)

    return appended


# ---------- MULTI (multi-account) path ---------- #

def build_layout(accounts: List[str]) -> Dict[str, int]:
    # Returns 1-based column indices for blocks
    case_col = 1
    qty_cols = {acc: 2 + i for i, acc in enumerate(accounts)}
    total_qty_col = 2 + len(accounts)
    unit_price_col = total_qty_col + 1
    val_cols = {acc: unit_price_col + 1 + i for i, acc in enumerate(accounts)}
    total_val_col = unit_price_col + 1 + len(accounts)
    return {
        "case_col": case_col,
        "qty_cols": qty_cols,
        "total_qty_col": total_qty_col,
        "unit_price_col": unit_price_col,
        "val_cols": val_cols,
        "total_val_col": total_val_col,
    }


def multi_get_map(ws, accounts: List[str], layout: Dict[str, int]) -> Tuple[Dict[str, int], Dict[str, Dict[str, Optional[float]]]]:
    values = ws.get_all_values()
    name_to_row: Dict[str, int] = {}
    sheet_qty: Dict[str, Dict[str, Optional[float]]] = {}
    for idx, row in enumerate(values[1:], start=2):
        name = (row[layout["case_col"] - 1].strip() if len(row) >= layout["case_col"] else "")
        if not name or name.lower() == "total":
            continue
        name_to_row[name] = idx
        sheet_qty[name] = {}
        for acc, col in layout["qty_cols"].items():
            val = None
            if len(row) >= col and row[col - 1].strip() != "":
                try:
                    val = float(row[col - 1].strip())
                except ValueError:
                    val = None
            sheet_qty[name][acc] = val
    return name_to_row, sheet_qty


def multi_append_rows(ws, names: List[str], accounts: List[str], account_qty_maps: Dict[str, Dict[str, float]], prices: Dict[str, float], layout: Dict[str, int]) -> int:
    width = layout["total_val_col"]
    rows = []
    for n in names:
        row = [""] * width
        row[layout["case_col"] - 1] = n
        for acc, col in layout["qty_cols"].items():
            row[col - 1] = account_qty_maps.get(acc, {}).get(n, 0)
        row[layout["unit_price_col"] - 1] = prices.get(n, "")
        rows.append(row)  # formulas later
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


def multi_write_totals(ws, last_data_row: int, accounts: List[str], layout: Dict[str, int]):
    """
    Writes bold "TOTAL" row under the last data row with sums for:
      - each <ACC> Quantity
      - Total Quantity
      - each <ACC> Value
      - Total Value
    Clears any previous totals row found elsewhere.
    """
    desired_row = max(1, last_data_row) + 1
    width = layout["total_val_col"]
    last_col_letter = col_idx_to_letter(width)

    # Clear any old TOTAL row not at the desired location
    col_a = ws.col_values(1)
    for i, v in enumerate(col_a, start=1):
        if i == 1:
            continue
        if str(v).strip().lower() == "total" and i != desired_row:
            ws.update(f"A{i}:{last_col_letter}{i}", [[""] * width], value_input_option="RAW")

    row_vals = [""] * width
    row_vals[0] = "TOTAL"

    # Per-account Quantity totals
    for acc in accounts:
        q_col = layout["qty_cols"][acc]
        q_letter = col_idx_to_letter(q_col)
        row_vals[q_col - 1] = f"=SUM({q_letter}2:{q_letter}{last_data_row})"

    # Total Quantity column
    tq_col = layout["total_qty_col"]
    tq_letter = col_idx_to_letter(tq_col)
    row_vals[tq_col - 1] = f"=SUM({tq_letter}2:{tq_letter}{last_data_row})"

    # Per-account Value totals
    for acc in accounts:
        v_col = layout["val_cols"][acc]
        v_letter = col_idx_to_letter(v_col)
        row_vals[v_col - 1] = f"=SUM({v_letter}2:{v_letter}{last_data_row})"

    # Total Value column
    tv_col = layout["total_val_col"]
    tv_letter = col_idx_to_letter(tv_col)
    row_vals[tv_col - 1] = f"=SUM({tv_letter}2:{tv_letter}{last_data_row})"

    # Write totals (as formulas) and bold the row
    ws.update(
        f"A{desired_row}:{last_col_letter}{desired_row}",
        [row_vals],
        value_input_option="USER_ENTERED",
    )
    try:
        ws.format(f"A{desired_row}:{last_col_letter}{desired_row}", {"textFormat": {"bold": True}})
    except Exception:
        pass


# ---------- Main ---------- #

def main():
    load_dotenv()
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
    sheet_id = os.getenv("SHEET_ID")
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError("Set GOOGLE_CREDENTIALS_PATH in .env to a valid service account JSON file.")
    if not sheet_id:
        raise RuntimeError("Set SHEET_ID in .env to your Google Sheet ID.")

    # Discover accounts strictly from ./watchlists/
    accounts_and_paths = discover_accounts()
    if not accounts_and_paths:
        print("[ERROR] No watchlists found in ./watchlists/. "
              "Create at least one CSV (e.g., watchlists/MAIN.csv) with headers: Case Name, Quantity.")
        sys.exit(1)

    accounts = [acc for acc, _ in accounts_and_paths]
    qty_maps = {acc: read_watchlist_csv(path) for acc, path in accounts_and_paths}

    # Scrape prices
    edge_opts = EdgeOptions()
    edge_opts.add_argument("--disable-logging")
    edge_opts.add_argument("--log-level=3")
    # edge_opts.add_argument("--headless=new")
    driver = webdriver.Edge(options=edge_opts)

    prices: Dict[str, float] = {}
    t0 = time.time()
    try:
        html0 = fetch_html(driver, URL_LISTING, "div.well.result-box.nomargin")
        prices.update(parse_listing_page(html0))
        html1 = fetch_html(driver, URL_ITEM, "div.col-lg-12.text-center.col-widen.content-header")
        prices.update(parse_item_page(html1))
    finally:
        driver.quit()
    print(f"[INFO] Scraped {len(prices)} items in {time.time() - t0:.2f}s")

    # Google Sheets client
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_file(creds_path, scopes=scope)
    client = gspread.authorize(credentials)
    sh = client.open_by_key(sheet_id)
    ws, _ = ensure_worksheet(sh, TARGET_TAB_TITLE)

    # ---- LAYOUT GUARD ----
    existing_headers = ws.row_values(1)
    layout_type, existing_accounts = detect_sheet_layout(existing_headers)

    if layout_type == "empty":
        if len(accounts) == 1:
            # Initialize GENERIC headers
            ws.update("A1:D1", [GENERIC_HEADERS])
            format_bold(ws, "A1:D1")
            appended = run_generic(ws, prices, next(iter(qty_maps.values())))
            print(f"[INFO] Done (initialized generic). Rows appended: {appended}")
            return
        else:
            # Initialize MULTI headers (exact account names)
            hdrs = build_multi_headers(accounts)
            last = col_idx_to_letter(len(hdrs))
            ws.update(f"A1:{last}1", [hdrs])
            format_bold(ws, f"A1:{last}1")
            layout = build_layout(accounts)
            # continue into multi path below

    elif layout_type == "generic":
        if len(accounts) != 1:
            print("[ERROR] This sheet has a single-account (generic) layout, "
                  f"but you provided multiple accounts: {', '.join(accounts)}.\n"
                  "Please select a different tab or reduce watchlists to one CSV.")
            sys.exit(1)
        appended = run_generic(ws, prices, next(iter(qty_maps.values())))
        print(f"[INFO] Done (generic). Rows appended: {appended}")
        return

    elif layout_type == "multi":
        # Must match names AND order exactly
        if existing_accounts != accounts:
            print("[ERROR] This sheet is configured for account columns: "
                  f"{existing_accounts} but you provided: {accounts}.\n"
                  "Please use a different tab that matches those accounts (names and order).")
            sys.exit(1)
        layout = build_layout(accounts)
        # proceed multi

    else:
        print(f"[ERROR] Unrecognized sheet header format: {existing_headers}\n"
              "Please point to an empty tab or a tab created by this script.")
        sys.exit(1)

    # ----- MULTI path (either initialized above or validated) -----
    union_names = set()
    for m in qty_maps.values():
        union_names.update(m.keys())

    name_to_row, sheet_qty = multi_get_map(ws, accounts, layout)

    appended = 0
    if not name_to_row and union_names:
        appended = multi_append_rows(ws, sorted(union_names), accounts, qty_maps, prices, layout)
        name_to_row, sheet_qty = multi_get_map(ws, accounts, layout)
    elif not name_to_row and not union_names:
        print("[WARN] Sheet is empty and no watchlist names found. Only headers ensured.")

    missing = sorted([n for n in union_names if n not in name_to_row])
    if missing:
        appended += multi_append_rows(ws, missing, accounts, qty_maps, prices, layout)
        name_to_row, sheet_qty = multi_get_map(ws, accounts, layout)

    updates = []
    # Fill blank per-account qty from CSVs; set Unit Price; set per-row formulas
    for name, row in name_to_row.items():
        # per-account quantities
        for acc in accounts:
            col = layout["qty_cols"][acc]
            has_qty = sheet_qty.get(name, {}).get(acc)
            if (has_qty is None) and (name in qty_maps.get(acc, {})):
                updates.append({"range": f"{col_idx_to_letter(col)}{row}",
                                "values": [[qty_maps[acc][name]]]})

        # unit price
        price = prices.get(name)
        if price is not None:
            pcol = layout["unit_price_col"]
            updates.append({"range": f"{col_idx_to_letter(pcol)}{row}",
                            "values": [[price]]})

        # Total Quantity (row)
        first_q = min(layout["qty_cols"].values())
        last_q = max(layout["qty_cols"].values())
        tq_col = layout["total_qty_col"]
        tq_formula = f"=SUM({col_idx_to_letter(first_q)}{row}:{col_idx_to_letter(last_q)}{row})"
        updates.append({"range": f"{col_idx_to_letter(tq_col)}{row}", "values": [[tq_formula]]})

        # Per-account Values (row)
        up_col = layout["unit_price_col"]
        for acc in accounts:
            q_col = layout["qty_cols"][acc]
            v_col = layout["val_cols"][acc]
            f = f'=IF(OR({col_idx_to_letter(q_col)}{row}="",{col_idx_to_letter(up_col)}{row}=""),"",{col_idx_to_letter(q_col)}{row}*{col_idx_to_letter(up_col)}{row})'
            updates.append({"range": f"{col_idx_to_letter(v_col)}{row}", "values": [[f]]})

        # Total Value (row)
        first_v = min(layout["val_cols"].values())
        last_v = max(layout["val_cols"].values())
        tv_col = layout["total_val_col"]
        tv_formula = f"=SUM({col_idx_to_letter(first_v)}{row}:{col_idx_to_letter(last_v)}{row})"
        updates.append({"range": f"{col_idx_to_letter(tv_col)}{row}", "values": [[tv_formula]]})

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    # Totals row (with per-account totals too)
    name_to_row, _ = multi_get_map(ws, accounts, layout)
    last_data_row = max(name_to_row.values(), default=1)
    multi_write_totals(ws, last_data_row, accounts, layout)

    print(f"[INFO] Done (multi). Rows appended: {appended}. Accounts: {', '.join(accounts)}")


if __name__ == "__main__":
    main()