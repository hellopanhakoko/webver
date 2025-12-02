from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import sqlite3
import os
import random
import string
import qrcode
from io import BytesIO
import base64
from datetime import datetime
import pytz
from bakong_khqr import KHQR
import logging

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ---------- App & Config ----------
app = FastAPI()
templates = Jinja2Templates(directory="../templates")  # templates folder is one level up in Vercel
API_TOKEN = os.getenv("API_TOKEN", "your_api_token_here")
khqr = KHQR(API_TOKEN)
BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "chhira_ly@aclb")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "855882000544")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Phnom_Penh")
DB = "bot_data.db"

# ---------- Helpers ----------
def now_iso():
    tz = pytz.timezone(TIMEZONE)
    return datetime.now(tz).isoformat()

def generate_short_transaction_id() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# ---------- Database ----------
def init_db():
    with sqlite3.connect(DB) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                is_reseller INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS item_prices (
                item_id TEXT PRIMARY KEY,
                game TEXT,
                normal_price REAL,
                reseller_price REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                game TEXT,
                item_id TEXT,
                amount REAL,
                server_id TEXT,
                zone_id TEXT,
                md5 TEXT,
                status TEXT,
                payment_response TEXT,
                created_at TEXT,
                paid_at TEXT
            )
        """)
        # default items
        items = [
            ("86_DIAMOND", "MLBB", 0.03, 0.03),
            ("172_DIAMAND", "MLBB", 0.03, 0.03),
            ("344_DIAMOND", "MLBB", 6.4, 5.6),
            ("429_DIAMOND", "MLBB", 8.0, 7.0),
            ("50_DIAMOND", "FF", 1.0, 0.85),
            ("100_DIAMOND", "FF", 2.0, 1.7)
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO item_prices (item_id, game, normal_price, reseller_price)
            VALUES (?, ?, ?, ?)
        """, items)
        conn.commit()
    logging.info("Database initialized.")

init_db()

# ---------- Item / User Functions ----------
def get_item_prices(game: str):
    with sqlite3.connect(DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT item_id, normal_price, reseller_price FROM item_prices WHERE game=?", (game,))
        rows = cursor.fetchall()
    return {r[0]: {"normal": r[1], "reseller": r[2]} for r in rows}

def is_reseller(user_id: int) -> bool:
    with sqlite3.connect(DB) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_reseller FROM users WHERE user_id=?", (user_id,))
        result = cursor.fetchone()
    return result[0] == 1 if result else False

# ---------- QR Generation ----------
def generate_qr_code(amount: float):
    try:
        qr_payload = khqr.create_qr(
            bank_account=BANK_ACCOUNT,
            merchant_name='PI YA LEGEND',
            merchant_city='Phnom Penh',
            amount=amount,
            currency='USD',
            store_label='MShop',
            phone_number=PHONE_NUMBER,
            bill_number=generate_short_transaction_id(),
            terminal_label='Cashier-01',
            static=False
        )
        img = qrcode.make(qr_payload)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        md5_hash = khqr.generate_md5(qr_payload)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return b64, md5_hash
    except Exception as e:
        logging.error("generate_qr_code error: %s", e)
        return None, None

# ---------- Routes ----------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Demo user
    user_id = 1
    ml_items = get_item_prices("MLBB")
    ff_items = get_item_prices("FF")
    return templates.TemplateResponse("mlbb.html", {"request": request, "ml_items": ml_items, "ff_items": ff_items, "reseller": is_reseller(user_id)})

@app.post("/buy", response_class=HTMLResponse)
async def buy(
    request: Request,
    game: str = Form(...),
    item_id: str = Form(...),
    server_id: str = Form(...),
    zone_id: str = Form(...)
):
    user_id = 1  # demo user
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("SELECT normal_price FROM item_prices WHERE item_id=? AND game=?", (item_id, game))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        amount = float(row[0])
    
    qr_b64, md5 = generate_qr_code(amount)
    if not qr_b64 or not md5:
        raise HTTPException(status_code=500, detail="Failed to generate QR")
    
    order_id = generate_short_transaction_id()
    created_at = now_iso()
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO orders (order_id, user_id, game, item_id, amount, server_id, zone_id, md5, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (order_id, user_id, game, item_id, amount, server_id, zone_id, md5, "UNPAID", created_at))
        conn.commit()
    
    return templates.TemplateResponse("deposit.html", {"request": request, "qr": qr_b64, "order_id": order_id, "amount": amount})

@app.get("/order_status/{order_id}")
async def order_status(order_id: str):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("SELECT status, payment_response, paid_at FROM orders WHERE order_id=?", (order_id,))
        r = c.fetchone()
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": r[0], "payment_response": r[1], "paid_at": r[2]}

@app.get("/orders", response_class=HTMLResponse)
async def orders(request: Request):
    user_id = 1  # demo user
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT order_id, game, item_id, amount, server_id, zone_id, status, created_at, paid_at
            FROM orders WHERE user_id=? ORDER BY created_at DESC
        """, (user_id,))
        rows = c.fetchall()
    return templates.TemplateResponse("orders.html", {"request": request, "orders": rows})
