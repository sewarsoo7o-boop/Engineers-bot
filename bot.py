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
# 2. إعدادات البوت الأساسية
# ==========================================
BOT_TOKEN = '8838936553:AAEQ-BlbFMyO8GwiFRB6RJdAk2_cv1X_ZzE'
ADMIN_CHAT_ID = '6596940817'
CHANNEL_USERNAME = '@UOB_Engineers'

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 3. الثوابت وقوائم التحكم
# ==========================================
GRADE_POINTS = {'AA': 4.0, 'A': 3.5, 'BB': 3.0, 'B': 2.5, 'CC': 2.0, 'C': 1.5, 'DD': 1.0, 'D': 0.5, 'F': 0.0}
IGNORED_GRADES = ['I', 'S', 'U']

MAIN_KEYBOARD = types.ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_KEYBOARD.row('📊 معدلي الحالي', '🧮 حاسبة المعدل')
MAIN_KEYBOARD.row('🎯 كم أحتاج لهدف معين', '🔄 أثر تحسين مادة')
MAIN_KEYBOARD.row('🏆 ما تقديري؟', '⚖️ موازنة مواد فصلي')
MAIN_KEYBOARD.row('💾 سجل معدلاتي', '💬 صارحني')
MAIN_KEYBOARD.row('❓ كيف أستخدم البوت')

# ==========================================
# 4. إدارة البيانات والملفات (Thread-Safe)
# ==========================================
file_lock = threading.Lock()

DB_USERS_FILE = 'uob_engineers_users.json'
HISTORY_FILE = 'uob_engineers_gpa_history.json'
SARAKHNI_FILE = 'uob_engineers_sarakhni_log.json'
STATS_FILE = 'uob_engineers_stats.json'
EVENTS_FILE = 'uob_engineers_events.json'

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
        self.wfile.write(b"UOB Engineers Bot is running smoothly!")
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
                    logger.info(f"إطلاق تنبيه مجدول: {details['text']}")
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
            safe_send_message(chat_id, '🚫 عذراً، الحساب معطل.')
            return

        USER_STATES[chat_id] = 'idle'
        
        # إذا دخل الشخص عبر رابط صارحني المباشر، نتخطى شرط الاشتراك تماماً
        args = message.text.split()
        if len(args) > 1 and args[1] == 'sarakhni':
            track_feature('💬 صارحني (رابط مباشر بدون اشتراك)')
            set_state_and_send(chat_id, 'sarakhni', '💬 **قسم صارحني (بدون اشتراك إجباري)**\nأرسل اقتراحك أو استفسارك وسيصل للإدارة بسرية تامة 🔒\n\nاكتب رسالتك الآن 👇')
            return

        # للبقية، نتحقق من الاشتراك في القناة
        if not check_subscription(chat_id):
            send_sub_prompt(chat_id)
            return

        safe_send_message(chat_id,
            '🎓 أهلاً بك في UOB Engineers\nمنصتك لمتابعة معدلك بكلية الهندسة - جامعة بنغازي 🏛️\n\n✨ بالتوفيق في مسيرتك!',
            reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"خطأ في /start: {e}", exc_info=True)

@bot.message_handler(commands=['admin'])
def handle_admin_command(message):
    if is_admin(message.chat.id):
        show_admin_panel(message.chat.id)

@bot.message_handler(commands=['add_event'])
def handle_add_event(message):
    chat_id = message.chat.id
    if is_admin(chat_id):
        try:
            parts = message.text.split(' ', 3)
            date_time = f"{parts[1]} {parts[2]}"
            event_text = parts[3]
            
            events = load_json(EVENTS_FILE, {})
            events[date_time] = {"text": event_text, "sent": False}
            save_json(EVENTS_FILE, events)
            
            safe_send_message(chat_id, f"✅ تم جدولة التنبيه!\n📅 الموعد: {date_time}\n📝 النص: {event_text}")
        except Exception:
            safe_send_message(chat_id, "⚠️ الصيغة خاطئة، استخدمي:\n`/add_event 2026-08-15 09:00 نص التنبيه`", parse_mode='Markdown')

def show_admin_panel(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('📊 الإحصائيات', callback_data='admin_stats'),
        types.InlineKeyboardButton('📢 إذاعة جماعية', callback_data='admin_broadcast'),
        types.InlineKeyboardButton(f"🛠️ الصيانة ({'مفعلة 🔴' if MAINTENANCE_MODE else 'معطلة 🟢'})", callback_data='admin_toggle_maint'),
        types.InlineKeyboardButton('📈 الأزرار النشطة', callback_data='admin_top_buttons'),
        types.InlineKeyboardButton('📜 سجل الأخطاء', callback_data='admin_download_log'),
        types.InlineKeyboardButton('📦 نسخة احتياطية (ZIP)', callback_data='admin_backup')
    )
    safe_send_message(chat_id, '⚙️ **لوحة تحكم الإدارة الذكية**', parse_mode='Markdown', reply_markup=markup)

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

        # استثناء خاص: إذا كان المستخدم يرسل رسالة "صارحني"، لا نشترط اشتراكه بالقناة ليتمكن من الإرسال بسلاسة
        state = USER_STATES.get(chat_id, 'idle')
        if state != 'sarakhni' and not check_subscription(chat_id):
            send_sub_prompt(chat_id)
            return

        # الرد المباشر للإدارة عند التمرير (Swipe-to-Reply)
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
            safe_send_message(chat_id, '✅ تم الإلغاء.', reply_markup=MAIN_KEYBOARD)
            return

        # الأوامر الرئيسية
        commands = {
            '📊 معدلي الحالي': lambda: (track_feature(text), safe_send_message(chat_id, '📊 هذه الميزة تتيح لك حساب معدلك عبر إدخال المواد مباشرة.\n\nاستخدم زر: 🧮 حاسبة المعدل')),
            '🧮 حاسبة المعدل': lambda: set_state_and_send(chat_id, 'manual_calc', '🧮 أرسل موادك (الوحدات ثم التقدير):\nمثال:\n12 BB\n9 CC\n\nللإلغاء أرسل: إلغاء'),
            '🎯 كم أحتاج لهدف معين': lambda: set_state_and_send(chat_id, 'what_if', '🎯 أرسل 4 أرقام في أسطر متتالية:\n1️⃣ التراكمي الحالي\n2️⃣ الوحدات المنجزة\n3️⃣ الوحدات القادمة\n4️⃣ المعدل المستهدف\n\nللإلغاء أرسل: إلغاء'),
            '🔄 أثر تحسين مادة': lambda: set_state_and_send(chat_id, 'improve', '🔄 أرسل 5 أسطر:\n1️⃣ التراكمي الحالي\n2️⃣ مجموع وحداتك\n3️⃣ وحدات المادة\n4️⃣ تقديرها القديم\n5️⃣ تقديرها المتوقع\n\nللإلغاء أرسل: إلغاء'),
            '🏆 ما تقديري؟': lambda: set_state_and_send(chat_id, 'my_grade', '🏆 أرسل معدلك فقط (مثال: 3.20)'),
            '⚖️ موازنة مواد فصلي': lambda: set_state_and_send(chat_id, 'balance', '⚖️ أرسل توقعات المواد (الوحدات ثم التقدير)\nمثال:\n12 BB\n9 AA'),
            '💬 صارحني': lambda: set_state_and_send(chat_id, 'sarakhni', '💬 **قسم صارحني**\nأرسل اقتراحك أو استفسارك وسيصل للإدارة بسرية تامة 🔒\n\nاكتب رسالتك الآن 👇\nللإلغاء أرسل: إلغاء'),
            '💾 سجل معدلاتي': lambda: (track_feature(text), show_history(chat_id)),
            '❓ كيف أستخدم البوت': lambda: (track_feature(text), show_help(chat_id)),
        }

        if text in commands:
            commands[text]()
            return

        # معالجة المدخلات حسب الحالة
        if state == 'manual_calc': handle_manual_calc(chat_id, text, is_balance=False)
        elif state == 'balance': handle_manual_calc(chat_id, text, is_balance=True)
        elif state == 'what_if': handle_what_if(chat_id, text)
        elif state == 'improve': handle_improve(chat_id, text)
        elif state == 'my_grade': handle_my_grade(chat_id, text)
        elif state == 'sarakhni': handle_sarakhni(chat_id, text, message)
        elif state == 'admin_broadcast_text' and is_admin(chat_id): handle_admin_broadcast(chat_id, text)
        else: safe_send_message(chat_id, '👇 اختر من القائمة أسفله', reply_markup=MAIN_KEYBOARD)

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
    safe_send_message(chat_id, "✅ وصلت رسالتك للإدارة بنجاح، شكراً لتواصلك!", reply_markup=MAIN_KEYBOARD)
    
    msg_for_channel = f"💬 **رسالة صراحة جديدة (مجهول #{anon_id}):**\n\n{text}"
    safe_send_message(ADMIN_CHAT_ID, msg_for_channel, parse_mode='Markdown')
    
    msg_identity = (
        f"🕵️ **هوية مرسل الرسالة أعلاه (خاص بالإدارة):**\n"
        f"• الرقم المميز: مجهول #{anon_id}\n"
        f"• الاسم: {u_info.first_name or ''} {u_info.last_name or ''}\n"
        f"• اليوزر: @{u_info.username or 'لا يوجد'}\n"
        f"• ID الطالب: `{chat_id}`"
    )
    safe_send_message(ADMIN_CHAT_ID, msg_identity, parse_mode='Markdown')

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
        out = f"🏆 التقدير: {rating_label(gpa)}\n━━━━━━━━━━━━━━━━━━━━\n📊 السلم الأكاديمي:\n💎 90-100 → AA (4.0)\n🏅 85-89 → A (3.5)\n🥈 80-84 → BB (3.0)\n🥉 75-79 → B (2.5)\n⭐ 70-74 → CC (2.0)\n📘 65-69 → C (1.5)\n📙 60-64 → DD (1.0)\n📕 50-59 → D (0.5)\n❌ <50 → F (0.0)"
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
# 10. التفاعلات (Callbacks) والعرض
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    try:
        chat_id = call.message.chat.id
        data = call.data

        if data == 'check_sub':
            if check_subscription(chat_id):
                bot.answer_callback_query(call.id, '✅ مرحباً بك!')
                safe_send_message(chat_id, '🎓 أهلاً بك في البوت!', reply_markup=MAIN_KEYBOARD)
            else:
                bot.answer_callback_query(call.id, '⚠️ لم تشترك في القناة بعد!', show_alert=True)
                
        elif data == 'save_no':
            bot.answer_callback_query(call.id)
            safe_send_message(chat_id, '👍 تم الإلغاء.', reply_markup=MAIN_KEYBOARD)
            
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

        elif data == 'admin_stats' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            msg_count = len(load_json(SARAKHNI_FILE, {}).get("messages", []))
            safe_send_message(chat_id, f"📊 **الإحصائيات:**\n👥 المشتركون: {len(ALL_USERS)}\n💬 رسائل صراحة: {msg_count}", parse_mode='Markdown')

        elif data == 'admin_broadcast' and is_admin(chat_id):
            bot.answer_callback_query(call.id)
            USER_STATES[chat_id] = 'admin_broadcast_text'
            safe_send_message(chat_id, '📢 أرسل نص الإذاعة الجماعية:')

        elif data == 'admin_toggle_maint' and is_admin(chat_id):
            global MAINTENANCE_MODE
            MAINTENANCE_MODE = not MAINTENANCE_MODE
            bot.answer_callback_query(call.id, f"وضع الصيانة: {'مفعل' if MAINTENANCE_MODE else 'معطل'}")
            show_admin_panel(chat_id)

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
            files_to_zip = [DB_USERS_FILE, HISTORY_FILE, SARAKHNI_FILE, STATS_FILE, EVENTS_FILE, 'bot_errors.log']
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
    out = '💾 سجل معدلاتك:\n' + "\n".join([f"📘 {r['semester']} → {r['gpa']:.2f} ({r['type']})" for r in records])
    safe_send_message(chat_id, out)

def show_help(chat_id):
    help_text = (
        "❓ **دليل استخدام بوت UOB Engineers الكامل** 🏛️\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧮 **حاسبة المعدل:**\n"
        "تتيح لك حساب نقاطك ومعدلك الفصلية.\n"
        "• أرسل المواد بنفس السطر (عدد الوحدات ثم التقدير).\n"
        "• مثال:\n  12 BB\n  9 CC\n\n"
        "🎯 **كم أحتاج لهدف معين:**\n"
        "تحدد لك المعدل المطلوب تحقيقه الفصل القادم للوصول لمعدل تراكمي معين.\n"
        "• أرسل 4 أرقام في أسطر متتالية:\n"
        "  1️⃣ معدلك التراكمي الحالي\n"
        "  2️⃣ عدد وحداتك المنجزة\n"
        "  3️⃣ عدد الوحدات التي ستسجلها\n"
        "  4️⃣ المعدل التراكمي المستهدف\n\n"
        "🔄 **أثر تحسين مادة:**\n"
        "حساب التغير في معدلك عند إعادة مادة لرفع تقديرها.\n"
        "• أرسل 5 أسطر متتالية:\n"
        "  1️⃣ معدلك التراكمي الحالي\n"
        "  2️⃣ إجمالي وحداتك\n"
        "  3️⃣ عدد وحدات المادة\n"
        "  4️⃣ التقدير القديم (مثال: DD)\n"
        "  5️⃣ التقدير المتوقع (مثال: AA)\n\n"
        "⚖️ **موازنة مواد فصلي:**\n"
        "تجربة سيناريوهات مختلفة لتوزيع تقديرات مواد الفصل القادم قبل اعتمادها.\n\n"
        "🏆 **ما تقديري؟**\n"
        "أرسل معدلك الرقمي (مثال: 3.25) لمعرفة تقديرك الأكاديمي وسلّم الدرجات بكليتنا.\n\n"
        "💾 **سجل معدلاتي:**\n"
        "يعرض لك جميع المعدلات الفصلية والتراكمية التي قمت بحفظها سابقاً.\n\n"
        "💬 **صارحني:**\n"
        "لإرسال اقتراح، استفسار، أو ملاحظة لإدارة القناة بسرية تامة ودون إظهار اسمك.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 *ملاحظة: يمكنك إدخال الأرقام باللغة العربية أو الإنجليزية.*"
    )
    safe_send_message(chat_id, help_text, parse_mode='Markdown')

# ==========================================
# 11. التشغيل الذاتي
# ==========================================
if __name__ == "__main__":
    logger.info("جاري إعادة تهيئة البوت...")
    try:
        bot.delete_webhook()
    except Exception:
        pass
    
    logger.info("🚀 البوت جاهز ويعمل بسلامة كاملة...")
    bot.infinity_polling(timeout=20, long_polling_timeout=15, logger_level=logging.ERROR)
