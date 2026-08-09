# ═══════════════════════════════════════════════════════════════════════════
# AgriBridge Flask Backend v3.0 — Full USSD + All Original Routes
# Deploy on Render.com as a Web Service
# ═══════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES (set in Render Dashboard → Environment):
#   JWT_SECRET, ADMIN_PASSWORD, AT_USERNAME, AT_API_KEY
#   AT_SHORTCODE, AT_SMS_SENDER, SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY
# ═══════════════════════════════════════════════════════════════════════════

import os
import datetime
import json as _json
import hmac
import requests

import jwt
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

try:
    import africastalking
    AT_AVAILABLE = True
except ImportError:
    AT_AVAILABLE = False
    print("WARNING: africastalking not installed. SMS disabled.")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": [
    "https://agribrige.com", "https://www.agribrige.com",
    "https://agribridge-1-og7a.onrender.com",
    "http://localhost:3000", "http://127.0.0.1:3000",
]}})

# ── Config ────────────────────────────────────────────────────────────────────
JWT_SECRET     = os.environ.get('JWT_SECRET',     'CHANGE_ME_IN_RENDER_ENV_VARS')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'agribridge2026')
AT_USERNAME    = os.environ.get('AT_USERNAME',    'sandbox')
AT_API_KEY     = os.environ.get('AT_API_KEY',     'atsk_REPLACE_ME')
AT_SHORTCODE   = os.environ.get('AT_SHORTCODE',   '*789#')
AT_SMS_SENDER  = os.environ.get('AT_SMS_SENDER',  'AgriBridge')
SUPABASE_URL   = os.environ.get('SUPABASE_URL',   'https://vyrctsiyaihsysgpozdm.supabase.co')
SUPABASE_KEY   = os.environ.get('SUPABASE_KEY',   '')
GEMINI_KEY     = os.environ.get('GEMINI_API_KEY', '')

# ── Payments (pluggable; each provider stays OFF until its keys are set) ───────
FLW_SECRET_KEY   = os.environ.get('FLW_SECRET_KEY',   '')  # Flutterwave secret key
FLW_WEBHOOK_HASH = os.environ.get('FLW_WEBHOOK_HASH', '')  # must match the hash set in the FLW dashboard
PESAPAL_KEY      = os.environ.get('PESAPAL_CONSUMER_KEY',    '')
PESAPAL_SECRET   = os.environ.get('PESAPAL_CONSUMER_SECRET', '')
MTN_MOMO_KEY     = os.environ.get('MTN_MOMO_SUBSCRIPTION_KEY', '')
PUBLIC_BASE_URL  = os.environ.get('PUBLIC_BASE_URL', 'https://agribrige.com')

at_sms = None
if AT_AVAILABLE and AT_API_KEY and AT_API_KEY != 'atsk_REPLACE_ME':
    try:
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        at_sms = africastalking.SMS
    except Exception as _at_err:
        print(f"WARNING: Africa's Talking init failed: {_at_err}")

# ── Security hardening ────────────────────────────────────────────────────────
import time as _time
from collections import defaultdict as _defaultdict
from functools import wraps as _wraps

_rl_buckets = _defaultdict(list)

def _client_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (request.remote_addr or 'unknown')

def rate_limit(max_req=30, window=60):
    """Simple in-memory sliding-window limiter, keyed by route+client IP."""
    def deco(fn):
        @_wraps(fn)
        def wrapper(*args, **kwargs):
            now = _time.time()
            key = fn.__name__ + ':' + _client_ip()
            bucket = _rl_buckets[key]
            cutoff = now - window
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= max_req:
                return jsonify({'error': 'Too many requests. Please slow down.'}), 429
            bucket.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return deco

@app.after_request
def _security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['X-XSS-Protection'] = '1; mode=block'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return resp

@app.errorhandler(404)
def _not_found(e):
    return jsonify({'error': 'Not found'}), 404

# ── Supabase helpers ──────────────────────────────────────────────────────────
def supa_get(table, filters=None, limit=100):
    if not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {'limit': limit}
    if filters:
        params.update(filters)
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        res = requests.get(url, params=params, headers=headers, timeout=8)
        return res.json() if res.ok else []
    except Exception as e:
        print(f"Supabase GET error: {e}")
        return []

def supa_insert(table, data):
    if not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }
    try:
        res = requests.post(url, json=data, headers=headers, timeout=8)
        return res.json() if res.ok else None
    except Exception as e:
        print(f"Supabase INSERT error: {e}")
        return None

def supa_update(table, data, eq_col, eq_val):
    if not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
    }
    params = {eq_col: f'eq.{eq_val}'}
    try:
        res = requests.patch(url, json=data, params=params, headers=headers, timeout=8)
        return res.status_code < 300
    except Exception as e:
        print(f"Supabase UPDATE error: {e}")
        return False

def supa_delete(table, eq_col, eq_val):
    if not SUPABASE_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    }
    params = {eq_col: f'eq.{eq_val}'}
    try:
        res = requests.delete(url, params=params, headers=headers, timeout=8)
        return res.status_code < 300
    except Exception as e:
        print(f"Supabase DELETE error: {e}")
        return False

# ── JWT helpers ───────────────────────────────────────────────────────────────
def make_token(payload_extra, hours=24):
    payload = {
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=hours),
        **payload_extra
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
    return token if isinstance(token, str) else token.decode('utf-8')

def verify_token(required_role=None):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None, (jsonify({'error': 'Missing token'}), 401)
    token = auth.split(' ', 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        if required_role and payload.get('role') != required_role:
            return None, (jsonify({'error': 'Insufficient permissions'}), 403)
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, (jsonify({'error': 'Token expired'}), 401)
    except jwt.InvalidTokenError:
        return None, (jsonify({'error': 'Invalid token'}), 401)

# ── Number formatter ──────────────────────────────────────────────────────────
def fmt(n):
    return f"{int(n):,}"

# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/')
@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'AgriBridge API',
        'version': '3.0.0',
        'at_enabled': AT_AVAILABLE and at_sms is not None,
        'supabase_connected': bool(SUPABASE_KEY)
    })

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN AUTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/admin/login', methods=['POST'])
@rate_limit(max_req=5, window=300)
def admin_login():
    data = request.get_json(force=True, silent=True) or {}
    if data.get('password', '') != ADMIN_PASSWORD:
        return jsonify({'error': 'Invalid password'}), 401
    token = make_token({'sub': 'admin', 'role': 'admin'}, hours=24)
    return jsonify({'token': token, 'role': 'admin', 'message': 'Login successful'}), 200

@app.route('/api/admin/verify', methods=['GET'])
def admin_verify():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify({'valid': True, 'sub': payload.get('sub')}), 200

# ══════════════════════════════════════════════════════════════════════════════
# USSD — Full AgriBridge Menu System
# Africa's Talking calls: POST /api/ussd
# Set Callback URL in AT dashboard:
#   https://agribridge-1-og7a.onrender.com/api/ussd
# ══════════════════════════════════════════════════════════════════════════════

USSD_SESSIONS = {}  # In-memory store (use Redis for production)

CROP_PRICES = {
    '1': ('Maize',        750,   1000,  'kg'),
    '2': ('Matooke',      950,   1400,  'bunch'),
    '3': ('Tomatoes',     1800,  2500,  'kg'),
    '4': ('Coffee',       12000, 15000, 'kg'),
    '5': ('Beans',        3400,  4200,  'kg'),
    '6': ('Cassava',      500,   800,   'kg'),
    '7': ('Irish Potato', 1200,  1800,  'kg'),
    '8': ('Onions',       2000,  2800,  'kg'),
    '9': ('Avocado',      800,   1200,  'kg'),
}

ANIMAL_PRICES = {
    '1': ('Cattle (Ankole/Friesian)', 2500000, 3500000, 'head'),
    '2': ('Goats',                    280000,  400000,  'head'),
    '3': ('Sheep',                    250000,  350000,  'head'),
    '4': ('Poultry (Broilers)',       25000,   45000,   'bird'),
    '5': ('Tilapia Fish',             12000,   18000,   'kg'),
    '6': ('Honey (Raw)',              15000,   22000,   'kg'),
}


def ussd_route(parts, depth, last, session_id, phone):
    # ── MAIN MENU ─────────────────────────────────────────────────────────────
    if depth == 0:
        return (
            "CON Welcome to AgriBridge *789#\n"
            "Uganda's Farm-to-Table Platform\n\n"
            "1. Check Crop Prices\n"
            "2. Animal & Livestock Prices\n"
            "3. Buy Produce / Marketplace\n"
            "4. List My Produce for Sale\n"
            "5. Farming Tips & Weather\n"
            "6. AI Crop & Animal Doctor\n"
            "7. Register as Farmer\n"
            "8. Support & My Account"
        )

    m = parts[0]

    # ── 1. CROP PRICES ────────────────────────────────────────────────────────
    if m == '1':
        if depth == 1:
            return (
                "CON Crop Prices - Select Crop:\n\n"
                "1. Maize\n2. Matooke (Banana)\n"
                "3. Tomatoes\n4. Coffee\n5. Beans\n"
                "6. Cassava\n7. Irish Potato\n"
                "8. Onions\n9. Avocado\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            if last not in CROP_PRICES:
                return "END Invalid option.\nDial *789# to try again."
            name, ws, rt, unit = CROP_PRICES[last]
            return (
                f"END {name.upper()} PRICES\n"
                f"District: Kampala\n\n"
                f"Wholesale: UGX {fmt(ws)}/{unit}\n"
                f"Retail:    UGX {fmt(rt)}/{unit}\n\n"
                f"Updated: Today 08:00\n"
                f"Source: AgriBridge Markets\n"
                f"More: agribrige.com"
            )

    # ── 2. ANIMAL PRICES ──────────────────────────────────────────────────────
    if m == '2':
        if depth == 1:
            return (
                "CON Animal & Livestock Prices:\n\n"
                "1. Cattle (Ankole/Friesian)\n"
                "2. Goats\n3. Sheep\n"
                "4. Poultry (Broilers/Layers)\n"
                "5. Tilapia Fish\n6. Honey\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            if last not in ANIMAL_PRICES:
                return "END Invalid option.\nDial *789# to retry."
            name, mn, mx, unit = ANIMAL_PRICES[last]
            return (
                f"END {name.upper()}\n\n"
                f"Min: UGX {fmt(mn)}/{unit}\n"
                f"Max: UGX {fmt(mx)}/{unit}\n\n"
                f"Source: AgriBridge Livestock\n"
                f"Updated: Today\n\n"
                f"Buy/sell: agribrige.com\n"
                f"Call: +256 755 966 690"
            )

    # ── 3. MARKETPLACE ────────────────────────────────────────────────────────
    if m == '3':
        if depth == 1:
            return (
                "CON AgriBridge Marketplace:\n\n"
                "1. Browse Fresh Produce\n"
                "2. Browse Livestock\n"
                "3. Bulk Orders (Hotels/NGOs)\n"
                "4. Order by Phone\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            if last == '3':
                return (
                    "END Bulk Orders:\n\n"
                    "Call: +256 755 966 690\n"
                    "WhatsApp: +256 755 966 690\n"
                    "Email: orders@agribridge.ug\n"
                    "Web: agribrige.com/bulk\n\n"
                    "Farm-to-door within 24hrs"
                )
            if last == '4':
                return (
                    "END Order by Phone:\n\n"
                    "Call: +256 755 966 690\n"
                    "Mon-Sat: 7am - 8pm\n\n"
                    "Tell us what you need.\n"
                    "We source from verified\n"
                    "farms directly for you."
                )
            return (
                "END Browse all listings:\n"
                "agribrige.com\n\n"
                "Or call us:\n"
                "+256 755 966 690\n"
                "Mon-Sat 7am-8pm"
            )

    # ── 4. LIST MY PRODUCE ────────────────────────────────────────────────────
    if m == '4':
        if depth == 1:
            return (
                "CON List Your Produce FREE:\n\n"
                "1. Select crop to list\n"
                "2. List via SMS instructions\n"
                "3. Call us to list\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            if last == '1':
                return (
                    "CON Select crop to list:\n\n"
                    "1. Maize\n2. Matooke\n"
                    "3. Tomatoes\n4. Coffee\n"
                    "5. Beans\n6. Cassava\n"
                    "7. Other crop\n"
                    "0. Back"
                )
            if last == '2':
                return (
                    "END List via SMS:\n\n"
                    "Send to 789:\n"
                    "LIST [crop] [qty] [price]\n\n"
                    "Example:\n"
                    "LIST MAIZE 500KG 750\n\n"
                    "We post your listing\n"
                    "within 5 minutes. FREE!"
                )
            if last == '3':
                return (
                    "END Call to List:\n\n"
                    "+256 755 966 690\n"
                    "Mon-Sat 7am-7pm\n\n"
                    "We list for you in\n"
                    "under 2 minutes.\n"
                    "Completely FREE!"
                )
        # depth 3 — crop selected
        if depth == 3 and parts[1] == '1':
            crop_map = {
                '1': 'Maize', '2': 'Matooke', '3': 'Tomatoes',
                '4': 'Coffee', '5': 'Beans', '6': 'Cassava', '7': 'Other'
            }
            if last == '0':
                return ussd_route(['4'], 1, '4', session_id, phone)
            crop = crop_map.get(last, 'Crop')
            USSD_SESSIONS[session_id] = {'crop': crop, 'phone': phone}
            return (
                f"CON {crop} selected.\n"
                f"Enter quantity in kg\n"
                f"(e.g. type 500 then Send):"
            )
        # depth 4 — quantity entered
        if depth == 4 and parts[1] == '1':
            session = USSD_SESSIONS.get(session_id, {})
            session['qty'] = last
            USSD_SESSIONS[session_id] = session
            return (
                f"CON Quantity: {last}kg\n"
                f"Enter your price per kg\n"
                f"in UGX (e.g. type 800):"
            )
        # depth 5 — price entered, save listing
        if depth == 5 and parts[1] == '1':
            session = USSD_SESSIONS.get(session_id, {})
            price = last
            crop  = session.get('crop', 'Crop')
            qty   = session.get('qty', '0')
            supa_insert('listings', {
                'crop_name':    crop,
                'quantity_kg':  float(qty)   if qty.replace('.', '').isdigit()   else 0,
                'price_per_kg': float(price) if price.replace('.', '').isdigit() else 0,
                'farmer_phone': phone,
                'is_available': True,
                'source':       'ussd'
            })
            if at_sms:
                try:
                    at_sms.send(
                        message=(
                            f"AgriBridge: Listing posted!\n"
                            f"{qty}kg {crop} @ UGX {price}/kg.\n"
                            f"Buyers will contact you. agribrige.com"
                        ),
                        recipients=[phone],
                        sender_id=AT_SMS_SENDER
                    )
                except Exception as e:
                    print(f"SMS error: {e}")
            USSD_SESSIONS.pop(session_id, None)
            return (
                f"END Listing posted!\n\n"
                f"Crop:     {crop}\n"
                f"Quantity: {qty}kg\n"
                f"Price:    UGX {price}/kg\n\n"
                f"Buyers will contact you.\n"
                f"SMS confirmation sent.\n"
                f"Thank you - AgriBridge!"
            )

    # ── 5. FARMING TIPS ───────────────────────────────────────────────────────
    if m == '5':
        if depth == 1:
            return (
                "CON Farming Tips & Weather:\n\n"
                "1. Soil & Planting Tips\n"
                "2. Pest & Disease Alerts\n"
                "3. Harvest & Storage\n"
                "4. Weather Forecast\n"
                "5. Planting Calendar\n"
                "6. Animal Care Tips\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            tips = {
                '1': (
                    "END SOIL & PLANTING TIPS\n\n"
                    "Test soil pH before planting.\n"
                    "Maize ideal pH: 5.8 - 6.5\n\n"
                    "Add lime to raise pH.\n"
                    "Add sulfur to lower pH.\n\n"
                    "Season A: Plant Mar-Apr\n"
                    "Season B: Plant Aug-Sep\n\n"
                    "More: agribrige.com/training"
                ),
                '2': (
                    "END PEST ALERT - 2026\n\n"
                    "FALL ARMYWORM ACTIVE!\n"
                    "Central & Eastern Uganda\n\n"
                    "Treatment:\n"
                    "Emamectin Benzoate\n"
                    "200ml per 20L water\n"
                    "Spray into whorl at dusk\n"
                    "Repeat after 7 days"
                ),
                '3': (
                    "END HARVEST & STORAGE\n\n"
                    "Harvest maize at 20-25%\n"
                    "moisture. Use PICS bags.\n"
                    "Store cool and dry.\n"
                    "Inspect every 2 weeks.\n\n"
                    "Proper storage = 6-12\n"
                    "months without loss."
                ),
                '4': (
                    "END WEATHER - Kampala\n\n"
                    "Today: Partly Cloudy 24C\n"
                    "Tomorrow: Light Rain 22C\n"
                    "This Week: Rain expected\n\n"
                    "Farming Advisory:\n"
                    "Delay fertilizer 2-3 days.\n"
                    "Good planting next week.\n\n"
                    "Powered by AgriBridge AI"
                ),
                '5': (
                    "END PLANTING CALENDAR\n\n"
                    "SEASON A (Long Rains)\n"
                    "Plant: March - April\n"
                    "Harvest: June - July\n\n"
                    "SEASON B (Short Rains)\n"
                    "Plant: August - September\n"
                    "Harvest: November - December\n\n"
                    "Coffee harvest: Oct - Feb"
                ),
                '6': (
                    "END ANIMAL CARE TIPS\n\n"
                    "Cattle vaccinations:\n"
                    "- FMD: every 6 months\n"
                    "- ECF (ITM): once yearly\n\n"
                    "ECF symptoms:\n"
                    "Fever 39-41C, swollen\n"
                    "lymph nodes, not eating.\n\n"
                    "ACT FAST - call vet!\n"
                    "Book: agribrige.com/vet"
                ),
            }
            return tips.get(last, "END Invalid option.\nDial *789# to retry.")

    # ── 6. AI DOCTOR ──────────────────────────────────────────────────────────
    if m == '6':
        if depth == 1:
            return (
                "CON AI Doctor - Free Diagnosis:\n\n"
                "1. Diagnose Crop Problem\n"
                "2. Diagnose Animal Problem\n"
                "3. Disease Alerts Uganda\n"
                "4. Book a Vet Visit\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            if last == '1':
                return (
                    "CON Select your crop:\n\n"
                    "1. Maize\n2. Matooke\n"
                    "3. Tomatoes\n4. Coffee\n"
                    "5. Beans\n6. Cassava\n"
                    "7. Other\n0. Back"
                )
            if last == '2':
                return (
                    "CON Select animal type:\n\n"
                    "1. Cattle\n2. Goats\n"
                    "3. Chickens/Poultry\n"
                    "4. Pigs\n5. Fish\n"
                    "6. Other\n0. Back"
                )
            if last == '3':
                return (
                    "END DISEASE ALERTS 2026\n\n"
                    "CROPS:\n"
                    "Fall Armyworm - ACTIVE\n"
                    "Maize Streak - Eastern UG\n\n"
                    "LIVESTOCK:\n"
                    "ECF - Central/Western UG\n"
                    "FMD - sporadic outbreaks\n"
                    "NCD (chickens) - reported\n\n"
                    "Full info: agribrige.com"
                )
            if last == '4':
                return (
                    "END Book a Vet Visit:\n\n"
                    "Call: +256 755 966 690\n"
                    "24/7 emergency line\n\n"
                    "Services:\n"
                    "Farm visit: UGX 50,000\n"
                    "Vaccination: from 5,000\n"
                    "Disease testing available\n"
                    "MAAIF certificates\n\n"
                    "Book: agribrige.com/vet"
                )
        if depth == 3:
            if parts[1] == '1':
                crop_names = {
                    '1': 'Maize', '2': 'Matooke', '3': 'Tomatoes',
                    '4': 'Coffee', '5': 'Beans', '6': 'Cassava', '7': 'Other'
                }
                if last == '0':
                    return ussd_route(['6'], 1, '6', session_id, phone)
                crop = crop_names.get(last, 'crop')
                USSD_SESSIONS[session_id] = {'crop': crop}
                return (
                    f"CON {crop} - Main symptom:\n\n"
                    "1. Yellowing / pale leaves\n"
                    "2. Brown spots / lesions\n"
                    "3. Wilting / drooping\n"
                    "4. White powder / mould\n"
                    "5. Holes / pest damage\n"
                    "6. Rotting / soft stem\n"
                    "7. Stunted growth\n"
                    "0. Back"
                )
            if parts[1] == '2':
                animal_names = {
                    '1': 'Cattle', '2': 'Goats', '3': 'Chickens',
                    '4': 'Pigs', '5': 'Fish', '6': 'Other'
                }
                if last == '0':
                    return ussd_route(['6'], 1, '6', session_id, phone)
                animal = animal_names.get(last, 'animal')
                USSD_SESSIONS[session_id] = {'animal': animal}
                return (
                    f"CON {animal} - Main symptom:\n\n"
                    "1. High fever / hot body\n"
                    "2. Not eating / dull\n"
                    "3. Diarrhoea\n"
                    "4. Coughing / breathing\n"
                    "5. Skin lesions / sores\n"
                    "6. Limping / swollen joints\n"
                    "7. Sudden deaths in group\n"
                    "0. Back"
                )
        if depth == 4:
            if parts[1] == '1':
                session = USSD_SESSIONS.get(session_id, {})
                crop = session.get('crop', 'crop')
                diagnoses = {
                    '1': (
                        f"END {crop.upper()} - Yellowing\n\n"
                        "Likely: Nitrogen deficiency\n"
                        "or Maize Streak Virus\n\n"
                        "Fix: Apply CAN fertilizer\n"
                        "150kg/ha. Control\n"
                        "leafhopper insects.\n\n"
                        "More: agribrige.com"
                    ),
                    '2': (
                        f"END {crop.upper()} - Brown Spots\n\n"
                        "Likely: Fungal blight\n\n"
                        "Fix: Spray Mancozeb 80WP\n"
                        "40g per 20L water\n"
                        "every 10-14 days.\n\n"
                        "More: agribrige.com"
                    ),
                    '3': (
                        f"END {crop.upper()} - Wilting\n\n"
                        "Likely: Bacterial Wilt\n"
                        "or drought stress\n\n"
                        "Fix: Check soil moisture.\n"
                        "If roots brown: apply\n"
                        "Metalaxyl fungicide.\n\n"
                        "Call: +256 755 966 690"
                    ),
                    '4': (
                        f"END {crop.upper()} - White Powder\n\n"
                        "Likely: Powdery Mildew\n\n"
                        "Fix: Spray sulphur-based\n"
                        "fungicide. Improve air\n"
                        "circulation.\n\n"
                        "More: agribrige.com"
                    ),
                    '5': (
                        f"END {crop.upper()} - Pest Damage\n\n"
                        "Likely: Fall Armyworm\n\n"
                        "Fix: Emamectin Benzoate\n"
                        "200ml per 20L water.\n"
                        "Spray into whorl at dusk.\n"
                        "Repeat after 7 days.\n\n"
                        "More: agribrige.com"
                    ),
                    '6': (
                        f"END {crop.upper()} - Rotting\n\n"
                        "Likely: Root rot or\n"
                        "bacterial soft rot\n\n"
                        "Fix: Improve drainage.\n"
                        "Apply Metalaxyl.\n"
                        "Remove affected plants.\n\n"
                        "Call: +256 755 966 690"
                    ),
                    '7': (
                        f"END {crop.upper()} - Stunted\n\n"
                        "Likely: Nutrient deficiency\n"
                        "or CMD virus (cassava)\n\n"
                        "Fix: Soil test first.\n"
                        "Apply NPK fertilizer.\n"
                        "For CMD: plant resistant\n"
                        "varieties (NAROCAS 1).\n\n"
                        "More: agribrige.com"
                    ),
                }
                USSD_SESSIONS.pop(session_id, None)
                return diagnoses.get(last, (
                    "END For detailed diagnosis\n"
                    "visit: agribrige.com\n\n"
                    "Call: +256 755 966 690\n"
                    "Mon-Sat 7am-8pm"
                ))
            if parts[1] == '2':
                session = USSD_SESSIONS.get(session_id, {})
                animal = session.get('animal', 'animal')
                diagnoses = {
                    '1': (
                        f"END {animal.upper()} - Fever\n\n"
                        "Likely: East Coast Fever\n"
                        "or Trypanosomiasis\n\n"
                        "ACT IMMEDIATELY!\n"
                        "Call vet within 24hrs.\n"
                        "ECF: Butalex injection\n"
                        "Tick control: Amitraz\n\n"
                        "Emergency: +256 755 966 690"
                    ),
                    '2': (
                        f"END {animal.upper()} - Not Eating\n\n"
                        "Likely: Fever or pain\n\n"
                        "Check temperature.\n"
                        "Normal cattle: 38.5C\n"
                        "If above 39C: call vet\n\n"
                        "Call: +256 755 966 690\n"
                        "Book: agribrige.com/vet"
                    ),
                    '3': (
                        f"END {animal.upper()} - Diarrhoea\n\n"
                        "Likely: Coccidiosis or\n"
                        "bacterial infection\n\n"
                        "Fix: Oral rehydration.\n"
                        "Amprolium for cocci.\n"
                        "Isolate sick animals.\n\n"
                        "Call: +256 755 966 690"
                    ),
                    '4': (
                        f"END {animal.upper()} - Coughing\n\n"
                        "Likely: Pneumonia\n\n"
                        "Fix: Isolate immediately.\n"
                        "Oxytetracycline injection.\n"
                        "Call vet within 24hrs.\n\n"
                        "Emergency: +256 755 966 690"
                    ),
                    '5': (
                        f"END {animal.upper()} - Skin Lesions\n\n"
                        "Likely: Lumpy Skin Disease\n\n"
                        "LSD is NOTIFIABLE!\n"
                        "Isolate animal.\n"
                        "Contact DVO now.\n\n"
                        "Call: +256 755 966 690"
                    ),
                    '6': (
                        f"END {animal.upper()} - Limping\n\n"
                        "Likely: Foot & Mouth\n"
                        "or joint infection\n\n"
                        "FMD is NOTIFIABLE!\n"
                        "Report to DVO now.\n"
                        "Isolate all cattle.\n\n"
                        "AgriBridge: +256 755 966 690"
                    ),
                    '7': (
                        f"END {animal.upper()} - EMERGENCY!\n\n"
                        "Sudden deaths likely:\n"
                        "Anthrax or Newcastle\n\n"
                        "DO NOT touch dead animals!\n"
                        "Call DVO IMMEDIATELY.\n"
                        "Quarantine the farm.\n\n"
                        "Emergency: +256 755 966 690"
                    ),
                }
                USSD_SESSIONS.pop(session_id, None)
                return diagnoses.get(last, (
                    "END For detailed diagnosis\n"
                    "visit: agribrige.com\n\n"
                    "Emergency vet:\n"
                    "+256 755 966 690 (24/7)"
                ))

    # ── 7. REGISTER ───────────────────────────────────────────────────────────
    if m == '7':
        if depth == 1:
            return (
                "CON Register on AgriBridge:\n\n"
                "1. Register as Crop Farmer\n"
                "2. Register as Livestock Farmer\n"
                "3. Register as Buyer/Vendor\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            types = {
                '1': 'Crop Farmer',
                '2': 'Livestock Farmer',
                '3': 'Buyer/Vendor'
            }
            role = types.get(last, 'Farmer')
            return (
                f"END Register as {role}\n\n"
                f"Your number: {phone}\n\n"
                "OPTION 1 - Website:\n"
                "agribrige.com -> Join Free\n\n"
                "OPTION 2 - SMS:\n"
                "Send: REG [Name] [District]\n"
                "To: 789\n\n"
                "OPTION 3 - Call:\n"
                "+256 755 966 690\n\n"
                "Registration is FREE!"
            )

    # ── 8. SUPPORT ────────────────────────────────────────────────────────────
    if m == '8':
        if depth == 1:
            return (
                "CON Support & My Account:\n\n"
                "1. My Listings\n"
                "2. My Orders\n"
                "3. Set Price Alert\n"
                "4. Contact Support\n"
                "5. About AgriBridge\n"
                "0. Main Menu"
            )
        if depth == 2:
            if last == '0':
                return ussd_route([], 0, '', session_id, phone)
            if last == '4':
                return (
                    "END Contact AgriBridge:\n\n"
                    "Phone/WhatsApp:\n"
                    "+256 755 966 690\n\n"
                    "Email:\n"
                    "hello@agribridge.ug\n\n"
                    "Website: agribrige.com\n\n"
                    "Hours: Mon-Sat 7am-8pm\n"
                    "Emergency vet: 24/7"
                )
            if last == '5':
                return (
                    "END About AgriBridge\n\n"
                    "Uganda's #1 Farm-to-Table\n"
                    "Platform\n\n"
                    "5,000+ farmers connected\n"
                    "directly to buyers with\n"
                    "zero middlemen.\n\n"
                    "Crops AND Animals.\n"
                    "Works on ANY phone.\n\n"
                    "agribrige.com"
                )
            if last == '3':
                return (
                    "END Set Price Alert:\n\n"
                    "Send SMS to 789:\n"
                    "ALERT [crop] [price]\n\n"
                    "Example:\n"
                    "ALERT MAIZE 1000\n\n"
                    "We SMS you when maize\n"
                    "exceeds UGX 1,000/kg.\n"
                    "Free SMS alerts!"
                )
            if last == '1':
                listings = supa_get(
                    'listings',
                    {'farmer_phone': f'eq.{phone}', 'order': 'created_at.desc'},
                    limit=3
                ) or []
                if listings:
                    lines = "\n".join(
                        f"{l.get('crop_name','?')} "
                        f"{int(l.get('quantity_kg', 0))}kg "
                        f"UGX{int(l.get('price_per_kg', 0)):,}"
                        for l in listings[:3]
                    )
                    return f"END Your Listings:\n\n{lines}\n\nManage: agribrige.com"
                return (
                    "END No listings found.\n\n"
                    "List produce free:\n"
                    "agribrige.com\n"
                    "Or dial back & choose 4"
                )
            if last == '2':
                orders = supa_get(
                    'orders',
                    {'buyer_phone': f'eq.{phone}', 'order': 'created_at.desc'},
                    limit=3
                ) or []
                if orders:
                    lines = "\n".join(
                        f"#{str(o.get('id', '?'))[-4:]} "
                        f"{o.get('status', 'pending').upper()}"
                        for o in orders[:3]
                    )
                    return f"END Your Recent Orders:\n\n{lines}\n\nTrack: agribrige.com"
                return (
                    "END No orders found.\n\n"
                    "Browse marketplace:\n"
                    "agribrige.com\n"
                    "Or call: +256 755 966 690"
                )

    # ── FALLBACK ──────────────────────────────────────────────────────────────
    return (
        "END Oops! Invalid option.\n\n"
        "Dial *789# to start again.\n\n"
        "Need help?\n"
        "Call: +256 755 966 690\n"
        "Web: agribrige.com"
    )


@app.route('/api/ussd', methods=['POST'])
def ussd():
    session_id = request.form.get('sessionId',   '')
    phone      = request.form.get('phoneNumber', '')
    text       = request.form.get('text',        '').strip()

    parts = [p.strip() for p in text.split('*') if p.strip()] if text else []
    depth = len(parts)
    last  = parts[-1] if parts else ''

    response_text = ussd_route(parts, depth, last, session_id, phone)

    # Log to Supabase analytics (best-effort)
    try:
        supa_insert('ussd_sessions', {
            'session_id': session_id,
            'phone':      phone,
            'text':       text,
            'response':   response_text[:200],
        })
    except Exception:
        pass

    return Response(response_text, mimetype='text/plain')


# ══════════════════════════════════════════════════════════════════════════════
# SMS DELIVERY WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/sms/delivery', methods=['POST'])
def sms_delivery():
    data = request.form.to_dict()
    print(f"SMS Delivery: {data}")
    return 'OK', 200

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    farmers  = supa_get('farmers',         limit=1000) or []
    listings = supa_get('listings',        limit=1000) or []
    orders   = supa_get('orders',          limit=1000) or []
    animals  = supa_get('animal_listings', limit=1000) or []
    return jsonify({
        'farmers':     len(farmers),
        'listings':    len(listings),
        'orders':      len(orders),
        'animals':     len(animals),
        'revenue_ugx': sum(
            float(o.get('total_price', 0)) for o in orders
            if o.get('payment_status') == 'paid'
        )
    })

@app.route('/api/admin/listings', methods=['GET'])
def admin_listings():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify(supa_get('listings', limit=200) or [])

@app.route('/api/admin/orders', methods=['GET'])
def admin_orders():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify(supa_get('orders', limit=200) or [])

@app.route('/api/admin/farmers', methods=['GET'])
def admin_farmers():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify(supa_get('farmers', limit=500) or [])

@app.route('/api/admin/order/<order_id>/status', methods=['PATCH'])
def update_order_status(order_id):
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    data = request.get_json(force=True) or {}
    new_status = data.get('status')
    valid = ('pending', 'confirmed', 'in_transit', 'delivered', 'cancelled')
    if new_status not in valid:
        return jsonify({'error': 'Invalid status'}), 400
    ok = supa_update('orders', {'status': new_status}, 'id', order_id)
    if ok:
        return jsonify({'message': f'Order {order_id} updated to {new_status}'}), 200
    return jsonify({'error': 'Update failed'}), 500

@app.route('/api/admin/animals', methods=['GET'])
def admin_animals():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify(supa_get('animal_listings', {'order': 'created_at.desc'}, limit=300) or [])

@app.route('/api/admin/deliveries', methods=['GET'])
def admin_deliveries():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify(supa_get('deliveries', {'order': 'created_at.desc'}, limit=300) or [])

@app.route('/api/admin/supplies', methods=['GET'])
def admin_supplies():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify(supa_get('supplier_products', {'order': 'category.asc'}, limit=300) or [])

# Generic admin editor for the console. Only whitelisted tables + columns can be
# written, so the admin token cannot set protected fields (id, timestamps, ownership).
_ADMIN_COLS = {
    'listings':        ['crop_name', 'category', 'quantity_kg', 'price_per_kg', 'unit', 'district',
                        'is_organic', 'is_available', 'is_verified', 'description', 'discount_pct',
                        'sale_unit', 'min_order_kg', 'delivery_available', 'payment_terms',
                        'image_url', 'video_url', 'farmer_id', 'farmer_phone'],
    'animal_listings': ['name', 'species', 'category', 'price', 'unit', 'qty', 'district',
                        'description', 'health_cert', 'maaif_certified', 'status',
                        'image_url', 'video_url', 'farmer_id', 'farmer_name', 'farmer_phone'],
    'farmers':         ['full_name', 'phone', 'email', 'district', 'is_verified', 'is_premium'],
    'orders':          ['status', 'payment_status', 'delivery_address', 'notes'],
    'market_prices':   ['crop_name', 'price', 'unit', 'district', 'market'],
    'deliveries':      ['status', 'driver_name', 'driver_phone', 'pickup_location',
                        'delivery_location', 'estimated_arrival', 'tracking_code', 'notes'],
    'supplier_products': ['category', 'name', 'brand', 'price', 'unit', 'badge',
                          'description', 'image_id', 'in_stock'],
}

def _filter_cols(table, data):
    allowed = _ADMIN_COLS.get(table, [])
    return {k: v for k, v in (data or {}).items() if k in allowed}

@app.route('/api/admin/row', methods=['POST', 'PATCH', 'DELETE'])
def admin_row():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    table = data.get('table')
    if table not in _ADMIN_COLS:
        return jsonify({'error': 'Table not allowed'}), 400

    if request.method == 'DELETE':
        row_id = data.get('id')
        if not row_id:
            return jsonify({'error': 'Missing id'}), 400
        return (jsonify({'ok': True}), 200) if supa_delete(table, 'id', row_id) \
            else (jsonify({'error': 'Delete failed'}), 500)

    fields = _filter_cols(table, data.get('fields'))
    if not fields:
        return jsonify({'error': 'No editable fields provided'}), 400

    if request.method == 'POST':
        row = supa_insert(table, fields)
        return (jsonify({'ok': True, 'row': row}), 200) if row else (jsonify({'error': 'Create failed'}), 500)

    # PATCH
    row_id = data.get('id')
    if not row_id:
        return jsonify({'error': 'Missing id'}), 400
    return (jsonify({'ok': True}), 200) if supa_update(table, fields, 'id', row_id) \
        else (jsonify({'error': 'Update failed'}), 500)

# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC PRICES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/prices', methods=['GET'])
def get_prices():
    data = supa_get('price_data', {'order': 'updated_at.desc'}, limit=50)
    return jsonify(data or [])

# ══════════════════════════════════════════════════════════════════════════════
# AI CROP DOCTOR
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/crop-doctor', methods=['POST'])
@rate_limit(max_req=15, window=300)
def crop_doctor():
    data        = request.get_json(force=True, silent=True) or {}
    description = data.get('description', '').strip()
    crop        = data.get('crop', 'crop')
    if not description:
        return jsonify({'error': 'Provide a description of the problem'}), 400
    if not GEMINI_KEY:
        return jsonify({
            'diagnosis':  'AI crop doctor is not configured on this server yet.',
            'treatment':  'Contact your nearest NAADS extension officer for advice.',
            'prevention': 'Keep records of your crop health to spot patterns early.'
        })
    prompt = (
        f"You are an expert agronomist specialising in Uganda and East Africa. "
        f"A farmer reports this problem with their {crop}: \"{description}\". "
        f"Respond ONLY in valid JSON with keys: diagnosis, treatment, prevention, confidence (0-100). "
        f"Keep each value under 150 words. "
        f"Use practical advice suitable for small-scale Uganda farmers."
    )
    try:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            f"?key={GEMINI_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=15
        )
        text = res.json()['candidates'][0]['content']['parts'][0]['text']
        text = text.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        return jsonify(_json.loads(text))
    except Exception as e:
        return jsonify({'error': 'AI service temporarily unavailable'}), 503

# ══════════════════════════════════════════════════════════════════════════════
# AI ASSISTANT (Groq) — key stays server-side in GROQ_API_KEY
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/ai', methods=['POST'])
@rate_limit(max_req=20, window=300)
def ai_chat():
    data = request.get_json(force=True, silent=True) or {}
    msg = (data.get('message') or '').strip()[:1500]
    if not msg:
        return jsonify({'reply': None, 'error': 'empty message'}), 400
    key = os.environ.get('GROQ_API_KEY', '')
    if not key:
        # Not configured — let the client fall back to its offline helper.
        return jsonify({'reply': None, 'error': 'AI not configured'}), 200
    system = (
        "You are the AgriBridge assistant for Ugandan smallholder farmers and buyers. "
        "AgriBridge connects farmers directly to buyers: a marketplace for crops AND livestock, "
        "live market prices, AI crop & animal disease help, USSD *789# for basic phones, "
        "mobile money (MTN MoMo, Airtel Money) and GPS delivery. "
        "Answer concisely, practically and warmly. Reply in the user's language if they write "
        "Luganda or Swahili. Give real, useful farming and market advice for Uganda. "
        "Keep replies under 180 words. When it clearly helps, you may add ONE action tag on its "
        "own final line, chosen from: [NAVIGATE:market] [NAVIGATE:prices] [NAVIGATE:animals] "
        "[NAVIGATE:doctor] [NAVIGATE:delivery] [NAVIGATE:training] [NAVIGATE:dashboard] [OPEN_BASKET]."
    )
    try:
        res = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': msg},
                ],
                'temperature': 0.4,
                'max_tokens': 500,
            },
            timeout=25,
        )
        if res.ok:
            reply = res.json()['choices'][0]['message']['content'].strip()
            return jsonify({'reply': reply}), 200
        return jsonify({'reply': None, 'error': 'ai upstream ' + str(res.status_code)}), 200
    except Exception:
        return jsonify({'reply': None, 'error': 'ai unavailable'}), 200


@app.route('/api/notify-order', methods=['POST'])
@rate_limit(max_req=30, window=300)
def notify_order():
    """Best-effort SMS to a farmer when a new order comes in. Looks up the farmer's
    phone server-side (never trusts the client for it). Always returns ok so the
    order flow is never blocked; SMS only actually sends once Africa's Talking is
    live (in sandbox this is a no-op)."""
    data = request.get_json(force=True, silent=True) or {}
    farmer_id = (data.get('farmer_id') or '').strip()
    item = (data.get('item') or 'produce').strip()[:60]
    qty = (data.get('qty') or '').strip()[:20]
    if not at_sms or not farmer_id:
        return jsonify({'ok': True, 'sent': False}), 200
    rows = supa_get('farmers', filters={'id': f'eq.{farmer_id}', 'select': 'phone'}, limit=1)
    phone = (rows[0].get('phone') if rows else '') or ''
    if not phone:
        return jsonify({'ok': True, 'sent': False}), 200
    try:
        at_sms.send(
            message=(f"AgriBridge: New order!\n{qty} {item}. "
                     f"Log in to confirm: agribrige.com"),
            recipients=[phone],
            sender_id=AT_SMS_SENDER,
        )
        return jsonify({'ok': True, 'sent': True}), 200
    except Exception as e:
        print(f"notify-order SMS error: {e}")
        return jsonify({'ok': True, 'sent': False}), 200


def _sms_receipt(phone, ref, total, kind='order'):
    """Best-effort SMS receipt to a buyer. No-op unless Africa's Talking is live."""
    if not at_sms or not phone:
        return False
    try:
        head = 'Payment received' if kind == 'payment' else 'Order confirmed'
        msg = (f"AgriBridge: {head}!\n"
               f"Ref {ref}. Total UGX {int(total or 0):,}.\n"
               f"Track at agribrige.com")
        at_sms.send(message=msg, recipients=[phone], sender_id=AT_SMS_SENDER)
        return True
    except Exception as e:
        print(f"sms receipt error: {e}")
        return False


@app.route('/api/sms-receipt', methods=['POST'])
@rate_limit(max_req=30, window=300)
def sms_receipt():
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get('phone') or '').strip()[:20]
    ref   = (data.get('ref')   or '').strip()[:40]
    kind  = (data.get('kind')  or 'order').strip()[:20]
    try:
        total = int(float(data.get('total') or 0))
    except Exception:
        total = 0
    return jsonify({'ok': True, 'sent': _sms_receipt(phone, ref, total, kind)}), 200


# ══════════════════════════════════════════════════════════════════════════════
# PAYMENTS  (pluggable; providers are OFF until their env keys are set, so this
# never affects the live checkout until you deliberately enable one)
# ══════════════════════════════════════════════════════════════════════════════
def _enabled_providers():
    return {
        'cod':         True,   # cash on delivery — no gateway needed
        'direct':      True,   # pay the farmer's MoMo directly — no gateway needed
        'flutterwave': bool(FLW_SECRET_KEY),
        'pesapal':     bool(PESAPAL_KEY and PESAPAL_SECRET),
        'mtn_momo':    bool(MTN_MOMO_KEY),
    }


@app.route('/api/pay/providers', methods=['GET'])
def pay_providers():
    """The client asks which online-payment methods are live so it only shows
    those; when none are configured it falls back to COD / direct MoMo."""
    return jsonify({'providers': _enabled_providers()}), 200


@app.route('/api/pay/initiate', methods=['POST'])
@rate_limit(max_req=20, window=300)
def pay_initiate():
    data = request.get_json(force=True, silent=True) or {}
    provider  = (data.get('provider')  or '').strip().lower()
    order_ref = (data.get('order_ref') or '').strip()[:60]
    email     = (data.get('email')     or '').strip()[:120]
    phone     = (data.get('phone')     or '').strip()[:20]
    try:
        amount = int(float(data.get('amount')))
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid amount'}), 400
    if amount <= 0 or not order_ref:
        return jsonify({'ok': False, 'error': 'missing order_ref or amount'}), 400

    if provider in ('cod', 'direct'):
        return jsonify({'ok': True, 'mode': provider, 'message': 'No online payment needed.'}), 200
    if provider == 'flutterwave':
        return _flw_initiate(order_ref, amount, email, phone)
    if provider in ('pesapal', 'mtn_momo'):
        # Structured for completion against the provider's sandbox once keys exist.
        return jsonify({'ok': False, 'enabled': False, 'error': provider + ' not configured yet'}), 200
    return jsonify({'ok': False, 'error': 'unknown provider'}), 400


def _flw_initiate(order_ref, amount, email, phone):
    if not FLW_SECRET_KEY:
        return jsonify({'ok': False, 'enabled': False, 'error': 'flutterwave not configured'}), 200
    try:
        res = requests.post(
            'https://api.flutterwave.com/v3/payments',
            headers={'Authorization': 'Bearer ' + FLW_SECRET_KEY, 'Content-Type': 'application/json'},
            json={
                'tx_ref':       order_ref,
                'amount':       str(amount),
                'currency':     'UGX',
                'redirect_url': PUBLIC_BASE_URL + '/?pay=done',
                'customer':     {'email': email or 'buyer@agribrige.com', 'phonenumber': phone},
                'customizations': {'title': 'AgriBridge', 'description': 'Order ' + order_ref},
                'payment_options': 'mobilemoneyuganda,card',
            },
            timeout=25,
        )
        j = res.json() if res.ok else {}
        link = ((j.get('data') or {}).get('link')) if isinstance(j, dict) else None
        if link:
            return jsonify({'ok': True, 'provider': 'flutterwave', 'link': link}), 200
        return jsonify({'ok': False, 'error': 'could not start payment'}), 200
    except Exception as e:
        print(f"flutterwave initiate error: {e}")
        return jsonify({'ok': False, 'error': 'payment gateway unavailable'}), 200


@app.route('/api/pay/webhook/flutterwave', methods=['POST'])
def pay_webhook_flw():
    # Only trust calls carrying the secret hash we configured in the FLW dashboard.
    sig = request.headers.get('verif-hash', '')
    if not FLW_WEBHOOK_HASH or not sig or not hmac.compare_digest(sig, FLW_WEBHOOK_HASH):
        return jsonify({'status': 'unauthorized'}), 401
    data = request.get_json(force=True, silent=True) or {}
    ev = data.get('data') or {}
    tx_ref = ev.get('tx_ref') or ''
    flw_id = ev.get('id')
    if (ev.get('status') or '').lower() == 'successful' and tx_ref and flw_id:
        # Never trust the webhook body alone — re-verify with Flutterwave server-side.
        try:
            v = requests.get(
                'https://api.flutterwave.com/v3/transactions/' + str(flw_id) + '/verify',
                headers={'Authorization': 'Bearer ' + FLW_SECRET_KEY}, timeout=20)
            vj = v.json() if v.ok else {}
            vdata = vj.get('data') or {}
            if (vdata.get('status') or '').lower() == 'successful':
                # Mark every order row of this checkout paid (they share payment_ref).
                # Idempotent: re-running just re-sets 'paid'; the match key is stable.
                supa_update('orders', {'payment_status': 'paid'}, 'payment_ref', tx_ref)
                _send_payment_receipt(tx_ref)
        except Exception as e:
            print(f"flw verify error: {e}")
    return jsonify({'status': 'ok'}), 200


def _send_payment_receipt(payment_ref):
    """Look up the paid order's buyer + items and email a receipt via the
    send-receipt Edge Function. Best-effort; never raises to the webhook."""
    try:
        orows = supa_get('orders', filters={
            'payment_ref': f'eq.{payment_ref}',
            'select': 'buyer_id,buyer_phone,quantity_kg,price_per_kg,total_price,tracking_code,notes',
        })
        if not orows:
            return
        buyer_id = orows[0].get('buyer_id')
        total = sum(float(o.get('total_price') or 0) for o in orows)
        tracking = orows[0].get('tracking_code') or ''
        _sms_receipt(orows[0].get('buyer_phone'), payment_ref, total, 'payment')
        email, name = '', 'there'
        if buyer_id:
            frows = supa_get('farmers', filters={'id': f'eq.{buyer_id}', 'select': 'email,full_name'}, limit=1)
            if frows:
                email = frows[0].get('email') or ''
                name = frows[0].get('full_name') or 'there'
        if not email:
            return
        items = [{
            'name': ((o.get('notes') or '').split('Item:')[-1].strip() or 'Order'),
            'qty': o.get('quantity_kg'),
            'price': o.get('price_per_kg'),
        } for o in orows]
        requests.post(
            SUPABASE_URL + '/functions/v1/send-receipt',
            headers={'Authorization': f'Bearer {SUPABASE_KEY}', 'Content-Type': 'application/json'},
            json={'type': 'payment', 'email': email, 'name': name, 'ref': payment_ref,
                  'total': total, 'tracking': tracking, 'items': items},
            timeout=15,
        )
    except Exception as e:
        print(f"payment receipt email error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    print(f"AgriBridge API v3 starting on port {port} (debug={debug})")
    app.run(host='0.0.0.0', port=port, debug=debug)
