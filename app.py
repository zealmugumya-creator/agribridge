"""
AgriBridge - Uganda Farm-to-Table Platform
Flask Backend with SQLite Database
Render-ready production deployment
"""

import os
import sqlite3
import json
import random
import hashlib
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, g

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'agribridge-uganda-2026')

DB_PATH = os.environ.get('DB_PATH', 'agribridge.db')

# ─────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ─────────────────────────────────────────
# DATABASE INIT
# ─────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('farmer','vendor','buyer','hotel','admin')),
    district TEXT,
    language TEXT DEFAULT 'en',
    verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS farmers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    national_id TEXT,
    farm_name TEXT,
    district TEXT,
    gps_lat REAL,
    gps_lon REAL,
    farm_size_acres REAL,
    land_ownership TEXT,
    crops TEXT,
    monthly_output_kg REAL,
    mobile_money TEXT,
    mobile_money_number TEXT,
    experience_years INTEGER,
    storage_available INTEGER DEFAULT 0,
    irrigation INTEGER DEFAULT 0,
    challenges TEXT,
    credit_history TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    business_name TEXT,
    business_permit TEXT,
    market_name TEXT,
    stall_gps_lat REAL,
    stall_gps_lon REAL,
    district TEXT,
    product_categories TEXT,
    weekly_volume_kg REAL,
    buyer_types TEXT,
    delivery_radius_km REAL DEFAULT 20,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hotels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    hotel_name TEXT,
    stars INTEGER,
    district TEXT,
    weekly_produce_kg REAL,
    quality_grade TEXT DEFAULT 'A',
    delivery_days TEXT,
    preferred_crops TEXT,
    sla_required INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id INTEGER REFERENCES farmers(id) ON DELETE CASCADE,
    crop TEXT NOT NULL,
    quantity_kg REAL NOT NULL,
    price_per_kg REAL NOT NULL,
    quality_grade TEXT DEFAULT 'A',
    harvest_date TEXT,
    available_from TEXT,
    district TEXT,
    delivery_available INTEGER DEFAULT 0,
    image_url TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','sold','expired')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER REFERENCES listings(id),
    buyer_user_id INTEGER REFERENCES users(id),
    quantity_kg REAL NOT NULL,
    total_price REAL NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','confirmed','delivered','cancelled')),
    delivery_address TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crop TEXT NOT NULL,
    district TEXT NOT NULL,
    price_per_kg REAL NOT NULL,
    recorded_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS forward_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id INTEGER REFERENCES farmers(id),
    buyer_user_id INTEGER REFERENCES users(id),
    crop TEXT NOT NULL,
    quantity_kg REAL NOT NULL,
    locked_price REAL NOT NULL,
    delivery_date TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS training_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    content TEXT,
    video_url TEXT,
    duration_mins INTEGER,
    level TEXT DEFAULT 'beginner'
);

CREATE TABLE IF NOT EXISTS training_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    module_id INTEGER REFERENCES training_modules(id),
    completed INTEGER DEFAULT 0,
    progress_pct INTEGER DEFAULT 0,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS sms_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    message TEXT NOT NULL,
    direction TEXT CHECK(direction IN ('inbound','outbound')),
    status TEXT DEFAULT 'sent',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ussd_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    phone TEXT NOT NULL,
    state TEXT DEFAULT 'main',
    data TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT,
    message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id INTEGER REFERENCES farmers(id),
    vendor_user_id INTEGER REFERENCES users(id),
    score REAL,
    crops TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);
"""

SEED_DATA = """
INSERT OR IGNORE INTO users (id,name,phone,email,password_hash,role,district,verified) VALUES
(1,'Nakato Grace','+256701000001','nakato@agri.ug','5f4dcc3b5aa765d61d83','farmer','Kampala',1),
(2,'Ssemakula John','+256701000002','john@agri.ug','5f4dcc3b5aa765d61d83','farmer','Wakiso',1),
(3,'Apio Mary','+256701000003','apio@agri.ug','5f4dcc3b5aa765d61d83','farmer','Gulu',1),
(4,'Mukasa Peter','+256701000004','mukasa@agri.ug','5f4dcc3b5aa765d61d83','vendor','Kampala',1),
(5,'Namutebi Rose','+256701000005','rose@agri.ug','5f4dcc3b5aa765d61d83','hotel','Kampala',1);

INSERT OR IGNORE INTO farmers (id,user_id,farm_name,district,crops,monthly_output_kg,farm_size_acres,experience_years,storage_available,irrigation) VALUES
(1,1,'Grace Organic Farm','Kampala','Tomatoes,Matooke,Sukuma Wiki',1200,2.5,8,1,1),
(2,2,'Ssemakula Gardens','Wakiso','Carrots,Cabbages,Onions,Pepper',800,1.8,5,0,1),
(3,3,'Apio Family Farm','Gulu','Maize,Groundnuts,Sorghum,Cassava',2000,5.0,12,1,0);

INSERT OR IGNORE INTO vendors (id,user_id,business_name,market_name,district,product_categories,weekly_volume_kg) VALUES
(1,4,'Mukasa Fresh Produce','Nakasero Market','Kampala','Vegetables,Fruits,Grains',500);

INSERT OR IGNORE INTO hotels (id,user_id,hotel_name,stars,district,weekly_produce_kg,quality_grade) VALUES
(1,5,'Serena Gardens Hotel',5,'Kampala',300,'A');

INSERT OR IGNORE INTO listings (farmer_id,crop,quantity_kg,price_per_kg,quality_grade,harvest_date,district,delivery_available,image_url,status) VALUES
(1,'Tomatoes',500,1500,'A','2026-04-10','Kampala',1,'https://images.unsplash.com/photo-1594282486552-05b4d80fbb9f?w=400','active'),
(1,'Matooke',800,800,'A','2026-04-08','Kampala',1,'https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400','active'),
(2,'Carrots',300,1200,'B','2026-04-12','Wakiso',0,'https://images.unsplash.com/photo-1598170845058-32b9d6a5da37?w=400','active'),
(2,'Cabbages',400,600,'A','2026-04-09','Wakiso',1,'https://images.unsplash.com/photo-1557800636-894a64c1696f?w=400','active'),
(3,'Maize',1000,700,'A','2026-04-15','Gulu',1,'https://images.unsplash.com/photo-1601312186003-4e0c8f9c0e22?w=400','active'),
(3,'Groundnuts',500,4000,'A','2026-04-20','Gulu',0,'https://images.unsplash.com/photo-1567306226416-28f0efdc88ce?w=400','active');

INSERT OR IGNORE INTO price_history (crop,district,price_per_kg,recorded_at) VALUES
('Tomatoes','Kampala',1200,'2026-01-01'),('Tomatoes','Kampala',1400,'2026-02-01'),
('Tomatoes','Kampala',1100,'2026-02-15'),('Tomatoes','Kampala',1600,'2026-03-01'),
('Tomatoes','Kampala',1300,'2026-03-15'),('Tomatoes','Kampala',1500,'2026-04-01'),
('Matooke','Kampala',700,'2026-01-01'),('Matooke','Kampala',800,'2026-02-01'),
('Matooke','Kampala',750,'2026-03-01'),('Matooke','Kampala',800,'2026-04-01'),
('Carrots','Wakiso',1000,'2026-01-01'),('Carrots','Wakiso',1100,'2026-02-01'),
('Carrots','Wakiso',1300,'2026-03-01'),('Carrots','Wakiso',1200,'2026-04-01'),
('Maize','Gulu',600,'2026-01-01'),('Maize','Gulu',650,'2026-02-01'),
('Maize','Gulu',700,'2026-03-01'),('Maize','Gulu',700,'2026-04-01');

INSERT OR IGNORE INTO training_modules (id,title,category,description,video_url,duration_mins,level) VALUES
(1,'Modern Crop Farming Techniques','Crop Science','Learn soil preparation, seed selection, and pest control for high-yield farming.','https://www.youtube.com/embed/t5UNSWNpJEM',45,'beginner'),
(2,'Post-Harvest Storage & Quality','Post-Harvest','Prevent losses with proper storage, grading and packaging methods.','https://www.youtube.com/embed/aCYC3TFVqXk',30,'intermediate'),
(3,'Mobile Money & Digital Finance','Finance','Use MTN Mobile Money, Airtel Money and banking apps for your farm business.','https://www.youtube.com/embed/0aTNPQiVNOw',25,'beginner'),
(4,'Negotiation & Pricing Skills','Business','How to negotiate fair prices and protect yourself from market exploitation.','https://www.youtube.com/embed/ZKLnhuzh9uY',35,'intermediate'),
(5,'Smartphone Farming Tools','Digital Literacy','Use WhatsApp, weather apps and AgriBridge on your phone to grow your business.','https://www.youtube.com/embed/mI0_8aZYvFQ',20,'beginner');
"""

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.executescript(SEED_DATA)
    db.commit()
    db.close()
    print("✅ AgriBridge database initialised")

# Call init at import time so gunicorn picks it up
init_db()

# ─────────────────────────────────────────
# SERVE FRONTEND
# ─────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# ─────────────────────────────────────────
# AUTH ENDPOINTS
# ─────────────────────────────────────────

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json or {}
    required = ['name', 'phone', 'password', 'role']
    if not all(d.get(k) for k in required):
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    try:
        uid = execute(
            "INSERT INTO users (name,phone,email,password_hash,role,district,language) VALUES (?,?,?,?,?,?,?)",
            (d['name'], d['phone'], d.get('email',''), hash_password(d['password']),
             d['role'], d.get('district',''), d.get('language','en'))
        )
        # Create role-specific profile
        if d['role'] == 'farmer':
            execute(
                "INSERT INTO farmers (user_id,farm_name,district,crops,monthly_output_kg,farm_size_acres,experience_years,mobile_money,mobile_money_number,challenges) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, d.get('farm_name',''), d.get('district',''), d.get('crops',''),
                 d.get('monthly_output_kg', 0), d.get('farm_size_acres', 0),
                 d.get('experience_years', 0), d.get('mobile_money','MTN'),
                 d.get('mobile_money_number',''), d.get('challenges',''))
            )
        elif d['role'] == 'vendor':
            execute(
                "INSERT INTO vendors (user_id,business_name,market_name,district,product_categories,weekly_volume_kg) VALUES (?,?,?,?,?,?)",
                (uid, d.get('business_name',''), d.get('market_name',''),
                 d.get('district',''), d.get('product_categories',''), d.get('weekly_volume_kg', 0))
            )
        elif d['role'] == 'hotel':
            execute(
                "INSERT INTO hotels (user_id,hotel_name,stars,district,weekly_produce_kg,quality_grade,preferred_crops) VALUES (?,?,?,?,?,?,?)",
                (uid, d.get('hotel_name',''), d.get('stars', 3),
                 d.get('district',''), d.get('weekly_produce_kg', 0),
                 d.get('quality_grade','A'), d.get('preferred_crops',''))
            )
        return jsonify({'success': True, 'user_id': uid, 'role': d['role']})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Phone number already registered'}), 409

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    user = query(
        "SELECT * FROM users WHERE phone=? AND password_hash=?",
        (d.get('phone',''), hash_password(d.get('password',''))),
        one=True
    )
    if not user:
        return jsonify({'success': False, 'error': 'Invalid phone or password'}), 401
    return jsonify({'success': True, 'user': dict(user)})

# ─────────────────────────────────────────
# LISTINGS / MARKETPLACE
# ─────────────────────────────────────────

@app.route('/api/listings')
def get_listings():
    crop = request.args.get('crop', '')
    district = request.args.get('district', '')
    grade = request.args.get('grade', '')
    sql = """
        SELECT l.*, u.name as farmer_name, u.phone as farmer_phone, f.farm_name, f.gps_lat, f.gps_lon
        FROM listings l
        JOIN farmers f ON l.farmer_id = f.id
        JOIN users u ON f.user_id = u.id
        WHERE l.status = 'active'
    """
    args = []
    if crop:
        sql += " AND lower(l.crop) LIKE ?"
        args.append(f'%{crop.lower()}%')
    if district:
        sql += " AND lower(l.district) LIKE ?"
        args.append(f'%{district.lower()}%')
    if grade:
        sql += " AND l.quality_grade = ?"
        args.append(grade)
    sql += " ORDER BY l.created_at DESC LIMIT 50"
    return jsonify({'success': True, 'listings': rows_to_list(query(sql, args))})

@app.route('/api/listings', methods=['POST'])
def create_listing():
    d = request.json or {}
    lid = execute(
        "INSERT INTO listings (farmer_id,crop,quantity_kg,price_per_kg,quality_grade,harvest_date,district,delivery_available,image_url) VALUES (?,?,?,?,?,?,?,?,?)",
        (d.get('farmer_id'), d.get('crop'), d.get('quantity_kg'), d.get('price_per_kg'),
         d.get('quality_grade','A'), d.get('harvest_date',''), d.get('district',''),
         d.get('delivery_available', 0), d.get('image_url',''))
    )
    return jsonify({'success': True, 'listing_id': lid})

# ─────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────

@app.route('/api/orders', methods=['POST'])
def create_order():
    d = request.json or {}
    listing = query("SELECT * FROM listings WHERE id=?", (d.get('listing_id'),), one=True)
    if not listing:
        return jsonify({'success': False, 'error': 'Listing not found'}), 404
    qty = float(d.get('quantity_kg', 0))
    total = qty * float(listing['price_per_kg'])
    commission = total * 0.04   # 4% platform commission
    oid = execute(
        "INSERT INTO orders (listing_id,buyer_user_id,quantity_kg,total_price,delivery_address,notes) VALUES (?,?,?,?,?,?)",
        (d.get('listing_id'), d.get('buyer_user_id'), qty, total, d.get('delivery_address',''), d.get('notes',''))
    )
    return jsonify({'success': True, 'order_id': oid, 'total': total, 'commission': commission})

@app.route('/api/orders/<int:user_id>')
def get_orders(user_id):
    orders = query("""
        SELECT o.*, l.crop, l.price_per_kg, l.district
        FROM orders o JOIN listings l ON o.listing_id = l.id
        WHERE o.buyer_user_id = ?
        ORDER BY o.created_at DESC
    """, (user_id,))
    return jsonify({'success': True, 'orders': rows_to_list(orders)})

# ─────────────────────────────────────────
# PRICE DATA
# ─────────────────────────────────────────

@app.route('/api/prices')
def get_prices():
    rows = query("""
        SELECT crop, district, AVG(price_per_kg) as avg_price, MAX(price_per_kg) as max_price,
               MIN(price_per_kg) as min_price, COUNT(*) as data_points,
               MAX(recorded_at) as last_updated
        FROM price_history
        GROUP BY crop, district
        ORDER BY crop, district
    """)
    return jsonify({'success': True, 'prices': rows_to_list(rows)})

@app.route('/api/prices/trend')
def price_trend():
    crop = request.args.get('crop', 'Tomatoes')
    district = request.args.get('district', 'Kampala')
    rows = query(
        "SELECT price_per_kg, recorded_at FROM price_history WHERE crop=? AND district=? ORDER BY recorded_at",
        (crop, district)
    )
    return jsonify({'success': True, 'trend': rows_to_list(rows)})

@app.route('/api/prices/record', methods=['POST'])
def record_price():
    d = request.json or {}
    execute(
        "INSERT INTO price_history (crop,district,price_per_kg) VALUES (?,?,?)",
        (d.get('crop'), d.get('district'), d.get('price'))
    )
    return jsonify({'success': True})

# ─────────────────────────────────────────
# MATCHING ENGINE
# ─────────────────────────────────────────

@app.route('/api/match')
def match_farmers():
    crop = request.args.get('crop', '')
    district = request.args.get('district', '')
    volume = float(request.args.get('volume', 0))
    grade = request.args.get('grade', 'A')

    farmers = query("""
        SELECT f.*, u.name, u.phone, u.district as user_district
        FROM farmers f JOIN users u ON f.user_id = u.id
        WHERE u.verified = 1
    """)

    results = []
    for f in farmers:
        score = 0
        farmer_crops = [c.strip().lower() for c in (f['crops'] or '').split(',')]
        if crop.lower() in farmer_crops:
            score += 40
        if district and (f['user_district'] or '').lower() == district.lower():
            score += 20
        elif district and district.lower() in (f['user_district'] or '').lower():
            score += 10
        if f['monthly_output_kg'] and float(f['monthly_output_kg']) >= volume:
            score += 20
        if f['storage_available']:
            score += 10
        if f['irrigation']:
            score += 10
        if score > 20:
            r = dict(f)
            r['match_score'] = score
            results.append(r)

    results.sort(key=lambda x: x['match_score'], reverse=True)
    return jsonify({'success': True, 'matches': results[:10]})

# ─────────────────────────────────────────
# FARMERS DIRECTORY
# ─────────────────────────────────────────

@app.route('/api/farmers')
def get_farmers():
    rows = query("""
        SELECT f.*, u.name, u.phone, u.district as user_district, u.verified
        FROM farmers f JOIN users u ON f.user_id = u.id
        ORDER BY u.name
    """)
    return jsonify({'success': True, 'farmers': rows_to_list(rows)})

# ─────────────────────────────────────────
# TRAINING
# ─────────────────────────────────────────

@app.route('/api/training')
def get_training():
    cat = request.args.get('category', '')
    if cat:
        rows = query("SELECT * FROM training_modules WHERE category=? ORDER BY id", (cat,))
    else:
        rows = query("SELECT * FROM training_modules ORDER BY id")
    return jsonify({'success': True, 'modules': rows_to_list(rows)})

@app.route('/api/training/progress', methods=['POST'])
def update_progress():
    d = request.json or {}
    existing = query(
        "SELECT id FROM training_progress WHERE user_id=? AND module_id=?",
        (d.get('user_id'), d.get('module_id')), one=True
    )
    if existing:
        execute(
            "UPDATE training_progress SET progress_pct=?, completed=?, completed_at=? WHERE id=?",
            (d.get('progress_pct', 0), d.get('completed', 0),
             datetime.now().isoformat() if d.get('completed') else None, existing['id'])
        )
    else:
        execute(
            "INSERT INTO training_progress (user_id,module_id,progress_pct,completed) VALUES (?,?,?,?)",
            (d.get('user_id'), d.get('module_id'), d.get('progress_pct', 0), d.get('completed', 0))
        )
    return jsonify({'success': True})

# ─────────────────────────────────────────
# USSD SIMULATOR
# ─────────────────────────────────────────

USSD_MENU = {
    'main': "CON Welcome to AgriBridge *789#\n1. Check Prices\n2. List My Produce\n3. Find Buyers\n4. Training\n5. Weather\n6. My Account",
}

@app.route('/api/ussd', methods=['POST'])
def ussd():
    d = request.json or {}
    text = d.get('text', '').strip()
    phone = d.get('phoneNumber', '+256700000000')
    session_id = d.get('sessionId', 'demo')

    parts = [p for p in text.split('*') if p] if text else []
    level = len(parts)

    if level == 0:
        return jsonify({'response': USSD_MENU['main']})

    choice = parts[0]

    if choice == '1':  # Prices
        if level == 1:
            return jsonify({'response': "CON Select crop:\n1. Tomatoes\n2. Matooke\n3. Maize\n4. Carrots\n5. Onions"})
        crops = ['Tomatoes','Matooke','Maize','Carrots','Onions']
        idx = int(parts[1]) - 1
        crop = crops[idx] if 0 <= idx < len(crops) else 'Tomatoes'
        row = query("SELECT AVG(price_per_kg) as avg FROM price_history WHERE crop=?", (crop,), one=True)
        avg = round(row['avg']) if row and row['avg'] else 1200
        return jsonify({'response': f"END {crop} price today:\nAvg: UGX {avg:,}/kg\nSource: AgriBridge Market\nSend PRICES to 8204 for daily updates"})

    elif choice == '2':  # List produce
        if level == 1:
            return jsonify({'response': "CON List your produce:\nEnter crop name (e.g. Tomatoes)"})
        if level == 2:
            return jsonify({'response': f"CON Crop: {parts[1]}\nEnter quantity in kg:"})
        if level == 3:
            return jsonify({'response': f"CON {parts[2]}kg of {parts[1]}\nEnter your price per kg (UGX):"})
        if level >= 4:
            execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
                    (phone, f"USSD listing: {parts[1]} {parts[2]}kg @{parts[3]}/kg", 'inbound'))
            return jsonify({'response': f"END ✅ Listed!\n{parts[2]}kg of {parts[1]}\n@ UGX {parts[3]}/kg\nBuyers will contact you shortly.\nRef#: AB{random.randint(10000,99999)}"})

    elif choice == '3':  # Find buyers
        farmers = query("SELECT COUNT(*) as cnt FROM farmers", one=True)
        return jsonify({'response': f"END Active buyers in your area:\n• Nakasero Market Vendors: 24\n• Hotel Buyers: 8\n• Exporters: 3\n\nTotal farmers connected: {farmers['cnt']}\nCall 0800-100-789 to connect"})

    elif choice == '4':  # Training
        if level == 1:
            return jsonify({'response': "CON Training topics:\n1. Crop Science\n2. Post-Harvest\n3. Finance\n4. Business Skills\n5. Digital Tools"})
        cats = ['Crop Science','Post-Harvest','Finance','Business Skills','Digital Tools']
        idx = int(parts[1]) - 1
        cat = cats[idx] if 0 <= idx < len(cats) else 'Crop Science'
        return jsonify({'response': f"END {cat} Training:\nSend LEARN {cat[:4].upper()} to 8204\nOr call village agent:\n0800-100-789 (free)\nNext session: Saturday 9am"})

    elif choice == '5':  # Weather
        return jsonify({'response': "END 🌤 Weather Forecast - Kampala:\nToday: Partly cloudy, 26°C\nTomorrow: Light rain, 23°C\nSat: Sunny, 28°C\n\nSend WEATHER to 8204 daily\nSource: Uganda Met Authority"})

    elif choice == '6':  # My Account
        user = query("SELECT * FROM users WHERE phone=?", (phone,), one=True)
        if user:
            return jsonify({'response': f"END My Account:\nName: {user['name']}\nRole: {user['role'].title()}\nDistrict: {user['district']}\nStatus: {'✅ Verified' if user['verified'] else '⏳ Pending'}"})
        return jsonify({'response': "END Account not found.\nDial *789# and register\nor call 0800-100-789"})

    return jsonify({'response': "END Invalid option. Dial *789# to start again."})

# ─────────────────────────────────────────
# SMS HANDLER
# ─────────────────────────────────────────

SMS_HELP = {
    'PRICES': "Today's prices: Tomatoes UGX 1,500/kg | Matooke UGX 800/kg | Maize UGX 700/kg | Carrots UGX 1,200/kg. Reply PRICES <crop> for details.",
    'WEATHER': "Kampala: 26°C partly cloudy. Rain expected Fri-Sat. Good for harvest Mon-Thu. Source: Uganda Met Authority.",
    'JOIN': "Welcome to AgriBridge! To register as a farmer reply: FARMER <name> <district> <crop>. For vendor: VENDOR <name> <market>. Free: 0800-100-789",
    'ORDERS': "Your recent orders: No pending orders. To buy produce reply BUY <crop> <kg> <district>. We connect you with local farmers.",
    'HELP': "AgriBridge SMS commands:\nPRICES - Market prices\nWEATHER - Forecast\nJOIN - Register\nORDERS - My orders\nCall 0800-100-789 (free)",
    'LIST': "To list your produce: LIST <crop> <kg> <price per kg>\nExample: LIST Tomatoes 100 1500\nWe will find buyers for you within 24hrs.",
}

@app.route('/api/sms', methods=['POST'])
def handle_sms():
    d = request.json or {}
    msg = (d.get('message', '') or '').strip().upper()
    phone = d.get('phone', '')
    keyword = msg.split()[0] if msg else ''
    reply = SMS_HELP.get(keyword, SMS_HELP['HELP'])

    # Handle dynamic commands
    if keyword == 'LIST' and len(msg.split()) >= 4:
        parts = msg.split()
        crop, qty, price = parts[1], parts[2], parts[3]
        execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
                (phone, f"SMS listing: {crop} {qty}kg @{price}", 'inbound'))
        reply = f"✅ Listed! {qty}kg of {crop} @ UGX {price}/kg. Buyers will contact you. Ref#AB{random.randint(10000,99999)}"

    execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
            (phone, reply, 'outbound'))
    return jsonify({'success': True, 'reply': reply})

# ─────────────────────────────────────────
# FORWARD CONTRACTS
# ─────────────────────────────────────────

@app.route('/api/contracts', methods=['POST'])
def create_contract():
    d = request.json or {}
    cid = execute(
        "INSERT INTO forward_contracts (farmer_id,buyer_user_id,crop,quantity_kg,locked_price,delivery_date) VALUES (?,?,?,?,?,?)",
        (d.get('farmer_id'), d.get('buyer_user_id'), d.get('crop'),
         d.get('quantity_kg'), d.get('locked_price'), d.get('delivery_date'))
    )
    return jsonify({'success': True, 'contract_id': cid})

@app.route('/api/contracts/<int:user_id>')
def get_contracts(user_id):
    rows = query(
        "SELECT * FROM forward_contracts WHERE buyer_user_id=? OR farmer_id IN (SELECT id FROM farmers WHERE user_id=?) ORDER BY created_at DESC",
        (user_id, user_id)
    )
    return jsonify({'success': True, 'contracts': rows_to_list(rows)})

# ─────────────────────────────────────────
# CONTACT FORM
# ─────────────────────────────────────────

@app.route('/api/contact', methods=['POST'])
def contact():
    d = request.json or {}
    execute(
        "INSERT INTO contacts (name,email,message) VALUES (?,?,?)",
        (d.get('name',''), d.get('email',''), d.get('message',''))
    )
    return jsonify({'success': True, 'message': 'Your message has been received. We will respond within 24 hours.'})

# ─────────────────────────────────────────
# DASHBOARD STATS
# ─────────────────────────────────────────

@app.route('/api/stats')
def stats():
    farmers_count = query("SELECT COUNT(*) as c FROM farmers", one=True)['c']
    vendors_count = query("SELECT COUNT(*) as c FROM vendors", one=True)['c']
    listings_count = query("SELECT COUNT(*) as c FROM listings WHERE status='active'", one=True)['c']
    orders_count = query("SELECT COUNT(*) as c FROM orders", one=True)['c']
    total_kg = query("SELECT SUM(quantity_kg) as s FROM listings WHERE status='active'", one=True)['s'] or 0
    return jsonify({
        'success': True,
        'stats': {
            'farmers': farmers_count,
            'vendors': vendors_count,
            'active_listings': listings_count,
            'orders': orders_count,
            'total_produce_kg': total_kg,
            'districts_covered': 135,
            'revenue_generated': 'UGX 847M',
        }
    })

# ─────────────────────────────────────────
# DISTRICTS
# ─────────────────────────────────────────

@app.route('/api/districts')
def districts():
    data = [
        {"name": "Kampala", "farmers": 342, "produce": "Vegetables, Matooke", "lat": 0.3476, "lon": 32.5825},
        {"name": "Wakiso", "farmers": 521, "produce": "Carrots, Cabbages, Onions", "lat": 0.4042, "lon": 32.4597},
        {"name": "Mukono", "farmers": 287, "produce": "Tomatoes, Pepper, Fruits", "lat": 0.3535, "lon": 32.7553},
        {"name": "Gulu", "farmers": 412, "produce": "Maize, Groundnuts, Sorghum", "lat": 2.7745, "lon": 32.2990},
        {"name": "Mbarara", "farmers": 356, "produce": "Maize, Beans, Sorghum", "lat": -0.6072, "lon": 30.6545},
        {"name": "Jinja", "farmers": 198, "produce": "Sugarcane, Maize, Vegetables", "lat": 0.4244, "lon": 33.2042},
        {"name": "Mbale", "farmers": 267, "produce": "Coffee, Maize, Vegetables", "lat": 1.0796, "lon": 34.1751},
        {"name": "Lira", "farmers": 389, "produce": "Cotton, Maize, Sunflower", "lat": 2.2449, "lon": 32.8997},
    ]
    return jsonify({'success': True, 'districts': data})

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'platform': 'AgriBridge Uganda', 'version': '2.0'})

# ─────────────────────────────────────────
# RUN
# ─────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
