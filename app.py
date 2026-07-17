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
CORS(app, resources={r"/api/*": {"origins": "*"}})

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

if AT_AVAILABLE and AT_API_KEY and AT_API_KEY != 'atsk_REPLACE_ME':
    try:
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        at_sms = africastalking.SMS
    except Exception as _at_err:
        print(f"WARNING: Africa's Talking init failed: {_at_err}")
        at_sms = None
else:
    at_sms = None

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
def admin_login():
    data = request.get_json(force=True) or {}
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
                f"More: agribridge.com"
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
                f"Buy/sell: agribridge.com\n"
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
                    "Web: agribridge.com/bulk\n\n"
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
                "agribridge.com\n\n"
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
                            f"Buyers will contact you. agribridge.com"
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
                    "More: agribridge.com/training"
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
                    "Book: agribridge.com/vet"
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
                    "Full info: agribridge.com"
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
                    "Book: agribridge.com/vet"
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
                        "More: agribridge.com"
                    ),
                    '2': (
                        f"END {crop.upper()} - Brown Spots\n\n"
                        "Likely: Fungal blight\n\n"
                        "Fix: Spray Mancozeb 80WP\n"
                        "40g per 20L water\n"
                        "every 10-14 days.\n\n"
                        "More: agribridge.com"
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
                        "More: agribridge.com"
                    ),
                    '5': (
                        f"END {crop.upper()} - Pest Damage\n\n"
                        "Likely: Fall Armyworm\n\n"
                        "Fix: Emamectin Benzoate\n"
                        "200ml per 20L water.\n"
                        "Spray into whorl at dusk.\n"
                        "Repeat after 7 days.\n\n"
                        "More: agribridge.com"
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
                        "More: agribridge.com"
                    ),
                }
                USSD_SESSIONS.pop(session_id, None)
                return diagnoses.get(last, (
                    "END For detailed diagnosis\n"
                    "visit: agribridge.com\n\n"
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
                        "Book: agribridge.com/vet"
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
                    "visit: agribridge.com\n\n"
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
                "agribridge.com -> Join Free\n\n"
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
                    "Website: agribridge.com\n\n"
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
                    "agribridge.com"
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
                    return f"END Your Listings:\n\n{lines}\n\nManage: agribridge.com"
                return (
                    "END No listings found.\n\n"
                    "List produce free:\n"
                    "agribridge.com\n"
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
                    return f"END Your Recent Orders:\n\n{lines}\n\nTrack: agribridge.com"
                return (
                    "END No orders found.\n\n"
                    "Browse marketplace:\n"
                    "agribridge.com\n"
                    "Or call: +256 755 966 690"
                )

    # ── FALLBACK ──────────────────────────────────────────────────────────────
    return (
        "END Oops! Invalid option.\n\n"
        "Dial *789# to start again.\n\n"
        "Need help?\n"
        "Call: +256 755 966 690\n"
        "Web: agribridge.com"
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
def crop_doctor():
    data        = request.get_json(force=True) or {}
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
        return jsonify({'error': 'AI service unavailable', 'detail': str(e)}), 503

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    print(f"AgriBridge API v3 starting on port {port} (debug={debug})")
    app.run(host='0.0.0.0', port=port, debug=debug)
