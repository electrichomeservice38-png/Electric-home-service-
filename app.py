import os, sqlite3, secrets, math, shutil, csv, io, hmac, hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
try:
    import requests
except ImportError:
    requests=None

APP=Flask(__name__)
APP.secret_key=os.environ.get('SECRET_KEY','change-this-secret-before-production')
APP.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=10*1024*1024, RAZORPAY_KEY_ID=os.environ.get('RAZORPAY_KEY_ID',''))
DB=os.environ.get('DATABASE_PATH','electric_home_services.db')
UPLOAD_DIR=os.environ.get('UPLOAD_DIR','uploads'); BACKUP_DIR=os.environ.get('BACKUP_DIR','backups')
os.makedirs(UPLOAD_DIR,exist_ok=True); os.makedirs(BACKUP_DIR,exist_ok=True)

DISTANCE_RATES=[(0,3,0),(3,5,30),(5,10,60),(10,15,100),(15,20,150),(20,30,220),(30,40,300),(40,50,400)]
SERVICES=[('Electrical Wiring',500,50,20),('Fan Repair',300,50,20),('Fan Installation',400,50,20),('Light Installation',250,50,20),('Switch Repair',200,50,20),('Socket Repair',200,50,20),('MCB Installation',350,50,20),('DB Installation',600,50,20),('Inverter Installation',700,50,20),('Inverter Repair',500,50,20),('AC Electrical Work',600,50,20),('Appliance Electrical Repair',400,50,20),('New Electrical Connection',1000,100,20),('Emergency Electrical Service',800,100,20),('Other Electrical Services',500,50,20)]
STATUSES=['REQUESTED','ACCEPTED','TECHNICIAN ASSIGNED','ON THE WAY','ARRIVED','SERVICE STARTED','SERVICE COMPLETED','CANCELLED']
ALLOWED_DOC={'pdf','jpg','jpeg','png'}

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,phone TEXT UNIQUE NOT NULL,email TEXT,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('OWNER','TECHNICIAN','CUSTOMER')),active INTEGER DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS technician_profiles(user_id INTEGER PRIMARY KEY,photo TEXT,address TEXT,district TEXT,state TEXT,pincode TEXT,skills TEXT,experience TEXT,service_area TEXT,verification_status TEXT DEFAULT 'PENDING',rejection_reason TEXT,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS technician_documents(id INTEGER PRIMARY KEY AUTOINCREMENT,technician_id INTEGER NOT NULL,doc_type TEXT NOT NULL,file_path TEXT NOT NULL,uploaded_at TEXT NOT NULL,FOREIGN KEY(technician_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,description TEXT,price REAL NOT NULL,service_charge REAL DEFAULT 0,duration INTEGER DEFAULT 60,commission REAL DEFAULT 20,technician_payout REAL DEFAULT 80,active INTEGER DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS addresses(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,label TEXT,address TEXT,landmark TEXT,city TEXT,district TEXT,state TEXT,pincode TEXT,lat REAL,lng REAL,is_default INTEGER DEFAULT 0,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,service_id INTEGER NOT NULL,technician_id INTEGER,address_id INTEGER,address TEXT,landmark TEXT,city TEXT,district TEXT,state TEXT,pincode TEXT,lat REAL,lng REAL,distance REAL DEFAULT 0,distance_charge REAL DEFAULT 0,scheduled_date TEXT,scheduled_time TEXT,status TEXT DEFAULT 'REQUESTED',completion_otp_hash TEXT,otp_expires TEXT,otp_verified INTEGER DEFAULT 0,created_at TEXT NOT NULL,completed_at TEXT,FOREIGN KEY(user_id) REFERENCES users(id),FOREIGN KEY(service_id) REFERENCES services(id),FOREIGN KEY(technician_id) REFERENCES users(id),FOREIGN KEY(address_id) REFERENCES addresses(id));
CREATE TABLE IF NOT EXISTS booking_items(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER NOT NULL,item_name TEXT,qty INTEGER,unit_price REAL,FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER NOT NULL,amount REAL,provider TEXT,transaction_id TEXT,status TEXT DEFAULT 'PENDING',created_at TEXT NOT NULL,verified_at TEXT,FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS commissions(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER NOT NULL,service_amount REAL,owner_amount REAL,technician_amount REAL,percentage REAL,created_at TEXT NOT NULL,FOREIGN KEY(booking_id) REFERENCES bookings(id));
CREATE TABLE IF NOT EXISTS earnings(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER,owner_amount REAL,technician_amount REAL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,sku TEXT UNIQUE NOT NULL,price REAL,stock INTEGER DEFAULT 0,gst REAL DEFAULT 0,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,total REAL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY AUTOINCREMENT,sale_id INTEGER,product_id INTEGER,qty INTEGER,price REAL,FOREIGN KEY(sale_id) REFERENCES sales(id) ON DELETE CASCADE,FOREIGN KEY(product_id) REFERENCES products(id));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER UNIQUE,rating INTEGER,comment TEXT,created_at TEXT NOT NULL,FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,title TEXT,message TEXT,read_at TEXT,created_at TEXT NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS coupons(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE,discount_type TEXT,discount_value REAL,active INTEGER DEFAULT 1,expires_at TEXT);
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,description TEXT,active INTEGER DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS otp_records(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,booking_id INTEGER,otp_hash TEXT,expires_at TEXT,kind TEXT,verified INTEGER DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER,action TEXT,entity TEXT,entity_id INTEGER,old_value TEXT,new_value TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id); CREATE INDEX IF NOT EXISTS idx_bookings_tech ON bookings(technician_id); CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status); CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
'''

def db():
    c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL'); return c

def now(): return datetime.utcnow().isoformat(timespec='seconds')
def init():
    c=db(); c.executescript(SCHEMA)
    if c.execute('SELECT COUNT(*) FROM services').fetchone()[0]==0:
        c.executemany('INSERT INTO services(name,price,service_charge,commission,technician_payout,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',[(n,p,sc,cp,100-cp,now(),now()) for n,p,sc,cp in SERVICES])
    if c.execute('SELECT COUNT(*) FROM users WHERE role="OWNER"').fetchone()[0]==0:
        c.execute('INSERT INTO users(name,phone,password,role,created_at) VALUES(?,?,?,?,?)',('Owner','owner',generate_password_hash(os.environ.get('OWNER_PASSWORD','change-me')),'OWNER',now()))
    defaults={'app_name':'Electric Home Services','logo_url':'','owner_commission':'20','tax_percent':'0','currency':'INR'}
    for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)',(k,v))
    c.commit(); c.close()
init()

def current_user():
    uid=session.get('uid');
    if not uid:return None
    c=db(); u=c.execute('SELECT * FROM users WHERE id=? AND active=1',(uid,)).fetchone(); c.close(); return u

def roles(*allowed):
    def deco(f):
        @wraps(f)
        def wrapped(*a,**kw):
            u=current_user()
            if not u: return redirect(url_for('login',next=request.path))
            if u['role'] not in allowed: abort(403)
            return f(*a,**kw)
        return wrapped
    return deco

def csrf_token():
    if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
    return session['csrf']
@APP.context_processor
def ctx():
    c=db(); settings={r['k']:r['v'] for r in c.execute('SELECT k,v FROM settings').fetchall()}; c.close()
    return {'me':current_user(),'settings':settings,'distance_rates':DISTANCE_RATES,'statuses':STATUSES,'csrf':csrf_token()}
@APP.before_request
def protect():
    if request.method=='POST':
        token=request.form.get('_csrf') or request.headers.get('X-CSRF-Token')
        if not token or not secrets.compare_digest(token,session.get('csrf','')): abort(400,'Invalid CSRF token')

def audit(actor,action,entity,eid,old='',new=''):
    c=db(); c.execute('INSERT INTO audit_logs(actor_id,action,entity,entity_id,old_value,new_value,created_at) VALUES(?,?,?,?,?,?,?)',(actor,action,entity,eid,old,new,now())); c.commit(); c.close()
def notify(uid,title,msg):
    if not uid:return
    c=db(); c.execute('INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)',(uid,title,msg,now())); c.commit(); c.close()
def distance_charge(km):
    km=max(0,float(km))
    for lo,hi,fee in DISTANCE_RATES:
        if km<=hi:return fee
    return 400+math.ceil(km-50)*10
def totals(b):
    return float(b['price'])+float(b['service_charge'])+float(b['distance_charge'])
def backup_db():
    ts=datetime.utcnow().strftime('%Y%m%d_%H%M%S'); path=os.path.join(BACKUP_DIR,f'electric_home_services_{ts}.db')
    src=db(); dest=sqlite3.connect(path); src.backup(dest); dest.close(); src.close(); return path

def send_otp(phone,otp):
    return bool(os.environ.get('SMS_URL'))

def razorpay_enabled():
    return bool(requests and os.environ.get('RAZORPAY_KEY_ID') and os.environ.get('RAZORPAY_KEY_SECRET'))

def razorpay_create_order(amount, receipt):
    if not razorpay_enabled(): return None
    r=requests.post('https://api.razorpay.com/v1/orders',auth=(os.environ['RAZORPAY_KEY_ID'],os.environ['RAZORPAY_KEY_SECRET']),json={'amount':int(round(amount*100)),'currency':'INR','receipt':receipt,'payment_capture':1},timeout=15)
    r.raise_for_status(); return r.json()

def verify_razorpay_signature(order_id,payment_id,signature):
    msg=f'{order_id}|{payment_id}'.encode(); expected=hmac.new(os.environ['RAZORPAY_KEY_SECRET'].encode(),msg,hashlib.sha256).hexdigest(); return hmac.compare_digest(expected,signature)

@APP.route('/')
def home():
    c=db(); services=c.execute('SELECT * FROM services WHERE active=1 ORDER BY id').fetchall(); offers=c.execute('SELECT * FROM offers WHERE active=1 ORDER BY id DESC').fetchall(); c.close(); return render_template('home.html',services=services,offers=offers)
@APP.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        name=request.form['name'].strip(); phone=request.form['phone'].strip(); email=request.form.get('email','').strip(); pw=request.form['password']
        if len(pw)<8: flash('Password must be at least 8 characters.'); return render_template('register.html')
        try:
            c=db(); cur=c.execute('INSERT INTO users(name,phone,email,password,role,created_at) VALUES(?,?,?,?,?,?)',(name,phone,email,generate_password_hash(pw),'CUSTOMER',now())); uid=cur.lastrowid; c.commit(); c.close(); session['uid']=uid; return redirect(url_for('dashboard'))
        except sqlite3.IntegrityError: flash('Phone number already registered.')
    return render_template('register.html')
@APP.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        c=db(); u=c.execute('SELECT * FROM users WHERE phone=?',(request.form['phone'].strip(),)).fetchone(); c.close()
        if u and check_password_hash(u['password'],request.form['password']): session.clear(); session['uid']=u['id']; return redirect(url_for('dashboard'))
        flash('Invalid login.')
    return render_template('login.html')
@APP.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))
@APP.route('/dashboard')
@roles('CUSTOMER','TECHNICIAN','OWNER')
def dashboard():
    u=current_user(); c=db()
    if u['role']=='OWNER': q='SELECT b.*,s.name service,s.price,s.service_charge,u.name customer FROM bookings b JOIN services s ON s.id=b.service_id JOIN users u ON u.id=b.user_id ORDER BY b.id DESC'; args=()
    elif u['role']=='TECHNICIAN': q='SELECT b.*,s.name service,s.price,s.service_charge,u.name customer FROM bookings b JOIN services s ON s.id=b.service_id JOIN users u ON u.id=b.user_id WHERE b.technician_id=? ORDER BY b.id DESC'; args=(u['id'],)
    else: q='SELECT b.*,s.name service,s.price,s.service_charge FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.user_id=? ORDER BY b.id DESC'; args=(u['id'],)
    bookings=c.execute(q,args).fetchall(); notifications=c.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 10',(u['id'],)).fetchall(); c.close(); return render_template('dashboard.html',bookings=bookings,notifications=notifications)
@APP.route('/book',methods=['GET','POST'])
@roles('CUSTOMER')
def book():
    c=db(); services=c.execute('SELECT * FROM services WHERE active=1').fetchall(); addresses=c.execute('SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC,id DESC',(current_user()['id'],)).fetchall()
    if request.method=='POST':
        sid=int(request.form['service_id']); s=c.execute('SELECT * FROM services WHERE id=? AND active=1',(sid,)).fetchone();
        if not s: abort(404)
        km=float(request.form.get('distance_km') or 0); dc=distance_charge(km); otp=str(secrets.randbelow(900000)+100000); exp=datetime.utcnow()+timedelta(minutes=15)
        cur=c.execute('''INSERT INTO bookings(user_id,service_id,address,landmark,city,district,state,pincode,lat,lng,distance,distance_charge,scheduled_date,scheduled_time,status,completion_otp_hash,otp_expires,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(current_user()['id'],sid,request.form['address'],request.form.get('landmark',''),request.form.get('city',''),request.form['district'],request.form['state'],request.form['pincode'],request.form.get('lat') or None,request.form.get('lng') or None,km,dc,request.form.get('date',''),request.form.get('time',''),'REQUESTED',generate_password_hash(otp),exp.isoformat(),now())); bid=cur.lastrowid
        c.execute('INSERT INTO otp_records(user_id,booking_id,otp_hash,expires_at,kind,created_at) VALUES(?,?,?,?,?,?)',(current_user()['id'],bid,generate_password_hash(otp),exp.isoformat(),'SERVICE_COMPLETION',now())); c.commit(); c.close()
        # Never show a production OTP in the UI; real SMS provider should deliver it.
        flash('Booking created. Distance charge: ₹%.2f. Configure SMS provider for real OTP delivery.'%dc)
        return redirect(url_for('invoice',bid=bid))
    c.close(); return render_template('book.html',services=services,addresses=addresses)
@APP.route('/api/distance')
def api_distance():
    try:
        km=float(request.args.get('km','0')); return jsonify(km=km,charge=distance_charge(km))
    except: return jsonify(error='Invalid distance'),400
@APP.route('/rates')
def rates(): return render_template('rates.html')
@APP.route('/invoice/<int:bid>')
@roles('CUSTOMER','TECHNICIAN','OWNER')
def invoice(bid):
    c=db(); b=c.execute('SELECT b.*,s.name service,s.price,s.service_charge,s.commission,s.technician_payout,u.name customer,u.phone FROM bookings b JOIN services s ON s.id=b.service_id JOIN users u ON u.id=b.user_id WHERE b.id=?',(bid,)).fetchone(); c.close()
    if not b: abort(404)
    u=current_user()
    if u['role']=='CUSTOMER' and b['user_id']!=u['id']: abort(403)
    if u['role']=='TECHNICIAN' and b['technician_id']!=u['id']: abort(403)
    return render_template('invoice.html',b=b,total=totals(b),tax=0)
@APP.route('/payment/<int:bid>',methods=['GET','POST'])
@roles('CUSTOMER')
def payment(bid):
    c=db(); b=c.execute('SELECT b.*,s.price,s.service_charge FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.id=? AND b.user_id=?',(bid,current_user()['id'],)).fetchone(); c.close()
    if not b: abort(404)
    amount=totals(b)
    if request.method=='POST':
        provider=request.form['provider']; tx=request.form.get('transaction_id','').strip()
        if provider=='Razorpay' and razorpay_enabled():
            try:
                order=razorpay_create_order(amount,f'EHS-{bid}-{int(datetime.utcnow().timestamp())}')
                c=db(); c.execute('INSERT INTO payments(booking_id,amount,provider,transaction_id,status,created_at) VALUES(?,?,?,?,?,?)',(bid,amount,'Razorpay',order['id'],'PENDING',now())); c.commit(); c.close()
                return render_template('payment.html',b=b,amount=amount,razorpay_order=order)
            except Exception:
                flash('Gateway order could not be created. Check server gateway configuration.')
        else:
            c=db(); c.execute('INSERT INTO payments(booking_id,amount,provider,transaction_id,status,created_at) VALUES(?,?,?,?,?,?)',(bid,amount,provider,tx,'PENDING',now())); c.commit(); c.close(); flash('Payment recorded as PENDING; verify it before marking it PAID.')
        return redirect(url_for('invoice',bid=bid))
    return render_template('payment.html',b=b,amount=amount,razorpay_order=None)

@APP.route('/payment/razorpay/verify',methods=['POST'])
@roles('CUSTOMER')
def razorpay_verify():
    if not razorpay_enabled(): abort(503)
    bid=int(request.form['booking_id']); order_id=request.form['razorpay_order_id']; payment_id=request.form['razorpay_payment_id']; signature=request.form['razorpay_signature']
    c=db(); b=c.execute('SELECT * FROM bookings WHERE id=? AND user_id=?',(bid,current_user()['id'])).fetchone()
    if not b or not verify_razorpay_signature(order_id,payment_id,signature): c.close(); abort(400)
    c.execute('UPDATE payments SET transaction_id=?,status='+'"PAID"'+',verified_at=? WHERE booking_id=? AND provider='+'"Razorpay"'+' AND transaction_id=?',(payment_id,now(),bid,order_id)); c.commit(); c.close(); flash('Razorpay payment verified successfully.'); return redirect(url_for('invoice',bid=bid))
@APP.route('/save-address',methods=['POST'])
@roles('CUSTOMER')
def save_address():
    c=db(); c.execute('INSERT INTO addresses(user_id,label,address,landmark,city,district,state,pincode,lat,lng,is_default) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(current_user()['id'],request.form.get('label','Home'),request.form['address'],request.form.get('landmark',''),request.form.get('city',''),request.form['district'],request.form['state'],request.form['pincode'],request.form.get('lat') or None,request.form.get('lng') or None,int(bool(request.form.get('is_default'))))); c.commit(); c.close(); flash('Address saved.'); return redirect(url_for('book'))

@APP.route('/technician/register',methods=['GET','POST'])
def technician_register():
    if request.method=='POST':
        try:
            c=db(); cur=c.execute('INSERT INTO users(name,phone,email,password,role,created_at) VALUES(?,?,?,?,?,?)',(request.form['name'],request.form['phone'],request.form.get('email',''),generate_password_hash(request.form['password']),'TECHNICIAN',now())); uid=cur.lastrowid; c.execute('INSERT INTO technician_profiles(user_id,address,district,state,pincode,skills,experience,service_area) VALUES(?,?,?,?,?,?,?,?)',(uid,request.form.get('address',''),request.form['district'],request.form['state'],request.form['pincode'],request.form.get('skills',''),request.form.get('experience',''),request.form.get('service_area',''))); c.commit(); c.close(); flash('Technician registration submitted for Owner verification.'); return redirect(url_for('login'))
        except sqlite3.IntegrityError: flash('Phone number already registered.')
    return render_template('technician_register.html')
@APP.route('/technician')
@roles('TECHNICIAN')
def technician():
    u=current_user(); c=db(); jobs=c.execute('SELECT b.*,s.name service,s.price,s.service_charge FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.technician_id=? OR (b.technician_id IS NULL AND b.status="REQUESTED") ORDER BY b.id DESC',(u['id'],)).fetchall(); profile=c.execute('SELECT * FROM technician_profiles WHERE user_id=?',(u['id'],)).fetchone(); c.close(); return render_template('technician.html',jobs=jobs,profile=profile)
@APP.route('/technician/job/<int:bid>/status',methods=['POST'])
@roles('TECHNICIAN')
def tech_status(bid):
    u=current_user(); status=request.form['status'];
    if status not in STATUSES: abort(400)
    c=db(); b=c.execute('SELECT * FROM bookings WHERE id=? AND (technician_id=? OR technician_id IS NULL)',(bid,u['id'])).fetchone();
    if not b: abort(404)
    if b['technician_id'] is None: c.execute('UPDATE bookings SET technician_id=?,status=? WHERE id=?',(u['id'],'ACCEPTED',bid)); notify(b['user_id'],'Technician Assigned',f'{u["name"]} accepted booking #{bid}.')
    else: c.execute('UPDATE bookings SET status=? WHERE id=?',(status,bid));
    c.commit(); c.close(); return redirect(url_for('technician'))
@APP.route('/technician/job/<int:bid>/otp',methods=['POST'])
@roles('TECHNICIAN')
def verify_completion(bid):
    otp=request.form['otp']; c=db(); b=c.execute('SELECT * FROM bookings WHERE id=? AND technician_id=?',(bid,current_user()['id'])).fetchone();
    if not b: abort(404)
    if not b['completion_otp_hash'] or b['otp_expires']<now() or not check_password_hash(b['completion_otp_hash'],otp): c.close(); flash('Invalid or expired completion OTP.'); return redirect(url_for('technician'))
    c.execute('UPDATE bookings SET otp_verified=1,status="SERVICE COMPLETED",completed_at=? WHERE id=?',(now(),bid)); s=c.execute('SELECT * FROM services WHERE id=?',(b['service_id'],)).fetchone(); owner_pct=float(s['commission']); amount=float(s['price'])+float(s['service_charge'])+float(b['distance_charge']); owner=amount*owner_pct/100; tech=amount-owner; c.execute('INSERT INTO commissions(booking_id,service_amount,owner_amount,technician_amount,percentage,created_at) VALUES(?,?,?,?,?,?)',(bid,amount,owner,tech,owner_pct,now())); c.execute('INSERT INTO earnings(booking_id,owner_amount,technician_amount,created_at) VALUES(?,?,?,?)',(bid,owner,tech,now())); c.commit(); c.close(); notify(b['user_id'],'Service Completed',f'Booking #{bid} is completed.'); flash('Service completion verified.'); return redirect(url_for('technician'))
@APP.route('/review/<int:bid>',methods=['POST'])
@roles('CUSTOMER')
def review(bid):
    c=db(); b=c.execute('SELECT * FROM bookings WHERE id=? AND user_id=? AND status="SERVICE COMPLETED"',(bid,current_user()['id'])).fetchone();
    if not b: abort(404)
    c.execute('INSERT OR REPLACE INTO reviews(booking_id,rating,comment,created_at) VALUES(?,?,?,?)',(bid,max(1,min(5,int(request.form['rating']))),request.form.get('comment',''),now())); c.commit(); c.close(); flash('Review saved.'); return redirect(url_for('dashboard'))

@APP.route('/owner')
@roles('OWNER')
def owner():
    c=db(); stats={
      'customers':c.execute('SELECT COUNT(*) FROM users WHERE role="CUSTOMER"').fetchone()[0], 'technicians':c.execute('SELECT COUNT(*) FROM users WHERE role="TECHNICIAN"').fetchone()[0],
      'active':c.execute('SELECT COUNT(*) FROM bookings WHERE status NOT IN ("SERVICE COMPLETED","CANCELLED")').fetchone()[0], 'completed':c.execute('SELECT COUNT(*) FROM bookings WHERE status="SERVICE COMPLETED"').fetchone()[0],
      'revenue':c.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE status="PAID"').fetchone()[0], 'pending':c.execute('SELECT COUNT(*) FROM payments WHERE status="PENDING"').fetchone()[0]}
    services=c.execute('SELECT * FROM services ORDER BY id').fetchall(); products=c.execute('SELECT * FROM products ORDER BY id').fetchall(); users=c.execute('SELECT id,name,phone,role,active FROM users ORDER BY id DESC').fetchall(); techs=c.execute('SELECT u.*,p.verification_status,p.skills FROM users u LEFT JOIN technician_profiles p ON p.user_id=u.id WHERE u.role="TECHNICIAN" ORDER BY u.id DESC').fetchall(); bookings=c.execute('SELECT b.*,s.name service,u.name customer,t.name technician FROM bookings b JOIN services s ON s.id=b.service_id JOIN users u ON u.id=b.user_id LEFT JOIN users t ON t.id=b.technician_id ORDER BY b.id DESC LIMIT 50').fetchall(); logs=c.execute('SELECT a.*,u.name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 50').fetchall(); c.close(); return render_template('owner.html',stats=stats,services=services,products=products,users=users,techs=techs,bookings=bookings,logs=logs)
@APP.route('/owner/service',methods=['POST'])
@roles('OWNER')
def owner_service():
    sid=request.form.get('id'); vals=(request.form['name'],request.form.get('description',''),float(request.form['price']),float(request.form.get('service_charge',0)),int(request.form.get('duration',60)),float(request.form.get('commission',20)),float(request.form.get('technician_payout',80)))
    c=db()
    if sid:
        old=c.execute('SELECT * FROM services WHERE id=?',(int(sid),)).fetchone(); c.execute('UPDATE services SET name=?,description=?,price=?,service_charge=?,duration=?,commission=?,technician_payout=?,updated_at=? WHERE id=?',(*vals,now(),int(sid))); audit(current_user()['id'],'UPDATE','service',int(sid),str(dict(old)),str(vals))
    else:
        cur=c.execute('INSERT INTO services(name,description,price,service_charge,duration,commission,technician_payout,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(*vals,now(),now())); audit(current_user()['id'],'CREATE','service',cur.lastrowid,'',str(vals))
    c.commit(); c.close(); return redirect(url_for('owner'))
@APP.route('/owner/product',methods=['POST'])
@roles('OWNER')
def owner_product():
    c=db(); cur=c.execute('INSERT OR REPLACE INTO products(id,name,sku,price,stock,gst,active) VALUES(COALESCE(?,NULL),?,?,?,?,?,1)',(request.form.get('id') or None,request.form['name'],request.form['sku'],float(request.form['price']),int(request.form['stock']),float(request.form.get('gst',0)))); c.commit(); audit(current_user()['id'],'UPSERT','product',cur.lastrowid,'',request.form['name']); c.close(); return redirect(url_for('owner'))
@APP.route('/owner/technician/<int:uid>',methods=['POST'])
@roles('OWNER')
def owner_tech(uid):
    status=request.form['status']; reason=request.form.get('reason',''); c=db(); c.execute('UPDATE technician_profiles SET verification_status=?,rejection_reason=? WHERE user_id=?',(status,reason,uid)); c.commit(); c.close(); audit(current_user()['id'],'VERIFY','technician',uid,'',status); notify(uid,'Verification Update',f'Your technician verification status is {status}.'); return redirect(url_for('owner'))
@APP.route('/owner/user/<int:uid>/toggle',methods=['POST'])
@roles('OWNER')
def toggle_user(uid):
    if uid==current_user()['id']: abort(400)
    c=db(); u=c.execute('SELECT active FROM users WHERE id=?',(uid,)).fetchone(); c.execute('UPDATE users SET active=? WHERE id=?',(0 if u['active'] else 1,uid)); c.commit(); c.close(); audit(current_user()['id'],'TOGGLE_USER','user',uid,str(u['active']),str(0 if u['active'] else 1)); return redirect(url_for('owner'))
@APP.route('/owner/settings',methods=['POST'])
@roles('OWNER')
def owner_settings():
    c=db()
    for k in ['app_name','logo_url','owner_commission','tax_percent']:
        if k in request.form:
            old=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)',(k,request.form[k])); audit(current_user()['id'],'SETTING','settings',0,str(old['v'] if old else ''),request.form[k])
    c.commit(); c.close(); return redirect(url_for('owner'))
@APP.route('/owner/backup')
@roles('OWNER')
def owner_backup():
    path=backup_db(); return send_file(path,as_attachment=True,download_name=os.path.basename(path))
@APP.route('/owner/audit')
@roles('OWNER')
def owner_audit():
    c=db(); logs=c.execute('SELECT a.*,u.name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC').fetchall(); c.close(); return render_template('audit.html',logs=logs)
@APP.route('/owner/doc/<int:docid>')
@roles('OWNER')
def owner_doc(docid):
    c=db(); d=c.execute('SELECT * FROM technician_documents WHERE id=?',(docid,)).fetchone(); c.close();
    if not d: abort(404)
    return send_file(d['file_path'],as_attachment=True)
@APP.route('/owner/technician/<int:uid>/document',methods=['POST'])
@roles('OWNER','TECHNICIAN')
def upload_doc(uid):
    u=current_user();
    if u['role']=='TECHNICIAN' and u['id']!=uid: abort(403)
    f=request.files.get('document'); dtype=request.form.get('doc_type','OTHER')
    if not f or '.' not in f.filename: abort(400)
    ext=f.filename.rsplit('.',1)[1].lower();
    if ext not in ALLOWED_DOC: abort(400)
    name=secrets.token_hex(16)+'.'+ext; path=os.path.join(UPLOAD_DIR,name); f.save(path)
    c=db(); c.execute('INSERT INTO technician_documents(technician_id,doc_type,file_path,uploaded_at) VALUES(?,?,?,?)',(uid,dtype,path,now())); c.commit(); c.close(); return redirect(url_for('technician' if u['role']=='TECHNICIAN' else 'owner'))
@APP.route('/owner/offer',methods=['POST'])
@roles('OWNER')
def owner_offer():
    c=db(); c.execute('INSERT INTO offers(title,description,active,created_at) VALUES(?,?,1,?)',(request.form['title'],request.form.get('description',''),now())); c.commit(); c.close(); return redirect(url_for('owner'))
@APP.route('/owner/coupon',methods=['POST'])
@roles('OWNER')
def owner_coupon():
    c=db(); c.execute('INSERT OR REPLACE INTO coupons(code,discount_type,discount_value,active,expires_at) VALUES(?,?,?,?,?)',(request.form['code'].upper(),request.form['discount_type'],float(request.form['discount_value']),1,request.form.get('expires_at',''))); c.commit(); c.close(); return redirect(url_for('owner'))

@APP.route('/api/booking/<int:bid>/location',methods=['POST'])
@roles('TECHNICIAN')
def update_location(bid):
    c=db(); b=c.execute('SELECT * FROM bookings WHERE id=? AND technician_id=?',(bid,current_user()['id'])).fetchone();
    if not b: abort(404)
    c.execute('UPDATE bookings SET lat=?,lng=? WHERE id=?',(request.form.get('lat'),request.form.get('lng'),bid)); c.commit(); c.close(); return jsonify(ok=True)
@APP.route('/api/booking/<int:bid>/status',methods=['POST'])
@roles('OWNER')
def owner_booking_status(bid):
    st=request.form['status'];
    if st not in STATUSES: abort(400)
    c=db(); b=c.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone(); c.execute('UPDATE bookings SET status=? WHERE id=?',(st,bid)); c.commit(); c.close(); audit(current_user()['id'],'STATUS','booking',bid,b['status'],st); notify(b['user_id'],'Booking Update',f'Booking #{bid} status: {st}'); return jsonify(ok=True)

if __name__=='__main__': APP.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)
