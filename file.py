import os
import json
import sqlite3
import webbrowser
from drive_upload import upload_pdf_to_drive, upload_monthly_report_to_drive
from db_backup import backup_db_to_drive, restore_db_from_drive
from tkinter import messagebox as _mb
import threading
from datetime import datetime, timedelta
import ttkbootstrap as tb
from drive_upload import upload_pdf_to_drive, upload_monthly_report_to_drive
from ttkbootstrap.constants import *
from ttkbootstrap import Style
import tkinter as tk
from tkinter import StringVar, DoubleVar, IntVar
from tkinter import ttk, messagebox
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.platypus import KeepTogether
import num2words
import calendar

# ----------------- Config -----------------
DB_FILE = "smartbilling.db"
INVOICE_DIR = "invoices"

os.makedirs(INVOICE_DIR, exist_ok=True)

# Shop/Company defaults (editable via Settings)
SHOP_CONFIG = {
    "name": "ARS TRADERS",
    "gstin": "33BRZPR7133L1ZX",
    "address": "Pallivasal Complex (Opp To Police Station)",
    "city": "Thuvakudi, Trichy - 15",
    "mobile": "96552 10084 | 90922 92081",
    "email": "arstraders1947@gmail.com",
    "bank_name": "HDFC Bank",
    "account_no": "12345678901234",
    "ifsc": "HDFC0001234",
    "branch": "Trichy Branch",
    "upi": "arstraders1947@upi",
    "currency": "₹",
}

# ----------------- Database -----------------
def _startup_restore_check():
    """On first launch (no local DB), offer to restore from Drive."""
    if not os.path.exists(DB_FILE):
        # No local DB found — must be a new system
        root_check = tk.Tk()
        root_check.withdraw()
        answer = _mb.askyesno(
            "SmartBilling — Restore Data?",
            "No local database found.\n\n"
            "Do you want to restore all data\n"
            "(customers, products, bills) from Google Drive?\n\n"
            "Click YES to restore.\n"
            "Click NO to start fresh."
        )
        root_check.destroy()
        if answer:
            print("[Startup] Restoring database from Google Drive...")
            restore_db_from_drive()
        else:
            print("[Startup] Starting with a fresh database.")
 
_startup_restore_check()

conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    name TEXT,
    hsn TEXT DEFAULT '',
    price REAL,
    tax_rate REAL DEFAULT 18.0,
    stock INTEGER
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    gstin TEXT DEFAULT '',
    address TEXT DEFAULT '',
    city TEXT DEFAULT '',
    phone TEXT DEFAULT ''
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no TEXT,
    date TEXT,
    customer_id INTEGER,
    customer_name TEXT,
    customer_gstin TEXT,
    customer_address TEXT,
    shipping_address TEXT,
    place_of_supply TEXT,
    items TEXT,
    subtotal REAL,
    discount REAL,
    taxable_amount REAL,
    igst_amount REAL,
    total REAL,
    payment_method TEXT,
    notes TEXT DEFAULT ''
)""")
conn.commit()

# Migrate existing products table if needed
try:
    cursor.execute("ALTER TABLE products ADD COLUMN hsn TEXT DEFAULT ''")
    conn.commit()
except:
    pass
try:
    cursor.execute("ALTER TABLE products ADD COLUMN tax_rate REAL DEFAULT 18.0")
    conn.commit()
except:
    pass

# ----------------- Helpers -----------------
def generate_next_code():
    cursor.execute("SELECT code FROM products ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row and row[0] and row[0].isdigit():
        return str(int(row[0]) + 1)
    cursor.execute("SELECT COUNT(*) FROM products")
    cnt = cursor.fetchone()[0]
    return str(cnt + 1)

def generate_invoice_no():
    now = datetime.now()
    year_suffix = now.strftime("%y")
    month = now.strftime("%m")
    cursor.execute("SELECT COUNT(*) FROM bills WHERE strftime('%Y-%m', date) = ?", 
                   (now.strftime("%Y-%m"),))
    cnt = cursor.fetchone()[0]
    return f"INV-{year_suffix}{month}-{cnt + 1:04d}"

def get_invoice_folder(date_str=None):
    """Restore date-based folders: invoices/YYYY/MM-Month/"""
    dt = datetime.now()
    if date_str:
        try:
            dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
        except ValueError:
            pass  # use now if parse fails
    year_folder = dt.strftime('%Y')
    month_folder = dt.strftime('%m-%B')  # 01-January
    folder_path = os.path.join(INVOICE_DIR, year_folder, month_folder)
    os.makedirs(folder_path, exist_ok=True)
    print(f'Using invoice folder: {folder_path}')
    return folder_path

def add_product_db(code, name, hsn, price, tax_rate, stock):
    try:
        cursor.execute(
            "INSERT INTO products (code, name, hsn, price, tax_rate, stock) VALUES (?, ?, ?, ?, ?, ?)",
            (str(code), name, hsn, float(price), float(tax_rate), int(stock))
        )
        conn.commit()
        return True, "Product added successfully"
    except Exception as e:
        return False, str(e)

def update_product_db(pid, name, hsn, price, tax_rate, stock):
    cursor.execute(
        "UPDATE products SET name=?, hsn=?, price=?, tax_rate=?, stock=? WHERE id=?",
        (name, hsn, float(price), float(tax_rate), int(stock), pid)
    )
    conn.commit()

def delete_product_db(pid):
    cursor.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()

def get_all_products():
    cursor.execute("SELECT id, code, name, hsn, price, tax_rate, stock FROM products ORDER BY id")
    return cursor.fetchall()

def find_products(search):
    s = f"%{search}%"
    cursor.execute(
        "SELECT id, code, name, hsn, price, tax_rate, stock FROM products WHERE code LIKE ? OR name LIKE ? ORDER BY id",
        (s, s)
    )
    return cursor.fetchall()

def get_product_by_code(code):
    cursor.execute("SELECT id, code, name, hsn, price, tax_rate, stock FROM products WHERE code=?", (str(code),))
    return cursor.fetchone()

def save_bill_to_db(invoice_no, customer_data, items, subtotal, discount, taxable_amount, igst_amount, total, payment_method, notes=""):
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    items_json = json.dumps(items)
    cursor.execute("""INSERT INTO bills 
        (invoice_no, date, customer_id, customer_name, customer_gstin, customer_address, shipping_address, 
         place_of_supply, items, subtotal, discount, taxable_amount, igst_amount, total, payment_method, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_no, date_now,
         customer_data.get('id', 0),
         customer_data.get('name', ''),
         customer_data.get('gstin', ''),
         customer_data.get('address', ''),
         customer_data.get('shipping', ''),
         customer_data.get('place_of_supply', ''),
         items_json, float(subtotal), float(discount),
         float(taxable_amount), float(igst_amount), float(total),
         payment_method, notes))
    conn.commit()
    threading.Thread(target=backup_db_to_drive, daemon=True).start()
    return cursor.lastrowid

def amount_in_words(amount):
    """Convert amount to words (INR)"""
    try:
        rupees = int(amount)
        paise = round((amount - rupees) * 100)
        words = num2words.num2words(rupees, lang='en_IN').title()
        result = f"INR {words} Rupees"
        if paise > 0:
            paise_words = num2words.num2words(paise, lang='en_IN').title()
            result += f" And {paise_words} Paise"
        result += " Only."
        return result
    except:
        return f"INR {amount:.2f} Only."

# ----------------- Monthly Analysis Functions -----------------
def get_monthly_analysis(year, month):
    """Get comprehensive monthly analysis data"""
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year + 1}-01-01"
    else:
        end_date = f"{year}-{month + 1:02d}-01"
    
    analysis = {
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "total_bills": 0,
        "total_sales": 0.0,
        "total_taxable": 0.0,
        "total_gst": 0.0,
        "total_discount": 0.0,
        "payment_methods": {},
        "daily_sales": {},
        "product_sales": {},
        "top_products": [],
        "low_stock_products": [],
        "bills_list": []
    }
    
    # Get all bills for the month
    cursor.execute("""
        SELECT id, invoice_no, date, customer_name, items, subtotal, discount, 
               taxable_amount, igst_amount, total, payment_method
        FROM bills 
        WHERE date >= ? AND date < ?
        ORDER BY date
    """, (start_date, end_date))
    
    bills = cursor.fetchall()
    analysis["total_bills"] = len(bills)
    
    for bill in bills:
        bid, inv_no, date_str, cust_name, items_json, subtotal, discount, taxable, igst, total, payment = bill
        
        analysis["total_sales"] += total
        analysis["total_taxable"] += taxable
        analysis["total_gst"] += igst
        analysis["total_discount"] += discount
        
        # Payment method breakdown
        pm = payment or "Cash"
        analysis["payment_methods"][pm] = analysis["payment_methods"].get(pm, 0) + total
        
        # Daily sales
        day = date_str[:10]
        analysis["daily_sales"][day] = analysis["daily_sales"].get(day, 0) + total
        
        # Product sales analysis
        try:
            items = json.loads(items_json)
            for item in items:
                pname = item.get("name", "Unknown")
                qty = int(item.get("qty", 1))
                item_total = float(item.get("total", 0))
                
                if pname not in analysis["product_sales"]:
                    analysis["product_sales"][pname] = {"qty": 0, "revenue": 0}
                analysis["product_sales"][pname]["qty"] += qty
                analysis["product_sales"][pname]["revenue"] += item_total
        except:
            pass
        
        analysis["bills_list"].append({
            "id": bid,
            "invoice_no": inv_no,
            "date": date_str,
            "customer": cust_name or "Walk-in",
            "total": total,
            "payment": payment
        })
    
    # Top 10 products by revenue
    sorted_products = sorted(analysis["product_sales"].items(), 
                            key=lambda x: x[1]["revenue"], reverse=True)
    analysis["top_products"] = sorted_products[:10]
    
    # Low stock products (stock <= 10)
    cursor.execute("SELECT code, name, stock FROM products WHERE stock <= 10 ORDER BY stock ASC")
    analysis["low_stock_products"] = cursor.fetchall()
    
    # Current stock summary
    cursor.execute("SELECT SUM(stock), COUNT(*) FROM products")
    stock_row = cursor.fetchone()
    analysis["total_stock_qty"] = stock_row[0] or 0
    analysis["total_products"] = stock_row[1] or 0
    
    # Stock value
    cursor.execute("SELECT SUM(price * stock) FROM products")
    analysis["stock_value"] = cursor.fetchone()[0] or 0
    
    return analysis

def generate_monthly_report_pdf(year, month):
    """Generate comprehensive monthly analysis PDF report"""
    analysis = get_monthly_analysis(year, month)
    
# Create separate Monthly Reports folder
    MONTHLY_REPORTS_DIR = "monthly_reports"
    os.makedirs(MONTHLY_REPORTS_DIR, exist_ok=True)
    
    report_folder = os.path.join(MONTHLY_REPORTS_DIR, str(year), f"{month:02d}-{calendar.month_name[month]}")
    os.makedirs(report_folder, exist_ok=True)
    
    filename = os.path.join(report_folder, f"Monthly_Report_{year}_{month:02d}.pdf")
    
    c = rl_canvas.Canvas(filename, pagesize=A4)
    W, H = A4
    cur = "Rs."  # Use ASCII for PDF to avoid square box rendering issues
    
    def draw_header(c, page_num=1):
        # Header
        c.setFillColor(colors.HexColor("#1a237e"))
        c.rect(0, H - 35*mm, W, 35*mm, fill=1, stroke=0)
        
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(W/2, H - 15*mm, f"MONTHLY BUSINESS REPORT")
        
        c.setFont("Helvetica", 12)
        c.drawCentredString(W/2, H - 23*mm, f"{analysis['month_name']} {year}")
        
        c.setFont("Helvetica", 9)
        c.drawCentredString(W/2, H - 30*mm, SHOP_CONFIG["name"])
        
        # Page number
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 8)
        c.drawRightString(W - 15*mm, 10*mm, f"Page {page_num}")
        c.drawString(15*mm, 10*mm, f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}")
    
    draw_header(c, 1)
    y = H - 50*mm
    
    # Summary Box
    c.setFillColor(colors.HexColor("#e3f2fd"))
    c.rect(15*mm, y - 45*mm, W - 30*mm, 45*mm, fill=1, stroke=1)
    
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20*mm, y - 8*mm, "SALES SUMMARY")
    
    c.setFont("Helvetica", 11)
    col1_x = 25*mm
    col2_x = W/2 + 10*mm
    
    summary_items = [
        (f"Total Bills Generated:", f"{analysis['total_bills']}", col1_x),
        (f"Total Sales:", f"{cur}{analysis['total_sales']:,.2f}", col1_x),
        (f"Taxable Amount:", f"{cur}{analysis['total_taxable']:,.2f}", col1_x),
        (f"Total GST Collected:", f"{cur}{analysis['total_gst']:,.2f}", col2_x),
        (f"Total Discounts Given:", f"{cur}{analysis['total_discount']:,.2f}", col2_x),
        (f"Net Revenue:", f"{cur}{analysis['total_sales'] - analysis['total_discount']:,.2f}", col2_x),
    ]
    
    row_y = y - 18*mm
    for i, (label, value, x) in enumerate(summary_items):
        if i == 3:
            row_y = y - 18*mm
        c.setFont("Helvetica", 10)
        c.drawString(x, row_y, label)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x + 45*mm, row_y, value)
        row_y -= 8*mm
    
    y = y - 55*mm
    
    # Payment Methods Breakdown
    c.setFont("Helvetica-Bold", 12)
    c.drawString(15*mm, y, "PAYMENT METHODS BREAKDOWN")
    y -= 5*mm
    c.setLineWidth(0.5)
    c.line(15*mm, y, W - 15*mm, y)
    y -= 8*mm
    
    c.setFont("Helvetica", 10)
    for pm, amount in analysis["payment_methods"].items():
        percentage = (amount / analysis["total_sales"] * 100) if analysis["total_sales"] > 0 else 0
        c.drawString(20*mm, y, f"{pm}:")
        c.drawString(60*mm, y, f"{cur}{amount:,.2f}")
        c.drawString(100*mm, y, f"({percentage:.1f}%)")
        y -= 6*mm
    
    y -= 10*mm
    
    # Top Products
    c.setFont("Helvetica-Bold", 12)
    c.drawString(15*mm, y, "TOP 10 PRODUCTS BY REVENUE")
    y -= 5*mm
    c.line(15*mm, y, W - 15*mm, y)
    y -= 3*mm
    
    # Table header
    c.setFillColor(colors.HexColor("#f5f5f5"))
    c.rect(15*mm, y - 7*mm, W - 30*mm, 7*mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(17*mm, y - 5*mm, "#")
    c.drawString(25*mm, y - 5*mm, "Product Name")
    c.drawString(120*mm, y - 5*mm, "Qty Sold")
    c.drawString(150*mm, y - 5*mm, "Revenue")
    y -= 10*mm
    
    c.setFont("Helvetica", 9)
    for i, (pname, data) in enumerate(analysis["top_products"], 1):
        if y < 60*mm:
            c.showPage()
            draw_header(c, 2)
            y = H - 50*mm
        
        c.drawString(17*mm, y, str(i))
        c.drawString(25*mm, y, pname[:40])
        c.drawString(120*mm, y, str(data["qty"]))
        c.drawString(150*mm, y, f"{cur}{data['revenue']:,.2f}")
        y -= 6*mm
    
    y -= 10*mm
    
    # Stock Status
    if y < 80*mm:
        c.showPage()
        draw_header(c, 2)
        y = H - 50*mm
    
    c.setFont("Helvetica-Bold", 12)
    c.drawString(15*mm, y, "INVENTORY STATUS")
    y -= 5*mm
    c.line(15*mm, y, W - 15*mm, y)
    y -= 10*mm
    
    c.setFont("Helvetica", 10)
    c.drawString(20*mm, y, f"Total Products in Inventory: {analysis['total_products']}")
    y -= 7*mm
    c.drawString(20*mm, y, f"Total Stock Quantity: {analysis['total_stock_qty']} units")
    y -= 7*mm
    c.drawString(20*mm, y, f"Total Stock Value: {cur}{analysis['stock_value']:,.2f}")
    y -= 12*mm
    
    # Low Stock Alert
    if analysis["low_stock_products"]:
        c.setFillColor(colors.HexColor("#ffebee"))
        c.rect(15*mm, y - (len(analysis["low_stock_products"]) * 6 + 15)*mm, 
               W - 30*mm, (len(analysis["low_stock_products"]) * 6 + 15)*mm, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#c62828"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20*mm, y - 8*mm, "⚠ LOW STOCK ALERT (Stock ≤ 10)")
        
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9)
        y -= 15*mm
        for code, name, stock in analysis["low_stock_products"][:15]:
            c.drawString(25*mm, y, f"{code} - {name[:35]}")
            c.setFillColor(colors.red)
            c.drawString(140*mm, y, f"Stock: {stock}")
            c.setFillColor(colors.black)
            y -= 6*mm
    
    # Daily Sales Chart (simple text version)
    if y < 60*mm:
        c.showPage()
        draw_header(c, 3)
        y = H - 50*mm
    
    y -= 15*mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(15*mm, y, "DAILY SALES BREAKDOWN")
    y -= 5*mm
    c.line(15*mm, y, W - 15*mm, y)
    y -= 8*mm
    
    c.setFont("Helvetica", 8)
    col = 0
    start_y = y
    for day, amount in sorted(analysis["daily_sales"].items()):
        if y < 20*mm:
            col += 1
            y = start_y
        x = 20*mm + (col * 60*mm)
        if x > W - 40*mm:
            break
        c.drawString(x, y, f"{day[8:]}: {cur}{amount:,.2f}")
        y -= 5*mm
    
    c.save()
    return filename

# ----------------- PDF Invoice Generation -----------------
def generate_tax_invoice_pdf(bill_id):
    cursor.execute("""SELECT id, invoice_no, date, customer_name, customer_gstin, customer_address, 
        shipping_address, place_of_supply, items, subtotal, discount, taxable_amount, igst_amount, total, 
        payment_method, notes FROM bills WHERE id=?""", (bill_id,))
    row = cursor.fetchone()
    if not row:
        return None

    (bid, inv_no, date_str, cust_name, cust_gstin, cust_addr, ship_addr,
     place_of_supply, items_json, subtotal, discount, taxable_amount, igst_amount, total,
     payment_method, notes) = row
    items = json.loads(items_json)

    # Get folder based on bill date
    folder_path = get_invoice_folder(date_str)
    safe_inv_no = inv_no.replace('/', '_').replace('\\', '_')
    filename = os.path.join(folder_path, f"Invoice_{safe_inv_no}.pdf")
    
    c = rl_canvas.Canvas(filename, pagesize=A4)
    W, H = A4
    cur = "Rs."  # Use ASCII for PDF to avoid square box rendering issues with Helvetica font

    # Page 1
    page_num = [1]
    
    def new_page():
        c.showPage()
        page_num[0] += 1
        return H - 20*mm
    
    def draw_page_header():
        # Outer border
        c.setLineWidth(1.5)
        c.rect(10*mm, 10*mm, W - 20*mm, H - 20*mm)
        
        # Title bar
        c.setFillColor(colors.HexColor("#1a237e"))
        c.rect(10*mm, H - 25*mm, W - 20*mm, 15*mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(W/2, H - 18*mm, "TAX INVOICE")
        c.setFont("Helvetica", 8)
        c.drawRightString(W - 15*mm, H - 22*mm, "ORIGINAL FOR RECIPIENT")
        
    draw_page_header()
    
    # Company info
    y = H - 30*mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15*mm, y, SHOP_CONFIG["name"])
    c.setFont("Helvetica", 9)
    y -= 5*mm
    c.drawString(15*mm, y, f"GSTIN: {SHOP_CONFIG['gstin']}")
    y -= 4*mm
    c.drawString(15*mm, y, SHOP_CONFIG["address"])
    y -= 4*mm
    c.drawString(15*mm, y, SHOP_CONFIG["city"])
    y -= 4*mm
    c.drawString(15*mm, y, f"Mobile: {SHOP_CONFIG['mobile']}")
    y -= 4*mm
    c.drawString(15*mm, y, f"Email: {SHOP_CONFIG['email']}")
    
    # Invoice details box (right side)
    box_x = W/2 + 5*mm
    box_y = H - 30*mm
    c.setLineWidth(0.5)
    c.rect(box_x, box_y - 22*mm, W/2 - 17*mm, 22*mm)
    
    c.setFont("Helvetica-Bold", 9)
    c.drawString(box_x + 3*mm, box_y - 5*mm, "Invoice No:")
    c.setFont("Helvetica", 9)
    c.drawString(box_x + 25*mm, box_y - 5*mm, inv_no)
    
    c.setFont("Helvetica-Bold", 9)
    c.drawString(box_x + 3*mm, box_y - 12*mm, "Date:")
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        inv_date_str = dt.strftime("%d %b %Y")
    except:
        inv_date_str = date_str[:10]
    c.setFont("Helvetica", 9)
    c.drawString(box_x + 25*mm, box_y - 12*mm, inv_date_str)
    
    c.setFont("Helvetica-Bold", 9)
    c.drawString(box_x + 3*mm, box_y - 19*mm, "Place of Supply:")
    c.setFont("Helvetica", 9)
    c.drawString(box_x + 35*mm, box_y - 19*mm, place_of_supply or "Maharashtra")
    
    # Divider
    y = H - 58*mm
    c.setLineWidth(0.5)
    c.line(10*mm, y, W - 10*mm, y)
    
    # Customer details
    y -= 3*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(15*mm, y, "Bill To:")
    c.setFont("Helvetica-Bold", 10)
    y -= 5*mm
    c.drawString(15*mm, y, cust_name or "Walk-in Customer")
    c.setFont("Helvetica", 9)
    if cust_gstin:
        y -= 4*mm
        c.drawString(15*mm, y, f"GSTIN: {cust_gstin}")
    if cust_addr:
        y -= 4*mm
        for line in cust_addr.split('\n')[:3]:
            c.drawString(15*mm, y, line)
            y -= 4*mm
    
    # Ship to (right side)
    if ship_addr:
        ship_y = H - 61*mm
        c.setFont("Helvetica-Bold", 9)
        c.drawString(W/2 + 5*mm, ship_y, "Ship To:")
        c.setFont("Helvetica", 9)
        ship_y -= 5*mm
        for line in ship_addr.split('\n')[:4]:
            c.drawString(W/2 + 5*mm, ship_y, line)
            ship_y -= 4*mm
    
    # Items table
    table_top = H - 90*mm
    c.setLineWidth(0.5)
    c.line(10*mm, table_top, W - 10*mm, table_top)
    
    # Table header
    c.setFillColor(colors.HexColor("#e3f2fd"))
    c.rect(10*mm, table_top - 8*mm, W - 20*mm, 8*mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8)
    
    headers = [
        (12, "#"), (20, "Item"), (90, "HSN"), (110, "Rate"), 
        (130, "Qty"), (145, "Taxable"), (165, "Tax%"), (178, "Tax"), (192, "Total")
    ]
    for x, text in headers:
        c.drawString(x*mm, table_top - 6*mm, text)
    
    c.line(10*mm, table_top - 8*mm, W - 10*mm, table_top - 8*mm)
    
    # Table rows
    y = table_top - 8*mm
    c.setFont("Helvetica", 8)
    
    for idx, it in enumerate(items, 1):
        if y < 55*mm:
            y = new_page()
            draw_page_header()
            y = H - 35*mm
        
        y -= 7*mm
        
        # Alternating row colors
        if idx % 2 == 0:
            c.setFillColor(colors.HexColor("#fafafa"))
            c.rect(10*mm, y - 1*mm, W - 20*mm, 7*mm, fill=1, stroke=0)
            c.setFillColor(colors.black)
        
        price = float(it.get("price", 0))
        qty = int(it.get("qty", 1))
        tax_rate = float(it.get("tax_rate", 18))
        taxable = price * qty
        tax_amt = taxable * tax_rate / 100
        item_total = taxable + tax_amt
        
        c.drawString(12*mm, y, str(idx))
        c.drawString(20*mm, y, str(it.get("name", ""))[:30])
        c.drawString(90*mm, y, str(it.get("hsn", "")))
        c.drawRightString(125*mm, y, f"{price:,.2f}")
        c.drawString(131*mm, y, str(qty))
        c.drawRightString(160*mm, y, f"{taxable:,.2f}")
        c.drawString(166*mm, y, f"{tax_rate:.0f}%")
        c.drawRightString(185*mm, y, f"{tax_amt:,.2f}")
        c.setFont("Helvetica-Bold", 8)
        c.drawRightString(W - 12*mm, y, f"{item_total:,.2f}")
        c.setFont("Helvetica", 8)
        
        c.setLineWidth(0.2)
        c.line(10*mm, y - 1*mm, W - 10*mm, y - 1*mm)
    
    # Totals section
    y -= 5*mm
    c.setLineWidth(0.5)
    c.line(10*mm, y, W - 10*mm, y)
    
    # Summary
    y -= 7*mm
    c.setFont("Helvetica", 9)
    c.drawString(15*mm, y, f"Total Items: {len(items)} | Total Qty: {sum(int(i.get('qty', 1)) for i in items)}")
    
    # Right side totals
    totals_x = 140*mm
    c.setFont("Helvetica", 9)
    c.drawString(totals_x, y, "Subtotal:")
    c.drawRightString(W - 12*mm, y, f"{cur}{subtotal:,.2f}")
    
    if discount > 0:
        y -= 6*mm
        c.drawString(totals_x, y, "Discount:")
        c.drawRightString(W - 12*mm, y, f"- {cur}{discount:,.2f}")
    
    y -= 6*mm
    c.drawString(totals_x, y, "Taxable Amount:")
    c.drawRightString(W - 12*mm, y, f"{cur}{taxable_amount:,.2f}")
    
    y -= 6*mm
    c.drawString(totals_x, y, "IGST:")
    c.drawRightString(W - 12*mm, y, f"{cur}{igst_amount:,.2f}")
    
    y -= 3*mm
    c.setLineWidth(1)
    c.line(totals_x - 5*mm, y, W - 10*mm, y)
    
    y -= 8*mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(totals_x, y, "GRAND TOTAL:")
    c.setFillColor(colors.HexColor("#1a237e"))
    c.drawRightString(W - 12*mm, y, f"{cur}{total:,.2f}")
    c.setFillColor(colors.black)
    
    # Amount in words
    y -= 10*mm
    c.setFont("Helvetica", 8)
    c.drawString(15*mm, y, f"Amount in words: {amount_in_words(total)}")
    
    y -= 5*mm
    c.line(10*mm, y, W - 10*mm, y)
    
    # HSN Summary
    y -= 8*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(15*mm, y, "HSN Summary")
    
    y -= 5*mm
    c.setFillColor(colors.HexColor("#f5f5f5"))
    c.rect(15*mm, y - 6*mm, W - 30*mm, 6*mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(17*mm, y - 4*mm, "HSN/SAC")
    c.drawString(50*mm, y - 4*mm, "Taxable Value")
    c.drawString(85*mm, y - 4*mm, "Tax Rate")
    c.drawString(110*mm, y - 4*mm, "Tax Amount")
    c.drawString(145*mm, y - 4*mm, "Total")
    
    # Group by HSN
    hsn_groups = {}
    for it in items:
        h = it.get("hsn", "N/A") or "N/A"
        t = float(it.get("price", 0)) * int(it.get("qty", 1))
        tr = float(it.get("tax_rate", 18))
        if h not in hsn_groups:
            hsn_groups[h] = {"taxable": 0, "tax_rate": tr, "tax_amt": 0}
        hsn_groups[h]["taxable"] += t
        hsn_groups[h]["tax_amt"] += t * tr / 100
    
    y -= 8*mm
    c.setFont("Helvetica", 7)
    for h, hdata in hsn_groups.items():
        c.drawString(17*mm, y, h)
        c.drawRightString(75*mm, y, f"{cur}{hdata['taxable']:,.2f}")
        c.drawString(90*mm, y, f"{hdata['tax_rate']:.0f}%")
        c.drawRightString(130*mm, y, f"{cur}{hdata['tax_amt']:,.2f}")
        c.drawRightString(165*mm, y, f"{cur}{hdata['taxable'] + hdata['tax_amt']:,.2f}")
        y -= 5*mm
    
    y -= 3*mm
    c.line(10*mm, y, W - 10*mm, y)
    
    # Bank details
    y -= 8*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(15*mm, y, "Bank Details:")
    c.setFont("Helvetica", 8)
    y -= 5*mm
    c.drawString(15*mm, y, f"Bank: {SHOP_CONFIG['bank_name']}")
    y -= 4*mm
    c.drawString(15*mm, y, f"A/C No: {SHOP_CONFIG['account_no']}")
    y -= 4*mm
    c.drawString(15*mm, y, f"IFSC: {SHOP_CONFIG['ifsc']}")
    y -= 4*mm
    c.drawString(15*mm, y, f"Branch: {SHOP_CONFIG['branch']}")
    
    # UPI
    c.setFont("Helvetica-Bold", 9)
    c.drawString(80*mm, y + 17*mm, "Pay via UPI:")
    c.setFont("Helvetica", 8)
    c.drawString(80*mm, y + 12*mm, SHOP_CONFIG["upi"])
    
    # Signature
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(W - 15*mm, y + 17*mm, f"For {SHOP_CONFIG['name']}")
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 15*mm, y, "Authorized Signatory")
    
    y -= 8*mm
    c.line(10*mm, y, W - 10*mm, y)
    
    # Notes and Terms
    y -= 6*mm
    c.setFont("Helvetica-Bold", 8)
    c.drawString(15*mm, y, "Notes:")
    c.setFont("Helvetica", 7)
    y -= 4*mm
    c.drawString(15*mm, y, notes or "Thank you for your business!")
    
    c.line(W/2, y + 10*mm, W/2, y - 20*mm)
    
    c.setFont("Helvetica-Bold", 8)
    c.drawString(W/2 + 5*mm, y + 4*mm, "Terms & Conditions:")
    c.setFont("Helvetica", 6)
    terms = [
        "1. Goods once sold cannot be taken back or exchanged.",
        "2. Warranty as per manufacturer's terms.",
        "3. Interest @24% p.a. charged on overdue payments.",
        "4. Subject to local jurisdiction only."
    ]
    for term in terms:
        c.drawString(W/2 + 5*mm, y, term)
        y -= 4*mm
    
    # Footer
    c.setFont("Helvetica", 7)
    c.drawCentredString(W/2, 12*mm, "This is a computer generated invoice.")
    
    c.save()
    return filename


# =================== UI SETUP ===================
app = tb.Window(themename="darkly")
app.title("ARS Traders — GST Invoice System")
try:
    app.state('zoomed')
except:
    app.attributes('-fullscreen', True)

style = app.style
style.configure('.', font=('Segoe UI', 11))
style.configure('Treeview', rowheight=30, font=('Segoe UI', 11))
style.configure('Treeview.Heading', font=('Segoe UI', 11, 'bold'))
style.map('Treeview',
    background=[('selected', '#0d6efd')],
    foreground=[('selected', 'white')]
)
style.configure('TButton', font=('Segoe UI', 11, 'bold'))
style.configure('TLabel', font=('Segoe UI', 11))
style.configure('TEntry', font=('Segoe UI', 11))

def apply_styles():
    style.configure('.', font=('Segoe UI', 11))
    style.configure('Treeview', rowheight=30)
    style.configure('TButton', font=('Segoe UI', 11, 'bold'))
    style.configure('TLabel', font=('Segoe UI', 11))
    style.configure('TEntry', font=('Segoe UI', 11))

def toggle_theme():
    cur = style.theme.name
    if cur == 'darkly':
        style.theme_use('flatly')
        theme_btn.config(text="🌙 Dark")
        lbl_shop.config(foreground='#1a1a2e')
    else:
        style.theme_use('darkly')
        theme_btn.config(text="☀️ Light")
        lbl_shop.config(foreground='white')
    apply_styles()

# -------- Top Bar (Header + Navbar combined) --------
topbar = ttk.Frame(app)
topbar.pack(fill="x", padx=10, pady=6)

# Left side: theme toggle + shop name + gstin
left_bar = ttk.Frame(topbar)
left_bar.pack(side="left", fill="y")

theme_btn = tb.Button(left_bar, text="☀️ Light", bootstyle="secondary-outline",
                      command=toggle_theme, width=9, padding=(3, 5))
theme_btn.pack(side="left", padx=(0, 8))

lbl_shop = ttk.Label(left_bar, text=f"🧾 {SHOP_CONFIG['name']}",
                     font=('Segoe UI', 15, 'bold'), foreground='white')
lbl_shop.pack(side="left", padx=(0, 8))

gstin_lbl = ttk.Label(left_bar, text=f"GSTIN: {SHOP_CONFIG['gstin']}",
                      font=('Segoe UI', 10), foreground='#adb5bd')
gstin_lbl.pack(side="left")

# Right side: all nav buttons
nav_specs = [
    ("🛒 New Bill",     "billing",   "primary"),
    ("➕ Add Product",  "add",       "success"),
    ("📋 Products",     "list",      "info"),
    ("📦 Stock",        "stock",     "warning"),
    ("📜 History",      "history",   "secondary"),
    ("📊 Analytics",    "analytics", "info"),
    ("⚙ Settings",     "settings",  "dark"),
    ("✖ Exit",          "exit",      "danger"),
]
nav_buttons = {}
for (label, key, bstyle) in nav_specs:
    b = tb.Button(topbar, text=label, bootstyle=bstyle, padding=(6, 5))
    b.pack(side="right", padx=3)
    nav_buttons[key] = b

# -------- Separator --------
ttk.Separator(app, orient='horizontal').pack(fill='x', padx=10, pady=2)

# -------- Main Layout --------
main = ttk.Frame(app)
main.pack(fill="both", expand=True, padx=10, pady=6)

left = ttk.Frame(main)
left.pack(side="left", fill="both", expand=True, padx=(0, 8))

right = ttk.Labelframe(main, text="  📋 Bill Summary  ", padding=10, width=360)
right.pack(side="right", fill="y")
right.pack_propagate(False)

# -------- Cart Table --------
cart_lf = ttk.Labelframe(left, text="  🛍️ Cart Items  ", padding=8)
cart_lf.pack(fill="both", expand=True)

cart_cols = ("#", "Code", "Product", "HSN", "Qty", "Rate", "Taxable", "Tax%", "Tax Amt", "Total", "Del")
cart_tree = ttk.Treeview(cart_lf, columns=cart_cols, show='headings', height=16)
col_widths = {"#": 35, "Code": 60, "Product": 180, "HSN": 80, "Qty": 50, "Rate": 80,
              "Taxable": 85, "Tax%": 50, "Tax Amt": 75, "Total": 90, "Del": 40}
for c in cart_cols:
    cart_tree.heading(c, text=c)
    cart_tree.column(c, width=col_widths.get(c, 80), anchor='center')

vsb = ttk.Scrollbar(cart_lf, orient='vertical', command=cart_tree.yview)
cart_tree.configure(yscrollcommand=vsb.set)
cart_tree.pack(side='left', fill='both', expand=True)
vsb.pack(side='right', fill='y')

cart_row_num = [0]

def on_cart_click(event):
    item = cart_tree.identify('item', event.x, event.y)
    col = cart_tree.identify('column', event.x, event.y)
    if not item or not col:
        return
    ci = int(col[1:]) - 1
    if ci == 10:  # Remove (⊖)
        cart_tree.delete(item)
        renumber_cart()
        update_summary()

cart_tree.bind('<Button-1>', on_cart_click)

def renumber_cart():
    for i, child in enumerate(cart_tree.get_children(), 1):
        vals = list(cart_tree.item(child)['values'])
        vals[0] = i
        cart_tree.item(child, values=vals)

# -------- Bill Summary (Right Panel) --------
date_lbl = ttk.Label(right, text=f"📅 {datetime.now().strftime('%d %b %Y')}", font=('Segoe UI', 11, 'bold'))
date_lbl.pack(pady=3)

inv_no_var = StringVar(value=generate_invoice_no())
inv_no_frame = ttk.Frame(right); inv_no_frame.pack(fill='x', pady=2)
ttk.Label(inv_no_frame, text="Invoice No:", font=('Segoe UI', 10)).pack(side='left')
ttk.Entry(inv_no_frame, textvariable=inv_no_var, width=13, font=('Segoe UI', 10)).pack(side='left', padx=4)

ttk.Separator(right).pack(fill='x', pady=3)

# Customer data stored as dict (filled via popup)
cust_name_var = StringVar()
cust_gstin_var = StringVar()
cust_addr_var = StringVar()
cust_pos_var = StringVar()

# Label showing current customer name (updates after popup)
cust_display_lbl = ttk.Label(right, text="No customer entered", font=('Segoe UI', 10), foreground='#adb5bd')
cust_display_lbl.pack(anchor='w', padx=4, pady=(0,2))

def open_bill_details():
    d = tb.Toplevel(app)
    d.title("Enter Bill Details")
    d.transient(app)
    d.grab_set()
    d.resizable(True, True)

    ttk.Label(d, text="Bill Details", font=('Segoe UI', 13, 'bold')).pack(pady=(12,4), padx=14, anchor='w')
    ttk.Separator(d, orient='horizontal').pack(fill='x', padx=10)

    frm = ttk.Frame(d, padding=12)
    frm.pack(fill='both', expand=True, padx=6, pady=6)
    frm.columnconfigure(1, weight=1)

    # Name
    ttk.Label(frm, text="Customer Name:", font=('Segoe UI', 11)).grid(row=0, column=0, sticky='e', padx=(0,10), pady=6)
    name_e = ttk.Entry(frm, font=('Segoe UI', 11))
    name_e.grid(row=0, column=1, sticky='ew', pady=6)
    name_e.insert(0, cust_name_var.get())

    # GSTIN
    ttk.Label(frm, text="GSTIN:", font=('Segoe UI', 11)).grid(row=1, column=0, sticky='e', padx=(0,10), pady=6)
    gstin_e = ttk.Entry(frm, font=('Segoe UI', 11))
    gstin_e.grid(row=1, column=1, sticky='ew', pady=6)
    gstin_e.insert(0, cust_gstin_var.get())

    # Address - multiline Text widget, comma → newline
    ttk.Label(frm, text="Address:", font=('Segoe UI', 11)).grid(row=2, column=0, sticky='ne', padx=(0,10), pady=6)
    addr_frame = ttk.Frame(frm)
    addr_frame.grid(row=2, column=1, sticky='ew', pady=6)
    addr_frame.columnconfigure(0, weight=1)
    addr_text = tk.Text(addr_frame, font=('Segoe UI', 11), height=4, wrap='word',
                        relief='flat', borderwidth=1, highlightthickness=1)
    addr_text.grid(row=0, column=0, sticky='ew')
    ttk.Label(addr_frame, text="Tip: Use comma ( , ) or Enter for each address line",
              font=('Segoe UI', 8), foreground='gray').grid(row=1, column=0, sticky='w', pady=(2,0))

    # Pre-fill address: stored newline-separated → show as-is
    existing_addr = cust_addr_var.get()
    addr_text.insert('1.0', existing_addr)

    def on_addr_key(event):
        # Replace comma with newline automatically
        if event.char == ',':
            addr_text.insert(tk.INSERT, '\n')
            return 'break'

    addr_text.bind('<Key>', on_addr_key)

    # Place of Supply
    ttk.Label(frm, text="Place of Supply:", font=('Segoe UI', 11)).grid(row=3, column=0, sticky='e', padx=(0,10), pady=6)
    pos_e = ttk.Entry(frm, font=('Segoe UI', 11))
    pos_e.grid(row=3, column=1, sticky='ew', pady=6)
    pos_e.insert(0, cust_pos_var.get())

    ttk.Separator(d, orient='horizontal').pack(fill='x', padx=10, pady=4)
    btn_row = ttk.Frame(d); btn_row.pack(pady=(0,12), padx=14, anchor='e')

    def do_save():
        cust_name_var.set(name_e.get().strip())
        cust_gstin_var.set(gstin_e.get().strip())
        # Get address from Text widget, strip trailing whitespace
        addr_raw = addr_text.get('1.0', 'end-1c').strip()
        cust_addr_var.set(addr_raw)
        cust_pos_var.set(pos_e.get().strip())
        # Update display label
        name_shown = cust_name_var.get() or "Walk-in Customer"
        cust_display_lbl.config(text=f"Customer: {name_shown}", foreground='#28a745')
        d.destroy()

    tb.Button(btn_row, text="Cancel",   bootstyle="secondary-outline", command=d.destroy).pack(side='left', padx=(0,8))
    tb.Button(btn_row, text="Save Details", bootstyle="success",       command=do_save).pack(side='left')

    d.update_idletasks()
    d.geometry("480x430")
    d.minsize(420, 380)

tb.Button(right, text="📝 Enter Bill Details", bootstyle="info-outline",
          command=open_bill_details).pack(fill='x', padx=4, pady=(0,4))

ttk.Separator(right).pack(fill='x', pady=3)

# Discount
disc_row = ttk.Frame(right); disc_row.pack(fill='x', pady=2)
ttk.Label(disc_row, text="Discount (Rs):", font=('Segoe UI', 10)).pack(side='left')
discount_var = DoubleVar(value=0.0)
ttk.Entry(disc_row, textvariable=discount_var, width=10, font=('Segoe UI', 10)).pack(side='left', padx=4)

ttk.Separator(right).pack(fill='x', pady=3)

# Totals
subtotal_lbl = ttk.Label(right, text="Subtotal:        Rs 0.00", font=('Segoe UI', 11))
subtotal_lbl.pack(anchor='w', padx=4, pady=1)
taxable_lbl = ttk.Label(right, text="Taxable Amt:  Rs 0.00", font=('Segoe UI', 11))
taxable_lbl.pack(anchor='w', padx=4, pady=1)
igst_lbl = ttk.Label(right, text="IGST:               Rs 0.00", font=('Segoe UI', 11))
igst_lbl.pack(anchor='w', padx=4, pady=1)
disc_lbl = ttk.Label(right, text="Discount:         Rs 0.00", font=('Segoe UI', 11))
disc_lbl.pack(anchor='w', padx=4, pady=1)

ttk.Separator(right).pack(fill='x', pady=3)
grand_lbl = ttk.Label(right, text="GRAND TOTAL:  Rs 0.00", font=('Segoe UI', 13, 'bold'), foreground='#28a745')
grand_lbl.pack(anchor='w', padx=4, pady=4)

# Payment
pm_frame = ttk.Frame(right); pm_frame.pack(fill='x', pady=3)
payment_var = StringVar(value="Cash")
ttk.Label(pm_frame, text="Payment:", font=('Segoe UI', 10)).pack(side='left')
pm_combo = ttk.Combobox(pm_frame, textvariable=payment_var, values=["Cash", "UPI", "Card", "Cheque", "NEFT/RTGS"],
                        width=11, state='readonly', font=('Segoe UI', 10))
pm_combo.pack(side='left', padx=6)

ttk.Separator(right).pack(fill='x', pady=3)

notes_lf = ttk.Labelframe(right, text=" Notes ", padding=5)
notes_lf.pack(fill='x', pady=3)
notes_var = StringVar(value="Thank you for the Business")
ttk.Entry(notes_lf, textvariable=notes_var, font=('Segoe UI', 10)).pack(fill='x')

subtotal_val = DoubleVar(value=0.0)
grand_val = DoubleVar(value=0.0)

def update_summary(*args):
    raw_sub = 0.0
    raw_igst = 0.0
    for ch in cart_tree.get_children():
        v = cart_tree.item(ch)['values']
        try:
            raw_sub += float(str(v[6]).replace(',', ''))
            raw_igst += float(str(v[8]).replace(',', ''))
        except:
            pass
    try:
        disc = float(discount_var.get() or 0.0)
    except:
        disc = 0.0
    disc = min(disc, raw_sub)
    taxable_amt = raw_sub - disc
    igst_amt = raw_igst
    grand = taxable_amt + igst_amt
    subtotal_val.set(raw_sub)
    grand_val.set(grand)
    cur = SHOP_CONFIG["currency"]
    subtotal_lbl.config(text=f"Subtotal:        {cur}{raw_sub:,.2f}")
    taxable_lbl.config(text=f"Taxable Amt:  {cur}{taxable_amt:,.2f}")
    igst_lbl.config(text=f"IGST:               {cur}{igst_amt:,.2f}")
    disc_lbl.config(text=f"Discount:         {cur}{disc:,.2f}")
    grand_lbl.config(text=f"GRAND TOTAL:  {cur}{grand:,.2f}")

discount_var.trace_add('write', update_summary)

def save_and_export():
    children = cart_tree.get_children()
    if not children:
        messagebox.showwarning("Empty Cart", "Please add items to the cart first.")
        return
    
    def clean_num(x):
        return float(str(x).replace(',','').replace('Rs','').replace('₹','').strip() or 0)

    items = []
    for ch in children:
        v = cart_tree.item(ch)["values"]
        try:
            items.append({
                "code":     str(v[1]),
                "name":     str(v[2]),
                "hsn":      str(v[3]),
                "qty":      int(str(v[4]).strip() or 1),
                "price":    clean_num(v[5]),
                "taxable":  clean_num(v[6]),
                "tax_rate": float(str(v[7]).replace("%","").strip() or 18),
                "tax_amt":  clean_num(v[8]),
                "total":    clean_num(v[9]),
            })
        except Exception as e:
            messagebox.showerror("Cart Error", f"Could not read row. Error: {e}")
            return
    
    raw_sub = sum(i['taxable'] for i in items)
    raw_igst = sum(i['tax_amt'] for i in items)
    disc = float(discount_var.get() or 0.0)
    taxable_amt = raw_sub - disc
    grand = taxable_amt + raw_igst
    
    customer = {
        "id": 0,
        "name": cust_name_var.get() or "Walk-in Customer",
        "gstin": cust_gstin_var.get(),
        "address": cust_addr_var.get(),
        "shipping": cust_addr_var.get(),
        "place_of_supply": cust_pos_var.get() or "Maharashtra",
    }
    
    inv_no = inv_no_var.get()
    bill_id = save_bill_to_db(inv_no, customer, items, raw_sub, disc, taxable_amt, raw_igst, grand,
                               payment_var.get(), notes_var.get())
    
    # Deduct stock
    for it in items:
        prod = get_product_by_code(str(it['code']))
        if prod:
            new_stock = max(0, prod[6] - int(it['qty']))
            cursor.execute("UPDATE products SET stock=? WHERE id=?", (new_stock, prod[0]))
    conn.commit()
    
    # Generate PDF
    try:
        pdf = generate_tax_invoice_pdf(bill_id)

        if pdf:
            date_str = datetime.now().strftime("%Y-%m-%d")
            threading.Thread(
                target=upload_pdf_to_drive,
                args=(pdf, inv_no, date_str),
                daemon=True
            ).start()
        if pdf:
            ans = messagebox.askyesno("Invoice Saved",
                f"Invoice {inv_no} saved!\nPDF: {pdf}\n\nOpen PDF now?")
            if ans:
                try:
                    if os.name == 'nt':
                        os.startfile(os.path.abspath(pdf))
                    else:
                        webbrowser.open_new(os.path.abspath(pdf))
                except Exception as e:
                    messagebox.showerror("Error", f"Could not open PDF: {e}")
        else:
            messagebox.showinfo("Saved", f"Invoice {inv_no} saved (ID: {bill_id})")
    except Exception as e:
        messagebox.showerror("PDF Error", f"Bill saved but PDF generation failed:\n{e}")
    
    # Clear cart
    for ch in cart_tree.get_children():
        cart_tree.delete(ch)
    cust_name_var.set('')
    cust_gstin_var.set('')
    cust_addr_var.set('')
    cust_pos_var.set('')
    cust_display_lbl.config(text="No customer entered", foreground='#adb5bd')
    discount_var.set(0.0)
    inv_no_var.set(generate_invoice_no())
    update_summary()

save_btn = tb.Button(right, text="💾 Save & Export PDF", bootstyle="success", command=save_and_export)
save_btn.pack(pady=4, fill="x", padx=4)
clear_btn = tb.Button(right, text="🗑 Clear Cart", bootstyle="warning-outline",
                      command=lambda: [cart_tree.delete(c) for c in cart_tree.get_children()] or update_summary())
clear_btn.pack(pady=2, fill="x", padx=4)

# =================== POPUPS ===================

def open_add_product():
    p = tb.Toplevel(app)
    p.title("Add New Product")
    p.transient(app)
    p.grab_set()
    p.resizable(True, True)

    ttk.Label(p, text="Add New Product", font=('Segoe UI', 14, 'bold')).pack(pady=8, padx=16, anchor='w')
    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)

    frm = ttk.Frame(p, padding=12)
    frm.pack(fill='both', expand=True, padx=10, pady=6)
    frm.columnconfigure(1, weight=1)

    fields = [
        ("Product Code *", "code",     generate_next_code()),
        ("Product Name *", "name",     ""),
        ("HSN / SAC Code", "hsn",      ""),
        ("Price (Rs) *",   "price",    ""),
        ("Tax Rate (%)",   "tax_rate", "18"),
        ("Stock Qty",      "stock",    "0"),
    ]
    entries = {}
    for i, (label, key, default) in enumerate(fields):
        ttk.Label(frm, text=label, font=('Segoe UI', 11)).grid(row=i, column=0, sticky='e', padx=(0,10), pady=5)
        e = ttk.Entry(frm, font=('Segoe UI', 11))
        e.grid(row=i, column=1, sticky='ew', pady=5)
        e.insert(0, default)
        entries[key] = e

    ttk.Label(frm, text="* Required fields", font=('Segoe UI', 9), foreground='gray').grid(
        row=len(fields), column=0, columnspan=2, sticky='w', pady=(4,0))

    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10, pady=6)
    btn_row = ttk.Frame(p); btn_row.pack(pady=(0,10), padx=16, anchor='e')

    def do_add():
        code     = entries['code'].get().strip()
        name     = entries['name'].get().strip()
        hsn      = entries['hsn'].get().strip()
        price    = entries['price'].get().strip()
        tax_rate = entries['tax_rate'].get().strip()
        stock    = entries['stock'].get().strip()
        if not code or not name or not price:
            messagebox.showwarning("Missing Fields", "Code, Name and Price are required.", parent=p)
            return
        try:
            float(price)
        except:
            messagebox.showwarning("Invalid", "Price must be a number.", parent=p)
            return
        ok, msg = add_product_db(code, name, hsn, price, tax_rate or 18, stock or 0)
        if ok:
            messagebox.showinfo("Success", msg, parent=p)
            p.destroy()
        else:
            messagebox.showerror("Error", msg, parent=p)

    tb.Button(btn_row, text="Cancel", bootstyle="secondary-outline", command=p.destroy).pack(side='left', padx=(0,8))
    tb.Button(btn_row, text="Add Product", bootstyle="success", command=do_add).pack(side='left')
    p.update_idletasks()
    p.minsize(420, p.winfo_reqheight() + 20)


def open_list_products():
    p = tb.Toplevel(app)
    p.title("Product List")
    p.transient(app)
    p.resizable(True, True)

    toolbar = ttk.Frame(p, padding=(10,8,10,4))
    toolbar.pack(fill='x')
    ttk.Label(toolbar, text="Search:", font=('Segoe UI', 11)).pack(side='left')
    s_var = StringVar()
    s_entry = ttk.Entry(toolbar, textvariable=s_var, width=24, font=('Segoe UI', 11))
    s_entry.pack(side='left', padx=6)
    tb.Button(toolbar, text="Search",   bootstyle="secondary",    command=lambda: load_p(s_var.get())).pack(side='left', padx=3)
    tb.Button(toolbar, text="Refresh",  bootstyle="info-outline", command=lambda: [s_var.set(''), load_p('')]).pack(side='left', padx=3)
    s_entry.bind('<KeyRelease>', lambda e: load_p(s_var.get()))
    ttk.Label(toolbar, text="Tip: Click a row then press Update or Delete", font=('Segoe UI', 9),
              foreground='gray').pack(side='right')

    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)

    btn_row = ttk.Frame(p, padding=(10,6,10,4)); btn_row.pack(fill='x')

    def update_selected():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Please click a row first.", parent=p)
            return
        vals = tree.item(sel[0])['values']
        pid, code, name, hsn, price, tax_rate, stock = vals

        up = tb.Toplevel(p)
        up.title(f"Update — {code}")
        up.transient(p)
        up.grab_set()
        up.resizable(False,False)
        ttk.Label(up, text=f"Update: {name}", font=('Segoe UI', 12, 'bold')).pack(pady=8, padx=14, anchor='w')
        ttk.Separator(up).pack(fill='x', padx=10)
        frm = ttk.Frame(up, padding=12)
        frm.pack(fill='x')
        frm.columnconfigure(1, weight=1)
        fields_u = [
            ("Name",          str(name)),
            ("HSN / SAC",     str(hsn)),
            ("Price (Rs)",    str(price)),
            ("Tax Rate (%)",  str(tax_rate).replace('%','')),
            ("Stock",         str(stock)),
        ]
        entries_u = []
        for i,(lb,val) in enumerate(fields_u):
            ttk.Label(frm, text=lb, font=('Segoe UI',11)).grid(row=i,column=0,sticky='e',padx=(0,10),pady=5)
            e = ttk.Entry(frm, font=('Segoe UI',11))
            e.grid(row=i,column=1,sticky='ew',pady=5)
            e.insert(0,val)
            entries_u.append(e)
        ttk.Separator(up).pack(fill='x',padx=10,pady=6)
        bf = ttk.Frame(up)
        bf.pack(pady=(0,10),padx=14,anchor='e')
        def do_update():
            try:
                update_product_db(pid, entries_u[0].get(), entries_u[1].get(),
                                  float(entries_u[2].get()), float(entries_u[3].get()),
                                  int(entries_u[4].get()))
                messagebox.showinfo("Updated","Product updated.", parent=up)
                up.destroy()
                load_p(s_var.get())
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=up)
        tb.Button(bf,text="Cancel",bootstyle="secondary-outline",command=up.destroy).pack(side='left',padx=(0,8))
        tb.Button(bf,text="Update",bootstyle="info",command=do_update).pack(side='left')
        up.update_idletasks()
        up.minsize(380, up.winfo_reqheight()+10)

    def delete_selected():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select","Please click a row first.", parent=p)
            return
        pid, code = tree.item(sel[0])['values'][:2]
        if messagebox.askyesno("Confirm Delete", f"Delete product '{code}'?", parent=p):
            delete_product_db(pid)
            load_p(s_var.get())

    tb.Button(btn_row, text="Edit Selected",   bootstyle="info",           command=update_selected).pack(side='left', padx=(0,6))
    tb.Button(btn_row, text="Delete Selected", bootstyle="danger-outline", command=delete_selected).pack(side='left')
    ttk.Label(btn_row, text="Select a row above, then click Edit or Delete",
              font=('Segoe UI',9), foreground='gray').pack(side='left', padx=12)

    tree_frame = ttk.Frame(p)
    tree_frame.pack(fill='both', expand=True, padx=10, pady=(0,10))
    cols = ("ID","Code","Name","HSN","Price (Rs)","Tax%","Stock")
    tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
    col_w = {"ID":50,"Code":80,"Name":340,"HSN":100,"Price (Rs)":100,"Tax%":60,"Stock":70}
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=col_w.get(c,90), anchor='center')
    vsb = ttk.Scrollbar(tree_frame, orient='vertical',   command=tree.yview)
    hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0,column=0,sticky='nsew')
    vsb.grid(row=0,column=1,sticky='ns')
    hsb.grid(row=1,column=0,sticky='ew')
    tree_frame.rowconfigure(0,weight=1)
    tree_frame.columnconfigure(0,weight=1)
    tree.tag_configure('even', background='#1e1e1e', foreground='#e0e0e0')
    tree.tag_configure('odd',  background='#2b2b2b', foreground='#e0e0e0')
    tree.bind('<Double-1>', lambda e: update_selected())

    def load_p(search=""):
        for r in tree.get_children():
            tree.delete(r)
        rows = find_products(search) if search else get_all_products()
        for i,row in enumerate(rows):
            pid,code,name,hsn,price,tax_rate,stock = row
            tag = 'even' if i%2==0 else 'odd'
            tree.insert('','end',values=(pid,code,name,hsn,f"{price:.2f}",f"{tax_rate:.0f}%",stock),tags=(tag,))

    load_p()
    p.update_idletasks()
    p.geometry("920x560")
    p.minsize(700, 400)


def open_stock_mgmt():
    p = tb.Toplevel(app)
    p.title("Stock Management")
    p.transient(app)
    p.resizable(True, True)

    toolbar = ttk.Frame(p, padding=(10,8,10,4))
    toolbar.pack(fill='x')
    ttk.Label(toolbar, text="Stock Overview", font=('Segoe UI',13,'bold')).pack(side='left')
    ttk.Label(toolbar, text="  |  Red = Low stock (<=5)   Green = OK",
              font=('Segoe UI',9), foreground='gray').pack(side='left')
    tb.Button(toolbar, text="Refresh", bootstyle="info-outline",
              command=lambda: [s_var.set(''), load_s('')]).pack(side='right')

    sf = ttk.Frame(p, padding=(10,4,10,4))
    sf.pack(fill='x')
    ttk.Label(sf, text="Search:", font=('Segoe UI',11)).pack(side='left')
    s_var = StringVar()
    s_entry = ttk.Entry(sf, textvariable=s_var, width=28, font=('Segoe UI',11))
    s_entry.pack(side='left', padx=6)
    s_entry.bind('<KeyRelease>', lambda e: load_s(s_var.get()))
    tb.Button(sf, text="Search", bootstyle="secondary", command=lambda: load_s(s_var.get())).pack(side='left', padx=3)

    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)

    bf = ttk.Frame(p, padding=(10,6,10,4))
    bf.pack(fill='x')

    def update_stock_popup():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select","Click a product row first.", parent=p)
            return
        code, name, hsn, price_s, tax_s, stock_s = tree.item(sel[0])['values']
        cursor.execute("SELECT id FROM products WHERE code=?", (str(code),))
        row = cursor.fetchone()
        if not row:
            return
        pid = row[0]
        up = tb.Toplevel(p)
        up.title(f"Update Stock — {code}")
        up.transient(p)
        up.grab_set()
        up.resizable(False,False)
        ttk.Label(up, text=f"{name}", font=('Segoe UI',12,'bold')).pack(pady=8,padx=14,anchor='w')
        ttk.Separator(up).pack(fill='x',padx=10)
        frm = ttk.Frame(up,padding=12)
        frm.pack(fill='x')
        frm.columnconfigure(1,weight=1)
        lbl_pairs = [("Price (Rs):",str(price_s)),("Tax Rate (%):",str(tax_s).replace('%','')),("Stock Qty:",str(stock_s))]
        ents = []
        for i,(lb,val) in enumerate(lbl_pairs):
            ttk.Label(frm,text=lb,font=('Segoe UI',11)).grid(row=i,column=0,sticky='e',padx=(0,10),pady=5)
            e = ttk.Entry(frm,font=('Segoe UI',11))
            e.grid(row=i,column=1,sticky='ew',pady=5)
            e.insert(0,val)
            ents.append(e)
        ttk.Separator(up).pack(fill='x',padx=10,pady=6)
        bff = ttk.Frame(up)
        bff.pack(pady=(0,10),padx=14,anchor='e')
        def do_update():
            try:
                update_product_db(pid, name, hsn, float(ents[0].get()), float(ents[1].get()), int(ents[2].get()))
                messagebox.showinfo("Updated","Stock updated.",parent=up)
                up.destroy()
                load_s(s_var.get())
            except Exception as ex:
                messagebox.showerror("Error",str(ex),parent=up)
        tb.Button(bff,text="Cancel",bootstyle="secondary-outline",command=up.destroy).pack(side='left',padx=(0,8))
        tb.Button(bff,text="Update Stock",bootstyle="success",command=do_update).pack(side='left')
        up.update_idletasks()
        up.minsize(340, up.winfo_reqheight()+10)

    tb.Button(bf, text="Update Selected Stock", bootstyle="warning", command=update_stock_popup).pack(side='left')
    ttk.Label(bf, text="  Click a row, then click Update", font=('Segoe UI',9), foreground='gray').pack(side='left')

    tree_frame = ttk.Frame(p)
    tree_frame.pack(fill='both', expand=True, padx=10, pady=(0,10))
    cols = ("Code","Product Name","HSN","Price (Rs)","Tax%","Stock")
    tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
    col_w = {"Code":90,"Product Name":380,"HSN":100,"Price (Rs)":110,"Tax%":60,"Stock":90}
    for c in cols:
        tree.heading(c,text=c)
        tree.column(c,width=col_w.get(c,100),anchor='center')
    vsb = ttk.Scrollbar(tree_frame, orient='vertical',   command=tree.yview)
    hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0,column=0,sticky='nsew')
    vsb.grid(row=0,column=1,sticky='ns')
    hsb.grid(row=1,column=0,sticky='ew')
    tree_frame.rowconfigure(0,weight=1)
    tree_frame.columnconfigure(0,weight=1)
    tree.tag_configure('low', foreground='#ff6b6b')
    tree.tag_configure('ok',  foreground='#51cf66')
    tree.bind('<Double-1>', lambda e: update_stock_popup())

    def load_s(search=""):
        for r in tree.get_children():
            tree.delete(r)
        rows = find_products(search) if search else get_all_products()
        for row in rows:
            pid,code,name,hsn,price,tax_rate,stock = row
            tag = 'low' if int(stock)<=5 else 'ok'
            tree.insert('','end',values=(code,name,hsn,f"{price:.2f}",f"{tax_rate:.0f}%",stock),tags=(tag,))

    load_s()
    p.update_idletasks()
    p.geometry("860x520")
    p.minsize(600,380)


def open_history():
    p = tb.Toplevel(app)
    p.title("Invoice History")
    p.transient(app)
    p.resizable(True, True)

    toolbar = ttk.Frame(p, padding=(10,8,10,4))
    toolbar.pack(fill='x')
    ttk.Label(toolbar, text="Invoice History", font=('Segoe UI',13,'bold')).pack(side='left', padx=(0,16))
    ttk.Label(toolbar, text="From:", font=('Segoe UI',11)).pack(side='left')
    f_e = ttk.Entry(toolbar, width=11, font=('Segoe UI',11))
    f_e.pack(side='left', padx=4)
    f_e.insert(0, (datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d"))
    ttk.Label(toolbar, text="To:", font=('Segoe UI',11)).pack(side='left')
    t_e = ttk.Entry(toolbar, width=11, font=('Segoe UI',11))
    t_e.pack(side='left', padx=4)
    t_e.insert(0, datetime.now().strftime("%Y-%m-%d"))
    tb.Button(toolbar, text="Filter",      bootstyle="secondary",    command=lambda: filter_range()).pack(side='left', padx=3)
    tb.Button(toolbar, text="Last 7 Days", bootstyle="info-outline", command=lambda: filter_range('week')).pack(side='left', padx=3)
    tb.Button(toolbar, text="Last 30 Days",bootstyle="info-outline", command=lambda: filter_range('month')).pack(side='left', padx=3)

    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)

    bf = ttk.Frame(p, padding=(10,6,10,4))
    bf.pack(fill='x')
    total_lbl = ttk.Label(bf, text="Total Sales: Rs 0.00", font=('Segoe UI',12,'bold'), foreground='#51cf66')
    total_lbl.pack(side='left', padx=(0,16))

    def open_pdf_selected():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select","Click a bill row first.", parent=p)
            return
        bid = tree.item(sel[0])['values'][0]
        _open_pdf(bid)

    def delete_selected():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select","Click a bill row first.", parent=p)
            return
        bid = tree.item(sel[0])['values'][0]
        if messagebox.askyesno("Confirm", f"Delete bill ID {bid}?", parent=p):
            cursor.execute("DELETE FROM bills WHERE id=?", (bid,))
            conn.commit()
            filter_range('month')

    def _open_pdf(bid):
        cursor.execute("SELECT invoice_no, date FROM bills WHERE id=?", (bid,))
        row = cursor.fetchone()
        if not row:
            return
        inv_no, date_str = row
        folder_path = get_invoice_folder(date_str)
        safe_inv_no = inv_no.replace('/','_').replace('\\', '_')
        fname = os.path.join(folder_path, f"Invoice_{safe_inv_no}.pdf")
        if not os.path.exists(fname):
            fname = generate_tax_invoice_pdf(bid)
        if fname and os.path.exists(fname):
            try:
                if os.name == 'nt':
                    os.startfile(os.path.abspath(fname))
                else:
                    webbrowser.open_new(os.path.abspath(fname))
            except:
                pass
        else:
            messagebox.showinfo("Not Found","Could not generate PDF.", parent=p)

    tb.Button(bf, text="Open / Print PDF",    bootstyle="primary",       command=open_pdf_selected).pack(side='left', padx=(0,6))
    tb.Button(bf, text="Delete Selected Bill", bootstyle="danger-outline", command=delete_selected).pack(side='left')
    ttk.Label(bf, text="  Click a row then Open PDF or Delete",
              font=('Segoe UI',9), foreground='gray').pack(side='left', padx=8)

    tree_frame = ttk.Frame(p)
    tree_frame.pack(fill='both', expand=True, padx=10, pady=(0,10))
    cols = ("ID","Invoice No","Date","Customer","Taxable","IGST","Total","Payment","Open PDF")
    tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
    col_w = {"ID":45,"Invoice No":100,"Date":95,"Customer":180,"Taxable":95,"IGST":75,"Total":95,"Payment":85,"Open PDF":80}
    for c in cols:
        tree.heading(c,text=c)
        tree.column(c,width=col_w.get(c,90),anchor='center')
    vsb = ttk.Scrollbar(tree_frame, orient='vertical',   command=tree.yview)
    hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0,column=0,sticky='nsew')
    vsb.grid(row=0,column=1,sticky='ns')
    hsb.grid(row=1,column=0,sticky='ew')
    tree_frame.rowconfigure(0,weight=1)
    tree_frame.columnconfigure(0,weight=1)
    tree.tag_configure('even', background='#1e1e1e', foreground='#e0e0e0')
    tree.tag_configure('odd',  background='#2b2b2b', foreground='#e0e0e0')
    tree.bind('<Double-1>', lambda e: open_pdf_selected())

    def filter_range(period=None):
        if period=='week':
            fr=(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
            to=datetime.now().strftime("%Y-%m-%d")
        elif period=='month':
            fr=(datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")
            to=datetime.now().strftime("%Y-%m-%d")
        else:
            fr=f_e.get().strip()
            to=t_e.get().strip()
        try:
            fr_dt=datetime.strptime(fr,"%Y-%m-%d")
            to_dt=datetime.strptime(to,"%Y-%m-%d")+timedelta(days=1)
        except:
            messagebox.showerror("Error","Date format must be YYYY-MM-DD",parent=p)
            return
        for r in tree.get_children():
            tree.delete(r)
        cursor.execute("""SELECT id,invoice_no,date,customer_name,taxable_amount,igst_amount,total,payment_method
            FROM bills WHERE date>=? AND date<? ORDER BY id DESC""",
            (fr_dt.strftime("%Y-%m-%d %H:%M:%S"), to_dt.strftime("%Y-%m-%d %H:%M:%S")))
        total_sum=0
        for i,rr in enumerate(cursor.fetchall()):
            bid,inv_no,date_s,cname,taxable,igst,tot,pm=rr
            total_sum+=tot
            tag='even' if i%2==0 else 'odd'
            cur=SHOP_CONFIG['currency']
            tree.insert('','end',values=(bid,inv_no,date_s[:10],cname or "Walk-in",
                f"{cur}{taxable:,.2f}",f"{cur}{igst:,.2f}",f"{cur}{tot:,.2f}",pm,"Open"),tags=(tag,))
        total_lbl.config(text=f"Total Sales: {SHOP_CONFIG['currency']}{total_sum:,.2f}")

    def on_tree_click(event):
        item=tree.identify('item',event.x,event.y)
        col=tree.identify('column',event.x,event.y)
        if not item or not col:
            return
        if int(col[1:])-1==8:
            bid=tree.item(item)['values'][0]
            _open_pdf(bid)

    tree.bind('<Button-1>', on_tree_click)
    filter_range('month')
    p.update_idletasks()
    p.geometry("1000x540")
    p.minsize(750,400)


def open_analytics():
    """Open Monthly Analytics Window"""
    p = tb.Toplevel(app)
    p.title("Monthly Analytics & Reports")
    p.transient(app)
    p.resizable(True, True)

    # Header
    hdr = ttk.Frame(p, padding=(10,8,10,4))
    hdr.pack(fill='x')
    ttk.Label(hdr, text="📊 Monthly Business Analytics", font=('Segoe UI',14,'bold')).pack(side='left')

    # Month/Year selector
    sel_frame = ttk.Frame(p, padding=(10,4,10,8))
    sel_frame.pack(fill='x')
    
    ttk.Label(sel_frame, text="Select Month:", font=('Segoe UI',11)).pack(side='left')
    
    month_var = IntVar(value=datetime.now().month)
    month_combo = ttk.Combobox(sel_frame, textvariable=month_var, 
                               values=list(range(1,13)), width=5, state='readonly')
    month_combo.pack(side='left', padx=4)
    
    ttk.Label(sel_frame, text="Year:", font=('Segoe UI',11)).pack(side='left', padx=(10,0))
    year_var = IntVar(value=datetime.now().year)
    year_combo = ttk.Combobox(sel_frame, textvariable=year_var,
                              values=list(range(2020, datetime.now().year + 2)), width=7, state='readonly')
    year_combo.pack(side='left', padx=4)
    
    def load_analysis():
        year = year_var.get()
        month = month_var.get()
        analysis = get_monthly_analysis(year, month)
        
        # Clear previous content
        for widget in content_frame.winfo_children():
            widget.destroy()
        
        cur = SHOP_CONFIG['currency']
        
        # Title
        ttk.Label(content_frame, text=f"Report for {analysis['month_name']} {year}",
                  font=('Segoe UI', 13, 'bold')).pack(pady=(0,10), anchor='w')
        
        # Sales Summary
        summary_lf = ttk.Labelframe(content_frame, text=" Sales Summary ", padding=10)
        summary_lf.pack(fill='x', pady=5)
        
        summary_data = [
            ("Total Bills:", f"{analysis['total_bills']}"),
            ("Total Sales:", f"{cur}{analysis['total_sales']:,.2f}"),
            ("Taxable Amount:", f"{cur}{analysis['total_taxable']:,.2f}"),
            ("Total GST Collected:", f"{cur}{analysis['total_gst']:,.2f}"),
            ("Discounts Given:", f"{cur}{analysis['total_discount']:,.2f}"),
        ]
        
        for i, (label, value) in enumerate(summary_data):
            row = ttk.Frame(summary_lf)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text=label, font=('Segoe UI', 10), width=20).pack(side='left')
            ttk.Label(row, text=value, font=('Segoe UI', 10, 'bold')).pack(side='left')
        
        # Payment Methods
        pm_lf = ttk.Labelframe(content_frame, text=" Payment Methods ", padding=10)
        pm_lf.pack(fill='x', pady=5)
        
        for pm, amount in analysis['payment_methods'].items():
            pct = (amount / analysis['total_sales'] * 100) if analysis['total_sales'] > 0 else 0
            row = ttk.Frame(pm_lf)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text=f"{pm}:", font=('Segoe UI', 10), width=15).pack(side='left')
            ttk.Label(row, text=f"{cur}{amount:,.2f} ({pct:.1f}%)", font=('Segoe UI', 10)).pack(side='left')
        
        # Stock Summary
        stock_lf = ttk.Labelframe(content_frame, text=" Inventory Status ", padding=10)
        stock_lf.pack(fill='x', pady=5)
        
        stock_data = [
            ("Total Products:", f"{analysis['total_products']}"),
            ("Total Stock Qty:", f"{analysis['total_stock_qty']} units"),
            ("Stock Value:", f"{cur}{analysis['stock_value']:,.2f}"),
        ]
        
        for label, value in stock_data:
            row = ttk.Frame(stock_lf)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text=label, font=('Segoe UI', 10), width=20).pack(side='left')
            ttk.Label(row, text=value, font=('Segoe UI', 10, 'bold')).pack(side='left')
        
        # Low Stock Alert
        if analysis['low_stock_products']:
            alert_lf = ttk.Labelframe(content_frame, text=" ⚠ Low Stock Alert ", padding=10)
            alert_lf.pack(fill='x', pady=5)
            
            for code, name, stock in analysis['low_stock_products'][:10]:
                row = ttk.Frame(alert_lf)
                row.pack(fill='x', pady=1)
                ttk.Label(row, text=f"{code} - {name}", font=('Segoe UI', 9), width=40).pack(side='left')
                ttk.Label(row, text=f"Stock: {stock}", font=('Segoe UI', 9, 'bold'), 
                         foreground='#ff6b6b').pack(side='left')
        
        # Top Products
        if analysis['top_products']:
            top_lf = ttk.Labelframe(content_frame, text=" Top 5 Products ", padding=10)
            top_lf.pack(fill='x', pady=5)
            
            for i, (pname, data) in enumerate(analysis['top_products'][:5], 1):
                row = ttk.Frame(top_lf)
                row.pack(fill='x', pady=1)
                ttk.Label(row, text=f"{i}. {pname[:30]}", font=('Segoe UI', 9), width=35).pack(side='left')
                ttk.Label(row, text=f"Qty: {data['qty']} | Rev: {cur}{data['revenue']:,.2f}",
                         font=('Segoe UI', 9)).pack(side='left')
    
    def generate_report():
        year = year_var.get()
        month = month_var.get()
        try:
            pdf_path = generate_monthly_report_pdf(year, month)
            if pdf_path and os.path.exists(pdf_path):
                ans = messagebox.askyesno("Report Generated",
                    f"Monthly report saved to:\n{pdf_path}\n\nOpen now?", parent=p)
                if ans:
                    if os.name == 'nt':
                        os.startfile(os.path.abspath(pdf_path))
                    else:
                        webbrowser.open_new(os.path.abspath(pdf_path))
                    
                    # Auto-upload monthly report to Google Drive
                    threading.Thread(
                        target=upload_monthly_report_to_drive,
                        args=(pdf_path, year, month),
                        daemon=True
                    ).start()
                else:
                    messagebox.showerror("Error", "Failed to generate report.", parent=p)
        except Exception as e:
            messagebox.showerror("Error", f"Report generation failed:\n{e}", parent=p)
    
    tb.Button(sel_frame, text="Load Analysis", bootstyle="info", command=load_analysis).pack(side='left', padx=10)
    tb.Button(sel_frame, text="📄 Generate PDF Report", bootstyle="success", command=generate_report).pack(side='left', padx=5)
    
    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)
    
    # Scrollable content area
    outer = ttk.Frame(p)
    outer.pack(fill='both', expand=True, padx=10, pady=6)
    
    canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
    content_frame = ttk.Frame(canvas, padding=10)
    
    content_window = canvas.create_window((0,0), window=content_frame, anchor='nw')
    canvas.configure(yscrollcommand=scrollbar.set)
    
    canvas.pack(side='left', fill='both', expand=True)
    scrollbar.pack(side='right', fill='y')
    
    def on_frame_configure(e):
        canvas.configure(scrollregion=canvas.bbox('all'))
    def on_canvas_configure(e):
        canvas.itemconfig(content_window, width=e.width)
    
    content_frame.bind('<Configure>', on_frame_configure)
    canvas.bind('<Configure>', on_canvas_configure)
    
    # Load current month by default
    load_analysis()
    
    p.update_idletasks()
    p.geometry("650x700")
    p.minsize(550, 500)


def open_billing():
    p = tb.Toplevel(app)
    p.title("New Bill — Select Products")
    p.transient(app)
    p.resizable(True, True)

    hdr = ttk.Frame(p, padding=(10,8,10,4))
    hdr.pack(fill='x')
    ttk.Label(hdr, text="Select Products to Add to Cart", font=('Segoe UI',13,'bold')).pack(side='left')
    ttk.Label(hdr, text="Double-click a row OR select + click Add to Cart",
              font=('Segoe UI',9), foreground='gray').pack(side='right')

    sf = ttk.Frame(p, padding=(10,4,10,4))
    sf.pack(fill='x')
    ttk.Label(sf, text="Search:", font=('Segoe UI',11)).pack(side='left')
    s_var = StringVar()
    s_entry = ttk.Entry(sf, textvariable=s_var, width=26, font=('Segoe UI',11))
    s_entry.pack(side='left', padx=6)
    s_entry.bind('<KeyRelease>', lambda e: load_p(s_var.get()))
    tb.Button(sf, text="Search",  bootstyle="secondary",    command=lambda: load_p(s_var.get())).pack(side='left', padx=3)
    tb.Button(sf, text="Show All",bootstyle="info-outline", command=lambda: [s_var.set(''), load_p('')]).pack(side='left', padx=3)

    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)

    bot = ttk.Frame(p, padding=(10,6,10,6))
    bot.pack(fill='x', side='bottom')
    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10, side='bottom')

    ttk.Label(bot, text="Qty:", font=('Segoe UI',11)).pack(side='left')
    qty_var = IntVar(value=1)
    qty_spin = tk.Spinbox(bot, from_=1, to=9999, textvariable=qty_var, width=7, font=('Segoe UI',12))
    qty_spin.pack(side='left', padx=(4,12))

    def add_to_cart():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select","Click a product row first.", parent=p)
            return
        code, name, hsn, price_str, tax_str, stock_str = tree.item(sel[0])['values']
        qty   = qty_var.get()
        stock = int(stock_str)
        price = float(str(price_str).replace(',',''))
        tax_rate = float(str(tax_str).replace('%',''))
        if qty > stock:
            messagebox.showwarning("Stock", f"Only {stock} available.", parent=p)
            return
        # Update qty if already in cart
        for ch in cart_tree.get_children():
            v = cart_tree.item(ch)['values']
            if str(v[1]) == str(code):
                new_qty = int(v[4]) + qty
                if new_qty > stock:
                    messagebox.showwarning("Stock", f"Only {stock} in stock.", parent=p)
                    return
                taxable = price * new_qty
                tax_amt = taxable * tax_rate / 100
                total   = taxable + tax_amt
                cart_tree.item(ch, values=(v[0],code,name,hsn,new_qty,
                    f"{price:.2f}",f"{taxable:.2f}",f"{tax_rate:.0f}%",f"{tax_amt:.2f}",f"{total:.2f}","X"))
                update_summary()
                messagebox.showinfo("Updated", f"'{name}' qty updated to {new_qty}.", parent=p)
                return
        # New item
        num = len(cart_tree.get_children()) + 1
        taxable = price * qty
        tax_amt = taxable * tax_rate / 100
        total   = taxable + tax_amt
        cart_tree.insert('','end', values=(num,code,name,hsn,qty,
            f"{price:.2f}",f"{taxable:.2f}",f"{tax_rate:.0f}%",f"{tax_amt:.2f}",f"{total:.2f}","X"))
        update_summary()
        messagebox.showinfo("Added", f"'{name}' x{qty} added to cart.", parent=p)

    tb.Button(bot, text="Add to Cart", bootstyle="primary",        command=add_to_cart).pack(side='left', padx=(0,8))
    tb.Button(bot, text="Done / Close",bootstyle="success-outline", command=p.destroy).pack(side='left')
    ttk.Label(bot, text="  After adding all items, close this window and click Save & Export PDF",
              font=('Segoe UI',9), foreground='gray').pack(side='left', padx=8)

    tree_frame = ttk.Frame(p)
    tree_frame.pack(fill='both', expand=True, padx=10, pady=4)
    cols = ("Code","Product Name","HSN","Price (Rs)","Tax%","Stock")
    tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
    col_w = {"Code":90,"Product Name":380,"HSN":100,"Price (Rs)":110,"Tax%":60,"Stock":80}
    for c in cols:
        tree.heading(c,text=c)
        tree.column(c,width=col_w.get(c,100),anchor='center')
    vsb = ttk.Scrollbar(tree_frame, orient='vertical',   command=tree.yview)
    hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0,column=0,sticky='nsew')
    vsb.grid(row=0,column=1,sticky='ns')
    hsb.grid(row=1,column=0,sticky='ew')
    tree_frame.rowconfigure(0,weight=1)
    tree_frame.columnconfigure(0,weight=1)
    tree.tag_configure('low', foreground='#ff9f43')
    tree.tag_configure('ok',  foreground='#e0e0e0')
    tree.bind('<Double-1>', lambda e: add_to_cart())

    def load_p(search=""):
        for r in tree.get_children():
            tree.delete(r)
        rows = find_products(search) if search else get_all_products()
        for row in rows:
            pid,code,name,hsn,price,tax_rate,stock = row
            tag='low' if int(stock)<=5 else 'ok'
            tree.insert('','end',values=(code,name,hsn,f"{price:.2f}",f"{tax_rate:.0f}%",stock),tags=(tag,))

    load_p()
    p.update_idletasks()
    p.geometry("860x540")
    p.minsize(640,420)


def open_settings():
    p = tb.Toplevel(app)
    p.title("Company Settings")
    p.transient(app)
    p.grab_set()
    p.resizable(True, True)

    ttk.Label(p, text="Company & Invoice Settings", font=('Segoe UI',13,'bold')).pack(pady=(12,4), padx=14, anchor='w')
    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10)

    outer = ttk.Frame(p)
    outer.pack(fill='both', expand=True, padx=10, pady=6)
    canvas_s = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas_s.yview)
    frm = ttk.Frame(canvas_s, padding=12)
    frm.columnconfigure(1, weight=1)
    frm_window = canvas_s.create_window((0,0), window=frm, anchor='nw')
    canvas_s.configure(yscrollcommand=scrollbar.set)
    canvas_s.pack(side='left', fill='both', expand=True)
    scrollbar.pack(side='right', fill='y')

    def on_frame_configure(e):
        canvas_s.configure(scrollregion=canvas_s.bbox('all'))
    def on_canvas_configure(e):
        canvas_s.itemconfig(frm_window, width=e.width)
    frm.bind('<Configure>', on_frame_configure)
    canvas_s.bind('<Configure>', on_canvas_configure)

    config_fields = [
        ("--- Company Info ---", None),
        ("Company Name",         "name"),
        ("GSTIN",                "gstin"),
        ("Address Line 1",       "address"),
        ("City / State / PIN",   "city"),
        ("Mobile",               "mobile"),
        ("Email",                "email"),
        ("--- Bank Details ---", None),
        ("Bank Name",            "bank_name"),
        ("Account No",           "account_no"),
        ("IFSC Code",            "ifsc"),
        ("Branch",               "branch"),
        ("UPI ID",               "upi"),
    ]
    entries = {}
    row_i = 0
    for label, key in config_fields:
        if key is None:
            ttk.Label(frm, text=label, font=('Segoe UI',10,'bold'), foreground='#888').grid(
                row=row_i, column=0, columnspan=2, sticky='w', pady=(10,2))
        else:
            ttk.Label(frm, text=label+":", font=('Segoe UI',11), width=20).grid(
                row=row_i, column=0, sticky='e', padx=(0,10), pady=4)
            e = ttk.Entry(frm, font=('Segoe UI',11))
            e.grid(row=row_i, column=1, sticky='ew', pady=4)
            e.insert(0, SHOP_CONFIG.get(key,''))
            entries[key] = e
        row_i += 1

    def do_save():
        for key,e in entries.items():
            SHOP_CONFIG[key] = e.get().strip()
        lbl_shop.config(text=f"🧾 {SHOP_CONFIG['name']}")
        gstin_lbl.config(text=f"GSTIN: {SHOP_CONFIG['gstin']}")
        messagebox.showinfo("Saved","Company settings updated.", parent=p)
        p.destroy()

    ttk.Separator(p, orient='horizontal').pack(fill='x', padx=10, pady=6)
    btn_row = ttk.Frame(p)
    btn_row.pack(pady=(0,12), padx=14, anchor='e')
    tb.Button(btn_row, text="Cancel",        bootstyle="secondary-outline", command=p.destroy).pack(side='left', padx=(0,8))
    tb.Button(btn_row, text="Save Settings", bootstyle="success",           command=do_save).pack(side='left')
    p.update_idletasks()
    p.geometry("560x560")
    p.minsize(440,400)


# ---- Wire nav ----
nav_buttons['billing'].config(command=open_billing)
nav_buttons['add'].config(command=open_add_product)
nav_buttons['list'].config(command=open_list_products)
nav_buttons['stock'].config(command=open_stock_mgmt)
nav_buttons['history'].config(command=open_history)
nav_buttons['analytics'].config(command=open_analytics)
nav_buttons['settings'].config(command=open_settings)
nav_buttons['exit'].config(command=app.destroy)

update_summary()
app.mainloop()