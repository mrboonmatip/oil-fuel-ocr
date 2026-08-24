import os
import json
import csv
from datetime import datetime
from typing import Tuple, Optional

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_JSON_FILE = os.path.join(BASE_DIR, "fuel_records.json")
LOCAL_CSV_FILE = os.path.join(BASE_DIR, "fuel_records.csv")
CONFIG_FILE = os.path.join(BASE_DIR, "sheet_config.json")

HEADERS = [
    "วันที่เติมน้ำมัน",
    "ชื่อปั๊มน้ำมันที่เติม",
    "จังหวัดที่เติม",
    "ชนิดน้ำมันที่เติม",
    "ราคาต่อหน่วย (บาท/ลิตร)",
    "ปริมาณลิตรที่เติม (ลิตร)",
    "จำนวนเงินทั้งสิ้น (บาท)",
    "เลขที่ใบเสร็จ / Composite Key",
    "เวลาบันทึก"
]

COMPANY_QUOTA_LITERS = 400.0

class SheetsService:
    def __init__(self, credentials_path: str = None, sheet_name: str = "บันทึกการเติมน้ำมัน"):
        if credentials_path is None:
            credentials_path = os.path.join(BASE_DIR, "credentials.json")
        self.credentials_path = credentials_path
        self.sheet_name = sheet_name
        self.gc = None
        self.sheet = None
        self.spreadsheet = None
        self.is_google_sheets_active = False
        self.spreadsheet_url = ""
        
        self._init_local_storage()
        self._try_connect_google_sheets()

    def _init_local_storage(self):
        if not os.path.exists(LOCAL_JSON_FILE):
            with open(LOCAL_JSON_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

        if not os.path.exists(LOCAL_CSV_FILE):
            with open(LOCAL_CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)

    def _try_connect_google_sheets(self):
        if not HAS_GSPREAD or not os.path.exists(self.credentials_path):
            self.is_google_sheets_active = False
            return

        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
            self.gc = gspread.authorize(creds)
            
            # Check if custom Sheet URL or ID is set in sheet_config.json
            custom_url = ""
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as cfg:
                        custom_url = json.load(cfg).get("sheet_url", "").strip()
                except Exception:
                    pass

            if custom_url:
                try:
                    if "docs.google.com" in custom_url:
                        self.spreadsheet = self.gc.open_by_url(custom_url)
                    else:
                        self.spreadsheet = self.gc.open_by_key(custom_url)
                except Exception as ex:
                    print(f"Failed to open custom Sheet URL: {ex}. Falling back to name search.")

            if not self.spreadsheet:
                try:
                    self.spreadsheet = self.gc.open(self.sheet_name)
                except gspread.SpreadsheetNotFound:
                    self.spreadsheet = self.gc.create(self.sheet_name)

            self.sheet = self.spreadsheet.sheet1
            self.spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{self.spreadsheet.id}"
            
            existing_headers = self.sheet.row_values(1)
            if not existing_headers:
                self.sheet.append_row(HEADERS)
                
            self.is_google_sheets_active = True
            print(f"Connected to Google Sheets successfully: '{self.spreadsheet.title}' ({self.spreadsheet_url})")
        except Exception as e:
            print(f"Google Sheets Connection Failed: {e}. Using local storage fallback.")
            self.is_google_sheets_active = False

    def get_all_records(self) -> list:
        if self.is_google_sheets_active and self.sheet:
            try:
                records = self.sheet.get_all_records()
                result = []
                for r in records:
                    result.append({
                        "refuel_date": str(r.get("วันที่เติมน้ำมัน", "")),
                        "station_name": str(r.get("ชื่อปั๊มน้ำมันที่เติม", "")),
                        "province": str(r.get("จังหวัดที่เติม", "")),
                        "fuel_type": str(r.get("ชนิดน้ำมันที่เติม", "")),
                        "price_per_unit": float(r.get("ราคาต่อหน่วย (บาท/ลิตร)", 0) or 0),
                        "liters": float(r.get("ปริมาณลิตรที่เติม (ลิตร)", 0) or 0),
                        "total_amount": float(r.get("จำนวนเงินทั้งสิ้น (บาท)", 0) or 0),
                        "receipt_no": str(r.get("เลขที่ใบเสร็จ / Composite Key", "")),
                        "created_at": str(r.get("เวลาบันทึก", ""))
                    })
                return result
            except Exception as e:
                print(f"Error fetching Google Sheets records: {e}")

        try:
            with open(LOCAL_JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def check_duplicate(self, record: dict) -> Tuple[bool, Optional[dict]]:
        all_records = self.get_all_records()
        
        target_date = str(record.get("refuel_date", "")).strip()
        target_station = str(record.get("station_name", "")).strip()
        target_amount = float(record.get("total_amount", 0) or 0)
        target_liters = float(record.get("liters", 0) or 0)
        target_receipt = str(record.get("receipt_no", "")).strip()

        for item in all_records:
            item_date = str(item.get("refuel_date", "")).strip()
            item_station = str(item.get("station_name", "")).strip()
            item_amount = float(item.get("total_amount", 0) or 0)
            item_liters = float(item.get("liters", 0) or 0)
            item_receipt = str(item.get("receipt_no", "")).strip()

            # 1. Exact Match by Unique Tax Invoice Receipt No (Exclude POS# machine ID, RD# tax machine ID, and RCP- fallback)
            if target_receipt and item_receipt and len(target_receipt) >= 10:
                if not target_receipt.startswith("POS#") and not target_receipt.startswith("RCP-") and not target_receipt.startswith("RD#"):
                    if target_receipt.lower() == item_receipt.lower():
                        return True, item

            # 2. Match Composite Key (Same refuel date + same total amount)
            if target_date == item_date and target_amount > 0 and abs(target_amount - item_amount) < 0.1:
                if target_station == item_station or abs(target_liters - item_liters) < 0.1:
                    return True, item

        return False, None

    def append_record(self, record: dict) -> bool:
        is_dup, _ = self.check_duplicate(record)
        if is_dup:
            return False

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row_values = [
            record.get("refuel_date", ""),
            record.get("station_name", ""),
            record.get("province", ""),
            record.get("fuel_type", ""),
            record.get("price_per_unit", 0.0),
            record.get("liters", 0.0),
            record.get("total_amount", 0.0),
            record.get("receipt_no", ""),
            created_at
        ]

        if self.is_google_sheets_active and self.sheet:
            try:
                self.sheet.append_row(row_values)
            except Exception as e:
                print(f"Failed to append to Google Sheets: {e}")

        records = self.get_all_records()
        record_with_time = {**record, "created_at": created_at}
        records.append(record_with_time)
        
        with open(LOCAL_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        with open(LOCAL_CSV_FILE, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row_values)

        return True

    def get_quota_summary(self) -> dict:
        records = self.get_all_records()
        total_used_liters = sum(float(r.get("liters", 0) or 0) for r in records)
        remaining_liters = max(0.0, COMPANY_QUOTA_LITERS - total_used_liters)
        
        latest_price = 38.50
        if records:
            for r in reversed(records):
                p = float(r.get("price_per_unit", 0) or 0)
                if p > 0:
                    latest_price = p
                    break
                    
        remaining_value = remaining_liters * latest_price
        used_percentage = min(100.0, round((total_used_liters / COMPANY_QUOTA_LITERS) * 100, 1))

        return {
            "quota_liters": COMPANY_QUOTA_LITERS,
            "total_used_liters": round(total_used_liters, 3),
            "remaining_liters": round(remaining_liters, 3),
            "latest_price_per_unit": round(latest_price, 2),
            "remaining_value_thb": round(remaining_value, 2),
            "used_percentage": used_percentage,
            "total_records_count": len(records),
            "spreadsheet_url": self.spreadsheet_url
        }

    def reset_cycle(self) -> bool:
        with open(LOCAL_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
            
        with open(LOCAL_CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)

        if self.is_google_sheets_active and self.sheet:
            try:
                self.sheet.clear()
                self.sheet.append_row(HEADERS)
            except Exception as e:
                print(f"Error resetting Google Sheets cycle: {e}")

        return True
