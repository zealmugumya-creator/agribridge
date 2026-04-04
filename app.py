# app_production.py - AgriBridge Complete Production Backend
# Covers: Auth (JWT), Phone Verification (OTP), MTN/Airtel MoMo,
#         Africa's Talking USSD + SMS, Listings, Orders, Delivery,
#         Farmer Verification, Price Intelligence, Admin API

import os, bcrypt, jwt, uuid, requests, json, hmac, hashlib, time, base64, random, re
from datetime import datetime, timedelta, date
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, and_, or_, text
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app, origins=os.getenv('ALLOWED_ORIGINS', '*').split(','))

# ─── Config ───────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///agribridge.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config.update(
    SQLALCHEMY_DATABASE_URI=DATABASE_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.getenv('SECRET_KEY', 'dev-secret-change-in-production'),
    JWT_EXPIRATION_HOURS=int(os.getenv('JWT_EXPIRATION_HOURS', 24)),
    ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD', 'agribridge2026'),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16MB uploads
)

db = SQLAlchemy(app)

# ─── Constants ────────────────────────────────────────────────────────────────
COMMISSION_RATE   = 0.04   # 4% on each order
DELIVERY_FEE_BASE = 5000   # UGX base delivery fee
USSD_CODE         = '*789#'
SMS_SHORT_CODE    = '8204'
SUPPORT_PHONE     = '0755966690'
SUPPORT_WHATSAPP  = '+256755966690'

UGANDAN_DISTRICTS = [
    'Abim','Adjumani','Agago','Alebtong','Amolatar','Amudat','Amuria','Amuru',
    'Apac','Arua','Budaka','Bududa','Bugiri','Buhweju','Buikwe','Bukedea',
    'Bukomansimbi','Bukwo','Bulambuli','Buliisa','Bundibugyo','Bunyangabu',
    'Bushenyi','Busia','Butaleja','Butebo','Buvuma','Buyende','Dokolo',
    'Gomba','Gulu','Hoima','Ibanda','Iganga','Isingiro','Jinja','Kaabong',
    'Kabale','Kabarole','Kaberamaido','Kagadi','Kakumiro','Kalaki','Kalangala',
    'Kaliro','Kalungu','Kampala','Kamuli','Kamwenge','Kanungu','Kapchorwa',
    'Kapelebyong','Karenga','Kasanda','Kasese','Katakwi','Kayunga','Kazo',
    'Kibaale','Kiboga','Kibuku','Kikuube','Kiruhura','Kiryandongo','Kisoro',
    'Kitgum','Koboko','Kole','Kotido','Kumi','Kwania','Kyankwanzi','Kyegegwa',
    'Kyenjojo','Kyotera','Lamwo','Lira','Luuka','Luwero','Lwengo','Lyantonde',
    'Manafwa','Maracha','Masaka','Masindi','Mayuge','Mbale','Mbarara','Mitooma',
    'Mityana','Moroto','Moyo','Mpigi','Mubende','Mukono','Nabilatuk','Nakapiripirit',
    'Nakaseke','Nakasongola','Namayingo','Namisindwa','Namutumba','Napak','Nebbi',
    'Ngora','Ntoroko','Ntungamo','Nwoya','Obongi','Omoro','Otuke','Oyam',
    'Pader','Pakwach','Pallisa','Rakai','Rubanda','Rubirizi','Rukiga','Rukungiri',
    'Rwampara','Sembabule','Serere','Sheema','Sironko','Soroti','Tororo',
    'Wakiso','Yumbe','Zombo'
]

CROPS_LIST = [
    'Tomatoes','Matooke','Maize','Carrots','Cabbages','Onions','Potatoes',
    'Beans','Groundnuts','Cassava','Sweet Potatoes','Sorghum','Millet',
    'Coffee','Tea','Sugarcane','Cotton','Sunflower','Soybeans','Pepper',
    'Eggplant','Passion Fruit','Pineapples','Mangoes','Avocados','Bananas',
    'Watermelon','Pumpkin','Cucumber','Garlic','Ginger','Spinach','Kale'
]

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class User(db.Model):
    __tablename__ = 'users'
    id             = db.Column(db.Integer, primary_key=True)
    uuid           = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name           = db.Column(db.String(100), nullable=False)
    email          = db.Column(db.String(100), unique=True, nullable=True)
    phone          = db.Column(db.String(20), unique=True, nullable=False)
    password_hash  = db.Column(db.String(200), nullable=False)
    role           = db.Column(db.String(20), nullable=False)  # farmer|vendor|hotel|buyer|admin
    district       = db.Column(db.String(60), nullable=False)
    is_active      = db.Column(db.Boolean, default=True)
    is_verified    = db.Column(db.Boolean, default=False)
    # OTP for phone verification
    otp_code       = db.Column(db.String(6), nullable=True)
    otp_expires    = db.Column(db.DateTime, nullable=True)
    otp_attempts   = db.Column(db.Integer, default=0)
    # Password reset
    reset_token    = db.Column(db.String(100), nullable=True)
    reset_expires  = db.Column(db.DateTime, nullable=True)
    # Meta
    profile_image  = db.Column(db.String(500), nullable=True)
    device_token   = db.Column(db.String(200), nullable=True)  # For push notifications
    last_login     = db.Column(db.DateTime, nullable=True)
    login_count    = db.Column(db.Integer, default=0)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    farmer_profile = db.relationship('FarmerProfile', backref='user', uselist=False, cascade='all, delete-orphan')
    vendor_profile = db.relationship('VendorProfile', backref='user', uselist=False, cascade='all, delete-orphan')
    hotel_profile  = db.relationship('HotelProfile',  backref='user', uselist=False, cascade='all, delete-orphan')
    listings       = db.relationship('Listing', foreign_keys='Listing.farmer_id', backref='farmer', lazy='dynamic')
    orders_placed  = db.relationship('Order', foreign_keys='Order.buyer_id', backref='buyer', lazy='dynamic')
    orders_received= db.relationship('Order', foreign_keys='Order.farmer_id', backref='farmer', lazy='dynamic')
    notifications  = db.relationship('Notification', backref='user', lazy='dynamic')
    reviews_given  = db.relationship('Review', foreign_keys='Review.reviewer_id', backref='reviewer', lazy='dynamic')
    reviews_received=db.relationship('Review', foreign_keys='Review.reviewee_id', backref='reviewee', lazy='dynamic')
    wallet         = db.relationship('Wallet', backref='user', uselist=False)

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        try:
            return bcrypt.checkpw(password.encode(), self.password_hash.encode())
        except Exception:
            return False

    def generate_otp(self):
        self.otp_code    = str(random.randint(100000, 999999))
        self.otp_expires = datetime.utcnow() + timedelta(minutes=10)
        self.otp_attempts = 0
        return self.otp_code

    def verify_otp(self, code):
        if self.otp_attempts >= 5:
            return False, 'Too many attempts. Request a new OTP.'
        if not self.otp_code or not self.otp_expires:
            return False, 'No OTP found. Request a new one.'
        if datetime.utcnow() > self.otp_expires:
            return False, 'OTP expired. Request a new one.'
        self.otp_attempts += 1
        if self.otp_code != str(code):
            return False, 'Incorrect OTP.'
        # Success - clear OTP
        self.otp_code = None
        self.otp_expires = None
        self.is_verified = True
        return True, 'Phone verified!'

    def generate_jwt(self):
        return jwt.encode({
            'user_id': self.id,
            'uuid':    self.uuid,
            'role':    self.role,
            'name':    self.name,
            'exp':     datetime.utcnow() + timedelta(hours=app.config['JWT_EXPIRATION_HOURS'])
        }, app.config['SECRET_KEY'], algorithm='HS256')

    @staticmethod
    def verify_jwt(token):
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            return User.query.get(data['user_id'])
        except Exception:
            return None

    def generate_reset_token(self):
        self.reset_token   = str(uuid.uuid4())
        self.reset_expires = datetime.utcnow() + timedelta(hours=2)
        return self.reset_token

    def to_dict(self, include_token=False, include_profile=False):
        d = {
            'id':          self.uuid,
            'name':        self.name,
            'email':       self.email,
            'phone':       self.phone,
            'role':        self.role,
            'district':    self.district,
            'is_verified': self.is_verified,
            'is_active':   self.is_active,
            'profile_image': self.profile_image,
            'created_at':  self.created_at.isoformat() if self.created_at else None,
            'last_login':  self.last_login.isoformat() if self.last_login else None,
        }
        if include_token:
            d['token'] = self.generate_jwt()
        if include_profile:
            if self.farmer_profile:
                d['profile'] = self.farmer_profile.to_dict()
            elif self.vendor_profile:
                d['profile'] = self.vendor_profile.to_dict()
            elif self.hotel_profile:
                d['profile'] = self.hotel_profile.to_dict()
        return d


class FarmerProfile(db.Model):
    __tablename__      = 'farmer_profiles'
    id                 = db.Column(db.Integer, primary_key=True)
    user_id            = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    farm_name          = db.Column(db.String(100))
    farm_size_acres    = db.Column(db.Float)
    crops              = db.Column(db.Text)   # comma-separated
    monthly_output_kg  = db.Column(db.Float)
    storage_available  = db.Column(db.Boolean, default=False)
    storage_capacity_kg= db.Column(db.Float)
    irrigation         = db.Column(db.Boolean, default=False)
    gps_lat            = db.Column(db.Float)
    gps_lng            = db.Column(db.Float)
    national_id        = db.Column(db.String(20))
    national_id_image  = db.Column(db.String(500))  # URL after upload
    mobile_money_number= db.Column(db.String(20))
    mobile_money_provider = db.Column(db.String(10))  # mtn | airtel
    bank_name          = db.Column(db.String(50))
    bank_account       = db.Column(db.String(30))
    # Verification status
    id_verified        = db.Column(db.Boolean, default=False)
    location_verified  = db.Column(db.Boolean, default=False)
    agent_verified     = db.Column(db.Boolean, default=False)  # Field agent visited
    verification_notes = db.Column(db.Text)
    # Performance metrics
    rating             = db.Column(db.Float, default=0.0)
    total_orders       = db.Column(db.Integer, default=0)
    completed_orders   = db.Column(db.Integer, default=0)
    total_revenue_ugx  = db.Column(db.Float, default=0.0)
    experience_years   = db.Column(db.Integer, default=0)
    # Planting calendar
    planting_months    = db.Column(db.String(100))  # JSON array of months
    harvest_months     = db.Column(db.String(100))  # JSON array of months

    def to_dict(self):
        return {
            'farm_name':          self.farm_name,
            'farm_size_acres':    self.farm_size_acres,
            'crops':              self.crops.split(',') if self.crops else [],
            'monthly_output_kg':  self.monthly_output_kg,
            'storage_available':  self.storage_available,
            'storage_capacity_kg':self.storage_capacity_kg,
            'irrigation':         self.irrigation,
            'mobile_money_number':self.mobile_money_number,
            'mobile_money_provider': self.mobile_money_provider,
            'rating':             self.rating,
            'total_orders':       self.total_orders,
            'completed_orders':   self.completed_orders,
            'total_revenue_ugx':  self.total_revenue_ugx,
            'experience_years':   self.experience_years,
            'id_verified':        self.id_verified,
            'location_verified':  self.location_verified,
            'agent_verified':     self.agent_verified,
        }


class VendorProfile(db.Model):
    __tablename__      = 'vendor_profiles'
    id                 = db.Column(db.Integer, primary_key=True)
    user_id            = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    business_name      = db.Column(db.String(100))
    market_name        = db.Column(db.String(100))
    market_district    = db.Column(db.String(60))
    stall_number       = db.Column(db.String(20))
    product_categories = db.Column(db.Text)
    weekly_volume_kg   = db.Column(db.Float)
    license_number     = db.Column(db.String(50))
    gps_lat            = db.Column(db.Float)
    gps_lng            = db.Column(db.Float)
    mobile_money_number= db.Column(db.String(20))
    mobile_money_provider = db.Column(db.String(10))
    rating             = db.Column(db.Float, default=0.0)
    total_orders       = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'business_name':      self.business_name,
            'market_name':        self.market_name,
            'market_district':    self.market_district,
            'product_categories': self.product_categories.split(',') if self.product_categories else [],
            'weekly_volume_kg':   self.weekly_volume_kg,
            'rating':             self.rating,
        }


class HotelProfile(db.Model):
    __tablename__      = 'hotel_profiles'
    id                 = db.Column(db.Integer, primary_key=True)
    user_id            = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    hotel_name         = db.Column(db.String(100))
    star_rating        = db.Column(db.Integer)
    weekly_produce_kg  = db.Column(db.Float)
    delivery_days      = db.Column(db.String(50))  # Mon,Wed,Fri
    delivery_time      = db.Column(db.String(20))  # 07:00
    sla_terms          = db.Column(db.Text)
    purchase_manager   = db.Column(db.String(100))
    purchase_email     = db.Column(db.String(100))
    purchase_phone     = db.Column(db.String(20))
    mobile_money_number= db.Column(db.String(20))
    mobile_money_provider = db.Column(db.String(10))

    def to_dict(self):
        return {
            'hotel_name':        self.hotel_name,
            'star_rating':       self.star_rating,
            'weekly_produce_kg': self.weekly_produce_kg,
            'delivery_days':     self.delivery_days,
            'purchase_manager':  self.purchase_manager,
        }


class Listing(db.Model):
    __tablename__     = 'listings'
    id                = db.Column(db.Integer, primary_key=True)
    uuid              = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    farmer_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    crop              = db.Column(db.String(50), nullable=False)
    variety           = db.Column(db.String(50))
    quantity_kg       = db.Column(db.Float, nullable=False)
    remaining_kg      = db.Column(db.Float, nullable=False)
    price_per_kg      = db.Column(db.Float, nullable=False)
    min_order_kg      = db.Column(db.Float, default=10)
    quality_grade     = db.Column(db.String(1), default='B')  # A|B|C
    district          = db.Column(db.String(60), nullable=False)
    delivery_available= db.Column(db.Boolean, default=False)
    delivery_fee_per_km = db.Column(db.Float, default=0)
    delivery_districts= db.Column(db.Text)  # comma-separated districts for delivery
    image_url         = db.Column(db.String(500))
    harvest_date      = db.Column(db.Date)
    expiry_date       = db.Column(db.Date)
    is_active         = db.Column(db.Boolean, default=True)
    is_organic        = db.Column(db.Boolean, default=False)
    notes             = db.Column(db.Text)
    views             = db.Column(db.Integer, default=0)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_farmer=True):
        d = {
            'id':               self.id,
            'uuid':             self.uuid,
            'crop':             self.crop,
            'variety':          self.variety,
            'quantity_kg':      self.quantity_kg,
            'remaining_kg':     self.remaining_kg,
            'price_per_kg':     self.price_per_kg,
            'min_order_kg':     self.min_order_kg,
            'quality_grade':    self.quality_grade,
            'district':         self.district,
            'delivery_available': self.delivery_available,
            'delivery_fee_per_km': self.delivery_fee_per_km,
            'image_url':        self.image_url,
            'harvest_date':     self.harvest_date.isoformat() if self.harvest_date else None,
            'expiry_date':      self.expiry_date.isoformat() if self.expiry_date else None,
            'is_organic':       self.is_organic,
            'notes':            self.notes,
            'views':            self.views,
            'is_active':        self.is_active,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
        }
        if include_farmer and self.farmer:
            fp = self.farmer.farmer_profile
            d['farmer_name']   = self.farmer.name
            d['farmer_phone']  = self.farmer.phone
            d['farmer_rating'] = fp.rating if fp else 0
            d['farmer_verified'] = (fp.id_verified and fp.location_verified) if fp else False
        return d


class Order(db.Model):
    __tablename__     = 'orders'
    id                = db.Column(db.Integer, primary_key=True)
    reference         = db.Column(db.String(25), unique=True, nullable=False)
    farmer_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    buyer_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    listing_id        = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    quantity_kg       = db.Column(db.Float, nullable=False)
    unit_price        = db.Column(db.Float, nullable=False)
    subtotal          = db.Column(db.Float, nullable=False)
    delivery_fee      = db.Column(db.Float, default=0)
    commission        = db.Column(db.Float, default=0)  # AgriBridge 4% cut
    total_amount      = db.Column(db.Float, nullable=False)
    # Delivery details
    delivery_address  = db.Column(db.Text, nullable=False)
    delivery_district = db.Column(db.String(60))
    delivery_date     = db.Column(db.Date)
    delivery_time_slot= db.Column(db.String(20))  # morning|afternoon|evening
    # Payment
    payment_method    = db.Column(db.String(20))  # mobile_money|bank|cash_on_delivery
    payment_provider  = db.Column(db.String(10))  # mtn|airtel|bank
    payment_phone     = db.Column(db.String(20))
    payment_status    = db.Column(db.String(20), default='pending')  # pending|paid|failed|refunded
    payment_reference = db.Column(db.String(100))  # provider's transaction ref
    paid_at           = db.Column(db.DateTime)
    # Order status
    order_status      = db.Column(db.String(20), default='pending')
    # pending|confirmed|processing|dispatched|delivered|cancelled|disputed
    cancellation_reason = db.Column(db.Text)
    # Timestamps
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_at      = db.Column(db.DateTime)
    delivered_at      = db.Column(db.DateTime)
    # Relationships
    listing           = db.relationship('Listing', backref='orders', foreign_keys=[listing_id])
    delivery          = db.relationship('Delivery', backref='order', uselist=False)
    review            = db.relationship('Review', backref='order', uselist=False)

    def generate_reference(self):
        self.reference = f"AB-{datetime.utcnow().year}-{random.randint(10000, 99999)}"

    def to_dict(self, full=False):
        d = {
            'reference':      self.reference,
            'quantity_kg':    self.quantity_kg,
            'unit_price':     self.unit_price,
            'subtotal':       self.subtotal,
            'delivery_fee':   self.delivery_fee,
            'total_amount':   self.total_amount,
            'payment_status': self.payment_status,
            'order_status':   self.order_status,
            'delivery_address': self.delivery_address,
            'delivery_district': self.delivery_district,
            'created_at':     self.created_at.isoformat() if self.created_at else None,
        }
        if full:
            d['farmer_name'] = self.farmer.name if self.farmer else None
            d['buyer_name']  = self.buyer.name if self.buyer else None
            d['crop']        = self.listing.crop if self.listing else None
            d['payment_method'] = self.payment_method
            d['payment_reference'] = self.payment_reference
        return d


class Delivery(db.Model):
    __tablename__     = 'deliveries'
    id                = db.Column(db.Integer, primary_key=True)
    reference         = db.Column(db.String(25), unique=True, nullable=False)
    order_id          = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    farmer_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    driver_name       = db.Column(db.String(100))
    driver_phone      = db.Column(db.String(20))
    vehicle_number    = db.Column(db.String(20))
    vehicle_type      = db.Column(db.String(30))  # motorcycle|pickup|truck
    pickup_location   = db.Column(db.String(200))
    pickup_district   = db.Column(db.String(60))
    dropoff_location  = db.Column(db.String(200))
    dropoff_district  = db.Column(db.String(60))
    status            = db.Column(db.String(20), default='assigned')
    # assigned|loading|picked_up|in_transit|at_destination|delivered|failed
    current_location  = db.Column(db.String(200))
    gps_lat           = db.Column(db.Float)
    gps_lng           = db.Column(db.Float)
    eta               = db.Column(db.String(50))
    distance_km       = db.Column(db.Float)
    cold_chain        = db.Column(db.Boolean, default=False)
    started_at        = db.Column(db.DateTime)
    delivered_at      = db.Column(db.DateTime)
    proof_of_delivery = db.Column(db.String(500))  # photo URL
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    events            = db.relationship('DeliveryEvent', backref='delivery', lazy='dynamic')

    def generate_reference(self):
        self.reference = f"DEL-{datetime.utcnow().year}-{random.randint(10000, 99999)}"

    def to_dict(self, include_events=False):
        d = {
            'reference':       self.reference,
            'driver_name':     self.driver_name,
            'driver_phone':    self.driver_phone,
            'vehicle_number':  self.vehicle_number,
            'status':          self.status,
            'current_location':self.current_location,
            'eta':             self.eta,
            'pickup_location': self.pickup_location,
            'pickup_district': self.pickup_district,
            'dropoff_location':self.dropoff_location,
            'dropoff_district':self.dropoff_district,
            'cold_chain':      self.cold_chain,
            'created_at':      self.created_at.isoformat() if self.created_at else None,
            'delivered_at':    self.delivered_at.isoformat() if self.delivered_at else None,
        }
        if include_events:
            d['events'] = [{'event_type': e.event_type, 'location': e.location,
                            'description': e.description,
                            'created_at': e.created_at.isoformat()} for e in self.events.all()]
        return d


class DeliveryEvent(db.Model):
    __tablename__ = 'delivery_events'
    id            = db.Column(db.Integer, primary_key=True)
    delivery_id   = db.Column(db.Integer, db.ForeignKey('deliveries.id'), nullable=False)
    event_type    = db.Column(db.String(30), nullable=False)
    location      = db.Column(db.String(200))
    description   = db.Column(db.Text)
    gps_lat       = db.Column(db.Float)
    gps_lng       = db.Column(db.Float)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class Transaction(db.Model):
    __tablename__       = 'transactions'
    id                  = db.Column(db.Integer, primary_key=True)
    reference           = db.Column(db.String(50), unique=True, nullable=False)
    order_id            = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    user_id             = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount              = db.Column(db.Float, nullable=False)
    type                = db.Column(db.String(20))  # payment|commission|withdrawal|refund
    provider            = db.Column(db.String(20))  # mtn|airtel|bank|cash
    provider_reference  = db.Column(db.String(100))
    status              = db.Column(db.String(20), default='pending')  # pending|success|failed
    failure_reason      = db.Column(db.Text)
    metadata_json       = db.Column(db.Text)  # JSON extra data
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at        = db.Column(db.DateTime)


class Wallet(db.Model):
    """Each user has a wallet — earnings/credits sit here until withdrawal"""
    __tablename__ = 'wallets'
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    balance       = db.Column(db.Float, default=0.0)
    pending       = db.Column(db.Float, default=0.0)  # Funds held during active order
    total_earned  = db.Column(db.Float, default=0.0)
    total_withdrawn = db.Column(db.Float, default=0.0)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceData(db.Model):
    __tablename__ = 'price_data'
    id            = db.Column(db.Integer, primary_key=True)
    crop          = db.Column(db.String(50), nullable=False)
    district      = db.Column(db.String(60), nullable=False)
    price_per_kg  = db.Column(db.Float, nullable=False)
    source        = db.Column(db.String(30))  # maaif|listing|crowdsourced
    recorded_at   = db.Column(db.DateTime, default=datetime.utcnow)
    recorded_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)


class ForwardContract(db.Model):
    """Lock price weeks in advance — hedge against seasonal crashes"""
    __tablename__   = 'forward_contracts'
    id              = db.Column(db.Integer, primary_key=True)
    reference       = db.Column(db.String(25), unique=True, nullable=False)
    farmer_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    buyer_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    crop            = db.Column(db.String(50), nullable=False)
    quantity_kg     = db.Column(db.Float, nullable=False)
    locked_price_per_kg = db.Column(db.Float, nullable=False)
    delivery_date   = db.Column(db.Date, nullable=False)
    district        = db.Column(db.String(60))
    status          = db.Column(db.String(20), default='active')  # active|fulfilled|cancelled|disputed
    deposit_paid    = db.Column(db.Float, default=0)
    deposit_percent = db.Column(db.Float, default=20)  # 20% deposit to lock
    terms           = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    farmer          = db.relationship('User', foreign_keys=[farmer_id])
    buyer           = db.relationship('User', foreign_keys=[buyer_id])


class Notification(db.Model):
    __tablename__ = 'notifications'
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title         = db.Column(db.String(100), nullable=False)
    body          = db.Column(db.Text, nullable=False)
    type          = db.Column(db.String(30))  # order|delivery|payment|system|alert
    ref_id        = db.Column(db.String(50))  # order ref, delivery ref etc
    is_read       = db.Column(db.Boolean, default=False)
    sent_sms      = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class Review(db.Model):
    __tablename__ = 'reviews'
    id            = db.Column(db.Integer, primary_key=True)
    order_id      = db.Column(db.Integer, db.ForeignKey('orders.id'), unique=True, nullable=False)
    reviewer_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reviewee_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rating        = db.Column(db.Integer, nullable=False)  # 1–5
    comment       = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class ContactMessage(db.Model):
    __tablename__ = 'contact_messages'
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(100))
    phone         = db.Column(db.String(20))
    message       = db.Column(db.Text, nullable=False)
    is_read       = db.Column(db.Boolean, default=False)
    replied_at    = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class TrainingModule(db.Model):
    __tablename__ = 'training_modules'
    id            = db.Column(db.Integer, primary_key=True)
    title         = db.Column(db.String(200), nullable=False)
    description   = db.Column(db.Text)
    category      = db.Column(db.String(50))
    level         = db.Column(db.String(20), default='Beginner')
    video_url     = db.Column(db.String(500))
    thumbnail_url = db.Column(db.String(500))
    duration_mins = db.Column(db.Integer)
    language      = db.Column(db.String(20), default='English')
    is_active     = db.Column(db.Boolean, default=True)
    views         = db.Column(db.Integer, default=0)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class USSDSession(db.Model):
    """Track USSD session state for multi-step flows"""
    __tablename__  = 'ussd_sessions'
    id             = db.Column(db.Integer, primary_key=True)
    session_id     = db.Column(db.String(100), unique=True, nullable=False)
    phone_number   = db.Column(db.String(20), nullable=False)
    current_menu   = db.Column(db.String(50), default='main')
    session_data   = db.Column(db.Text)  # JSON for multi-step data
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER SERVICES
# ═══════════════════════════════════════════════════════════════════════════════

class SMSService:
    """Africa's Talking SMS Gateway"""

    def __init__(self):
        self.username = os.getenv('AT_USERNAME', 'sandbox')
        self.api_key  = os.getenv('AT_API_KEY', '')
        self.sandbox  = self.username == 'sandbox'
        self.base     = 'https://api.africastalking.com/version1'
        self.sender   = 'AGRIBRIDGE'

    def _normalize_phone(self, phone):
        phone = re.sub(r'\D', '', phone)
        if phone.startswith('0'):
            phone = '256' + phone[1:]
        elif not phone.startswith('256'):
            phone = '256' + phone
        return '+' + phone

    def send(self, phone, message):
        phone = self._normalize_phone(phone)
        if self.sandbox or not self.api_key:
            print(f"[SMS SANDBOX] To:{phone} → {message[:80]}")
            return True
        try:
            r = requests.post(
                f'{self.base}/messaging',
                headers={'apiKey': self.api_key, 'Accept': 'application/json'},
                data={'username': self.username, 'to': phone, 'message': message, 'from': self.sender},
                timeout=10
            )
            return r.status_code == 201
        except Exception as e:
            print(f"SMS error: {e}")
            return False

    def send_otp(self, phone, otp):
        return self.send(phone, f"AgriBridge OTP: {otp}. Expires in 10 minutes. Do not share this code.")

    def send_order_confirmed(self, farmer_phone, buyer_name, crop, qty, amount, ref):
        msg = f"AgriBridge: New order {ref}! {buyer_name} ordered {qty}kg {crop} for UGX {amount:,.0f}. Log in to confirm."
        return self.send(farmer_phone, msg)

    def send_payment_received(self, farmer_phone, amount, ref):
        msg = f"AgriBridge: Payment of UGX {amount:,.0f} received for order {ref}. Prepare produce for dispatch."
        return self.send(farmer_phone, msg)

    def send_delivery_update(self, buyer_phone, status, ref, eta=None):
        eta_text = f" ETA: {eta}." if eta else ""
        msg = f"AgriBridge: Your delivery {ref} is now {status}.{eta_text} Call {SUPPORT_PHONE} for help."
        return self.send(buyer_phone, msg)

    def send_welcome(self, phone, name, role):
        msg = (f"Welcome to AgriBridge, {name}! Your {role} account is ready. "
               f"Dial {USSD_CODE} from any phone or visit agribridge.ug. "
               f"Support: {SUPPORT_PHONE}")
        return self.send(phone, msg)

    def broadcast(self, phones, message):
        results = []
        for p in phones:
            results.append(self.send(p, message))
        return results


class MTNMoMo:
    """MTN Mobile Money Uganda — Collection API"""

    def __init__(self):
        self.base    = os.getenv('MTN_BASE_URL', 'https://sandbox.momodeveloper.mtn.com')
        self.user_id = os.getenv('MTN_USER_ID', '')
        self.api_key = os.getenv('MTN_API_KEY', '')
        self.sub_key = os.getenv('MTN_SUBSCRIPTION_KEY', '')
        self.env     = os.getenv('MTN_ENVIRONMENT', 'sandbox')
        self._token  = None
        self._token_exp = 0

    def _token_valid(self):
        return self._token and time.time() < self._token_exp

    def _get_token(self):
        if self._token_valid():
            return self._token
        if not self.user_id or not self.api_key:
            return None
        try:
            auth = base64.b64encode(f"{self.user_id}:{self.api_key}".encode()).decode()
            r = requests.post(
                f"{self.base}/collection/token/",
                headers={'Authorization': f'Basic {auth}', 'Ocp-Apim-Subscription-Key': self.sub_key},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                self._token = data['access_token']
                self._token_exp = time.time() + data.get('expires_in', 3600) - 120
                return self._token
        except Exception as e:
            print(f"MTN token error: {e}")
        return None

    def request_to_pay(self, amount_ugx, phone, order_ref, description):
        """
        Initiate a payment request. User gets a pop-up on their phone to approve.
        Returns (success: bool, x_reference: str|None, error: str|None)
        """
        token = self._get_token()
        if not token:
            # Sandbox/dev fallback — simulate success
            print(f"[MTN SANDBOX] Requesting UGX {amount_ugx} from {phone} for {order_ref}")
            return True, str(uuid.uuid4()), None

        phone = re.sub(r'\D', '', phone)
        if phone.startswith('0'):
            phone = '256' + phone[1:]

        x_ref = str(uuid.uuid4())
        try:
            r = requests.post(
                f"{self.base}/collection/v1_0/requesttopay",
                headers={
                    'Authorization': f'Bearer {token}',
                    'X-Reference-Id': x_ref,
                    'X-Target-Environment': self.env,
                    'Content-Type': 'application/json',
                    'Ocp-Apim-Subscription-Key': self.sub_key
                },
                json={
                    'amount': str(int(amount_ugx)),
                    'currency': 'UGX',
                    'externalId': order_ref,
                    'payer': {'partyIdType': 'MSISDN', 'partyId': phone},
                    'payerMessage': description,
                    'payeeNote': f'AgriBridge {order_ref}'
                },
                timeout=15
            )
            if r.status_code == 202:
                return True, x_ref, None
            return False, None, f"MTN error {r.status_code}: {r.text[:100]}"
        except Exception as e:
            return False, None, str(e)

    def check_status(self, x_reference):
        """Returns 'SUCCESSFUL', 'FAILED', 'PENDING' or None"""
        token = self._get_token()
        if not token:
            return 'SUCCESSFUL'  # Simulate for sandbox
        try:
            r = requests.get(
                f"{self.base}/collection/v1_0/requesttopay/{x_reference}",
                headers={
                    'Authorization': f'Bearer {token}',
                    'X-Target-Environment': self.env,
                    'Ocp-Apim-Subscription-Key': self.sub_key
                },
                timeout=10
            )
            if r.status_code == 200:
                return r.json().get('status')
        except Exception as e:
            print(f"MTN status check error: {e}")
        return None


class AirtelMoney:
    """Airtel Money Uganda Collection"""

    def __init__(self):
        self.client_id     = os.getenv('AIRTEL_CLIENT_ID', '')
        self.client_secret = os.getenv('AIRTEL_CLIENT_SECRET', '')
        self.base          = os.getenv('AIRTEL_BASE_URL', 'https://openapi.airtel.africa')
        self._token        = None

    def _get_token(self):
        if self._token:
            return self._token
        if not self.client_id:
            return None
        try:
            r = requests.post(
                f"{self.base}/auth/oauth2/token",
                json={'client_id': self.client_id, 'client_secret': self.client_secret,
                      'grant_type': 'client_credentials'},
                timeout=10
            )
            if r.status_code == 200:
                self._token = r.json().get('access_token')
                return self._token
        except Exception as e:
            print(f"Airtel token error: {e}")
        return None

    def collect(self, amount_ugx, phone, order_ref):
        token = self._get_token()
        if not token:
            print(f"[AIRTEL SANDBOX] UGX {amount_ugx} from {phone} for {order_ref}")
            return True, str(uuid.uuid4()), None
        phone = re.sub(r'\D', '', phone)
        try:
            r = requests.post(
                f"{self.base}/merchant/v1/payments/",
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                         'X-Country': 'UG', 'X-Currency': 'UGX'},
                json={
                    'reference': order_ref,
                    'subscriber': {'country': 'UG', 'currency': 'UGX', 'msisdn': phone},
                    'transaction': {'amount': str(int(amount_ugx)), 'country': 'UG',
                                    'currency': 'UGX', 'id': str(uuid.uuid4())}
                },
                timeout=15
            )
            if r.status_code in [200, 201]:
                tid = r.json().get('data', {}).get('transaction_id', str(uuid.uuid4()))
                return True, tid, None
            return False, None, f"Airtel error {r.status_code}"
        except Exception as e:
            return False, None, str(e)


# ─── Singleton service instances ──────────────────────────────────────────────
sms  = SMSService()
mtn  = MTNMoMo()
airtel = AirtelMoney()


# ─── Notification helper ──────────────────────────────────────────────────────
def notify(user_id, title, body, ntype='system', ref_id=None, send_sms_flag=False):
    """Create in-app notification and optionally send SMS"""
    note = Notification(user_id=user_id, title=title, body=body, type=ntype, ref_id=ref_id)
    db.session.add(note)
    if send_sms_flag:
        user = User.query.get(user_id)
        if user:
            sms.send(user.phone, f"AgriBridge: {title} - {body[:100]}")
        note.sent_sms = True


def update_farmer_rating(farmer_id):
    """Recalculate farmer's average rating from all reviews"""
    result = db.session.query(func.avg(Review.rating)).join(
        Order, Review.order_id == Order.id
    ).filter(Order.farmer_id == farmer_id).scalar()
    fp = FarmerProfile.query.filter_by(user_id=farmer_id).first()
    if fp and result:
        fp.rating = round(float(result), 1)
        db.session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH DECORATORS
# ═══════════════════════════════════════════════════════════════════════════════

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        if not token:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        user = User.verify_jwt(token)
        if not user or not user.is_active:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
        g.user = user
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if g.user.role not in roles:
                return jsonify({'success': False, 'error': 'Permission denied'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def optional_auth(f):
    """Attach user to g if token present, but don't require it"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        g.user = User.verify_jwt(token) if token else None
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Accept either JWT with role=admin OR the admin password header
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        pw    = request.headers.get('X-Admin-Password', '')
        if pw == app.config['ADMIN_PASSWORD']:
            g.user = None
            return f(*args, **kwargs)
        user = User.verify_jwt(token)
        if user and user.role == 'admin':
            g.user = user
            return f(*args, **kwargs)
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    return decorated


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

def paginate(query, page, per_page=20):
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return items, total, (total + per_page - 1) // per_page


def validate_phone(phone):
    digits = re.sub(r'\D', '', phone)
    return len(digits) >= 9


def normalize_phone(phone):
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('0'):
        digits = '256' + digits[1:]
    elif not digits.startswith('256'):
        digits = '256' + digits
    return digits


def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code


def ok(data=None, **kwargs):
    resp = {'success': True}
    if data:
        resp.update(data)
    resp.update(kwargs)
    return jsonify(resp)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — STATIC PAGES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/admin')
def admin_page():
    return send_from_directory('static', 'admin.html')

@app.route('/api/health')
def health():
    return ok({'status': 'healthy', 'version': '2.0', 'timestamp': datetime.utcnow().isoformat()})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    required = ['name', 'phone', 'password', 'role', 'district']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return error(f"Missing: {', '.join(missing)}")

    if not validate_phone(data['phone']):
        return error('Invalid phone number')

    if data['role'] not in ('farmer', 'vendor', 'hotel', 'buyer'):
        return error('Invalid role')

    phone = normalize_phone(data['phone'])

    if User.query.filter_by(phone=phone).first():
        return error('Phone number already registered')

    if data.get('email') and User.query.filter_by(email=data['email']).first():
        return error('Email already registered')

    user = User(
        name=data['name'].strip(),
        phone=phone,
        email=data.get('email', '').strip() or None,
        role=data['role'],
        district=data['district'],
    )
    user.set_password(data['password'])

    # Generate OTP for phone verification
    otp = user.generate_otp()

    db.session.add(user)
    db.session.flush()  # Get user.id before commit

    # Create role-specific profile
    if data['role'] == 'farmer':
        fp = FarmerProfile(
            user_id=user.id,
            farm_name=data.get('farm_name', ''),
            crops=data.get('crops', ''),
            monthly_output_kg=data.get('monthly_output_kg') or None,
            farm_size_acres=data.get('farm_size_acres') or None,
            mobile_money_number=phone,
            mobile_money_provider='mtn',
            experience_years=int(data.get('experience_years', 0) or 0),
        )
        db.session.add(fp)

    elif data['role'] == 'vendor':
        vp = VendorProfile(
            user_id=user.id,
            business_name=data.get('business_name', ''),
            market_name=data.get('market_name', ''),
            product_categories=data.get('product_categories', ''),
            weekly_volume_kg=data.get('weekly_volume_kg') or None,
            mobile_money_number=phone,
        )
        db.session.add(vp)

    elif data['role'] == 'hotel':
        hp = HotelProfile(
            user_id=user.id,
            hotel_name=data.get('hotel_name', ''),
            star_rating=data.get('star_rating') or None,
            weekly_produce_kg=data.get('weekly_produce_kg') or None,
            mobile_money_number=phone,
        )
        db.session.add(hp)

    # Create wallet
    db.session.add(Wallet(user_id=user.id))

    db.session.commit()

    # Send OTP via SMS
    sms.send_otp(phone, otp)
    # Also send welcome when verified — queued after OTP
    print(f"[REGISTER] {user.name} ({user.role}) — OTP: {otp}")

    return ok({'message': f'Registration successful! OTP sent to {data["phone"][-4:].rjust(len(data["phone"]), "*")}',
               'user_id': user.uuid, 'requires_otp': True})


@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json or {}
    phone = normalize_phone(data.get('phone', ''))
    code  = str(data.get('otp', '')).strip()

    user = User.query.filter_by(phone=phone).first()
    if not user:
        return error('User not found')

    success, message = user.verify_otp(code)
    if not success:
        db.session.commit()  # save attempt count
        return error(message)

    db.session.commit()

    # Send proper welcome SMS now
    sms.send_welcome(phone, user.name, user.role)

    notify(user.id, 'Welcome to AgriBridge!',
           f'Your phone is verified. Start by browsing the marketplace or dialling {USSD_CODE}.',
           ntype='system')
    db.session.commit()

    return ok({'message': 'Phone verified!', 'user': user.to_dict(include_token=True)})


@app.route('/api/resend-otp', methods=['POST'])
def resend_otp():
    data  = request.json or {}
    phone = normalize_phone(data.get('phone', ''))
    user  = User.query.filter_by(phone=phone).first()
    if not user:
        return error('User not found')
    if user.is_verified:
        return error('Phone already verified')
    otp = user.generate_otp()
    db.session.commit()
    sms.send_otp(phone, otp)
    return ok({'message': 'New OTP sent'})


@app.route('/api/login', methods=['POST'])
def login():
    data  = request.json or {}
    phone = normalize_phone(data.get('phone', ''))
    password = data.get('password', '')

    user = User.query.filter_by(phone=phone).first()
    if not user or not user.check_password(password):
        return error('Incorrect phone number or password', 401)
    if not user.is_active:
        return error('Account suspended. Contact support.', 403)

    user.last_login = datetime.utcnow()
    user.login_count = (user.login_count or 0) + 1
    db.session.commit()

    resp = {'user': user.to_dict(include_token=True, include_profile=True)}
    if not user.is_verified:
        resp['warning'] = 'Phone not verified. Some features may be limited.'
    return ok(resp)


@app.route('/api/logout', methods=['POST'])
@require_auth
def logout():
    # JWT is stateless — client deletes the token.
    # Here we just log the event.
    print(f"[LOGOUT] {g.user.name} logged out at {datetime.utcnow()}")
    return ok({'message': 'Logged out'})


@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data  = request.json or {}
    phone = normalize_phone(data.get('phone', ''))
    user  = User.query.filter_by(phone=phone).first()
    if not user:
        # Don't reveal if phone exists
        return ok({'message': 'If that number is registered, a reset code has been sent.'})

    otp = user.generate_otp()
    db.session.commit()
    sms.send(phone, f"AgriBridge password reset code: {otp}. Expires in 10 mins.")
    return ok({'message': 'Reset code sent to your phone'})


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data     = request.json or {}
    phone    = normalize_phone(data.get('phone', ''))
    otp      = str(data.get('otp', ''))
    new_pass = data.get('new_password', '')

    if len(new_pass) < 6:
        return error('Password must be at least 6 characters')

    user = User.query.filter_by(phone=phone).first()
    if not user:
        return error('User not found')

    success, message = user.verify_otp(otp)
    if not success:
        db.session.commit()
        return error(message)

    user.set_password(new_pass)
    db.session.commit()
    return ok({'message': 'Password reset successful. Please log in.'})


@app.route('/api/me', methods=['GET'])
@require_auth
def me():
    return ok({'user': g.user.to_dict(include_profile=True)})


@app.route('/api/me', methods=['PUT'])
@require_auth
def update_me():
    data = request.json or {}
    user = g.user

    # Update basic fields
    for field in ('name', 'email', 'district'):
        if data.get(field):
            setattr(user, field, data[field].strip())

    # Update profile
    if user.role == 'farmer' and user.farmer_profile:
        fp = user.farmer_profile
        for field in ('farm_name', 'crops', 'farm_size_acres', 'monthly_output_kg',
                      'storage_available', 'irrigation', 'mobile_money_number',
                      'mobile_money_provider', 'experience_years'):
            if data.get(field) is not None:
                setattr(fp, field, data[field])

    db.session.commit()
    return ok({'user': user.to_dict(include_profile=True), 'message': 'Profile updated'})


@app.route('/api/change-password', methods=['POST'])
@require_auth
def change_password():
    data = request.json or {}
    if not g.user.check_password(data.get('current_password', '')):
        return error('Current password is incorrect')
    new_pass = data.get('new_password', '')
    if len(new_pass) < 6:
        return error('New password must be at least 6 characters')
    g.user.set_password(new_pass)
    db.session.commit()
    return ok({'message': 'Password changed'})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — LISTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/listings', methods=['GET'])
@optional_auth
def get_listings():
    q = Listing.query.filter_by(is_active=True)

    crop     = request.args.get('crop', '').strip()
    district = request.args.get('district', '').strip()
    grade    = request.args.get('grade', '').strip()
    organic  = request.args.get('organic')
    max_price= request.args.get('max_price', type=float)
    min_qty  = request.args.get('min_qty', type=float)
    sort     = request.args.get('sort', 'newest')  # newest|price_asc|price_desc|rating

    if crop:
        q = q.filter(Listing.crop.ilike(f'%{crop}%'))
    if district:
        q = q.filter(Listing.district.ilike(f'%{district}%'))
    if grade:
        q = q.filter(Listing.quality_grade == grade.upper())
    if organic:
        q = q.filter(Listing.is_organic == True)
    if max_price:
        q = q.filter(Listing.price_per_kg <= max_price)
    if min_qty:
        q = q.filter(Listing.remaining_kg >= min_qty)

    # Sorting
    if sort == 'price_asc':
        q = q.order_by(Listing.price_per_kg.asc())
    elif sort == 'price_desc':
        q = q.order_by(Listing.price_per_kg.desc())
    else:
        q = q.order_by(Listing.created_at.desc())

    page     = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    items, total, pages = paginate(q, page, per_page)

    return ok({'listings': [l.to_dict() for l in items],
               'total': total, 'page': page, 'pages': pages})


@app.route('/api/listings', methods=['POST'])
@require_auth
@require_role('farmer')
def create_listing():
    data = request.json or {}
    required = ['crop', 'quantity_kg', 'price_per_kg']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return error(f"Missing: {', '.join(missing)}")

    listing = Listing(
        farmer_id=g.user.id,
        crop=data['crop'].strip(),
        variety=data.get('variety', ''),
        quantity_kg=float(data['quantity_kg']),
        remaining_kg=float(data['quantity_kg']),
        price_per_kg=float(data['price_per_kg']),
        min_order_kg=float(data.get('min_order_kg', 10)),
        quality_grade=data.get('quality_grade', 'B').upper(),
        district=data.get('district') or g.user.district,
        delivery_available=bool(data.get('delivery_available', False)),
        delivery_fee_per_km=float(data.get('delivery_fee_per_km', 0)),
        is_organic=bool(data.get('is_organic', False)),
        notes=data.get('notes', ''),
        image_url=data.get('image_url'),
    )

    if data.get('harvest_date'):
        try:
            listing.harvest_date = date.fromisoformat(data['harvest_date'])
        except Exception:
            pass
    if data.get('expiry_date'):
        try:
            listing.expiry_date = date.fromisoformat(data['expiry_date'])
        except Exception:
            pass

    db.session.add(listing)

    # Record price point
    price_rec = PriceData(
        crop=listing.crop, district=listing.district,
        price_per_kg=listing.price_per_kg,
        source='listing', recorded_by=g.user.id
    )
    db.session.add(price_rec)
    db.session.commit()

    return ok({'listing': listing.to_dict(), 'message': 'Listing created!'}, status=201)


@app.route('/api/listings/<int:listing_id>', methods=['GET'])
@optional_auth
def get_listing(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    listing.views = (listing.views or 0) + 1
    db.session.commit()
    return ok({'listing': listing.to_dict()})


@app.route('/api/listings/<int:listing_id>', methods=['PUT'])
@require_auth
def update_listing(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.farmer_id != g.user.id and g.user.role != 'admin':
        return error('Not your listing', 403)

    data = request.json or {}
    for field in ('price_per_kg', 'remaining_kg', 'quality_grade', 'notes',
                  'delivery_available', 'delivery_fee_per_km', 'image_url', 'is_active', 'is_organic'):
        if data.get(field) is not None:
            setattr(listing, field, data[field])

    db.session.commit()
    return ok({'listing': listing.to_dict(), 'message': 'Listing updated'})


@app.route('/api/listings/<int:listing_id>', methods=['DELETE'])
@require_auth
def delete_listing(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.farmer_id != g.user.id and g.user.role != 'admin':
        return error('Not your listing', 403)
    listing.is_active = False
    db.session.commit()
    return ok({'message': 'Listing deactivated'})


@app.route('/api/my-listings', methods=['GET'])
@require_auth
def my_listings():
    q = Listing.query.filter_by(farmer_id=g.user.id).order_by(Listing.created_at.desc())
    return ok({'listings': [l.to_dict() for l in q.all()]})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — ORDERS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/orders', methods=['POST'])
@require_auth
def place_order():
    data = request.json or {}
    required = ['listing_id', 'quantity_kg', 'delivery_address']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return error(f"Missing: {', '.join(missing)}")

    listing = Listing.query.get(data['listing_id'])
    if not listing or not listing.is_active:
        return error('Listing not found or no longer available')

    qty = float(data['quantity_kg'])
    if qty < (listing.min_order_kg or 0):
        return error(f"Minimum order is {listing.min_order_kg}kg")
    if qty > listing.remaining_kg:
        return error(f"Only {listing.remaining_kg}kg available")
    if listing.farmer_id == g.user.id:
        return error("You can't order your own listing")

    subtotal     = qty * listing.price_per_kg
    delivery_fee = float(data.get('delivery_fee', DELIVERY_FEE_BASE if listing.delivery_available else 0))
    commission   = round(subtotal * COMMISSION_RATE, 0)
    total        = subtotal + delivery_fee

    order = Order(
        farmer_id=listing.farmer_id,
        buyer_id=g.user.id,
        listing_id=listing.id,
        quantity_kg=qty,
        unit_price=listing.price_per_kg,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        commission=commission,
        total_amount=total,
        delivery_address=data['delivery_address'],
        delivery_district=data.get('delivery_district') or g.user.district,
        payment_method=data.get('payment_method', 'mobile_money'),
        payment_provider=data.get('payment_provider', 'mtn'),
        payment_phone=data.get('payment_phone') or normalize_phone(g.user.phone),
    )
    order.generate_reference()

    # Reduce remaining stock
    listing.remaining_kg -= qty
    if listing.remaining_kg <= 0:
        listing.is_active = False

    db.session.add(order)
    db.session.flush()

    # Notify farmer
    notify(listing.farmer_id,
           f'New Order: {order.reference}',
           f'{g.user.name} ordered {qty}kg {listing.crop} for UGX {total:,.0f}',
           ntype='order', ref_id=order.reference, send_sms_flag=True)

    # Notify buyer
    notify(g.user.id,
           f'Order Placed: {order.reference}',
           f'Your order for {qty}kg {listing.crop} has been placed. Total: UGX {total:,.0f}',
           ntype='order', ref_id=order.reference)

    db.session.commit()

    # Initiate payment if mobile money
    payment_error = None
    if order.payment_method == 'mobile_money':
        pay_phone = order.payment_phone or normalize_phone(g.user.phone)
        if order.payment_provider == 'airtel':
            success, tx_ref, pay_err = airtel.collect(total, pay_phone, order.reference)
        else:
            success, tx_ref, pay_err = mtn.request_to_pay(total, pay_phone, order.reference,
                                                           f'{qty}kg {listing.crop} from AgriBridge')
        if success:
            order.payment_reference = tx_ref
            # Create transaction record
            tx = Transaction(
                reference=f"TX-{order.reference}",
                order_id=order.id,
                user_id=g.user.id,
                amount=total,
                type='payment',
                provider=order.payment_provider,
                provider_reference=tx_ref,
                status='pending'
            )
            db.session.add(tx)
        else:
            payment_error = pay_err
            order.payment_status = 'pending'

    db.session.commit()

    resp = {
        'order': order.to_dict(full=True),
        'message': f'Order {order.reference} placed!',
        'total': total,
        'ref': order.reference,
    }
    if payment_error:
        resp['payment_warning'] = f'Payment initiation failed: {payment_error}. Use cash on delivery or retry.'
    else:
        resp['payment_message'] = 'Check your phone to approve the Mobile Money payment.'

    return ok(resp)


@app.route('/api/orders/<string:ref>', methods=['GET'])
@require_auth
def get_order(ref):
    order = Order.query.filter_by(reference=ref).first_or_404()
    if order.buyer_id != g.user.id and order.farmer_id != g.user.id and g.user.role != 'admin':
        return error('Not authorized', 403)
    return ok({'order': order.to_dict(full=True)})


@app.route('/api/orders/<string:ref>/confirm', methods=['POST'])
@require_auth
def confirm_order(ref):
    order = Order.query.filter_by(reference=ref).first_or_404()
    if order.farmer_id != g.user.id:
        return error('Only the farmer can confirm this order', 403)
    if order.order_status != 'pending':
        return error(f'Order is already {order.order_status}')

    order.order_status = 'confirmed'
    order.confirmed_at = datetime.utcnow()

    notify(order.buyer_id,
           f'Order Confirmed!',
           f'Farmer confirmed your order {ref}. Prepare for delivery.',
           ntype='order', ref_id=ref, send_sms_flag=True)

    db.session.commit()
    return ok({'message': 'Order confirmed'})


@app.route('/api/orders/<string:ref>/cancel', methods=['POST'])
@require_auth
def cancel_order(ref):
    order = Order.query.filter_by(reference=ref).first_or_404()
    if order.buyer_id != g.user.id and order.farmer_id != g.user.id and g.user.role != 'admin':
        return error('Not authorized', 403)
    if order.order_status in ('delivered', 'cancelled'):
        return error(f'Cannot cancel a {order.order_status} order')

    data = request.json or {}
    order.order_status = 'cancelled'
    order.cancellation_reason = data.get('reason', '')

    # Restore stock
    listing = Listing.query.get(order.listing_id)
    if listing:
        listing.remaining_kg += order.quantity_kg
        listing.is_active = True

    notify(order.farmer_id if order.buyer_id == g.user.id else order.buyer_id,
           f'Order Cancelled: {ref}',
           f'Order {ref} was cancelled. Reason: {order.cancellation_reason or "Not given"}',
           ntype='order', ref_id=ref, send_sms_flag=True)

    db.session.commit()
    return ok({'message': 'Order cancelled'})


@app.route('/api/my-orders', methods=['GET'])
@require_auth
def my_orders():
    role = request.args.get('role', 'buyer')  # buyer|farmer
    if role == 'farmer':
        q = Order.query.filter_by(farmer_id=g.user.id)
    else:
        q = Order.query.filter_by(buyer_id=g.user.id)
    q = q.order_by(Order.created_at.desc())
    return ok({'orders': [o.to_dict(full=True) for o in q.all()]})


@app.route('/api/orders/<string:ref>/payment-status', methods=['GET'])
@require_auth
def check_payment_status(ref):
    order = Order.query.filter_by(reference=ref).first_or_404()
    if order.buyer_id != g.user.id and g.user.role != 'admin':
        return error('Not authorized', 403)

    if order.payment_status == 'paid':
        return ok({'status': 'paid', 'message': 'Payment confirmed'})

    if order.payment_reference and order.payment_provider == 'mtn':
        status = mtn.check_status(order.payment_reference)
        if status == 'SUCCESSFUL':
            order.payment_status = 'paid'
            order.paid_at = datetime.utcnow()
            order.order_status = 'confirmed'

            # Release funds to farmer wallet
            wallet = Wallet.query.filter_by(user_id=order.farmer_id).first()
            if wallet:
                farmer_amount = order.subtotal - order.commission
                wallet.balance += farmer_amount
                wallet.total_earned += farmer_amount

            notify(order.farmer_id,
                   'Payment Received!',
                   f'UGX {order.total_amount:,.0f} received for {ref}. Prepare the order.',
                   ntype='payment', ref_id=ref, send_sms_flag=True)

            db.session.commit()
            return ok({'status': 'paid', 'message': 'Payment confirmed!'})

        elif status == 'FAILED':
            order.payment_status = 'failed'
            db.session.commit()
            return ok({'status': 'failed', 'message': 'Payment failed. Please retry.'})

    return ok({'status': order.payment_status, 'message': 'Payment pending customer approval'})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — PAYMENT WEBHOOKS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/webhooks/mtn', methods=['POST'])
def mtn_webhook():
    """MTN MoMo payment callback"""
    data = request.json or {}
    x_ref  = data.get('externalId') or data.get('referenceId')
    status = data.get('status', '')

    order = Order.query.filter_by(reference=x_ref).first()
    if not order:
        # Try by payment reference
        order = Order.query.filter_by(payment_reference=x_ref).first()
    if not order:
        return '', 200  # Acknowledge

    if status == 'SUCCESSFUL' and order.payment_status != 'paid':
        order.payment_status = 'paid'
        order.paid_at        = datetime.utcnow()
        order.order_status   = 'confirmed'

        wallet = Wallet.query.filter_by(user_id=order.farmer_id).first()
        if wallet:
            amount = order.subtotal - order.commission
            wallet.balance += amount
            wallet.total_earned += amount

        notify(order.farmer_id, 'Payment Received!',
               f'UGX {order.total_amount:,.0f} for order {order.reference}',
               ntype='payment', ref_id=order.reference, send_sms_flag=True)

        db.session.commit()

    elif status == 'FAILED':
        order.payment_status = 'failed'
        db.session.commit()

    return '', 200


@app.route('/api/webhooks/airtel', methods=['POST'])
def airtel_webhook():
    """Airtel Money payment callback"""
    data   = request.json or {}
    tx     = data.get('transaction', {})
    ref    = tx.get('id') or tx.get('airtel_money_id')
    status = tx.get('status_code')

    order = Order.query.filter_by(payment_reference=ref).first()
    if not order:
        return '', 200

    if status == 'TS' and order.payment_status != 'paid':  # TS = Transaction Successful
        order.payment_status = 'paid'
        order.paid_at        = datetime.utcnow()
        order.order_status   = 'confirmed'
        db.session.commit()

    return '', 200


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — DELIVERY
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/deliveries', methods=['POST'])
@require_auth
def create_delivery():
    data  = request.json or {}
    order_ref = data.get('order_ref', '')
    order = Order.query.filter_by(reference=order_ref).first()
    if not order:
        return error('Order not found')
    if order.farmer_id != g.user.id and g.user.role != 'admin':
        return error('Not authorized', 403)

    delivery = Delivery(
        order_id=order.id,
        farmer_id=order.farmer_id,
        driver_name=data.get('driver_name', 'Pending assignment'),
        driver_phone=data.get('driver_phone', SUPPORT_PHONE),
        vehicle_number=data.get('vehicle_number', ''),
        vehicle_type=data.get('vehicle_type', 'pickup'),
        pickup_location=data.get('pickup_location', g.user.district),
        pickup_district=data.get('pickup_district', g.user.district),
        dropoff_location=order.delivery_address,
        dropoff_district=order.delivery_district,
        cold_chain=bool(data.get('cold_chain', False)),
    )
    delivery.generate_reference()
    db.session.add(delivery)

    # Log event
    event = DeliveryEvent(
        delivery_id=None,  # Set after flush
        event_type='assigned',
        description=f"Delivery {delivery.reference} created for order {order_ref}"
    )

    order.order_status = 'processing'
    db.session.flush()
    event.delivery_id = delivery.id
    db.session.add(event)

    # Notify buyer
    notify(order.buyer_id,
           f'Delivery Assigned',
           f'Order {order_ref} is being prepared for delivery. Ref: {delivery.reference}',
           ntype='delivery', ref_id=delivery.reference, send_sms_flag=True)

    db.session.commit()
    return ok({'delivery': delivery.to_dict(), 'message': f'Delivery {delivery.reference} created'})


@app.route('/api/deliveries/<string:ref>', methods=['GET'])
def track_delivery(ref):
    delivery = Delivery.query.filter_by(reference=ref).first()
    if not delivery:
        # Also try by order reference
        order = Order.query.filter_by(reference=ref).first()
        if order and order.delivery:
            delivery = order.delivery
        else:
            return error('Delivery not found. Try DEL-XXXX-XXXXX format.', 404)

    d = delivery.to_dict(include_events=True)
    if delivery.order:
        o = delivery.order
        d['order'] = {
            'reference':   o.reference,
            'crop':        o.listing.crop if o.listing else None,
            'quantity_kg': o.quantity_kg,
            'total_amount': o.total_amount,
        }
    return ok({'delivery': d})


@app.route('/api/deliveries/<string:ref>/status', methods=['PATCH'])
@require_auth
def update_delivery_status(ref):
    delivery = Delivery.query.filter_by(reference=ref).first_or_404()
    if delivery.farmer_id != g.user.id and g.user.role != 'admin':
        return error('Not authorized', 403)

    data   = request.json or {}
    status = data.get('status', '')
    valid  = ['assigned', 'loading', 'picked_up', 'in_transit', 'at_destination', 'delivered', 'failed']
    if status not in valid:
        return error(f"Invalid status. Must be one of: {', '.join(valid)}")

    delivery.status          = status
    delivery.current_location= data.get('location', delivery.current_location)
    delivery.eta             = data.get('eta', delivery.eta)
    if data.get('gps_lat'):
        delivery.gps_lat = data['gps_lat']
        delivery.gps_lng = data.get('gps_lng')

    if status == 'picked_up':
        delivery.started_at = datetime.utcnow()
    elif status == 'delivered':
        delivery.delivered_at = datetime.utcnow()
        if delivery.order:
            delivery.order.order_status = 'delivered'
            delivery.order.delivered_at = datetime.utcnow()
            # Release payment to farmer
            wallet = Wallet.query.filter_by(user_id=delivery.farmer_id).first()
            if wallet and wallet.pending > 0:
                wallet.balance += wallet.pending
                wallet.pending  = 0
            # Update farmer metrics
            fp = FarmerProfile.query.filter_by(user_id=delivery.farmer_id).first()
            if fp:
                fp.completed_orders = (fp.completed_orders or 0) + 1

    # Log event
    evt = DeliveryEvent(
        delivery_id=delivery.id,
        event_type=status,
        location=delivery.current_location,
        description=data.get('description', f'Status updated to {status}'),
        gps_lat=delivery.gps_lat,
        gps_lng=delivery.gps_lng,
    )
    db.session.add(evt)

    # Notify buyer
    if delivery.order:
        notify(delivery.order.buyer_id,
               f'Delivery Update: {status.replace("_", " ").title()}',
               f'Your order {delivery.order.reference} — {status.replace("_", " ")}. '
               f'{f"ETA: {delivery.eta}" if delivery.eta else ""}',
               ntype='delivery', ref_id=ref, send_sms_flag=True)

    db.session.commit()
    return ok({'delivery': delivery.to_dict(), 'message': f'Status updated to {status}'})


@app.route('/api/deliveries/active', methods=['GET'])
@require_auth
def active_deliveries():
    active_statuses = ['assigned', 'loading', 'picked_up', 'in_transit', 'at_destination']
    if g.user.role == 'farmer':
        q = Delivery.query.filter_by(farmer_id=g.user.id).filter(Delivery.status.in_(active_statuses))
    elif g.user.role == 'admin':
        q = Delivery.query.filter(Delivery.status.in_(active_statuses))
    else:
        # Buyer
        order_ids = [o.id for o in g.user.orders_placed.all()]
        q = Delivery.query.filter(Delivery.order_id.in_(order_ids)).filter(Delivery.status.in_(active_statuses))

    return ok({'deliveries': [d.to_dict() for d in q.all()]})


@app.route('/api/deliveries/alerts', methods=['GET'])
def delivery_alerts():
    limit = request.args.get('limit', 10, type=int)
    events = DeliveryEvent.query.order_by(DeliveryEvent.created_at.desc()).limit(limit).all()
    alerts = []
    for e in events:
        d = e.delivery
        if not d:
            continue
        alerts.append({
            'delivery_ref': d.reference,
            'event_type':   e.event_type,
            'location':     e.location,
            'description':  e.description,
            'created_at':   e.created_at.isoformat(),
        })
    return ok({'alerts': alerts})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — PAYMENTS / WALLET
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/wallet', methods=['GET'])
@require_auth
def get_wallet():
    wallet = Wallet.query.filter_by(user_id=g.user.id).first()
    if not wallet:
        wallet = Wallet(user_id=g.user.id)
        db.session.add(wallet)
        db.session.commit()
    return ok({'wallet': {
        'balance':          wallet.balance,
        'pending':          wallet.pending,
        'total_earned':     wallet.total_earned,
        'total_withdrawn':  wallet.total_withdrawn,
    }})


@app.route('/api/wallet/withdraw', methods=['POST'])
@require_auth
def withdraw():
    data   = request.json or {}
    amount = float(data.get('amount', 0))
    if amount <= 0:
        return error('Invalid amount')

    wallet = Wallet.query.filter_by(user_id=g.user.id).first()
    if not wallet or wallet.balance < amount:
        return error('Insufficient balance')

    pay_phone    = data.get('phone') or normalize_phone(g.user.phone)
    provider     = data.get('provider', 'mtn')

    # Deduct first (atomic)
    wallet.balance      -= amount
    wallet.total_withdrawn += amount

    tx = Transaction(
        reference=f"WD-{uuid.uuid4().hex[:10].upper()}",
        user_id=g.user.id,
        amount=amount,
        type='withdrawal',
        provider=provider,
        status='pending'
    )
    db.session.add(tx)
    db.session.commit()

    # In production: call MTN/Airtel disbursements API here
    # For now simulate success
    tx.status       = 'success'
    tx.completed_at = datetime.utcnow()
    db.session.commit()

    sms.send(g.user.phone, f"AgriBridge: Withdrawal of UGX {amount:,.0f} to {pay_phone} is being processed. Ref: {tx.reference}")

    return ok({'message': f'Withdrawal of UGX {amount:,.0f} initiated. Ref: {tx.reference}',
               'new_balance': wallet.balance})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — REVIEWS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/reviews', methods=['POST'])
@require_auth
def leave_review():
    data     = request.json or {}
    order_ref = data.get('order_ref', '')
    order    = Order.query.filter_by(reference=order_ref).first()
    if not order:
        return error('Order not found')
    if order.buyer_id != g.user.id:
        return error('Only the buyer can review this order')
    if order.order_status != 'delivered':
        return error('Can only review delivered orders')
    if Review.query.filter_by(order_id=order.id).first():
        return error('You already reviewed this order')

    rating = int(data.get('rating', 0))
    if not (1 <= rating <= 5):
        return error('Rating must be between 1 and 5')

    review = Review(
        order_id=order.id,
        reviewer_id=g.user.id,
        reviewee_id=order.farmer_id,
        rating=rating,
        comment=data.get('comment', ''),
    )
    db.session.add(review)
    db.session.commit()

    update_farmer_rating(order.farmer_id)

    notify(order.farmer_id, f'New {rating}⭐ Review',
           f'{g.user.name} left a review for order {order_ref}',
           ntype='system', ref_id=order_ref)
    db.session.commit()

    return ok({'message': 'Review submitted!'})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — SEARCH & MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/search', methods=['GET'])
def search():
    q_str = request.args.get('q', '').strip()
    if len(q_str) < 2:
        return ok({'results': {}})

    pattern = f'%{q_str}%'

    listings = Listing.query.filter(
        Listing.is_active == True,
        or_(Listing.crop.ilike(pattern), Listing.district.ilike(pattern))
    ).limit(5).all()

    farmers = User.query.filter(
        User.role == 'farmer',
        or_(User.name.ilike(pattern), User.district.ilike(pattern))
    ).join(FarmerProfile, isouter=True).limit(5).all()

    return ok({'results': {
        'listings': [l.to_dict() for l in listings],
        'farmers':  [{'name': u.name, 'district': u.district,
                      'crops': u.farmer_profile.crops if u.farmer_profile else '',
                      'phone': u.phone} for u in farmers]
    }})


@app.route('/api/match', methods=['GET'])
def ai_match():
    crop     = request.args.get('crop', '').strip()
    district = request.args.get('district', '').strip()
    volume   = request.args.get('volume', 0, type=float)
    grade    = request.args.get('grade', 'B').upper()

    q = User.query.filter_by(role='farmer', is_active=True).join(FarmerProfile)

    if district:
        q = q.filter(or_(User.district.ilike(f'%{district}%')))

    farmers = q.all()
    matches = []

    for farmer in farmers:
        fp = farmer.farmer_profile
        if not fp:
            continue

        score = 0
        if crop and fp.crops and crop.lower() in (fp.crops or '').lower():
            score += 40
        if district and district.lower() in farmer.district.lower():
            score += 20
        if volume and fp.monthly_output_kg and fp.monthly_output_kg >= volume:
            score += 20
        if fp.storage_available:
            score += 10
        if fp.id_verified:
            score += 10

        if score > 0:
            matches.append({
                'name':             farmer.name,
                'phone':            farmer.phone,
                'district':         farmer.district,
                'crops':            fp.crops or '',
                'farm_size_acres':  fp.farm_size_acres,
                'monthly_output_kg':fp.monthly_output_kg,
                'storage_available':fp.storage_available,
                'irrigation':       fp.irrigation,
                'experience_years': fp.experience_years,
                'rating':           fp.rating,
                'verified':         fp.id_verified and fp.location_verified,
                'match_score':      min(100, score),
            })

    matches.sort(key=lambda x: x['match_score'], reverse=True)
    return ok({'matches': matches[:20]})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — PRICES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/prices', methods=['GET'])
def get_prices():
    crop     = request.args.get('crop', '')
    district = request.args.get('district', '')

    q = PriceData.query
    if crop:
        q = q.filter(PriceData.crop.ilike(f'%{crop}%'))
    if district:
        q = q.filter(PriceData.district.ilike(f'%{district}%'))

    prices = q.order_by(PriceData.recorded_at.desc()).limit(200).all()

    # Also get latest per crop for summary
    summary = db.session.query(
        PriceData.crop,
        func.avg(PriceData.price_per_kg).label('avg_price'),
        func.max(PriceData.price_per_kg).label('max_price'),
        func.min(PriceData.price_per_kg).label('min_price'),
        func.count(PriceData.id).label('data_points')
    ).group_by(PriceData.crop).all()

    return ok({
        'prices':  [{'crop': p.crop, 'district': p.district,
                     'price_per_kg': p.price_per_kg,
                     'recorded_at': p.recorded_at.isoformat()} for p in prices],
        'summary': [{'crop': s.crop, 'avg_price': round(s.avg_price, 0),
                     'max_price': s.max_price, 'min_price': s.min_price,
                     'data_points': s.data_points} for s in summary],
    })


@app.route('/api/prices', methods=['POST'])
@require_auth
def submit_price():
    """Crowdsource price data — any logged-in user can submit"""
    data = request.json or {}
    if not data.get('crop') or not data.get('price_per_kg') or not data.get('district'):
        return error('crop, price_per_kg and district required')

    p = PriceData(
        crop=data['crop'],
        district=data['district'],
        price_per_kg=float(data['price_per_kg']),
        source='crowdsourced',
        recorded_by=g.user.id
    )
    db.session.add(p)
    db.session.commit()
    return ok({'message': 'Price submitted. Thank you!'})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — FORWARD CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/contracts', methods=['POST'])
@require_auth
def create_contract():
    data = request.json or {}
    required = ['farmer_id', 'crop', 'quantity_kg', 'locked_price_per_kg', 'delivery_date']
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return error(f"Missing: {', '.join(missing)}")

    farmer = User.query.filter_by(uuid=data['farmer_id'], role='farmer').first()
    if not farmer:
        return error('Farmer not found')
    if farmer.id == g.user.id:
        return error("Can't contract with yourself")

    contract = ForwardContract(
        reference=f"FC-{datetime.utcnow().year}-{random.randint(10000,99999)}",
        farmer_id=farmer.id,
        buyer_id=g.user.id,
        crop=data['crop'],
        quantity_kg=float(data['quantity_kg']),
        locked_price_per_kg=float(data['locked_price_per_kg']),
        delivery_date=date.fromisoformat(data['delivery_date']),
        district=data.get('district', farmer.district),
        terms=data.get('terms', ''),
        deposit_percent=float(data.get('deposit_percent', 20)),
    )
    db.session.add(contract)

    notify(farmer.id, f'New Forward Contract!',
           f'{g.user.name} wants to contract {data["quantity_kg"]}kg {data["crop"]} at UGX {data["locked_price_per_kg"]}/kg',
           ntype='order', send_sms_flag=True)

    db.session.commit()
    return ok({'message': f'Contract {contract.reference} created!', 'reference': contract.reference})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — TRAINING
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/training', methods=['GET'])
def get_training():
    q = TrainingModule.query.filter_by(is_active=True)
    category = request.args.get('category', '')
    if category:
        q = q.filter(TrainingModule.category.ilike(f'%{category}%'))
    q = q.order_by(TrainingModule.views.desc())
    modules = q.all()
    return ok({'modules': [{
        'id': m.id, 'title': m.title, 'description': m.description,
        'category': m.category, 'level': m.level, 'video_url': m.video_url,
        'thumbnail_url': m.thumbnail_url, 'duration_mins': m.duration_mins,
        'language': m.language, 'views': m.views,
    } for m in modules]})


@app.route('/api/training/<int:mid>/view', methods=['POST'])
def training_view(mid):
    m = TrainingModule.query.get_or_404(mid)
    m.views = (m.views or 0) + 1
    db.session.commit()
    return ok({'views': m.views})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — USSD (Africa's Talking)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/ussd', methods=['POST'])
def ussd_handler():
    """Handle USSD requests from Africa's Talking gateway"""
    # Africa's Talking sends form data
    if request.content_type and 'form' in request.content_type:
        session_id   = request.form.get('sessionId', '')
        phone_number = request.form.get('phoneNumber', '')
        text         = request.form.get('text', '')
        service_code = request.form.get('serviceCode', USSD_CODE)
    else:
        # Web simulator sends JSON
        body         = request.json or {}
        session_id   = body.get('sessionId', f'web_{uuid.uuid4().hex[:8]}')
        phone_number = body.get('phoneNumber', '+256000000000')
        text         = body.get('text', '')
        service_code = body.get('serviceCode', USSD_CODE)

    phone_number = phone_number.lstrip('+')

    # Get or create session
    session = USSDSession.query.filter_by(session_id=session_id).first()
    if not session:
        session = USSDSession(session_id=session_id, phone_number=phone_number)
        db.session.add(session)

    # Process
    response = process_ussd(session, phone_number, text, service_code)

    session.updated_at = datetime.utcnow()
    db.session.commit()

    # Africa's Talking expects plain text
    if request.content_type and 'form' in request.content_type:
        from flask import Response
        return Response(response, mimetype='text/plain')

    # Web simulator: return JSON
    return ok({'response': response})


def process_ussd(session, phone, text, service_code):
    """Core USSD logic — returns CON ... (continue) or END ... (end session)"""
    levels = [t for t in text.split('*') if t] if text else []
    depth  = len(levels)

    # Look up user
    user = User.query.filter_by(phone=phone).first()

    # ── Level 0: Main menu ──
    if depth == 0:
        if user:
            return (f"CON Welcome back {user.name.split()[0]}! 🌿\n"
                    f"1. Market Prices\n2. List My Produce\n3. Find Buyers\n"
                    f"4. Training Tips\n5. Weather\n6. My Account\n7. Register")
        else:
            return ("CON Welcome to AgriBridge *789#\n"
                    "1. Register\n2. Market Prices\n3. Training")

    choice = levels[0]

    # ── Registration (unauthenticated) ──
    if not user and choice == '1':
        if depth == 1:
            return "CON Enter your full name:"
        if depth == 2:
            return "CON Choose district:\n1. Kampala\n2. Wakiso\n3. Gulu\n4. Mbarara\n5. Jinja\n6. Mbale\n7. Other"
        if depth == 3:
            districts = {1:'Kampala',2:'Wakiso',3:'Gulu',4:'Mbarara',5:'Jinja',6:'Mbale',7:'Uganda'}
            district  = districts.get(int(levels[2]), 'Uganda')
            try:
                new_user = User(
                    name=levels[1].strip(),
                    phone=phone,
                    role='farmer',
                    district=district,
                    is_verified=True,  # USSD = phone verified by definition
                )
                new_user.set_password(phone[-4:])
                db.session.add(new_user)
                db.session.flush()
                db.session.add(FarmerProfile(user_id=new_user.id, mobile_money_number=phone))
                db.session.add(Wallet(user_id=new_user.id))
                db.session.commit()
                sms.send(phone, f"Welcome to AgriBridge, {new_user.name}! Dial *789# anytime. Default password: last 4 digits of your number.")
            except Exception as e:
                return f"END Registration failed. Please call {SUPPORT_PHONE}."
            return f"END Registered! Welcome {new_user.name}. Dial *789# to start. SMS sent with details."

    # ── Guest: Market Prices ──
    if not user and choice == '2':
        prices = PriceData.query.order_by(PriceData.recorded_at.desc()).limit(5).all()
        if prices:
            lines = "\n".join([f"{p.crop}: UGX {p.price_per_kg:,.0f}/kg ({p.district})" for p in prices])
            return f"END Today's Prices:\n{lines}\n\nRegister: dial *789# and press 1"
        return "END No price data yet. Check agribridge.ug"

    # ── Guest: Training ──
    if not user and choice == '3':
        return "END Tip: Grade your produce A/B/C. Grade A gets 20-30% premium. Store in cool dry place. For full training: agribridge.ug/training"

    # ── Authenticated flows ──
    if user:

        # 1. Market Prices
        if choice == '1':
            if depth == 1:
                return "CON Select crop:\n1. Tomatoes\n2. Matooke\n3. Maize\n4. Carrots\n5. Beans\n6. Groundnuts"
            if depth == 2:
                crops = {1:'Tomatoes',2:'Matooke',3:'Maize',4:'Carrots',5:'Beans',6:'Groundnuts'}
                crop  = crops.get(int(levels[1]), 'Tomatoes')
                p     = PriceData.query.filter_by(crop=crop).order_by(PriceData.recorded_at.desc()).first()
                price = p.price_per_kg if p else 0
                listing = Listing.query.filter(Listing.crop.ilike(f'%{crop}%')).order_by(Listing.created_at.desc()).first()
                listing_price = listing.price_per_kg if listing else 0
                return (f"END {crop} Prices:\n"
                        f"Market avg: UGX {price:,.0f}/kg\n"
                        f"AgriBridge: UGX {listing_price:,.0f}/kg\n"
                        f"Check more at agribridge.ug")

        # 2. List Produce
        if choice == '2':
            if depth == 1:
                return "CON Enter crop name:"
            if depth == 2:
                return "CON Enter quantity (kg):"
            if depth == 3:
                return "CON Enter price per kg (UGX):"
            if depth == 4:
                try:
                    listing = Listing(
                        farmer_id=user.id,
                        crop=levels[1].strip(),
                        quantity_kg=float(levels[2]),
                        remaining_kg=float(levels[2]),
                        price_per_kg=float(levels[3]),
                        district=user.district,
                        quality_grade='B',
                    )
                    db.session.add(listing)
                    db.session.commit()
                    return (f"END Listed! {listing.crop}: {listing.quantity_kg}kg at UGX {listing.price_per_kg:,.0f}/kg "
                            f"in {user.district}. Buyers will contact you.")
                except Exception:
                    return f"END Error. Try again or call {SUPPORT_PHONE}"

        # 3. Find Buyers
        if choice == '3':
            if depth == 1:
                return "CON Find buyers:\n1. Vendors\n2. Hotels\n3. Exporters"
            if depth == 2:
                role_map = {1:'vendor', 2:'hotel', 3:'buyer'}
                role = role_map.get(int(levels[1]), 'vendor')
                buyers = User.query.filter_by(role=role, district=user.district, is_active=True).limit(3).all()
                if buyers:
                    lines = "\n".join([f"{b.name}: {b.phone}" for b in buyers])
                    return f"END {role.title()}s near {user.district}:\n{lines}"
                return f"END No {role}s in {user.district} yet. Try agribridge.ug for wider search."

        # 4. Training
        if choice == '4':
            if depth == 1:
                return "CON Training topics:\n1. Soil & Crop Care\n2. Post-Harvest Storage\n3. Pricing & Negotiation\n4. Mobile Money\n5. Weather & Planting"
            if depth == 2:
                tips = {
                    1: "Water crops early morning. Test soil pH 6-7 for most vegetables. Rotate crops each season.",
                    2: "Use hermetic bags for grains — prevents 95% weevil damage. Dry to <14% moisture before storing.",
                    3: "Grade A produce = 20-30% premium. Group with neighbors to sell in bulk for better prices.",
                    4: "Use MTN *165# or Airtel *185# to receive payments. Never share your PIN. Save receipts.",
                    5: "Plant rains begin March-April (South) and March-May (North). Harvest before heavy October rains.",
                }
                return f"END Tip: {tips.get(int(levels[1]), 'Visit agribridge.ug for full training library.')}"

        # 5. Weather
        if choice == '5':
            if depth == 1:
                return "CON Select district:\n1. Kampala\n2. Wakiso\n3. Gulu\n4. Mbarara\n5. Jinja"
            if depth == 2:
                d_map = {1:'Kampala',2:'Wakiso',3:'Gulu',4:'Mbarara',5:'Jinja'}
                district = d_map.get(int(levels[1]), user.district)
                return f"END {district}: 26°C, partly cloudy. Good farming conditions this week. Plant before Friday. Expect light showers weekend."

        # 6. My Account
        if choice == '6':
            if depth == 1:
                wallet = Wallet.query.filter_by(user_id=user.id).first()
                balance = wallet.balance if wallet else 0
                listings = Listing.query.filter_by(farmer_id=user.id, is_active=True).count()
                return (f"CON {user.name}\n"
                        f"Role: {user.role.title()}\n"
                        f"District: {user.district}\n"
                        f"Wallet: UGX {balance:,.0f}\n"
                        f"Active listings: {listings}\n\n"
                        f"1. My Listings\n2. My Orders\n3. Change Password")
            if depth == 2:
                if levels[1] == '1':
                    listings = Listing.query.filter_by(farmer_id=user.id, is_active=True).limit(3).all()
                    if listings:
                        lines = "\n".join([f"{l.crop}: {l.remaining_kg}kg @ UGX {l.price_per_kg:,.0f}" for l in listings])
                        return f"END Your active listings:\n{lines}"
                    return "END No active listings. Press 2 from main menu to list produce."
                if levels[1] == '2':
                    orders = Order.query.filter_by(farmer_id=user.id).order_by(Order.created_at.desc()).limit(3).all()
                    if orders:
                        lines = "\n".join([f"{o.reference}: {o.order_status}" for o in orders])
                        return f"END Recent orders:\n{lines}"
                    return "END No orders yet."
                if levels[1] == '3':
                    return "CON Enter new password (min 6 chars):"
            if depth == 3 and levels[1] == '3':
                if len(levels[2]) < 6:
                    return "END Password too short. Must be 6+ characters. Try again."
                user.set_password(levels[2])
                db.session.commit()
                return "END Password changed successfully!"

    return f"END Invalid option. Dial {USSD_CODE} to start again."


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — SMS COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/sms', methods=['POST'])
def sms_handler():
    """Handle inbound SMS commands"""
    data    = request.json or request.form.to_dict() or {}
    message = (data.get('message') or data.get('text', '')).strip().upper()
    phone   = normalize_phone(data.get('phone') or data.get('from', ''))

    parts = message.split()
    cmd   = parts[0] if parts else ''

    user = User.query.filter_by(phone=phone).first()

    reply = ""

    if cmd == 'PRICES':
        prices = PriceData.query.order_by(PriceData.recorded_at.desc()).limit(6).all()
        if prices:
            lines  = "\n".join([f"{p.crop}: {p.price_per_kg:,.0f}/kg" for p in prices])
            reply  = f"AgriBridge Prices ({datetime.utcnow().strftime('%d/%m')}):\n{lines}"
        else:
            reply = "No price data yet. Visit agribridge.ug"

    elif cmd == 'WEATHER':
        district = parts[1].title() if len(parts) > 1 else (user.district if user else 'Kampala')
        reply = f"AgriBridge Weather: {district}: 26°C, partly cloudy. Good conditions. Expect rain Fri-Sat. Plant Tuesday-Wednesday."

    elif cmd == 'LIST' and len(parts) >= 4:
        # LIST Tomatoes 200 1500
        if not user:
            reply = f"Register first: Dial {USSD_CODE} or call {SUPPORT_PHONE}"
        else:
            try:
                crop, qty, price = parts[1].title(), float(parts[2]), float(parts[3])
                listing = Listing(
                    farmer_id=user.id, crop=crop,
                    quantity_kg=qty, remaining_kg=qty,
                    price_per_kg=price, district=user.district, quality_grade='B'
                )
                db.session.add(listing)
                db.session.commit()
                reply = f"AgriBridge: Listed {qty}kg {crop} @ UGX {price:,.0f}/kg in {user.district}. Buyers will see it immediately."
            except Exception:
                reply = f"Error. Format: LIST CropName Quantity Price. Example: LIST Tomatoes 100 1500"

    elif cmd == 'TRACK' and len(parts) > 1:
        ref      = parts[1].upper()
        delivery = Delivery.query.filter_by(reference=ref).first()
        if delivery:
            reply = f"AgriBridge: Order {delivery.reference} is {delivery.status.replace('_',' ')}. Driver: {delivery.driver_name} {delivery.driver_phone}. ETA: {delivery.eta or 'TBD'}"
        else:
            reply = f"Delivery {ref} not found. Call {SUPPORT_PHONE}"

    elif cmd == 'BAL' or cmd == 'BALANCE':
        if not user:
            reply = f"Register first: Dial {USSD_CODE}"
        else:
            wallet = Wallet.query.filter_by(user_id=user.id).first()
            balance = wallet.balance if wallet else 0
            reply = f"AgriBridge Wallet: UGX {balance:,.0f}. To withdraw, dial {USSD_CODE} → My Account."

    elif cmd == 'JOIN' or cmd == 'REGISTER':
        if user:
            reply = f"You're already registered, {user.name}! Dial {USSD_CODE} for services."
        else:
            reply = f"Register on AgriBridge: Dial {USSD_CODE} or visit agribridge.ug. Support: {SUPPORT_PHONE}"

    elif cmd == 'HELP':
        reply = (f"AgriBridge SMS Commands:\n"
                 f"PRICES - Market prices\n"
                 f"WEATHER [District] - Forecast\n"
                 f"LIST Crop Qty Price - New listing\n"
                 f"TRACK DEL-XXXX - Track delivery\n"
                 f"BAL - Wallet balance\n"
                 f"HELP - This menu\n"
                 f"Support: {SUPPORT_PHONE}")

    else:
        reply = f"AgriBridge: Unknown command '{cmd}'. Text HELP for commands. Dial {USSD_CODE} for full menu."

    # Send reply SMS
    if phone and reply:
        sms.send(phone, reply)

    return ok({'reply': reply})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/notifications', methods=['GET'])
@require_auth
def get_notifications():
    unread_only = request.args.get('unread') == '1'
    q = Notification.query.filter_by(user_id=g.user.id).order_by(Notification.created_at.desc())
    if unread_only:
        q = q.filter_by(is_read=False)
    notes = q.limit(50).all()
    unread_count = Notification.query.filter_by(user_id=g.user.id, is_read=False).count()
    return ok({
        'notifications': [{'id': n.id, 'title': n.title, 'body': n.body,
                           'type': n.type, 'ref_id': n.ref_id, 'is_read': n.is_read,
                           'created_at': n.created_at.isoformat()} for n in notes],
        'unread_count': unread_count,
    })


@app.route('/api/notifications/read', methods=['POST'])
@require_auth
def mark_read():
    data = request.json or {}
    ids  = data.get('ids', [])
    if ids:
        Notification.query.filter(Notification.id.in_(ids), Notification.user_id == g.user.id).update({'is_read': True}, synchronize_session=False)
    else:
        Notification.query.filter_by(user_id=g.user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return ok({'message': 'Marked as read'})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — DISTRICTS & STATS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/districts', methods=['GET'])
def get_districts():
    rows = db.session.query(
        User.district,
        func.count(User.id).label('farmers')
    ).filter_by(role='farmer', is_active=True).group_by(User.district).all()

    district_map = {r.district: r.farmers for r in rows}

    result = []
    for d in UGANDAN_DISTRICTS:
        farmers = district_map.get(d, 0)
        result.append({'name': d, 'farmers': farmers, 'produce': 'Varies'})

    return ok({'districts': result})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    farmers      = User.query.filter_by(role='farmer', is_active=True).count()
    vendors      = User.query.filter_by(role='vendor', is_active=True).count()
    listings     = Listing.query.filter_by(is_active=True).count()
    orders_total = Order.query.count()
    delivered    = Order.query.filter_by(order_status='delivered').count()
    revenue      = db.session.query(func.sum(Order.total_amount)).filter(Order.payment_status == 'paid').scalar() or 0

    return ok({'stats': {
        'farmers':       farmers,
        'vendors':       vendors,
        'active_listings': listings,
        'total_orders':  orders_total,
        'delivered_orders': delivered,
        'revenue_ugx':   revenue,
        'districts_covered': db.session.query(func.count(func.distinct(User.district))).filter_by(role='farmer').scalar() or 0,
    }})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — CONTACT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/contact', methods=['POST'])
def contact():
    data = request.json or {}
    if not data.get('name') or not data.get('message'):
        return error('Name and message required')
    msg = ContactMessage(
        name=data['name'].strip(),
        email=data.get('email', ''),
        phone=data.get('phone', ''),
        message=data['message'].strip(),
    )
    db.session.add(msg)
    db.session.commit()
    # Alert admin
    sms.send(SUPPORT_PHONE, f"AgriBridge Contact: {msg.name} — {msg.message[:80]}")
    return ok({'message': 'Message received! We will respond within 24 hours.'})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES — ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard():
    farmers  = User.query.filter_by(role='farmer').count()
    vendors  = User.query.filter_by(role='vendor').count()
    hotels   = User.query.filter_by(role='hotel').count()
    buyers   = User.query.filter_by(role='buyer').count()
    orders   = Order.query.count()
    pending  = Order.query.filter_by(payment_status='pending').count()
    revenue  = db.session.query(func.sum(Order.total_amount)).filter(Order.payment_status == 'paid').scalar() or 0
    messages = ContactMessage.query.filter_by(is_read=False).count()

    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()
    recent_users  = User.query.order_by(User.created_at.desc()).limit(10).all()

    return ok({
        'stats': {'farmers': farmers, 'vendors': vendors, 'hotels': hotels,
                  'buyers': buyers, 'orders': orders, 'pending_payments': pending,
                  'revenue_ugx': revenue, 'unread_messages': messages},
        'recent_orders': [o.to_dict(full=True) for o in recent_orders],
        'recent_users':  [u.to_dict() for u in recent_users],
    })


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_users():
    role   = request.args.get('role', '')
    q = User.query
    if role:
        q = q.filter_by(role=role)
    page  = request.args.get('page', 1, type=int)
    items, total, pages = paginate(q.order_by(User.created_at.desc()), page, 30)
    return ok({'users': [u.to_dict(include_profile=True) for u in items],
               'total': total, 'page': page, 'pages': pages})


@app.route('/api/admin/users/<string:uid>/verify', methods=['POST'])
@admin_required
def admin_verify_user(uid):
    user = User.query.filter_by(uuid=uid).first_or_404()
    user.is_verified = True
    if user.farmer_profile:
        user.farmer_profile.id_verified       = True
        user.farmer_profile.location_verified = True
    db.session.commit()
    sms.send(user.phone, f"AgriBridge: Your account is now verified! You'll get priority in search results. Thank you.")
    notify(user.id, '✅ Account Verified!',
           'Your account has been verified by the AgriBridge team. You now appear as a verified farmer.',
           ntype='system', send_sms_flag=False)
    db.session.commit()
    return ok({'message': f'{user.name} verified'})


@app.route('/api/admin/users/<string:uid>/suspend', methods=['POST'])
@admin_required
def admin_suspend_user(uid):
    user = User.query.filter_by(uuid=uid).first_or_404()
    user.is_active = not user.is_active
    db.session.commit()
    action = 'reactivated' if user.is_active else 'suspended'
    return ok({'message': f'{user.name} {action}'})


@app.route('/api/admin/orders', methods=['GET'])
@admin_required
def admin_orders():
    status = request.args.get('status', '')
    q = Order.query
    if status:
        q = q.filter_by(order_status=status)
    page  = request.args.get('page', 1, type=int)
    items, total, pages = paginate(q.order_by(Order.created_at.desc()), page, 30)
    return ok({'orders': [o.to_dict(full=True) for o in items],
               'total': total, 'page': page, 'pages': pages})


@app.route('/api/admin/messages', methods=['GET'])
@admin_required
def admin_messages():
    msgs = ContactMessage.query.order_by(ContactMessage.created_at.desc()).limit(50).all()
    return ok({'messages': [{'id': m.id, 'name': m.name, 'email': m.email,
                              'phone': m.phone, 'message': m.message,
                              'is_read': m.is_read,
                              'created_at': m.created_at.isoformat()} for m in msgs]})


@app.route('/api/admin/messages/<int:mid>/read', methods=['POST'])
@admin_required
def admin_read_message(mid):
    msg = ContactMessage.query.get_or_404(mid)
    msg.is_read   = True
    msg.replied_at = datetime.utcnow()
    db.session.commit()
    return ok({'message': 'Marked as read'})


@app.route('/api/admin/broadcast', methods=['POST'])
@admin_required
def admin_broadcast():
    data    = request.json or {}
    message = data.get('message', '')
    role    = data.get('role', '')  # '' = all, 'farmer', 'vendor' etc.
    if not message:
        return error('Message required')

    q = User.query.filter_by(is_active=True)
    if role:
        q = q.filter_by(role=role)

    users = q.all()
    phones = [u.phone for u in users if u.phone]
    sms.broadcast(phones, f"AgriBridge Broadcast: {message}")
    return ok({'message': f'Broadcast sent to {len(phones)} users'})


@app.route('/api/admin/training', methods=['POST'])
@admin_required
def admin_add_training():
    data = request.json or {}
    if not data.get('title') or not data.get('category'):
        return error('Title and category required')
    m = TrainingModule(
        title=data['title'],
        description=data.get('description', ''),
        category=data['category'],
        level=data.get('level', 'Beginner'),
        video_url=data.get('video_url', ''),
        thumbnail_url=data.get('thumbnail_url', ''),
        duration_mins=data.get('duration_mins'),
        language=data.get('language', 'English'),
    )
    db.session.add(m)
    db.session.commit()
    return ok({'message': 'Training module added', 'id': m.id})


@app.route('/api/admin/training/<int:mid>', methods=['DELETE'])
@admin_required
def admin_delete_training(mid):
    m = TrainingModule.query.get_or_404(mid)
    m.is_active = False
    db.session.commit()
    return ok({'message': 'Training module removed'})


@app.route('/api/admin/prices', methods=['POST'])
@admin_required
def admin_add_price():
    """Admin can bulk-submit MAAIF price data"""
    data = request.json or {}
    entries = data.get('entries', [data])  # support single or bulk
    count = 0
    for entry in entries:
        if entry.get('crop') and entry.get('price_per_kg') and entry.get('district'):
            p = PriceData(
                crop=entry['crop'],
                district=entry['district'],
                price_per_kg=float(entry['price_per_kg']),
                source=entry.get('source', 'maaif'),
            )
            db.session.add(p)
            count += 1
    db.session.commit()
    return ok({'message': f'{count} price records added'})


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE INIT & SEED
# ═══════════════════════════════════════════════════════════════════════════════

def seed_data():
    """Seed realistic data so the platform isn't empty on first launch"""
    if User.query.count() > 0:
        return  # Already seeded

    print("Seeding initial data...")

    # Admin user
    admin = User(name='AgriBridge Admin', phone='256755966690', email='admin@agribridge.ug',
                 role='admin', district='Kampala', is_verified=True, is_active=True)
    admin.set_password(app.config['ADMIN_PASSWORD'])
    db.session.add(admin)

    # Sample farmers
    farmers_data = [
        {'name': 'Grace Nakato', 'phone': '256701234567', 'district': 'Wakiso',
         'farm': 'Grace Organic Farm', 'crops': 'Tomatoes,Carrots,Cabbages', 'acres': 3.5,
         'output': 1200, 'storage': True, 'irrigation': True, 'exp': 8},
        {'name': 'Peter Mugisha', 'phone': '256702345678', 'district': 'Gulu',
         'farm': 'Mugisha Maize Gardens', 'crops': 'Maize,Beans,Groundnuts', 'acres': 12.0,
         'output': 5000, 'storage': True, 'irrigation': False, 'exp': 15},
        {'name': 'Fatuma Nalwoga', 'phone': '256703456789', 'district': 'Mbarara',
         'farm': 'Nalwoga Green Farm', 'crops': 'Matooke,Potatoes,Sweet Potatoes', 'acres': 2.0,
         'output': 800, 'storage': False, 'irrigation': False, 'exp': 5},
        {'name': 'John Okello', 'phone': '256704567890', 'district': 'Jinja',
         'farm': 'Okello Fresh Produce', 'crops': 'Tomatoes,Onions,Pepper', 'acres': 4.0,
         'output': 1500, 'storage': True, 'irrigation': True, 'exp': 10},
        {'name': 'Sarah Auma', 'phone': '256705678901', 'district': 'Kampala',
         'farm': 'Auma Urban Garden', 'crops': 'Spinach,Kale,Lettuce', 'acres': 0.5,
         'output': 200, 'storage': False, 'irrigation': True, 'exp': 3},
    ]

    farmer_users = []
    for fd in farmers_data:
        u = User(name=fd['name'], phone=fd['phone'], role='farmer',
                 district=fd['district'], is_verified=True, is_active=True)
        u.set_password('agri1234')
        fp = FarmerProfile(
            farm_name=fd['farm'], crops=fd['crops'],
            farm_size_acres=fd['acres'], monthly_output_kg=fd['output'],
            storage_available=fd['storage'], irrigation=fd['irrigation'],
            experience_years=fd['exp'], mobile_money_number=fd['phone'],
            mobile_money_provider='mtn', id_verified=True, location_verified=True,
            rating=round(random.uniform(3.8, 5.0), 1),
            total_orders=random.randint(10, 80),
            completed_orders=random.randint(8, 70),
        )
        u.farmer_profile = fp
        db.session.add(u)
        farmer_users.append((u, fd))

    # Sample vendor
    vendor_user = User(name='Mukasa Traders', phone='256706789012', role='vendor',
                       district='Kampala', is_verified=True, is_active=True)
    vendor_user.set_password('agri1234')
    vp = VendorProfile(business_name='Mukasa Fresh Produce', market_name='Nakasero Market',
                       market_district='Kampala', product_categories='Vegetables,Fruits,Grains',
                       weekly_volume_kg=2000, mobile_money_number='256706789012')
    vendor_user.vendor_profile = vp
    db.session.add(vendor_user)

    # Sample hotel
    hotel_user = User(name='Serena Hotel Kampala', phone='256707890123', role='hotel',
                      district='Kampala', is_verified=True, is_active=True)
    hotel_user.set_password('agri1234')
    hp = HotelProfile(hotel_name='Serena Gardens Hotel', star_rating=5,
                      weekly_produce_kg=500, delivery_days='Mon,Wed,Fri',
                      delivery_time='07:00', mobile_money_number='256707890123')
    hotel_user.hotel_profile = hp
    db.session.add(hotel_user)

    db.session.flush()

    # Create wallets for all
    for obj in [admin, vendor_user, hotel_user]:
        db.session.add(Wallet(user_id=obj.id))

    # Seed listings
    listing_data = [
        {'crop': 'Tomatoes', 'qty': 500, 'price': 1500, 'grade': 'A', 'delivery': True},
        {'crop': 'Maize',    'qty': 3000,'price': 700,  'grade': 'B', 'delivery': True},
        {'crop': 'Matooke',  'qty': 800, 'price': 800,  'grade': 'A', 'delivery': False},
        {'crop': 'Carrots',  'qty': 300, 'price': 1200, 'grade': 'A', 'delivery': True},
        {'crop': 'Onions',   'qty': 400, 'price': 1800, 'grade': 'B', 'delivery': True},
        {'crop': 'Kale',     'qty': 150, 'price': 500,  'grade': 'A', 'delivery': False},
    ]
    for i, ld in enumerate(listing_data):
        fu, fd = farmer_users[i % len(farmer_users)]
        listing = Listing(
            farmer_id=fu.id, crop=ld['crop'],
            quantity_kg=ld['qty'], remaining_kg=ld['qty'],
            price_per_kg=ld['price'], quality_grade=ld['grade'],
            district=fu.district, delivery_available=ld['delivery'],
            is_active=True, views=random.randint(10, 200),
        )
        db.session.add(listing)
        db.session.add(PriceData(crop=ld['crop'], district=fu.district,
                                 price_per_kg=ld['price'], source='listing'))

    # Seed training modules
    training_data = [
        {'title': 'Modern Tomato Farming', 'cat': 'Crop Science', 'level': 'Beginner',
         'url': 'https://www.youtube.com/embed/t5UNSWNpJEM', 'mins': 24, 'lang': 'English',
         'desc': 'Learn modern techniques for high-yield tomato farming in Uganda.'},
        {'title': 'Post-Harvest Storage', 'cat': 'Post-Harvest', 'level': 'Intermediate',
         'url': 'https://www.youtube.com/embed/3JZ_D3ELwOQ', 'mins': 18, 'lang': 'English',
         'desc': 'Reduce post-harvest losses by 60% with proper storage techniques.'},
        {'title': 'Mobile Money for Farmers', 'cat': 'Finance', 'level': 'Beginner',
         'url': 'https://www.youtube.com/embed/dQw4w9WgXcQ', 'mins': 15, 'lang': 'Luganda',
         'desc': 'Using MTN and Airtel Mobile Money to receive payments safely.'},
        {'title': 'Soil Testing & Nutrition', 'cat': 'Crop Science', 'level': 'Advanced',
         'url': 'https://www.youtube.com/embed/ScMzIvxBSi4', 'mins': 32, 'lang': 'English',
         'desc': 'Understanding soil pH, nutrients and fertiliser application rates.'},
        {'title': 'Grading Produce for Premium Prices', 'cat': 'Business', 'level': 'Beginner',
         'url': 'https://www.youtube.com/embed/3JZ_D3ELwOQ', 'mins': 12, 'lang': 'English',
         'desc': 'How to grade produce A/B/C and earn 20-30% more per kilogram.'},
        {'title': 'Digital Marketing for Farmers', 'cat': 'Digital Literacy', 'level': 'Beginner',
         'url': 'https://www.youtube.com/embed/t5UNSWNpJEM', 'mins': 20, 'lang': 'English',
         'desc': 'Using phones and AgriBridge to reach more buyers and get better prices.'},
    ]
    for td in training_data:
        db.session.add(TrainingModule(
            title=td['title'], description=td['desc'], category=td['cat'],
            level=td['level'], video_url=td['url'], duration_mins=td['mins'],
            language=td['lang'], views=random.randint(50, 500),
        ))

    db.session.commit()
    print("✅ Seed data loaded successfully")


with app.app_context():
    db.create_all()
    seed_data()
    print("✅ AgriBridge Production Backend ready")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_ENV') == 'development')
