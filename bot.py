import os
import re
import json
import time
import zipfile
import threading
import logging
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

# ==========================================
# 1. نظام تسجيل الأخطاء (Logging System)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_errors.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("UOB_Bot")

# ==========================================
# 2. إعدادات الأمان الأساسية (متغيرات البيئة)
# ==========================================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8838936553:AAEQ-BlbFMyO8GwiFRB6RJdAk2_cv1X_ZzE')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '6596940817')
CHANNEL_USERNAME = '@UOB_Engineers'

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 3. الثوابت وقوائم التحكم
# ==========================================
GRADE_POINTS = {'AA': 4.0, 'A': 3.5, 'BB': 3.0, 'B': 2.5, 'CC': 2.0, 'C': 1.5, 'DD': 1.0, 'D': 0.5, 'F': 0.0}
IGNORED_GRADES = ['I', 'S', 'U']

def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    features = load_json(FEATURES_FILE, DEFAULT_FEATURES)
    
    if features.get('gpa_current', True): kb.row('📊 معدلي الحالي')
    if features.get('gpa_calc', True): kb.row('🧮 حاسبة المعدل')
    if features.get('what_if', True): kb.row('🎯 كم أحتاج لهدف معين')
    if features.get('improve', True): kb.row('🔄 أثر تحسين مادة')
    if features.get('my_grade', True): kb.row('🏆 ما تقديري؟', '⚖️ موازنة مواد فصلي')
    if features.get('history', True): kb.row('💾 سجل معدلاتي')
    if features.get('sarakhni', True): kb.row('💬 صارحني')
    kb.row('❓ كيف أستخدم البوت')
    return kb

# ==========================================
# 4. إدارة البيانات والملفات (Thread-Safe)
# ==========================================
file_lock = threading.Lock()

DB_USERS_FILE = 'uob_engineers_users.json'
HISTORY_FILE = 'uob_engineers_gpa_history.json'
SARAKHNI_FILE = 'uob_engineers_sarakhni_log.json'
STATS_FILE = 'uob_engineers_stats.json'
EVENTS_FILE = 'uob_engineers_events.json'
FEATURES_FILE = 'uob_engineers_features.json'

DEFAULT_FEATURES = {
    'gpa_current': True,
    'gpa_calc': True,
    'what_if': True,
    'improve': True,
    'my_grade': True,
    'balance': True,
    'history': True,
    'sarakhni': True
}

USER_STATES = {}
USER_DATA = {}
MAINTENANCE_MODE = False

def load_json(filename, default):
    if os.path.exists(filename):
        try:
            with file_lock:
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"خطأ في قراءة {filename}: {e}")
            return default
    return default

def save_json(filename, data):
    try:
        with file_lock:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطأ في حفظ {filename}: {e}")

db_users = load_json(DB_USERS_FILE, {"all_users": [], "banned": []})
ALL_USERS = set(db_users.get("all_users", []))
BANNED_USERS = set(db_users.get("banned", []))

def save_user_db():
    save_json(DB_USERS_FILE, {"all_users": list(ALL_USERS), "banned": list(BANNED_USERS)})

def register_user(chat_id):
    chat_str = str(chat_id)
    if chat_str not in ALL_USERS:
        ALL_USERS.add(chat_str)
        save_user_db()

def track_feature(feature_name):
    stats = load_json(STATS_FILE, {})
    stats[feature_name] = stats.get(feature_name, 0) + 1
    save_json(STATS_FILE, stats)

# ==========================================
# 5. دوال الحماية والمساعدة
# ==========================================
def safe_send_message(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except ApiTelegramException as e:
        logger.warning(f"تعذر الإرسال للمستخدم {chat_id}: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع عند الإرسال لـ {chat_id}: {e}")
    return None

def normalize_digits(s):
    digit_map = {'٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9','،':','}
    for k, v in digit_map.items():
        s = s.replace(k, v)
    return s

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return True

def is_admin(user_id):
    return str(user_id) == str(ADMIN_CHAT_ID)

def get_anon_id(chat_id):
    sarakhni_data = load_json(SARAKHNI_FILE, {"count": 100, "users": {}, "messages": []})
    user_str = str(chat_id)
    if user_str not in sarakhni_data["users"]:
        current_count = sarakhni_data.get("count", 100) + 1
        sarakhni_data["count"] = current_count
        sarakhni_data["users"][user_str] = str(current_count)
        save_json(SARAKHNI_FILE, sarakhni_data)
        return str(current_count)
    return sarakhni_data["users"][user_str]

# ==========================================
# 6. سيرفر الـ Keep-Alive
# ==========================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"UOB Engineers Bot is running securely!")
    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ==========================================
# 7. نظام التنبيهات المجدولة
# ==========================================
def scheduler_loop():
    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=2)
            current_date_time = now.strftime("%Y-%m-%d %H:%M")
            events = load_json(EVENTS_FILE, {})
            modified = False
            for dt_str, details in events.items():
                if not details.get("sent") and current_date_time == dt_str:
                    for u_id in list(ALL_USERS):
                        safe_send_message(u_id, f"🔔 **تنبيه هام:**\n\n{details['text']}", parse_mode='Markdown')
                    events[dt_str]["sent"] = True
                    modified = True
            if modified:
                save_json(EVENTS_FILE, events)
        except Exception as e:
            logger.error(f"خطأ في المجدول: {e}")
        time.sleep(60)

threading.Thread(target=scheduler_loop, daemon=True).start()

# ==========================================
# 8. المعالجات الرئيسية (Handlers)
# ==========================================
@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        chat_id = message.chat.id
        register_user(chat_id)
        
        if str(chat_id) in BANNED_USERS:
            safe_send_message(chat_id, '🚫 عذراً، تم حظرك من استخدام البوت.')
            return

        USER_STATES[chat_id] = 'idle'
        
        args = message.text.split()
        if len(args) > 1 and args[1] == 'sarakhni':
            track_feature('💬 صارحني (رابط مباشر بدون اشتراك)')
            set_state_and_send(chat_id, 'sarakhni', '💬 صارحني\nهنا يمكنك إرسال أي شيء بكل حرية:\nسؤال، اقتراح، ملاحظة، أو أي شيء تريده\n\nاكتب رسالتك الآن 👇\nللإلغاء أرسل: إلغاء')
            return

        if not check_subscription(chat_id):
            send_sub_prompt(chat_id)
            return

        safe_send_message(chat_id,
            '🎓 أهلاً بك في UOB Engineers\nمنصتك لمتابعة معدلك بكلية الهندسة - جامعة بنغازي 🏛️\n\n✨ بالتوفيق في مسيرتك!',
            reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"خطأ في /start: {e}", exc_info=True)

@bot.message_handler(commands=['admin'])
def handle_admin_command(message):
    if is_admin(message.chat.id):
        show_admin_panel(message.chat.id)

def show_admin_panel(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    maint_status = 'مفعلة 🔴' if MAINTENANCE_MODE else 'معطلة 🟢'
    markup.add(
        types.InlineKeyboardButton('📊 الإحصائيات', callback_data='admin_stats'),
        types.InlineKeyboardButton('📢 إذاعة جماعية', callback_data='admin_broadcast'),
        types.InlineKeyboardButton(f"🛠️ الصيانة ({maint_status})", callback_data='admin_toggle_maint'),
        types.InlineKeyboardButton('👥 إدارة المحظورين', callback_data='admin_banned_list'),
        types.InlineKeyboardButton('⚡ حظر سريع للطلاب', callback_data='admin_quick_ban_menu'),
        types.InlineKeyboardButton('⚙️ تحكم بالأزرار والمميزات', callback_data='admin_features_menu'),
        types.InlineKeyboardButton('📈 الأزرار النشطة', callback_data='admin_top_buttons'),
        types.InlineKeyboardButton('📜 سجل الأخطاء', callback_data='admin_download_log'),
        types.InlineKeyboardButton('📦 نسخة احتياطية (ZIP)', callback_data='admin_backup')
    )
    safe_send_message(chat_id, '⚙️ **لوحة تحكم الإدارة الشاملة (أزرار تفاعلية فقط)**', parse_mode='Markdown', reply_markup=markup)

def send_sub_prompt(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('✅ اشتركت', callback_data='check_sub'))
    safe_send_message(chat_id,
        f'⚠️ يجب الاشتراك في القناة أولاً لاستخدام البوت:\n👉 {CHANNEL_USERNAME}',
        reply_markup=markup)

@bot.message_handler(func=lambda msg: True)
def handle_all_messages(message):
    try:
        chat_id = message.chat.id
        text = (message.text or '').strip()
        register_user(chat_id)

        if str(chat_id) in BANNED_USERS:
            return

        if MAINTENANCE_MODE and not is_admin(chat_id):
            safe_send_message(chat_id, '🛠️ البوت في وضع الصيانة الدورية، نعود قريباً.')
            return

        state = USER_STATES.get(chat_id, 'idle')
        if state != 'sarakhni' and not check_subscription(chat_id):
            send_sub_prompt(chat_id)
            return

        if is_admin(chat_id) and message.reply_to_message:
            replied_text = message.reply_to_message.text or ''
            m = re.search(r'مجهول\s*#(\d+)', replied_text)
            if m:
                anon_id = m.group(1)
                sarakhni_data = load_json(SARAKHNI_FILE, {"users": {}})
                target_chat_id = next((u for u, a in sarakhni_data.get("users", {}).items() if a == anon_id), None)
                if target_chat_id:
                    success = safe_send_message(target_chat_id, f"📩 **رد من الإدارة:**\n\n{text}", parse_mode='Markdown')
                    if success:
                        safe_send_message(chat_id, f"✅ تم إرسال الرد لمجهول #{anon_id}")
                    else:
                        safe_send_message(chat_id, f"❌ تعذر الإرسال (قد يكون الطالب حظر البوت).")
                else:
                    safe_send_message(chat_id, f"⚠️ لم يتم العثور على صاحب الرقم #{anon_id}")
                return

        if text == 'إلغاء':
            USER_STATES[chat_id] = 'idle'
            safe_send_message(chat_id, '✅ تم الإلغاء.', reply_markup=get_main_keyboard())
            return

        features = load_json(FEATURES_FILE, DEFAULT_FEATURES)
        
        commands = {}
        if features.get('gpa_calc', True):
            commands['🧮 حاسبة المعدل'] = lambda: set_state_and_send(chat_id, 'manual_calc',
                "🧮 حاسبة المعدل\n"
                "أرسل موادك بهذا الشكل:\n"
                "عدد الوحدات ثم الدرجة، كل مادة في سطر\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "مثال:\n"
                "12 BB\n"
                "9 CC\n"
                "6 AA\n"
                "3 A\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📌 ملاحظات:\n"
                "• استخدم آخر درجة حصلت عليها\n"
                "• مواد فصل واحد = معدل فصلي\n"
                "• كل مواد دراستك = المعدل التراكمي\n"
                "• مواد I أو S أو U تُتجاهل تلقائياً\n"
                "للإلغاء أرسل: إلغاء")
                
        if features.get('what_if', True):
            commands['🎯 كم أحتاج لهدف معين'] = lambda: set_state_and_send(chat_id, 'what_if',
                "🎯 كم أحتاج لهدف معين؟\n"
                "أرسل 4 أرقام كل رقم في سطر:\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ معدلك التراكمي الحالي\n"
                "2️⃣ وحداتك المنجزة حتى الآن\n"
                "3️⃣ وحداتك المتبقية\n"
                "4️⃣ المعدل الذي تطمح إليه\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "مثال:\n"
                "2.80\n"
                "60\n"
                "30\n"
                "3.20\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "للإلغاء أرسل: إلغاء")
                
        if features.get('improve', True):
            commands['🔄 أثر تحسين مادة'] = lambda: set_state_and_send(chat_id, 'improve',
                "🔄 أثر تحسين مادة على معدلك\n"
                "أرسل 5 أسطر:\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ معدلك التراكمي الحالي\n"
                "2️⃣ مجموع وحداتك الكلية\n"
                "3️⃣ وحدات المادة التي تريد تحسينها\n"
                "4️⃣ درجتها القديمة\n"
                "5️⃣ درجتها الجديدة المتوقعة\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "مثال:\n"
                "2.80\n"
                "90\n"
                "12\n"
                "CC\n"
                "BB\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "للإلغاء أرسل: إلغاء")
                
        if features.get('my_grade', True):
            commands['🏆 ما تقديري؟'] = lambda: set_state_and_send(chat_id, 'my_grade',
                "🏆 ما تقديري؟\n"
                "أرسل معدلك فقط\n"
                "مثال: 3.20\n"
                "للإلغاء أرسل: إلغاء")
                
        if features.get('balance', True):
            commands['⚖️ موازنة مواد فصلي'] = lambda: set_state_and_send(chat_id, 'balance',
                "⚖️ موازنة مواد فصلي\n"
                "أرسل مواد فصلك مع توقعاتك:\n"
                "عدد الوحدات ثم الدرجة المتوقعة، كل مادة في سطر\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "مثال:\n"
                "12 BB\n"
                "9 AA\n"
                "6 CC\n"
                "3 B\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "هذه الميزة تساعدك على:\n"
                "• معرفة معدلك المتوقع هذا الفصل\n"
                "• اكتشاف أي مادة تستحق جهداً أكبر\n"
                "للإلغاء أرسل: إلغاء")
                
        if features.get('sarakhni', True):
            commands['💬 صارحني'] = lambda: set_state_and_send(chat_id, 'sarakhni',
                "💬 صارحني\n"
                "هنا يمكنك إرسال أي شيء بكل حرية:\n"
                "سؤال، اقتراح، ملاحظة، أو أي شيء تريده\n\n"
                "اكتب رسالتك الآن 👇\n"
                "للإلغاء أرسل: إلغاء")
                
        if features.get('history', True):
            commands['💾 سجل معدلاتي'] = lambda: (track_feature('💾 سجل معدلاتي'), show_history(chat_id))
            
        commands['❓ كيف أستخدم البوت'] = lambda: (track_feature('❓ كيف أستخدم البوت'), show_help(chat_id))

        if text in commands:
            commands[text]()
            return

        if state == 'manual_calc': handle_manual_calc(chat_id, text, is_balance=False)
        elif state == 'balance': handle_manual_calc(chat_id, text, is_balance=True)
        elif state == 'what_if': handle_what_if(chat_id, text)
        elif state == 'improve': handle_improve(chat_id, text)
        elif state == 'my_grade': handle_my_grade(chat_id, text)
        elif state == 'sarakhni': handle_sarakhni(chat_id, text, message)
        elif state == 'admin_broadcast_text' and is_admin(chat_id): handle_admin_broadcast(chat_id, text)
        else: safe_send_message(chat_id, '👇 اختر من القائمة أسفله', reply_markup=get_main_keyboard())

    except Exception as e:
        logger.error(f"خطأ في معالجة الرسالة: {e}", exc_info=True)

def set_state_and_send(chat_id, state, msg):
    USER_STATES[chat_id] = state
    safe_send_message(chat_id, msg)

# ==========================================
# 9. الحسابات الأكاديمية ونظام صارحني
# ==========================================
def rating_label(gpa):
    if gpa >= 3.50: return "💎 ممتاز (AA, A)"
    elif gpa >= 2.50: return "🥈 جيد جداً (BB, B)"
    elif gpa >= 1.50: return "📘 جيد (CC, C)"
    elif gpa >= 0.50: return "📙 مقبول (DD, D)"
    return "❌ ضعيف (F)"

def handle_manual_calc(chat_id, text, is_balance):
    lines = [s.strip() for s in text.split('\n') if s.strip()]
    details, ignored = [], []
    total_units, total_points = 0.0, 0.0

    for line in lines:
        norm = normalize_digits(line)
        m = re.match(r'^(\d+(?:\.\d+)?)\s*([A-Za-z]{1,2})$', norm)
        if not m: continue
        units, grade = float(m.group(1)), m.group(2).upper()
        if grade in IGNORED_GRADES:
            ignored.append(f"{units} {grade}")
            continue
        if grade in GRADE_POINTS:
            pts = GRADE_POINTS[grade]
            details.append({'units': units, 'grade': grade, 'points': pts})
            total_units += units
            total_points += units * pts

    if not details:
        safe_send_message(chat_id, '⚠️ الصيغة غير صحيحة، أعد المحاولة أو أرسل: إلغاء')
        return

    gpa = total_points / total_units if total_units > 0 else 0
    out = f"{'⚖️ تحليل فصلك' if is_balance else '✅ النتيجة'}\n━━━━━━━━━━━━━━━━━━━━\n📋 المواد:\n"
    for d in details:
        out += f"• {d['units']} و × {d['grade']} = {d['units']*d['points']:.1f}\n"
    out += '━━━━━━━━━━━━━━━━━━━━\n'
    if ignored: out += f"ℹ️ تم تجاهل: {', '.join(ignored)}\n"
    out += f"📐 الوحدات: {total_units} | 📊 النقاط: {total_points:.1f}\n🎯 المعدل: {gpa:.2f}\n🏆 {rating_label(gpa)}"
    
    safe_send_message(chat_id, out)
    USER_DATA[f"{chat_id}_pending_gpa"] = f"{gpa:.2f}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('✅ حفظ المعدل', callback_data='save_yes'), types.InlineKeyboardButton('❌ إلغاء', callback_data='save_no'))
    safe_send_message(chat_id, '💾 هل تريد حفظ الناتج في سجل معدلاتك؟', reply_markup=markup)

def handle_sarakhni(chat_id, text, msg):
    anon_id = get_anon_id(chat_id)
    u_info = msg.from_user
    
    s_data = load_json(SARAKHNI_FILE, {"count": 100, "users": {}, "messages": []})
    s_data["messages"].append({"anonId": anon_id, "text": text, "chat_id": chat_id})
    save_json(SARAKHNI_FILE, s_data)
    
    USER_STATES[chat_id] = 'idle'
    safe_send_message(chat_id, "وصلت رسالتك بنجاح ✅", reply_markup=get_main_keyboard())
    
    msg_for_channel = f"💬 **رسالة صراحة جديدة (مجهول #{anon_id}):**\n\n{text}"
    safe_send_message(ADMIN_CHAT_ID, msg_for_channel, parse_mode='Markdown')
    
    msg_identity = (
        f"🕵️ **هوية مرسل الرسالة أعلاه (خاص بالإدارة):**\n"
        f"• الرقم المميز: مجهول #{anon_id}\n"
        f"• الاسم: {u_info.first_name or ''} {u_info.last_name or ''}\n"
        f"• اليوزر: @{u_info.username or 'لا يوجد'}\n"
        f"• ID الطالب: `{chat_id}`"
    )
    markup_admin = types.InlineKeyboardMarkup()
    if str(chat_id) in BANNED_USERS:
        markup_admin.add(types.InlineKeyboardButton('🔓 إلغاء الحظر عن هذا الطالب', callback_data=f'unban_{chat_id}'))
    else:
        markup_admin.add(types.InlineKeyboardButton('🚫 حظر هذا الطالب من البوت', callback_data=f'ban_{chat_id}'))
        
    safe_send_message(ADMIN_CHAT_ID, msg_identity, parse_mode='Markdown', reply_markup=markup_admin)

def handle_what_if(chat_id, text):
    try:
        lines = [float(normalize_digits(s.strip())) for s in text.split('\n') if s.strip()]
        if len(lines) != 4 or lines[2] <= 0: raise ValueError
        needed = (lines[3] * (lines[1] + lines[2]) - lines[0] * lines[1]) / lines[2]
        if needed > 4.0: out = f"⚠️ تحتاج معدل {needed:.2f} وهو أعلى من 4.0 (الهدف غير ممكن حالياً)."
        elif needed <= 0: out = "🎉 أنت متجاوز للهدف بالفعل!"
        else: out = f"📌 تحتاج لتحقيق معدل {needed:.2f} في الفصل القادم للوصول لـ {lines[3]:.2f} 💪"
        safe_send_message(chat_id, out)
        USER_STATES[chat_id] = 'idle'
    except ValueError:
        safe_send_message(chat_id, '⚠️ يرجى التأكد من أدخال 4 أرقام صحيحة.')

def handle_improve(chat_id, text):
    try:
        lines = [normalize_digits(s.strip()) for s in text.split('\n') if s.strip()]
        current, total_u, subj_u = map(float, lines[:3])
        old_g, new_g = lines[3].upper(), lines[4].upper()
        if old_g not in GRADE_POINTS or new_g not in GRADE_POINTS: raise ValueError
        new_gpa = (current * total_u - (subj_u * GRADE_POINTS[old_g]) + (subj_u * GRADE_POINTS[new_g])) / total_u
        safe_send_message(chat_id, f"📈 المعدل بعد التحسين: {new_gpa:.2f}\n✨ مقدار التغير: {new_gpa - current:+.2f}")
        USER_STATES[chat_id] = 'idle'
    except:
        safe_send_message(chat_id, '⚠️ تأكد من إدخال البيانات بالشكل المطلوب.')

def handle_my_grade(chat_id, text):
    try:
        gpa = float(normalize_digits(text.strip()))
        if not (0 <= gpa <= 4.0): raise ValueError
        out = (
            f"🏆 التقدير: {rating_label(gpa)}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📊 السلم الأكاديمي:\n"
            "💎 90-100 → AA (4.0)\n"
            "🏅 85-89 → A (3.5)\n"
            "🥈 80-84 → BB (3.0)\n"
            "🥉 75-79 → B (2.5)\n"
            "⭐ 70-74 → CC (2.0)\n"
            "📘 65-69 → C (1.5)\n"
            "📙 60-64 → DD (1.0)\n"
            "📕 50-59 → D (0.5)\n"
            "❌ <50 → F (0.0)"
        )
        safe_send_message(chat_id, out)
        USER_STATES[chat_id] = 'idle'
    except ValueError:
        safe_send_message(chat_id, '⚠️ أرسل رقماً بين 0.00 و 4.00.')

def handle_admin_broadcast(chat_id, text):
    USER_STATES[chat_id] = 'idle'
    success_count = 0
    safe_send_message(chat_id, "⏳ جاري إرسال الإذاعة...")
    for u_id in list(ALL_USERS):
        if safe_send_message(u_id, f"📢 **تنويه هـام:**\n\n{text}", parse_mode='Markdown'):
            success_count += 1
    safe_send_message(chat_id, f"✅ وصلت الإذاعة لـ {success_count} طالب.")

# ==========================================
# 10. التفاعلات (Callbacks) والأزرار التفاعلية
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    try:
        chat_id = call.message.chat.id
        data = call.data

        if data == 'check_sub':
            if check_subscription(chat_id):
                bot.answer_callback_query(call.id, '✅ مرحباً بك!')
                safe_send_message(chat_id, '🎓 أهلاً بك في البوت!', reply_markup=get_main_keyboard())
            else:
                bot.answer_callback_query(call.id, '⚠️ لم تشترك في القناة بعد!', show_alert=True)
                
        elif data == 'save_no':
            bot.answer_callback_query(call.id)
            safe_send_message(chat_id, '👍 تم الإلغاء.', reply_markup=get_main_keyboard())
            
        elif data == 'save_yes':
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton('تراكمي', callback_data='save_type_cum'),
                types.InlineKeyboardButton('فصلي', callback_data='save_type_sem')
            )
            safe_send_message(chat_id, '📌 اختر نوع المعدل للحفظ:', reply_markup=markup)
            
        elif data in ['save_type_cum', 'save_type_sem']:
            bot.answer_callback_query(call.id)
            gpa = float(USER_DATA.get(f"{chat_id}_pending_gpa", 0))
            history = load_json(HISTORY_FILE, {})
            u_str = str(chat_id)
            if u_str not in history: history[u_str] = []

            if data == 'save_type_cum':
                history[u_str].append({'semester': 'تراكمي', 'type': 'تراكمي', 'gpa': gpa})
                safe_send_message(chat_id, '✅ تم حفظ المعدل التراكمي!')
            else:
                sem_count = sum(1 for r in history[u_str] if r['type'] == 'فصلي') + 1
                history[u_str].append({'semester': f"فصلي {sem_count}", 'type': 'فصلي', 'gpa': gpa})
                safe_send_message(chat_id, f'✅ تم الحفظ تحت اسم (فصلي {sem_count})!')
            save_json(HISTORY_FILE, history)

        elif data.startswith('ban_') and is_admin(chat_id):
            target_id = data.split('_')[1]
            if target_id not in BANNED_USERS:
                BANNED_USERS.add(target_id)
                save_user_db()
                bot.answer_callback_query(call.id, f"🚫 تم حظر المستخدم {target_id}")
                safe_send_message(chat_id, f"✅ تم حظر المستخدم ذو الـ ID: `{target_id}` بنجاح.")
                try:
                    markup_new = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🔓 إلغاء الحظر عن هذا الطالب', callback_data=f'unban_{target_id}'))
                    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=markup_new)
                except Exception:
                    pass
            else:
                bot.answer_callback_query(call.id, "المستخدم محظر بالفعل")

        elif data.startswith('unban_') and is_admin(chat_id):
            target_id = data.split('_')[1]
            if target_id in BANNED_USERS:
                BANNED_USERS.remove(target_id)
                save_user_db()
                bot.answer_callback_query(call.id, f"🔓 تم رفع الحظر عن {target_id}")
                safe_send_message(chat_id, f"✅ تم رفع الحظر عن المستخدم ذو الـ ID: `{target_id}`.")
                try:
                    markup_new = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🚫 حظر هذا الطالب من البوت', callback_data=f'ban_{target_id}'))
                    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=markup_new)
                except Exception:
                    pass
            else:
                bot.answer_callback_query(call.id, "المستخدم ليس محظوراً")

        elif data == 'admin_stats' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            msg_count = len(load_json(SARAKHNI_FILE, {}).get("messages", []))
            safe_send_message(chat_id, f"📊 **الإحصائيات:**\n👥 المشتركون: {len(ALL_USERS)}\n🚫 المحظورون: {len(BANNED_USERS)}\n💬 رسائل صراحة: {msg_count}", parse_mode='Markdown')

        elif data == 'admin_broadcast' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            USER_STATES[chat_id] = 'admin_broadcast_text'
            safe_send_message(chat_id, '📢 أرسل نص الإذاعة الجماعية:')

        elif data == 'admin_toggle_maint' and is_admin(chat_id):
            global MAINTENANCE_MODE
            MAINTENANCE_MODE = not MAINTENANCE_MODE
            bot.answer_callback_query(call.id, f"وضع الصيانة: {'مفعل' if MAINTENANCE_MODE else 'معطل'}")
            show_admin_panel(chat_id)

        elif data == 'admin_banned_list' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup(row_width=1)
            if BANNED_USERS:
                for b_id in list(BANNED_USERS):
                    markup.add(types.InlineKeyboardButton(f"🔓 إلغاء حظر: {b_id}", callback_data=f"unban_{b_id}"))
                safe_send_message(chat_id, "🚫 **قائمة المستخدمين المحظورين حالياً:**\nانقر على زر الفك بجانب أي ID:", parse_mode='Markdown', reply_markup=markup)
            else:
                safe_send_message(chat_id, "🟢 لا يوجد أي مستخدم محظور حالياً.")

        elif data == 'admin_quick_ban_menu' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup(row_width=1)
            recent_users = list(ALL_USERS)[-10:]
            if recent_users:
                for u_id in recent_users:
                    if u_id not in BANNED_USERS and not is_admin(u_id):
                        markup.add(types.InlineKeyboardButton(f"🚫 حظر الطالب: {u_id}", callback_data=f"ban_{u_id}"))
                safe_send_message(chat_id, "⚡ **قائمة أحدث الطلاب المسجلين (للحظر الفوري بضغطة زر):**", parse_mode='Markdown', reply_markup=markup)
            else:
                safe_send_message(chat_id, "ℹ️ لا توجد سجلات طلاب بعد.")

        elif data == 'admin_features_menu' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            features = load_json(FEATURES_FILE, DEFAULT_FEATURES)
            markup = types.InlineKeyboardMarkup(row_width=2)
            for f_key, f_status in features.items():
                status_icon = '🟢 مفعل' if f_status else '🔴 معطل'
                markup.add(types.InlineKeyboardButton(f"{f_key}: {status_icon}", callback_data=f"toggle_feat_{f_key}"))
            safe_send_message(chat_id, '⚙️ **إدارة تفعيل أو تعطيل ميزات وأزرار البوت:**\nانقر على الميزة لتبديل حالتها فوراً:', parse_mode='Markdown', reply_markup=markup)

        elif data.startswith('toggle_feat_') and is_admin(chat_id):
            f_key = data.replace('toggle_feat_', '')
            features = load_json(FEATURES_FILE, DEFAULT_FEATURES)
            if f_key in features:
                features[f_key] = not features[f_key]
                save_json(FEATURES_FILE, features)
                bot.answer_callback_query(call.id, f"تم تغيير حالة {f_key}")
                markup = types.InlineKeyboardMarkup(row_width=2)
                for fk, fs in features.items():
                    s_icon = '🟢 مفعل' if fs else '🔴 معطل'
                    markup.add(types.InlineKeyboardButton(f"{fk}: {s_icon}", callback_data=f"toggle_feat_{fk}"))
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=markup)

        elif data == 'admin_top_buttons' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            stats = sorted(load_json(STATS_FILE, {}).items(), key=lambda x: x[1], reverse=True)
            safe_send_message(chat_id, "📈 **الأزرار الأكثر استخداماً:**\n" + "\n".join([f"• {k}: {v}" for k, v in stats]) if stats else "لا توجد بيانات.", parse_mode='Markdown')

        elif data == 'admin_download_log' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            if os.path.exists("bot_errors.log") and os.path.getsize("bot_errors.log") > 0:
                with open("bot_errors.log", "rb") as f:
                    bot.send_document(chat_id, f, caption="📜 سجل الأخطاء والأحداث.")
            else:
                safe_send_message(chat_id, "✅ الملف نظيف بدون أخطاء.")

        elif data == 'admin_backup' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            safe_send_message(chat_id, "⏳ جاري تجهيز النسخة الاحتياطية...")
            files_to_zip = [DB_USERS_FILE, HISTORY_FILE, SARAKHNI_FILE, STATS_FILE, EVENTS_FILE, FEATURES_FILE, 'bot_errors.log']
            zip_name = 'UOB_Database_Backup.zip'
            try:
                with zipfile.ZipFile(zip_name, 'w') as zipf:
                    for f_file in files_to_zip:
                        if os.path.exists(f_file):
                            zipf.write(f_file)
                with open(zip_name, 'rb') as f:
                    bot.send_document(chat_id, f, caption=f"📦 النسخة الاحتياطية لبيانات البوت.\n📅 التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            except Exception as e:
                safe_send_message(chat_id, f"❌ خطأ عند ضغط الملفات: {e}")

    except Exception as e:
        logger.error(f"خطأ في Callback: {e}", exc_info=True)

def show_history(chat_id):
    records = load_json(HISTORY_FILE, {}).get(str(chat_id), [])
    if not records:
        safe_send_message(chat_id, '💾 السجل فارغ.')
        return
    out = '💾 سجل معدلاتي:\n' + "\n".join([f"📘 {r['semester']} → {r['gpa']:.2f} ({r['type']})" for r in records])
    safe_send_message(chat_id, out)

def show_help(chat_id):
    help_text = (
        "❓ دليل استخدام بوت UOB Engineers الكامل 🏛️\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧮 حاسبة المعدل: تتيح لك حساب نقاطك ومعدلك الفصلية.\n"
        "🎯 كم أحتاج لهدف معين: تحديد المعدل المطلوب للفصل القادم.\n"
        "🔄 أثر تحسين مادة: حساب التغير عند إعادة مادة لرفع تقديرها.\n"
        "⚖️ موازنة مواد فصلي: تجربة سيناريوهات تقديرات الفصل القادم.\n"
        "🏆 ما تقديري؟ معرفة التقدير بناءً على المعدل الرقمي.\n"
        "💾 سجل معدلاتي: استعراض المعدلات الفصلية والتراكمية المحفوظة.\n"
        "💬 صارحني: لإرسال استفسار أو اقتراح للإدارة بسرية تامة.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    safe_send_message(chat_id, help_text, parse_mode='Markdown')

# ==========================================
# 11. التشغيل الذاتي
# ==========================================
if __name__ == "__main__":
    logger.info("جاري إعادة تهيئة البوت بأمان تام...")
    try:
        bot.delete_webhook()
    except Exception:
        pass
    
    logger.info("🚀 البوت جاهز ويعمل بسلامة كاملة...")
    bot.infinity_polling(timeout=20, long_polling_timeout=15, logger_level=logging.ERROR)
