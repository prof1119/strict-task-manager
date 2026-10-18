"""
نظام إدارة المهام والتنبيهات الصارم - النسخة النهائية
Strict Task Manager - Final Version
"""

from flask import Flask, render_template_string, request, redirect, url_for, jsonify, session
from datetime import datetime, timedelta, date
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import sqlite3
import json
import urllib.request
import urllib.parse
import logging
import os
import time
import threading
import functools
import secrets
import hmac
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Never ship real credentials or predictable secrets in source control.
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    _secret_key = os.urandom(32).hex()
    logger.warning('SECRET_KEY is not set; sessions will be invalidated after restart. Set it in the environment.')
app.config.update(
    SECRET_KEY=_secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '0').lower() in ('1', 'true', 'yes'),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

@app.before_request
def protect_state_changes():
    """Validate a per-session CSRF token on state-changing requests."""
    if not session.get('_csrf_token'):
        session['_csrf_token'] = secrets.token_urlsafe(32)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        supplied = request.headers.get('X-CSRF-Token') or request.form.get('_csrf_token', '')
        expected = session.get('_csrf_token', '')
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            return jsonify({'status': 'error', 'message': 'CSRF validation failed. Refresh the page and try again.'}), 400

@app.after_request
def add_csrf_fields(response):
    """Expose the token to same-origin forms and fetch calls."""
    if response.mimetype == 'text/html' and session.get('_csrf_token'):
        html = response.get_data(as_text=True)
        token = session['_csrf_token']
        meta = '<meta name="csrf-token" content="' + token + '">'
        if '<head>' in html and 'name="csrf-token"' not in html:
            html = html.replace('<head>', '<head>\n    ' + meta, 1)
        hidden = '<input type="hidden" name="_csrf_token" value="' + token + '">'
        html = re.sub(r'(<form\b(?=[^>]*\bmethod=["\']?POST)[^>]*>)', lambda m: m.group(1) + hidden, html, flags=re.IGNORECASE)
        response.set_data(html)
    return response

# ==================== إعدادات ====================
# Set these values in the hosting environment; never commit actual secrets.
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
CHAT_ID = os.environ.get('CHAT_ID', '').strip()
APP_PASSWORD = os.environ.get('APP_PASSWORD', '')
API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ''
DB = os.environ.get('DATABASE_PATH', 'tasks.db')

# إحداثيات السناجرة / أبو حماد - الشرقية (دقيقة جدًا)
PRAYER_LAT    = 30.5236   # خط العرض
PRAYER_LNG    = 31.6722   # خط الطول
PRAYER_METHOD = 5         # Egyptian General Authority of Survey
PRAYER_NAMES_AR = {
    'Fajr': 'الفجر', 'Dhuhr': 'الظهر', 'Asr': 'العصر',
    'Maghrib': 'المغرب', 'Isha': 'العشاء'
}
CITY = "أبو حماد - السناجرة"

if not BOT_TOKEN or not CHAT_ID:
    logger.warning("Telegram notifications are disabled: set BOT_TOKEN and CHAT_ID to enable them.")
if not APP_PASSWORD:
    logger.warning("APP_PASSWORD is not set. Login will reject all passwords until it is configured.")

# ==================== نظام اللوجن ====================
def login_required(f):
    """decorator - أي route عليه ده لازم يكون المستخدم داخل"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

LOGIN_HTML = '''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>دخول | نظام المهام</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{
            min-height:100vh;display:flex;align-items:center;justify-content:center;
            background:radial-gradient(circle at 30% 30%,rgba(0,245,255,.08),transparent 50%),
                        radial-gradient(circle at 70% 70%,rgba(185,103,255,.08),transparent 50%),
                        #0a0e17;
            font-family:'Segoe UI',Tahoma,Arial,sans-serif;color:#e8ecf5;
        }
        .box{
            background:linear-gradient(145deg,#111827,#0f1523);
            border:1px solid #1f2937;border-radius:20px;
            padding:42px 38px;width:92%;max-width:400px;
            box-shadow:0 25px 60px rgba(0,0,0,.6);
        }
        h2{
            text-align:center;margin-bottom:28px;font-size:1.5rem;
            background:linear-gradient(90deg,#00f5ff,#b967ff);
            -webkit-background-clip:text;background-clip:text;color:transparent;
        }
        .icon{text-align:center;font-size:3rem;margin-bottom:16px}
        label{display:block;margin-bottom:6px;font-size:.85rem;color:#8993a8;font-weight:600}
        input{
            width:100%;padding:13px 16px;border:1px solid #1f2937;border-radius:10px;
            background:#0a0e17;color:#e8ecf5;font-size:1rem;margin-bottom:20px;
        }
        input:focus{outline:none;border-color:#00f5ff;box-shadow:0 0 0 3px rgba(0,245,255,.15)}
        button{
            width:100%;padding:14px;border:none;border-radius:10px;
            background:linear-gradient(135deg,#00f5ff,#b967ff);
            color:#050d14;font-size:1.05rem;font-weight:800;cursor:pointer;
            transition:opacity .2s;
        }
        button:hover{opacity:.88}
        .err{
            background:rgba(255,61,110,.1);border:1px solid rgba(255,61,110,.3);
            color:#ff3d6e;padding:12px;border-radius:8px;margin-bottom:16px;
            text-align:center;font-size:.9rem;
        }
        .hint{text-align:center;margin-top:16px;font-size:.78rem;color:#4b5563}
    </style>
</head>
<body>
<div class="box">
    <div class="icon">⚡</div>
    <h2>نظام المهام الصارم</h2>
    {% if error %}<div class="err">❌ {{ error }}</div>{% endif %}
    <form method="POST">
        <label>كلمة المرور</label>
        <input type="password" name="password" placeholder="••••••••" autofocus required>
        <button type="submit">دخول ⚡</button>
    </form>
    <p class="hint">أبو حماد · الشرقية · مصر</p>
</div>
</body>
</html>'''

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if APP_PASSWORD and request.form.get('password') == APP_PASSWORD:
            session.clear()  # Rotate the session on successful authentication.
            session['logged_in'] = True
            session.permanent = True
            return redirect('/')
        error = 'كلمة المرور غلط، حاول تاني'
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect('/login')

# ==================== قاعدة بيانات ====================
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        category TEXT DEFAULT 'عام',
        scheduled_time TIMESTAMP NOT NULL,
        duration INTEGER DEFAULT 30,
        is_done INTEGER DEFAULT 0,
        status TEXT DEFAULT 'Pending',
        priority TEXT DEFAULT 'medium',
        task_type TEXT DEFAULT 'task',
        course_id INTEGER,
        last_nag_time TIMESTAMP,
        warning_sent INTEGER DEFAULT 0,
        start_sent INTEGER DEFAULT 0,
        started_at TIMESTAMP,
        should_finish_at TIMESTAMP,
        finished INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        code TEXT UNIQUE NOT NULL,
        total_hours REAL NOT NULL,
        total_days INTEGER NOT NULL,
        daily_hours REAL,
        start_time TEXT DEFAULT '09:00',
        start_date TIMESTAMP NOT NULL,
        progress REAL DEFAULT 0,
        status TEXT DEFAULT 'active',
        color TEXT DEFAULT '#4e73df',
        repeat_days TEXT,
        last_final_warning_date TEXT,
        last_final_hour_warning TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS prayers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prayer_date TEXT NOT NULL,
        prayer_name TEXT NOT NULL,
        prayer_time TEXT NOT NULL,
        warning_sent INTEGER DEFAULT 0,
        start_sent INTEGER DEFAULT 0,
        last_nag_time TIMESTAMP,
        confirmed INTEGER DEFAULT 0,
        UNIQUE(prayer_date, prayer_name)
    )''')

    # إضافة أعمدة جديدة إذا مش موجودة (ترقية آمنة لقاعدة بيانات قديمة)
    for alter_sql in [
        "ALTER TABLE courses ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE courses ADD COLUMN repeat_days TEXT",
        "ALTER TABLE courses ADD COLUMN last_final_warning_date TEXT",
        "ALTER TABLE courses ADD COLUMN last_final_hour_warning TEXT",
        "ALTER TABLE tasks ADD COLUMN notes TEXT",
        "ALTER TABLE tasks ADD COLUMN tags TEXT",
        "ALTER TABLE tasks ADD COLUMN repeat_type TEXT DEFAULT 'none'",
        "ALTER TABLE tasks ADD COLUMN repeat_count INTEGER DEFAULT 0",
        "ALTER TABLE tasks ADD COLUMN repeat_remaining INTEGER DEFAULT 0",
        "ALTER TABLE tasks ADD COLUMN repeat_interval_minutes INTEGER",
    ]:
        try:
            c.execute(alter_sql)
        except Exception:
            pass

    conn.commit()
    conn.close()

def normalize_dt(raw):
    """
    يحول أي صيغة تاريخ جايه من الفورم (datetime-local بيها T أو مسافة)
    إلى صيغة موحدة 'YYYY-MM-DD HH:MM:SS' عشان المقارنات النصية في SQLite تشتغل صح.
    """
    if not raw:
        return None
    raw = raw.strip().replace('T', ' ')
    if len(raw) == 16:  # YYYY-MM-DD HH:MM
        raw += ':00'
    return raw

PRIORITY_ORDER = {'high': 0, 'medium': 1, 'low': 2}

def compute_course_progress(course_id):
    """يحسب نسبة تقدم الكورس بناء على عدد الجلسات المكتملة"""
    sessions = q('SELECT is_done FROM tasks WHERE course_id=?', (course_id,))
    if not sessions:
        return 0
    done = sum(1 for s in sessions if s['is_done'])
    return round(done / len(sessions) * 100, 1)

def compute_course_live_stats(course):
    """
    يحسب حالة الكورس اللايف:
    - كام يوم عدى من إجمالي الأيام (بناء على تاريخ البدء الفعلي)
    - كام ساعة اتذاكرت فعليًا (بناء على الجلسات المنجزة) من إجمالي الساعات
    - كام باقي على نهاية الكورس (بالوقت الفعلي مش بعدد الأيام بس)
    """
    sessions = q('SELECT * FROM tasks WHERE course_id=? ORDER BY scheduled_time ASC', (course['id'],))
    total_sessions = len(sessions)
    done_sessions = sum(1 for s in sessions if s['is_done'])

    total_hours = course['total_hours'] or 0
    hours_studied = round((done_sessions / total_sessions) * total_hours, 2) if total_sessions else 0

    try:
        start_dt = datetime.strptime(course['start_date'][:10] + ' ' + (course['start_time'] or '09:00'), '%Y-%m-%d %H:%M')
    except Exception:
        start_dt = datetime.now()

    total_days = course['total_days'] or 1
    now = datetime.now()
    days_elapsed = max(0, (now.date() - start_dt.date()).days)
    days_elapsed = min(days_elapsed, total_days)

    # آخر جلسة مجدولة = نهاية الكورس الفعلية
    if sessions:
        last_session_raw = normalize_dt(sessions[-1]['scheduled_time'])
        last_session_dt = datetime.strptime(last_session_raw, '%Y-%m-%d %H:%M:%S')
        end_dt = last_session_dt + timedelta(minutes=sessions[-1]['duration'] or 60)
    else:
        end_dt = start_dt + timedelta(days=total_days)

    time_left = end_dt - now
    time_left_seconds = max(0, time_left.total_seconds())
    hours_left_num = time_left_seconds / 3600

    if hours_left_num >= 24:
        time_left_display = f"{int(hours_left_num // 24)} يوم و {int(hours_left_num % 24)} ساعة"
    elif hours_left_num >= 1:
        time_left_display = f"{int(hours_left_num)} ساعة"
    elif time_left_seconds > 0:
        time_left_display = f"{int(time_left_seconds // 60)} دقيقة"
    else:
        time_left_display = "انتهى الوقت"

    return {
        'days_elapsed': days_elapsed,
        'total_days': total_days,
        'hours_studied': hours_studied,
        'total_hours': total_hours,
        'done_sessions': done_sessions,
        'total_sessions': total_sessions,
        'time_left_display': time_left_display,
        'hours_left_num': round(hours_left_num, 1),
        'end_dt': end_dt,
        'is_finished': time_left_seconds <= 0 and total_sessions > 0 and done_sessions >= total_sessions,
    }

def q(sql, params=(), fetch=True):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        if fetch:
            result = cur.fetchall()
        else:
            conn.commit()
            result = cur.lastrowid
    except Exception as e:
        logger.error(f"DB Error: {e}")
        result = [] if fetch else 0
    conn.close()
    return result

# ==================== تيليجرام ====================
def tg_send(text, keyboard=None):
    """إرسال رسالة تيليجرام بـ JSON (مش urlencode عشان متكسرش العربي والـ HTML)"""
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("tg_send: BOT_TOKEN أو CHAT_ID فاضي")
        return False
    try:
        payload = {
            'chat_id': CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'
        }
        if keyboard:
            payload['reply_markup'] = keyboard  # dict مش string

        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req  = urllib.request.Request(
            f"{API}/sendMessage",
            data=body,
            headers={'Content-Type': 'application/json; charset=utf-8'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if not result.get('ok'):
                logger.error(f"TG not ok: {result}")
            return result.get('ok', False)
    except Exception as e:
        logger.error(f"TG Error: {e}")
        return False

# ==================== معالجة الأزرار ====================
# ==================== مواقيت الصلاة ====================
def fetch_prayer_times(target_date):
    """
    يجيب مواقيت الصلاة بالإحداثيات الدقيقة للسناجرة / أبو حماد.
    أدق من اسم المدينة لأن Aladhan أحيانًا بيرجع مدينة غلط باسم "Abu Hammad".
    """
    try:
        date_str = target_date.strftime('%d-%m-%Y')
        # timings endpoint بالإحداثيات - أدق وأضمن
        url = (f"https://api.aladhan.com/v1/timings/{date_str}"
               f"?latitude={PRAYER_LAT}&longitude={PRAYER_LNG}"
               f"&method={PRAYER_METHOD}&school=0")
        req = urllib.request.Request(url, headers={'User-Agent': 'TaskManager/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if data.get('code') == 200:
            timings = data['data']['timings']
            result = {}
            for name in PRAYER_NAMES_AR.keys():
                t = timings.get(name, '')
                result[name] = t[:5] if t else ''  # ناخد HH:MM بس
            logger.info(f"✅ مواقيت الصلاة ({date_str}): {result}")
            return result
        else:
            logger.error(f"Aladhan API error: {data}")
    except Exception as e:
        logger.error(f"Prayer API Error: {e}")
    return None

def sync_prayer_times_for_date(target_date):
    """يجيب مواقيت الصلاة ويخزنها - الوقت بصيغة YYYY-MM-DD HH:MM:00"""
    date_str = target_date.strftime('%Y-%m-%d')
    existing = q("SELECT COUNT(*) as c FROM prayers WHERE prayer_date=?", (date_str,))[0]['c']
    if existing >= len(PRAYER_NAMES_AR):
        logger.info(f"الصلاة {date_str} موجودة أصلاً")
        return

    timings = fetch_prayer_times(target_date)
    if not timings:
        logger.warning(f"⚠️ فشل جلب مواقيت {date_str}")
        return

    for prayer_name, hhmm in timings.items():
        if not hhmm:
            continue
        full_dt = f"{date_str} {hhmm}:00"   # YYYY-MM-DD HH:MM:00
        q('''INSERT OR IGNORE INTO prayers (prayer_date, prayer_name, prayer_time)
             VALUES (?,?,?)''', (date_str, prayer_name, full_dt), fetch=False)

    logger.info(f"✅ تم حفظ مواقيت الصلاة ليوم {date_str}")

def ensure_prayer_times_synced():
    """يتأكد إن مواقيت النهاردة وبكرة موجودة في القاعدة (بيتنفذ بشكل دوري)"""
    today = datetime.now().date()
    sync_prayer_times_for_date(today)
    sync_prayer_times_for_date(today + timedelta(days=1))

def check_prayer_notifications():
    """
    نفس نظام المهام العادية بالظبط:
    - تنبيه قبل الصلاة بـ 5 دقايق
    - رسالة وقت الصلاة + زرار تأكيد
    - إلحاح كل دقيقة لو مفيش تأكيد
    """
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')

    # 1. تحذير قبل الصلاة بـ 5 دقايق
    warning_time = (now + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    prayers = q('''SELECT * FROM prayers WHERE prayer_time<=? AND prayer_time>?
                   AND warning_sent=0 AND confirmed=0''', (warning_time, now_str))
    for p in prayers:
        name_ar = PRAYER_NAMES_AR.get(p['prayer_name'], p['prayer_name'])
        msg = f"""🕌 <b>تنبيه! صلاة {name_ar} خلال 5 دقائق</b>
⏰ الموعد: {p['prayer_time'][11:16]}
<i>جهز نفسك للصلاة!</i>"""
        if tg_send(msg):
            q('UPDATE prayers SET warning_sent=1 WHERE id=?', (p['id'],), fetch=False)

    # 2. وقت الصلاة - رسالة + زرار تأكيد
    prayers = q('''SELECT * FROM prayers WHERE prayer_time<=? AND start_sent=0 AND confirmed=0''', (now_str,))
    for p in prayers:
        name_ar = PRAYER_NAMES_AR.get(p['prayer_name'], p['prayer_name'])
        msg = f"""🕌🕌🕌 <b>حان وقت صلاة {name_ar}!</b>
⏰ {p['prayer_time'][11:16]}
<i>قوم صلي وارجع دوس تأكيد</i>"""
        kb = {'inline_keyboard': [[
            {'text': '✅ صليت', 'callback_data': f'prayer_done_{p["id"]}'}
        ]]}
        if tg_send(msg, kb):
            q('UPDATE prayers SET start_sent=1, last_nag_time=? WHERE id=?', (now_str, p['id']), fetch=False)

    # ============================================================
    # 🔔 رسائل إلحاح الصلاة - عدّل هنا براحتك 🔔
    # ============================================================
    PRAYER_NAG_MESSAGES = [
        lambda name_ar, t: f"🔔 الصلاة يا صاحبي 🔔\n🕌 <b>صلاة {name_ar}</b> مستنياك!\n⏰ كان الموعد: {t['prayer_time'][11:16]}\n<i>قوم صلي وارجع دوس تأكيد!</i>",
        lambda name_ar, t: f"🔔 تاني تذكير للصلاة 🔔\n🕌 <b>{name_ar}</b> لسه ما اتأكدتش!\n⏰ فات على الوقت شوية\n<i>يلا قوم الصلاة أهم حاجة!</i>",
        lambda name_ar, t: f"🔔 تالت تذكير 🔔\n🕌🕌 <b>صلاة {name_ar}</b> 🕌🕌\n😤 لسه ما صليتش؟\n<i>ما في عذر - قوم صلي دلوقتي!</i>",
        lambda name_ar, t: f"🔔 رابع تذكير - ربنا شايفك 🔔\n🕌 <b>{name_ar}</b> يا عم!\n⏰ الوقت بيعدي والصلاة واجبة\n<i>قوم صلي وخلص وارجع!</i>",
        lambda name_ar, t: f"🔔 آخر تذكير والله 🔔\n🕌🚨 <b>صلاة {name_ar}</b> 🚨🕌\n😰 الوقت بيعدي يا صاحبي!\n<i>قوم صلي دلوقتي قبل ما يفوت الوقت!</i>",
    ]
    # ============================================================

    # 3. إلحاح الصلاة كل دقيقة لو ما اتأكدش
    nag_time_p = (now - timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')
    prayers = q('''SELECT * FROM prayers WHERE prayer_time<=? AND start_sent=1 AND confirmed=0
                   AND (last_nag_time IS NULL OR last_nag_time<=?)''', (now_str, nag_time_p))
    for p in prayers:
        name_ar = PRAYER_NAMES_AR.get(p['prayer_name'], p['prayer_name'])
        # نحسب عدد مرات الإلحاح من nag_count المخزن في prayer_nag_count لو موجود، أو بعدد مرات التحديث
        nag_c = q("SELECT COUNT(*) as c FROM prayers WHERE id=? AND last_nag_time IS NOT NULL", (p['id'],))
        idx = (nag_c[0]['c'] if nag_c else 0) % len(PRAYER_NAG_MESSAGES)
        msg = PRAYER_NAG_MESSAGES[idx](name_ar, p)
        kb = {'inline_keyboard': [[{'text': '✅ صليت الحمد لله', 'callback_data': f'prayer_done_{p["id"]}'}]]}
        if tg_send(msg, kb):
            q('UPDATE prayers SET last_nag_time=? WHERE id=?', (now_str, p['id']), fetch=False)

# ==================== معالجة الأزرار ====================
def process_callback(data):
    logger.info(f"🔘 زر: {data}")
    
    if data.startswith('start_task_'):
        tid = int(data.split('_')[2])
        now = datetime.now()
        task = q('SELECT * FROM tasks WHERE id=?', (tid,))
        if task:
            t = task[0]
            dur = t['duration'] or 30
            finish = now + timedelta(minutes=dur)
            q('''UPDATE tasks SET status='In Progress', started_at=?, should_finish_at=?, last_nag_time=? WHERE id=?''',
              (now.strftime('%Y-%m-%d %H:%M:%S'), finish.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S'), tid), fetch=False)
            tg_send(f"🚀 <b>برافووو!</b>\n📌 {t['title']}\n⏱️ المدة: {dur}د\n⏰ تخلص: {finish.strftime('%H:%M')}")
    
    elif data.startswith('finish_task_'):
        tid = int(data.split('_')[2])
        task = q('SELECT * FROM tasks WHERE id=?', (tid,))
        q('UPDATE tasks SET is_done=1, status="Completed", finished=1 WHERE id=?', (tid,), fetch=False)
        if task:
            t = task[0]
            tg_send(f"🎉🎉🎉 <b>أحسنت يا أسطى!</b>\n✅ تم: {t['title']}")
            # لو المهمة متكررة وباقي منها مرات، نجدول النسخة الجاية أوتوماتيك
            if t['repeat_type'] == 'count' and (t['repeat_remaining'] or 0) > 0:
                raw = normalize_dt(t['scheduled_time'])
                old_time = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S')
                next_time = old_time + timedelta(days=1)
                remaining = t['repeat_remaining'] - 1
                base_title = t['title'].split(' (')[0]
                total = t['repeat_count']
                occurrence_no = total - remaining
                q('''INSERT INTO tasks (title, category, scheduled_time, priority, duration, repeat_type, repeat_count, repeat_remaining)
                     VALUES (?,?,?,?,?,?,?,?)''',
                  (f"{base_title} ({occurrence_no}/{total})", t['category'], next_time.strftime('%Y-%m-%d %H:%M:%S'),
                   t['priority'], t['duration'], 'count', total, remaining), fetch=False)
    
    elif data.startswith('still_working_'):
        tid = int(data.split('_')[2])
        now = datetime.now()
        new_finish = now + timedelta(minutes=10)
        q('UPDATE tasks SET should_finish_at=?, last_nag_time=? WHERE id=?',
          (new_finish.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S'), tid), fetch=False)
        tg_send(f"⏰ <b>تمام!</b> خد 10 دقايق زيادة\n⏰ تخلص: {new_finish.strftime('%H:%M')}")
    
    elif data.startswith('wake_up_'):
        tid = int(data.split('_')[2])
        now = datetime.now()
        q('UPDATE tasks SET last_nag_time=? WHERE id=?', (now.strftime('%Y-%m-%d %H:%M:%S'), tid), fetch=False)
        tg_send("☕ <b>اصحى يا نعسان!</b> ركز وكمل شغلك!")
    
    elif data.startswith('delay_task_') or data.startswith('delay_before_'):
        tid = int(data.split('_')[2])
        now = datetime.now()
        new_time = now + timedelta(minutes=10)
        q('''UPDATE tasks SET scheduled_time=?, warning_sent=0, start_sent=0,
             last_nag_time=NULL, started_at=NULL, should_finish_at=NULL WHERE id=?''',
          (new_time.strftime('%Y-%m-%d %H:%M:%S'), tid), fetch=False)
        tg_send(f"⏰ <b>تم التأجيل 10 دقايق</b>\n🕐 {new_time.strftime('%H:%M')}")
    
    elif data.startswith('ready_task_'):
        tid = int(data.split('_')[2])
        q('UPDATE tasks SET last_nag_time=? WHERE id=?',
          (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tid), fetch=False)
        tg_send("✅ <b>تمام!</b> استعد... المهمة هتبدأ")

    elif data.startswith('prayer_done_'):
        pid = int(data.split('_')[2])
        prayer = q('SELECT * FROM prayers WHERE id=?', (pid,))
        if prayer:
            p = prayer[0]
            name_ar = PRAYER_NAMES_AR.get(p['prayer_name'], p['prayer_name'])
            q('UPDATE prayers SET confirmed=1 WHERE id=?', (pid,), fetch=False)
            tg_send(f"✅✅✅ <b>ماشاء الله!</b>\nصليت {name_ar} 🕌\n<i>تقبل الله!</i>")

# ==================== نظام التنبيهات ====================
def check_notifications():
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f"🔔 check_notifications تشغيل @ {now_str}")

    # 1. تحذير 5 دقايق قبل أي مهمة عادية
    warning_time = (now + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    tasks = q('''SELECT * FROM tasks WHERE scheduled_time<=? AND scheduled_time>?
                 AND is_done=0 AND warning_sent=0 AND status="Pending"''', (warning_time, now_str))
    logger.info(f"  ├─ تحذير 5د: {len(tasks)} مهمة (نافذة {now_str} → {warning_time})")
    for t in tasks:
        logger.info(f"  │   └─ [{t['id']}] {t['title']} @ {t['scheduled_time']}")
        msg = f"""⚠️ <b>تنبيه! مهمة خلال 5 دقائق</b>
📌 {t['title']} | 📂 {t['category']} | ⏱️ {t['duration']}د
⏰ {t['scheduled_time'][:16]}
<i>جهز نفسك!</i>"""
        kb = {'inline_keyboard': [[
            {'text': '✅ جاهز', 'callback_data': f'ready_task_{t["id"]}'},
            {'text': '⏰ أجل 10د', 'callback_data': f'delay_before_{t["id"]}'}
        ]]}
        ok = tg_send(msg, kb)
        logger.info(f"  │   tg_send={'✅' if ok else '❌'}")
        if ok:
            q('UPDATE tasks SET warning_sent=1 WHERE id=?', (t['id'],), fetch=False)

    # 2. بدء المهمة
    tasks = q('''SELECT * FROM tasks WHERE scheduled_time<=? AND is_done=0
                 AND start_sent=0 AND status="Pending"''', (now_str,))
    logger.info(f"  ├─ بدء المهمة: {len(tasks)} مهمة")
    for t in tasks:
        logger.info(f"  │   └─ [{t['id']}] {t['title']} @ {t['scheduled_time']} status={t['status']}")
        msg = f"""🚀 <b>يلا بينا! المهمة بدأت</b>
📌 {t['title']} | ⏱️ {t['duration']}د
<i>اضغط [بدأت] وابدأ!</i>"""
        kb = {'inline_keyboard': [[
            {'text': '✅ بدأت', 'callback_data': f'start_task_{t["id"]}'},
            {'text': '⏰ أجل 10د', 'callback_data': f'delay_task_{t["id"]}'}
        ]]}
        ok = tg_send(msg, kb)
        logger.info(f"  │   tg_send={'✅' if ok else '❌'}")
        if ok:
            q('UPDATE tasks SET start_sent=1 WHERE id=?', (t['id'],), fetch=False)

    # 3. إلحاح كل دقيقة لو مبداش - 5 رسائل مختلفة بتتناوب
    # ============================================================
    # 🔔 رسائل إلحاح المهام العادية - عدّل هنا براحتك 🔔
    # ============================================================
    TASK_NAG_MESSAGES = [
        # رسالة 1
        lambda t: (
            f"🔔 يسطا انت فين؟! 🔔\n"
            f"📌 <b>{t['title']}</b> مستنياك!\n"
            f"⏰ المفروض ابتدت {t['scheduled_time'][11:16]}\n"
            f"<i>دوس [بدأت] وإلا هفضل أزعقلك!</i>",
            [{'text': '✅ حاضر بدأت', 'callback_data': f'start_task_{t["id"]}'},
             {'text': '⏰ أجل 10د', 'callback_data': f'delay_task_{t["id"]}'}]
        ),
        # رسالة 2
        lambda t: (
            f"🔔 تاني تذكير 🔔\n"
            f"📌 <b>{t['title']}</b>\n"
            f"🚨 ما بدأتش لسه - إيه اللي بيحصل؟\n"
            f"<i>يلا قوم إنت عارف مفيش عذر!</i>",
            [{'text': '🚀 بدأت دلوقتي', 'callback_data': f'start_task_{t["id"]}'},
             {'text': '⏰ أجل 10د', 'callback_data': f'delay_task_{t["id"]}'}]
        ),
        # رسالة 3
        lambda t: (
            f"🔔 تالت تذكير - بدأ الجد 🔔\n"
            f"📌 <b>{t['title']}</b> لسه فاتح؟!\n"
            f"⏱️ فضلت {t['duration']} دقيقة من وقتك!\n"
            f"<i>مش هسكت غير لو دوست البدء!</i>",
            [{'text': '✅ تمام بدأت', 'callback_data': f'start_task_{t["id"]}'},
             {'text': '⏰ أجل 10د', 'callback_data': f'delay_task_{t["id"]}'}]
        ),
        # رسالة 4
        lambda t: (
            f"🔔 رابع مرة ومش هسكت 🔔\n"
            f"⚠️ <b>{t['title']}</b>\n"
            f"🔥 النظام الصارم بيراقبك يا معلم!\n"
            f"<i>قوم دوس البدء ده - مش وقت تعليق!</i>",
            [{'text': '✅ حاضر يسطا بدأت', 'callback_data': f'start_task_{t["id"]}'},
             {'text': '⏰ أجل 10د', 'callback_data': f'delay_task_{t["id"]}'}]
        ),
        # رسالة 5
        lambda t: (
            f"🔔 آخر إنذار والله 🔔\n"
            f"🚨🚨 <b>{t['title']}</b> 🚨🚨\n"
            f"😤 خمس رسايل وعامل نفسك مش شايف!\n"
            f"<i>ابدأ دلوقتي أو بكرة هتندم!</i>",
            [{'text': '🔥 بدأت يسطا خلاص!', 'callback_data': f'start_task_{t["id"]}'},
             {'text': '⏰ أجل 10د', 'callback_data': f'delay_task_{t["id"]}'}]
        ),
    ]
    # ============================================================

    nag_time = (now - timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')
    tasks = q('''SELECT * FROM tasks WHERE scheduled_time<=? AND is_done=0
                 AND start_sent=1 AND status="Pending"
                 AND (last_nag_time IS NULL OR last_nag_time<=?)''', (now_str, nag_time))
    logger.info(f"  ├─ إلحاح: {len(tasks)} مهمة")
    for t in tasks:
        # نحسب عدد مرات الإلحاح اللي بعتناها عشان نختار الرسالة المناسبة
        nag_count = q("SELECT COUNT(*) as c FROM tasks WHERE id=? AND last_nag_time IS NOT NULL", (t['id'],))
        idx = min((nag_count[0]['c'] if nag_count else 0) % len(TASK_NAG_MESSAGES), len(TASK_NAG_MESSAGES)-1)
        msg_text, kb_buttons = TASK_NAG_MESSAGES[idx](t)
        kb = {'inline_keyboard': [kb_buttons]}
        ok = tg_send(msg_text, kb)
        if ok:
            q('UPDATE tasks SET last_nag_time=? WHERE id=?', (now_str, t['id']), fetch=False)

    # 4. متابعة - خلصت؟
    tasks = q('''SELECT * FROM tasks WHERE status="In Progress" AND is_done=0
                 AND finished=0 AND should_finish_at IS NOT NULL AND should_finish_at<=?''', (now_str,))
    logger.info(f"  ├─ خلصت؟: {len(tasks)} مهمة")
    for t in tasks:
        msg = f"""⏰ <b>خلصت المهمة؟</b>
📌 {t['title']} | ⏱️ {t['duration']}د
<i>المفروض تكون خلصت...</i>"""
        kb = {'inline_keyboard': [
            [{'text': '🎉 خلصت', 'callback_data': f'finish_task_{t["id"]}'},
             {'text': '⏰ لسه 10د', 'callback_data': f'still_working_{t["id"]}'}],
            [{'text': '😴 نمت', 'callback_data': f'wake_up_{t["id"]}'}]
        ]}
        if tg_send(msg, kb):
            q('UPDATE tasks SET last_nag_time=? WHERE id=?', (now_str, t['id']), fetch=False)

    # 5. تنبيهات الكورسات الحرجة
    check_course_deadline_alerts(now)

    # 6. تنبيهات الصلاة
    check_prayer_notifications()
    logger.info("  └─ check_notifications انتهى")

def check_course_deadline_alerts(now):
    """
    ============================================================
    🔔 رسائل إلحاح الكورسات - عدّل هنا براحتك 🔔
    ============================================================
    """
    # 5 رسائل إلحاح لأقل من ساعة (كل 5 دقائق)
    COURSE_HOUR_MESSAGES = [
        lambda c, s: f"🔔 جرس الخطر الأول 🔔\n📚 كورس <b>{c['name']}</b> باقي أقل من ساعة!\n📊 ذاكرت {s['hours_studied']}/{s['total_hours']} ساعة\n⏳ الباقي: {s['time_left_display']}\n<i>يلا ركز دلوقتي!</i>",
        lambda c, s: f"🔔 تاني جرس 🔔\n⚠️ <b>{c['name']}</b> الوقت بيجري!\n📖 الجلسات: {s['done_sessions']}/{s['total_sessions']}\n⏳ {s['time_left_display']} وبعدين خلص!\n<i>كمّل ما توقفش!</i>",
        lambda c, s: f"🔔 تالت جرس - جد جد 🔔\n🚨 <b>{c['name']}</b> يا معلم!\n📅 فات {s['days_elapsed']} يوم من {s['total_days']}\n⏳ الباقي: {s['time_left_display']} بس!\n<i>مش وقت استراحة!</i>",
        lambda c, s: f"🔔 رابع جرس ومش هسكت 🔔\n🔥🔥 <b>{c['name']}</b>\n📊 ذاكرت {s['hours_studied']} ساعة من {s['total_hours']}\n⏳ آخر {s['time_left_display']}!\n<i>قوم كمّل قبل ما يخلص الوقت!</i>",
        lambda c, s: f"🔔 آخر جرس - الوقت بيولع 🔔\n🚨🚨🚨 <b>{c['name']}</b> 🚨🚨🚨\n⏳ {s['time_left_display']} وبعدين انتهى الكورس!\n📖 {s['done_sessions']}/{s['total_sessions']} جلسة\n<i>دلوقتي دلوقتي أو خسرت!</i>",
    ]
    # 5 رسائل إلحاح لأقل من يوم (كل ساعة)
    COURSE_DAY_MESSAGES = [
        lambda c, s: f"🔔 تنبيه الكورس - أول جرس 🔔\n📚 <b>{c['name']}</b> باقي أقل من يوم!\n📊 ذاكرت {s['hours_studied']}/{s['total_hours']} ساعة\n⏳ الباقي: {s['time_left_display']}\n<i>خلي بالك وكمّل!</i>",
        lambda c, s: f"🔔 تاني تنبيه 🔔\n⚠️ <b>{c['name']}</b> - الوقت بيعدي!\n📅 {s['days_elapsed']}/{s['total_days']} يوم عدى\n⏳ {s['time_left_display']} باقيين\n<i>ما تضيعش الفرصة!</i>",
        lambda c, s: f"🔔 تالت تنبيه 🔔\n📚 <b>{c['name']}</b> يا معلم!\n📖 {s['done_sessions']}/{s['total_sessions']} جلسة خلصت\n⏳ {s['time_left_display']} وبعدين!\n<i>كمّل التسلسل!</i>",
        lambda c, s: f"🔔 رابع تنبيه 🔔\n🔥 <b>{c['name']}</b> لسه فاضل!\n📊 {s['hours_studied']} من {s['total_hours']} ساعة اتذاكرت\n⏳ الباقي: {s['time_left_display']}\n<i>يلا خلص ما اتبقاش!</i>",
        lambda c, s: f"🔔 آخر تنبيه قبل الساعة الأخيرة 🔔\n🚨 <b>{c['name']}</b> يا صاحبي!\n⏳ {s['time_left_display']} وبعدين خلص!\n📖 {s['done_sessions']}/{s['total_sessions']} جلسة\n<i>دلوقتي وإلا بكرة هتتأسف!</i>",
    ]

    now_str_hour = now.strftime('%Y-%m-%d %H:00')
    now_str_5min = now.strftime('%Y-%m-%d %H:%M')

    courses = q("SELECT * FROM courses WHERE status='active'")
    for course in courses:
        stats = compute_course_live_stats(course)
        if stats['total_sessions'] == 0 or stats['is_finished']:
            continue

        hours_left = stats['hours_left_num']

        # إلحاح شديد: أقل من ساعة - كل 5 دقائق، 5 رسائل تتناوب
        if 0 < hours_left <= 1:
            last_marker = course['last_final_hour_warning'] or ''
            if last_marker != now_str_5min:
                # نحسب عدد مرات الإرسال بناءً على الـ marker السابق
                count = q("SELECT COUNT(*) as c FROM courses WHERE id=? AND last_final_hour_warning IS NOT NULL", (course['id'],))
                idx = (count[0]['c'] if count else 0) % len(COURSE_HOUR_MESSAGES)
                msg = COURSE_HOUR_MESSAGES[idx](course, stats)
                if tg_send(msg):
                    q("UPDATE courses SET last_final_hour_warning=? WHERE id=?", (now_str_5min, course['id']), fetch=False)

        # تنبيه: أقل من يوم - كل ساعة، 5 رسائل تتناوب
        elif 1 < hours_left <= 24:
            last_marker = course['last_final_warning_date'] or ''
            if last_marker != now_str_hour:
                count = q("SELECT COUNT(*) as c FROM courses WHERE id=? AND last_final_warning_date IS NOT NULL", (course['id'],))
                idx = (count[0]['c'] if count else 0) % len(COURSE_DAY_MESSAGES)
                msg = COURSE_DAY_MESSAGES[idx](course, stats)
                if tg_send(msg):
                    q("UPDATE courses SET last_final_warning_date=? WHERE id=?", (now_str_hour, course['id']), fetch=False)

# ==================== فحص الأزرار ====================
def poll_updates():
    last_id = 0
    while True:
        try:
            url = f"{API}/getUpdates?offset={last_id+1}&timeout=30"
            with urllib.request.urlopen(urllib.request.Request(url), timeout=35) as resp:
                data = json.loads(resp.read())
                if data.get('ok') and data.get('result'):
                    for upd in data['result']:
                        last_id = upd['update_id']
                        if 'callback_query' in upd:
                            process_callback(upd['callback_query']['data'])
        except Exception:
            logger.exception('Telegram update polling failed; retrying shortly')
        time.sleep(2)

# ==================== الصفحة الرئيسية ====================
@app.route('/')
@login_required
def index():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    stats = {
        'total': q('SELECT COUNT(*) as c FROM tasks')[0]['c'],
        'completed': q('SELECT COUNT(*) as c FROM tasks WHERE is_done=1')[0]['c'],
        'pending': q('SELECT COUNT(*) as c FROM tasks WHERE is_done=0')[0]['c'],
        'today': q("SELECT COUNT(*) as c FROM tasks WHERE date(scheduled_time)=?", (today,))[0]['c'],
        'in_progress': q("SELECT COUNT(*) as c FROM tasks WHERE status='In Progress'")[0]['c'],
        'courses': q("SELECT COUNT(*) as c FROM courses WHERE status='active'")[0]['c'],
        'week_completed': q("SELECT COUNT(*) as c FROM tasks WHERE is_done=1 AND scheduled_time>=?", (week_ago,))[0]['c'],
        'high_priority_pending': q("SELECT COUNT(*) as c FROM tasks WHERE is_done=0 AND priority='high'")[0]['c'],
        'prayers_today': q("SELECT COUNT(*) as c FROM prayers WHERE prayer_date=?", (today,))[0]['c'],
        'prayers_confirmed': q("SELECT COUNT(*) as c FROM prayers WHERE prayer_date=? AND confirmed=1", (today,))[0]['c'],
    }
    stats['rate'] = round((stats['completed']/stats['total']*100) if stats['total'] > 0 else 0, 1)

    # إحصائيات الأيام: عدد الأيام اللي فيها على الأقل مهمة واحدة اتخلصت (مش مجرد اليوم)
    days_with_completion = q('''SELECT COUNT(DISTINCT date(scheduled_time)) as c FROM tasks
                               WHERE is_done=1''')[0]['c']
    stats['days_with_completion'] = days_with_completion

    # ترتيب اليوم: غير المنجز أولاً، ثم بالأولوية، ثم بالوقت
    today_tasks_raw = q("SELECT * FROM tasks WHERE date(scheduled_time)=?", (today,))
    today_tasks = sorted(today_tasks_raw, key=lambda t: (t['is_done'], PRIORITY_ORDER.get(t['priority'], 1), t['scheduled_time']))

    all_tasks = q("SELECT * FROM tasks ORDER BY scheduled_time DESC LIMIT 200")
    courses_raw = q("SELECT * FROM courses ORDER BY id DESC")

    # إضافة نسبة التقدم + الإحصائيات اللايف لكل كورس
    courses = []
    for c in courses_raw:
        cd = dict(c)
        cd['progress'] = compute_course_progress(c['id'])
        cd['live'] = compute_course_live_stats(c)
        courses.append(cd)

    categories = sorted({t['category'] for t in all_tasks} | {'عام', 'دراسة', 'عمل', 'شخصي'})

    # مواقيت الصلاة اليوم مرتبة بالوقت
    prayers_today = q("SELECT * FROM prayers WHERE prayer_date=? ORDER BY prayer_time ASC", (today,))

    return render_template_string(HTML, now=now, today=today,
                                stats=stats, today_tasks=today_tasks, all_tasks=all_tasks,
                                courses=courses, city=CITY, chat_id=CHAT_ID,
                                categories=categories, prayers_today=prayers_today)

# ==================== العمليات ====================
@app.route('/add_task', methods=['POST'])
@login_required
def add_task():
    title = request.form.get('title', '').strip()
    if not title:
        return redirect('/')

    scheduled_time = normalize_dt(request.form.get('scheduled_time')) or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    category = request.form.get('category', 'عام')
    priority = request.form.get('priority', 'medium')
    duration = int(request.form.get('duration', 30))

    repeat_type = request.form.get('repeat_type', 'none')  # none / daily / count
    repeat_count = int(request.form.get('repeat_count', 0) or 0)

    if repeat_type == 'none' or repeat_count <= 1:
        q('INSERT INTO tasks (title, category, scheduled_time, priority, duration, repeat_type) VALUES (?,?,?,?,?,?)',
          (title, category, scheduled_time, priority, duration, 'none'), fetch=False)
        tg_send(f"✅ <b>تمت الإضافة:</b> {title}\n⏰ {scheduled_time[:16]}")
    else:
        # مهمة متكررة بعدد مرات محدد - كل نسخة كل 24 ساعة من سابقتها
        base_dt = datetime.strptime(scheduled_time, '%Y-%m-%d %H:%M:%S')
        for i in range(repeat_count):
            occurrence_time = base_dt + timedelta(days=i)
            q('''INSERT INTO tasks (title, category, scheduled_time, priority, duration, repeat_type, repeat_count, repeat_remaining)
                 VALUES (?,?,?,?,?,?,?,?)''',
              (f"{title} ({i+1}/{repeat_count})", category, occurrence_time.strftime('%Y-%m-%d %H:%M:%S'),
               priority, duration, 'count', repeat_count, repeat_count - i - 1), fetch=False)
        tg_send(f"✅ <b>تمت إضافة مهمة متكررة:</b> {title}\n🔁 {repeat_count} مرة، بداية من {scheduled_time[:16]}")

    return redirect('/')

@app.route('/fetch_prayers')
@login_required
def fetch_prayers_manual():
    """جلب يدوي لمواقيت الصلاة من الواجهة"""
    ensure_prayer_times_synced()
    return redirect('/#prayers')

@app.route('/api/task/<int:tid>')
@login_required
def api_get_task(tid):
    """يرجع بيانات مهمة واحدة كـ JSON - يُستخدم لملء حوار التعديل مباشرة من السيرفر"""
    task = q('SELECT * FROM tasks WHERE id=?', (tid,))
    if not task:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(dict(task[0]))

@app.route('/edit_task/<int:tid>', methods=['POST'])
@login_required
def edit_task(tid):
    task = q('SELECT * FROM tasks WHERE id=?', (tid,))
    if not task:
        return redirect('/')

    title = request.form.get('title', '').strip()
    scheduled_time = normalize_dt(request.form.get('scheduled_time'))
    category = request.form.get('category', 'عام')
    priority = request.form.get('priority', 'medium')
    duration = int(request.form.get('duration', 30))

    if title:
        # لو الوقت اتغير، لازم نصفر أعلام الإشعارات عشان النظام يبعتها تاني في الوقت الجديد
        old_time = task[0]['scheduled_time']
        time_changed = scheduled_time and scheduled_time != old_time

        if time_changed:
            q('''UPDATE tasks SET title=?, category=?, scheduled_time=?, priority=?, duration=?,
                 warning_sent=0, start_sent=0, last_nag_time=NULL WHERE id=?''',
              (title, category, scheduled_time, priority, duration, tid), fetch=False)
        else:
            q('UPDATE tasks SET title=?, category=?, priority=?, duration=? WHERE id=?',
              (title, category, priority, duration, tid), fetch=False)

        tg_send(f"✏️ <b>تم تعديل المهمة:</b> {title}")
    return redirect('/')

@app.route('/toggle/<int:tid>', methods=['POST'])
@login_required
def toggle(tid):
    task = q('SELECT * FROM tasks WHERE id=?', (tid,))
    if task:
        t = task[0]
        new = 0 if t['is_done'] else 1
        q('UPDATE tasks SET is_done=?, status=?, finished=? WHERE id=?',
          (new, 'Completed' if new else 'Pending', 1 if new else 0, tid), fetch=False)
        if new:
            tg_send(f"🎉 <b>أحسنت!</b> تم: {t['title']}")
    return redirect('/')

@app.route('/delete/<int:tid>', methods=['POST'])
@login_required
def delete(tid):
    task = q('SELECT title FROM tasks WHERE id=?', (tid,))
    if task:
        q('DELETE FROM tasks WHERE id=?', (tid,), fetch=False)
        tg_send(f"🗑️ <b>تم حذف:</b> {task[0]['title']}")
    return redirect('/')

@app.route('/clear_all/<section>', methods=['POST'])
@login_required
def clear_all(section):
    """
    مسح كل بيانات قسم معين بعد التحقق من الباسورد.
    section: tasks_done | tasks_all | courses | prayers
    """
    entered = request.form.get('confirm_password', '')
    if entered != APP_PASSWORD:
        return jsonify({'status': 'error', 'msg': 'كلمة المرور غلط! ما اتمسح حاجة.'}), 403

    if section == 'tasks_done':
        count = q('SELECT COUNT(*) as c FROM tasks WHERE is_done=1')[0]['c']
        q('DELETE FROM tasks WHERE is_done=1', fetch=False)
        tg_send(f"🗑️ تم مسح {count} مهمة مكتملة")
        return jsonify({'status': 'ok', 'msg': f'تم مسح {count} مهمة مكتملة ✅'})

    elif section == 'tasks_all':
        count = q('SELECT COUNT(*) as c FROM tasks')[0]['c']
        q('DELETE FROM tasks', fetch=False)
        tg_send(f"🗑️ تم مسح كل المهام ({count})")
        return jsonify({'status': 'ok', 'msg': f'تم مسح {count} مهمة ✅'})

    elif section == 'courses':
        count = q('SELECT COUNT(*) as c FROM courses')[0]['c']
        q('DELETE FROM tasks WHERE course_id IS NOT NULL', fetch=False)
        q('DELETE FROM courses', fetch=False)
        tg_send(f"🗑️ تم مسح {count} كورس وجلساتهم")
        return jsonify({'status': 'ok', 'msg': f'تم مسح {count} كورس وكل جلساتهم ✅'})

    elif section == 'prayers':
        count = q('SELECT COUNT(*) as c FROM prayers')[0]['c']
        q('DELETE FROM prayers', fetch=False)
        return jsonify({'status': 'ok', 'msg': f'تم مسح {count} صلاة ✅ (هيتجدد أوتوماتيك)'})

    return jsonify({'status': 'error', 'msg': 'قسم غير معروف'}), 400

@app.route('/add_course', methods=['POST'])
@login_required
def add_course():
    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip()
    if name and code:
        td = int(request.form.get('total_days', 1))
        st = request.form.get('start_time', '09:00')
        sd = request.form.get('start_date', date.today().strftime('%Y-%m-%d'))
        total_hours = float(request.form.get('total_hours', 0))
        daily_hours_raw = request.form.get('daily_hours')
        daily_hours = float(daily_hours_raw) if daily_hours_raw else round(total_hours / td, 2) if td else 0

        # وضع التكرار: everyday (كل يوم) أو specific_days (أيام معينة زي سبت/اتنين/أربع)
        repeat_mode = request.form.get('repeat_mode', 'everyday')
        specific_days = request.form.getlist('specific_days')  # قائمة أرقام أيام الأسبوع 0=الاثنين ... 6=الأحد (Python weekday)
        repeat_days_str = ','.join(specific_days) if repeat_mode == 'specific_days' and specific_days else ''

        cid = q('''INSERT INTO courses (name, code, total_hours, total_days, daily_hours, start_time, start_date, repeat_days)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (name, code, total_hours, td, daily_hours, st, sd, repeat_days_str), fetch=False)

        session_minutes = int(round(daily_hours * 60)) if daily_hours else 60
        start_dt = datetime.strptime(f"{sd} {st}", '%Y-%m-%d %H:%M')

        if repeat_mode == 'specific_days' and specific_days:
            allowed_weekdays = {int(d) for d in specific_days}
            created = 0
            cursor_date = start_dt
            # نمشي يوم بيوم لحد ما نوصل لعدد td جلسة فعلية في الأيام المسموحة
            while created < td:
                if cursor_date.weekday() in allowed_weekdays:
                    q("INSERT INTO tasks (title, category, scheduled_time, priority, task_type, duration, course_id) VALUES (?,?,?,?,?,?,?)",
                      (f"📚 {name} - جلسة {created+1}/{td}", 'دراسة',
                       cursor_date.strftime('%Y-%m-%d %H:%M:%S'), 'high', 'course_session', session_minutes, cid), fetch=False)
                    created += 1
                cursor_date += timedelta(days=1)
        else:
            for day in range(td):
                session_date = start_dt + timedelta(days=day)
                q("INSERT INTO tasks (title, category, scheduled_time, priority, task_type, duration, course_id) VALUES (?,?,?,?,?,?,?)",
                  (f"📚 {name} - جلسة {day+1}/{td}", 'دراسة', session_date.strftime('%Y-%m-%d %H:%M:%S'), 'high', 'course_session', session_minutes, cid), fetch=False)

        tg_send(f"📚 <b>تم إنشاء الكورس:</b> {name}\n📖 {td} جلسة | ⏱️ {total_hours} ساعة إجمالي")
    return redirect('/')

@app.route('/delete_course/<int:cid>', methods=['POST'])
@login_required
def delete_course(cid):
    course = q('SELECT name FROM courses WHERE id=?', (cid,))
    if course:
        q('DELETE FROM tasks WHERE course_id=?', (cid,), fetch=False)
        q('DELETE FROM courses WHERE id=?', (cid,), fetch=False)
        tg_send(f"🗑️ <b>تم حذف الكورس:</b> {course[0]['name']}")
    return redirect('/')

@app.route('/postpone/<int:tid>/<int:minutes>', methods=['POST'])
@login_required
def postpone(tid, minutes):
    """تأجيل مهمة بعدد دقائق مخصص من الواجهة مباشرة"""
    task = q('SELECT * FROM tasks WHERE id=?', (tid,))
    if task:
        t = task[0]
        raw = normalize_dt(t['scheduled_time'])
        old_time = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S')
        new_time = old_time + timedelta(minutes=minutes)
        q('''UPDATE tasks SET scheduled_time=?, warning_sent=0, start_sent=0,
             last_nag_time=NULL WHERE id=?''',
          (new_time.strftime('%Y-%m-%d %H:%M:%S'), tid), fetch=False)
        tg_send(f"⏰ <b>تم تأجيل المهمة يدويًا:</b> {t['title']}\n🕐 {new_time.strftime('%Y-%m-%d %H:%M')}")
    return redirect('/')

@app.route('/duplicate/<int:tid>', methods=['POST'])
@login_required
def duplicate_task(tid):
    """تكرار مهمة موجودة (مفيد للمهام المتكررة)"""
    task = q('SELECT * FROM tasks WHERE id=?', (tid,))
    if task:
        t = task[0]
        q('''INSERT INTO tasks (title, category, scheduled_time, duration, priority)
             VALUES (?,?,?,?,?)''',
          (t['title'], t['category'], t['scheduled_time'], t['duration'], t['priority']), fetch=False)
    return redirect('/')

@app.route('/api/stats')
@login_required
def api_stats():
    """تصدير الإحصائيات كـ JSON (يفيد لأي تكامل خارجي أو تطبيق موبايل مستقبلًا)"""
    total = q('SELECT COUNT(*) as c FROM tasks')[0]['c']
    completed = q('SELECT COUNT(*) as c FROM tasks WHERE is_done=1')[0]['c']
    return jsonify({
        'total': total,
        'completed': completed,
        'pending': total - completed,
        'rate': round((completed/total*100) if total > 0 else 0, 1),
    })

@app.route('/send_test', methods=['POST'])
@login_required
def send_test():
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')

    # 1. فحص قاعدة البيانات
    total_tasks = q('SELECT COUNT(*) as c FROM tasks')[0]['c']
    pending_tasks = q('SELECT COUNT(*) as c FROM tasks WHERE is_done=0 AND status="Pending"')[0]['c']
    sample = q('SELECT id, title, scheduled_time, warning_sent, start_sent, status FROM tasks WHERE is_done=0 ORDER BY scheduled_time ASC LIMIT 3')
    sample_text = '\n'.join([f"  [{t['id']}] {t['title'][:20]} @ {t['scheduled_time'][:16]} w={t['warning_sent']} s={t['start_sent']}" for t in sample]) or '  (لا توجد)'

    # 2. فحص الـ scheduler
    sched_status = "✅ شغال" if _scheduler_started else "❌ مش شغال"

    msg = f"""🔬 <b>تقرير تشخيص النظام</b>
━━━━━━━━━━━━━━━━━━
🕐 الوقت الحالي: <code>{now_str}</code>
⚙️ Scheduler: {sched_status}
📋 إجمالي المهام: {total_tasks}
⏳ معلقة (Pending): {pending_tasks}

<b>أقرب 3 مهام:</b>
{sample_text}
━━━━━━━━━━━━━━━━━━
<i>لو الـ scheduler شغال ومفيش تنبيهات، يبقى المشكلة في صيغة scheduled_time</i>"""
    ok = tg_send(msg, {'inline_keyboard': [[{'text': '✅ استلمت', 'callback_data': 'test_ok'}]]})
    return jsonify({"status": "success" if ok else "error", "scheduler": _scheduler_started, "pending": pending_tasks})

@app.route('/force_check', methods=['POST'])
@login_required
def force_check():
    """تشغيل check_notifications فورًا من المتصفح للتأكد"""
    try:
        check_notifications()
        return jsonify({"status": "ok", "msg": "check_notifications ran - شوف اللوج في التيرمنال"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})

# ==================== HTML ====================
# ==================== HTML ====================
HTML = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚡ نظام المهام الصارم | {{ city }}</title>
    <style>
        :root{
            --bg:#0a0e17;
            --bg2:#0f1523;
            --card:#111827;
            --card2:#161f30;
            --border:#1f2937;
            --neon-p:#00f5ff;
            --neon-p-glow:rgba(0,245,255,.35);
            --neon-s:#39ff88;
            --neon-s-glow:rgba(57,255,136,.35);
            --neon-w:#ffb200;
            --neon-w-glow:rgba(255,178,0,.35);
            --neon-d:#ff3d6e;
            --neon-d-glow:rgba(255,61,110,.35);
            --neon-i:#b967ff;
            --neon-i-glow:rgba(185,103,255,.35);
            --text:#e8ecf5;
            --text-dim:#8993a8;
        }
        *{margin:0;padding:0;box-sizing:border-box}
        body{
            font-family:'Segoe UI',Tahoma,Arial,sans-serif;
            background:
                radial-gradient(circle at 15% 10%, rgba(0,245,255,.06), transparent 40%),
                radial-gradient(circle at 85% 20%, rgba(185,103,255,.06), transparent 40%),
                radial-gradient(circle at 50% 90%, rgba(57,255,136,.05), transparent 45%),
                var(--bg);
            color:var(--text);
            min-height:100vh;
        }
        ::-webkit-scrollbar{width:8px;height:8px}
        ::-webkit-scrollbar-track{background:var(--bg2)}
        ::-webkit-scrollbar-thumb{background:var(--border);border-radius:10px}
        ::-webkit-scrollbar-thumb:hover{background:var(--neon-p)}

        /* ===== Header ===== */
        .h{
            background:linear-gradient(135deg, rgba(15,21,35,.95), rgba(10,14,23,.95));
            backdrop-filter:blur(10px);
            border-bottom:1px solid var(--border);
            padding:16px 30px;
            position:sticky;top:0;z-index:100;
            box-shadow:0 4px 30px rgba(0,0,0,.4);
        }
        .hc{max-width:1500px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:15px}
        .h h1{
            font-size:1.4rem;font-weight:800;
            background:linear-gradient(90deg,var(--neon-p),var(--neon-i));
            -webkit-background-clip:text;background-clip:text;color:transparent;
            display:flex;align-items:center;gap:10px;
        }
        .bot{
            background:rgba(57,255,136,.08);
            border:1px solid rgba(57,255,136,.3);
            color:var(--neon-s);
            padding:7px 16px;border-radius:20px;font-size:.85rem;
            display:flex;align-items:center;gap:8px;font-weight:600;
        }
        .dot{width:9px;height:9px;border-radius:50%;background:var(--neon-s);box-shadow:0 0 8px var(--neon-s);animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

        .main{max-width:1500px;margin:24px auto;padding:0 20px}

        /* ===== Stat Cards ===== */
        .sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:22px}
        .sc{
            background:linear-gradient(145deg,var(--card),var(--card2));
            padding:18px 20px;border-radius:14px;
            border:1px solid var(--border);
            cursor:pointer;transition:all .25s ease;
            position:relative;overflow:hidden;
        }
        .sc::before{
            content:'';position:absolute;inset:0;
            background:linear-gradient(90deg,transparent,var(--neon-p-glow),transparent);
            transform:translateX(-100%);transition:transform .6s;
        }
        .sc:hover::before{transform:translateX(100%)}
        .sc:hover{transform:translateY(-4px);border-color:var(--neon-p);box-shadow:0 8px 25px var(--neon-p-glow)}
        .sc.s:hover{border-color:var(--neon-s);box-shadow:0 8px 25px var(--neon-s-glow)}
        .sc.w:hover{border-color:var(--neon-w);box-shadow:0 8px 25px var(--neon-w-glow)}
        .sc.d:hover{border-color:var(--neon-d);box-shadow:0 8px 25px var(--neon-d-glow)}
        .sc.i:hover{border-color:var(--neon-i);box-shadow:0 8px 25px var(--neon-i-glow)}
        .sv{font-size:1.9rem;font-weight:800;color:var(--neon-p)}
        .sc.s .sv{color:var(--neon-s)}.sc.w .sv{color:var(--neon-w)}.sc.d .sv{color:var(--neon-d)}.sc.i .sv{color:var(--neon-i)}
        .sl{color:var(--text-dim);font-size:.8rem;margin-top:4px;font-weight:600}

        /* ===== Tabs ===== */
        .tabs{display:flex;gap:4px;background:var(--card);border-radius:14px 14px 0 0;border:1px solid var(--border);border-bottom:none;overflow-x:auto;padding:6px 6px 0}
        .tb{
            padding:12px 22px;border:none;background:transparent;cursor:pointer;
            font-weight:700;color:var(--text-dim);border-radius:10px 10px 0 0;
            white-space:nowrap;transition:all .25s;font-size:.9rem;
        }
        .tb:hover{color:var(--text);background:rgba(255,255,255,.03)}
        .tb.active{color:var(--neon-p);background:var(--bg);box-shadow:inset 0 2px 0 var(--neon-p)}

        .cont{
            background:var(--card);border:1px solid var(--border);
            border-radius:0 0 14px 14px;padding:26px;min-height:500px;
        }
        .tp{display:none}.tp.active{display:block;animation:fadein .3s ease}
        @keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}

        /* ===== Task Cards ===== */
        .task{
            border:1px solid var(--border);border-radius:12px;padding:16px 18px;
            margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;
            gap:12px;flex-wrap:wrap;
            background:rgba(255,255,255,.015);
            transition:all .25s;
        }
        .task:hover{border-color:var(--neon-p);box-shadow:0 0 0 1px var(--neon-p-glow), 0 4px 20px var(--neon-p-glow)}
        .task.done{opacity:.5;background:rgba(57,255,136,.03)}
        .task.progress{background:rgba(255,178,0,.05);border-color:rgba(255,178,0,.4)}
        .task.done .tt{text-decoration:line-through}
        .tt{font-weight:700;margin-bottom:6px;font-size:1rem}
        .tm{display:flex;gap:8px;flex-wrap:wrap;font-size:.82rem;color:var(--text-dim);align-items:center}

        .badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:.75rem;font-weight:700;border:1px solid transparent}
        .bp{background:rgba(0,245,255,.1);color:var(--neon-p);border-color:rgba(0,245,255,.3)}
        .bs{background:rgba(57,255,136,.1);color:var(--neon-s);border-color:rgba(57,255,136,.3)}
        .bw{background:rgba(255,178,0,.1);color:var(--neon-w);border-color:rgba(255,178,0,.3)}
        .bd{background:rgba(255,61,110,.1);color:var(--neon-d);border-color:rgba(255,61,110,.3)}
        .bi{background:rgba(185,103,255,.1);color:var(--neon-i);border-color:rgba(185,103,255,.3)}

        .btn{
            padding:9px 18px;border:1px solid transparent;border-radius:8px;cursor:pointer;
            font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:6px;
            font-size:.85rem;transition:all .2s;background:transparent;
        }
        .btn-p{background:rgba(0,245,255,.12);color:var(--neon-p);border-color:rgba(0,245,255,.4)}
        .btn-p:hover{background:var(--neon-p);color:#03181c;box-shadow:0 0 18px var(--neon-p-glow)}
        .btn-s{background:rgba(57,255,136,.12);color:var(--neon-s);border-color:rgba(57,255,136,.4)}
        .btn-s:hover{background:var(--neon-s);color:#03230f;box-shadow:0 0 18px var(--neon-s-glow)}
        .btn-d{background:rgba(255,61,110,.12);color:var(--neon-d);border-color:rgba(255,61,110,.4)}
        .btn-d:hover{background:var(--neon-d);color:#2a0410;box-shadow:0 0 18px var(--neon-d-glow)}
        .btn-w{background:rgba(255,178,0,.12);color:var(--neon-w);border-color:rgba(255,178,0,.4)}
        .btn-w:hover{background:var(--neon-w);color:#2a1a00;box-shadow:0 0 18px var(--neon-w-glow)}
        .btn-sm{padding:6px 12px;font-size:.78rem}
        .btn-lg{padding:14px 32px;font-size:1.05rem}

        /* ===== Filters ===== */
        .filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:18px}
        .filters select, .filters input{
            background:var(--card2);border:1px solid var(--border);color:var(--text);
            padding:9px 14px;border-radius:8px;font-size:.85rem;
        }
        .filters select:focus, .filters input:focus{outline:none;border-color:var(--neon-p)}

        /* ===== Progress bar (courses) ===== */
        .pbar{width:100%;height:8px;background:var(--bg2);border-radius:10px;overflow:hidden;margin-top:8px;border:1px solid var(--border)}
        .pbar-fill{height:100%;background:linear-gradient(90deg,var(--neon-s),var(--neon-p));border-radius:10px;transition:width .4s}

        /* ===== Forms ===== */
        .fg{margin-bottom:16px}
        .fg label{display:block;margin-bottom:6px;font-weight:700;font-size:.85rem;color:var(--text-dim)}
        .fc{
            width:100%;padding:11px 14px;border:1px solid var(--border);border-radius:8px;
            font-size:.9rem;background:var(--bg2);color:var(--text);
        }
        .fc:focus{border-color:var(--neon-p);outline:none;box-shadow:0 0 0 3px var(--neon-p-glow)}
        .fr{display:grid;grid-template-columns:1fr 1fr;gap:14px}

        /* ===== Modal ===== */
        .modal{display:none;position:fixed;inset:0;background:rgba(3,5,10,.75);backdrop-filter:blur(4px);z-index:1000;align-items:center;justify-content:center}
        .modal.show{display:flex}
        .mc{
            background:linear-gradient(160deg,var(--card),var(--bg2));
            border:1px solid var(--border);
            padding:30px;border-radius:16px;width:92%;max-width:650px;max-height:85vh;overflow-y:auto;
            animation:si .3s;box-shadow:0 20px 60px rgba(0,0,0,.6);
        }
        @keyframes si{from{transform:translateY(-30px);opacity:0}to{transform:translateY(0);opacity:1}}

        .alert{padding:14px 18px;border-radius:10px;margin-bottom:14px;font-size:.88rem;border:1px solid transparent}
        .as{background:rgba(57,255,136,.08);color:var(--neon-s);border-color:rgba(57,255,136,.3)}
        .ai{background:rgba(0,245,255,.08);color:var(--neon-p);border-color:rgba(0,245,255,.3)}

        .empty{text-align:center;padding:60px 20px;color:var(--text-dim)}
        .empty span{font-size:3.5rem;display:block;margin-bottom:14px;opacity:.5}

        .fb{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
        .mb20{margin-bottom:20px}.mt20{margin-top:20px}.tc{text-align:center}

        .course-card{
            border:1px solid var(--border);border-radius:14px;padding:20px;
            background:linear-gradient(145deg,var(--card),var(--card2));
            transition:all .25s;
        }
        .course-card:hover{border-color:var(--neon-i);box-shadow:0 8px 25px var(--neon-i-glow)}

        @media(max-width:768px){
            .fr{grid-template-columns:1fr}
            .h h1{font-size:1.1rem}
            .sg{grid-template-columns:repeat(2,1fr)}
            .task{flex-direction:column;align-items:stretch}
            .task > div[style*="display:flex"]{justify-content:flex-end}
        }
    </style>
</head>
<body>
    <header class="h">
        <div class="hc">
            <h1>⚡ نظام المهام الصارم | {{ city }}</h1>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
                <div class="bot"><span class="dot"></span>🤖 متصل</div>
                <button class="btn btn-p" onclick="openModal('taskModal')">➕ مهمة</button>
                <button class="btn btn-s" onclick="openModal('courseModal')">📚 كورس</button>
                <form method="POST" action="/logout" style="display:inline"><button type="submit" class="btn btn-d" title="خروج" aria-label="تسجيل الخروج">🚪</button></form>
            </div>
        </div>
    </header>

    <div class="main">
        <div class="sg">
            <div class="sc" onclick="switchTabByName('today')"><div class="sv">{{ stats.today }}</div><div class="sl">📅 مهام اليوم</div></div>
            <div class="sc s"><div class="sv">{{ stats.rate }}%</div><div class="sl">✅ نسبة الإنجاز</div></div>
            <div class="sc w"><div class="sv">{{ stats.pending }}</div><div class="sl">⏳ مهام معلقة</div></div>
            <div class="sc i"><div class="sv">{{ stats.in_progress }}</div><div class="sl">🔄 جاري التنفيذ</div></div>
            <div class="sc d"><div class="sv">{{ stats.high_priority_pending }}</div><div class="sl">🔥 عالية الأولوية</div></div>
            <div class="sc s"><div class="sv">{{ stats.week_completed }}</div><div class="sl">🏆 مكتملة هذا الأسبوع</div></div>
        </div>

        <div class="tabs">
            <button class="tb active" data-tab="today" onclick="switchTab(this,'today')">📅 اليوم</button>
            <button class="tb" data-tab="all" onclick="switchTab(this,'all')">📋 المهام</button>
            <button class="tb" data-tab="progress" onclick="switchTab(this,'progress')">🔄 جاري</button>
            <button class="tb" data-tab="courses" onclick="switchTab(this,'courses')">📚 الكورسات</button>
            <button class="tb" data-tab="prayers" onclick="switchTab(this,'prayers')">🕌 الصلاة</button>
            <button class="tb" data-tab="statistics" onclick="switchTab(this,'statistics')">📊 إحصائيات</button>
            <button class="tb" data-tab="settings" onclick="switchTab(this,'settings')">⚙️ الإعدادات</button>
        </div>

        <div class="cont">
            <div id="panel-today" class="tp active">
                <div class="fb mb20">
                    <h3>📅 مهام اليوم</h3>
                    <div style="display:flex;gap:8px;align-items:center">
                        <span class="badge bp">{{ today_tasks|length }} مهمة</span>
                        <button class="btn btn-sm btn-d" onclick="openClearModal('tasks_done','المهام المكتملة')">🗑️ مسح المكتملة</button>
                    </div>
                </div>
                {% for task in today_tasks %}
                <div class="task {{ 'done' if task.is_done else 'progress' if task.status=='In Progress' else '' }}">
                    <div style="flex:1;min-width:200px">
                        <div class="tt">{{ task.title }}</div>
                        <div class="tm">
                            <span class="badge bp">{{ task.category }}</span>
                            <span class="badge {{ 'bd' if task.priority=='high' else 'bw' if task.priority=='medium' else 'bs' }}">{{ task.priority }}</span>
                            <span class="badge {{ 'bi' if task.status=='In Progress' else 'bs' if task.is_done else 'bp' }}">{{ task.status }}</span>
                            <span>⏰ {{ task.scheduled_time[11:16] }}</span>
                            <span>⏱️ {{ task.duration }}د</span>
                        </div>
                    </div>
                    <div style="display:flex;gap:6px;flex-wrap:wrap">
                        <a href="#" class="btn btn-sm btn-p" title="تعديل" onclick="openEditModal({{ task.id }});return false;">✏️</a>
                        <form method="POST" action="/postpone/{{ task.id }}/10" style="display:inline"><button class="btn btn-sm btn-w" title="أجل 10 دقائق" type="submit">⏰ +10د</button></form>
                        <form method="POST" action="/duplicate/{{ task.id }}" style="display:inline"><button class="btn btn-sm btn-p" title="تكرار المهمة" type="submit">📑</button></form>
                        <form method="POST" action="/toggle/{{ task.id }}" style="display:inline"><button class="btn btn-sm {{ 'btn-s' if not task.is_done else 'btn-w' }}" type="submit">{{ '✅' if not task.is_done else '↩️' }}</button></form>
                        <form method="POST" action="/delete/{{ task.id }}" style="display:inline" onsubmit="return confirm('حذف؟')"><button class="btn btn-sm btn-d" type="submit">🗑️</button></form>
                    </div>
                </div>
                {% else %}
                <div class="empty"><span>📭</span><h3>لا توجد مهام اليوم</h3></div>
                {% endfor %}
            </div>

            <div id="panel-all" class="tp">
                <div class="fb mb20">
                    <h3>📋 جميع المهام</h3>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-sm btn-w" onclick="openClearModal('tasks_done','المهام المكتملة فقط')">🗑️ مسح المكتملة</button>
                        <button class="btn btn-sm btn-d" onclick="openClearModal('tasks_all','كل المهام نهائياً')">💣 مسح الكل</button>
                    </div>
                </div>
                <div class="filters">
                    <input type="text" class="fc" placeholder="🔍 بحث بالعنوان..." onkeyup="filterTasks()" id="si" style="max-width:260px">
                    <select id="fCategory" class="fc" onchange="filterTasks()" style="max-width:160px">
                        <option value="">كل التصنيفات</option>
                        {% for cat in categories %}<option value="{{ cat }}">{{ cat }}</option>{% endfor %}
                    </select>
                    <select id="fPriority" class="fc" onchange="filterTasks()" style="max-width:160px">
                        <option value="">كل الأولويات</option>
                        <option value="high">عالية</option>
                        <option value="medium">متوسطة</option>
                        <option value="low">منخفضة</option>
                    </select>
                    <select id="fStatus" class="fc" onchange="filterTasks()" style="max-width:160px">
                        <option value="">كل الحالات</option>
                        <option value="Pending">معلقة</option>
                        <option value="In Progress">جارية</option>
                        <option value="Completed">مكتملة</option>
                    </select>
                </div>
                <div id="atc">
                    {% for task in all_tasks %}
                    <div class="task {{ 'done' if task.is_done else 'progress' if task.status=='In Progress' else '' }}"
                         data-category="{{ task.category }}" data-priority="{{ task.priority }}" data-status="{{ task.status }}">
                        <div style="flex:1;min-width:200px">
                            <div class="tt">{{ task.title }}</div>
                            <div class="tm">
                                <span class="badge bp">{{ task.category }}</span>
                                <span class="badge {{ 'bd' if task.priority=='high' else 'bw' if task.priority=='medium' else 'bs' }}">{{ task.priority }}</span>
                                <span class="badge {{ 'bi' if task.status=='In Progress' else 'bs' if task.is_done else 'bp' }}">{{ task.status }}</span>
                                <span>⏰ {{ task.scheduled_time[:16] }}</span>
                                <span>⏱️ {{ task.duration }}د</span>
                            </div>
                        </div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap">
                            <a href="#" class="btn btn-sm btn-p" title="تعديل" onclick="openEditModal({{ task.id }});return false;">✏️</a>
                            <form method="POST" action="/duplicate/{{ task.id }}" style="display:inline"><button class="btn btn-sm btn-p" title="تكرار المهمة" type="submit">📑</button></form>
                            <form method="POST" action="/toggle/{{ task.id }}" style="display:inline"><button class="btn btn-sm {{ 'btn-s' if not task.is_done else 'btn-w' }}" type="submit">{{ '✅' if not task.is_done else '↩️' }}</button></form>
                            <form method="POST" action="/delete/{{ task.id }}" style="display:inline" onsubmit="return confirm('حذف؟')"><button class="btn btn-sm btn-d" type="submit">🗑️</button></form>
                        </div>
                    </div>
                    {% else %}
                    <div class="empty"><span>📭</span><h3>لا توجد مهام</h3></div>
                    {% endfor %}
                </div>
            </div>

            <div id="panel-progress" class="tp">
                <h3 class="mb20">🔄 مهام جاري تنفيذها</h3>
                {% set progress_tasks = all_tasks|selectattr('status','equalto','In Progress')|list %}
                {% if progress_tasks %}
                    {% for task in progress_tasks %}
                    <div class="task progress">
                        <div style="flex:1;min-width:200px">
                            <div class="tt">{{ task.title }}</div>
                            <div class="tm">
                                <span class="badge bp">{{ task.category }}</span>
                                <span>⏱️ {{ task.duration }}د</span>
                                <span>🚀 بدأت: {{ task.started_at[:16] if task.started_at else '؟' }}</span>
                                <span>⏰ تخلص: {{ task.should_finish_at[:16] if task.should_finish_at else '؟' }}</span>
                            </div>
                        </div>
                        <form method="POST" action="/toggle/{{ task.id }}" style="display:inline"><button class="btn btn-sm btn-s" type="submit">✅ خلصت</button></form>
                    </div>
                    {% endfor %}
                {% else %}
                <div class="empty"><span>🔄</span><h3>لا توجد مهام جارية</h3></div>
                {% endif %}
            </div>

            <div id="panel-courses" class="tp">
                <div class="fb mb20">
                    <h3>📚 الكورسات</h3>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-s" onclick="openModal('courseModal')">➕ كورس</button>
                        <button class="btn btn-sm btn-d" onclick="openClearModal('courses','كل الكورسات وجلساتهم')">🗑️ مسح الكل</button>
                    </div>
                </div>
                {% if courses %}
                <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:18px">
                    {% for course in courses %}
                    <div class="course-card">
                        <div class="fb"><h4>{{ course.name }}</h4><span class="badge {{ 'bd' if course.live.hours_left_num <= 24 and course.live.hours_left_num > 0 else 'bs' }}">{{ 'قرب الخلاص!' if course.live.hours_left_num <= 24 and course.live.hours_left_num > 0 else 'نشط' }}</span></div>
                        <p style="color:var(--text-dim);font-size:.85rem;margin-top:6px">{{ course.code }}</p>
                        <div class="pbar"><div class="pbar-fill" style="width:{{ course.progress }}%"></div></div>
                        <div class="fb" style="margin-top:10px">
                            <small style="color:var(--text-dim)">📅 {{ course.total_days }} يوم | 📚 {{ course.total_hours }} ساعة | 🎯 {{ course.progress }}%</small>
                            <form method="POST" action="/delete_course/{{ course.id }}" style="display:inline" onsubmit="return confirm('حذف الكورس وكل جلساته؟')"><button class="btn btn-sm btn-d" type="submit">🗑️</button></form>
                        </div>
                        <div class="alert ai" style="margin-top:12px;margin-bottom:0;font-size:.8rem;line-height:1.9">
                            📅 <b>اتعدى:</b> {{ course.live.days_elapsed }}/{{ course.live.total_days }} يوم<br>
                            📚 <b>اتذاكر:</b> {{ course.live.hours_studied }}/{{ course.live.total_hours }} ساعة<br>
                            📖 <b>جلسات:</b> {{ course.live.done_sessions }}/{{ course.live.total_sessions }}<br>
                            ⏳ <b>باقي على النهاية:</b> {{ course.live.time_left_display }}
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <div class="empty"><span>📚</span><h3>لا توجد كورسات</h3></div>
                {% endif %}
            </div>

            <div id="panel-prayers" class="tp">
                <div class="fb mb20">
                    <h3>🕌 مواقيت الصلاة - {{ city }}</h3>
                    <div style="display:flex;gap:8px;align-items:center">
                        <span class="badge bs">{{ stats.prayers_confirmed }}/{{ stats.prayers_today }} ✅</span>
                        <button class="btn btn-sm btn-d" onclick="openClearModal('prayers','سجل الصلوات')">🗑️ مسح السجل</button>
                    </div>
                </div>
                {% if prayers_today %}
                <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px">
                    {% for p in prayers_today %}
                    {% set name_ar = {'Fajr':'الفجر','Dhuhr':'الظهر','Asr':'العصر','Maghrib':'المغرب','Isha':'العشاء'} %}
                    <div class="course-card" style="text-align:center;border-color:{{ 'rgba(57,255,136,.5)' if p.confirmed else 'var(--border)' }}">
                        <div style="font-size:2rem;margin-bottom:6px">{{ {'Fajr':'🌅','Dhuhr':'☀️','Asr':'🌤️','Maghrib':'🌇','Isha':'🌙'}[p.prayer_name] }}</div>
                        <h4 style="color:{{ 'var(--neon-s)' if p.confirmed else 'var(--neon-p)' }}">{{ name_ar[p.prayer_name] }}</h4>
                        <p style="font-size:1.4rem;font-weight:800;margin:8px 0;color:var(--text)">{{ p.prayer_time[11:16] }}</p>
                        {% if p.confirmed %}
                            <span class="badge bs">✅ صليت</span>
                        {% elif p.start_sent %}
                            <span class="badge bw">⏳ منتظر تأكيد</span>
                        {% else %}
                            <span class="badge bp">🕐 لسه</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <div class="empty">
                    <span>🕌</span>
                    <h3>لم يتم جلب مواقيت الصلاة بعد</h3>
                    <p style="color:var(--text-dim);margin-top:10px">سيتم الجلب تلقائيًا عند اتصال السيرفر بالإنترنت</p>
                    <a href="/fetch_prayers" class="btn btn-p mt20" style="margin-top:20px">🔄 جلب يدوي</a>
                </div>
                {% endif %}
            </div>

            <div id="panel-statistics" class="tp">
                <h3 class="mb20">📊 إحصائيات النظام</h3>
                <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px">
                    <div class="sc" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.total }}</div>
                        <div class="sl">📋 إجمالي المهام</div>
                    </div>
                    <div class="sc s" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.completed }}</div>
                        <div class="sl">✅ مهام مكتملة</div>
                    </div>
                    <div class="sc w" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.pending }}</div>
                        <div class="sl">⏳ مهام معلقة</div>
                    </div>
                    <div class="sc i" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.rate }}%</div>
                        <div class="sl">🎯 نسبة الإنجاز الكلية</div>
                    </div>
                    <div class="sc d" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.high_priority_pending }}</div>
                        <div class="sl">🔥 عالية الأولوية معلقة</div>
                    </div>
                    <div class="sc s" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.week_completed }}</div>
                        <div class="sl">🏆 مكتملة هذا الأسبوع</div>
                    </div>
                    <div class="sc" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.days_with_completion }}</div>
                        <div class="sl">📅 أيام أنجزت فيها مهام</div>
                    </div>
                    <div class="sc i" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.courses }}</div>
                        <div class="sl">📚 كورسات نشطة</div>
                    </div>
                    <div class="sc s" style="cursor:default">
                        <div class="sv" style="font-size:2.5rem">{{ stats.prayers_confirmed }}/{{ stats.prayers_today }}</div>
                        <div class="sl">🕌 صلوات اليوم</div>
                    </div>
                </div>

                <h4 style="margin:28px 0 14px;color:var(--neon-p)">📚 تفصيل الكورسات</h4>
                {% if courses %}
                <div style="overflow-x:auto">
                    <table style="width:100%;border-collapse:collapse;font-size:.88rem">
                        <thead>
                            <tr style="border-bottom:1px solid var(--border);color:var(--text-dim)">
                                <th style="padding:10px;text-align:right">الكورس</th>
                                <th style="padding:10px">الأيام</th>
                                <th style="padding:10px">الساعات</th>
                                <th style="padding:10px">الجلسات</th>
                                <th style="padding:10px">التقدم</th>
                                <th style="padding:10px">الباقي</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for course in courses %}
                            <tr style="border-bottom:1px solid var(--border)">
                                <td style="padding:10px;font-weight:700">{{ course.name }}</td>
                                <td style="padding:10px;text-align:center">{{ course.live.days_elapsed }}/{{ course.live.total_days }}</td>
                                <td style="padding:10px;text-align:center">{{ course.live.hours_studied }}/{{ course.live.total_hours }}</td>
                                <td style="padding:10px;text-align:center">{{ course.live.done_sessions }}/{{ course.live.total_sessions }}</td>
                                <td style="padding:10px;text-align:center">
                                    <div class="pbar"><div class="pbar-fill" style="width:{{ course.progress }}%"></div></div>
                                    <small>{{ course.progress }}%</small>
                                </td>
                                <td style="padding:10px;text-align:center;color:{{ 'var(--neon-d)' if course.live.hours_left_num <= 24 and course.live.hours_left_num > 0 else 'var(--text-dim)' }}">{{ course.live.time_left_display }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="empty"><span>📚</span><p>لا توجد كورسات بعد</p></div>
                {% endif %}
            </div>

            <div id="panel-settings" class="tp">
                <h3 class="mb20">⚙️ إعدادات النظام الصارم</h3>
                <div style="max-width:520px;margin:0 auto">
                    <div class="alert {{ 'as' if chat_id else 'ai' }}">
                        {% if chat_id %}✅ البوت متصل | Chat ID: {{ chat_id }}{% else %}⚠️ لم يتم ضبط BOT_TOKEN / CHAT_ID كمتغيرات بيئة{% endif %}
                    </div>
                    <div class="alert ai">
                        <strong>📝 النظام الصارم:</strong>
                        <ul style="margin:10px 20px;line-height:2">
                            <li>⚠️ قبل المهمة بـ 5د: تنبيه + أزرار</li>
                            <li>🚀 وقت المهمة: "يلا ابدأ" + [بدأت]</li>
                            <li>🔔 إلحاح كل دقيقة لو مبداش</li>
                            <li>⏱️ يحسب المدة بعد [بدأت]</li>
                            <li>⏰ بعد المدة: "خلصت؟" + [خلصت] [لسه] [نمت]</li>
                            <li>📍 {{ city }} - الشرقية</li>
                        </ul>
                    </div>
                    <div class="tc mt20" style="display:flex;gap:12px;flex-wrap:wrap;justify-content:center">
                        <button class="btn btn-p btn-lg" onclick="testBot()">🔬 تشخيص شامل</button>
                        <button class="btn btn-w btn-lg" onclick="forceCheck()">⚡ فحص فوري</button>
                        <a href="/fetch_prayers" class="btn btn-s btn-lg">🕌 جلب مواقيت الصلاة</a>
                    </div>
                    <p id="tr" class="mt20"></p>
                </div>
            </div>
        </div>
    </div>

    <div id="taskModal" class="modal">
        <div class="mc">
            <div class="fb mb20"><h3>➕ مهمة جديدة</h3><button class="btn btn-sm btn-d" onclick="closeModal('taskModal')">✕</button></div>
            <form action="/add_task" method="POST">
                <div class="fg"><label>العنوان *</label><input type="text" name="title" class="fc" required autofocus></div>
                <div class="fr">
                    <div class="fg"><label>التصنيف</label>
                        <select name="category" class="fc">
                            {% for cat in categories %}<option>{{ cat }}</option>{% endfor %}
                        </select>
                    </div>
                    <div class="fg"><label>الأولوية</label><select name="priority" class="fc"><option value="medium">متوسطة</option><option value="high">عالية</option><option value="low">منخفضة</option></select></div>
                </div>
                <div class="fr">
                    <div class="fg"><label>التاريخ والوقت</label><input type="datetime-local" name="scheduled_time" class="fc" value="{{ now.strftime('%Y-%m-%dT%H:%M') }}"></div>
                    <div class="fg"><label>⏱️ المدة (بالدقائق)</label><input type="number" name="duration" class="fc" value="30" min="1"></div>
                </div>
                <div class="fr">
                    <div class="fg"><label>🔁 تكرار</label>
                        <select name="repeat_type" class="fc" id="taskRepeatType" onchange="toggleRepeatCount()">
                            <option value="none">بدون تكرار</option>
                            <option value="count">تكرار عدد مرات محدد</option>
                        </select>
                    </div>
                    <div class="fg" id="repeatCountWrap" style="display:none">
                        <label>عدد المرات</label>
                        <input type="number" name="repeat_count" class="fc" value="2" min="2">
                    </div>
                </div>
                <button type="submit" class="btn btn-p" style="width:100%;justify-content:center">💾 حفظ</button>
            </form>
        </div>
    </div>

    <div id="editTaskModal" class="modal">
        <div class="mc">
            <div class="fb mb20"><h3>✏️ تعديل المهمة</h3><button class="btn btn-sm btn-d" onclick="closeModal('editTaskModal')">✕</button></div>
            <form id="editTaskForm" method="POST">
                <div class="fg"><label>العنوان *</label><input type="text" name="title" id="editTitle" class="fc" required></div>
                <div class="fr">
                    <div class="fg"><label>التصنيف</label>
                        <select name="category" id="editCategory" class="fc">
                            {% for cat in categories %}<option>{{ cat }}</option>{% endfor %}
                        </select>
                    </div>
                    <div class="fg"><label>الأولوية</label>
                        <select name="priority" id="editPriority" class="fc">
                            <option value="medium">متوسطة</option><option value="high">عالية</option><option value="low">منخفضة</option>
                        </select>
                    </div>
                </div>
                <div class="fr">
                    <div class="fg"><label>التاريخ والوقت</label><input type="datetime-local" name="scheduled_time" id="editScheduledTime" class="fc"></div>
                    <div class="fg"><label>⏱️ المدة (بالدقائق)</label><input type="number" name="duration" id="editDuration" class="fc" min="1"></div>
                </div>
                <button type="submit" class="btn btn-p" style="width:100%;justify-content:center">💾 حفظ التعديل</button>
            </form>
        </div>
    </div>

    <div id="courseModal" class="modal">
        <div class="mc">
            <div class="fb mb20"><h3>📚 كورس جديد</h3><button class="btn btn-sm btn-d" onclick="closeModal('courseModal')">✕</button></div>
            <form action="/add_course" method="POST">
                <div class="fr">
                    <div class="fg"><label>الاسم *</label><input type="text" name="name" class="fc" required></div>
                    <div class="fg"><label>الكود *</label><input type="text" name="code" class="fc" required></div>
                </div>
                <div class="fr">
                    <div class="fg"><label>الساعات *</label><input type="number" name="total_hours" class="fc" step="0.5" required></div>
                    <div class="fg"><label>الأيام *</label><input type="number" name="total_days" class="fc" required></div>
                    <div class="fg"><label>يومياً</label><input type="number" name="daily_hours" class="fc" step="0.5" placeholder="تلقائي"></div>
                </div>
                <div class="fr">
                    <div class="fg"><label>وقت البدء *</label><input type="time" name="start_time" class="fc" value="09:00" required></div>
                    <div class="fg"><label>تاريخ البدء *</label><input type="date" name="start_date" class="fc" value="{{ today }}" required></div>
                </div>
                <div class="fg">
                    <label>🗓️ نمط تكرار الجلسات</label>
                    <select name="repeat_mode" class="fc" id="courseRepeatMode" onchange="toggleSpecificDays()">
                        <option value="everyday">كل يوم</option>
                        <option value="specific_days">أيام معينة في الأسبوع</option>
                    </select>
                </div>
                <div class="fg" id="specificDaysWrap" style="display:none">
                    <label>اختر الأيام</label>
                    <div style="display:flex;gap:8px;flex-wrap:wrap">
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="0"> الاثنين</label>
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="1"> الثلاثاء</label>
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="2"> الأربعاء</label>
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="3"> الخميس</label>
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="4"> الجمعة</label>
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="5"> السبت</label>
                        <label style="display:flex;align-items:center;gap:4px;font-weight:normal"><input type="checkbox" name="specific_days" value="6"> الأحد</label>
                    </div>
                </div>
                <button type="submit" class="btn btn-s" style="width:100%;justify-content:center">📚 إنشاء الكورس والجلسات</button>
            </form>
        </div>
    </div>

    <script>
        function switchTab(btn, name){
            document.querySelectorAll('.tb').forEach(b=>b.classList.remove('active'));
            document.querySelectorAll('.tp').forEach(p=>p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('panel-'+name).classList.add('active');
        }
        function switchTabByName(name){
            const btn = document.querySelector('.tb[data-tab="'+name+'"]');
            if(btn) switchTab(btn, name);
        }
        function openModal(id){document.getElementById(id).classList.add('show')}
        function closeModal(id){document.getElementById(id).classList.remove('show')}
        window.onclick=function(e){if(e.target.classList.contains('modal'))e.target.classList.remove('show')}

        function toggleRepeatCount(){
            var v = document.getElementById('taskRepeatType').value;
            document.getElementById('repeatCountWrap').style.display = (v === 'count') ? 'block' : 'none';
        }
        function toggleSpecificDays(){
            var v = document.getElementById('courseRepeatMode').value;
            document.getElementById('specificDaysWrap').style.display = (v === 'specific_days') ? 'block' : 'none';
        }

        function openEditModal(taskId){
            // نجيب بيانات المهمة مباشرة من السيرفر - حل نهائي لمشكلة المودال الفاضي
            fetch('/api/task/' + taskId)
                .then(function(r){ return r.json(); })
                .then(function(task){
                    if(task.error){ alert('المهمة مش موجودة!'); return; }
                    document.getElementById('editTitle').value = task.title || '';
                    document.getElementById('editCategory').value = task.category || 'عام';
                    document.getElementById('editPriority').value = task.priority || 'medium';
                    document.getElementById('editDuration').value = task.duration || 30;
                    var raw = (task.scheduled_time || '').replace(' ', 'T').substring(0,16);
                    document.getElementById('editScheduledTime').value = raw;
                    document.getElementById('editTaskForm').action = '/edit_task/' + taskId;
                    openModal('editTaskModal');
                })
                .catch(function(e){ console.error(e); alert('خطأ في الاتصال - تأكد من السيرفر شغال'); });
        }

        function filterTasks(){
            var text = document.getElementById('si').value.toLowerCase();
            var cat = document.getElementById('fCategory').value;
            var pri = document.getElementById('fPriority').value;
            var status = document.getElementById('fStatus').value;
            document.querySelectorAll('#atc .task').forEach(function(t){
                var matchesText = t.textContent.toLowerCase().includes(text);
                var matchesCat = !cat || t.dataset.category === cat;
                var matchesPri = !pri || t.dataset.priority === pri;
                var matchesStatus = !status || t.dataset.status === status;
                t.style.display = (matchesText && matchesCat && matchesPri && matchesStatus) ? '' : 'none';
            });
        }

        function testBot(){
            document.getElementById('tr').innerHTML='<div class="alert ai">⏳ جاري التشخيص...</div>';
            fetch('/send_test', {method:'POST', headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}}).then(r=>r.json()).then(d=>{
                document.getElementById('tr').innerHTML = d.status==='success'
                    ?`<div class="alert as">✅ تم إرسال تقرير التشخيص! تفقد تيليجرام<br>Scheduler: ${d.scheduler?'✅ شغال':'❌ مش شغال'} | معلق: ${d.pending}</div>`
                    :'<div class="alert ai">❌ فشل إرسال تيليجرام - تأكد من BOT_TOKEN و CHAT_ID</div>';
            });
        }
        function forceCheck(){
            document.getElementById('tr').innerHTML='<div class="alert ai">⏳ جاري الفحص الفوري...</div>';
            fetch('/force_check', {method:'POST', headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}}).then(r=>r.json()).then(d=>{
                document.getElementById('tr').innerHTML=`<div class="alert ${d.status==='ok'?'as':'ai'}">${d.status==='ok'?'✅':'❌'} ${d.msg}</div>`;
            });
        }
    </script>
    <div id="clearModal" class="modal">
        <div class="mc" style="max-width:420px">
            <div class="fb mb20">
                <h3>🗑️ تأكيد المسح</h3>
                <button class="btn btn-sm btn-d" onclick="closeModal('clearModal')">✕</button>
            </div>
            <div class="alert bd" style="margin-bottom:18px">
                ⚠️ هتمسح <b id="clearLabel"></b> - العملية دي مش ممكن تترجع!
            </div>
            <div class="fg">
                <label>اكتب كلمة المرور للتأكيد</label>
                <input type="password" id="clearPassword" class="fc" placeholder="••••••••" autocomplete="off">
            </div>
            <div id="clearMsg" style="margin-bottom:12px;font-size:.88rem"></div>
            <div style="display:flex;gap:10px">
                <button class="btn btn-d" style="flex:1" onclick="executeClear()">🗑️ امسح دلوقتي</button>
                <button class="btn btn-p" style="flex:1" onclick="closeModal('clearModal')">إلغاء</button>
            </div>
        </div>
    </div>

    <script>
        var _clearSection = '';

        function openClearModal(section, label){
            _clearSection = section;
            document.getElementById('clearLabel').textContent = label;
            document.getElementById('clearPassword').value = '';
            document.getElementById('clearMsg').innerHTML = '';
            openModal('clearModal');
            setTimeout(function(){ document.getElementById('clearPassword').focus(); }, 200);
        }

        function executeClear(){
            var pw = document.getElementById('clearPassword').value;
            if(!pw){ document.getElementById('clearMsg').innerHTML='<span style="color:var(--neon-d)">⚠️ اكتب الباسورد الأول!</span>'; return; }
            document.getElementById('clearMsg').innerHTML='<span style="color:var(--neon-p)">⏳ جاري المسح...</span>';
            var fd = new FormData();
            fd.append('confirm_password', pw);
            fetch('/clear_all/' + _clearSection, {method:'POST', body:fd, headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}})
                .then(function(r){ return r.json(); })
                .then(function(d){
                    if(d.status === 'ok'){
                        document.getElementById('clearMsg').innerHTML='<span style="color:var(--neon-s)">✅ ' + d.msg + '</span>';
                        setTimeout(function(){ closeModal('clearModal'); location.reload(); }, 1500);
                    } else {
                        document.getElementById('clearMsg').innerHTML='<span style="color:var(--neon-d)">❌ ' + d.msg + '</span>';
                        document.getElementById('clearPassword').value='';
                        document.getElementById('clearPassword').focus();
                    }
                })
                .catch(function(){ document.getElementById('clearMsg').innerHTML='<span style="color:var(--neon-d)">❌ خطأ في الاتصال</span>'; });
        }

        // تأكيد بـ Enter في حقل الباسورد
        document.addEventListener('DOMContentLoaded', function(){
            document.getElementById('clearPassword').addEventListener('keydown', function(e){
                if(e.key === 'Enter') executeClear();
            });
        });
    </script>
</body>
</html>
'''

_scheduler_started = False

def start_background_jobs():
    """
    يشغل الـ scheduler وthread استقبال أزرار تيليجرام.
    محمي من التشغيل المزدوج (مثلاً مع Flask debug reloader اللي بيشغل الملف مرتين).
    """
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_notifications, 'interval', seconds=30, id='notif_check')
    scheduler.add_job(ensure_prayer_times_synced, 'interval', hours=6, id='prayer_sync')
    scheduler.start()
    logger.info("✅ Scheduler اشتغل - هيفحص الإشعارات كل 30 ثانية")
    # جيب مواقيت الصلاة فورًا من أول تشغيل
    threading.Thread(target=ensure_prayer_times_synced, daemon=True).start()

    if BOT_TOKEN and CHAT_ID:
        threading.Thread(target=poll_updates, daemon=True).start()
        logger.info("Telegram callback polling thread started")
    else:
        logger.info("Telegram polling skipped because BOT_TOKEN/CHAT_ID are not configured")

# ==================== تشغيل ====================
# ==================== تشغيل التطبيق والخدمات ====================

# إنشاء قاعدة البيانات لو مش موجودة
init_db()

# تشغيل المهام في الخلفية (الـ Scheduler والإشعارات)
start_background_jobs()

# إرسال رسالة التليجرام عند بدء التشغيل
tg_send("🚀 <b>تم التشغيل!</b>\n✅ نظام الإشعارات شغال دلوقتي فعليًا")

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 النظام شغال والموقع فاتح محلّياً!")
    print("📱 http://127.0.0.1:5000")
    print("="*60 + "\n")
    
    app.run(debug=False, host='0.0.0.0', port=5000)