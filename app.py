# ══════════════════════════════════════════════════════════════════════════════
# AGRIBRIDGE — Admin Login Endpoint Patch
# Add this route to app_production.py (paste after the existing auth routes)
# ══════════════════════════════════════════════════════════════════════════════
#
# PROBLEM: admin.html POSTs to /api/admin/login but the route doesn't exist.
# SOLUTION: Add the route below to app_production.py.
#
# REQUIREMENTS (already in your existing code):
#   - import jwt, datetime, os
#   - JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key')
#   - ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'agribridge2026')
#
# PASTE THIS INTO app_production.py — after line ~50 (auth section):
# ══════════════════════════════════════════════════════════════════════════════

from flask import Flask, request, jsonify
import jwt, datetime, os, hashlib

# ── Environment variables ─────────────────────────────────────────────────────
JWT_SECRET      = os.environ.get('JWT_SECRET', 'agribridge-jwt-secret-2026')
ADMIN_PASSWORD  = os.environ.get('ADMIN_PASSWORD', 'agribridge2026')

# ── Admin Login Route ─────────────────────────────────────────────────────────
# Replace this stub with a proper Flask app reference in your actual file:
app = Flask(__name__)  # <-- In your file, this already exists; don't duplicate it

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """
    Admin login endpoint for admin.html dashboard.

    Accepts JSON body: { "password": "agribridge2026" }
    Returns: { "token": "<jwt>", "role": "admin" }

    The JWT token should then be sent in the Authorization header:
    Authorization: Bearer <token>
    """
    try:
        data = request.get_json(force=True) or {}
        password = data.get('password', '')

        # Compare against ADMIN_PASSWORD env var
        if not password or password != ADMIN_PASSWORD:
            return jsonify({'error': 'Invalid password'}), 401

        # Generate JWT token with admin role (24-hour expiry)
        payload = {
            'sub': 'admin',
            'role': 'admin',
            'iat': datetime.datetime.utcnow(),
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')

        # jwt.encode() returns bytes in PyJWT < 2.0, str in PyJWT >= 2.0
        if isinstance(token, bytes):
            token = token.decode('utf-8')

        return jsonify({
            'token': token,
            'role': 'admin',
            'message': 'Login successful'
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Admin Token Verification Helper ──────────────────────────────────────────
def verify_admin_token(token):
    """
    Helper to verify admin JWT token.
    Use in protected admin routes:

        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        payload = verify_admin_token(token)
        if not payload:
            return jsonify({'error': 'Unauthorized'}), 401
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        if payload.get('role') != 'admin':
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ALSO UPDATE admin.html — the login fetch call should be:
# ══════════════════════════════════════════════════════════════════════════════
#
# async function loginAdmin(password) {
#   const res = await fetch('/api/admin/login', {
#     method: 'POST',
#     headers: { 'Content-Type': 'application/json' },
#     body: JSON.stringify({ password })
#   });
#   const data = await res.json();
#   if (res.ok) {
#     localStorage.setItem('adminToken', data.token);
#     showDashboard();
#   } else {
#     showError(data.error || 'Login failed');
#   }
# }
#
# Then in each admin API call, send:
# headers: { 'Authorization': 'Bearer ' + localStorage.getItem('adminToken') }
#
# ══════════════════════════════════════════════════════════════════════════════
# RENDER.YAML ENV VARS TO ADD:
# ══════════════════════════════════════════════════════════════════════════════
# - key: ADMIN_PASSWORD
#   value: your-secure-password-here       (change from default agribridge2026!)
# - key: JWT_SECRET
#   value: your-256-bit-random-secret      (generate: python -c "import secrets; print(secrets.token_hex(32))")
# ══════════════════════════════════════════════════════════════════════════════
