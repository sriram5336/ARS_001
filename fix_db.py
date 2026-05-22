import sqlite3
import os

DB_FILE = 'smartbilling.db'

print('Fixing smartbilling.db schema...')

# Backup
os.system('copy smartbilling.db smartbilling.db.bak' if os.name == 'nt' else 'cp smartbilling.db smartbilling.db.bak')

conn = sqlite3.connect(DB_FILE)
c = conn.cursor()

# Check current bills table
c.execute("PRAGMA table_info(bills)")
schema = c.fetchall()
print('Current bills schema:')
for col in schema:
    print('  ', col)

# Drop and recreate bills table
c.execute('DROP TABLE IF EXISTS bills')
c.execute('''
CREATE TABLE bills (
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
)
''')
conn.commit()

c.execute("PRAGMA table_info(bills)")
print('\\nNEW bills schema:')
for col in c.fetchall():
    print('  ', col)

print('\\n✅ Bills table recreated successfully!')
print('Run: python file.py')

conn.close()

