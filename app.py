from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, DateField, FloatField, SelectField, TextAreaField, IntegerField
from wtforms.validators import DataRequired, Email, EqualTo, NumberRange, Length
from datetime import datetime
from sqlalchemy import or_, text, func, inspect
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename
import qrcode
import io
import tempfile
import os
from uuid import uuid4
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
os.makedirs(app.instance_path, exist_ok=True)
default_sqlite_db = os.path.join(app.instance_path, 'safar_suvidha.db')
database_url = os.environ.get('DATABASE_URL', f"sqlite:///{default_sqlite_db}")
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['CAR_UPLOAD_FOLDER'] = os.path.join(app.static_folder, 'uploads', 'cars')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
# For MySQL, you can switch the line above to:
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://username:password@localhost/safar_suvidha'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
ADMIN_ROLES = {'super_admin', 'admin', 'boss', 'manager'}

def is_admin_role(role_name):
    return role_name in ADMIN_ROLES

def is_super_admin(user):
    return user.role in {'super_admin', 'admin'}

def is_boss(user):
    return user.role == 'boss'

def has_full_control(user):
    return is_super_admin(user) or is_boss(user)

def is_manager_approved(user):
    return user.role == 'manager' and user.approval_status == 'approved'

def can_manage_operations(user):
    return is_super_admin(user) or is_boss(user) or is_manager_approved(user)

def dashboard_endpoint_for(user):
    if is_super_admin(user):
        return 'super_admin_dashboard'
    if is_boss(user):
        return 'boss_dashboard'
    if user.role == 'manager':
        return 'manager_dashboard'
    return 'home'

def apply_company_scope(query, company_column, user_obj):
    if is_super_admin(user_obj):
        return query
    if not user_obj.company_id:
        return query.filter(company_column == -1)
    return query.filter(company_column == user_obj.company_id)

def ensure_booking_columns():
    with db.engine.begin() as conn:
        cols = {col['name'] for col in inspect(db.engine).get_columns('booking')}
        if db.engine.name == 'sqlite':
            if 'payment_status' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN payment_status VARCHAR(50) DEFAULT \'pending\''))
            if 'payment_method' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN payment_method VARCHAR(50)'))
            if 'transaction_id' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN transaction_id VARCHAR(100)'))
            if 'created_at' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN created_at DATETIME'))
        else:
            if 'payment_status' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN IF NOT EXISTS payment_status VARCHAR(50) DEFAULT \'pending\''))
            if 'payment_method' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50)'))
            if 'transaction_id' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN IF NOT EXISTS transaction_id VARCHAR(100)'))
            if 'created_at' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN IF NOT EXISTS created_at TIMESTAMP'))
        conn.execute(text('UPDATE "booking" SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL'))

def ensure_user_columns():
    with db.engine.begin() as conn:
        cols = {col['name'] for col in inspect(db.engine).get_columns('user')}
        if db.engine.name == 'sqlite':
            if 'approval_status' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN approval_status VARCHAR(30) DEFAULT \'approved\''))
            if 'approved_by_id' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN approved_by_id INTEGER'))
            if 'company_name' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN company_name VARCHAR(150)'))
            if 'company_address' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN company_address VARCHAR(250)'))
            if 'boss_id' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN boss_id INTEGER'))
            if 'company_id' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN company_id INTEGER'))
        else:
            if 'approval_status' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS approval_status VARCHAR(30) DEFAULT \'approved\''))
            if 'approved_by_id' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS approved_by_id INTEGER'))
            if 'company_name' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS company_name VARCHAR(150)'))
            if 'company_address' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS company_address VARCHAR(250)'))
            if 'boss_id' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS boss_id INTEGER'))
            if 'company_id' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS company_id INTEGER'))
        conn.execute(text('UPDATE "user" SET role = \'super_admin\' WHERE role = \'admin\''))
        conn.execute(text('UPDATE "user" SET approval_status = \'approved\' WHERE approval_status IS NULL'))
        conn.execute(text('UPDATE "user" SET approval_status = \'pending\' WHERE role = \'manager\' AND approval_status = \'approved\''))
        conn.execute(text('UPDATE "user" SET boss_id = approved_by_id WHERE role = \'manager\' AND boss_id IS NULL AND approved_by_id IS NOT NULL'))

def ensure_user_profile_columns():
    with db.engine.begin() as conn:
        cols = {col['name'] for col in inspect(db.engine).get_columns('user')}
        if db.engine.name == 'sqlite':
            if 'full_name' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN full_name VARCHAR(150)'))
            if 'gender' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN gender VARCHAR(20)'))
            if 'age' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN age INTEGER'))
            if 'driving_license_no' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN driving_license_no VARCHAR(100)'))
            if 'has_driving_license' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN has_driving_license VARCHAR(10) DEFAULT \'no\''))
        else:
            if 'full_name' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS full_name VARCHAR(150)'))
            if 'gender' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS gender VARCHAR(20)'))
            if 'age' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS age INTEGER'))
            if 'driving_license_no' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS driving_license_no VARCHAR(100)'))
            if 'has_driving_license' not in cols:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS has_driving_license VARCHAR(10) DEFAULT \'no\''))

        conn.execute(text('UPDATE "user" SET full_name = username WHERE full_name IS NULL OR TRIM(full_name) = \'\''))
        conn.execute(text('UPDATE "user" SET gender = \'not_specified\' WHERE gender IS NULL OR TRIM(gender) = \'\''))
        conn.execute(text('UPDATE "user" SET age = 18 WHERE age IS NULL AND role IN (\'super_admin\', \'admin\', \'boss\', \'manager\', \'customer\')'))
        conn.execute(text("""
            UPDATE "user"
            SET has_driving_license = CASE
                WHEN driving_license_no IS NOT NULL AND TRIM(driving_license_no) != '' THEN 'yes'
                ELSE 'no'
            END
            WHERE has_driving_license IS NULL OR TRIM(has_driving_license) = ''
        """))

def ensure_car_columns():
    with db.engine.begin() as conn:
        cols = {col['name'] for col in inspect(db.engine).get_columns('car')}
        if db.engine.name == 'sqlite':
            if 'company_id' not in cols:
                conn.execute(text('ALTER TABLE "car" ADD COLUMN company_id INTEGER'))
        else:
            if 'company_id' not in cols:
                conn.execute(text('ALTER TABLE "car" ADD COLUMN IF NOT EXISTS company_id INTEGER'))

def ensure_booking_company_column():
    with db.engine.begin() as conn:
        cols = {col['name'] for col in inspect(db.engine).get_columns('booking')}
        if db.engine.name == 'sqlite':
            if 'company_id' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN company_id INTEGER'))
        else:
            if 'company_id' not in cols:
                conn.execute(text('ALTER TABLE "booking" ADD COLUMN IF NOT EXISTS company_id INTEGER'))

def ensure_support_columns():
    with db.engine.begin() as conn:
        cols = {col['name'] for col in inspect(db.engine).get_columns('support_request')}
        if db.engine.name == 'sqlite':
            if 'requester_role' not in cols:
                conn.execute(text('ALTER TABLE "support_request" ADD COLUMN requester_role VARCHAR(30) DEFAULT \'user\''))
            if 'company_name' not in cols:
                conn.execute(text('ALTER TABLE "support_request" ADD COLUMN company_name VARCHAR(150)'))
        else:
            if 'requester_role' not in cols:
                conn.execute(text('ALTER TABLE "support_request" ADD COLUMN IF NOT EXISTS requester_role VARCHAR(30) DEFAULT \'user\''))
            if 'company_name' not in cols:
                conn.execute(text('ALTER TABLE "support_request" ADD COLUMN IF NOT EXISTS company_name VARCHAR(150)'))
        conn.execute(text('UPDATE "support_request" SET requester_role = \'user\' WHERE requester_role IS NULL'))
        conn.execute(text("""
            UPDATE "support_request"
            SET requester_role = 'admin'
            WHERE user_id IN (
                SELECT id FROM "user" WHERE role IN ('super_admin', 'admin', 'boss', 'manager')
            )
        """))
        conn.execute(text("""
            UPDATE "support_request"
            SET company_name = (
                SELECT c.name
                FROM "user" AS u
                JOIN company AS c ON c.id = u.company_id
                WHERE u.id = "support_request".user_id
            )
            WHERE company_name IS NULL
        """))

def save_car_photo(photo_file):
    if not photo_file or not photo_file.filename:
        return None
    os.makedirs(app.config['CAR_UPLOAD_FOLDER'], exist_ok=True)
    original_name = secure_filename(photo_file.filename)
    _, ext = os.path.splitext(original_name)
    unique_name = f"car_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex[:8]}{ext.lower()}"
    absolute_path = os.path.join(app.config['CAR_UPLOAD_FOLDER'], unique_name)
    photo_file.save(absolute_path)
    return os.path.join('uploads', 'cars', unique_name).replace('\\', '/')

def ensure_default_super_admin():
    default_username = 'king'
    default_password = 'developer'
    default_email = 'king@example.com'

    super_admin_user = User.query.filter_by(role='super_admin').order_by(User.id.asc()).first()
    king_user = User.query.filter_by(username=default_username).first()

    if super_admin_user:
        # If "king" username is already used by some other account, keep existing username but set password.
        if king_user and king_user.id != super_admin_user.id:
            super_admin_user.password = default_password
        else:
            super_admin_user.username = default_username
            super_admin_user.password = default_password
            if not super_admin_user.email:
                super_admin_user.email = default_email
        if not super_admin_user.full_name:
            super_admin_user.full_name = 'King'
        if not super_admin_user.gender:
            super_admin_user.gender = 'male'
        if not super_admin_user.age or super_admin_user.age < 18:
            super_admin_user.age = 30
        if not super_admin_user.has_driving_license:
            super_admin_user.has_driving_license = 'no'
        super_admin_user.approval_status = 'approved'
        db.session.commit()
        return

    if king_user:
        king_user.role = 'super_admin'
        king_user.password = default_password
        if not king_user.full_name:
            king_user.full_name = 'King'
        if not king_user.gender:
            king_user.gender = 'male'
        if not king_user.age or king_user.age < 18:
            king_user.age = 30
        if not king_user.has_driving_license:
            king_user.has_driving_license = 'no'
        king_user.approval_status = 'approved'
        db.session.commit()
        return

    new_super_admin = User(
        username=default_username,
        full_name='King',
        email=default_email,
        gender='male',
        age=30,
        has_driving_license='no',
        password=default_password,
        role='super_admin',
        approval_status='approved'
    )
    db.session.add(new_super_admin)
    db.session.commit()

def backfill_company_links():
    changed = False

    bosses = User.query.filter_by(role='boss').all()
    for boss in bosses:
        company_name = (boss.company_name or f"Company-{boss.id}").strip()
        existing_company = Company.query.filter(func.lower(Company.name) == company_name.lower()).first()
        if not existing_company:
            existing_company = Company(name=company_name)
            db.session.add(existing_company)
            db.session.flush()
            changed = True
        if boss.company_id != existing_company.id:
            boss.company_id = existing_company.id
            changed = True
        if existing_company.owner_id != boss.id:
            existing_company.owner_id = boss.id
            changed = True
        if not boss.company_name:
            boss.company_name = existing_company.name
            changed = True

    managers = User.query.filter_by(role='manager').all()
    for manager in managers:
        target_company_id = None
        if manager.boss_id:
            boss_user = db.session.get(User, manager.boss_id)
            if boss_user and boss_user.company_id:
                target_company_id = boss_user.company_id
        if not target_company_id and manager.company_name:
            company = Company.query.filter(func.lower(Company.name) == manager.company_name.lower()).first()
            if company:
                target_company_id = company.id
        if target_company_id and manager.company_id != target_company_id:
            manager.company_id = target_company_id
            changed = True

    customers = User.query.filter_by(role='customer').all()
    for customer in customers:
        if customer.company_id is None and customer.company_name:
            company = Company.query.filter(func.lower(Company.name) == customer.company_name.lower()).first()
            if company:
                customer.company_id = company.id
                changed = True

    company_ids = [c.id for c in Company.query.all()]
    single_company_id = company_ids[0] if len(company_ids) == 1 else None

    for car in Car.query.filter(Car.company_id.is_(None)).all():
        if single_company_id:
            car.company_id = single_company_id
            changed = True

    for booking in Booking.query.filter(Booking.company_id.is_(None)).all():
        inferred_company_id = None
        if booking.user and booking.user.company_id:
            inferred_company_id = booking.user.company_id
        elif booking.car and booking.car.company_id:
            inferred_company_id = booking.car.company_id
        elif single_company_id:
            inferred_company_id = single_company_id
        if inferred_company_id:
            booking.company_id = inferred_company_id
            changed = True

    if changed:
        db.session.commit()

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    full_name = db.Column(db.String(150))
    email = db.Column(db.String(150), unique=True, nullable=False)
    gender = db.Column(db.String(20))
    age = db.Column(db.Integer)
    driving_license_no = db.Column(db.String(100))
    has_driving_license = db.Column(db.String(10), default='no')
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), default='customer')
    approval_status = db.Column(db.String(30), default='approved')
    approved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    company_name = db.Column(db.String(150))
    company_address = db.Column(db.String(250))
    boss_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'))
    bookings = db.relationship('Booking', backref='user')

class Car(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    model = db.Column(db.String(150), nullable=False)
    city = db.Column(db.String(150), nullable=False)
    price_per_day = db.Column(db.Float, nullable=False)
    available = db.Column(db.Boolean, default=True)
    image = db.Column(db.String(300))
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'))

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    car_id = db.Column(db.Integer, db.ForeignKey('car.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    total_cost = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='confirmed')
    payment_status = db.Column(db.String(50), default='pending')
    payment_method = db.Column(db.String(50))
    transaction_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'))
    car = db.relationship('Car', backref='bookings')

class SupportRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    username = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    requester_role = db.Column(db.String(30), default='user')
    company_name = db.Column(db.String(150))
    request_type = db.Column(db.String(30), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default='open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='support_requests')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notification_type = db.Column(db.String(30), default='notification')
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_notifications')
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='received_notifications')

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    full_name = StringField('Full Name', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    gender = SelectField(
        'Gender',
        choices=[('male', 'Male'), ('female', 'Female'), ('other', 'Other')],
        validators=[DataRequired()]
    )
    age = IntegerField('Age', validators=[DataRequired(), NumberRange(min=1, max=120)])
    has_driving_license = SelectField(
        'Do You Have Driving License?',
        choices=[('no', 'No'), ('yes', 'Yes')],
        validate_choice=False
    )
    driving_license_no = StringField('Driving License Number', validators=[Length(max=100)])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    company_name = StringField('Company Name')
    company_address = StringField('Company Address')
    submit = SubmitField('Register')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class CarForm(FlaskForm):
    name = StringField('Car Name', validators=[DataRequired()])
    model = StringField('Model', validators=[DataRequired()])
    city = StringField('City', validators=[DataRequired()])
    price_per_day = FloatField('Price per Day', validators=[DataRequired()])
    photo = FileField('Car Photo', validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Only image files are allowed.')])
    company_name = StringField('Company Name (for Super Admin)')
    submit = SubmitField('Add Car')

class BookingForm(FlaskForm):
    start_date = DateField('Start Date', validators=[DataRequired()], render_kw={"type": "date"})
    end_date = DateField('End Date', validators=[DataRequired()], render_kw={"type": "date"})
    submit = SubmitField('Book')

class PaymentForm(FlaskForm):
    payment_method = SelectField(
        'Payment Method',
        choices=[('upi', 'UPI'), ('card', 'Card'), ('cash', 'Cash')],
        validators=[DataRequired()]
    )
    submit = SubmitField('Pay Now')

class ActionForm(FlaskForm):
    submit = SubmitField('Submit')

class AdminAccessForm(FlaskForm):
    access_code = PasswordField('Admin Access Code', validators=[DataRequired()])
    submit = SubmitField('Continue')

class CompanyForm(FlaskForm):
    name = StringField('Company Name', validators=[DataRequired()])
    submit = SubmitField('Create Company')

class SupportForm(FlaskForm):
    name = StringField('Name')
    email = StringField('Email')
    requester_role = SelectField(
        'Who Are You?',
        choices=[('user', 'User'), ('admin', 'Admin')]
    )
    company_name = StringField('Company Name')
    request_type = SelectField(
        'Type',
        choices=[('help', 'Help'), ('complaint', 'Complaint')],
        validators=[DataRequired()]
    )
    message = TextAreaField('Message', validators=[DataRequired()])
    submit = SubmitField('Send Request')

class NotificationForm(FlaskForm):
    recipient_id = SelectField('Send To', coerce=int, validators=[DataRequired()])
    notification_type = SelectField(
        'Message Type',
        choices=[('notification', 'Notification'), ('order', 'Order')],
        validators=[DataRequired()]
    )
    message = TextAreaField('Message', validators=[DataRequired()])
    submit = SubmitField('Send')

def notification_recipient_choices(sender_id):
    company_map = {c.id: c.name for c in Company.query.all()}
    users = User.query.filter(User.id != sender_id).order_by(User.id.asc()).all()
    choices = [(-1, 'All Users')]
    for user in users:
        company_name = company_map.get(user.company_id) or user.company_name or '-'
        display_name = user.full_name or user.username
        label = f"{display_name} (@{user.username}, {user.role}) - {company_name}"
        choices.append((user.id, label))
    return choices

@app.before_request
def initialize_schema_once():
    if app.config.get('_schema_initialized'):
        return
    db.create_all()
    ensure_user_columns()
    ensure_user_profile_columns()
    ensure_booking_columns()
    ensure_car_columns()
    ensure_booking_company_column()
    ensure_support_columns()
    backfill_company_links()
    ensure_default_super_admin()
    os.makedirs(app.config['CAR_UPLOAD_FOLDER'], exist_ok=True)
    app.config['_schema_initialized'] = True

@app.route('/')
def index():
    return home()

@app.route('/home')
def home():
    search = request.args.get('q', '').strip()
    cars_query = Car.query.filter_by(available=True)
    active_company_name = None

    if current_user.is_authenticated:
        if not is_super_admin(current_user):
            cars_query = apply_company_scope(cars_query, Car.company_id, current_user)
            if current_user.company_id:
                company = db.session.get(Company, current_user.company_id)
                active_company_name = company.name if company else None
        else:
            active_company_name = 'All Companies'

    if search:
        like = f"%{search}%"
        cars_query = cars_query.filter(
            or_(
                Car.name.ilike(like),
                Car.model.ilike(like),
                Car.city.ilike(like)
            )
        )
    cars = cars_query.all()
    return render_template('home.html', cars=cars, search=search, active_company_name=active_company_name)

@app.route('/auth/user')
def user_portal():
    return render_template('user_portal.html')

@app.route('/auth/admin', methods=['GET', 'POST'])
def admin_access():
    form = AdminAccessForm()
    if form.validate_on_submit():
        code = form.access_code.data.strip().upper()
        if code == 'BOSS123':
            session['admin_access_role'] = 'boss'
            flash('Boss code verified. Please create your account.')
            return redirect(url_for('register', role='boss'))
        if code == 'MANAGER123':
            session['admin_access_role'] = 'manager'
            flash('Manager code verified. Please create your account.')
            return redirect(url_for('register', role='manager'))
        session.pop('admin_access_role', None)
        flash('Invalid admin code')
        return redirect(url_for('admin_access'))
    return render_template('admin_code.html', form=form)

@app.route('/support', methods=['GET', 'POST'])
def support():
    form = SupportForm()
    is_logged_in_admin = current_user.is_authenticated and is_admin_role(current_user.role)
    detected_role = 'admin' if is_logged_in_admin else 'user'
    detected_company_name = ''
    if current_user.is_authenticated:
        if current_user.company_id:
            company = db.session.get(Company, current_user.company_id)
            if company:
                detected_company_name = company.name
        if not detected_company_name:
            detected_company_name = (current_user.company_name or '').strip()

    if request.method == 'GET' and current_user.is_authenticated:
        form.name.data = current_user.username
        form.email.data = current_user.email
        form.requester_role.data = detected_role
        form.company_name.data = detected_company_name

    if form.validate_on_submit():
        if current_user.is_authenticated:
            requester_name = current_user.full_name or current_user.username
            requester_email = current_user.email
            requester_id = current_user.id
            requester_role = detected_role
            requester_company_name = detected_company_name
            if requester_role == 'admin' and not requester_company_name:
                requester_company_name = (form.company_name.data or '').strip()
                if not requester_company_name:
                    flash('Admin request must include company name.')
                    return redirect(url_for('support'))
        else:
            requester_name = (form.name.data or '').strip()
            requester_email = (form.email.data or '').strip()
            requester_id = None
            requester_role = (form.requester_role.data or 'user').strip().lower()
            if requester_role not in {'user', 'admin'}:
                requester_role = 'user'
            requester_company_name = (form.company_name.data or '').strip() if requester_role == 'admin' else ''
            if not requester_name or not requester_email:
                flash('Name and email are required for guest requests.')
                return redirect(url_for('support'))
            if requester_role == 'admin' and not requester_company_name:
                flash('Admin request must include company name.')
                return redirect(url_for('support'))

        new_request = SupportRequest(
            user_id=requester_id,
            username=requester_name,
            email=requester_email,
            requester_role=requester_role,
            company_name=requester_company_name or None,
            request_type=form.request_type.data,
            message=form.message.data.strip(),
            status='open'
        )
        db.session.add(new_request)
        db.session.commit()
        flash('Your request has been sent to king.')
        return redirect(url_for('home'))
    return render_template('support.html', form=form, is_logged_in_admin=is_logged_in_admin)

@app.context_processor
def inject_unread_notifications():
    if not current_user.is_authenticated:
        return {'unread_notifications': 0}
    unread_count = Notification.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()
    return {'unread_notifications': unread_count}

@app.route('/notifications')
@login_required
def notifications():
    notification_items = Notification.query.filter_by(
        recipient_id=current_user.id
    ).order_by(Notification.id.desc()).all()
    action_form = ActionForm()
    return render_template('notifications.html', notifications=notification_items, action_form=action_form)

@app.route('/notifications/read/<int:notification_id>', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for('notifications'))
    notification = Notification.query.filter_by(
        id=notification_id,
        recipient_id=current_user.id
    ).first_or_404()
    notification.is_read = True
    db.session.commit()
    return redirect(url_for('notifications'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    selected_role = request.args.get('role', request.form.get('selected_role', 'customer')).strip().lower()
    if selected_role not in {'customer', 'boss', 'manager'}:
        selected_role = 'customer'
    if selected_role in {'boss', 'manager'} and session.get('admin_access_role') != selected_role:
        flash('Please verify admin code first.')
        return redirect(url_for('admin_access'))

    form = RegistrationForm()
    if form.validate_on_submit():
        full_name = (form.full_name.data or '').strip()
        age_value = form.age.data or 0
        has_driving_license = (form.has_driving_license.data or 'no').strip().lower()
        driving_license_no = (form.driving_license_no.data or '').strip()
        registration_note = None
        if not full_name:
            flash('Full name is required.')
            return redirect(url_for('register', role=selected_role))
        if age_value < 18:
            flash('Registration requires age 18 or above.')
            return redirect(url_for('register', role=selected_role))
        if selected_role == 'customer':
            if has_driving_license not in {'yes', 'no'}:
                has_driving_license = 'no'
            if has_driving_license == 'yes' and not driving_license_no:
                flash('Please enter driving license number when you select Yes.')
                return redirect(url_for('register', role='customer'))
            if has_driving_license == 'no':
                driving_license_no = None
        else:
            has_driving_license = 'no'
            driving_license_no = None

        existing_user = User.query.filter(
            or_(
                User.email == form.email.data,
                User.username == form.username.data
            )
        ).first()
        if existing_user:
            flash('Username or email already registered!')
            return redirect(url_for('register', role=selected_role))

        company_name = (form.company_name.data or '').strip()
        company_address = (form.company_address.data or '').strip()
        selected_company = None

        if selected_role == 'boss':
            if not company_name or not company_address:
                flash('Boss registration needs company name and company address.')
                return redirect(url_for('register', role='boss'))
            duplicate_company = Company.query.filter(func.lower(Company.name) == company_name.lower()).first()
            if duplicate_company:
                flash('Company already exists. Boss must use a unique company.')
                return redirect(url_for('register', role='boss'))

        manager_boss = None
        if selected_role == 'manager':
            if not company_name:
                flash('Manager registration needs company name.')
                return redirect(url_for('register', role='manager'))
            selected_company = Company.query.filter(func.lower(Company.name) == company_name.lower()).first()
            if not selected_company:
                flash('Company not found. Please contact your boss.')
                return redirect(url_for('register', role='manager'))
            if not selected_company.owner_id:
                flash('Company is not linked to a boss yet.')
                return redirect(url_for('register', role='manager'))
            manager_boss = db.session.get(User, selected_company.owner_id)
            if not manager_boss or manager_boss.role != 'boss':
                flash('Company boss not found.')
                return redirect(url_for('register', role='manager'))
            if manager_boss.company_id != selected_company.id:
                flash('Company boss mapping is invalid.')
                return redirect(url_for('register', role='manager'))

        if selected_role == 'customer':
            available_companies = Company.query.order_by(Company.id.asc()).all()
            if len(available_companies) == 1:
                selected_company = available_companies[0]
                company_name = selected_company.name
            elif len(available_companies) == 0:
                company_name = None
                registration_note = 'Account created. Boss must create company before booking.'
            else:
                company_name = None
                registration_note = 'Account created without company link. Ask your boss to map your account.'

        user = User(
            username=form.username.data,
            full_name=full_name,
            email=form.email.data,
            gender=form.gender.data,
            age=age_value,
            has_driving_license=has_driving_license,
            driving_license_no=driving_license_no,
            password=form.password.data,
            role=selected_role,
            approval_status='pending' if selected_role == 'manager' else 'approved',
            company_name=company_name if selected_role in {'boss', 'manager', 'customer'} else None,
            company_address=company_address if selected_role == 'boss' else None,
            boss_id=manager_boss.id if manager_boss else None,
            company_id=selected_company.id if selected_company else None
        )
        db.session.add(user)

        try:
            # Boss creates a new company and becomes owner.
            if selected_role == 'boss':
                db.session.flush()
                new_company = Company(name=company_name, owner_id=user.id)
                db.session.add(new_company)
                db.session.flush()
                user.company_id = new_company.id
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Username or email already registered!')
            return redirect(url_for('register', role=selected_role))
        if selected_role == 'manager':
            flash('Manager account created. Wait for boss approval before managing operations.')
        else:
            flash('Registration successful! Now login.')
            if registration_note:
                flash(registration_note)
        if selected_role in {'boss', 'manager'}:
            session.pop('admin_access_role', None)
        return redirect(url_for('login'))
    return render_template('register.html', form=form, selected_role=selected_role)

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.password == form.password.data:
            login_user(user)
            if user.role == 'manager' and user.approval_status != 'approved':
                flash('Manager account is pending boss approval.')
                return redirect(url_for('manager_dashboard'))
            return redirect(url_for(dashboard_endpoint_for(user)))
        flash('Invalid credentials')
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/car/<int:car_id>', methods=['GET', 'POST'])
@login_required
def book_car(car_id):
    if current_user.role != 'customer':
        flash('Only customer accounts can book cars.')
        return redirect(url_for('home'))
    if not current_user.age or current_user.age < 18:
        flash('Only 18+ users can book cars.')
        return redirect(url_for('home'))
    if not current_user.company_id:
        flash('Customer account is not linked to any company.')
        return redirect(url_for('home'))
    car = Car.query.filter_by(id=car_id, available=True, company_id=current_user.company_id).first()
    if not car:
        flash('Car not found in your company.')
        return redirect(url_for('home'))
    form = BookingForm()
    if form.validate_on_submit():
        start_date = form.start_date.data
        end_date = form.end_date.data
        days = (end_date - start_date).days
        if days <= 0:
            flash('End date must be after start date')
            return redirect(url_for('book_car', car_id=car_id))
        overlapping = Booking.query.filter(
            Booking.car_id == car_id,
            Booking.company_id == current_user.company_id,
            Booking.status != 'cancelled',
            Booking.start_date < end_date,
            Booking.end_date > start_date
        ).first()
        if overlapping:
            flash('This car is already booked for the selected dates.')
            return redirect(url_for('book_car', car_id=car_id))
        base_cost = days * car.price_per_day
        gst = base_cost * 0.18
        total_cost = base_cost + gst
        booking = Booking(
            user_id=current_user.id,
            car_id=car_id,
            start_date=start_date,
            end_date=end_date,
            total_cost=total_cost,
            status='pending',
            payment_status='pending',
            company_id=current_user.company_id
        )
        db.session.add(booking)
        db.session.commit()
        flash('Booking created! Please complete payment.')
        return redirect(url_for('payment', booking_id=booking.id))
    return render_template('book.html', car=car, form=form)

@app.route('/payment/<int:booking_id>', methods=['GET', 'POST'])
@login_required
def payment(booking_id):
    booking_query = Booking.query.filter_by(id=booking_id)
    booking_query = apply_company_scope(booking_query, Booking.company_id, current_user)
    booking = booking_query.first_or_404()
    if booking.user_id != current_user.id and not can_manage_operations(current_user) and not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    if booking.payment_status == 'paid':
        return redirect(url_for('invoice', booking_id=booking.id))
    form = PaymentForm()
    if form.validate_on_submit():
        booking.payment_status = 'paid'
        booking.payment_method = form.payment_method.data
        booking.status = 'confirmed'
        booking.transaction_id = f"TXN{booking.id}{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        db.session.commit()
        flash('Payment successful!')
        return redirect(url_for('invoice', booking_id=booking.id))
    return render_template('payment.html', booking=booking, form=form)

@app.route('/invoice/<int:booking_id>')
@login_required
def invoice(booking_id):
    booking_query = Booking.query.filter_by(id=booking_id)
    booking_query = apply_company_scope(booking_query, Booking.company_id, current_user)
    booking = booking_query.first_or_404()
    if booking.user_id != current_user.id and not can_manage_operations(current_user) and not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    if booking.payment_status != 'paid':
        flash('Please complete payment to download invoice.')
        return redirect(url_for('payment', booking_id=booking.id))
    
    # Generate QR code
    qr_data = (
        f"Booking ID: {booking.id}\n"
        f"User: {booking.user.username}\n"
        f"Car: {booking.car.name}\n"
        f"Total: {booking.total_cost}"
    )
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    
    # Save QR to temp file
    temp_dir = tempfile.mkdtemp()
    qr_path = os.path.join(temp_dir, 'qr.png')
    img.save(qr_path)
    
    # Generate PDF
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.drawString(100, 750, "Safar Suvidha Invoice")
    p.drawString(100, 730, f"Booking ID: {booking.id}")
    p.drawString(100, 710, f"User: {booking.user.username}")
    p.drawString(100, 690, f"Car: {booking.car.name} ({booking.car.model})")
    p.drawString(100, 670, f"City: {booking.car.city}")
    p.drawString(100, 650, f"Start Date: {booking.start_date}")
    p.drawString(100, 630, f"End Date: {booking.end_date}")
    days = (booking.end_date - booking.start_date).days
    base_cost = days * booking.car.price_per_day
    gst = base_cost * 0.18
    p.drawString(100, 610, f"Days: {days}")
    p.drawString(100, 590, f"Base Cost: {base_cost}")
    p.drawString(100, 570, f"GST (18%): {gst}")
    p.drawString(100, 550, f"Total Cost: {booking.total_cost}")
    if booking.transaction_id:
        p.drawString(100, 530, f"Transaction ID: {booking.transaction_id}")
        p.drawString(100, 510, f"Payment Method: {booking.payment_method}")
    p.drawString(100, 490, "GST Compliant Invoice")
    
    # Add QR code
    qr_img = ImageReader(qr_path)
    p.drawImage(qr_img, 400, 500, width=100, height=100)
    
    p.showPage()
    p.save()
    buffer.seek(0)
    
    # Clean up temp file
    os.remove(qr_path)
    os.rmdir(temp_dir)
    
    return send_file(buffer, as_attachment=True, download_name=f'invoice_{booking.id}.pdf', mimetype='application/pdf')

@app.route('/my_bookings')
@login_required
def my_bookings():
    if current_user.role != 'customer':
        flash('Only customer accounts have booking history.')
        return redirect(url_for('home'))
    bookings = Booking.query.filter_by(
        user_id=current_user.id,
        company_id=current_user.company_id
    ).order_by(Booking.id.desc()).all()
    return render_template('bookings.html', bookings=bookings)

def build_admin_context(user_obj):
    users_query = User.query
    cars_query = Car.query
    bookings_query = Booking.query
    companies = []
    support_requests = []
    sent_notifications = []
    notification_form = NotificationForm()

    if not is_super_admin(user_obj):
        users_query = users_query.filter_by(company_id=user_obj.company_id)
        cars_query = cars_query.filter_by(company_id=user_obj.company_id)
        bookings_query = bookings_query.filter_by(company_id=user_obj.company_id)
    else:
        companies = Company.query.order_by(Company.id.asc()).all()
        support_requests = SupportRequest.query.order_by(SupportRequest.id.asc()).all()
        sent_notifications = Notification.query.filter_by(sender_id=user_obj.id).order_by(Notification.id.asc()).all()
        notification_form.recipient_id.choices = notification_recipient_choices(user_obj.id)

    if is_super_admin(user_obj):
        users = users_query.order_by(User.id.asc()).all()
        cars = cars_query.order_by(Car.id.asc()).all() if can_manage_operations(user_obj) else []
        bookings = bookings_query.order_by(Booking.id.asc()).all() if can_manage_operations(user_obj) else []
    else:
        users = users_query.order_by(User.id.desc()).all()
        cars = cars_query.order_by(Car.id.desc()).all() if can_manage_operations(user_obj) else []
        bookings = bookings_query.order_by(Booking.id.desc()).all() if can_manage_operations(user_obj) else []

    manager_requests = []
    if is_boss(user_obj):
        requests_query = User.query.filter_by(role='manager', approval_status='pending')
        requests_query = requests_query.filter_by(company_id=user_obj.company_id, boss_id=user_obj.id)
        manager_requests = requests_query.order_by(User.id.desc()).all()

    action_form = ActionForm()
    company_form = CompanyForm()
    approved_by_map = {}
    approver_ids = {u.approved_by_id for u in users if u.approved_by_id}
    if approver_ids:
        approvers = User.query.filter(User.id.in_(approver_ids)).all()
        approved_by_map = {u.id: u.username for u in approvers}

    boss_map = {}
    boss_ids = {u.boss_id for u in users if u.boss_id}
    if boss_ids:
        bosses = User.query.filter(User.id.in_(boss_ids)).all()
        boss_map = {u.id: u.username for u in bosses}

    company_map = {}
    company_ids = {u.company_id for u in users if u.company_id}
    company_ids.update({c.company_id for c in cars if c.company_id})
    company_ids.update({b.company_id for b in bookings if b.company_id})
    company_ids.update({c.id for c in companies})
    if company_ids:
        linked_companies = Company.query.filter(Company.id.in_(company_ids)).all()
        company_map = {c.id: c.name for c in linked_companies}

    active_company = db.session.get(Company, user_obj.company_id) if user_obj.company_id else None
    return {
        'users': users,
        'cars': cars,
        'bookings': bookings,
        'companies': companies,
        'support_requests': support_requests,
        'manager_requests': manager_requests,
        'action_form': action_form,
        'company_form': company_form,
        'notification_form': notification_form,
        'sent_notifications': sent_notifications,
        'approved_by_map': approved_by_map,
        'boss_map': boss_map,
        'company_map': company_map,
        'current_company_name': active_company.name if active_company else None
    }

@app.route('/admin')
@login_required
def admin():
    if not is_admin_role(current_user.role):
        flash('Access denied')
        return redirect(url_for('home'))
    return redirect(url_for(dashboard_endpoint_for(current_user)))

@app.route('/dashboard/boss')
@login_required
def boss_dashboard():
    if not is_boss(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    context = build_admin_context(current_user)
    return render_template('boss_dashboard.html', **context)

@app.route('/dashboard/manager')
@login_required
def manager_dashboard():
    if current_user.role != 'manager':
        flash('Access denied')
        return redirect(url_for('home'))
    context = build_admin_context(current_user)
    context['manager_pending'] = current_user.approval_status != 'approved'
    return render_template('manager_dashboard.html', **context)

@app.route('/manager')
@login_required
def manager_dashboard_alias():
    return redirect(url_for('manager_dashboard'))

@app.route('/dashboard/superadmin')
@login_required
def super_admin_dashboard():
    if not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    context = build_admin_context(current_user)
    return render_template('super_admin_dashboard.html', **context)

@app.route('/superadmin/send_notification', methods=['POST'])
@login_required
def send_super_admin_notification():
    if not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    form = NotificationForm()
    form.recipient_id.choices = notification_recipient_choices(current_user.id)
    if not form.validate_on_submit():
        flash('Invalid notification request')
        return redirect(url_for('super_admin_dashboard'))

    message_text = (form.message.data or '').strip()
    if not message_text:
        flash('Message cannot be empty.')
        return redirect(url_for('super_admin_dashboard'))

    target_id = form.recipient_id.data
    target_users = []
    if target_id == -1:
        target_users = User.query.filter(User.id != current_user.id).order_by(User.id.asc()).all()
        if not target_users:
            flash('No users available to receive this message.')
            return redirect(url_for('super_admin_dashboard'))
    else:
        target_user = db.session.get(User, target_id)
        if not target_user or target_user.id == current_user.id:
            flash('Selected user is invalid.')
            return redirect(url_for('super_admin_dashboard'))
        target_users = [target_user]

    for user in target_users:
        notification_item = Notification(
            sender_id=current_user.id,
            recipient_id=user.id,
            notification_type=form.notification_type.data,
            message=message_text,
            is_read=False
        )
        db.session.add(notification_item)
    db.session.commit()
    flash(f"{form.notification_type.data.title()} sent to {len(target_users)} user(s).")
    return redirect(url_for('super_admin_dashboard'))

@app.route('/superadmin')
@login_required
def super_admin_dashboard_alias():
    return redirect(url_for('super_admin_dashboard'))

@app.route('/superadmin/support/resolve/<int:request_id>', methods=['POST'])
@login_required
def resolve_support_request(request_id):
    if not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for('super_admin_dashboard'))
    support_item = db.session.get(SupportRequest, request_id)
    if not support_item:
        flash('Request not found.')
        return redirect(url_for('super_admin_dashboard'))
    support_item.status = 'resolved'
    db.session.commit()
    flash('Request marked as resolved.')
    return redirect(url_for('super_admin_dashboard'))

@app.route('/admin/add_car', methods=['GET', 'POST'])
@login_required
def add_car():
    if not can_manage_operations(current_user):
        if current_user.role == 'manager':
            flash('Manager approval required from boss.')
        else:
            flash('Access denied')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    form = CarForm()
    if form.validate_on_submit():
        target_company_id = current_user.company_id
        if is_super_admin(current_user):
            company_name = (form.company_name.data or '').strip()
            if not company_name:
                flash('Super admin must enter company name to add a car.')
                return redirect(url_for('add_car'))
            company = Company.query.filter(func.lower(Company.name) == company_name.lower()).first()
            if not company:
                flash('Company not found.')
                return redirect(url_for('add_car'))
            target_company_id = company.id
        if not target_company_id:
            flash('No company linked for this action.')
            return redirect(url_for(dashboard_endpoint_for(current_user)))
        image_path = save_car_photo(form.photo.data)
        car = Car(
            name=form.name.data,
            model=form.model.data,
            city=form.city.data,
            price_per_day=form.price_per_day.data,
            image=image_path,
            company_id=target_company_id
        )
        db.session.add(car)
        db.session.commit()
        flash('Car added successfully!')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    if request.method == 'POST':
        for field_errors in form.errors.values():
            if field_errors:
                flash(field_errors[0])
                break
    return render_template('add_car.html', form=form)

@app.route('/admin/delete_car/<int:car_id>', methods=['POST'])
@login_required
def delete_car(car_id):
    if not can_manage_operations(current_user):
        if current_user.role == 'manager':
            flash('Manager approval required from boss.')
        else:
            flash('Access denied')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    car_query = Car.query.filter_by(id=car_id)
    car_query = apply_company_scope(car_query, Car.company_id, current_user)
    car = car_query.first_or_404()
    active_booking_query = Booking.query.filter(
        Booking.car_id == car_id,
        Booking.status != 'cancelled'
    )
    active_booking_query = apply_company_scope(active_booking_query, Booking.company_id, current_user)
    active_booking = active_booking_query.first()
    if active_booking:
        flash('Cannot delete a car with active bookings.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    db.session.delete(car)
    db.session.commit()
    flash('Car deleted successfully!')
    return redirect(url_for(dashboard_endpoint_for(current_user)))

@app.route('/admin/cancel_booking/<int:booking_id>', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    if not can_manage_operations(current_user):
        if current_user.role == 'manager':
            flash('Manager approval required from boss.')
        else:
            flash('Access denied')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    booking_query = Booking.query.filter_by(id=booking_id)
    booking_query = apply_company_scope(booking_query, Booking.company_id, current_user)
    booking = booking_query.first_or_404()
    booking.status = 'cancelled'
    booking.payment_status = 'cancelled'
    db.session.commit()
    flash('Booking cancelled.')
    return redirect(url_for(dashboard_endpoint_for(current_user)))

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not has_full_control(current_user):
        flash('Access denied')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    user_query = User.query.filter_by(id=user_id)
    user_query = apply_company_scope(user_query, User.company_id, current_user)
    user = user_query.first_or_404()
    if user.id == current_user.id:
        flash('You cannot delete your own account while logged in.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    if user.role in {'super_admin', 'admin'} and not is_super_admin(current_user):
        flash('Only super admin can delete admin role users.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    if user.role == 'boss' and not is_super_admin(current_user):
        flash('Boss accounts are protected.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    if user.role == 'boss' and is_super_admin(current_user):
        company = Company.query.filter_by(owner_id=user.id).first()
        if company:
            other_users = User.query.filter(User.company_id == company.id, User.id != user.id).first()
            has_cars = Car.query.filter_by(company_id=company.id).first()
            has_bookings = Booking.query.filter_by(company_id=company.id).first()
            if other_users or has_cars or has_bookings:
                flash('Cannot delete boss while company still has linked records.')
                return redirect(url_for('super_admin_dashboard'))
            db.session.delete(company)
    has_bookings_query = Booking.query.filter_by(user_id=user_id)
    has_bookings_query = apply_company_scope(has_bookings_query, Booking.company_id, current_user)
    has_bookings = has_bookings_query.first()
    if has_bookings:
        flash('Cannot delete a user with bookings.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    db.session.delete(user)
    db.session.commit()
    flash('User deleted.')
    return redirect(url_for(dashboard_endpoint_for(current_user)))

@app.route('/admin/approve_manager/<int:user_id>', methods=['POST'])
@login_required
def approve_manager(user_id):
    if not is_boss(current_user):
        flash('Only company boss can approve managers.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    manager_query = User.query.filter_by(
        id=user_id,
        role='manager',
        company_id=current_user.company_id,
        boss_id=current_user.id
    )
    manager_user = manager_query.first_or_404()
    if manager_user.role != 'manager':
        flash('Selected user is not a manager.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    manager_user.approval_status = 'approved'
    manager_user.approved_by_id = current_user.id
    if is_boss(current_user):
        manager_user.boss_id = current_user.id
        manager_user.company_id = current_user.company_id
        if current_user.company_name and not manager_user.company_name:
            manager_user.company_name = current_user.company_name
    db.session.commit()
    flash(f'Manager {manager_user.username} approved.')
    return redirect(url_for(dashboard_endpoint_for(current_user)))

@app.route('/admin/reject_manager/<int:user_id>', methods=['POST'])
@login_required
def reject_manager(user_id):
    if not is_boss(current_user):
        flash('Only company boss can reject managers.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    manager_query = User.query.filter_by(
        id=user_id,
        role='manager',
        company_id=current_user.company_id,
        boss_id=current_user.id
    )
    manager_user = manager_query.first_or_404()
    if manager_user.role != 'manager':
        flash('Selected user is not a manager.')
        return redirect(url_for(dashboard_endpoint_for(current_user)))
    manager_user.approval_status = 'rejected'
    manager_user.approved_by_id = current_user.id
    db.session.commit()
    flash(f'Manager {manager_user.username} rejected.')
    return redirect(url_for(dashboard_endpoint_for(current_user)))

@app.route('/superadmin/company/create', methods=['POST'])
@login_required
def create_company():
    if not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    form = CompanyForm()
    if not form.validate_on_submit():
        flash('Invalid company request')
        return redirect(url_for('super_admin_dashboard'))
    company_name = form.name.data.strip()
    exists = Company.query.filter(func.lower(Company.name) == company_name.lower()).first()
    if exists:
        flash('Company already exists.')
        return redirect(url_for('super_admin_dashboard'))
    new_company = Company(name=company_name)
    db.session.add(new_company)
    db.session.commit()
    flash('Company created.')
    return redirect(url_for('super_admin_dashboard'))

@app.route('/superadmin/company/delete/<int:company_id>', methods=['POST'])
@login_required
def delete_company(company_id):
    if not is_super_admin(current_user):
        flash('Access denied')
        return redirect(url_for('home'))
    form = ActionForm()
    if not form.validate_on_submit():
        flash('Invalid request')
        return redirect(url_for('super_admin_dashboard'))
    company = db.session.get(Company, company_id)
    if not company:
        flash('Company not found.')
        return redirect(url_for('super_admin_dashboard'))
    has_users = User.query.filter_by(company_id=company.id).first()
    has_cars = Car.query.filter_by(company_id=company.id).first()
    has_bookings = Booking.query.filter_by(company_id=company.id).first()
    if has_users or has_cars or has_bookings:
        flash('Cannot delete company with linked users/cars/bookings.')
        return redirect(url_for('super_admin_dashboard'))
    db.session.delete(company)
    db.session.commit()
    flash('Company deleted.')
    return redirect(url_for('super_admin_dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_user_columns()
        ensure_user_profile_columns()
        ensure_booking_columns()
        ensure_car_columns()
        ensure_booking_company_column()
        ensure_support_columns()
        backfill_company_links()
        ensure_default_super_admin()
        os.makedirs(app.config['CAR_UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True)
