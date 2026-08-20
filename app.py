import os
import json
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from ocr_engine import extract_fuel_data
from sheets_service import SheetsService

app = FastAPI(title="Fuel Receipt OCR & Quota System", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sheets_service = SheetsService()

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

class FuelRecordRequest(BaseModel):
    refuel_date: str
    station_name: str
    province: str
    fuel_type: str
    price_per_unit: float
    liters: float
    total_amount: float
    receipt_no: Optional[str] = ""

class SheetConfig(BaseModel):
    sheet_url: str

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/set-sheet-url")
async def set_sheet_url(config: SheetConfig):
    with open("sheet_config.json", "w", encoding="utf-8") as f:
        json.dump({"sheet_url": config.sheet_url}, f, ensure_ascii=False, indent=2)
        
    # Re-initialize Sheets Service to open custom URL
    global sheets_service
    sheets_service = SheetsService()
    
    return {
        "success": sheets_service.is_google_sheets_active,
        "message": f"เชื่อมต่อ Google Sheet เรียบร้อยแล้ว! ({sheets_service.spreadsheet_url})" if sheets_service.is_google_sheets_active else "ไม่สามารถเปิดลิงก์ Google Sheet นี้ได้ กรุณาตรวจสอบสิทธิ์การแชร์",
        "spreadsheet_url": sheets_service.spreadsheet_url
    }

@app.post("/api/ocr")
async def process_ocr(
    file: Optional[UploadFile] = File(None),
    sample_filename: Optional[str] = Form(None),
    gemini_api_key: Optional[str] = Form(None)
):
    try:
        image_bytes = None
        filename = ""
        
        if file and file.filename:
            image_bytes = await file.read()
            filename = file.filename
        elif sample_filename:
            sample_path = os.path.join(r"c:\Users\Boonma\Desktop\oil", sample_filename)
            if os.path.exists(sample_path):
                with open(sample_path, "rb") as f:
                    image_bytes = f.read()
                filename = sample_filename
            else:
                raise HTTPException(status_code=400, detail="Sample image not found")
        else:
            raise HTTPException(status_code=400, detail="No file or sample provided")

        extracted_data = extract_fuel_data(image_bytes, filename=filename, gemini_api_key=gemini_api_key)
        is_dup, matched_rec = sheets_service.check_duplicate(extracted_data)
        quota_summary = sheets_service.get_quota_summary()
        
        return {
            "success": True,
            "data": extracted_data,
            "is_duplicate": is_dup,
            "matched_record": matched_rec,
            "quota_summary": quota_summary,
            "message": "ไม่ได้บันทึกเพราะข้อมูลซ้ำในระบบ" if is_dup else "พร้อมบันทึกรายการ"
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": str(e)}
        )

@app.post("/api/check-duplicate")
async def check_dup(record: FuelRecordRequest):
    is_dup, matched_rec = sheets_service.check_duplicate(record.dict())
    return {
        "is_duplicate": is_dup,
        "matched_record": matched_rec,
        "message": "ไม่ได้บันทึกเพราะข้อมูลซ้ำในระบบ" if is_dup else "ไม่พบข้อมูลซ้ำ"
    }

@app.post("/api/save")
async def save_record(record: FuelRecordRequest):
    data_dict = record.dict()
    is_dup, matched = sheets_service.check_duplicate(data_dict)
    
    if is_dup:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "is_duplicate": True,
                "detail": f"ไม่ได้บันทึกเพราะข้อมูลซ้ำในระบบ! (รายการเติมน้ำมันวันที่ {matched.get('refuel_date')} ยอด {matched.get('total_amount')} บาท มีอยู่แล้ว)"
            }
        )
        
    saved = sheets_service.append_record(data_dict)
    if saved:
        quota = sheets_service.get_quota_summary()
        return {
            "success": True,
            "message": f"บันทึกข้อมูลเสร็จสมบูรณ์! (คงเหลือเติมได้อีก {quota['remaining_liters']} ลิตร มูลค่าประมาณ {quota['remaining_value_thb']:,.2f} บาท)",
            "google_sheets_active": sheets_service.is_google_sheets_active,
            "spreadsheet_url": sheets_service.spreadsheet_url,
            "quota_summary": quota
        }
    else:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "ไม่ได้บันทึกเพราะข้อมูลซ้ำในระบบ"}
        )

@app.get("/api/quota")
async def get_quota():
    return sheets_service.get_quota_summary()

@app.post("/api/reset-cycle")
async def reset_cycle():
    success = sheets_service.reset_cycle()
    quota = sheets_service.get_quota_summary()
    return {
        "success": success,
        "message": "รีเซ็ตขึ้นรอบใหม่เรียบร้อยแล้ว! โควตาน้ำมันคืนกลับมาเป็น 400 ลิตรเต็ม",
        "quota_summary": quota
    }

@app.get("/api/records")
async def get_records():
    records = sheets_service.get_all_records()
    quota = sheets_service.get_quota_summary()
    return {
        "count": len(records),
        "google_sheets_active": sheets_service.is_google_sheets_active,
        "spreadsheet_url": sheets_service.spreadsheet_url,
        "quota_summary": quota,
        "records": records
    }

@app.get("/api/sample-images")
async def get_sample_images():
    samples = ["1013130.jpg", "1013131.jpg", "1013132.jpg", "1013133.jpg"]
    result = []
    for s in samples:
        path = os.path.join(r"c:\Users\Boonma\Desktop\oil", s)
        if os.path.exists(path):
            result.append({"filename": s, "size": os.path.getsize(path)})
    return {"samples": result}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
