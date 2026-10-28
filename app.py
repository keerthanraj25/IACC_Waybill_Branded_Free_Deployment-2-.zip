import os, io, json
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
# Production secrets and database URLs are supplied through host environment variables.
_secret = os.environ.get('IACC_SECRET_KEY')
if os.environ.get('RENDER') and not _secret:
    raise RuntimeError('Set IACC_SECRET_KEY in the Render environment before deployment.')
app.secret_key = _secret or 'local-development-only-change-before-deployment'
_database_url = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(BASE, 'iacc_waybills.db'))
if _database_url.startswith('postgres://'):
    _database_url = 'postgresql://' + _database_url[len('postgres://'):]
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('IACC_SECURE_COOKIES', 'true' if os.environ.get('RENDER') else 'false').lower() == 'true'
db = SQLAlchemy(app)

FIELDS = [
 ('date','Date'),('receiver_name','Name of Receiver'),('destination','Destination'),('consignee','Name of Consignee'),
 ('transport_agreement_no','Transport Agreement No.'),('agreement_date','Agreement Date'),('vessel_name','Name of Vessel'),
 ('arrival_date','Date of Arrival'),('bl_no','B/L No.'),('port_of_loading','Port of Loading'),('driver_name',"Driver's Name"),
 ('driver_phone','Driver Contact No.'),('driver_iqama','Driver Iqama No.'),('driver_employee_id','IACC Employee ID'),
 ('truck_no','Truck No.'),('departure_time','Departure Time'),('qty','Quantity'),('nature_of_goods','Nature of Goods'),
 ('trailer_no','Trailer No.'),('container_no','Container No.'),('remarks','Remarks'),('ref_no','REF#'),('iacc_job_no','IACC Job No.'),('operating_office','Operating Office'),
 ('receiving_date','Receiving Date'),('receiving_time','Receiving Time'),('arrival_datetime','Time & Date of Arrival'),
 ('departure_datetime','Time & Date of Departure'),('receiver_signature','Receiver Signature / Name'),('receiver_stamp','Receiver Stamp Notes'),
 ('terminal_arrival_date','Time & Date of Arrival to I.A.C.C. Terminal'),('gate_control_signature','Gate Control Signature')]
FIELD_MAP = dict(FIELDS)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False, default='')
    employee_id = db.Column(db.String(40), nullable=False, default='')
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='operator')

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    customer_code = db.Column(db.String(60), default='')
    phone = db.Column(db.String(60), default='')
    address = db.Column(db.String(240), default='')
    default_destination = db.Column(db.String(160), default='')
    notes = db.Column(db.String(240), default='')

class Driver(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(40), default='')
    door_no = db.Column(db.String(40), default='')
    truck_no = db.Column(db.String(40), default='', index=True)
    employee_id = db.Column(db.String(40), default='')
    iqama_no = db.Column(db.String(40), default='')
    version_no = db.Column(db.String(40), default='')
    note = db.Column(db.String(200), default='')
    source_sheet = db.Column(db.String(40), default='Sheet1')

class Waybill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    waybill_no = db.Column(db.String(80), unique=True, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='Draft')
    data_json = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.String(80), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_by = db.Column(db.String(80), nullable=False)
    issued_by_name = db.Column(db.String(120), nullable=False, default='')
    issued_by_employee_id = db.Column(db.String(40), nullable=False, default='')

class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    waybill_id = db.Column(db.Integer, db.ForeignKey('waybill.id'), nullable=False, index=True)
    action = db.Column(db.String(30), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    details = db.Column(db.Text, nullable=False, default='')


def current_user(): return User.query.filter_by(username=session.get('username')).first() if session.get('username') else None

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return redirect(url_for('login'))
        if current_user().role != 'admin': abort(403)
        return fn(*args, **kwargs)
    return wrapped

def next_number():
    year = datetime.now().year
    prefix = f'IACC-LWB-{year}-'
    numbers = [w.waybill_no for w in Waybill.query.filter(Waybill.waybill_no.like(prefix+'%')).all()]
    highest = 0
    for n in numbers:
        try: highest = max(highest, int(n.rsplit('-', 1)[1]))
        except Exception: pass
    return prefix + f'{highest+1:04d}'

def log_action(w, action, details=''):
    db.session.add(Audit(waybill_id=w.id, action=action, username=session.get('username','system'), details=details))

def init_db():
    with app.app_context():
        db.create_all()
        # Lightweight migration for databases created by earlier starter versions.
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        user_cols = {c['name'] for c in inspector.get_columns('user')} if inspector.has_table('user') else set()
        waybill_cols = {c['name'] for c in inspector.get_columns('waybill')} if inspector.has_table('waybill') else set()
        with db.engine.begin() as conn:
            if 'display_name' not in user_cols: conn.execute(text('ALTER TABLE "user" ADD COLUMN display_name VARCHAR(120) NOT NULL DEFAULT ' + "''"))
            if 'employee_id' not in user_cols: conn.execute(text('ALTER TABLE "user" ADD COLUMN employee_id VARCHAR(40) NOT NULL DEFAULT ' + "''"))
            if 'issued_by_name' not in waybill_cols: conn.execute(text("ALTER TABLE waybill ADD COLUMN issued_by_name VARCHAR(120) NOT NULL DEFAULT ''"))
            if 'issued_by_employee_id' not in waybill_cols: conn.execute(text("ALTER TABLE waybill ADD COLUMN issued_by_employee_id VARCHAR(40) NOT NULL DEFAULT ''"))
        if not User.query.filter_by(username='admin').first():
            initial_admin_password = os.environ.get('IACC_ADMIN_PASSWORD')
            if os.environ.get('RENDER') and not initial_admin_password:
                raise RuntimeError('Set IACC_ADMIN_PASSWORD before first production startup; no default admin password is allowed.')
            if not initial_admin_password:
                initial_admin_password = 'ChangeMe123!'  # local-only development fallback
            db.session.add(User(username='admin', display_name='IACC Administrator', employee_id='ADMIN', password_hash=generate_password_hash(initial_admin_password), role='admin'))
        else:
            admin_user = User.query.filter_by(username='admin').first()
            if not admin_user.display_name: admin_user.display_name = 'IACC Administrator'
            if not admin_user.employee_id: admin_user.employee_id = 'ADMIN'
        # Backfill issuer details on old waybills without changing historical usernames.
        for old_w in Waybill.query.filter((Waybill.issued_by_name == '') | (Waybill.issued_by_name.is_(None))).all():
            issuer = User.query.filter_by(username=old_w.created_by).first()
            old_w.issued_by_name = ((issuer.display_name or issuer.username) if issuer else old_w.created_by)
            old_w.issued_by_employee_id = issuer.employee_id if issuer else ''
        # Populate customer master from existing waybills so updates do not strand old customer names.
        for old_w in Waybill.query.all():
            try: old_data = json.loads(old_w.data_json or '{}')
            except Exception: old_data = {}
            old_customer = (old_data.get('receiver_name') or old_data.get('consignee') or '').strip()
            if old_customer and not Customer.query.filter(db.func.lower(Customer.name) == old_customer.lower()).first():
                db.session.add(Customer(name=old_customer, default_destination=old_data.get('destination','') or ''))
        # Import the uploaded Excel driver/truck master on first startup.
        # Existing records are matched by name + truck number to avoid duplicate imports.
        seed_path = os.path.join(BASE, 'drivers_seed.json')
        if os.path.exists(seed_path):
            with open(seed_path, encoding='utf-8') as f:
                seed_records = json.load(f)
            for item in seed_records:
                existing = Driver.query.filter_by(name=item.get('name',''), truck_no=item.get('truck_no','')).first()
                if not existing:
                    db.session.add(Driver(**item))
        db.session.commit()

def driver_choices():
    return Driver.query.order_by(Driver.name.asc(), Driver.truck_no.asc()).all()

def customer_choices():
    return Customer.query.order_by(Customer.name.asc()).all()

def user_display(user=None):
    user = user or current_user()
    return (user.display_name or user.username) if user else ''

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username','').strip()).first()
        if u and check_password_hash(u.password_hash, request.form.get('password','')):
            session['username'] = u.username
            return redirect(url_for('index'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    q = request.args.get('q','').strip()
    status = request.args.get('status','')
    query = Waybill.query
    if q:
        query = query.filter(db.or_(Waybill.waybill_no.ilike(f'%{q}%'), Waybill.data_json.ilike(f'%{q}%')))
    if status: query = query.filter_by(status=status)
    waybills = query.order_by(Waybill.updated_at.desc()).limit(300).all()
    return render_template('index.html', waybills=waybills, q=q, status=status)

@app.route('/new', methods=['GET','POST'])
@login_required
def new_waybill():
    if request.method == 'POST':
        if not (current_user().display_name or '').strip() or not (current_user().employee_id or '').strip():
            flash('Your full name and IACC employee ID must be completed by an administrator before issuing waybills.', 'error')
            return redirect(url_for('index'))
        data = {key: request.form.get(key,'').strip() for key,_ in FIELDS}
        driver_id = request.form.get('driver_id','').strip()
        driver = Driver.query.get(int(driver_id)) if driver_id.isdigit() else None
        customer_id = request.form.get('customer_id','').strip()
        customer = Customer.query.get(int(customer_id)) if customer_id.isdigit() else None
        if not driver:
            flash('Select a saved driver/truck. Driver details cannot be typed manually.', 'error')
            return render_template('form.html', w=None, data=data, fields=FIELDS, suggested=next_number(), drivers=driver_choices(), customers=customer_choices(), selected_driver_id=driver_id, selected_customer_id=customer_id)
        if not customer:
            flash('Select a customer from the approved customer list.', 'error')
            return render_template('form.html', w=None, data=data, fields=FIELDS, suggested=next_number(), drivers=driver_choices(), customers=customer_choices(), selected_driver_id=driver_id, selected_customer_id=customer_id)
        # Authoritative driver details are taken from the master record, never from editable form values.
        data.update({'driver_id': str(driver.id), 'driver_name': driver.name, 'driver_phone': driver.phone or '', 'driver_iqama': driver.iqama_no or '', 'driver_employee_id': driver.employee_id or '', 'truck_no': driver.truck_no or ''})
        data['customer_id'] = str(customer.id)
        data['receiver_name'] = customer.name
        if not data.get('consignee'): data['consignee'] = customer.name
        if not data.get('destination'): data['destination'] = customer.default_destination or ''
        manual_no = request.form.get('waybill_no','').strip()
        number = manual_no or next_number()
        if manual_no and current_user().role != 'admin':
            flash('Only an administrator can enter a manual waybill number.', 'error')
            return render_template('form.html', w=None, data=data, fields=FIELDS, suggested=next_number(), drivers=driver_choices(), customers=customer_choices(), selected_driver_id=str(driver.id), selected_customer_id=str(customer.id))
        if Waybill.query.filter_by(waybill_no=number).first():
            flash('That waybill number already exists. Choose a unique number.', 'error')
            return render_template('form.html', w=None, data=data, fields=FIELDS, suggested=next_number(), drivers=driver_choices(), customers=customer_choices(), selected_driver_id=str(driver.id), selected_customer_id=str(customer.id))
        issuer_name = user_display()
        issuer_emp = current_user().employee_id or ''
        w = Waybill(waybill_no=number, status=request.form.get('status','Draft'), data_json=json.dumps(data, ensure_ascii=False), created_by=session['username'], updated_by=session['username'], issued_by_name=issuer_name, issued_by_employee_id=issuer_emp)
        db.session.add(w); db.session.flush(); log_action(w, 'CREATED', f'Created waybill {number}; issuer {issuer_name} / {issuer_emp}')
        db.session.commit(); flash(f'Waybill {number} saved.', 'success'); return redirect(url_for('view_waybill', wid=w.id))
    return render_template('form.html', w=None, data={}, fields=FIELDS, suggested=next_number(), drivers=driver_choices(), customers=customer_choices(), selected_driver_id='', selected_customer_id='')

@app.route('/drivers', methods=['GET','POST'])
@login_required
@admin_required
def drivers():
    if request.method == 'POST':
        name=request.form.get('name','').strip(); truck_no=request.form.get('truck_no','').strip()
        if not name or not truck_no:
            flash('Driver name and truck registration are required.','error')
        elif Driver.query.filter_by(name=name, truck_no=truck_no).first():
            flash('That driver/truck assignment already exists.','error')
        else:
            db.session.add(Driver(name=name, phone=request.form.get('phone','').strip(), door_no=request.form.get('door_no','').strip(), truck_no=truck_no, employee_id=request.form.get('employee_id','').strip(), iqama_no=request.form.get('iqama_no','').strip(), version_no=request.form.get('version_no','').strip(), note=request.form.get('note','').strip(), source_sheet='Admin entry'))
            db.session.commit(); flash('Driver/truck record added.','success')
        return redirect(url_for('drivers'))
    q = request.args.get('q','').strip()
    query = Driver.query
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(Driver.name.ilike(like), Driver.phone.ilike(like),
                                    Driver.door_no.ilike(like), Driver.truck_no.ilike(like),
                                    Driver.employee_id.ilike(like)))
    records = query.order_by(Driver.name.asc(), Driver.truck_no.asc()).all()
    return render_template('drivers.html', drivers=records, q=q, is_admin=(current_user().role == 'admin'))

@app.route('/drivers/<int:did>/update', methods=['POST'])
@login_required
@admin_required
def update_driver(did):
    d=Driver.query.get_or_404(did)
    name=request.form.get('name','').strip(); truck_no=request.form.get('truck_no','').strip()
    duplicate=Driver.query.filter_by(name=name,truck_no=truck_no).first() if name and truck_no else None
    if not name or not truck_no:
        flash('Driver name and truck registration are required.','error')
    elif duplicate and duplicate.id != d.id:
        flash('That driver/truck assignment already exists.','error')
    else:
        d.name=name; d.phone=request.form.get('phone','').strip(); d.door_no=request.form.get('door_no','').strip(); d.truck_no=truck_no; d.employee_id=request.form.get('employee_id','').strip(); d.iqama_no=request.form.get('iqama_no','').strip(); d.version_no=request.form.get('version_no','').strip(); d.note=request.form.get('note','').strip(); d.source_sheet='Admin edited'
        db.session.commit(); flash('Driver/truck record updated.','success')
    return redirect(url_for('drivers'))

@app.route('/waybill/<int:wid>')
@login_required
def view_waybill(wid):
    w = Waybill.query.get_or_404(wid)
    return render_template('view.html', w=w, data=json.loads(w.data_json), history=Audit.query.filter_by(waybill_id=w.id).order_by(Audit.changed_at.desc()).all())

@app.route('/waybill/<int:wid>/edit', methods=['GET','POST'])
@login_required
def edit_waybill(wid):
    w = Waybill.query.get_or_404(wid); data = json.loads(w.data_json)
    if request.method == 'POST':
        new_no = request.form.get('waybill_no', w.waybill_no).strip()
        if new_no != w.waybill_no and current_user().role != 'admin':
            flash('Only an administrator can change an issued waybill number.', 'error')
            return render_template('form.html', w=w, data=data, fields=FIELDS, suggested=w.waybill_no, drivers=driver_choices(), customers=customer_choices(), selected_driver_id=data.get('driver_id',''), selected_customer_id=data.get('customer_id',''))
        driver_id = request.form.get('driver_id','').strip()
        driver = Driver.query.get(int(driver_id)) if driver_id.isdigit() else None
        customer_id = request.form.get('customer_id','').strip()
        customer = Customer.query.get(int(customer_id)) if customer_id.isdigit() else None
        if not driver or not customer:
            flash('Select an approved driver/truck and customer.', 'error')
            return render_template('form.html', w=w, data=data, fields=FIELDS, suggested=w.waybill_no, drivers=driver_choices(), customers=customer_choices(), selected_driver_id=driver_id, selected_customer_id=customer_id)
        duplicate = Waybill.query.filter_by(waybill_no=new_no).first()
        if duplicate and duplicate.id != w.id:
            flash('That waybill number already exists.', 'error')
            return render_template('form.html', w=w, data=data, fields=FIELDS, suggested=w.waybill_no, drivers=driver_choices(), customers=customer_choices(), selected_driver_id=driver_id, selected_customer_id=customer_id)
        updated = {key: request.form.get(key,'').strip() for key,_ in FIELDS}
        updated.update({'driver_id': str(driver.id), 'driver_name': driver.name, 'driver_phone': driver.phone or '', 'driver_iqama': driver.iqama_no or '', 'driver_employee_id': driver.employee_id or '', 'truck_no': driver.truck_no or '', 'customer_id': str(customer.id), 'receiver_name': customer.name})
        if not updated.get('consignee'): updated['consignee'] = customer.name
        if not updated.get('destination'): updated['destination'] = customer.default_destination or ''
        changed = [FIELD_MAP[k] for k in updated if k in FIELD_MAP and updated[k] != data.get(k,'')]
        old_no = w.waybill_no
        if new_no != old_no: changed.append(f'Waybill number: {old_no} → {new_no}')
        w.waybill_no = new_no; w.data_json = json.dumps(updated, ensure_ascii=False)
        w.status = request.form.get('status',w.status); w.updated_at = datetime.utcnow(); w.updated_by = session['username']
        log_action(w, 'UPDATED', 'Changed: ' + (', '.join(changed) if changed else 'Saved without field changes'))
        db.session.commit(); flash('Waybill updated and change recorded. Issued-by identity remains locked to the original creator.', 'success'); return redirect(url_for('view_waybill', wid=w.id))
    return render_template('form.html', w=w, data=data, fields=FIELDS, suggested=w.waybill_no, drivers=driver_choices(), customers=customer_choices(), selected_driver_id=data.get('driver_id',''), selected_customer_id=data.get('customer_id',''))

@app.route('/waybill/<int:wid>/cancel', methods=['POST'])
@login_required
def cancel_waybill(wid):
    w = Waybill.query.get_or_404(wid); w.status='Cancelled'; w.updated_at=datetime.utcnow(); w.updated_by=session['username']; log_action(w,'CANCELLED','Marked cancelled; record retained for audit.'); db.session.commit(); flash('Waybill cancelled; history retained.','success'); return redirect(url_for('view_waybill',wid=w.id))

@app.route('/waybill/<int:wid>/pdf')
@login_required
def pdf_waybill(wid):
    """Generate a clean, original IACC-style waybill layout.

    The uploaded scan is used only as a source for field names and workflow;
    it is not placed behind the generated PDF.
    """
    w = Waybill.query.get_or_404(wid)
    data = json.loads(w.data_json)
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth

    width, height = A4
    result = io.BytesIO()
    c = canvas.Canvas(result, pagesize=A4)
    navy = colors.HexColor('#18324B')
    blue = colors.HexColor('#2D6688')
    pale = colors.HexColor('#EEF4F8')
    line = colors.HexColor('#B8C6D1')
    ink = colors.HexColor('#17212B')
    muted = colors.HexColor('#536575')
    left = 34
    right = width - 34
    usable = right - left

    def fit_text(value, font, size, max_width):
        value = str(value or '').strip()
        while value and stringWidth(value, font, size) > max_width and size > 5.5:
            size -= 0.25
        return value, size

    def section(y, title):
        c.setFillColor(navy)
        c.roundRect(left, y-18, usable, 20, 4, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(left+9, y-12, title.upper())
        return y-28

    def field(x, y, w, label, value, height=31):
        c.setStrokeColor(line)
        c.setLineWidth(0.65)
        c.roundRect(x, y-height, w, height, 3, fill=0, stroke=1)
        c.setFillColor(muted)
        c.setFont('Helvetica', 6.8)
        c.drawString(x+6, y-10, label.upper())
        val, fs = fit_text(value, 'Helvetica-Bold', 8.4, w-12)
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', fs)
        c.drawString(x+6, y-23, val)

    # Fresh company-branded header. The uploaded scan is not used as a background.
    from reportlab.lib.utils import ImageReader
    c.setFillColor(colors.white)
    c.rect(left, height-91, usable, 63, fill=1, stroke=0)
    logo_file = os.path.join(BASE, 'static', 'iacc_logo.png')
    if os.path.exists(logo_file):
        c.drawImage(logo_file, left+2, height-78, width=66, height=42,
                    preserveAspectRatio=True, mask='auto', anchor='c')
    c.setFillColor(navy)
    c.setFont('Helvetica-Bold', 11.5)
    c.drawString(left+75, height-43, 'INTERNATIONAL ASSOCIATED CARGO CARRIER')
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left+75, height-57, 'I.A.C.C.  |  LANDWAY BILL')
    c.setFillColor(muted)
    c.setFont('Helvetica', 6.2)
    c.drawString(left+75, height-69, 'King Abdul Aziz Seaport, P.O. Box 4636, Dammam 31412, Saudi Arabia')
    c.drawString(left+75, height-79, 'Tel: +966 13 8140460 / 61 / 62  |  www.iaccsaudi.com')
    rx = right-166
    c.setFillColor(pale)
    c.roundRect(rx, height-81, 164, 48, 4, fill=1, stroke=0)
    c.setFillColor(navy)
    c.setFont('Helvetica-Bold', 6.8)
    c.drawString(rx+7, height-45, 'CR NO. 2051001397')
    c.drawString(rx+7, height-57, 'VAT NO. 300488852800003')
    c.setFont('Helvetica', 6.3)
    c.drawString(rx+7, height-69, 'WAYBILL NO.')
    no, fs = fit_text(w.waybill_no, 'Helvetica-Bold', 8.5, 145)
    c.setFont('Helvetica-Bold', fs)
    c.drawString(rx+7, height-78, no)
    c.setStrokeColor(blue); c.setLineWidth(1.2); c.line(left, height-91, right, height-91)

    y = height-105
    c.setFillColor(muted); c.setFont('Helvetica', 6.6)
    issuer_emp_id = w.issued_by_employee_id or '—'
    issuer_name = w.issued_by_name or w.created_by
    issuer_line = f'Status: {w.status}  |  Created: {w.created_at.strftime("%d-%b-%Y")}  |  Issued by: {issuer_name}  |  Employee ID: {issuer_emp_id}'
    issuer_size = 6.6
    while stringWidth(issuer_line, 'Helvetica', issuer_size) > usable and issuer_size > 5.5: issuer_size -= 0.2
    c.setFont('Helvetica', issuer_size); c.drawString(left, y, issuer_line)
    y -= 12

    # Shipment and customer details.
    y = section(y, 'Shipment & customer details')
    gap = 8
    colw = (usable-gap)/2
    pairs = [
        ('Date', 'date', 'Name of Receiver', 'receiver_name'),
        ('Destination', 'destination', 'Name of Consignee', 'consignee'),
        ('Transport Agreement No.', 'transport_agreement_no', 'Agreement Date', 'agreement_date'),
        ('Name of Vessel', 'vessel_name', 'Date of Arrival', 'arrival_date'),
        ('B/L No.', 'bl_no', 'Port of Loading', 'port_of_loading'),
    ]
    for l1,k1,l2,k2 in pairs:
        field(left, y, colw, l1, data.get(k1,''))
        field(left+colw+gap, y, colw, l2, data.get(k2,''))
        y -= 36

    y = section(y-1, 'Transport & cargo details')
    field(left, y, colw, "Driver's Name", data.get('driver_name',''))
    field(left+colw+gap, y, colw, 'Truck No.', data.get('truck_no',''))
    y -= 36
    # Driver identity/contact fields are aligned as a dedicated three-column row.
    three_gap = 7
    three_w = (usable - 2*three_gap) / 3
    field(left, y, three_w, 'Driver Contact No.', data.get('driver_phone',''), height=31)
    field(left+three_w+three_gap, y, three_w, 'Driver Iqama No.', data.get('driver_iqama',''), height=31)
    field(left+2*(three_w+three_gap), y, three_w, 'IACC Employee ID', data.get('driver_employee_id',''), height=31)
    y -= 36
    field(left, y, colw, 'Departure Time', data.get('departure_time',''))
    field(left+colw+gap, y, colw, 'Operating Office', data.get('operating_office',''))
    y -= 38

    # Five-line goods details table. Lines 4 and 5 are reserved for REF# and IACC JOB NO.
    headers = [('QTY', 42), ('NATURE OF GOODS', 170), ('TRAILER NO.', 92), ('CONTAINER NO.', 100), ('REMARKS', usable-42-170-92-100)]
    table_h = 22
    row_h = 24
    x = left
    c.setFillColor(pale); c.setStrokeColor(line)
    c.rect(left, y-table_h, usable, table_h, fill=1, stroke=1)
    for label, cw in headers:
        c.setFillColor(navy); c.setFont('Helvetica-Bold', 6.8)
        c.drawCentredString(x+cw/2, y-14, label)
        x += cw
    y -= table_h
    # Lines 1–3 remain available for cargo details / additional goods.
    for row_index in range(3):
        x = left
        vals = ([data.get('qty',''), data.get('nature_of_goods',''), data.get('trailer_no',''), data.get('container_no',''), data.get('remarks','')] if row_index == 0 else ['', '', '', '', ''])
        for (label,cw),val in zip(headers, vals):
            c.setFillColor(colors.white); c.setStrokeColor(line)
            c.rect(x, y-row_h, cw, row_h, fill=1, stroke=1)
            c.setFillColor(ink)
            if label == 'NATURE OF GOODS':
                c.setFont('Helvetica', 6.5)
                words = str(val or '').split()
                lines = []; current = ''
                maxw = cw - 8
                for word in words:
                    trial = (current + ' ' + word).strip()
                    if stringWidth(trial, 'Helvetica', 6.5) <= maxw:
                        current = trial
                    else:
                        if current: lines.append(current)
                        current = word
                if current: lines.append(current)
                for li, line_text in enumerate(lines[:3]):
                    c.drawString(x+4, y-7-(li*7), line_text)
            else:
                text, fs = fit_text(val, 'Helvetica', 7.2, cw-8)
                c.setFont('Helvetica', fs)
                if label == 'QTY': c.drawCentredString(x+cw/2, y-12, text)
                else: c.drawString(x+4, y-12, text)
            x += cw
        y -= row_h
    # Line 4: REF#; line 5: IACC JOB NO. Each is a dedicated full-width row.
    for label, key in [('REF#', 'ref_no'), ('IACC JOB NO.', 'iacc_job_no')]:
        c.setFillColor(colors.white); c.setStrokeColor(line)
        c.rect(left, y-row_h, usable, row_h, fill=1, stroke=1)
        c.setFillColor(navy); c.setFont('Helvetica-Bold', 7.0)
        c.drawString(left+6, y-12, label)
        value, fs = fit_text(data.get(key, ''), 'Helvetica', 7.2, usable-95)
        c.setFillColor(ink); c.setFont('Helvetica', fs)
        c.drawString(left+82, y-12, value)
        y -= row_h
    y -= 7

    y = section(y, 'Receiving & delivery record')
    field(left, y, colw, 'Receiving Date', data.get('receiving_date',''))
    field(left+colw+gap, y, colw, 'Receiving Time', data.get('receiving_time',''))
    y -= 36
    field(left, y, colw, 'Time & Date of Arrival', data.get('arrival_datetime',''))
    field(left+colw+gap, y, colw, 'Time & Date of Departure', data.get('departure_datetime',''))
    y -= 38

    # Signature areas with enough space for handwriting/stamp.
    sign_h = 48
    signw = (usable-2*gap)/3
    for i,(label,key) in enumerate([
        ('Receiver Signature / Name','receiver_signature'),
        ('Receiver Stamp / Notes','receiver_stamp'),
        ('Gate Control Signature','gate_control_signature')
    ]):
        x = left + i*(signw+gap)
        c.setStrokeColor(line); c.setFillColor(colors.white)
        c.roundRect(x, y-sign_h, signw, sign_h, 3, fill=1, stroke=1)
        c.setFillColor(muted); c.setFont('Helvetica', 6.5)
        c.drawString(x+6, y-10, label.upper())
        val, fs = fit_text(data.get(key,''), 'Helvetica', 7, signw-12)
        c.setFillColor(ink); c.setFont('Helvetica', fs)
        c.drawString(x+6, y-23, val)
    y -= sign_h+9
    field(left, y, usable, 'Time & Date of Arrival to I.A.C.C. Terminal', data.get('terminal_arrival_date',''), height=30)

    # Footer / document control.
    c.setStrokeColor(line); c.line(left, 30, right, 30)
    c.setFillColor(muted); c.setFont('Helvetica', 6.5)
    c.drawString(left, 19, 'IACC WAYBILL MANAGEMENT  •  Generated document — verify shipment details before signing.')
    c.drawRightString(right, 19, f'Page 1 of 1  |  {w.waybill_no}')
    c.save(); result.seek(0)
    log_action(w, 'PDF_GENERATED', 'PDF generated using the new original layout (uploaded scan used for field reference only).')
    db.session.commit()
    return send_file(result, as_attachment=False, download_name=f'{w.waybill_no}.pdf', mimetype='application/pdf')

@app.route('/admin/users', methods=['GET','POST'])
@admin_required
def users():
    if request.method=='POST':
        username=request.form.get('username','').strip(); display_name=request.form.get('display_name','').strip(); employee_id=request.form.get('employee_id','').strip(); password=request.form.get('password',''); role=request.form.get('role','operator')
        if not username or not display_name or not employee_id or len(password)<8 or role not in ('admin','operator'):
            flash('Enter name, employee ID, username, a password of at least 8 characters, and a valid role.','error')
        elif User.query.filter_by(username=username).first(): flash('Username already exists.','error')
        else:
            db.session.add(User(username=username, display_name=display_name, employee_id=employee_id, password_hash=generate_password_hash(password),role=role)); db.session.commit(); flash('User created.','success')
    return render_template('users.html', users=User.query.order_by(User.display_name, User.username).all())

@app.route('/admin/users/<int:uid>/update', methods=['POST'])
@admin_required
def update_user(uid):
    u=User.query.get_or_404(uid)
    name=request.form.get('display_name','').strip(); emp=request.form.get('employee_id','').strip(); role=request.form.get('role','operator')
    if not name or not emp or role not in ('admin','operator'):
        flash('Name, employee ID and valid role are required.','error')
    elif u.id == current_user().id and role != 'admin':
        flash('You cannot remove your own administrator role.','error')
    else:
        u.display_name=name; u.employee_id=emp; u.role=role; db.session.commit(); flash('User details updated.','success')
    return redirect(url_for('users'))

@app.route('/admin/users/<int:uid>/password', methods=['POST'])
@admin_required
def reset_password(uid):
    u=User.query.get_or_404(uid); pw=request.form.get('password','')
    if len(pw)<8: flash('Password must be at least 8 characters.','error')
    else: u.password_hash=generate_password_hash(pw); db.session.commit(); flash(f'Password updated for {u.username}.','success')
    return redirect(url_for('users'))

@app.route('/admin/customers', methods=['GET','POST'])
@admin_required
def customers():
    if request.method == 'POST':
        name=request.form.get('name','').strip()
        if not name:
            flash('Customer name is required.','error')
        else:
            db.session.add(Customer(name=name, customer_code=request.form.get('customer_code','').strip(), phone=request.form.get('phone','').strip(), address=request.form.get('address','').strip(), default_destination=request.form.get('default_destination','').strip(), notes=request.form.get('notes','').strip()))
            db.session.commit(); flash('Customer saved.','success')
        return redirect(url_for('customers'))
    q=request.args.get('q','').strip(); query=Customer.query
    if q:
        like=f'%{q}%'; query=query.filter(db.or_(Customer.name.ilike(like),Customer.customer_code.ilike(like),Customer.phone.ilike(like)))
    return render_template('customers.html', customers=query.order_by(Customer.name).all(), q=q)

@app.route('/admin/customers/<int:cid>/update', methods=['POST'])
@admin_required
def update_customer(cid):
    c=Customer.query.get_or_404(cid); name=request.form.get('name','').strip()
    if not name: flash('Customer name is required.','error')
    else:
        c.name=name; c.customer_code=request.form.get('customer_code','').strip(); c.phone=request.form.get('phone','').strip(); c.address=request.form.get('address','').strip(); c.default_destination=request.form.get('default_destination','').strip(); c.notes=request.form.get('notes','').strip(); db.session.commit(); flash('Customer updated.','success')
    return redirect(url_for('customers'))

@app.context_processor
def inject_user():
    return {'current_user': current_user(), 'user_display': user_display}

@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return {}

# Initialize the database after all routes, filters, and context processors are registered.
if os.environ.get('IACC_SKIP_INIT_DB') != '1':
    init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
