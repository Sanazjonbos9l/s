#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تبدیل متن به صدا  (@txtmhrnbot)  —  نسخه ۳

امکانات:
  • زبان / لهجه / صدای زن، مرد، دختر بچه، پسر بچه + حالت مسیریاب (/navi)
  • ایموجی و دکمه‌های پریمیوم (اگر سازنده ربات پریمیوم داشته باشد)
  • پنل مدیریت (/admin): آمار، ارسال همگانی، فوروارد همگانی، بکاپ و بازگردانی،
    عضویت اجباری، حالت رایگان/اشتراکی، اشتراک کاربر، مسدود/آزاد، تنظیمات

نصب:
    pip install "python-telegram-bot[job-queue]>=22.7" edge-tts

اجرا:
    python tts_bot_v3.py

(بهتره توکن رو به جای داخل کد، با متغیر محیطی BOT_TOKEN بدی.)
"""

import asyncio
import base64
import html
import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime
from urllib.parse import quote

import edge_tts
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

# ═════════════════════════ تنظیمات ═════════════════════════
BOT_TOKEN = os.environ["BOT_TOKEN"]  # توکن رو در تب Environment پنل هاست بگذار
OWNER_ID = int(os.getenv("OWNER_ID", "5234934574"))  # مدیر اصلی
ADMIN_IDS = {OWNER_ID} | {
    int(x) for x in os.getenv("EXTRA_ADMINS", "").split(",") if x.strip().isdigit()
}
PREMIUM_MODE = os.getenv("PREMIUM_EMOJI", "auto").lower()  # auto | on | off
DB_PATH = os.getenv("DB_PATH", "bot.db")
MAX_CHARS = 1500
H = ParseMode.HTML

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tts-bot")

# ═════════════════════════ ایموجی‌ها ═════════════════════════
# (ایموجی معمولی, آیدی ایموجی پریمیوم) — آیدی‌ها رو خودت پر کن (راهنما در پیام)
EMOJI = {
    "wave": ("👋", ""), "mic": ("🎙", ""), "world": ("🌍", ""), "accent": ("🗣", ""),
    "woman": ("👩", ""), "man": ("👨", ""), "girl": ("👧", ""), "boy": ("👦", ""),
    "speed": ("⏩", ""), "pitch": ("🎚", ""), "back": ("⬅️", ""), "ok": ("✅", ""),
    "error": ("❌", ""), "warn": ("⚠️", ""), "gear": ("🎛", ""), "voice": ("🎤", ""),
    "stats": ("📊", ""), "backup": ("💾", ""), "bc": ("📢", ""), "fw": ("🔁", ""),
    "lock": ("🔒", ""), "card": ("💳", ""), "gift": ("🎁", ""), "ban": ("🚫", ""),
    "restore": ("📥", ""), "cancel": ("✖️", ""), "check": ("✅", ""), "user": ("👤", ""),
    "admin": ("🛠", ""),
}

STATE = {"premium": False}
CUSTOM_IDS = {}  # آیدی ایموجی‌های پریمیوم ثبت‌شده از پنل (در دیتابیس ذخیره میشه)
PSTATE_FILE = "premium_state.json"


def load_premium_state():
    if PREMIUM_MODE == "on":
        STATE["premium"] = True
    elif PREMIUM_MODE == "off":
        STATE["premium"] = False
    elif os.path.exists(PSTATE_FILE):
        try:
            with open(PSTATE_FILE, "r", encoding="utf-8") as f:
                STATE["premium"] = bool(json.load(f).get("premium"))
        except Exception:
            pass


def save_premium_state():
    try:
        with open(PSTATE_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE, f)
    except Exception:
        log.exception("premium state save failed")


def E(key: str) -> str:
    fb, cid = EMOJI[key][0], CUSTOM_IDS.get(key) or EMOJI[key][1]
    if STATE["premium"] and cid:
        return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>'
    return fb


def B(key, label, data, style=None):
    """style: primary(آبی) | success(سبز) | danger(قرمز)"""
    fb, cid = EMOJI[key][0], CUSTOM_IDS.get(key) or EMOJI[key][1]
    if STATE["premium"] and cid:
        return InlineKeyboardButton(label, callback_data=data, icon_custom_emoji_id=cid, style=style)
    return InlineKeyboardButton(f"{fb} {label}", callback_data=data, style=style)


def plain(label, data, style=None):
    return InlineKeyboardButton(label, callback_data=data, style=style)


def chunk(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]


# ═════════════════════════ دیتابیس ═════════════════════════
def db_exec(sql, args=()):
    with closing(sqlite3.connect(DB_PATH)) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def db_all(sql, args=()):
    with closing(sqlite3.connect(DB_PATH)) as c:
        c.row_factory = sqlite3.Row
        return c.execute(sql, args).fetchall()


def db_one(sql, args=()):
    rows = db_all(sql, args)
    return rows[0] if rows else None


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, name TEXT, username TEXT,
                joined INTEGER, last_seen INTEGER,
                banned INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,
                sub_until INTEGER DEFAULT 0,
                used_date TEXT DEFAULT '', used_count INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0, settings TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS channels(
                chat_id INTEGER PRIMARY KEY, title TEXT, link TEXT
            );
            CREATE TABLE IF NOT EXISTS engines(
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
                config TEXT, enabled INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS custom_voices(
                id INTEGER PRIMARY KEY AUTOINCREMENT, engine_id INTEGER,
                name TEXT, category TEXT, kind TEXT, param TEXT,
                enabled INTEGER DEFAULT 1
            );
            """
        )
        c.commit()


KV_DEFAULTS = {
    "emoji_ids": "{}",
    "mode": "free",          # free | paid
    "daily_free": "3",       # تعداد رایگان روزانه در حالت اشتراکی
    "support": "@",          # آیدی پشتیبانی برای خرید
    "price": "برای خرید اشتراک به پشتیبانی پیام بده.",
}


def get_kv(k):
    row = db_one("SELECT v FROM kv WHERE k=?", (k,))
    return row["v"] if row else KV_DEFAULTS[k]


def set_kv(k, v):
    db_exec("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def load_emoji_ids():
    CUSTOM_IDS.clear()
    try:
        CUSTOM_IDS.update(json.loads(get_kv("emoji_ids")))
    except Exception:
        pass


DEFAULT_S = {"voice": "fa-IR-DilaraNeural", "mode": "normal",
             "child_gender": "Female", "rate": 0, "pitch": 0}


def get_s(uid):
    row = db_one("SELECT settings FROM users WHERE id=?", (uid,))
    s = dict(DEFAULT_S)
    if row:
        try:
            s.update(json.loads(row["settings"] or "{}"))
        except Exception:
            pass
    return s


def save_s(uid, s):
    db_exec("UPDATE users SET settings=? WHERE id=?", (json.dumps(s), uid))


def is_admin(uid):
    return uid in ADMIN_IDS


# ═════════════════════════ اشتراک / سهمیه ═════════════════════════
def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def can_use(uid):
    if is_admin(uid) or get_kv("mode") != "paid":
        return True
    row = db_one("SELECT sub_until, used_date, used_count FROM users WHERE id=?", (uid,))
    if row and row["sub_until"] > time.time():
        return True
    used = row["used_count"] if row and row["used_date"] == today_str() else 0
    return used < int(get_kv("daily_free"))


def mark_use(uid):
    row = db_one("SELECT used_date, used_count FROM users WHERE id=?", (uid,))
    used = row["used_count"] + 1 if row and row["used_date"] == today_str() else 1
    db_exec("UPDATE users SET total=total+1, used_date=?, used_count=? WHERE id=?",
            (today_str(), used, uid))


def account_text(uid):
    row = db_one("SELECT sub_until, used_date, used_count, total FROM users WHERE id=?", (uid,))
    lines = [f"{E('user')} آیدی عددی: <code>{uid}</code>"]
    if get_kv("mode") == "paid":
        if row and row["sub_until"] > time.time():
            exp = datetime.fromtimestamp(row["sub_until"]).strftime("%Y-%m-%d")
            lines.append(f"{E('card')} اشتراک فعال تا: {exp}")
        else:
            limit = int(get_kv("daily_free"))
            used = row["used_count"] if row and row["used_date"] == today_str() else 0
            lines.append(f"{E('card')} بدون اشتراک — سهمیه امروز: {used}/{limit}")
    else:
        lines.append(f"{E('gift')} ربات فعلاً رایگانه")
    lines.append(f"{E('stats')} مجموع تبدیل‌ها: {row['total'] if row else 0}")
    return "\n".join(lines)


def denied_text(uid):
    return (
        f"{E('lock')} سهمیه رایگان امروزت تموم شده.\n\n"
        f"{html.escape(get_kv('price'))}\n"
        f"پشتیبانی: {html.escape(get_kv('support'))}\n"
        f"آیدی عددی تو: <code>{uid}</code>"
    )


# ═════════════════════════ عضویت اجباری ═════════════════════════
async def not_joined(bot, uid):
    missing = []
    for c in db_all("SELECT * FROM channels"):
        try:
            m = await bot.get_chat_member(c["chat_id"], uid)
            if m.status in ("left", "kicked"):
                missing.append(c)
        except TelegramError:
            continue  # ربات ادمین کانال نیست → رد میشه
    return missing


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if is_admin(uid):
        return True
    missing = await not_joined(context.bot, uid)
    if not missing:
        return True
    rows = [[InlineKeyboardButton(f"📢 {c['title']}", url=c["link"])] for c in missing if c["link"]]
    rows.append([B("check", "عضو شدم", "JOINCHK", "success")])
    await update.effective_message.reply_html(
        f"{E('lock')} برای استفاده از ربات اول در کانال‌های زیر عضو شو، بعد «عضو شدم» رو بزن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return False


# ═════════════════════════ ثبت کاربر / مسدودی ═════════════════════════
async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.is_bot:
        return
    now = int(time.time())
    row = db_one("SELECT banned FROM users WHERE id=?", (user.id,))
    if row is None:
        db_exec("INSERT INTO users(id,name,username,joined,last_seen) VALUES(?,?,?,?,?)",
                (user.id, user.full_name, user.username or "", now, now))
    else:
        db_exec("UPDATE users SET name=?, username=?, last_seen=?, blocked=0 WHERE id=?",
                (user.full_name, user.username or "", now, user.id))

    if PREMIUM_MODE == "auto" and user.id == OWNER_ID:
        new = bool(user.is_premium)
        if new != STATE["premium"]:
            STATE["premium"] = new
            save_premium_state()
            log.info("premium emoji mode: %s", new)

    if row and row["banned"] and not is_admin(user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ دسترسی شما به ربات مسدود شده.")
        raise ApplicationHandlerStop


# ═════════════════════════ داده‌های صدا ═════════════════════════
LANGUAGES = {
    "fa": "🇮🇷 فارسی", "en": "🇬🇧 انگلیسی", "ar": "🇸🇦 عربی",
    "tr": "🇹🇷 ترکی استانبولی", "az": "🇦🇿 ترکی آذری", "es": "🇪🇸 اسپانیایی",
    "fr": "🇫🇷 فرانسوی", "de": "🇩🇪 آلمانی", "ru": "🇷🇺 روسی",
    "it": "🇮🇹 ایتالیایی", "pt": "🇧🇷 پرتغالی", "hi": "🇮🇳 هندی",
    "ur": "🇵🇰 اردو", "zh": "🇨🇳 چینی", "ja": "🇯🇵 ژاپنی", "ko": "🇰🇷 کره‌ای",
}
REGION_NAMES = {
    "IR": "ایران", "US": "آمریکا", "GB": "بریتانیا", "AU": "استرالیا", "CA": "کانادا",
    "IN": "هند", "IE": "ایرلند", "ZA": "آفریقای جنوبی", "NZ": "نیوزیلند",
    "SA": "عربستان", "EG": "مصر", "AE": "امارات", "IQ": "عراق", "SY": "سوریه",
    "JO": "اردن", "KW": "کویت", "LB": "لبنان", "LY": "لیبی", "MA": "مراکش",
    "DZ": "الجزایر", "TN": "تونس", "QA": "قطر", "BH": "بحرین", "OM": "عمان",
    "YE": "یمن", "TR": "ترکیه", "AZ": "آذربایجان", "ES": "اسپانیا", "MX": "مکزیک",
    "AR": "آرژانتین", "CO": "کلمبیا", "CL": "شیلی", "PE": "پرو", "VE": "ونزوئلا",
    "CU": "کوبا", "FR": "فرانسه", "BE": "بلژیک", "CH": "سوئیس", "DE": "آلمان",
    "AT": "اتریش", "RU": "روسیه", "IT": "ایتالیا", "BR": "برزیل", "PT": "پرتغال",
    "PK": "پاکستان", "CN": "چین", "TW": "تایوان", "HK": "هنگ‌کنگ", "JP": "ژاپن",
    "KR": "کره جنوبی",
}
CHILD = {"Female": {"pitch": 45, "rate": 8}, "Male": {"pitch": 60, "rate": 12}}
_voices_cache = None


async def get_voices():
    global _voices_cache
    if _voices_cache is None:
        _voices_cache = await edge_tts.list_voices()
    return _voices_cache


def region_label(locale):
    code = locale.split("-")[-1]
    return REGION_NAMES.get(code, code)


def voice_name(short):
    return short.split("-")[-1].replace("Neural", "")


def languages_markup():
    rows = chunk([plain(n, f"L:{c}") for c, n in LANGUAGES.items()], 2)
    if custom_categories():
        rows.append([B("voice", "صداهای ویژه", "CVL", "primary")])
    return InlineKeyboardMarkup(rows)


# ═════════════════════════ موتورها و صداهای سفارشی ═════════════════════════
ENGINE_EXAMPLE = (
    "name: نام موتور\n"
    "url: https://api.example.com/tts\n"
    "method: POST\n"
    'headers: {"Authorization": "Bearer KEY"}\n'
    'body: {"text": "{text}", "voice": "{voice}"}\n'
    "format: json\n"
    "response: audio"
)
ENGINE_HELP = (
    "🎙 <b>افزودن موتور (API)</b>\nهر خط یک مورد، به این شکل بفرست:\n\n"
    f"<code>{html.escape(ENGINE_EXAMPLE)}</code>\n\n"
    "• <b>method</b>: GET یا POST (پیش‌فرض POST)\n"
    "• <b>format</b>: json یا form (پیش‌فرض json)\n"
    "• <b>response</b>: <code>audio</code> (خروجی مستقیم فایل صوتی) | "
    "<code>base64:data.audio</code> (مسیر فیلد base64 در JSON) | "
    "<code>url:data.link</code> (مسیر لینک فایل در JSON)\n"
    "• headers و body اختیاری‌اند. هرجا لازمه <code>{text}</code> و <code>{voice}</code> بنویس "
    "(در url هم قابل استفاده‌اند)."
)
VOICE_HELP = (
    "🎙 <b>افزودن صدا</b>\nهر خط یک صدا:\n"
    "<code>شناسه‌موتور | نام نمایشی | دسته | نوع | مقدار voice</code>\n\n"
    "مثال:\n<code>1 | مهتاب | اصفهانی | زن | mahtab\n1 | مهرداد | اصفهانی | مرد | mehrdad</code>\n\n"
    "نوع: زن / مرد / دختر / پسر"
)
KINDS = {"woman": "woman", "زن": "woman", "man": "man", "مرد": "man",
         "girl": "girl", "دختر": "girl", "دختر بچه": "girl",
         "boy": "boy", "پسر": "boy", "پسر بچه": "boy"}


def parse_engine(text):
    cfg = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            cfg[k.strip().lower()] = v.strip()
    if not cfg.get("name") or not cfg.get("url"):
        raise ValueError("name و url الزامی هستند.")
    out = {
        "name": cfg["name"], "url": cfg["url"],
        "method": cfg.get("method", "POST").upper(),
        "format": cfg.get("format", "json").lower(),
        "response": cfg.get("response", "audio"),
    }
    if out["method"] not in ("GET", "POST"):
        raise ValueError("method باید GET یا POST باشد.")
    if out["format"] not in ("json", "form"):
        raise ValueError("format باید json یا form باشد.")
    r = out["response"]
    if not (r == "audio" or r.startswith(("base64:", "url:"))):
        raise ValueError("response نامعتبر است (audio | base64:مسیر | url:مسیر).")
    for k in ("headers", "body"):
        if cfg.get(k) and cfg[k] != "-":
            try:
                val = json.loads(cfg[k])
            except Exception:
                raise ValueError(f"{k} باید JSON معتبر در یک خط باشد.")
            if not isinstance(val, dict):
                raise ValueError(f"{k} باید یک آبجکت JSON باشد.")
            out[k] = val
    return out


def parse_voices(text):
    added, errors = 0, []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        p = [x.strip() for x in ln.split("|")]
        if (len(p) != 5 or not p[0].isdigit() or p[3] not in KINDS
                or not db_one("SELECT 1 FROM engines WHERE id=?", (int(p[0]),))):
            errors.append(ln)
            continue
        db_exec("INSERT INTO custom_voices(engine_id,name,category,kind,param) VALUES(?,?,?,?,?)",
                (int(p[0]), p[1], p[2], KINDS[p[3]], p[4]))
        added += 1
    return added, errors


def _fill(obj, mp, quote_it=False):
    if isinstance(obj, str):
        for k, v in mp.items():
            obj = obj.replace("{" + k + "}", quote(v, safe="") if quote_it else v)
        return obj
    if isinstance(obj, dict):
        return {k: _fill(v, mp, quote_it) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, mp, quote_it) for v in obj]
    return obj


def _dig(data, path):
    for part in path.split("."):
        data = data[int(part)] if part.isdigit() else data[part]
    return data


async def custom_tts(voice_id: int, text: str) -> bytes:
    row = db_one(
        """SELECT v.param, e.config FROM custom_voices v JOIN engines e ON e.id=v.engine_id
           WHERE v.id=? AND v.enabled=1 AND e.enabled=1""", (voice_id,))
    if not row:
        raise ValueError("این صدا حذف یا غیرفعال شده. با /voice یک صدای دیگه انتخاب کن.")
    cfg = json.loads(row["config"])
    mp = {"text": text, "voice": row["param"]}
    url = _fill(cfg["url"], mp, quote_it=True)
    headers = _fill(cfg.get("headers", {}), mp)
    body = _fill(cfg["body"], mp) if cfg.get("body") else None
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
        if cfg["method"] == "GET":
            r = await cli.get(url, headers=headers)
        elif cfg["format"] == "form":
            r = await cli.post(url, headers=headers, data=body)
        else:
            r = await cli.post(url, headers=headers, json=body)
        r.raise_for_status()
        mode = cfg["response"]
        if mode == "audio":
            return r.content
        val = _dig(r.json(), mode.split(":", 1)[1])
        if mode.startswith("base64:"):
            return base64.b64decode(val)
        r2 = await cli.get(val)
        r2.raise_for_status()
        return r2.content


def sniff_ext(b: bytes) -> str:
    if b[:4] == b"OggS":
        return "ogg"
    if b[:3] == b"ID3" or b[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if b[4:8] == b"ftyp":
        return "m4a"
    if b[:4] == b"RIFF":
        return "wav"
    return "bin"


async def reply_audio_bytes(msg, data: bytes):
    ext = sniff_ext(data)
    bio = io.BytesIO(data)
    bio.name = f"voice.{ext}"
    if ext in ("ogg", "mp3", "m4a"):
        await msg.reply_voice(voice=bio)
    else:
        await msg.reply_document(document=bio)


CV_JOIN = "FROM custom_voices v JOIN engines e ON e.id=v.engine_id WHERE v.enabled=1 AND e.enabled=1"


def custom_categories():
    return [r["c"] for r in db_all(f"SELECT DISTINCT v.category c {CV_JOIN} ORDER BY v.category")]


# ═════════════════════════ دستورات کاربر ═════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context):
        return
    text = (
        f"سلام {E('wave')}\n"
        f"متنت رو بفرست تا برات به <b>ویس</b> تبدیلش کنم {E('mic')}\n\n"
        f"{E('voice')} /voice ← زبان، لهجه و صدا\n"
        f"{E('voice')} /navi ← گوینده به سبک مسیریاب\n"
        f"{E('speed')} /speed ← سرعت\n"
        f"{E('pitch')} /pitch ← زیر و بمی\n"
        f"{E('gear')} /current ← تنظیمات فعلی\n"
        f"{E('user')} /me ← حساب من"
    )
    if is_admin(update.effective_user.id):
        text += f"\n{E('admin')} /admin ← پنل مدیریت"
    await update.message.reply_html(text)


async def me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(account_text(update.effective_user.id))


async def voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context):
        return
    await update.message.reply_html(f"{E('world')} زبان رو انتخاب کن:", reply_markup=languages_markup())


async def navi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context):
        return
    rows = [
        [B("woman", "زن", "N:woman", "primary"), B("man", "مرد", "N:man", "primary")],
        [B("girl", "دختر بچه", "N:girl", "success"), B("boy", "پسر بچه", "N:boy", "success")],
    ]
    await update.message.reply_html(
        f"{E('voice')} گوینده به سبک مسیریاب رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows)
    )


async def current_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_s(update.effective_user.id)
    mode = "کودک" if s["mode"] == "child" else "عادی"
    await update.message.reply_html(
        f"{E('gear')} <b>تنظیمات فعلی</b>\nصدا: <code>{s['voice']}</code>\nحالت: {mode}\n"
        f"سرعت: {s['rate']:+d}%\nزیر و بمی: {s['pitch']:+d}Hz"
    )


async def speed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [B("speed", f"{v:+d}%", f"R:{v}") for v in [-50, -25, 0, 25, 50, 100]]
    await update.message.reply_html(f"{E('speed')} سرعت صدا:", reply_markup=InlineKeyboardMarkup(chunk(btns, 3)))


async def pitch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [B("pitch", f"{v:+d}Hz", f"P:{v}") for v in [-40, -20, 0, 20, 40, 80]]
    await update.message.reply_html(f"{E('pitch')} زیر و بمی صدا:", reply_markup=InlineKeyboardMarkup(chunk(btns, 3)))


# ═════════════════════════ دکمه‌های کاربر ═════════════════════════
async def safe_edit(q, text, markup=None):
    try:
        await q.edit_message_text(text, reply_markup=markup, parse_mode=H)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data

    if data == "JOINCHK":
        if await not_joined(context.bot, uid):
            await q.answer("هنوز در همه کانال‌ها عضو نشدی ❗", show_alert=True)
            return
        await q.answer()
        await safe_edit(q, f"{E('ok')} عضویت تایید شد! حالا متنت رو بفرست.")
        return

    await q.answer()
    s = get_s(uid)
    voices = await get_voices() if data[:2] in ("L:", "C:", "G:", "K:") else None

    if data.startswith("L:"):
        lang = data[2:]
        locales = sorted({v["Locale"] for v in voices if v["Locale"].lower().startswith(lang + "-")})
        if not locales:
            await safe_edit(q, "برای این زبان صدایی پیدا نشد.")
            return
        rows = chunk([plain(region_label(l), f"C:{l}") for l in locales], 3)
        rows.append([B("back", "بازگشت", "BACK")])
        await safe_edit(q, f"{E('accent')} لهجه / کشور رو انتخاب کن:", InlineKeyboardMarkup(rows))

    elif data.startswith("C:"):
        locale = data[2:]
        genders = {v["Gender"] for v in voices if v["Locale"] == locale}
        r1, r2 = [], []
        if "Female" in genders:
            r1.append(B("woman", "زن", f"G:{locale}:Female", "primary"))
            r2.append(B("girl", "دختر بچه", f"K:{locale}:Female", "success"))
        if "Male" in genders:
            r1.append(B("man", "مرد", f"G:{locale}:Male", "primary"))
            r2.append(B("boy", "پسر بچه", f"K:{locale}:Male", "success"))
        rows = [r for r in (r1, r2) if r] + [[B("back", "بازگشت", "BACK")]]
        await safe_edit(q, f"{E('voice')} نوع صدا ({region_label(locale)}):", InlineKeyboardMarkup(rows))

    elif data.startswith("G:"):
        _, locale, gender = data.split(":")
        vs = [v for v in voices if v["Locale"] == locale and v["Gender"] == gender]
        if len(vs) == 1:
            s["voice"], s["mode"] = vs[0]["ShortName"], "normal"
            save_s(uid, s)
            await safe_edit(q, f"{E('ok')} صدا تنظیم شد: <code>{s['voice']}</code>\nحالا متنت رو بفرست.")
            return
        key = "woman" if gender == "Female" else "man"
        rows = chunk([B(key, voice_name(v["ShortName"]), f"V:{v['ShortName']}") for v in vs], 2)
        rows.append([B("back", "بازگشت", f"C:{locale}")])
        await safe_edit(q, f"{E('voice')} گوینده رو انتخاب کن:", InlineKeyboardMarkup(rows))

    elif data.startswith("V:"):
        s["voice"], s["mode"] = data[2:], "normal"
        save_s(uid, s)
        await safe_edit(q, f"{E('ok')} صدا تنظیم شد: <code>{s['voice']}</code>\nحالا متنت رو بفرست.")

    elif data.startswith("K:"):
        _, locale, gender = data.split(":")
        vs = [v for v in voices if v["Locale"] == locale and v["Gender"] == gender]
        s["voice"], s["mode"], s["child_gender"] = vs[0]["ShortName"], "child", gender
        save_s(uid, s)
        kind = f"دختر بچه {E('girl')}" if gender == "Female" else f"پسر بچه {E('boy')}"
        await safe_edit(q, f"{E('ok')} صدای {kind} فعال شد.\nحالا متنت رو بفرست.")

    elif data.startswith("N:"):
        kind = data[2:]
        female = kind in ("woman", "girl")
        s["voice"] = "fa-IR-DilaraNeural" if female else "fa-IR-FaridNeural"
        s["child_gender"] = "Female" if female else "Male"
        s["mode"] = "child" if kind in ("girl", "boy") else "normal"
        s["rate"], s["pitch"] = -5, 0
        save_s(uid, s)
        names = {"woman": "زن", "man": "مرد", "girl": "دختر بچه", "boy": "پسر بچه"}
        await safe_edit(q, f"{E('ok')} گوینده مسیریاب ({names[kind]}) فعال شد.\nحالا متنت رو بفرست.")

    elif data.startswith("R:"):
        s["rate"] = int(data[2:])
        save_s(uid, s)
        await safe_edit(q, f"{E('ok')} سرعت: {s['rate']:+d}%")

    elif data.startswith("P:"):
        s["pitch"] = int(data[2:])
        save_s(uid, s)
        await safe_edit(q, f"{E('ok')} زیر و بمی: {s['pitch']:+d}Hz")

    elif data == "CVL":
        cats = custom_categories()
        if not cats:
            await safe_edit(q, "هنوز صدای ویژه‌ای ثبت نشده.")
            return
        rows = chunk([plain(c, f"CVC:{i}") for i, c in enumerate(cats)], 2)
        rows.append([B("back", "بازگشت", "BACK")])
        await safe_edit(q, f"{E('voice')} دسته‌ی صداهای ویژه:", InlineKeyboardMarkup(rows))

    elif data.startswith("CVC:"):
        cats = custom_categories()
        i = int(data[4:])
        if i >= len(cats):
            return
        vs = db_all(f"SELECT v.id, v.name, v.kind {CV_JOIN} AND v.category=?", (cats[i],))
        rows = chunk([B(v["kind"], v["name"], f"CVS:{v['id']}") for v in vs], 2)
        rows.append([B("back", "بازگشت", "CVL")])
        await safe_edit(q, f"{E('voice')} گوینده رو انتخاب کن ({html.escape(cats[i])}):", InlineKeyboardMarkup(rows))

    elif data.startswith("CVS:"):
        s["voice"], s["mode"] = f"custom:{data[4:]}", "normal"
        save_s(uid, s)
        await safe_edit(q, f"{E('ok')} صدای ویژه انتخاب شد.\nحالا متنت رو بفرست.")

    elif data == "BACK":
        await safe_edit(q, f"{E('world')} زبان رو انتخاب کن:", languages_markup())


# ═════════════════════════ پنل مدیریت ═════════════════════════
EMOJI_ORDER = list(EMOJI.keys())
EMOJI_LABELS = {
    "wave": "سلام", "mic": "میکروفون", "world": "کره‌ی زمین", "accent": "لهجه",
    "woman": "زن", "man": "مرد", "girl": "دختر بچه", "boy": "پسر بچه",
    "speed": "سرعت", "pitch": "زیر و بمی", "back": "بازگشت", "ok": "تایید",
    "error": "خطا", "warn": "هشدار", "gear": "تنظیمات", "voice": "گوینده",
    "stats": "آمار", "bc": "ارسال همگانی", "fw": "فوروارد", "lock": "قفل",
    "card": "کارت/اشتراک", "gift": "هدیه", "ban": "مسدود", "restore": "بازگردانی",
    "cancel": "لغو", "check": "عضو شدم", "user": "کاربر", "admin": "مدیر",
}


def emo_text(idx):
    done = sum(1 for k in EMOJI_ORDER if CUSTOM_IDS.get(k))
    if idx >= len(EMOJI_ORDER):
        return f"{E('ok')} همه‌ی ایموجی‌ها ثبت شدند."
    nxt = "، ".join(EMOJI_LABELS[k] for k in EMOJI_ORDER[idx:idx + 6])
    return (
        f"{E('gift')} <b>ثبت ایموجی پریمیوم</b>\n"
        f"ثبت‌شده: {done}/{len(EMOJI_ORDER)}\n\n"
        f"الان نوبت: <b>{EMOJI_LABELS[EMOJI_ORDER[idx]]}</b>\n"
        f"بعدی‌ها: {nxt}\n\n"
        "ایموجی پریمیوم رو بفرست. می‌تونی چندتا رو توی یک پیام بفرستی؛ به ترتیب بالا ثبت میشن."
    )


def emo_markup():
    return InlineKeyboardMarkup([
        [plain("⏭ رد کردن این یکی", "A:emoskip"), plain("🗑 پاک کردن همه", "A:emoreset", "danger")],
        [B("ok", "پایان", "A:home", "success")],
    ])


def panel_text():
    paid = get_kv("mode") == "paid"
    return (
        f"{E('admin')} <b>پنل مدیریت</b>\n"
        f"حالت ربات: <b>{'اشتراکی' if paid else 'رایگان'}</b>\n"
        f"ایموجی پریمیوم: {'فعال ✅' if STATE['premium'] else 'غیرفعال (اکانت مدیر پریمیوم نیست یا هنوز پیام نداده)'}\n"
        f"کانال‌های اجباری: {db_one('SELECT COUNT(*) n FROM channels')['n']}"
    )


def panel_markup():
    paid = get_kv("mode") == "paid"
    rows = [
        [B("stats", "آمار", "A:stats"), B("backup", "بکاپ", "A:backup")],
        [B("bc", "ارسال همگانی", "A:bc"), B("fw", "فوروارد همگانی", "A:fw")],
        [B("lock", "عضویت اجباری", "A:ch"),
         B("card", f"حالت: {'اشتراکی' if paid else 'رایگان'}", "A:mode")],
        [B("gift", "اشتراک کاربر", "A:sub"), B("ban", "مسدود/آزاد", "A:ban")],
        [B("gear", "تنظیمات", "A:set"), B("restore", "بازگردانی بکاپ", "A:restore")],
        [B("mic", "صداهای سفارشی (API)", "A:cv", "primary")],
        [B("gift", "ثبت ایموجی پریمیوم", "A:emo")],
    ]
    return InlineKeyboardMarkup(rows)


def cancel_markup():
    return InlineKeyboardMarkup([[B("cancel", "لغو", "A:home", "danger")]])


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data.pop("await", None)
    await update.message.reply_html(panel_text(), reply_markup=panel_markup())


def make_backup():
    path = os.path.join(tempfile.gettempdir(), f"bot_backup_{datetime.now():%Y%m%d_%H%M%S}.db")
    with closing(sqlite3.connect(DB_PATH)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
    return path


async def send_backup(bot, chat_id, caption="💾 بکاپ دیتابیس"):
    path = make_backup()
    try:
        with open(path, "rb") as f:
            await bot.send_document(chat_id, f, caption=caption)
    finally:
        os.remove(path)


async def auto_backup(context: ContextTypes.DEFAULT_TYPE):
    for aid in ADMIN_IDS:
        try:
            await send_backup(context.bot, aid, "💾 بکاپ خودکار روزانه")
        except Exception:
            log.exception("auto backup failed for %s", aid)


def stats_text():
    now = time.time()
    r = db_one(
        """SELECT COUNT(*) total,
                  SUM(joined >= ?) new24, SUM(last_seen >= ?) act24,
                  SUM(blocked) blocked, SUM(banned) banned,
                  SUM(sub_until > ?) subs, SUM(total) conv
           FROM users""",
        (now - 86400, now - 86400, now),
    )
    g = lambda k: r[k] or 0
    return (
        f"{E('stats')} <b>آمار ربات</b>\n"
        f"کل کاربران: {g('total')}\n"
        f"جدید (۲۴ ساعت): {g('new24')}\n"
        f"فعال (۲۴ ساعت): {g('act24')}\n"
        f"بلاک‌کرده‌ها: {g('blocked')}\n"
        f"مسدودشده توسط مدیر: {g('banned')}\n"
        f"دارای اشتراک: {g('subs')}\n"
        f"کل تبدیل‌ها: {g('conv')}"
    )


async def ask(q, context, state, text):
    context.user_data["await"] = state
    await safe_edit(q, text, cancel_markup())


async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    d = q.data[2:]

    if d == "home":
        context.user_data.pop("await", None)
        context.user_data.pop("pending", None)
        await safe_edit(q, panel_text(), panel_markup())

    elif d == "stats":
        await safe_edit(q, stats_text(), InlineKeyboardMarkup([[B("back", "بازگشت", "A:home")]]))

    elif d == "backup":
        await send_backup(context.bot, q.message.chat_id)

    elif d == "mode":
        set_kv("mode", "free" if get_kv("mode") == "paid" else "paid")
        await safe_edit(q, panel_text(), panel_markup())

    elif d in ("bc", "fw"):
        what = "ارسال همگانی (کپی)" if d == "bc" else "فوروارد همگانی"
        await ask(q, context, d, f"{E('bc')} <b>{what}</b>\nپیامت رو بفرست (متن، عکس، ویدیو، ... هر چیزی).")

    elif d in ("bcgo", "fwgo"):
        p = context.user_data.pop("pending", None)
        if not p:
            await safe_edit(q, "پیامی برای ارسال نیست.", panel_markup())
            return
        await safe_edit(q, f"{E('bc')} ارسال شروع شد ...")
        context.application.create_task(
            run_broadcast(context.bot, q.message.chat_id, p["kind"], p["chat_id"], p["message_id"])
        )

    elif d == "ch":
        chans = db_all("SELECT * FROM channels")
        rows = [[plain(f"🗑 حذف {c['title']}", f"A:chdel:{c['chat_id']}", "danger")] for c in chans]
        rows.append([B("lock", "افزودن کانال", "A:chadd", "success")])
        rows.append([B("back", "بازگشت", "A:home")])
        txt = f"{E('lock')} <b>عضویت اجباری</b>\n"
        txt += "\n".join(f"• {html.escape(c['title'])}" for c in chans) if chans else "هیچ کانالی تنظیم نشده."
        txt += "\n\nربات باید در کانال/گروه <b>ادمین</b> باشه."
        await safe_edit(q, txt, InlineKeyboardMarkup(rows))

    elif d.startswith("chdel:"):
        db_exec("DELETE FROM channels WHERE chat_id=?", (int(d.split(":", 1)[1]),))
        q.data = "A:ch"
        await admin_cb(update, context)

    elif d == "chadd":
        await ask(q, context, "chadd",
                  "آیدی کانال رو بفرست (مثل <code>@mychannel</code> یا <code>-100123...</code>).\n"
                  "برای کانال خصوصی، در خط دوم لینک دعوت رو هم بنویس.")

    elif d == "sub":
        await ask(q, context, "sub",
                  f"{E('gift')} آیدی عددی کاربر و تعداد روز رو بفرست:\n<code>5234934574 30</code>\n"
                  "(روز = 0 یعنی لغو اشتراک)")

    elif d == "ban":
        await ask(q, context, "ban", f"{E('ban')} آیدی عددی کاربر رو بفرست (وضعیت مسدودی برعکس میشه).")

    elif d == "set":
        txt = (
            f"{E('gear')} <b>تنظیمات</b>\n"
            f"سهمیه رایگان روزانه (حالت اشتراکی): {get_kv('daily_free')}\n"
            f"آیدی پشتیبانی: {html.escape(get_kv('support'))}\n"
            f"متن قیمت/خرید: {html.escape(get_kv('price'))}"
        )
        rows = [
            [plain("سهمیه روزانه", "A:daily"), plain("پشتیبانی", "A:support")],
            [plain("متن خرید", "A:price")],
            [B("back", "بازگشت", "A:home")],
        ]
        await safe_edit(q, txt, InlineKeyboardMarkup(rows))

    elif d == "daily":
        await ask(q, context, "daily", "تعداد تبدیل رایگان روزانه برای کاربران بدون اشتراک (عدد):")
    elif d == "support":
        await ask(q, context, "support", "آیدی پشتیبانی رو بفرست (مثلاً @admin):")
    elif d == "price":
        await ask(q, context, "price", "متن خرید اشتراک / قیمت‌ها رو بفرست:")

    elif d == "emo":
        context.user_data["emoji_idx"] = 0
        context.user_data["await"] = "emo"
        await safe_edit(q, emo_text(0), emo_markup())

    elif d == "emoskip":
        idx = min(context.user_data.get("emoji_idx", 0) + 1, len(EMOJI_ORDER))
        context.user_data["emoji_idx"] = idx
        await safe_edit(q, emo_text(idx), emo_markup())

    elif d == "emoreset":
        CUSTOM_IDS.clear()
        set_kv("emoji_ids", "{}")
        context.user_data["emoji_idx"] = 0
        context.user_data["await"] = "emo"
        await safe_edit(q, emo_text(0), emo_markup())

    elif d == "cv":
        engs = db_all("SELECT id, name FROM engines")
        nv = db_one("SELECT COUNT(*) n FROM custom_voices")["n"]
        txt = f"{E('mic')} <b>صداهای سفارشی</b>\nموتورها: {len(engs)} | صداها: {nv}\n"
        txt += "\n".join(f"• شناسه <code>{e['id']}</code> — {html.escape(e['name'])}" for e in engs)
        rows = [
            [B("mic", "افزودن موتور (API)", "A:eadd", "success"), B("mic", "افزودن صدا", "A:vadd", "success")],
            [B("gear", "لیست و مدیریت", "A:vlist")],
            [B("back", "بازگشت", "A:home")],
        ]
        await safe_edit(q, txt, InlineKeyboardMarkup(rows))

    elif d == "eadd":
        await ask(q, context, "eadd", ENGINE_HELP)

    elif d == "vadd":
        await ask(q, context, "vadd", VOICE_HELP)

    elif d == "vlist":
        rows = []
        for e in db_all("SELECT id, name FROM engines"):
            rows.append([plain(f"🧪 تست {e['name']}", f"A:etest:{e['id']}"),
                         plain("🗑 حذف موتور", f"A:edel:{e['id']}", "danger")])
        vs = db_all("SELECT id, name, category FROM custom_voices ORDER BY category, id LIMIT 40")
        rows += chunk([plain(f"🗑 {v['name']} ({v['category']})", f"A:vdel:{v['id']}") for v in vs], 2)
        rows.append([B("back", "بازگشت", "A:cv")])
        await safe_edit(q, f"{E('mic')} مدیریت موتورها و صداها (برای حذف روی دکمه بزن):", InlineKeyboardMarkup(rows))

    elif d.startswith("edel:"):
        eid = int(d.split(":", 1)[1])
        db_exec("DELETE FROM custom_voices WHERE engine_id=?", (eid,))
        db_exec("DELETE FROM engines WHERE id=?", (eid,))
        q.data = "A:vlist"
        await admin_cb(update, context)

    elif d.startswith("vdel:"):
        db_exec("DELETE FROM custom_voices WHERE id=?", (int(d.split(":", 1)[1]),))
        q.data = "A:vlist"
        await admin_cb(update, context)

    elif d.startswith("etest:"):
        eid = int(d.split(":", 1)[1])
        v = db_one("SELECT id FROM custom_voices WHERE engine_id=? AND enabled=1 LIMIT 1", (eid,))
        if not v:
            await q.message.reply_text("اول یک صدا برای این موتور ثبت کن.")
            return
        try:
            data = await custom_tts(v["id"], "سلام، این یک پیام آزمایشی است.")
            await reply_audio_bytes(q.message, data)
        except Exception as e:
            await q.message.reply_html(f"{E('error')} تست ناموفق:\n<code>{html.escape(str(e))[:500]}</code>")

    elif d == "restore":
        await ask(q, context, "restore",
                  f"{E('restore')} فایل بکاپ (<code>.db</code>) رو بفرست.\n"
                  "⚠️ دیتای فعلی با بکاپ جایگزین میشه.")


async def run_broadcast(bot, admin_chat, kind, from_chat, mid):
    users = [r["id"] for r in db_all("SELECT id FROM users WHERE banned=0 AND blocked=0")]
    ok = fail = 0
    status = await bot.send_message(admin_chat, f"⏳ ارسال به {len(users)} کاربر شروع شد ...")
    for i, uid in enumerate(users, 1):
        for _ in range(2):
            try:
                if kind == "fw":
                    await bot.forward_message(uid, from_chat, mid)
                else:
                    await bot.copy_message(uid, from_chat, mid)
                ok += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Forbidden:
                db_exec("UPDATE users SET blocked=1 WHERE id=?", (uid,))
                fail += 1
                break
            except Exception:
                fail += 1
                break
        if i % 50 == 0:
            try:
                await status.edit_text(f"⏳ {i}/{len(users)} — موفق: {ok} | ناموفق: {fail}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    try:
        await status.edit_text(f"✅ ارسال تموم شد\nموفق: {ok}\nناموفق: {fail}")
    except Exception:
        await bot.send_message(admin_chat, f"✅ ارسال تموم شد\nموفق: {ok}\nناموفق: {fail}")


async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """ورودی‌های مدیر وقتی منتظر پاسخ هستیم. True یعنی پیام مصرف شد."""
    st = context.user_data.get("await")
    msg = update.message
    if not st:
        return False
    back = InlineKeyboardMarkup([[B("back", "بازگشت به پنل", "A:home")]])

    if st in ("bc", "fw"):
        context.user_data.pop("await")
        context.user_data["pending"] = {"kind": st, "chat_id": msg.chat_id, "message_id": msg.message_id}
        n = db_one("SELECT COUNT(*) n FROM users WHERE banned=0 AND blocked=0")["n"]
        rows = [[B("ok", f"تایید و ارسال به {n} کاربر", f"A:{st}go", "success")],
                [B("cancel", "لغو", "A:home", "danger")]]
        await msg.reply_html("پیش‌نمایش بالاست. ارسال بشه؟", reply_markup=InlineKeyboardMarkup(rows))
        return True

    if st == "restore":
        doc = msg.document
        if not doc or not (doc.file_name or "").endswith(".db"):
            await msg.reply_text("لطفاً فایل .db بفرست.")
            return True
        tmp = os.path.join(tempfile.gettempdir(), "restore_tmp.db")
        f = await doc.get_file()
        await f.download_to_drive(tmp)
        try:
            with closing(sqlite3.connect(tmp)) as c:
                c.execute("SELECT COUNT(*) FROM users").fetchone()
        except Exception:
            os.remove(tmp)
            await msg.reply_text("❌ فایل معتبر نیست.")
            return True
        shutil.copyfile(tmp, DB_PATH)
        os.remove(tmp)
        init_db()
        load_emoji_ids()
        context.user_data.pop("await")
        await msg.reply_html(f"{E('ok')} بکاپ بازگردانی شد.", reply_markup=back)
        return True

    if st == "emo":
        ents = sorted([e for e in (msg.entities or []) if e.type == "custom_emoji"],
                      key=lambda e: e.offset)
        if not ents:
            await msg.reply_text("ایموجی پریمیوم نفرستادی. یک (یا چند) ایموجی پریمیوم بفرست.")
            return True
        idx = context.user_data.get("emoji_idx", 0)
        for e in ents:
            if idx >= len(EMOJI_ORDER):
                break
            CUSTOM_IDS[EMOJI_ORDER[idx]] = e.custom_emoji_id
            idx += 1
        context.user_data["emoji_idx"] = idx
        set_kv("emoji_ids", json.dumps(CUSTOM_IDS))
        if idx >= len(EMOJI_ORDER):
            context.user_data.pop("await")
            await msg.reply_html(emo_text(idx), reply_markup=back)
        else:
            await msg.reply_html(emo_text(idx), reply_markup=emo_markup())
        return True

    text = (msg.text or "").strip()
    if not text:
        await msg.reply_text("لطفاً متن بفرست.")
        return True

    if st == "chadd":
        lines = text.splitlines()
        ident = lines[0].strip()
        try:
            chat = await context.bot.get_chat(int(ident) if ident.lstrip("-").isdigit() else ident)
        except TelegramError as e:
            await msg.reply_text(f"❌ کانال پیدا نشد: {e}")
            return True
        link = lines[1].strip() if len(lines) > 1 else (f"https://t.me/{chat.username}" if chat.username else "")
        if not link:
            await msg.reply_text("این کانال خصوصیه؛ در خط دوم لینک دعوت رو هم بنویس.")
            return True
        db_exec("INSERT OR REPLACE INTO channels(chat_id,title,link) VALUES(?,?,?)",
                (chat.id, chat.title or ident, link))
        context.user_data.pop("await")
        await msg.reply_html(f"{E('ok')} کانال «{html.escape(chat.title or ident)}» اضافه شد.", reply_markup=back)
        return True

    if st == "sub":
        parts = text.split()
        if len(parts) != 2 or not (parts[0].isdigit() and parts[1].lstrip("-").isdigit()):
            await msg.reply_text("فرمت: آیدی  تعداد‌روز   (مثلاً 5234934574 30)")
            return True
        uid, days = int(parts[0]), int(parts[1])
        row = db_one("SELECT sub_until FROM users WHERE id=?", (uid,))
        if not row:
            await msg.reply_text("این کاربر هنوز ربات رو استارت نکرده.")
            return True
        new = 0 if days <= 0 else int(max(time.time(), row["sub_until"]) + days * 86400)
        db_exec("UPDATE users SET sub_until=? WHERE id=?", (new, uid))
        context.user_data.pop("await")
        await msg.reply_html(f"{E('ok')} انجام شد.", reply_markup=back)
        try:
            note = f"{E('gift')} اشتراک شما برای {days} روز فعال شد." if days > 0 else "اشتراک شما لغو شد."
            await context.bot.send_message(uid, note, parse_mode=H)
        except TelegramError:
            pass
        return True

    if st == "ban":
        if not text.isdigit():
            await msg.reply_text("آیدی عددی بفرست.")
            return True
        uid = int(text)
        row = db_one("SELECT banned FROM users WHERE id=?", (uid,))
        if not row:
            await msg.reply_text("کاربر پیدا نشد.")
            return True
        nb = 0 if row["banned"] else 1
        db_exec("UPDATE users SET banned=? WHERE id=?", (nb, uid))
        context.user_data.pop("await")
        await msg.reply_html(f"{E('ok')} کاربر {uid} {'مسدود' if nb else 'آزاد'} شد.", reply_markup=back)
        return True

    if st == "eadd":
        try:
            cfg = parse_engine(text)
        except ValueError as e:
            await msg.reply_text(f"❌ {e}\nدوباره بفرست یا لغو کن.")
            return True
        eid = db_exec("INSERT INTO engines(name, config) VALUES(?,?)", (cfg["name"], json.dumps(cfg)))
        context.user_data.pop("await")
        await msg.reply_html(
            f"{E('ok')} موتور ثبت شد. شناسه: <code>{eid}</code>\n"
            "حالا با «افزودن صدا» صداهاش رو ثبت کن.", reply_markup=back)
        return True

    if st == "vadd":
        added, errors = parse_voices(text)
        if added:
            context.user_data.pop("await")
        out = f"{E('ok') if added else E('error')} {added} صدا ثبت شد."
        if errors:
            out += "\n\nخطوط نامعتبر (شناسه موتور یا نوع اشتباهه):\n" + html.escape("\n".join(errors))
        await msg.reply_html(out, reply_markup=back if added else cancel_markup())
        return True

    if st == "daily":
        if not text.isdigit():
            await msg.reply_text("یک عدد بفرست.")
            return True
        set_kv("daily_free", int(text))
    elif st in ("support", "price"):
        set_kv(st, text)
    context.user_data.pop("await")
    await msg.reply_html(f"{E('ok')} ذخیره شد.", reply_markup=back)
    return True


# ═════════════════════════ پیام‌ها → ویس ═════════════════════════
async def emoji_id_helper(update: Update) -> bool:
    msg = update.message
    if update.effective_user.id != OWNER_ID or not msg or not msg.entities:
        return False
    ids = [e.custom_emoji_id for e in msg.entities if e.type == "custom_emoji"]
    if not ids:
        return False
    await msg.reply_text("\n".join(ids))
    return True


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id

    if is_admin(uid) and await admin_input(update, context):
        return
    if await emoji_id_helper(update):
        return
    if not msg.text:
        return
    if not await gate(update, context):
        return
    if not can_use(uid):
        await msg.reply_html(denied_text(uid))
        return

    text = msg.text.strip()
    if len(text) > MAX_CHARS:
        await msg.reply_html(f"{E('warn')} متن خیلی طولانیه. حداکثر {MAX_CHARS} کاراکتر.")
        return

    s = get_s(uid)
    if s["voice"].startswith("custom:"):
        await msg.chat.send_action("record_voice")
        try:
            data = await custom_tts(int(s["voice"].split(":")[1]), text)
            await reply_audio_bytes(msg, data)
            mark_use(uid)
        except ValueError as e:
            await msg.reply_html(f"{E('warn')} {html.escape(str(e))}")
        except Exception:
            log.exception("custom TTS error")
            await msg.reply_html(f"{E('error')} خطا در ساخت صدا. چند لحظه بعد دوباره امتحان کن.")
        return

    rate, pitch = s["rate"], s["pitch"]
    if s["mode"] == "child":
        c = CHILD[s["child_gender"]]
        rate += c["rate"]
        pitch += c["pitch"]

    await msg.chat.send_action("record_voice")
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        await edge_tts.Communicate(text, s["voice"], rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz").save(path)
        with open(path, "rb") as audio:
            await msg.reply_voice(voice=audio)
        mark_use(uid)
    except Exception as e:
        log.exception("TTS error")
        await msg.reply_html(f"{E('error')} خطا در ساخت صدا:\n<code>{html.escape(str(e))}</code>")
    finally:
        if path and os.path.exists(path):
            os.remove(path)


# ═════════════════════════ اجرا ═════════════════════════
def main():
    init_db()
    load_emoji_ids()
    load_premium_state()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(TypeHandler(Update, track), group=-1)
    for name, fn in [("start", start), ("voice", voice_cmd), ("navi", navi_cmd),
                     ("speed", speed_cmd), ("pitch", pitch_cmd), ("current", current_cmd),
                     ("me", me_cmd), ("admin", admin_cmd)]:
        app.add_handler(CommandHandler(name, fn, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern=r"^A:"))
    app.add_handler(CallbackQueryHandler(user_cb, pattern=r"^(?:[LCGVKNRP]:|CVC:|CVS:|CVL$|BACK$|JOINCHK$)"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))

    if app.job_queue:
        app.job_queue.run_repeating(auto_backup, interval=86400, first=3600)
    else:
        log.warning("job-queue نصب نیست؛ بکاپ خودکار غیرفعاله. (pip install 'python-telegram-bot[job-queue]')")

    log.info("Bot started. premium=%s admins=%s", STATE["premium"], ADMIN_IDS)
    app.run_polling()


if __name__ == "__main__":
    main()
