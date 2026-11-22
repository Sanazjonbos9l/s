import asyncio
import html
import os
import sqlite3
import tempfile
import time
from datetime import date, datetime

from faster_whisper import WhisperModel
from telethon import Button, TelegramClient, events
from telethon.errors import FloodWaitError, MessageNotModifiedError

# ======================= تنظیمات =======================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
HARD_MAX_MB = 100
ALLOWED_LANGS = {"fa", "en"}
PAGE = 6

# ======================= دیتابیس =======================
db = sqlite3.connect("bot.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, name TEXT, username TEXT,
    joined INTEGER, last_seen INTEGER,
    files INTEGER DEFAULT 0, seconds REAL DEFAULT 0,
    banned INTEGER DEFAULT 0, day TEXT DEFAULT '', day_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""
)
DEFAULTS = {"active": "1", "lang": "auto", "max_mb": "100", "daily": "0"}
ALLOWED_VALUES = {
    "active": {"1", "0"},
    "lang": {"auto", "fa", "en"},
    "max_mb": {"25", "50", "100"},
    "daily": {"0", "5", "20", "50"},
}


def S(key):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else DEFAULTS[key]


def set_S(key, value):
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def touch(u):
    now = int(time.time())
    name = " ".join(filter(None, [u.first_name, u.last_name])) or "—"
    db.execute(
        "INSERT INTO users(id,name,username,joined,last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
        "username=excluded.username, last_seen=excluded.last_seen",
        (u.id, name, u.username, now, now),
    )
    db.commit()


def get_user(uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


# ======================= ابزارها =======================
def fmt_dur(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h} ساعت و {m} دقیقه" if h else f"{m} دقیقه و {s} ثانیه"


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"


def esc(x):
    return html.escape(str(x))


LINE = "━━━━━━━━━━━━━━━━"

# ======================= مدل و کلاینت =======================
print("در حال بارگذاری مدل...")
model = WhisperModel(MODEL_SIZE, device="auto", compute_type="auto")
print("مدل آماده است.")

client = TelegramClient("stt_bot", API_ID, API_HASH)
client.parse_mode = "html"
lock = asyncio.Semaphore(1)
state = {}  # وضعیت گفتگوی ادمین‌ها: uid -> (kind, data)


def transcribe(path, language):
    segments, info = model.transcribe(
        path, language=language, vad_filter=True, beam_size=5
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, info.language, info.duration


def is_audio(msg):
    if msg.voice or msg.audio or msg.video_note or msg.video:
        return True
    d = msg.document
    return bool(d and d.mime_type and d.mime_type.startswith(("audio/", "video/")))


async def show(event, text, buttons=None):
    try:
        await event.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass


# ======================= منوی کاربر =======================
def user_menu(uid):
    rows = [
        [Button.inline("📖 راهنما", b"u:help"), Button.inline("📊 آمار من", b"u:me")]
    ]
    if uid in ADMIN_IDS:
        rows.append([Button.inline("👑 پنل مدیریت", b"a:home")])
    return rows


def welcome_text():
    return (
        "✨ <b>به ربات تبدیل صدا به متن خوش اومدی</b>\n"
        f"{LINE}\n"
        "🎙 ویس، فایل صوتی یا ویدیو بفرست\n"
        "📝 من برات به متن تبدیلش می‌کنم\n"
        "🌐 فارسی و انگلیسی پشتیبانی می‌شود\n"
        f"📦 حداکثر حجم: <b>{S('max_mb')} مگابایت</b>\n"
        f"{LINE}\n"
        "<i>Send a voice or audio file and I'll transcribe it.</i>"
    )


def help_text():
    return (
        "📖 <b>راهنما</b>\n"
        f"{LINE}\n"
        "1️⃣ ویس یا فایل صوتی رو بفرست\n"
        "2️⃣ صبر کن تا پردازش شه ⏳\n"
        "3️⃣ متن آماده رو دریافت کن ✅\n\n"
        "💡 هرچی صدا واضح‌تر باشه، دقت بیشتره.\n"
        "📄 برای متن‌های طولانی، فایل txt هم فرستاده میشه."
    )


def me_text(uid):
    r = get_user(uid)
    today = date.today().isoformat()
    used = r["day_count"] if r["day"] == today else 0
    daily = int(S("daily"))
    limit = "نامحدود" if (daily == 0 or uid in ADMIN_IDS) else str(daily)
    return (
        "📊 <b>آمار من</b>\n"
        f"{LINE}\n"
        f"📁 فایل‌های تبدیل‌شده: <b>{r['files']}</b>\n"
        f"⏱ مجموع مدت صدا: <b>{fmt_dur(r['seconds'])}</b>\n"
        f"📅 امروز: <b>{used}</b> از <b>{limit}</b>\n"
        f"🗓 عضویت: {fmt_ts(r['joined'])}"
    )


# ======================= پنل مدیریت =======================
def admin_home():
    total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    today = date.today().isoformat()
    todays = db.execute(
        "SELECT COALESCE(SUM(day_count),0) s FROM users WHERE day=?", (today,)
    ).fetchone()["s"]
    status = "🟢 فعال" if S("active") == "1" else "🔴 غیرفعال"
    text = (
        "👑 <b>پنل مدیریت</b>\n"
        f"{LINE}\n"
        f"🔌 وضعیت ربات: <b>{status}</b>\n"
        f"👥 کل کاربران: <b>{total}</b>\n"
        f"📁 فایل‌های امروز: <b>{todays}</b>\n"
        f"{LINE}\n"
        "یکی از گزینه‌ها رو انتخاب کن 👇"
    )
    btns = [
        [Button.inline("📊 آمار کامل", b"a:stats"), Button.inline("👥 کاربران", b"a:users:0")],
        [Button.inline("📢 پیام همگانی", b"a:bc"), Button.inline("🔎 مدیریت با آیدی", b"a:banid")],
        [Button.inline("⚙️ تنظیمات", b"a:set")],
        [Button.inline("✖️ بستن", b"a:close")],
    ]
    return text, btns


def admin_stats():
    now = int(time.time())
    sod = int(datetime.combine(date.today(), datetime.min.time()).timestamp())
    q = lambda sql, *a: db.execute(sql, a).fetchone()[0]
    total = q("SELECT COUNT(*) FROM users")
    active24 = q("SELECT COUNT(*) FROM users WHERE last_seen>?", now - 86400)
    new_today = q("SELECT COUNT(*) FROM users WHERE joined>=?", sod)
    banned = q("SELECT COUNT(*) FROM users WHERE banned=1")
    files = q("SELECT COALESCE(SUM(files),0) FROM users")
    secs = q("SELECT COALESCE(SUM(seconds),0) FROM users")
    top = db.execute(
        "SELECT name, files FROM users ORDER BY files DESC LIMIT 3"
    ).fetchall()
    medals = ["🥇", "🥈", "🥉"]
    top_txt = "\n".join(
        f"{medals[i]} {esc(r['name'])} — {r['files']} فایل" for i, r in enumerate(top) if r["files"]
    ) or "—"
    text = (
        "📊 <b>آمار کامل</b>\n"
        f"{LINE}\n"
        f"👥 کل کاربران: <b>{total}</b>\n"
        f"🔥 فعال در ۲۴ ساعت: <b>{active24}</b>\n"
        f"🆕 جدید امروز: <b>{new_today}</b>\n"
        f"🚫 مسدود: <b>{banned}</b>\n"
        f"{LINE}\n"
        f"📁 کل فایل‌ها: <b>{files}</b>\n"
        f"⏱ مجموع صدا: <b>{fmt_dur(secs)}</b>\n"
        f"{LINE}\n"
        f"🏆 <b>برترین کاربران</b>\n{top_txt}"
    )
    return text, [[Button.inline("🔄 بروزرسانی", b"a:stats")], [Button.inline("🔙 بازگشت", b"a:home")]]


def admin_users(page):
    total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    rows = db.execute(
        "SELECT * FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?",
        (PAGE, page * PAGE),
    ).fetchall()
    btns = [
        [
            Button.inline(
                f"{'🚫' if r['banned'] else '👤'} {r['name'][:18]} · {r['files']}",
                f"a:user:{r['id']}:{page}".encode(),
            )
        ]
        for r in rows
    ]
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ قبلی", f"a:users:{page - 1}".encode()))
    nav.append(Button.inline(f"{page + 1}/{pages}", b"noop"))
    if page < pages - 1:
        nav.append(Button.inline("بعدی ▶️", f"a:users:{page + 1}".encode()))
    btns.append(nav)
    btns.append([Button.inline("🔙 بازگشت", b"a:home")])
    text = f"👥 <b>کاربران</b> ({total})\n{LINE}\nروی هر کاربر بزن تا مدیریتش کنی 👇"
    return text, btns


def admin_user(uid, page):
    r = get_user(uid)
    back = [Button.inline("🔙 لیست کاربران", f"a:users:{page}".encode())]
    if not r:
        return "❌ کاربر پیدا نشد.", [back]
    uname = f"@{esc(r['username'])}" if r["username"] else "—"
    status = "🚫 مسدود" if r["banned"] else "✅ آزاد"
    text = (
        f"👤 <b>{esc(r['name'])}</b>\n"
        f"{LINE}\n"
        f"🆔 آیدی: <code>{r['id']}</code>\n"
        f"🔗 یوزرنیم: {uname}\n"
        f"📌 وضعیت: <b>{status}</b>\n"
        f"📁 فایل‌ها: <b>{r['files']}</b>\n"
        f"⏱ مجموع صدا: <b>{fmt_dur(r['seconds'])}</b>\n"
        f"🗓 عضویت: {fmt_ts(r['joined'])}\n"
        f"👁 آخرین بازدید: {fmt_ts(r['last_seen'])}"
    )
    btns = []
    if r["id"] not in ADMIN_IDS:
        label = "✅ آزادسازی کاربر" if r["banned"] else "🚫 مسدود کردن"
        btns.append([Button.inline(label, f"a:ban:{uid}:{page}".encode())])
    btns.append(back)
    return text, btns


def admin_settings():
    def opt(label, key, val):
        mark = "✅ " if S(key) == val else ""
        return Button.inline(f"{mark}{label}", f"a:set:{key}:{val}".encode())

    text = (
        "⚙️ <b>تنظیمات ربات</b>\n"
        f"{LINE}\n"
        "هر گزینه‌ای که بزنی، همون لحظه اعمال میشه ✨"
    )
    btns = [
        [Button.inline("🔌 وضعیت ربات", b"noop")],
        [opt("فعال", "active", "1"), opt("غیرفعال", "active", "0")],
        [Button.inline("🌐 زبان تشخیص", b"noop")],
        [opt("خودکار", "lang", "auto"), opt("فارسی", "lang", "fa"), opt("English", "lang", "en")],
        [Button.inline("📦 حداکثر حجم فایل", b"noop")],
        [opt("25MB", "max_mb", "25"), opt("50MB", "max_mb", "50"), opt("100MB", "max_mb", "100")],
        [Button.inline("⏱ سقف روزانه هر کاربر", b"noop")],
        [opt("∞", "daily", "0"), opt("5", "daily", "5"), opt("20", "daily", "20"), opt("50", "daily", "50")],
        [Button.inline("🔙 بازگشت", b"a:home")],
    ]
    return text, btns


async def do_broadcast(event, msg):
    ids = [r["id"] for r in db.execute("SELECT id FROM users WHERE banned=0")]
    ok = fail = 0
    total = len(ids)
    for i, uid in enumerate(ids, 1):
        try:
            await client.send_message(uid, msg)
            ok += 1
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            try:
                await client.send_message(uid, msg)
                ok += 1
            except Exception:
                fail += 1
        except Exception:
            fail += 1
        if i % 20 == 0:
            await show(event, f"📤 در حال ارسال...\n{LINE}\n{i} / {total}")
        await asyncio.sleep(0.05)
    await show(
        event,
        f"✅ <b>ارسال همگانی تمام شد</b>\n{LINE}\n"
        f"📬 موفق: <b>{ok}</b>\n❌ ناموفق: <b>{fail}</b>",
        [[Button.inline("🔙 پنل مدیریت", b"a:home")]],
    )


# ======================= دستورات =======================
@client.on(events.NewMessage(pattern=r"^/start", func=lambda e: e.is_private))
async def cmd_start(event):
    sender = await event.get_sender()
    touch(sender)
    await event.reply(welcome_text(), buttons=user_menu(sender.id))


@client.on(events.NewMessage(pattern=r"^/admin", func=lambda e: e.is_private))
async def cmd_admin(event):
    if event.sender_id not in ADMIN_IDS:
        return
    state.pop(event.sender_id, None)
    text, btns = admin_home()
    await event.reply(text, buttons=btns)


@client.on(events.NewMessage(pattern=r"^/cancel", func=lambda e: e.is_private))
async def cmd_cancel(event):
    state.pop(event.sender_id, None)
    await event.reply("✅ لغو شد.")


# ======================= دکمه‌ها =======================
@client.on(events.CallbackQuery())
async def on_callback(event):
    data = event.data.decode()
    uid = event.sender_id
    p = data.split(":")

    if data == "noop":
        await event.answer()
        return

    # ----- کاربر -----
    if p[0] == "u":
        back = [[Button.inline("🔙 منو", b"u:menu")]]
        if p[1] == "help":
            await show(event, help_text(), back)
        elif p[1] == "me":
            if not get_user(uid):
                await event.answer("ابتدا /start بزن", alert=True)
                return
            await show(event, me_text(uid), back)
        elif p[1] == "menu":
            await show(event, welcome_text(), user_menu(uid))
        await event.answer()
        return

    # ----- مدیر -----
    if p[0] != "a":
        return
    if uid not in ADMIN_IDS:
        await event.answer("⛔ دسترسی ندارید", alert=True)
        return

    act = p[1]
    if act == "home":
        state.pop(uid, None)
        await show(event, *admin_home())
    elif act == "close":
        state.pop(uid, None)
        await event.delete()
        return
    elif act == "stats":
        await show(event, *admin_stats())
    elif act == "users":
        await show(event, *admin_users(int(p[2])))
    elif act == "user":
        await show(event, *admin_user(int(p[2]), int(p[3])))
    elif act == "ban":
        target, page = int(p[2]), int(p[3])
        if target not in ADMIN_IDS:
            db.execute("UPDATE users SET banned = 1 - banned WHERE id=?", (target,))
            db.commit()
        await show(event, *admin_user(target, page))
    elif act == "set":
        if len(p) == 4 and p[3] in ALLOWED_VALUES.get(p[2], set()):
            set_S(p[2], p[3])
        await show(event, *admin_settings())
    elif act == "bc":
        state[uid] = ("bc", None)
        await show(
            event,
            f"📢 <b>پیام همگانی</b>\n{LINE}\n"
            "پیامت رو همین‌جا بفرست (متن، عکس، ویدیو و ...).\n"
            "بعدش پیش‌نمایش و تأیید نهایی میاد.",
            [[Button.inline("❌ لغو", b"a:home")]],
        )
    elif act == "bcgo":
        st = state.pop(uid, None)
        if not st or st[0] != "bc_ready":
            await event.answer("⌛️ منقضی شده، دوباره بفرست.", alert=True)
            return
        await event.answer("🚀 ارسال شروع شد")
        await do_broadcast(event, st[1])
        return
    elif act == "banid":
        state[uid] = ("banid", None)
        await show(
            event,
            f"🔎 <b>مدیریت با آیدی</b>\n{LINE}\nآیدی عددی کاربر رو بفرست.",
            [[Button.inline("❌ لغو", b"a:home")]],
        )
    await event.answer()


# ======================= پیام‌ها =======================
async def handle_state(event, uid):
    kind = state[uid][0]
    if kind in ("bc", "bc_ready"):
        state[uid] = ("bc_ready", event.message)
        n = db.execute("SELECT COUNT(*) c FROM users WHERE banned=0").fetchone()["c"]
        await event.reply(
            f"👆 این پیام برای <b>{n}</b> کاربر ارسال میشه.\nتأیید می‌کنی؟",
            buttons=[
                [Button.inline("✅ ارسال همگانی", b"a:bcgo"), Button.inline("❌ لغو", b"a:home")]
            ],
            reply_to=event.id,
        )
    elif kind == "banid":
        txt = (event.raw_text or "").strip()
        if not txt.isdigit() or not get_user(int(txt)):
            await event.reply("❌ کاربری با این آیدی پیدا نشد. دوباره بفرست یا /cancel")
            return
        state.pop(uid, None)
        text, btns = admin_user(int(txt), 0)
        await event.reply(text, buttons=btns)


@client.on(
    events.NewMessage(
        func=lambda e: e.is_private and not (e.raw_text or "").startswith("/")
    )
)
async def on_message(event):
    sender = await event.get_sender()
    uid = sender.id
    touch(sender)
    is_admin = uid in ADMIN_IDS

    if is_admin and uid in state:
        await handle_state(event, uid)
        return

    row = get_user(uid)
    if row["banned"] and not is_admin:
        await event.reply("⛔ دسترسی شما به ربات مسدود شده است.")
        return
    if S("active") != "1" and not is_admin:
        await event.reply("🔧 ربات موقتاً غیرفعال است. لطفاً بعداً امتحان کن.")
        return

    msg = event.message
    if not is_audio(msg):
        await event.reply("🎙 لطفاً یک ویس یا فایل صوتی بفرست.", buttons=user_menu(uid))
        return

    today = date.today().isoformat()
    used = row["day_count"] if row["day"] == today else 0
    daily = int(S("daily"))
    if daily and used >= daily and not is_admin:
        await event.reply(f"📅 سقف روزانه ({daily} فایل) تموم شده. فردا دوباره بیا 🌙")
        return

    max_mb = min(int(S("max_mb")), HARD_MAX_MB)
    size = (msg.file.size if msg.file else 0) or 0
    if size > max_mb * 1024 * 1024:
        await event.reply(f"❌ حجم فایل بیشتر از <b>{max_mb} مگابایت</b> است.")
        return

    status = await event.reply("⏳ در صف پردازش...")
    forced = S("lang")
    async with lock:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                await status.edit("⬇️ در حال دانلود فایل...")
                path = await client.download_media(msg, file=tmp + "/")
                await status.edit("🎧 در حال تبدیل به متن...")
                text, lang, dur = await asyncio.to_thread(
                    transcribe, path, None if forced == "auto" else forced
                )
            except Exception as e:
                await status.edit(f"❌ خطا: {esc(e)}")
                return

    if forced == "auto" and lang not in ALLOWED_LANGS:
        await status.edit(f"⚠️ زبان تشخیص‌داده‌شده ({esc(lang)}) پشتیبانی نمی‌شود. فقط فارسی و انگلیسی.")
        return
    if not text:
        await status.edit("❌ متنی در فایل تشخیص داده نشد.")
        return

    db.execute(
        "UPDATE users SET files=files+1, seconds=seconds+?, day=?, day_count=? WHERE id=?",
        (dur, today, used + 1, uid),
    )
    db.commit()

    await status.delete()
    flag = "🇮🇷 فارسی" if lang == "fa" else "🇬🇧 English"
    header = f"<b>{flag}</b> · ⏱ {fmt_dur(dur)}\n{LINE}\n"
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)]
    for i, c in enumerate(chunks):
        await event.reply((header if i == 0 else "") + esc(c))

    if len(chunks) > 1:
        out = os.path.join(tempfile.gettempdir(), f"transcript_{msg.id}.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        await event.reply("📄 فایل متن کامل:", file=out)
        os.remove(out)


if __name__ == "__main__":
    if not ADMIN_IDS:
        print("⚠️ ADMIN_IDS تنظیم نشده؛ پنل مدیریت در دسترس نخواهد بود.")
    client.start(bot_token=BOT_TOKEN)
    print("ربات فعال شد.")
    client.run_until_disconnected()
