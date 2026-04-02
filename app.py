"""
AgriBridge - Uganda Farm-to-Table Intelligence Platform
Complete Cloud Backend — Flask + SQLite
Includes: Auth, Marketplace, Orders, Delivery Tracking,
          Matching Engine, Training, USSD, SMS, Contracts, Search
Contact: +256 755 966 690
"""

import os
import sqlite3
import json
import random
import hashlib
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, g

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'agribridge-uganda-2026')

DB_PATH = os.environ.get('DB_PATH', 'agribridge.db')
CONTACT_PHONE = '+256755966690'
CONTACT_DISPLAY = '0755 966 690'
TOLLFREE = '0800-100-789'

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

def gen_ref(prefix='AB'):
    return f"{prefix}-2026-{''.join(random.choices(string.digits, k=5))}"

# ─────────────────────────────────────────
# SCHEMA
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
    profile_pct INTEGER DEFAULT 60,
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
    ref TEXT UNIQUE,
    listing_id INTEGER REFERENCES listings(id),
    buyer_user_id INTEGER REFERENCES users(id),
    farmer_user_id INTEGER REFERENCES users(id),
    quantity_kg REAL NOT NULL,
    total_price REAL NOT NULL,
    commission REAL DEFAULT 0,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','confirmed','packed','collected','in_transit','delivered','cancelled')),
    delivery_address TEXT,
    delivery_district TEXT,
    notes TEXT,
    payment_method TEXT DEFAULT 'mobile_money',
    payment_status TEXT DEFAULT 'unpaid' CHECK(payment_status IN ('unpaid','paid','refunded')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE,
    order_id INTEGER REFERENCES orders(id),
    driver_name TEXT,
    driver_phone TEXT,
    vehicle_plate TEXT,
    pickup_location TEXT,
    pickup_district TEXT,
    dropoff_location TEXT,
    dropoff_district TEXT,
    distance_km REAL,
    status TEXT DEFAULT 'assigned' CHECK(status IN ('assigned','loading','picked_up','in_transit','at_destination','delivered','failed')),
    current_lat REAL,
    current_lon REAL,
    current_location TEXT,
    eta TEXT,
    started_at TEXT,
    delivered_at TEXT,
    delivery_photo TEXT,
    recipient_name TEXT,
    recipient_signature TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS delivery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER REFERENCES deliveries(id),
    event_type TEXT NOT NULL,
    description TEXT,
    location TEXT,
    lat REAL,
    lon REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS delivery_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER REFERENCES deliveries(id),
    user_id INTEGER REFERENCES users(id),
    alert_type TEXT CHECK(alert_type IN ('info','warning','success','error')),
    title TEXT,
    message TEXT,
    read INTEGER DEFAULT 0,
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
    ref TEXT UNIQUE,
    farmer_id INTEGER REFERENCES farmers(id),
    buyer_user_id INTEGER REFERENCES users(id),
    crop TEXT NOT NULL,
    quantity_kg REAL NOT NULL,
    locked_price REAL NOT NULL,
    delivery_date TEXT NOT NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','fulfilled','cancelled')),
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
    phone TEXT,
    subject TEXT,
    message TEXT,
    status TEXT DEFAULT 'new',
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

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    type TEXT DEFAULT 'info',
    read INTEGER DEFAULT 0,
    link TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

SEED_DATA = """
INSERT OR IGNORE INTO users (id,name,phone,email,password_hash,role,district,verified,profile_pct) VALUES
(1,'Nakato Grace','+256701000001','nakato@agri.ug','5f4dcc3b5aa765d61d8300a80c12a0b03702b5dc','farmer','Kampala',1,90),
(2,'Ssemakula John','+256701000002','john@agri.ug','5f4dcc3b5aa765d61d8300a80c12a0b03702b5dc','farmer','Wakiso',1,85),
(3,'Apio Mary','+256701000003','apio@agri.ug','5f4dcc3b5aa765d61d8300a80c12a0b03702b5dc','farmer','Gulu',1,80),
(4,'Mukasa Peter','+256701000004','mukasa@agri.ug','5f4dcc3b5aa765d61d8300a80c12a0b03702b5dc','vendor','Kampala',1,75),
(5,'Namutebi Rose','+256701000005','rose@agri.ug','5f4dcc3b5aa765d61d8300a80c12a0b03702b5dc','hotel','Kampala',1,80),
(6,'Okello James','+256701000006','okello@agri.ug','5f4dcc3b5aa765d61d8300a80c12a0b03702b5dc','buyer','Jinja',1,70);

INSERT OR IGNORE INTO farmers (id,user_id,farm_name,district,crops,monthly_output_kg,farm_size_acres,experience_years,storage_available,irrigation,gps_lat,gps_lon) VALUES
(1,1,'Grace Organic Farm','Kampala','Tomatoes,Matooke,Sukuma Wiki',1200,2.5,8,1,1,0.3476,32.5825),
(2,2,'Ssemakula Gardens','Wakiso','Carrots,Cabbages,Onions,Pepper',800,1.8,5,0,1,0.4042,32.4597),
(3,3,'Apio Family Farm','Gulu','Maize,Groundnuts,Sorghum,Cassava',2000,5.0,12,1,0,2.7745,32.2990);

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

INSERT OR IGNORE INTO orders (id,ref,listing_id,buyer_user_id,farmer_user_id,quantity_kg,total_price,commission,status,delivery_address,delivery_district,payment_status) VALUES
(1,'AB-2026-00101',1,4,1,200,300000,12000,'delivered','Nakasero Market, Kampala','Kampala','paid'),
(2,'AB-2026-00102',2,5,1,300,240000,9600,'in_transit','Serena Hotel, Kampala','Kampala','paid'),
(3,'AB-2026-00103',5,6,3,500,350000,14000,'confirmed','Northern Depot, Gulu','Gulu','paid'),
(4,'AB-2026-00104',3,4,2,150,180000,7200,'pending','Owino Market, Kampala','Kampala','unpaid');

INSERT OR IGNORE INTO deliveries (id,ref,order_id,driver_name,driver_phone,vehicle_plate,pickup_location,pickup_district,dropoff_location,dropoff_district,distance_km,status,current_location,eta) VALUES
(1,'DEL-2026-001',1,'Nalubega Ruth','+256702345678','UAX 123B','Grace Organic Farm, Kampala','Kampala','Nakasero Market','Kampala',8.5,'delivered','Nakasero Market','Delivered 9:50 AM'),
(2,'DEL-2026-002',2,'Okello David','+256703456789','UBE 456C','Grace Farm, Kampala','Kampala','Serena Hotel, Kampala','Kampala',12.0,'in_transit','Kampala Road, 5km away','Today 3:00 PM'),
(3,'DEL-2026-003',3,'Ongom Patrick','+256704567890','UCF 789D','Apio Family Farm, Gulu','Gulu','Northern Depot, Gulu','Gulu',25.0,'picked_up','Gulu Highway','Today 5:30 PM');

INSERT OR IGNORE INTO delivery_events (delivery_id,event_type,description,location,created_at) VALUES
(1,'order_confirmed','Order confirmed by farmer','Grace Farm, Kampala','2026-04-02 07:00:00'),
(1,'loading','Produce loaded and weighed — 300kg Matooke','Grace Farm, Kampala','2026-04-02 07:30:00'),
(1,'picked_up','Driver Ruth departed farm','Grace Farm gate','2026-04-02 08:00:00'),
(1,'in_transit','In transit — 8km to Serena Hotel','Kampala Road','2026-04-02 08:30:00'),
(1,'delivered','Delivered and signed by hotel manager','Serena Hotel, Kampala','2026-04-02 09:50:00'),
(2,'order_confirmed','Order confirmed','Grace Farm, Kampala','2026-04-02 07:00:00'),
(2,'loading','500kg Tomatoes graded and packed','Grace Farm, Kampala','2026-04-02 08:00:00'),
(2,'picked_up','Driver David departed','Grace Farm gate','2026-04-02 08:45:00'),
(2,'in_transit','In transit — 5km from Nakasero','Kampala Road','2026-04-02 11:00:00'),
(3,'order_confirmed','Order confirmed by farmer','Apio Farm, Gulu','2026-04-02 06:00:00'),
(3,'loading','1000kg Maize bagged and loaded','Apio Farm, Gulu','2026-04-02 07:00:00'),
(3,'picked_up','Driver Patrick departed Gulu','Gulu Main Road','2026-04-02 07:30:00');

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

INSERT OR IGNORE INTO forward_contracts (ref,farmer_id,buyer_user_id,crop,quantity_kg,locked_price,delivery_date,status) VALUES
('FC-2026-001',1,4,'Tomatoes',500,1400,'2026-05-01','active'),
('FC-2026-002',3,6,'Maize',1000,680,'2026-05-15','active');
"""

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.executescript(SEED_DATA)
    db.commit()
    db.close()
    print("✅ AgriBridge database initialised")

init_db()

# ─────────────────────────────────────────
# CORS HEADERS (allow frontend to call API)
# ─────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,PATCH,DELETE,OPTIONS'
    return response

@app.route('/api/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    return '', 200

# ─────────────────────────────────────────
# SERVE FRONTEND
# ─────────────────────────────────────────

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml'), 200, {'Content-Type': 'application/xml'}

@app.route('/robots.txt')
def robots():
    return send_from_directory('static', 'robots.txt'), 200, {'Content-Type': 'text/plain'}

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json or {}
    required = ['name', 'phone', 'password', 'role']
    if not all(d.get(k) for k in required):
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    phone = d['phone'].replace(' ', '').replace('-', '')
    if not phone.startswith('+'):
        phone = '+256' + phone.lstrip('0')
    try:
        uid = execute(
            "INSERT INTO users (name,phone,email,password_hash,role,district,language) VALUES (?,?,?,?,?,?,?)",
            (d['name'], phone, d.get('email',''), hash_password(d['password']),
             d['role'], d.get('district',''), d.get('language','en'))
        )
        if d['role'] == 'farmer':
            execute(
                "INSERT INTO farmers (user_id,farm_name,district,crops,monthly_output_kg,farm_size_acres,experience_years,mobile_money,mobile_money_number,challenges) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, d.get('farm_name',''), d.get('district',''), d.get('crops',''),
                 d.get('monthly_output_kg',0), d.get('farm_size_acres',0),
                 d.get('experience_years',0), d.get('mobile_money','MTN'),
                 d.get('mobile_money_number',''), d.get('challenges',''))
            )
        elif d['role'] == 'vendor':
            execute(
                "INSERT INTO vendors (user_id,business_name,market_name,district,product_categories,weekly_volume_kg) VALUES (?,?,?,?,?,?)",
                (uid, d.get('business_name',''), d.get('market_name',''),
                 d.get('district',''), d.get('product_categories',''), d.get('weekly_volume_kg',0))
            )
        elif d['role'] == 'hotel':
            execute(
                "INSERT INTO hotels (user_id,hotel_name,stars,district,weekly_produce_kg,quality_grade,preferred_crops) VALUES (?,?,?,?,?,?,?)",
                (uid, d.get('hotel_name',''), d.get('stars',3),
                 d.get('district',''), d.get('weekly_produce_kg',0),
                 d.get('quality_grade','A'), d.get('preferred_crops',''))
            )
        execute(
            "INSERT INTO notifications (user_id,title,message,type) VALUES (?,?,?,?)",
            (uid, '🎉 Welcome to AgriBridge!',
             f'Hello {d["name"]}! Your account is created. Our team will verify it within 24 hours. Call {CONTACT_DISPLAY} for help.', 'success')
        )
        return jsonify({'success': True, 'user_id': uid, 'role': d['role'],
                        'message': f'Welcome {d["name"]}! Account created successfully.'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Phone number already registered'}), 409

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    phone = d.get('phone','').replace(' ','').replace('-','')
    if phone and not phone.startswith('+'):
        phone = '+256' + phone.lstrip('0')
    user = query(
        "SELECT * FROM users WHERE phone=? AND password_hash=?",
        (phone, hash_password(d.get('password',''))), one=True
    )
    if not user:
        return jsonify({'success': False, 'error': 'Invalid phone or password'}), 401
    u = dict(user)
    u.pop('password_hash', None)
    return jsonify({'success': True, 'user': u})

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    user = query("SELECT id,name,phone,email,role,district,verified,profile_pct,created_at FROM users WHERE id=?", (user_id,), one=True)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    return jsonify({'success': True, 'user': dict(user)})

@app.route('/api/user/<int:user_id>', methods=['PATCH'])
def update_user(user_id):
    d = request.json or {}
    fields = []
    vals = []
    allowed = ['name','email','district','language']
    for f in allowed:
        if f in d:
            fields.append(f'{f}=?')
            vals.append(d[f])
    if not fields:
        return jsonify({'success': False, 'error': 'Nothing to update'}), 400
    vals.append(user_id)
    execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", vals)
    return jsonify({'success': True})

# ─────────────────────────────────────────
# LISTINGS / MARKETPLACE
# ─────────────────────────────────────────

@app.route('/api/listings')
def get_listings():
    crop     = request.args.get('crop','')
    district = request.args.get('district','')
    grade    = request.args.get('grade','')
    sql = """
        SELECT l.*, u.name as farmer_name, u.phone as farmer_phone,
               f.farm_name, f.gps_lat, f.gps_lon, f.experience_years, f.storage_available
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
    if not d.get('farmer_id') or not d.get('crop'):
        return jsonify({'success': False, 'error': 'farmer_id and crop required'}), 400
    lid = execute(
        "INSERT INTO listings (farmer_id,crop,quantity_kg,price_per_kg,quality_grade,harvest_date,district,delivery_available,image_url) VALUES (?,?,?,?,?,?,?,?,?)",
        (d['farmer_id'], d['crop'], d.get('quantity_kg',0), d.get('price_per_kg',0),
         d.get('quality_grade','A'), d.get('harvest_date',''), d.get('district',''),
         d.get('delivery_available',0), d.get('image_url',''))
    )
    return jsonify({'success': True, 'listing_id': lid})

@app.route('/api/listings/<int:lid>', methods=['PATCH'])
def update_listing(lid):
    d = request.json or {}
    allowed = ['quantity_kg','price_per_kg','quality_grade','status','delivery_available']
    fields = [f'{k}=?' for k in allowed if k in d]
    vals = [d[k] for k in allowed if k in d]
    if not fields:
        return jsonify({'success': False, 'error': 'Nothing to update'}), 400
    vals.append(lid)
    execute(f"UPDATE listings SET {','.join(fields)} WHERE id=?", vals)
    return jsonify({'success': True})

# ─────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────

@app.route('/api/orders', methods=['POST'])
def create_order():
    d = request.json or {}
    listing = query("SELECT l.*,f.user_id as farmer_uid FROM listings l JOIN farmers f ON l.farmer_id=f.id WHERE l.id=?",
                    (d.get('listing_id'),), one=True)
    if not listing:
        return jsonify({'success': False, 'error': 'Listing not found'}), 404
    qty   = float(d.get('quantity_kg', 0))
    total = qty * float(listing['price_per_kg'])
    commission = round(total * 0.04, 0)
    ref = gen_ref('AB')
    oid = execute(
        "INSERT INTO orders (ref,listing_id,buyer_user_id,farmer_user_id,quantity_kg,total_price,commission,delivery_address,delivery_district,notes,payment_method) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ref, d['listing_id'], d.get('buyer_user_id'), listing['farmer_uid'],
         qty, total, commission, d.get('delivery_address',''), d.get('delivery_district',''),
         d.get('notes',''), d.get('payment_method','mobile_money'))
    )
    execute(
        "INSERT INTO notifications (user_id,title,message,type) VALUES (?,?,?,?)",
        (listing['farmer_uid'], f'📦 New Order {ref}',
         f'You have a new order for {qty}kg of {listing["crop"]}. Total: UGX {total:,.0f}. Call buyer or check your dashboard.', 'info')
    )
    return jsonify({'success': True, 'order_id': oid, 'ref': ref,
                    'total': total, 'commission': commission,
                    'message': f'Order {ref} placed! The farmer will confirm within 2 hours.'})

@app.route('/api/orders/<int:order_id>/status', methods=['PATCH'])
def update_order_status(order_id):
    d = request.json or {}
    new_status = d.get('status')
    valid = ['pending','confirmed','packed','collected','in_transit','delivered','cancelled']
    if new_status not in valid:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    execute("UPDATE orders SET status=?, updated_at=? WHERE id=?",
            (new_status, datetime.now().isoformat(), order_id))
    return jsonify({'success': True, 'status': new_status})

@app.route('/api/orders/user/<int:user_id>')
def get_user_orders(user_id):
    orders = query("""
        SELECT o.*, l.crop, l.price_per_kg, l.district as listing_district,
               u.name as farmer_name, u.phone as farmer_phone
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.farmer_user_id = u.id
        WHERE o.buyer_user_id = ? OR o.farmer_user_id = ?
        ORDER BY o.created_at DESC
    """, (user_id, user_id))
    return jsonify({'success': True, 'orders': rows_to_list(orders)})

@app.route('/api/orders/ref/<string:ref>')
def get_order_by_ref(ref):
    order = query("""
        SELECT o.*, l.crop, l.price_per_kg, u.name as farmer_name, u.phone as farmer_phone
        FROM orders o
        JOIN listings l ON o.listing_id = l.id
        JOIN users u ON o.farmer_user_id = u.id
        WHERE o.ref = ?
    """, (ref,), one=True)
    if not order:
        return jsonify({'success': False, 'error': 'Order not found'}), 404
    return jsonify({'success': True, 'order': dict(order)})

# ─────────────────────────────────────────
# DELIVERY TRACKING SYSTEM
# ─────────────────────────────────────────

@app.route('/api/deliveries', methods=['POST'])
def create_delivery():
    d = request.json or {}
    order_id = d.get('order_id')
    if not order_id:
        return jsonify({'success': False, 'error': 'order_id required'}), 400
    order = query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        return jsonify({'success': False, 'error': 'Order not found'}), 404
    ref = gen_ref('DEL')
    did = execute(
        """INSERT INTO deliveries
           (ref,order_id,driver_name,driver_phone,vehicle_plate,
            pickup_location,pickup_district,dropoff_location,dropoff_district,
            distance_km,eta,notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ref, order_id, d.get('driver_name','TBA'), d.get('driver_phone',''),
         d.get('vehicle_plate',''), d.get('pickup_location',''),
         d.get('pickup_district',''), d.get('dropoff_location', order['delivery_address'] or ''),
         d.get('dropoff_district', order['delivery_district'] or ''),
         d.get('distance_km',0), d.get('eta',''), d.get('notes',''))
    )
    execute(
        "INSERT INTO delivery_events (delivery_id,event_type,description,location) VALUES (?,?,?,?)",
        (did, 'assigned', f'Delivery {ref} created and driver assigned', d.get('pickup_location',''))
    )
    execute("UPDATE orders SET status='confirmed', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), order_id))
    return jsonify({'success': True, 'delivery_id': did, 'ref': ref})

@app.route('/api/deliveries/<string:ref>')
def get_delivery(ref):
    delivery = query("SELECT * FROM deliveries WHERE ref=?", (ref,), one=True)
    if not delivery:
        # Try by order ref
        order = query("SELECT id FROM orders WHERE ref=?", (ref,), one=True)
        if order:
            delivery = query("SELECT * FROM deliveries WHERE order_id=?", (order['id'],), one=True)
    if not delivery:
        return jsonify({'success': False, 'error': 'Delivery not found. Try: DEL-2026-001, DEL-2026-002, DEL-2026-003'}), 404
    d = dict(delivery)
    events = rows_to_list(query(
        "SELECT * FROM delivery_events WHERE delivery_id=? ORDER BY created_at", (d['id'],)
    ))
    order = query("SELECT o.*,l.crop,l.quantity_kg FROM orders o JOIN listings l ON o.listing_id=l.id WHERE o.id=?",
                  (d['order_id'],), one=True)
    alerts = rows_to_list(query(
        "SELECT * FROM delivery_alerts WHERE delivery_id=? ORDER BY created_at DESC LIMIT 10", (d['id'],)
    ))
    return jsonify({
        'success': True,
        'delivery': d,
        'events': events,
        'order': dict(order) if order else {},
        'alerts': alerts
    })

@app.route('/api/deliveries/<int:did>/status', methods=['PATCH'])
def update_delivery_status(did):
    d = request.json or {}
    new_status = d.get('status')
    valid_statuses = ['assigned','loading','picked_up','in_transit','at_destination','delivered','failed']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': f'Invalid status. Must be one of: {valid_statuses}'}), 400

    now = datetime.now().isoformat()
    execute("UPDATE deliveries SET status=?, current_location=?, updated_at=? WHERE id=?",
            (new_status, d.get('location',''), now, did))

    if new_status == 'delivered':
        execute("UPDATE deliveries SET delivered_at=?, recipient_name=? WHERE id=?",
                (now, d.get('recipient_name',''), did))
        delivery = query("SELECT order_id FROM deliveries WHERE id=?", (did,), one=True)
        if delivery:
            execute("UPDATE orders SET status='delivered', updated_at=? WHERE id=?",
                    (now, delivery['order_id']))

    execute(
        "INSERT INTO delivery_events (delivery_id,event_type,description,location,lat,lon) VALUES (?,?,?,?,?,?)",
        (did, new_status, d.get('description', f'Status updated to {new_status}'),
         d.get('location',''), d.get('lat'), d.get('lon'))
    )

    status_labels = {
        'loading': 'Produce being loaded',
        'picked_up': 'Picked up from farm',
        'in_transit': 'In transit to destination',
        'at_destination': 'Arrived at destination',
        'delivered': '✅ Delivered successfully',
        'failed': '❌ Delivery failed'
    }
    execute(
        "INSERT INTO delivery_alerts (delivery_id,alert_type,title,message) VALUES (?,?,?,?)",
        (did, 'success' if new_status == 'delivered' else 'info',
         status_labels.get(new_status, new_status),
         d.get('description', f'Delivery status: {new_status}'))
    )
    return jsonify({'success': True, 'status': new_status})

@app.route('/api/deliveries/<int:did>/location', methods=['PATCH'])
def update_delivery_location(did):
    d = request.json or {}
    execute(
        "UPDATE deliveries SET current_lat=?, current_lon=?, current_location=?, eta=?, updated_at=? WHERE id=?",
        (d.get('lat'), d.get('lon'), d.get('location',''), d.get('eta',''), datetime.now().isoformat(), did)
    )
    return jsonify({'success': True})

@app.route('/api/deliveries/active')
def get_active_deliveries():
    rows = query("""
        SELECT d.*, o.ref as order_ref, l.crop, l.quantity_kg,
               ub.name as buyer_name, uf.name as farmer_name
        FROM deliveries d
        JOIN orders o ON d.order_id = o.id
        JOIN listings l ON o.listing_id = l.id
        JOIN users ub ON o.buyer_user_id = ub.id
        JOIN users uf ON o.farmer_user_id = uf.id
        WHERE d.status NOT IN ('delivered','failed')
        ORDER BY d.created_at DESC
    """)
    return jsonify({'success': True, 'deliveries': rows_to_list(rows)})

@app.route('/api/deliveries/alerts')
def get_delivery_alerts():
    limit = int(request.args.get('limit', 20))
    rows = query("""
        SELECT da.*, d.ref as delivery_ref
        FROM delivery_alerts da
        JOIN deliveries d ON da.delivery_id = d.id
        ORDER BY da.created_at DESC LIMIT ?
    """, (limit,))
    return jsonify({'success': True, 'alerts': rows_to_list(rows)})

# ─────────────────────────────────────────
# PRICE DATA
# ─────────────────────────────────────────

@app.route('/api/prices')
def get_prices():
    rows = query("""
        SELECT crop, district, AVG(price_per_kg) as avg_price,
               MAX(price_per_kg) as max_price, MIN(price_per_kg) as min_price,
               COUNT(*) as data_points, MAX(recorded_at) as last_updated
        FROM price_history GROUP BY crop, district ORDER BY crop, district
    """)
    return jsonify({'success': True, 'prices': rows_to_list(rows)})

@app.route('/api/prices/trend')
def price_trend():
    crop     = request.args.get('crop','Tomatoes')
    district = request.args.get('district','Kampala')
    rows = query(
        "SELECT price_per_kg, recorded_at FROM price_history WHERE crop=? AND district=? ORDER BY recorded_at",
        (crop, district)
    )
    return jsonify({'success': True, 'trend': rows_to_list(rows)})

@app.route('/api/prices/record', methods=['POST'])
def record_price():
    d = request.json or {}
    execute("INSERT INTO price_history (crop,district,price_per_kg) VALUES (?,?,?)",
            (d.get('crop'), d.get('district'), d.get('price')))
    return jsonify({'success': True})

# ─────────────────────────────────────────
# MATCHING ENGINE
# ─────────────────────────────────────────

@app.route('/api/match')
def match_farmers():
    crop     = request.args.get('crop','')
    district = request.args.get('district','')
    volume   = float(request.args.get('volume', 0))
    grade    = request.args.get('grade','A')

    farmers = query("""
        SELECT f.*, u.name, u.phone, u.district as user_district, u.verified
        FROM farmers f JOIN users u ON f.user_id = u.id WHERE u.verified = 1
    """)

    results = []
    for f in farmers:
        score = 0
        farmer_crops = [c.strip().lower() for c in (f['crops'] or '').split(',')]
        if crop.lower() in farmer_crops:       score += 40
        if district and (f['user_district'] or '').lower() == district.lower(): score += 20
        elif district and district.lower() in (f['user_district'] or '').lower(): score += 10
        if f['monthly_output_kg'] and float(f['monthly_output_kg']) >= volume: score += 20
        if f['storage_available']:             score += 10
        if f['irrigation']:                    score += 10
        if score > 20:
            r = dict(f)
            r['match_score'] = min(score, 99)
            results.append(r)

    results.sort(key=lambda x: x['match_score'], reverse=True)
    return jsonify({'success': True, 'matches': results[:10]})

# ─────────────────────────────────────────
# FARMERS & VENDORS DIRECTORY
# ─────────────────────────────────────────

@app.route('/api/farmers')
def get_farmers():
    rows = query("""
        SELECT f.*, u.name, u.phone, u.district as user_district, u.verified
        FROM farmers f JOIN users u ON f.user_id = u.id ORDER BY u.name
    """)
    return jsonify({'success': True, 'farmers': rows_to_list(rows)})

@app.route('/api/vendors')
def get_vendors():
    rows = query("""
        SELECT v.*, u.name, u.phone, u.verified
        FROM vendors v JOIN users u ON v.user_id = u.id ORDER BY u.name
    """)
    return jsonify({'success': True, 'vendors': rows_to_list(rows)})

# ─────────────────────────────────────────
# TRAINING
# ─────────────────────────────────────────

@app.route('/api/training')
def get_training():
    cat = request.args.get('category','')
    if cat:
        rows = query("SELECT * FROM training_modules WHERE category=? ORDER BY id", (cat,))
    else:
        rows = query("SELECT * FROM training_modules ORDER BY id")
    return jsonify({'success': True, 'modules': rows_to_list(rows)})

@app.route('/api/training/progress', methods=['POST'])
def update_progress():
    d = request.json or {}
    existing = query("SELECT id FROM training_progress WHERE user_id=? AND module_id=?",
                     (d.get('user_id'), d.get('module_id')), one=True)
    if existing:
        execute("UPDATE training_progress SET progress_pct=?, completed=?, completed_at=? WHERE id=?",
                (d.get('progress_pct',0), d.get('completed',0),
                 datetime.now().isoformat() if d.get('completed') else None, existing['id']))
    else:
        execute("INSERT INTO training_progress (user_id,module_id,progress_pct,completed) VALUES (?,?,?,?)",
                (d.get('user_id'), d.get('module_id'), d.get('progress_pct',0), d.get('completed',0)))
    return jsonify({'success': True})

# ─────────────────────────────────────────
# FORWARD CONTRACTS
# ─────────────────────────────────────────

@app.route('/api/contracts', methods=['POST'])
def create_contract():
    d = request.json or {}
    ref = gen_ref('FC')
    cid = execute(
        "INSERT INTO forward_contracts (ref,farmer_id,buyer_user_id,crop,quantity_kg,locked_price,delivery_date) VALUES (?,?,?,?,?,?,?)",
        (ref, d.get('farmer_id'), d.get('buyer_user_id'), d.get('crop'),
         d.get('quantity_kg'), d.get('locked_price'), d.get('delivery_date'))
    )
    return jsonify({'success': True, 'contract_id': cid, 'ref': ref})

@app.route('/api/contracts/user/<int:user_id>')
def get_contracts(user_id):
    rows = query("""
        SELECT fc.*, u.name as farmer_name
        FROM forward_contracts fc
        JOIN farmers f ON fc.farmer_id = f.id
        JOIN users u ON f.user_id = u.id
        WHERE fc.buyer_user_id=? OR fc.farmer_id IN (SELECT id FROM farmers WHERE user_id=?)
        ORDER BY fc.created_at DESC
    """, (user_id, user_id))
    return jsonify({'success': True, 'contracts': rows_to_list(rows)})

# ─────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────

@app.route('/api/search')
def search():
    q = request.args.get('q','').strip().lower()
    if not q or len(q) < 2:
        return jsonify({'success': False, 'error': 'Query too short'}), 400

    listings = rows_to_list(query("""
        SELECT l.*, u.name as farmer_name
        FROM listings l JOIN farmers f ON l.farmer_id=f.id JOIN users u ON f.user_id=u.id
        WHERE (lower(l.crop) LIKE ? OR lower(l.district) LIKE ? OR lower(u.name) LIKE ?)
        AND l.status='active' ORDER BY l.created_at DESC LIMIT 6
    """, (f'%{q}%', f'%{q}%', f'%{q}%')))

    farmers = rows_to_list(query("""
        SELECT f.*, u.name, u.phone, u.district, u.verified
        FROM farmers f JOIN users u ON f.user_id=u.id
        WHERE lower(u.name) LIKE ? OR lower(u.district) LIKE ?
           OR lower(f.farm_name) LIKE ? OR lower(f.crops) LIKE ?
        ORDER BY u.verified DESC LIMIT 5
    """, (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')))

    vendors = rows_to_list(query("""
        SELECT v.*, u.name, u.phone, u.verified
        FROM vendors v JOIN users u ON v.user_id=u.id
        WHERE lower(u.name) LIKE ? OR lower(v.business_name) LIKE ?
           OR lower(v.market_name) LIKE ? OR lower(v.district) LIKE ?
        LIMIT 4
    """, (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')))

    training = rows_to_list(query("""
        SELECT * FROM training_modules
        WHERE lower(title) LIKE ? OR lower(category) LIKE ? OR lower(description) LIKE ?
        LIMIT 4
    """, (f'%{q}%', f'%{q}%', f'%{q}%')))

    total = len(listings) + len(farmers) + len(vendors) + len(training)
    return jsonify({'success': True, 'query': q, 'total_results': total,
                    'results': {'listings': listings, 'farmers': farmers, 'vendors': vendors, 'training': training}})

# ─────────────────────────────────────────
# CONTACT FORM
# ─────────────────────────────────────────

@app.route('/api/contact', methods=['POST'])
def contact():
    d = request.json or {}
    if not d.get('name') or not d.get('message'):
        return jsonify({'success': False, 'error': 'Name and message are required'}), 400
    execute(
        "INSERT INTO contacts (name,email,phone,subject,message) VALUES (?,?,?,?,?)",
        (d.get('name',''), d.get('email',''), d.get('phone',''),
         d.get('subject','General Enquiry'), d.get('message',''))
    )
    return jsonify({'success': True,
                    'message': f'Thank you {d["name"]}! We will respond within 24 hours. For urgent help call {CONTACT_DISPLAY}.'})

# ─────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────

@app.route('/api/notifications/<int:user_id>')
def get_notifications(user_id):
    rows = query(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user_id,)
    )
    unread = query("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND read=0", (user_id,), one=True)
    return jsonify({'success': True, 'notifications': rows_to_list(rows),
                    'unread_count': unread['c'] if unread else 0})

@app.route('/api/notifications/<int:nid>/read', methods=['PATCH'])
def mark_read(nid):
    execute("UPDATE notifications SET read=1 WHERE id=?", (nid,))
    return jsonify({'success': True})

# ─────────────────────────────────────────
# DASHBOARD STATS
# ─────────────────────────────────────────

@app.route('/api/stats')
def stats():
    farmers_count  = query("SELECT COUNT(*) as c FROM farmers", one=True)['c']
    vendors_count  = query("SELECT COUNT(*) as c FROM vendors", one=True)['c']
    listings_count = query("SELECT COUNT(*) as c FROM listings WHERE status='active'", one=True)['c']
    orders_count   = query("SELECT COUNT(*) as c FROM orders", one=True)['c']
    deliveries_active = query("SELECT COUNT(*) as c FROM deliveries WHERE status NOT IN ('delivered','failed')", one=True)['c']
    total_kg = query("SELECT SUM(quantity_kg) as s FROM listings WHERE status='active'", one=True)['s'] or 0
    return jsonify({'success': True, 'stats': {
        'farmers': farmers_count, 'vendors': vendors_count,
        'active_listings': listings_count, 'orders': orders_count,
        'active_deliveries': deliveries_active,
        'total_produce_kg': total_kg, 'districts_covered': 135,
        'revenue_generated': 'UGX 847M', 'contact': CONTACT_DISPLAY,
        'tollfree': TOLLFREE
    }})

@app.route('/api/dashboard/user/<int:user_id>')
def user_dashboard(user_id):
    user = query("SELECT * FROM users WHERE id=?", (user_id,), one=True)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    orders = rows_to_list(query("""
        SELECT o.*, l.crop FROM orders o JOIN listings l ON o.listing_id=l.id
        WHERE o.buyer_user_id=? OR o.farmer_user_id=? ORDER BY o.created_at DESC LIMIT 5
    """, (user_id, user_id)))
    deliveries = rows_to_list(query("""
        SELECT d.* FROM deliveries d JOIN orders o ON d.order_id=o.id
        WHERE o.buyer_user_id=? OR o.farmer_user_id=?
        ORDER BY d.created_at DESC LIMIT 5
    """, (user_id, user_id)))
    revenue = query("""
        SELECT SUM(total_price) as total FROM orders
        WHERE farmer_user_id=? AND status='delivered'
    """, (user_id,), one=True)
    notifs = rows_to_list(query(
        "SELECT * FROM notifications WHERE user_id=? AND read=0 ORDER BY created_at DESC LIMIT 5", (user_id,)
    ))
    return jsonify({'success': True, 'dashboard': {
        'user': dict(user), 'recent_orders': orders,
        'recent_deliveries': deliveries,
        'revenue_this_month': revenue['total'] or 0,
        'notifications': notifs
    }})

# ─────────────────────────────────────────
# DISTRICTS
# ─────────────────────────────────────────

@app.route('/api/districts')
def districts():
    data = [
        {"name":"Kampala","farmers":342,"produce":"Vegetables, Matooke","lat":0.3476,"lon":32.5825},
        {"name":"Wakiso","farmers":521,"produce":"Carrots, Cabbages, Onions","lat":0.4042,"lon":32.4597},
        {"name":"Mukono","farmers":287,"produce":"Tomatoes, Pepper, Fruits","lat":0.3535,"lon":32.7553},
        {"name":"Gulu","farmers":412,"produce":"Maize, Groundnuts, Sorghum","lat":2.7745,"lon":32.2990},
        {"name":"Mbarara","farmers":356,"produce":"Maize, Beans, Sorghum","lat":-0.6072,"lon":30.6545},
        {"name":"Jinja","farmers":198,"produce":"Sugarcane, Maize, Vegetables","lat":0.4244,"lon":33.2042},
        {"name":"Mbale","farmers":267,"produce":"Coffee, Maize, Vegetables","lat":1.0796,"lon":34.1751},
        {"name":"Lira","farmers":389,"produce":"Cotton, Maize, Sunflower","lat":2.2449,"lon":32.8997},
    ]
    return jsonify({'success': True, 'districts': data})

# ─────────────────────────────────────────
# USSD (Africa's Talking + Web Simulator)
# ─────────────────────────────────────────

@app.route('/api/ussd', methods=['POST'])
def ussd():
    content_type = request.content_type or ''
    if 'application/json' in content_type:
        d          = request.json or {}
        text       = d.get('text','').strip()
        phone      = d.get('phoneNumber', CONTACT_PHONE)
        session_id = d.get('sessionId','demo')
        network    = 'web'
    else:
        text       = (request.form.get('text') or '').strip()
        phone      = request.form.get('phoneNumber', CONTACT_PHONE)
        session_id = request.form.get('sessionId','demo')
        network    = request.form.get('networkCode','unknown')

    try:
        execute("INSERT OR IGNORE INTO ussd_sessions (session_id,phone,state,data) VALUES (?,?,?,?)",
                (session_id, phone, 'main', '{}'))
        execute("UPDATE ussd_sessions SET updated_at=? WHERE session_id=?",
                (datetime.now().isoformat(), session_id))
    except Exception:
        pass

    parts = [p for p in text.split('*') if p] if text else []
    level = len(parts)

    def respond(msg):
        if network == 'web':
            return jsonify({'response': msg})
        return msg, 200, {'Content-Type': 'text/plain'}

    main_menu = (
        "CON Welcome to AgriBridge\nUganda Farm-to-Table\n"
        "----------------------------\n"
        "1. Market Prices\n2. List My Produce\n"
        "3. Find Buyers\n4. Training & Tips\n"
        "5. Weather Forecast\n6. My Account\n7. Register"
    )

    if level == 0:
        return respond(main_menu)

    choice = parts[0]

    if choice == '1':
        if level == 1:
            return respond("CON Select crop:\n1. Tomatoes\n2. Matooke\n3. Maize\n4. Carrots\n5. Onions\n6. Groundnuts\n0. Back")
        if parts[1] == '0': return respond(main_menu)
        crop_map = {'1':'Tomatoes','2':'Matooke','3':'Maize','4':'Carrots','5':'Onions','6':'Groundnuts'}
        crop = crop_map.get(parts[1],'Tomatoes')
        if level == 2:
            return respond(f"CON {crop} — select district:\n1. Kampala\n2. Wakiso\n3. Gulu\n4. Mbarara\n0. Back")
        dist_map = {'1':'Kampala','2':'Wakiso','3':'Gulu','4':'Mbarara'}
        district = dist_map.get(parts[2] if level > 2 else '1','Kampala')
        row = query("SELECT AVG(price_per_kg) as avg FROM price_history WHERE crop=? AND district=?", (crop,district), one=True)
        avg = round(row['avg']) if row and row['avg'] else 1200
        return respond(f"END {crop} — {district}\nPrice: UGX {avg:,}/kg\n----------------------------\nSend PRICES to 8204\nCall {CONTACT_DISPLAY}")

    elif choice == '2':
        if level == 1: return respond("CON Enter crop name:\n(e.g. Tomatoes, Maize)")
        if level == 2: return respond(f"CON Crop: {parts[1]}\nEnter quantity in kg:")
        if level == 3: return respond(f"CON {parts[2]}kg of {parts[1]}\nEnter price per kg (UGX):")
        if level == 4: return respond(f"CON Price: UGX {parts[3]}/kg\nSelect district:\n1.Kampala 2.Wakiso\n3.Gulu 4.Mbarara 5.Other")
        if level >= 5:
            dist_map = {'1':'Kampala','2':'Wakiso','3':'Gulu','4':'Mbarara','5':'Other'}
            district = dist_map.get(parts[4],'Uganda')
            ref = gen_ref('AB')
            execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
                    (phone, f"USSD listing: {parts[1]} {parts[2]}kg @{parts[3]}/kg {district}", 'inbound'))
            return respond(f"END Listed!\nCrop: {parts[1]}\nQty: {parts[2]}kg\nPrice: UGX {parts[3]}/kg\nDistrict: {district}\nRef: {ref}\nCall {CONTACT_DISPLAY}")

    elif choice == '3':
        if level == 1:
            return respond("CON Find buyers:\n1. Market Vendors\n2. Hotels & Restaurants\n3. Exporters\n4. All Buyers\n0. Back")
        binfo = {'1':("Vendors","Nakasero:24\nOwino:18 active"),'2':("Hotels","Kampala:8 hotels"),'3':("Exporters","3 active, min 1000kg"),'4':("All","247 active buyers")}
        btype, bdet = binfo.get(parts[1],("Buyers","Contact us"))
        return respond(f"END {btype}:\n{bdet}\n----------------------------\nCall {CONTACT_DISPLAY}\n(free from any network)")

    elif choice == '4':
        if level == 1:
            return respond("CON Training topics:\n1. Crop Science\n2. Post-Harvest\n3. Mobile Money\n4. Business Skills\n5. Digital Tools\n0. Back")
        cats = {'1':'Crop Science','2':'Post-Harvest','3':'Finance','4':'Business Skills','5':'Digital Tools'}
        cat = cats.get(parts[1],'Crop Science')
        tips = {'Crop Science':"Test soil pH 6.0-7.0\nUse compost fertiliser",
                'Post-Harvest':"Dry maize to 13%\nUse airtight bags",
                'Finance':"MTN *165# Airtel *185#\nfor mobile money",
                'Business Skills':"Always get receipts\nNegotiate in groups",
                'Digital Tools':f"Use AgriBridge *789#\nCall {CONTACT_DISPLAY}"}
        return respond(f"END {cat}:\n{tips.get(cat,'')}\n----------------------------\nSend LEARN to 8204\nCall {CONTACT_DISPLAY}")

    elif choice == '5':
        if level == 1:
            return respond("CON Select district:\n1. Kampala\n2. Wakiso\n3. Gulu\n4. Mbarara\n5. Mbale\n6. Lira\n0. Back")
        weather = {'1':("Kampala","26C Partly cloudy\nGood harvest conditions"),
                   '2':("Wakiso","25C Mostly sunny\nExcellent for transport"),
                   '3':("Gulu","29C Hot & sunny\nWater crops early"),
                   '4':("Mbarara","23C Light showers\nDelay harvest"),
                   '5':("Mbale","22C Rainy\nSecure storage"),
                   '6':("Lira","30C Hot & sunny\nIrrigate fields")}
        dist, info = weather.get(parts[1],("Uganda","Check local forecast"))
        return respond(f"END Weather — {dist}\n{info}\n----------------------------\nSend WEATHER to 8204\nSource: Uganda Met Auth")

    elif choice == '6':
        user = query("SELECT * FROM users WHERE phone=?", (phone,), one=True)
        if not user:
            return respond(f"END Account not found.\nDial back and choose 7\nOr call {CONTACT_DISPLAY}")
        return respond(f"END My Account\nName: {user['name']}\nRole: {user['role'].title()}\nDistrict: {user['district'] or 'Not set'}\nStatus: {'Verified' if user['verified'] else 'Pending'}\n----------------------------\nCall {CONTACT_DISPLAY}")

    elif choice == '7':
        if level == 1:
            return respond("CON Register as:\n1. Farmer\n2. Vendor\n3. Hotel\n4. Buyer\n0. Back")
        roles = {'1':'farmer','2':'vendor','3':'hotel','4':'buyer'}
        role = roles.get(parts[1],'farmer')
        if level == 2: return respond(f"CON Register as {role.title()}\nEnter your full name:")
        if level == 3: return respond(f"CON Name: {parts[2]}\nEnter your district:")
        if level >= 4:
            ref = gen_ref('REG')
            execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
                    (phone, f"USSD register: {parts[2]} as {role} in {parts[3]}", 'inbound'))
            return respond(f"END Registration received!\nName: {parts[2]}\nRole: {role.title()}\nDistrict: {parts[3]}\nRef: {ref}\nWe call within 24hrs.\n{CONTACT_DISPLAY}")

    return respond(f"END Invalid option.\nDial *789# to start.\nHelp: {CONTACT_DISPLAY}")

# ─────────────────────────────────────────
# SMS HANDLER
# ─────────────────────────────────────────

SMS_HELP = {
    'PRICES': f"Today: Tomatoes UGX 1,500/kg | Matooke UGX 800/kg | Maize UGX 700/kg | Carrots UGX 1,200/kg. Call {CONTACT_DISPLAY}.",
    'WEATHER': "Kampala: 26C partly cloudy. Rain Fri-Sat. Good harvest Mon-Thu. Source: Uganda Met Authority.",
    'JOIN':    f"Welcome to AgriBridge! Reply: FARMER <name> <district> <crop> OR VENDOR <name> <market>. Free: {CONTACT_DISPLAY}",
    'ORDERS':  f"No pending orders. To buy: BUY <crop> <kg> <district>. We connect you with farmers. Call {CONTACT_DISPLAY}.",
    'TRACK':   "To track: TRACK <order-ref> e.g. TRACK AB-2026-001. Or call for live updates.",
    'HELP':    f"AgriBridge SMS:\nPRICES - prices\nWEATHER - forecast\nJOIN - register\nORDERS - orders\nTRACK - delivery\nCall {CONTACT_DISPLAY}",
}

@app.route('/api/sms', methods=['POST'])
def handle_sms():
    d = request.json or {}
    msg     = (d.get('message','') or '').strip().upper()
    phone   = d.get('phone','')
    keyword = msg.split()[0] if msg else ''
    reply   = SMS_HELP.get(keyword, SMS_HELP['HELP'])

    if keyword == 'LIST' and len(msg.split()) >= 4:
        parts = msg.split()
        crop, qty, price = parts[1], parts[2], parts[3]
        ref = gen_ref('AB')
        execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
                (phone, f"SMS listing: {crop} {qty}kg @{price}", 'inbound'))
        reply = f"Listed! {qty}kg {crop} @UGX {price}/kg. Ref:{ref}. Buyers will contact you. {CONTACT_DISPLAY}"

    elif keyword == 'TRACK' and len(msg.split()) >= 2:
        ref = msg.split()[1]
        delivery = query("SELECT d.* FROM deliveries d JOIN orders o ON d.order_id=o.id WHERE o.ref=? OR d.ref=?", (ref,ref), one=True)
        if delivery:
            reply = f"Delivery {ref}: {delivery['status'].replace('_',' ').title()}. Location: {delivery['current_location'] or 'updating'}. ETA: {delivery['eta'] or 'TBD'}. Call {CONTACT_DISPLAY}"
        else:
            reply = f"Order {ref} not found. Check your ref number or call {CONTACT_DISPLAY}"

    execute("INSERT INTO sms_logs (phone,message,direction) VALUES (?,?,?)",
            (phone, reply, 'outbound'))
    return jsonify({'success': True, 'reply': reply})

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────

@app.route('/api/health')
def health():
    db_ok = True
    try:
        query("SELECT 1")
    except Exception:
        db_ok = False
    return jsonify({
        'status': 'ok', 'platform': 'AgriBridge Uganda', 'version': '3.0',
        'db': 'connected' if db_ok else 'error',
        'contact': CONTACT_DISPLAY, 'tollfree': TOLLFREE,
        'timestamp': datetime.now().isoformat()
    })

# ─────────────────────────────────────────
# RUN
# ─────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
