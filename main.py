import os
import time
import gspread
import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from google.oauth2.service_account import Credentials

load_dotenv()

credentials_path = os.getenv('GOOGLE_CREDENTIALS_PATH')

scope = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_file(credentials_path, scopes=scope)
client = gspread.authorize(credentials)

sheetID = "1HccDbcHhS0KjcIZaHxq2YD4fvyFAdJa4yEsifwZXGtM"
sheet = client.open_by_key(sheetID).sheet1

urls = [
    "https://stash.clash.gg/containers/skin-cases",
    "https://stash.clash.gg/stickers/capsule/294/CS20-Sticker-Capsule"
]

edge_options = Options()
edge_options.add_argument("--disable-logging")
edge_options.add_argument("--log-level=3") 

driver = webdriver.ChromiumEdge(options= edge_options)

html_contents = {}

start_time = time.time()

for i, url in enumerate(urls):
    driver.get(url)
    html_content = driver.page_source
    html_contents[i] = html_content

driver.quit()

scrape_duration = time.time() - start_time
print("Scaping Time: ",scrape_duration)

cases = [
    "Kilowatt Case",
    "Snakebite Case",
    "Revolution Case",
    "Dreams & Nightmares Case",
    "Clutch Case",
    "Danger Zone Case",
    "Fracture Case",
    "Prisma 2 Case",
    "Prisma Case",
    "CS20 Case",
    "Spectrum 2 Case",
    "Gamma 2 Case",
    "Glove Case",
    "Horizon Case",
    "CS20 Sticker Capsule",
    "Recoil Case",
    "CS:GO Weapon Case 2",
    "Operation Phoenix Weapon Case",
    "Revolver Case",
    "Shadow Case",
    "Chroma 3 Case"
]

obtained_cases = {}

# Process the first URL (Skin Cases)
soup = BeautifulSoup(html_contents[0], 'html.parser')
containers = soup.find_all('div', class_='well result-box nomargin')

for container in containers:
    title = container.find('h4').text.strip()
    price_container = container.find('div', 'price margin-top-sm')
    price_text = price_container.find('p').text.strip()
    price = price_text.replace('$', '')

    if title in cases and title not in obtained_cases:
        obtained_cases[title] = price

# Process the second URL (Sticker Capsule)
soup = BeautifulSoup(html_contents[1], 'html.parser')
capsules = soup.find_all('div', class_='col-lg-12 text-center col-widen content-header')

for capsule in capsules:
    title_container = capsule.find('div', class_='inline-middle collapsed-top-margin')
    title = title_container.find('h1').text.strip()
    price_container = capsule.find('a', class_='btn btn-default market-button-item')
    price_text = price_container.text.strip()
    price = price_text.split()[0].replace('$', '')

    if title in cases and title not in obtained_cases:
        obtained_cases[title] = price

# Update the Google Sheet with the latest prices
case_names = sheet.col_values(1)[1:22]

# Prepare data for batch update
updates = []  # List to hold all cell updates

# Iterate through each case name and prepare batch update if it matches
for i, case_name in enumerate(case_names):
    if case_name in obtained_cases:
        new_price = float(obtained_cases[case_name])
        # Prepare the range and value to update
        updates.append({'range': f'F{i + 2}', 'values': [[new_price]]})  # Column F starts at F2

# Batch update the prices in the spreadsheet
if updates:
    sheet.batch_update(updates)

print("Updated Excel file with latest prices")

df = pd.DataFrame(obtained_cases.items(), columns=['Case Name', 'Price'])
print(df)
