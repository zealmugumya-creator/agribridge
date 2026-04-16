# ═══════════════════════════════════════════════════════════════════════════
# AgriBridge Flask Backend — Production-Ready Version
# Deploy on Render (https://render.com) as a Web Service
# ═══════════════════════════════════════════════════════════════════════════
# REQUIRED ENVIRONMENT VARIABLES (set in Render Dashboard → Environment):
#   JWT_SECRET        — a long random string, e.g. openssl rand -hex 32
#   ADMIN_PASSWORD    — your admin panel password (e.g. agribridge2026)
#   AT_USERNAME       — Africa's Talking username (default: sandbox)
#   AT_API_KEY        — Africa's Talking API key from their dashboard
#   AT_SHORTCODE      — Africa's Talking USSD shortcode (e.g. *789#)
#   AT_SMS_SENDER     — Africa's Talking SMS sender ID or shortcode
#   SUPABASE_URL      — https://vyrctsiyaihsysgpozdm.supabase.co
#   SUPABASE_KEY      — Your Supabase service_role key (NOT anon key)
# ═══════════════════════════════════════════════════════════════════════════

import os
import datetime
import hashlib
import requests

import jwt
from flask import Flask, request, jsonify
from flask_cors import CORS

# ── Africa's Talking ─────────────────────────────────────────────────────────
try:
    import africastalking
    AT_AVAILABLE = True
except ImportError:
    AT_AVAILABLE = False
    print("WARNING: africastalking package not installed. USSD and SMS will be disabled.")
    print("  Fix: pip install africastalking")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── Config from environment ───────────────────────────────────────────────────
JWT_SECRET     = os.environ.get('JWT_SECRET',     'CHANGE_ME_IN_RENDER_ENV_VARS')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'agribridge2026')
AT_USERNAME    = os.environ.get('AT_USERNAME',    'sandbox')
AT_API_KEY     = os.environ.get('AT_API_KEY',     'atsk_REPLACE_ME')
AT_SHORTCODE   = os.environ.get('AT_SHORTCODE',   '*789#')
AT_SMS_SENDER  = os.environ.get('AT_SMS_SENDER',  'AgriBridge')
SUPABASE_URL   = os.environ.get('SUPABASE_URL',   'https://vyrctsiyaihsysgpozdm.supabase.co')
SUPABASE_KEY   = os.environ.get('SUPABASE_KEY',   '')  # service_role key

# ── Initialise Africa's Talking ───────────────────────────────────────────────
if AT_AVAILABLE and AT_API_KEY != 'atsk_REPLACE_ME':
    africastalking.initialize(AT_USERNAME, AT_API_KEY)
    at_sms = africastalking.SMS()
else:
    at_sms = None

# ── Supabase helper (server-side, uses service_role key) ─────────────────────
def supa_get(table, filters=None, limit=100):
    """Read rows from a Supabase table."""
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
    """Insert a row into a Supabase table."""
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
    """Update rows in a Supabase table."""
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
    """Decode and verify a JWT from the Authorization header.
    Returns (payload, None) on success or (None, error_response) on failure."""
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
        return None, (jsonify({'error': 'Token expired — please log in again'}), 401)
    except jwt.InvalidTokenError:
        return None, (jsonify({'error': 'Invalid token'}), 401)

# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/')
@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'AgriBridge API',
        'version': '2.0.0',
        'at_enabled': AT_AVAILABLE and at_sms is not None,
        'supabase_connected': bool(SUPABASE_KEY)
    })

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN AUTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """Admin login — returns a JWT valid for 24 hours.

    Body: { "password": "your_admin_password" }
    Response: { "token": "...", "role": "admin", "message": "Login successful" }
    """
    data = request.get_json(force=True) or {}
    password = data.get('password', '')
    if not password or password != ADMIN_PASSWORD:
        return jsonify({'error': 'Invalid password'}), 401
    token = make_token({'sub': 'admin', 'role': 'admin'}, hours=24)
    return jsonify({'token': token, 'role': 'admin', 'message': 'Login successful'}), 200


@app.route('/api/admin/verify', methods=['GET'])
def admin_verify():
    """Check if an admin token is still valid."""
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    return jsonify({'valid': True, 'sub': payload.get('sub')}), 200

# ══════════════════════════════════════════════════════════════════════════════
# USSD HANDLER  (*789#)
# ══════════════════════════════════════════════════════════════════════════════
USSD_SESSIONS = {}  # In-memory; resets on redeploy — use Redis in production

@app.route('/api/ussd', methods=['POST'])
def ussd():
    session_id   = request.form.get('sessionId', '')
    phone        = request.form.get('phoneNumber', '')
    text         = request.form.get('text', '')
    service_code = request.form.get('serviceCode', AT_SHORTCODE)

    parts   = [p.strip() for p in text.split('*')] if text else ['']
    level   = len([p for p in parts if p])  # depth in menu
    last    = parts[-1] if parts else ''

    # ── Level 0: Main menu ──────────────────────────────────────────────────
    if text == '':
        resp = (
            "CON Welcome to AgriBridge 🌾\n"
            "Uganda's Farm-to-Table Platform\n\n"
            "1. Sell My Crops\n"
            "2. Browse Buyers\n"
            "3. Check Crop Prices\n"
            "4. My Orders\n"
            "5. Training & Tips\n"
            "0. Exit"
        )
    # ── Sell My Crops ────────────────────────────────────────────────────────
    elif text == '1':
        resp = (
            "CON Sell Crops — Select Crop:\n"
            "1. Maize\n"
            "2. Beans\n"
            "3. Tomatoes\n"
            "4. Coffee\n"
            "5. Bananas\n"
            "6. Other\n"
            "0. Back"
        )
    elif text.startswith('1*') and level == 2 and last not in ('0',):
        crop_map = {'1':'Maize','2':'Beans','3':'Tomatoes','4':'Coffee','5':'Bananas','6':'Other'}
        crop = crop_map.get(last, 'Crop')
        USSD_SESSIONS[session_id] = {'crop': crop, 'phone': phone}
        resp = f"CON {crop} selected.\nEnter quantity in kg (e.g. 500):"
    elif text.startswith('1*') and level == 3:
        session = USSD_SESSIONS.get(session_id, {})
        qty = last
        crop = session.get('crop', 'Crop')
        session['qty'] = qty
        USSD_SESSIONS[session_id] = session
        resp = f"CON {qty}kg {crop}.\nEnter price per kg in UGX (e.g. 800):"
    elif text.startswith('1*') and level == 4:
        session = USSD_SESSIONS.get(session_id, {})
        price = last
        crop  = session.get('crop', 'Crop')
        qty   = session.get('qty', '?')
        # Save to Supabase
        listing_data = {
            'crop_name':    crop,
            'quantity_kg':  float(qty) if qty.replace('.','').isdigit() else 0,
            'price_per_kg': float(price) if price.replace('.','').isdigit() else 0,
            'farmer_phone': phone,
            'is_available': True,
            'source':       'ussd'
        }
        supa_insert('listings', listing_data)
        # SMS confirmation
        if at_sms:
            try:
                at_sms.send(
                    message=f"AgriBridge: Listing posted!\n{qty}kg {crop} @ UGX {price}/kg.\nBuyers will contact you shortly.",
                    recipients=[phone],
                    sender_id=AT_SMS_SENDER
                )
            except Exception as e:
                print(f"SMS error: {e}")
        resp = (
            f"END Listing posted!\n"
            f"{qty}kg {crop} @ UGX {price}/kg.\n"
            f"Buyers will contact you.\n"
            f"Thank you for using AgriBridge!"
        )
        USSD_SESSIONS.pop(session_id, None)
    # ── Browse Buyers ────────────────────────────────────────────────────────
    elif text == '2':
        buyers = supa_get('buyers', limit=5) or []
        if buyers:
            lines = "\n".join(
                f"{i+1}. {b.get('full_name','Buyer')} — {b.get('district','UG')}"
                for i, b in enumerate(buyers[:5])
            )
            resp = f"CON Active Buyers:\n{lines}\n0. Back"
        else:
            resp = "CON No buyers registered yet.\nCheck again tomorrow.\n0. Back"
    # ── Crop Prices ──────────────────────────────────────────────────────────
    elif text == '3':
        prices = supa_get('price_data', {'order': 'updated_at.desc', 'limit': '5'}) or []
        if prices:
            lines = "\n".join(
                f"{p.get('crop_name','?')}: UGX {p.get('price_per_kg','?')}/kg — {p.get('market','?')}"
                for p in prices[:5]
            )
            resp = f"END Today's Prices:\n{lines}"
        else:
            resp = (
                "END Today's Market Prices:\n"
                "Maize: UGX 700/kg — Kampala\n"
                "Beans: UGX 2,500/kg — Jinja\n"
                "Tomatoes: UGX 1,200/kg — Kampala\n"
                "Coffee: UGX 8,000/kg — Mbarara\n"
                "Bananas: UGX 400/kg — Kampala"
            )
    # ── My Orders ────────────────────────────────────────────────────────────
    elif text == '4':
        orders = supa_get('orders', {'buyer_phone': f'eq.{phone}', 'order': 'created_at.desc', 'limit': '3'}) or []
        if orders:
            lines = "\n".join(
                f"#{str(o.get('id','?'))[-4:]} — {o.get('status','pending').upper()}"
                for o in orders[:3]
            )
            resp = f"END Your Recent Orders:\n{lines}"
        else:
            resp = "END No orders found for this number.\nVisit agribrige.com to place orders."
    # ── Training Tips ────────────────────────────────────────────────────────
    elif text == '5':
        resp = (
            "END AgriBridge Farming Tip:\n"
            "Use hermetic bags (PICS bags) to\n"
            "store maize — stops weevils for 6+\n"
            "months without chemicals.\n"
            "Visit agribrige.com for video guides."
        )
    # ── Exit ─────────────────────────────────────────────────────────────────
    elif last == '0':
        resp = "END Thank you for using AgriBridge!\nDial *789# anytime."
    else:
        resp = "CON Invalid option.\n0. Back to Main Menu"

    return resp, 200, {'Content-Type': 'text/plain'}

# ══════════════════════════════════════════════════════════════════════════════
# SMS WEBHOOK (Africa's Talking delivery reports)
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/sms/delivery', methods=['POST'])
def sms_delivery():
    data = request.form.to_dict()
    print(f"SMS Delivery: {data}")
    return 'OK', 200

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — DASHBOARD DATA
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    farmers  = supa_get('farmers',  limit=1000) or []
    buyers   = supa_get('buyers',   limit=1000) or []
    listings = supa_get('listings', limit=1000) or []
    orders   = supa_get('orders',   limit=1000) or []
    return jsonify({
        'farmers':  len(farmers),
        'buyers':   len(buyers),
        'listings': len(listings),
        'orders':   len(orders),
        'revenue_ugx': sum(float(o.get('total_price', 0)) for o in orders if o.get('payment_status') == 'paid')
    })

@app.route('/api/admin/listings', methods=['GET'])
def admin_listings():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    data = supa_get('listings', limit=200)
    return jsonify(data or [])

@app.route('/api/admin/orders', methods=['GET'])
def admin_orders():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    data = supa_get('orders', limit=200)
    return jsonify(data or [])

@app.route('/api/admin/farmers', methods=['GET'])
def admin_farmers():
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    data = supa_get('farmers', limit=500)
    return jsonify(data or [])

@app.route('/api/admin/order/<order_id>/status', methods=['PATCH'])
def update_order_status(order_id):
    payload, err = verify_token(required_role='admin')
    if err:
        return err
    data = request.get_json(force=True) or {}
    new_status = data.get('status')
    if new_status not in ('pending', 'confirmed', 'in_transit', 'delivered', 'cancelled'):
        return jsonify({'error': 'Invalid status'}), 400
    ok = supa_update('orders', {'status': new_status}, 'id', order_id)
    if ok:
        return jsonify({'message': f'Order {order_id} updated to {new_status}'}), 200
    return jsonify({'error': 'Update failed'}), 500

# ══════════════════════════════════════════════════════════════════════════════
# PRICE DATA (public)
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/prices', methods=['GET'])
def get_prices():
    data = supa_get('price_data', {'order': 'updated_at.desc'}, limit=50)
    return jsonify(data or [])

# ══════════════════════════════════════════════════════════════════════════════
# AI CROP DOCTOR  (proxied — keeps API key server-side)
# ══════════════════════════════════════════════════════════════════════════════
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')

@app.route('/api/crop-doctor', methods=['POST'])
def crop_doctor():
    data = request.get_json(force=True) or {}
    description = data.get('description', '').strip()
    crop        = data.get('crop', 'crop')
    if not description:
        return jsonify({'error': 'Provide a description of the problem'}), 400
    if not GEMINI_KEY:
        return jsonify({
            'diagnosis': 'AI crop doctor is not configured on this server yet.',
            'treatment': 'Contact your nearest NAADS extension officer for advice.',
            'prevention': 'Keep records of your crop health to spot patterns early.'
        })
    prompt = (
        f"You are an expert agronomist specialising in Uganda and East Africa. "
        f"A farmer reports this problem with their {crop}: \"{description}\". "
        f"Respond ONLY in valid JSON with keys: diagnosis, treatment, prevention, confidence (0-100). "
        f"Keep each value under 150 words. Use practical advice suitable for small-scale Uganda farmers."
    )
    try:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=15
        )
        text = res.json()['candidates'][0]['content']['parts'][0]['text']
        # Strip markdown code fences if present
        text = text.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        import json as _json
        return jsonify(_json.loads(text))
    except Exception as e:
        return jsonify({'error': 'AI service unavailable', 'detail': str(e)}), 503

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    print(f"AgriBridge API starting on port {port} (debug={debug})")
    app.run(host='0.0.0.0', port=port, debug=debug)
