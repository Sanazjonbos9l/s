"""
سلف‌بات تلگرام با Telethon
ساخته شده توسط @MRZverse

قابلیت‌ها (خلاصه‌شده):
  - میو دوره‌ای در یک گپ
  - ساعت خودکار روی بیو و نام خانوادگی (با فونت شیک)
  - افک (پاسخ خودکار وقتی نیستی)
  - فارم خودکار بات میویی، داخل یه گروه مشخص
  - کنترل کامل از راه دور توسط مدیر(ها) از طریق پیوی همین اکانت

نصب پیش‌نیاز:
    pip install telethon jdatetime

قبل از اجرا:
    1) برو به https://my.telegram.org و یه اپلیکیشن بساز
    2) مقدار api_id و api_hash رو زیر جایگزین کن
    3) آیدی عددی مدیر(ها) رو توی ADMIN_IDS بذار
    4) اولین بار که اجرا می‌کنی، شماره تلفن و کد تایید رو ازت می‌پرسه

نکته‌ی مهم درباره‌ی دستورها:
    دستورهای فعال‌سازی/تنظیم به‌عمد انگلیسی نوشته شدن (on/off) تا با
    مشکلات رایج تطبیق حروف فارسی (مثل «ي» عربی به‌جای «ی» فارسی، یا
    نویسه‌های نیم‌فاصله) روبه‌رو نشیم. جواب‌های ربات همچنان فارسی‌ان.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

import jdatetime  # برای تبدیل درست تاریخ شمسی -> نصب: pip install jdatetime

from telethon import TelegramClient, events
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.errors import FloodWaitError
from telethon.tl.types import PeerChannel

# ====== تنظیمات ======
HOSTED = os.environ.get("MOZI_HOSTED") == "1"   # 1 = اجرا توسط ربات سلف‌ساز (بدون لاگ پیوی و ذخیره‌ی سکرت)
api_id = int(os.environ.get("MOZI_API_ID", "123456"))          # <-- اینجا رو با api_id خودت جایگزین کن
api_hash = os.environ.get("MOZI_API_HASH", "YOUR_API_HASH")  # <-- اینجا رو با api_hash خودت جایگزین کن
session_name = os.environ.get("MOZI_SESSION", "meow_session")

tz_offset_hours = 3.5            # تایم‌زون تهران (UTC+3:30)
clock_interval_seconds = 60      # فاصله‌ی آپدیت ساعت بیو/اسم

ADMIN_IDS: list[int] = [int(x) for x in os.environ.get("MOZI_ADMINS", "").split(",") if x.strip()]   # <-- آیدی عددی اکانت‌های مدیر (از @userinfobot بگیر)

MEOWIE_TRIGGER_TEXT = "پیشی"      # پیامی که برای فارم به گروه فرستاده میشه
MEOWIE_BUTTON_KEYWORD = "برداشت میو پوینت ها"  # عبارت کامل دکمه، تا با دکمه‌های دیگه (مثلاً بانکی) اشتباه گرفته نشه
FRIDGE_TRIGGER_TEXT = "یخچال میویی"     # پیامی که برای بررسی یخچال فرستاده میشه
RESCUE_BUTTON_KEYWORD = "نجات پیشی خیابونی"   # دکمه‌ی پیام «پیشی خیابونی»
FISH_TRIGGER_TEXT = "ماهی"                 # دستور ماهی‌گیری
FRIDGE_STORE_KEYWORD = "بندازش تو یخچال"   # دکمه‌ی گذاشتن ماهی توی یخچال
FEED_BUTTON_KEYWORD = "بده پیشی بخوره"
COOK_BUTTON_KEYWORD = "بپوخش"
MAX_FISH_VALUE = 2000            # فقط ماهی‌هایی که ارزششون کمتر از این عدده خورانده/پخته میشن
# ======================

# آخرین خط‌های لاگ توی حافظه نگه داشته میشه تا با دستور «feed log» داخل تلگرام دیده بشه
import collections as _collections
_log_ring = _collections.deque(maxlen=25)
_orig_print = print


def print(*args, **kwargs):   # noqa: A001 — print معمولی + ذخیره توی _log_ring
    try:
        _log_ring.append(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in args))
    except Exception:
        pass
    _orig_print(*args, **kwargs)


client = TelegramClient(session_name, api_id, api_hash)

OWNER_ID: int | None = None  # موقع اجرا پر میشه

# --- وضعیت‌های سراسری ---
active_meow_tasks: dict[int, asyncio.Task] = {}
meow_seconds: dict[int, float] = {}
resume_state: dict = {}
meow_default_seconds = 10 * 60

clock_bio_task: asyncio.Task | None = None
clock_name_task: asyncio.Task | None = None

TELEGRAM_SERVICE_IDS = (777000, 42777, 333000, 429000, 93372553, 178220800)  # اکانت‌های رسمی تلگرام
dm_log_enabled = False          # ذخیره‌ی پیام‌های پیوی توی کانال
dm_log_target = None            # کانال مقصد (با «log channel <id|link>» تنظیم میشه)
ttl_save_enabled = True  # ذخیره‌ی عکس/ویدیوی نابودشونده توی Saved Messages
feed_interval_seconds: float | None = None   # None = غذا دادن همراه فارم؛ عدد = چک جدا هر چند ثانیه
feed_task: asyncio.Task | None = None
rescue_enabled = True   # کلیک خودکار روی «نجات پیشی خیابونی»
feed_enabled = True     # وقتی پیشی گشنمیوئه: یخچال رو چک کن و غذا بده
feed_lock = asyncio.Lock()
rescued_msg_ids: set[tuple[int, int]] = set()
RESCUE_DONE_WORDS = ("کشته شد", "علت مرگ", "نجات پیدا", "نجات یافت", "نجات داده شد", "نجات داد", "صاحب پیدا کرد", "صاحب یک خونه")
rescue_max_tries = 10          # حداکثر تعداد کلیک روی هر پیام نجات
rescue_delay_seconds = 3.0     # فاصله‌ی بین کلیک‌ها

afk_enabled = False
afk_message = "فعلاً نیستم، بعداً جواب می‌دم 🌙"
afk_replied_chats: set[int] = set()

farm_task: asyncio.Task | None = None
farm_chat_id: int | None = None
farm_interval_seconds = 60 * 60  # هر یک ساعت

awaiting_farm_group = False           # منتظر آیدی گروه از طرف خودِ اکانت
admin_awaiting_group: set[int] = set()  # منتظر آیدی گروه از طرف مدیر (توی پیوی)

# فلوی گفتگویی «delete messages»: None / "await_type" / "await_group" / "await_private"
delete_flow_stage: str | None = None


def normalize(text: str) -> str:
    """برای جلوگیری از مشکلات تطبیق حروف عربی/فارسی و نیم‌فاصله"""
    return (
        text.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")  # نیم‌فاصله -> فاصله‌ی معمولی
        .strip()
    )


def parse_duration_seconds(raw: str) -> float | None:
    """'30' یا '30m' -> ۳۰ دقیقه، '2h' -> ۲ ساعت. خروجی به ثانیه، یا None اگه نامعتبر بود"""
    raw = raw.strip().lower()
    if not raw:
        return None
    try:
        if raw.endswith("h"):
            return float(raw[:-1]) * 3600
        if raw.endswith("m"):
            return float(raw[:-1]) * 60
        return float(raw) * 60  # پیش‌فرض: دقیقه
    except ValueError:
        return None


def now_str() -> str:
    tz = timezone(timedelta(hours=tz_offset_hours))
    return datetime.now(tz).strftime("%H:%M")


def _local_now():
    tz = timezone(timedelta(hours=tz_offset_hours))
    return datetime.now(tz)


JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def jalali_date_str() -> str:
    now = _local_now()
    jd = jdatetime.date.fromgregorian(date=now.date())
    return f"{jd.day} {JALALI_MONTHS[jd.month - 1]} {jd.year}"


# چند سبک فونت اعداد یونیکد که می‌شه برای ساعت روی بیو/اسم انتخاب کرد
DIGIT_FONTS = {
    "circle": {"0": "⓿", **{str(i): chr(0x2775 + i) for i in range(1, 10)}},   # ➊➋➌ + ⓿
    "bold":   {str(i): chr(0x1D7CE + i) for i in range(10)},                    # 𝟎𝟏𝟐
    "double": {str(i): chr(0x1D7D8 + i) for i in range(10)},                    # 𝟘𝟙𝟚
    "sans":   {str(i): chr(0x1D7E2 + i) for i in range(10)},                    # 𝟢𝟣𝟤
    "mono":   {str(i): chr(0x1D7F6 + i) for i in range(10)},                    # 𝟶𝟷𝟸
    "boldsans": {str(i): chr(0x1D7EC + i) for i in range(10)},                 # 𝟬𝟭𝟮
    "circled": {"0": "⓪", **{str(i): chr(0x2460 + i - 1) for i in range(1, 10)}},   # ①②③
    "sans_circle": {"0": "⓪", **{str(i): chr(0x2780 + i - 1) for i in range(1, 10)}},  # ➀➁➂
    "dbl_circle": {"0": "⓪", **{str(i): chr(0x24F5 + i - 1) for i in range(1, 10)}},   # ⓵⓶⓷
    "keycap": {**{str(i): f"{i}\ufe0f\u20e3" for i in range(10)}, ":": ":"},           # 1️⃣2️⃣3️⃣
    "fullwidth": {**{str(i): chr(0xFF10 + i) for i in range(10)}, ":": "："},            # ０１２
    "paren":  {"0": "⓪", **{str(i): chr(0x2474 + i - 1) for i in range(1, 10)}},   # ⑴⑵⑶
    "normal": {str(i): str(i) for i in range(10)},                              # بدون تغییر
}

bio_reverse = False          # با «clock bio reverse on» ساعت بیو از قبل برعکس نوشته میشه (اگه LRM جواب نداد)
bio_digit_font = "circle"    # فونت ساعت بیو؛ با «clock bio font <name>» عوض میشه
name_digit_font = "circle"   # فونت ساعت نام خانوادگی؛ با «clock name font <name>» عوض میشه


def fancy_digits(s: str, font: str = "circle") -> str:
    mapping = DIGIT_FONTS.get(font, DIGIT_FONTS["circle"])
    return "".join(mapping.get(ch, ch) for ch in s)


def fancy_digits_preview(font_name: str) -> str:
    mapping = DIGIT_FONTS.get(font_name, DIGIT_FONTS["circle"])
    return "".join(mapping.get(ch, ch) for ch in "12:34")


LRM = "\u200e"  # علامت چپ‌به‌راست (نامرئی)


def fancy_time(font: str = "circle", reverse: bool = False) -> str:
    """ساعت با سبک فونت اعداد انتخاب‌شده، برای بیو/اسم.
    بین همه‌ی نویسه‌ها LRM می‌ذاریم تا اعداد دایره‌ای/نمادی (که توی متن راست‌به‌چپ معکوس می‌شن)
    همیشه چپ‌به‌راست نمایش داده بشن: 01:57 و نه 75:10"""
    chars = []
    for ch in now_str():
        chars.append(fancy_digits(ch, font))
    if reverse:
        # حالت پشتیبان: اگه تلگرام علامت LRM رو نادیده بگیره، ساعت رو از قبل برعکس می‌نویسیم
        # تا موقع نمایش (که معکوس میشه) درست دیده بشه
        return "".join(reversed(chars))
    return LRM + LRM.join(chars) + LRM


BIO_STYLES = {
    "1": "🌙 {d}  ┃  🕰 {t}",
    "2": "🗓 {d}  ✦  {wd}  ✦  ⏰ {t}",
    "3": "✨ {d} ⋆ 🕐 {t} ✨",
    "4": "🌸 {d} ‹ ⏳ {t} › 🌸",
    "5": "💫 {d}  •  🕒 {t}",
}
bio_style = "1"  # با دستور «clock bio style <1-5>» عوض میشه


def fancy_bio_text() -> str:
    """تاریخ شمسی + ساعت، با ایموجی و فونت اعداد انتخاب‌شده، برای بیو"""
    now = _local_now()
    d = now.strftime("%d %b %Y")   # تاریخ میلادی، مثلاً 20 Oct 2026 (هر روز خودکار عوض میشه)
    wd = now.strftime("%a")        # روز هفته، مثلاً Tue
    prefix = "" if bio_reverse else LRM  # LRM اول متن: جهت کل بیو چپ‌به‌راست میشه
    text = BIO_STYLES.get(bio_style, BIO_STYLES["1"]).format(
        d=d, t=fancy_time(bio_digit_font, bio_reverse), wd=wd, gd=now.day, gm=now.strftime("%b")
    )
    return prefix + text


# ---------- میو دوره‌ای ----------
async def meow_loop(chat_id: int, seconds: float):
    try:
        while True:
            await asyncio.sleep(seconds)
            await client.send_message(chat_id, "میو")
    except asyncio.CancelledError:
        pass


async def toggle_meow(chat_id: int, minutes: float | None = None) -> str:
    if chat_id in active_meow_tasks:
        return "میو فعال می‌باشد 🐱 (از قبل روشن بود)"
    seconds = (minutes * 60) if minutes else meow_default_seconds
    active_meow_tasks[chat_id] = asyncio.create_task(meow_loop(chat_id, seconds))
    meow_seconds[chat_id] = seconds
    shown = minutes if minutes else meow_default_seconds / 60
    return f"میو فعال شد ✅ (هر {shown:g} دقیقه)"


async def untoggle_meow(chat_id: int) -> str:
    task = active_meow_tasks.pop(chat_id, None)
    meow_seconds.pop(chat_id, None)
    if task:
        task.cancel()
        return "میو غیرفعال شد ❌"
    return "میو غیرفعال می‌باشد ❌ (از قبل خاموش بود)"


# ---------- ساعت خودکار بیو/اسم ----------
lrm_checked = False  # فقط یک بار چک می‌کنیم تلگرام علامت LRM رو نگه داشته یا نه


async def _safe_profile_update(**kwargs):
    """آپدیت پروفایل؛ خطاها (مثل FloodWait) حلقه‌ی ساعت رو نمی‌کشن"""
    try:
        await client(UpdateProfileRequest(**kwargs))
    except FloodWaitError as e:
        print(f"FloodWait: {e.seconds}s صبر می‌کنم")
        await asyncio.sleep(e.seconds + 1)
    except Exception as e:
        print(f"آپدیت پروفایل: {e}")


async def _check_lrm_in_bio():
    """اگه تلگرام علامت LRM رو از بیو حذف کرده باشه، خودکار حالت «برعکس‌نویسی» رو روشن می‌کنیم"""
    global bio_reverse, lrm_checked
    if lrm_checked or bio_reverse:
        return
    lrm_checked = True
    try:
        full = await client(GetFullUserRequest("me"))
        about = full.full_user.about or ""
        if LRM not in about:
            bio_reverse = True
            print("ساعت بیو: تلگرام LRM رو حذف کرد، حالت reverse خودکار روشن شد")
            await _safe_profile_update(about=fancy_bio_text())
    except Exception as e:
        print(f"چک LRM: {e}")


async def clock_bio_loop():
    try:
        while True:
            await _safe_profile_update(about=fancy_bio_text())
            await _check_lrm_in_bio()
            await asyncio.sleep(clock_interval_seconds)
    except asyncio.CancelledError:
        pass


async def clock_name_loop():
    try:
        while True:
            await _safe_profile_update(last_name=f"{LRM}✨ {fancy_time(name_digit_font)}")
            await asyncio.sleep(clock_interval_seconds)
    except asyncio.CancelledError:
        pass


async def toggle_clock_bio() -> str:
    global clock_bio_task
    if clock_bio_task:
        return "ساعت بیو فعال می‌باشد 🕐 (از قبل روشن بود)"
    clock_bio_task = asyncio.create_task(clock_bio_loop())
    return "ساعت خودکار بیو فعال شد ✅"


async def untoggle_clock_bio() -> str:
    global clock_bio_task
    if clock_bio_task:
        clock_bio_task.cancel()
        clock_bio_task = None
        return "ساعت بیو غیرفعال شد ❌"
    return "ساعت بیو غیرفعال می‌باشد ❌ (از قبل خاموش بود)"


async def toggle_clock_name() -> str:
    global clock_name_task
    if clock_name_task:
        return "ساعت اسم فعال می‌باشد 🕐 (از قبل روشن بود)"
    clock_name_task = asyncio.create_task(clock_name_loop())
    return "ساعت خودکار نام خانوادگی فعال شد ✅"


async def untoggle_clock_name() -> str:
    global clock_name_task
    if clock_name_task:
        clock_name_task.cancel()
        clock_name_task = None
        return "ساعت اسم غیرفعال شد ❌"
    return "ساعت اسم غیرفعال می‌باشد ❌ (از قبل خاموش بود)"


# ---------- افک ----------
async def toggle_afk(custom_message: str = "") -> str:
    global afk_enabled, afk_message
    if afk_enabled and not custom_message:
        return "حالت افک فعال می‌باشد 🌙 (از قبل روشن بود)"
    if custom_message:
        afk_message = custom_message
    afk_enabled = True
    afk_replied_chats.clear()
    return f"حالت افک فعال شد ✅\nپیام: {afk_message}"


async def untoggle_afk() -> str:
    global afk_enabled
    if not afk_enabled:
        return "حالت افک غیرفعال می‌باشد ❌ (از قبل خاموش بود)"
    afk_enabled = False
    afk_replied_chats.clear()
    return "حالت افک غیرفعال شد ❌ خوش اومدی 👋"


# ---------- فارم خودکار (داخل یه گروه مشخص) ----------
async def click_farm_button(chat_id: int, sent_msg_id: int):
    """منتظر پیامی می‌مونه که دقیقاً روی sent_msg_id ریپلای زده باشه، و دکمه‌ی برداشت رو می‌زنه.
    اگه توی پیام پاسخ، شکم «گشنمیو» بود، فلوی غذا دادن (یخچال) رو هم راه می‌ندازه."""
    for _ in range(15):  # حداکثر ۱۵ ثانیه صبر
        await asyncio.sleep(1)
        async for msg in client.iter_messages(chat_id, limit=40):
            if msg.reply_to_msg_id == sent_msg_id and not msg.out and msg.buttons and await _is_bot_msg(msg):
                text = msg.raw_text or ""
                try:
                    await msg.click(text=lambda t: t and MEOWIE_BUTTON_KEYWORD in t)
                except Exception:
                    pass
                hungry, room = feed_decision(text)
                print(f"شکم: {parse_stomach(text)} | متن‌های گرسنگی: {feed_hunger_texts} | غذا بده؟ {hungry}")
                if hungry and feed_enabled and feed_interval_seconds is None:
                    asyncio.create_task(feed_cat_flow(chat_id, room))
                return


# ---------- غذا دادن به پیشی (یخچال میویی) ----------
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


async def _is_bot_msg(msg) -> bool:
    """فقط پیام‌های ربات (میویی) رو قبول می‌کنیم، نه آدم‌های دیگه‌ی گروه"""
    try:
        sender = msg.sender or await msg.get_sender()
        return bool(getattr(sender, "bot", False))
    except Exception:
        return False


def _flat_buttons(msg):
    return [b for row in (msg.buttons or []) for b in row]


safe_cfg = {"on": True, "gap": 6.0, "jitter": 2.0}   # فاصله‌ی حداقلی بین دو دستور بازی (ثانیه) — جلوگیری از زندان
_game_last = 0.0
_game_lock = None


async def _game_gate():
    """همه‌ی دستورهای خودکار صف میشن و بین دو دستور حداقل gap (+کمی تصادفی) ثانیه فاصله میفته"""
    global _game_last, _game_lock
    if _game_lock is None:
        _game_lock = asyncio.Lock()
    async with _game_lock:
        import random as _r
        wait = _game_last + float(safe_cfg.get("gap", 6.0)) + _r.uniform(0, float(safe_cfg.get("jitter", 2.0))) - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _game_last = time.time()


def _is_game_cmd(entity, message, kw) -> bool:
    if kw.get("reply_to") is not None or entity in ("me", "self"):
        return False
    t = _clean(normalize(message or "")).strip()
    fixed = {MEOWIE_TRIGGER_TEXT, FRIDGE_TRIGGER_TEXT, FISH_TRIGGER_TEXT, FACTORY_TRIGGER_TEXT,
             MINER_TRIGGER_TEXT, BANK_TRIGGER_TEXT, "میو"}
    if t in fixed:
        return True
    return any(t == _clean(normalize(j.get("send", ""))).strip() for j in jobs.values())


def install_game_throttle():
    if getattr(client, "_game_throttled", False):
        return
    orig = client.send_message

    async def send_message(entity, message="", *a, **kw):
        if safe_cfg.get("on", True) and isinstance(message, str) and _is_game_cmd(entity, message, kw):
            await _game_gate()
        return await orig(entity, message, *a, **kw)

    client.send_message = send_message
    client._game_throttled = True


def safe_command(text: str) -> str:
    a = text.lower().translate(_DIGITS).split()
    sub = a[1] if len(a) > 1 else "status"
    c = safe_cfg
    if sub in ("status", "list"):
        return (f"🛡 محدودکننده‌ی سرعت دستورها: {'روشن ✅' if c['on'] else 'خاموش ❌'}\n"
                f"• فاصله‌ی حداقلی بین دو دستور: {c['gap']:g} ثانیه (+ تا {c['jitter']:g} ثانیه تصادفی)\n"
                "• همه‌ی دستورهای خودکار (پیشی، میو، ماهی، یخچال، ماینر، بانک، کارخونه، وظیفه‌ها) صف میشن تا میویی سلف رو زندانی نکنه.\n"
                "تغییر: ایمنی فاصله 8 | ایمنی تصادفی 3 | ایمنی روشن/خاموش")
    if sub in ("on", "off"):
        want = sub == "on"
        if bool(c["on"]) == want:
            return "محدودکننده‌ی سرعت " + ("فعال می‌باشد ✅ (از قبل روشن بود)" if want else "غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
        c["on"] = want
        return "محدودکننده‌ی سرعت فعال شد ✅" if want else "محدودکننده‌ی سرعت غیرفعال شد ❌ (خطر زندان!)"
    if sub in ("gap", "interval", "jitter") and len(a) > 2:
        try:
            v = float(a[2])
        except ValueError:
            return "عدد نامعتبره؛ مثال: ایمنی فاصله 8"
        if sub == "jitter":
            if not 0 <= v <= 30:
                return "تصادفی باید بین 0 تا 30 ثانیه باشه"
            c["jitter"] = v
            return f"تصادفی روی {v:g} ثانیه تنظیم شد ✅"
        if not 1 <= v <= 120:
            return "فاصله باید بین 1 تا 120 ثانیه باشه"
        c["gap"] = v
        return f"فاصله‌ی بین دو دستور روی {v:g} ثانیه تنظیم شد ✅"
    return "ایمنی وضعیت | ایمنی فاصله 8 | ایمنی تصادفی 3 | ایمنی روشن | ایمنی خاموش"


def _has_words(text: str, n: int = 3) -> bool:
    """متن واقعی (حداقل n حرف)؛ استیکر و ایموجی متحرکِ تنها (مثل 🎣🐟) متن واقعی حساب نمیشن"""
    return len(re.findall(r"[A-Za-z\u0600-\u06FF]", text or "")) >= n


_CONFIRM_WORDS = ("اطمینان", "مطمئن", "با تایید", "با تأیید", "تایید کن", "تأیید کن", "تایید میکنی", "تایید می کنی", "تأیید می کنی")


def _confirm_txt(text) -> bool:
    """متن صفحه‌ی تأیید؟ («آیا اطمینان دارید؟» یا «اگه مطمئنی تایید کن..» یا ...)"""
    t = _clean(normalize(text or "")).replace("\u200c", " ")
    if any(w in t for w in _CONFIRM_WORDS):
        return True
    lt = _loose(t)
    return any(_loose(p) and _loose(p) in lt for p in confirm_texts)


confirm_texts: list[str] = []   # متن‌های صفحه‌ی تأیید که با «confirm text add» اضافه میشن (ذخیره میشن)


def confirm_command(text: str) -> str:
    parts = text.split(None, 3)
    sub_ = parts[1].lower() if len(parts) > 1 else ""
    act = parts[2].lower() if len(parts) > 2 else ""
    arg = normalize(parts[3]) if len(parts) > 3 else ""
    if sub_ != "text" or act not in ("add", "del", "list", "reset"):
        return ("✅ تأیید خودکار: سلف روی صفحه‌هایی که متنشون شبیه «تأیید» باشه دکمه‌ی سبز ✓ رو می‌زنه\n"
                "confirm text add با تایید — اضافه کردن متن صفحه‌ی تأیید\n"
                "confirm text del <متن یا شماره> | confirm text list | confirm text reset\n"
                "confirm check — روی پیام تأییدِ ربات ریپلای کن تا ببینی شناخته میشه یا نه")
    if act == "list":
        extra = "".join(f"\n{i}. {p}" for i, p in enumerate(confirm_texts, 1)) if confirm_texts else " —"
        return "متن‌های پیش‌فرض: " + " ، ".join(_CONFIRM_WORDS) + "\nمتن‌های اضافه‌شده:" + extra
    if act == "reset":
        confirm_texts.clear()
        return "متن‌های اضافه‌شده پاک شد ✅ (فقط پیش‌فرض‌ها می‌مونن)"
    if not arg:
        return "مثال: confirm text add با تایید"
    if act == "add":
        if any(_loose(p) == _loose(arg) for p in confirm_texts):
            return "این متن از قبل توی لیست هست ✅"
        confirm_texts.append(arg)
        return f"متن تأیید اضافه شد ✅\nاز این به بعد هر پیام ربات (با دکمه) که شامل «{arg}» باشه صفحه‌ی تأیید حساب میشه و دکمه‌ی سبز ✓ زده میشه."
    if arg.isdigit() and 1 <= int(arg) <= len(confirm_texts):
        gone = confirm_texts.pop(int(arg) - 1)
        return f"حذف شد ✅ «{gone}»"
    for p in list(confirm_texts):
        if _loose(p) == _loose(arg):
            confirm_texts.remove(p)
            return f"حذف شد ✅ «{p}»"
    return "این متن توی لیست نیست. لیست: confirm text list"


async def confirm_check(event):
    rep = await event.get_reply_message() if event.is_reply else None
    if not rep:
        await event.edit("روی پیام تأییدِ ربات ریپلای کن و بنویس: confirm check", parse_mode=None)
        return
    raw = rep.raw_text or ""
    btns = _flat_buttons(rep)
    ok = _confirm_txt(raw)
    pick = _first_confirm(rep) if btns else None
    lines = [ln for ln in _clean(raw).splitlines() if ln.strip()]
    out = [f"🔎 صفحه‌ی تأیید شناخته میشه؟ {'بله ✅' if ok else 'نه ❌'}",
           "دکمه‌ها: " + (" | ".join((b.text or "?") for b in btns) or "—"),
           f"دکمه‌ای که زده میشه: {(pick.text or '?') if pick else '—'}"]
    if not ok and lines:
        out.append("برای شناسایی بنویس:\nconfirm text add " + lines[-1].strip())
    await event.edit("\n".join(out), parse_mode=None)


async def confirm_click(chat_id: int, cf, picker=None, tries: int = 3, timeout: int = 8):
    """دکمه‌ی تأیید رو می‌زنه و مطمئن میشه صفحه‌ی تأیید بسته شد؛ اگه نشد (کلیک نخورد/دیر ادیت شد)
    دوباره امتحان می‌کنه. پیام بعد از تأیید رو برمی‌گردونه (یا None)"""
    pick = picker or _first_confirm
    cur = cf
    for _ in range(tries):
        btn = pick(cur)
        if btn is not None and await safe_click(btn):
            res = await wait_msg(chat_id, cf.id, lambda x: not _confirm_txt(x.raw_text), timeout)
            if res is not None:
                return res
        else:
            await asyncio.sleep(0.7)
        latest = await client.get_messages(chat_id, ids=cf.id)
        if latest is not None:
            if not (_confirm_txt(latest.raw_text) and latest.buttons):
                return latest                     # صفحه عوض شده
            cur = latest
    return None


async def wait_reply_to(chat_id: int, sent_id: int, timeout: int = 15, need_buttons: bool = True):
    """پاسخ (ریپلای) ربات به پیام sent_id رو که دکمه داره برمی‌گردونه"""
    for _ in range(timeout):
        await asyncio.sleep(1)
        async for msg in client.iter_messages(chat_id, limit=40):
            if msg.reply_to_msg_id == sent_id and not msg.out and await _is_bot_msg(msg) \
                    and (msg.buttons or (not need_buttons and _has_words(msg.raw_text))):
                return msg
    return None


async def wait_button(chat_id: int, msg_id: int, keyword: str, timeout: int = 10):
    """پیام msg_id (که ربات ادیتش می‌کنه) رو دوباره می‌خونه تا دکمه‌ای با keyword توش پیدا بشه"""
    for _ in range(timeout):
        await asyncio.sleep(1)
        m = await client.get_messages(chat_id, ids=msg_id)
        if m and m.buttons:
            for b in _flat_buttons(m):
                if b.text and keyword in b.text:
                    return b
    return None


# ---------- یخچال، ماهی، غذا دادن، پخت و فروش ----------
SELL_BUTTON_KEYWORD = "فروش ماهی"
SOLD_WORDS = ("فروخته", "فروش رفت", "فروخت")
FED_WORDS = ("خوردش", "خورد")
BACK_KEYWORDS = ("BK", "🔙", "بازگشت", "برگشت", "◀", "⬅", "↩")
fish_feed_max = 10**12  # بدون محدودیت قیمت؛ با «fish feed below n» میشه محدود کرد  #      # ماهی پخته‌ی ارزان‌تر از این عدد به پیشی داده میشه (و ماهی خام ارزان‌تر پخته میشه)
fish_sell_min = 50000                # ماهی پخته‌ی با ارزش مساوی یا بیشتر از این عدد فروخته میشه
fish_sell_enabled = True
fish_cook_enabled = True             # همه‌ی ماهی‌های خام پخته میشن
fish_cook_min = 0                    # فقط ماهی خام با ارزش >= این عدد پخته میشه (0 = بدون استثنا همه)
feed_setup_stage = None              # فلوی گفتگویی دستور feed: None | cap | need
feed_setup_cap = None
cook_timers: dict[int, asyncio.Task] = {}


def parse_int(raw: str) -> int | None:
    """عدد با ویرگول یا پسوند k/m: 50000 ، 50,000 ، 50k ، 1.5m"""
    raw = (raw or "").translate(_DIGITS).replace(",", "").replace("،", "").strip().lower()
    mult = 1
    if raw.endswith("k"):
        mult, raw = 1000, raw[:-1]
    elif raw.endswith("m"):
        mult, raw = 1_000_000, raw[:-1]
    try:
        v = int(float(raw) * mult)
        return v if v >= 0 else None
    except ValueError:
        return None


def _lvl(x: str) -> str:
    """«کمیاب 🧿» → «کمیاب» (فقط حروف، برای مقایسه‌ی سطح ماهی‌ها)"""
    return re.sub(r"[\W\d_]+", "", x or "")


def parse_fridge(msg):
    """پیام یخچال رو به لیست ماهی‌ها تبدیل می‌کنه (به‌ترتیب نمایش).
    هر ماهی: {'emoji', 'status': cooked|raw|other, 'cooked', 'raw', 'value'}"""
    text = _clean(msg.raw_text)
    fish, cur = [], None
    for ln in text.splitlines():
        if "|" in ln and any(w in ln for w in ("پخته", "خام", "پخت", "پخیدن")):
            status = "cooked" if "پخته" in ln else ("raw" if "خام" in ln else "other")
            cur = {"emoji": ln.split("|")[0].strip(), "status": status,
                   "cooked": status == "cooked", "raw": status == "raw", "value": None, "food": None, "level": None}
            hl = re.search(r"سطح[^:\n]*[:：]\s*(.+)", ln)
            if hl:
                cur["level"] = _lvl(hl.group(1))
            fish.append(cur)
            continue
        if cur is not None:
            ml = re.search(r"سطح[^:\n]*[:：]\s*(.+)", ln)
            if ml and cur.get("level") is None:
                cur["level"] = _lvl(ml.group(1))
                continue
            mf = re.search(r"ارزش\s*غذایی[^\d\n]*(\d+)", ln)
            if mf:
                cur["food"] = int(mf.group(1))
            elif cur["value"] is None:
                m = re.search(r"ارزش[^\d\n:]*:[^\d\n]*([0-9][0-9,]*)", ln)
                if m:
                    cur["value"] = int(m.group(1).replace(",", ""))
    for f in fish:
        if f["value"] is None:
            f["value"] = 0
    return fish


def parse_capacity(text: str):
    """ظرفیت یخچال : 3 / 5  →  (3, 5)  (یا None)"""
    t = _clean(text)
    m = re.search(r"ظرفیت[^\n:：]*[:：][\s‎‏]*(\d+)[\s‎‏]*/[\s‎‏]*(\d+)", t)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_cook_seconds(text: str, mmss: bool = False) -> int | None:
    """«زمان مورد نیاز پخیدن : 5:07» → ساعت:دقیقه (یا ساعت:دقیقه:ثانیه) به ثانیه"""
    t = _clean(text)
    m = re.search(r"زمان[^\n]*?(\d+)\s*:\s*(\d{1,2})(?:\s*:\s*(\d{1,2}))?", t) or \
        re.search(r"(\d+)\s*:\s*(\d{1,2})(?:\s*:\s*(\d{1,2}))?", t)
    if not m:
        return None
    if mmss and m.group(3) is None:      # ربات جدید: «3:45» یعنی ۳ دقیقه و ۴۵ ثانیه
        return int(m.group(1)) * 60 + int(m.group(2))
    h, mi = int(m.group(1)), int(m.group(2))
    s = int(m.group(3)) if m.group(3) else 0
    return h * 3600 + mi * 60 + s


def is_hungry_text(text: str) -> bool:
    return any("شکم" in ln and "گشنمیو" in ln for ln in (text or "").splitlines())


feed_hunger_texts: list[str] = ["گشنمیو", "دیگه کار نمیکنمیو"]   # متن‌هایی که اگه جلوی «شکم» باشن سلف غذا میده
feed_rules: dict[int, int] = {}   # {ظرفیت شکم: حداکثر عددی که تا اون سلف غذا میده}  مثلا feed 15/5 → {15: 5}


_INVIS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\u200b\u200c]")


def _clean(t: str) -> str:
    """علامت‌های نامرئی جهت (LRM/RLM/...) رو برمیداره و ارقام فارسی رو انگلیسی می‌کنه"""
    return _INVIS.sub("", (t or "")).translate(_DIGITS)


def parse_stomach(text: str):
    """«شکم : ... (0 / 12)» → (ظرفیت=12، پُری فعلی=0).
    توی پیام واقعیِ ربات ترتیب «پُری / ظرفیت» هست ولی چون متن راست‌به‌چپ نمایش داده میشه
    روی صفحه برعکس دیده میشه (12 / 0). برای همین به ترتیب تکیه نمی‌کنیم:
    عدد بزرگ‌تر = ظرفیت، عدد کوچک‌تر = پُری فعلی (پُری هیچ‌وقت از ظرفیت بیشتر نمیشه).
    مستقل از متن جلوی شکم (گشنمیو، پره، ...) و از علامت‌های نامرئی"""
    t = _clean(text)
    pat = r"\(\s*(\d+)\s*/\s*(\d+)\s*\)"
    m = None
    for ln in t.splitlines():
        if "شکم" in ln:
            m = re.search(pat, ln)
            if m:
                break
    if not m:
        m = re.search(pat, t)  # اگه «شکم» و عدد توی یه خط نبودن
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return max(a, b), min(a, b)


def _loose(t: str) -> str:
    """برای مقایسه‌ی متن‌ها: نویسه‌های نامرئی و فاصله‌ها حذف، حرف‌های تکراری (گشنمیووو) یکی میشن"""
    t = _clean(normalize(t or "")).replace(" ", "").replace(".", "")
    return re.sub(r"(.)\1+", r"\1", t)


def feed_decision(text: str):
    """(باید غذا بده؟، جای خالی شکم).
    اصلی‌ترین ملاک: اگه خط «شکم» یکی از متن‌های گرسنگیِ تنظیم‌شده رو داشته باشه (feed text add ...)
    ملاک دوم (اختیاری): قانون عددی feed 12/7"""
    t = _clean(text)
    line = next((ln for ln in t.splitlines() if "شکم" in ln), t)
    lline = _loose(line)
    text_hit = any(_loose(p) and _loose(p) in lline for p in feed_hunger_texts)
    st = parse_stomach(text)
    room = None
    rule_hit = False
    if st:
        cap, cur = st
        room = max(cap - cur, 0)
        rule = feed_rules.get(cap)
        rule_hit = rule is not None and cur <= rule
    need = text_hit or rule_hit
    if room == 0:
        need = False
    return need, room


async def safe_click(btn) -> bool:
    """کلیک روی دکمه‌ی اینلاین؛ خطای «BOT_RESPONSE_TIMEOUT» و مشابهش رو نادیده می‌گیره"""
    try:
        await btn.click()
        return True
    except Exception as e:
        if "TIMEOUT" in str(e).upper() or "timed out" in str(e).lower():
            return True  # ربات جواب کوتاه نداد ولی کلیک ثبت شده
        print(f"کلیک ناموفق: {e}")
        return False


async def wait_msg(chat_id: int, msg_id: int, pred, timeout: int = 10):
    """پیام (که ربات ادیتش می‌کنه) رو دوباره می‌خونه تا pred(m) درست بشه"""
    for _ in range(int(timeout / 0.7) + 1):
        await asyncio.sleep(0.7)
        m = await client.get_messages(chat_id, ids=msg_id)
        if m and pred(m):
            return m
    return None


def find_back_button(m):
    """دکمه‌ی قرمز «BK» (بازگشت). اگه متنش ایموجی/خالی بود: تک‌دکمه‌ی آخرین ردیف"""
    btns = _flat_buttons(m)
    for b in btns:
        t = b.text or ""
        if any(k.lower() in t.lower() for k in BACK_KEYWORDS):
            return b
    if len(btns) == 1:
        return btns[0]
    forbidden = ("فروش", "بده", "بپوخش", "ارتقا", "✅", "❌", "✔", "✖")
    if m.buttons:
        last = m.buttons[-1]
        if len(last) == 1 and not any(x in (last[0].text or "") for x in forbidden):
            return last[0]
    return None


async def click_back(chat_id: int, msg_id: int) -> bool:
    """دکمه‌ی BK رو می‌زنه تا برگرده به منوی قبلی (یخچال)"""
    for _ in range(12):
        await asyncio.sleep(0.7)
        m = await client.get_messages(chat_id, ids=msg_id)
        if m and m.buttons:
            b = find_back_button(m)
            if b:
                ok = await safe_click(b)
                await asyncio.sleep(1.0)
                return ok
    print("دکمه‌ی BK پیدا نشد")
    return False


def map_fish_buttons(fmsg, fish) -> bool:
    """دکمه‌ی هر ماهی رو پیدا می‌کنه: اول با ایموجی گوشه‌ی هر ماهی، وگرنه به‌ترتیب نمایش"""
    btns = [b for b in _flat_buttons(fmsg)
            if "ارتقا" not in (b.text or "") and not any(k.lower() in (b.text or "").lower() for k in BACK_KEYWORDS)]
    if len(btns) != len(fish):
        print(f"fish: تعداد ماهی‌ها ({len(fish)}) با دکمه‌ها ({len(btns)}) نمی‌خونه")
        return False
    matched = []
    for f in fish:
        hits = [b for b in btns if f["emoji"] and (b.text or "").strip() == f["emoji"]]
        matched.append(hits[0] if len(hits) == 1 else None)
    if None not in matched and len({id(b) for b in matched}) == len(fish):
        for f, b in zip(fish, matched):
            f["btn"] = b
    else:
        for f, b in zip(fish, btns):
            f["btn"] = b
    return True


async def sell_fish(chat_id: int, msg_id: int, f) -> bool:
    """ماهی → «فروش ماهی» → صبر برای «فروخته شد» → BK"""
    if not await safe_click(f["btn"]):
        return False
    b = await wait_button(chat_id, msg_id, SELL_BUTTON_KEYWORD)
    if not b or not await safe_click(b):
        return False
    m = await wait_msg(chat_id, msg_id, lambda m: any(w in (m.raw_text or "") for w in SOLD_WORDS), 8)
    await click_back(chat_id, msg_id)
    print(f"fish: ماهی با ارزش {f['value']:,} {'فروخته شد' if m else '(تأیید فروش دیده نشد)'}")
    return bool(m)


async def feed_fish(chat_id: int, msg_id: int, f) -> bool:
    """ماهی پخته → «بده پیشی بخوره» → «پیشی خوردش» → BK"""
    if not await safe_click(f["btn"]):
        print("🍖 غذا دادن: کلیک روی ماهی ناموفق بود")
        return False
    b = await wait_button(chat_id, msg_id, FEED_BUTTON_KEYWORD)
    if not b:
        m_now = await client.get_messages(chat_id, ids=msg_id)
        seen = [x.text for x in _flat_buttons(m_now)] if m_now else []
        print(f"🍖 غذا دادن: دکمه‌ی «{FEED_BUTTON_KEYWORD}» پیدا نشد؛ دکمه‌های صفحه: {seen}")
        return False
    if not await safe_click(b):
        print("🍖 غذا دادن: کلیک روی «بده پیشی بخوره» ناموفق بود")
        return False
    m = await wait_msg(chat_id, msg_id, lambda m: any(w in (m.raw_text or "") for w in FED_WORDS), 8)
    await click_back(chat_id, msg_id)
    print("fish: ماهی پخته به پیشی داده شد" if m else "fish: ماهی داده شد (تأیید «خوردش» دیده نشد)")
    return True


def _fridge_is_home(m) -> bool:
    t = _clean(m.raw_text or "")
    return "ظرفیت یخچال" in t or "سطح یخچال" in t


def _has_explicit_back(m) -> bool:
    return any(any(k.lower() in (b.text or "").lower() for k in BACK_KEYWORDS) for b in _flat_buttons(m))


async def fridge_back(chat_id: int, msg_id: int, tries: int = 3) -> bool:
    """با دکمه‌ی BK آن‌قدر برمی‌گرده تا صفحه‌ی اصلی یخچال. اگه همین الان توی صفحه‌ی اصلیه، کاری نمی‌کنه"""
    presses = 0
    for _ in range(14):
        m = await client.get_messages(chat_id, ids=msg_id)
        if not m:
            return False
        if _fridge_is_home(m):
            return True
        bk = find_back_button(m) if m.buttons else None
        if bk is None or _confirm_txt(m.raw_text):
            await asyncio.sleep(0.7)
            continue
        if presses >= tries:
            break
        sig = _btn_texts(m)
        if not await safe_click(bk):
            return False
        presses += 1
        await wait_msg(chat_id, msg_id, lambda x: _fridge_is_home(x) or _btn_texts(x) != sig, 8)
    m = await client.get_messages(chat_id, ids=msg_id)
    return bool(m and _fridge_is_home(m))


async def back_to_fridge(chat_id: int, fmsg):
    """جواب «یخچال میویی» گاهی صفحه‌ی پخت (در حال پختن) میشه نه خودِ یخچال؛ اگه BK داشت می‌زنیم تا برسه به یخچال.
    خروجی: (پیام یخچال، متن صفحه‌ی اولیه اگه BK زده شد وگرنه None)"""
    if _fridge_is_home(fmsg) or not fmsg.buttons:
        return fmsg, None
    lt = _loose(fmsg.raw_text or "")
    if not ("پختوپز" in lt or "میپزه" in lt or _has_explicit_back(fmsg)):
        return fmsg, None
    print("یخچال: پاسخ ربات صفحه‌ی اصلی یخچال نبود ولی دکمه‌ی BK داشت → BK می‌زنم")
    first = fmsg.raw_text or ""
    if await fridge_back(chat_id, fmsg.id):
        return (await client.get_messages(chat_id, ids=fmsg.id)) or fmsg, first
    return fmsg, None


async def cook_fish(chat_id: int, msg_id: int, f):
    """ماهی خام → «بپوخش» → تأیید با دکمه‌ی سبز ✓ → «داره میپزه» → BK تا صفحه‌ی اصلی یخچال. زمان پخت (ثانیه) یا None"""
    if not await safe_click(f["btn"]):
        return None
    b = await wait_button(chat_id, msg_id, COOK_BUTTON_KEYWORD)
    if not b or not await safe_click(b):
        await fridge_back(chat_id, msg_id)
        return None
    m = await wait_msg(chat_id, msg_id, lambda m: _confirm_txt((m.raw_text or "")) and bool(m.buttons), 8)
    if not m:
        print("cook: منوی تأیید نیومد (اگه ربات متن تأیید رو عوض کرده: confirm text add <متن>)")
        await fridge_back(chat_id, msg_id)
        return None
    # دکمه‌ی سبز ✓ اولین دکمه است؛ قرمز × (انصراف) دومیه
    if await confirm_click(chat_id, m) is None:
        print("cook: تأیید پخت انجام نشد")
        await fridge_back(chat_id, msg_id)
        return None
    m2 = await wait_msg(chat_id, msg_id,
                        lambda m: not _confirm_txt((m.raw_text or "")) and ("پخیدن" in (m.raw_text or "") or "پخت" in (m.raw_text or "") or "میپزه" in _loose(m.raw_text or "")), 10)
    seconds = parse_cook_seconds(m2.raw_text, mmss=True) if m2 else None
    await fridge_back(chat_id, msg_id)
    print(f"cook: پخت شروع شد، زمان لازم {seconds} ثانیه" if seconds else "cook: پخت شروع شد (زمان خونده نشد)")
    return seconds if seconds else 3600


# ---------- ماهی‌گیری ----------
# «ماهی» می‌فرسته → «بندازش تو یخچال». هر ۵۰ دقیقه یک بار مجازه.
# اگه کرم داشته باشه، زیر پیام ربات دکمه‌ی سبزِ «استفاده از کرم» میاد و سلف روش می‌زنه.
FISH_DEFAULT_COOLDOWN = 50 * 60
WORM_KEYWORD = "کرم"
fish_cfg = {"auto": True, "interval": FISH_DEFAULT_COOLDOWN,
            "only_hungry": False,     # فقط وقتی پیشی گرسنه‌ست ماهی بگیر
            "fridge_max": 2,          # فقط وقتی ماهی‌های یخچال کمتر از این عدده ماهی بگیر (0 = بدون محدودیت)
            "recover": True,          # «این ماهی از قبل تو یخچال موجوده» → ماهی هم‌سطح رو به پیشی بده و ماهی گرفته‌شده رو بنداز تو یخچال
            "recover_sell": False}    # اگه خوراندن نشد، ماهی هم‌سطح فروخته بشه   # auto: ماهی‌گیری خودکار هر interval ثانیه
fish_state = {"next": 0.0}                                       # تا این زمان (epoch) ماهی‌گیری ممکن نیست
fish_task = None


def _fish_buttons(msg):
    btns = _flat_buttons(msg)
    store = next((b for b in btns if FRIDGE_STORE_KEYWORD in (b.text or "")), None)
    worm = next((b for b in btns if WORM_KEYWORD in (b.text or "")), None)
    return store, worm


def _fish_sig(m):
    return (_clean(m.raw_text), tuple((b.text or "") for b in _flat_buttons(m)))


async def _fish_next_msg(chat_id: int, msg, sig):
    """بعد از کلیک: منتظر ادیتِ همون پیام یا پیامِ تازه‌ی ربات (با دکمه‌ی یخچال/کرم) می‌مونه"""
    for _ in range(8):
        await asyncio.sleep(1)
        m = await client.get_messages(chat_id, ids=msg.id)
        if m and _fish_sig(m) != sig:
            return m
        async for x in client.iter_messages(chat_id, limit=8):
            if x.id > msg.id and not x.out and x.buttons and await _is_bot_msg(x) and any(_fish_buttons(x)):
                return x
    return None


def _fish_already_in_fridge(text: str) -> bool:
    t = _clean(normalize(text or ""))
    return "از قبل" in t and "یخچال" in t and "موجود" in t


async def _fridge_fish_count(chat_id: int):
    sent = await client.send_message(chat_id, FRIDGE_TRIGGER_TEXT)
    fmsg = await wait_reply_to(chat_id, sent.id, need_buttons=False)
    if not fmsg:
        return None
    fmsg, _ = await back_to_fridge(chat_id, fmsg)
    return len(parse_fridge(fmsg))


async def recover_duplicate_fish(chat_id: int, fish_msg, fish_text: str) -> bool:
    """ربات گفت «این ماهی از قبل تو یخچال موجوده»:
    «یخچال میویی» → ماهیِ هم‌سطحِ ماهی گرفته‌شده → «بده پیشی بخوره» → برگشت به پیام ماهی‌گیری → «بندازش تو یخچال»"""
    ct = _clean(fish_text or "")
    lm = re.search(r"سطح[^:\n]*[:：]\s*([^\n]+)", ct)
    vm = re.search(r"ارزش\s*[:：][^\d\n]*(\d[\d,]*)", ct)
    level = _lvl(lm.group(1)) if lm else ""
    value = _toint(vm.group(1)) if vm else 0
    print(f"🎣 ماهی تکراری (سطح «{level}»، ارزش {value}) → یخچال رو چک می‌کنم")
    sent = await client.send_message(chat_id, FRIDGE_TRIGGER_TEXT)
    fmsg = await wait_reply_to(chat_id, sent.id, need_buttons=False)
    if not fmsg:
        print("🎣 بازیابی: پاسخ یخچال نیومد")
        return False
    fmsg, _ = await back_to_fridge(chat_id, fmsg)
    fish = parse_fridge(fmsg)
    if not fish or not map_fish_buttons(fmsg, fish):
        print("🎣 بازیابی: ماهی‌های یخچال یا دکمه‌ها خونده نشد")
        return False
    same = [f for f in fish if level and f.get("level") and f["level"] == level]
    if not same:
        same = sorted(fish, key=lambda f: abs((f["value"] or 0) - (value or 0)))[:1]
        print("🎣 بازیابی: سطح ماهی‌های یخچال خونده نشد؛ ماهیِ نزدیک از نظر ارزش انتخاب شد")
    pick = sorted(same, key=lambda f: (not f["cooked"], f["value"]))[0]      # اول ماهی پخته
    fed = await feed_fish(chat_id, fmsg.id, pick)
    if not fed:
        await click_back(chat_id, fmsg.id)
        if fish_cfg.get("recover_sell"):
            fed = await sell_fish(chat_id, fmsg.id, pick)
    if not fed:
        print("🎣 بازیابی: ماهی هم‌سطح نه خورانده شد نه فروخته (شاید خامه یا پیشی سیره)")
        return False
    await asyncio.sleep(1.5)
    m = await client.get_messages(chat_id, ids=fish_msg.id)
    store = _fish_buttons(m)[0] if m else None
    if not store:
        print("🎣 بازیابی: دکمه‌ی «بندازش تو یخچال» روی پیام ماهی‌گیری دیگه نیست")
        return False
    sig = _fish_sig(m)
    if not await safe_click(store):
        return False
    nxt = await _fish_next_msg(chat_id, m, sig)
    if nxt and _fish_already_in_fridge(nxt.raw_text):
        print("🎣 بازیابی: هنوز میگه از قبل توی یخچاله")
        return False
    print("🎣 ماهی گرفته‌شده بعد از خوراندن ماهی هم‌سطح توی یخچال انداخته شد ✅")
    return True


async def fish_once(chat_id: int, force: bool = False, fridge_count=None) -> bool:
    """یک نوبت ماهی‌گیری: ماهی → (یخچال) → (اگه کرم بود: کرم → یخچال ...). True یعنی ماهی گرفته و توی یخچال انداخته شد"""
    now = time.time()
    if not force and now < fish_state["next"]:
        print(f"🎣 ماهی‌گیری: {int((fish_state['next'] - now) / 60) + 1} دقیقه‌ی دیگه مجازه")
        return False
    fmax = int(fish_cfg.get("fridge_max", 2) or 0)
    if not force and fmax:
        n = fridge_count if fridge_count is not None else await _fridge_fish_count(chat_id)
        if n is not None and n >= fmax:
            print(f"🎣 ماهی‌گیری: توی یخچال {n} ماهی هست (حد {fmax}) → ماهی نمی‌گیره")
            fish_state["next"] = time.time() + 600
            return False
    sent = await client.send_message(chat_id, FISH_TRIGGER_TEXT)
    msg = await wait_reply_to(chat_id, sent.id, timeout=25, need_buttons=False)   # اول استیکر/ایموجی متحرک میاد، بعد پیام اصلی
    if not msg:
        print("🎣 ماهی‌گیری: پاسخ ماهی نیومد")
        fish_state["next"] = time.time() + 120
        return False
    caught = worms = 0
    for _ in range(10):
        store, worm = _fish_buttons(msg)
        btn = store or worm
        if not btn:
            break
        sig = _fish_sig(msg)
        fish_text = msg.raw_text or ""      # متن پیام ماهی (سطح/ارزش) قبل از کلیک، چون بعدش ادیت میشه
        if not await safe_click(btn):
            break
        nxt = await _fish_next_msg(chat_id, msg, sig)
        if btn is store:
            if nxt and _fish_already_in_fridge(nxt.raw_text):
                print("🎣 ربات گفت این ماهی از قبل توی یخچاله ❌")
                if fish_cfg.get("recover", True):
                    try:
                        if await recover_duplicate_fish(chat_id, nxt, fish_text):
                            caught += 1
                    except Exception as e:
                        print(f"🎣 بازیابی ماهی تکراری: {e}")
                msg = nxt
                break
            caught += 1
            print("🎣 ماهی توی یخچال انداخته شد ✅")
        else:
            worms += 1
            print("🎣 از کرم استفاده شد 🪱")
        if not nxt:
            break
        msg = nxt
    txt = _clean(msg.raw_text)
    secs = parse_fa_duration(txt) if any(u in txt for u in ("ساعت", "دقیقه", "ثانیه")) else None
    if secs is None:   # «باید 6:29 صبر کنی» = ۶ دقیقه و ۲۹ ثانیه (یا ساعت:دقیقه:ثانیه)
        cm = re.search(r"(\d+)\s*:\s*(\d+)(?:\s*:\s*(\d+))?", txt)
        if cm:
            a1, a2, a3 = int(cm.group(1)), int(cm.group(2)), cm.group(3)
            secs = a1 * 3600 + a2 * 60 + int(a3) if a3 else a1 * 60 + a2
    if secs:
        fish_state["next"] = time.time() + secs + 30
    elif caught or worms:
        fish_state["next"] = time.time() + fish_cfg["interval"] + 30
    else:
        fish_state["next"] = time.time() + min(600, fish_cfg["interval"])
    if not caught and not worms:
        print("🎣 ماهی‌گیری انجام نشد؛ پیام ربات: " + txt[:100].replace("\n", " | "))
    return caught > 0


async def catch_and_store_fish(chat_id: int, fridge_count=None) -> bool:
    return await fish_once(chat_id, fridge_count=fridge_count)


async def fish_loop():
    """ماهی‌گیری خودکار؛ اولویت با یخچال و غذا دادنه (اگه فلوی یخچال در حال اجرا باشه صبر می‌کنه)"""
    try:
        while True:
            wait = fish_state["next"] - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 600))
                continue
            if fish_cfg["auto"] and not fish_cfg.get("only_hungry") and farm_chat_id is not None and not feed_lock.locked():
                try:
                    async with feed_lock:
                        await fish_once(farm_chat_id)
                except Exception as e:
                    print(f"خطا توی ماهی‌گیری: {e}")
                if fish_state["next"] <= time.time():
                    fish_state["next"] = time.time() + 300
            else:
                await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass


def start_fish_loop():
    global fish_task
    if fish_task:
        fish_task.cancel()
    fish_task = asyncio.create_task(fish_loop())


def stop_fish_loop():
    global fish_task
    if fish_task:
        fish_task.cancel()
        fish_task = None


def schedule_post_cook(chat_id: int, seconds: int):
    """بعد از تمام شدن زمان پخت، یخچال رو چک می‌کنه (فروش ماهی‌های گرون، ...)"""
    old = cook_timers.pop(chat_id, None)
    if old and old is not asyncio.current_task():
        old.cancel()
    cook_timers[chat_id] = asyncio.create_task(_post_cook(chat_id, seconds + 60))


async def _post_cook(chat_id: int, wait: float):
    try:
        await asyncio.sleep(wait)
        async with feed_lock:
            await fridge_session(chat_id, hungry=False, allow_fish=False)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"post-cook: {e}")


async def fridge_session(chat_id: int, hungry: bool, allow_fish: bool, room=None):
    """یه نشست کامل یخچال (بعد از هر کار با BK به یخچال برمی‌گرده و دوباره بررسی میشه):
    ۱) ماهی پخته با ارزش >= حد فروش → فروش
    ۲) اگه باید غذا بده: ماهی پخته‌ای که ارزش غذاییش تو جای خالی شکم جا بشه → بده بخوره
    ۳) همه‌ی ماهی‌های خام (بدون استثنا) → بپوخش → ✓ → BK، و تایمر/پیام پخت
    ۴) اگه یخچال پر نیست (دو عدد ظرفیت برابر نیستن) → «ماهی» + «بندازش تو یخچال»"""
    was_hungry = hungry
    for _round in range(10):
        try:
            sent = await client.send_message(chat_id, FRIDGE_TRIGGER_TEXT)
            fmsg = await wait_reply_to(chat_id, sent.id, need_buttons=False)
            if not fmsg:
                print("fridge: پاسخ یخچال نیومد")
                return
            fmsg, cook_page = await back_to_fridge(chat_id, fmsg)
            if cook_page:
                rem = parse_cook_seconds(cook_page, mmss=True)
                t_old = cook_timers.get(chat_id)
                if rem and (not t_old or t_old.done()):
                    schedule_post_cook(chat_id, rem)
            fish = parse_fridge(fmsg)
            n_fish = len(fish)
            cap = parse_capacity(fmsg.raw_text)
            if fish and not map_fish_buttons(fmsg, fish):
                print("یخچال: دکمه‌ها با ماهی‌ها نمی‌خونن؛ فقط ماهی‌گیری انجام میشه")
                fish = []
            cooking = any(f["status"] == "other" for f in fish) or bool(cook_page)
            print(f"یخچال: {len(fish)} ماهی (پخته {sum(1 for f in fish if f['cooked'])}، خام {sum(1 for f in fish if f['raw'])}) | ظرفیت {cap} | جای خالی شکم {room} | باید غذا بده؟ {hungry}")

            def fits(f):  # ارزش غذایی ماهی نباید از جای خالی شکم بیشتر باشه
                return room is None or f["food"] is None or f["food"] <= room
            best = lambda f: (-(f["food"] or 0), f["value"])

            # ۱) فروش
            if fish_sell_enabled:
                sellable = [f for f in fish if f["cooked"] and f["value"] >= fish_sell_min]
                if sellable:
                    if await sell_fish(chat_id, fmsg.id, max(sellable, key=lambda f: f["value"])):
                        continue
                    return

            # ۲) غذا دادن
            print(f"🍖 بررسی غذا: گرسنه={hungry} | جای خالی={room} | ماهی‌ها (پخته؟، غذایی، ارزش): "
                  + str([(f['cooked'], f['food'], f['value']) for f in fish]) + f" | حد غذادهی={fish_feed_max}")
            if hungry:
                cooked_fit = [f for f in fish if f["cooked"] and f["value"] < fish_feed_max and fits(f)]
                if not cooked_fit:
                    print("یخچال: ماهی پخته‌ی مناسب پیدا نشد (ارزش غذایی بیشتر از جای خالی شکم، یا گرون‌تر از حد غذادهی، یا ماهی پخته نیست)")
                if cooked_fit:
                    if not await feed_fish(chat_id, fmsg.id, min(cooked_fit, key=best)):
                        return
                    feed_fed["n"] += 1
                    hungry = False
                    continue

            # ۳) پختن همه‌ی ماهی‌های خام
            if fish_cook_enabled and not cooking:
                raws = [f for f in fish if f["raw"] and f["value"] >= fish_cook_min]
                if raws:
                    pool = [f for f in raws if fits(f)] if hungry else []
                    pick = min(pool, key=best) if pool else max(raws, key=lambda f: f["value"])
                    seconds = await cook_fish(chat_id, fmsg.id, pick)
                    if seconds:
                        schedule_post_cook(chat_id, seconds)
                    return

            # ۴) یخچال پر نیست → ماهی بگیر
            fmax = int(fish_cfg.get("fridge_max", 2) or 0)
            if allow_fish and (not fmax or n_fish < fmax) and (cap is None or cap[0] != cap[1]) \
                    and (was_hungry or not fish_cfg.get("only_hungry")):
                allow_fish = False
                if await catch_and_store_fish(chat_id, n_fish):
                    await asyncio.sleep(2)
                    continue
            return
        except Exception as e:
            print(f"خطا توی یخچال: {e}")
            return


# =====================================================================
#                          غذا دادن به پیشی
# ---------------------------------------------------------------------
#  راه‌اندازها (هرکدوم باشه کافیه):
#    ۱) هر پیام/ادیتِ ربات میویی توی گروه فارم که متن گرسنگی داشته باشه
#    ۲) جواب «پیشی» بعد از فارم   ۳) چک زمان‌دار (feed interval)   ۴) دستور feed now
#  کار: یخچال میویی → ماهی پخته‌ای که ارزش غذاییش تو جای خالی شکم جا بشه → «بده پیشی بخوره»
#       → دوباره شکم رو چک می‌کنه تا سیر بشه (حداکثر ۴ دور)؛ اگه ماهی مناسب نبود: پختن خام‌ها / ماهی‌گیری
# =====================================================================
FEED_COOLDOWN = 60          # ثانیه؛ جلوی اجرای تکراریِ پشت‌سرهم
FEED_MAX_ROUNDS = 4         # حداکثر چند بار پشت‌سرهم غذا بده تا شکم سیر بشه
feed_state = {"last": 0.0}
feed_fed = {"n": 0}         # تعداد دفعاتی که واقعاً ماهی خورانده شده (توسط fridge_session زیاد میشه)


def _is_farm_chat(cid) -> bool:
    if farm_chat_id is None or cid is None:
        return False
    a = abs(int(farm_chat_id))
    return cid in (farm_chat_id, a, -a, int("-100" + str(a)))


def hunger_in_text(text: str):
    """(گرسنه‌ست؟، جای خالی شکم). اگه خط «شکم» نباشه، متن‌های گرسنگی توی کل پیام هم جستجو میشن"""
    need, room = feed_decision(text)
    if not need and "شکم" not in _clean(text):
        low = _loose(text)
        need = any(_loose(p) and _loose(p) in low for p in feed_hunger_texts)
        room = None
    return need, room


async def feed_run(chat_id: int, room=None, source: str = "") -> bool:
    """فلوی کامل غذا دادن؛ False یعنی اجرا نشد (قفل یا cooldown)"""
    if feed_lock.locked():
        print(f"🍖 غذادهی ({source}): یه فلوی یخچال در حال اجراست، رد شد")
        return False
    if time.time() - feed_state["last"] < FEED_COOLDOWN:
        print(f"🍖 غذادهی ({source}): همین چند لحظه پیش اجرا شده، تکراری رد شد")
        return False
    feed_state["last"] = time.time()
    async with feed_lock:
        print(f"🍖 غذادهی شروع شد ({source}) — جای خالی شکم: {room}")
        await asyncio.sleep(2)
        for rnd in range(FEED_MAX_ROUNDS):
            before = feed_fed["n"]
            await fridge_session(chat_id, hungry=True, allow_fish=True, room=room)
            if feed_fed["n"] == before:
                break                      # ماهی‌ای خورونده نشد؛ بیشتر تلاش نمی‌کنیم
            sent = await client.send_message(chat_id, MEOWIE_TRIGGER_TEXT)
            reply = await wait_reply_to(chat_id, sent.id, need_buttons=False)
            if not reply:
                break
            need, room = hunger_in_text(reply.raw_text)
            if not need:
                print("🍖 شکم پیشی سیر شد ✅")
                break
        print("🍖 غذادهی تموم شد")
    return True


async def feed_probe(chat_id: int, source: str = "", force: bool = False) -> bool:
    """«پیشی» می‌فرسته، شکم رو می‌خونه و اگه گرسنه بود (یا force) غذا میده"""
    if feed_lock.locked():
        return False
    sent = await client.send_message(chat_id, MEOWIE_TRIGGER_TEXT)
    reply = await wait_reply_to(chat_id, sent.id, need_buttons=False)
    if not reply:
        print(f"🍖 غذادهی ({source}): جواب «پیشی» نیومد")
        return False
    need, room = hunger_in_text(reply.raw_text)
    print(f"🍖 چک شکم ({source}): شکم={parse_stomach(reply.raw_text)} | گرسنه؟ {need}")
    if need or force:
        if force:
            feed_state["last"] = 0.0
        return await feed_run(chat_id, room, source)
    return False


async def feed_cat_flow(chat_id: int, room=None):
    """(سازگاری با بقیه‌ی کد) همون feed_run"""
    await feed_run(chat_id, room, "فارم")


async def hunger_watch(event):
    """هر پیام یا ادیتِ پیام ربات میویی توی گروه فارم که متن گرسنگی داشته باشه → غذا دادن"""
    try:
        if not feed_enabled or event.out or not _is_farm_chat(event.chat_id):
            return
        text = event.raw_text or ""
        if not text or feed_lock.locked():
            return
        need, room = hunger_in_text(text)
        if not need:
            if "شکم" in _clean(text):
                print(f"پیام شکم دیده شد ولی متن گرسنگی نبود → غذا نمیده | شکم: {parse_stomach(text)}")
            return
        sender = await event.get_sender()
        if not getattr(sender, "bot", False):
            return
        asyncio.create_task(feed_run(farm_chat_id, room, "متن گرسنگی از ربات"))
    except Exception as e:
        print(f"خطا توی hunger_watch: {e}")


client.add_event_handler(hunger_watch, events.NewMessage(incoming=True))
client.add_event_handler(hunger_watch, events.MessageEdited(incoming=True))


# ---------- کارخونه میویی ----------
FACTORY_TRIGGER_TEXT = "کارخونه میویی"
FACTORY_CATEGORIES = {"1": "آبنبات", "2": "کیک", "3": "تکنولوژی", "4": "خودرو", "5": "هواپیما", "6": "تکنولوژی پیشرفته"}
FACTORY_SOLD_WORDS = ("فروخته", "فروش رفت", "فروخت", "با موفقیت")
factory_enabled = False
factory_chat_id = None            # گروهی که کارخونه توش بررسی میشه (پیش‌فرض: گروه فارم)
factory_sell_min = None           # قیمت بازار >= این عدد → فروش محصول (None = فروش خاموش)
factory_cost_max = 0              # هزینه‌ی ساخت هر عدد باید <= این عدد باشه (0 = بدون محدودیت)
factory_targets = [{"cat": "تکنولوژی پیشرفته", "item": 1}]   # لیست اولویت تولید: هر کدوم «محصول از دسته»؛ اولی که شد تولید میشه
# ترتیب دکمه‌های هر دسته از سمت راست (مثل نمایش توی تلگرام) و اسم/شکل‌هایی که میشه براشون نوشت
FACTORY_LAYOUT = {
    "آبنبات": [("🍫", "شکلات"), ("🍭", "آبنبات چوبی", "آبنبات"), ("🎊", "جشن")],
    "کیک": [("🎂", "کیک تولد"), ("🍰", "کیک"), ("🧁", "کاپ کیک")],
    "تکنولوژی": [("🤖", "ربات"), ("💻", "لپ تاپ", "لپتاپ"), ("📱", "موبایل")],
    "خودرو": [("🏎", "ماشین مسابقه"), ("🚕", "تاکسی"), ("🚗", "ماشین")],
    "هواپیما": [("🚀", "موشک"), ("🛩", "جت"), ("✈", "هواپیما")],
    "تکنولوژی پیشرفته": [("⭐", "ستاره", "star"), ("AI", "ای آی", "هوش مصنوعی"), ("HPC", "اچ پی سی")],
}
factory_percent = 100             # درصد پر کردن انبار: 25 / 50 / 75 / 100
factory_interval_seconds = 3600
factory_busy_until = 0.0          # تا این زمان (epoch) تولید در جریانه
factory_task = None
factory_timer = None
factory_lock = asyncio.Lock()
_FA_ONLY = re.compile("[^؀-ۿ ]")


def _fa(t) -> str:
    """فقط حروف فارسی (ایموجی و عدد و علامت حذف میشه) برای مقایسه‌ی متن دکمه‌ها"""
    return re.sub(r"\s+", " ", _FA_ONLY.sub("", normalize(_clean(t or "")))).strip()


def _toint(x: str) -> int:
    return int(x.replace(",", ""))


def parse_factory_storage(text: str):
    m = re.search(r"ظرفیت\s*انبار[^\d\n]*(\d[\d,]*)\s*/\s*(\d[\d,]*)", _clean(text))
    return (_toint(m.group(1)), _toint(m.group(2))) if m else None


def parse_market_price(text: str):
    m = re.search(r"قیمت[^\n:]*بازار[^\d\n]*(\d[\d,]*)", _clean(text))
    return _toint(m.group(1)) if m else None


def parse_unit_cost(text: str):
    m = re.search(r"هر\s*یک\s*عدد[^\d\n]*(\d[\d,]*)", _clean(text))
    return _toint(m.group(1)) if m else None


def _factory_is_main(m) -> bool:
    names = [_fa(b.text) for b in _flat_buttons(m)]
    return any("کارکنان" in n or "دستگاه" in n for n in names)


def _pick_btn(m, exact=None, contains=None):
    for b in _flat_buttons(m):
        n = _fa(b.text)
        if exact is not None and n == exact:
            return b
        if contains is not None and contains in n:
            return b
    return None


def _confirm_btn(m):
    for b in _flat_buttons(m):
        if any(c in (b.text or "") for c in ("✅", "✔", "☑", "✓", "تایید", "تأیید", "بله")):
            return b
    return None


async def factory_home(chat_id: int, mid: int) -> bool:
    """با دکمه‌ی قرمز BK آن‌قدر برمی‌گرده تا صفحه‌ی اول کارخونه"""
    for _ in range(6):
        m = await client.get_messages(chat_id, ids=mid)
        if m and _factory_is_main(m):
            return True
        if not await click_back(chat_id, mid):
            return False
        await asyncio.sleep(0.8)
    m = await client.get_messages(chat_id, ids=mid)
    return bool(m and _factory_is_main(m))


async def factory_sell_flow(chat_id: int, mid: int) -> int:
    """انبار → هر محصول → اگه قیمت بازار >= حد فروش: «فروش محصول» (و تیک سبز) → BK ... تا صفحه‌ی اول"""
    sold, i = 0, 0
    for _ in range(12):
        m = await client.get_messages(chat_id, ids=mid)
        if not m or not _factory_is_main(m):
            if not await factory_home(chat_id, mid):
                return sold
            m = await client.get_messages(chat_id, ids=mid)
        b = _pick_btn(m, exact="انبار")
        if not b or not await safe_click(b):
            print("کارخونه: دکمه‌ی انبار پیدا نشد")
            return sold
        wh = await wait_msg(chat_id, mid, lambda x: bool(x.buttons) and not _factory_is_main(x), 8)
        if not wh:
            return sold
        bk = find_back_button(wh)
        prods = [x for x in _flat_buttons(wh) if x is not bk]
        if i >= len(prods):
            break
        if not await safe_click(prods[i]):
            return sold
        pm = await wait_msg(chat_id, mid, lambda x: "محصول انتخابی" in _clean(x.raw_text) or "بازار" in _clean(x.raw_text), 8)
        if not pm:
            print("کارخونه: صفحه‌ی محصول باز نشد")
            i += 1
            continue
        price = parse_market_price(pm.raw_text)
        if price is not None and factory_sell_min is not None and price >= factory_sell_min:
            sb = _pick_btn(pm, contains="فروش")
            if sb and await safe_click(sb):
                r = await wait_msg(chat_id, mid, lambda x: any(w in _clean(x.raw_text) for w in FACTORY_SOLD_WORDS) or "محصول انتخابی" not in _clean(x.raw_text) or _confirm_btn(x) is not None, 8)
                if r is not None and not any(w in _clean(r.raw_text) for w in FACTORY_SOLD_WORDS):
                    cb = _confirm_btn(r)
                    if cb and await safe_click(cb):   # تیک سبز
                        await wait_msg(chat_id, mid, lambda x: any(w in _clean(x.raw_text) for w in FACTORY_SOLD_WORDS) or _confirm_btn(x) is None, 8)
                sold += 1
                print(f"کارخونه: محصول با قیمت بازار {price:,} فروخته شد")
                await factory_home(chat_id, mid)
                continue  # لیست انبار کوتاه شده؛ همون اندیس دوباره
            i += 1
        else:
            print(f"کارخونه: قیمت بازار {price} به حد فروش {factory_sell_min} نرسیده؛ فروخته نشد")
            i += 1
        await factory_home(chat_id, mid)
    await factory_home(chat_id, mid)
    return sold


POS_WORDS = {"right": "right", "r": "right", "راست": "right",
             "left": "left", "l": "left", "چپ": "left",
             "middle": "middle", "mid": "middle", "center": "middle", "وسط": "middle", "وسطی": "middle"}
POS_FA = {"right": "دکمه‌ی سمت راست", "middle": "دکمه‌ی وسط", "left": "دکمه‌ی سمت چپ"}


def _pos_index(pos: str, n: int) -> int:
    """موقعیت روی صفحه → اندیس دکمه. تلگرام دکمه‌های یه ردیف رو چپ→راست میچینه: راست = آخری، چپ = اولی"""
    return {"right": n - 1, "left": 0, "middle": (n - 1) // 2}[pos]


def _item_label(item) -> str:
    return POS_FA.get(item, str(item))


def _resolve_item_index(cat: str, item, prods):
    """محصول (راست/وسط/چپ، شماره از راست، شکل یا اسم) رو به اندیس دکمه تبدیل می‌کنه؛ None اگه پیدا نشد"""
    n = len(prods)
    if not n:
        return None
    if isinstance(item, str) and item.strip().lower() in POS_WORDS:
        return _pos_index(POS_WORDS[item.strip().lower()], n)
    if isinstance(item, int):
        return n - min(max(item, 1), n)          # شماره‌ی ۱ = دکمه‌ی سمت راست
    key = str(item).replace("\ufe0f", "").strip().lower()
    for pos, names in enumerate(FACTORY_LAYOUT.get(cat, [])):   # pos از سمت راست
        if any(key == nm.replace("\ufe0f", "").lower() for nm in names):
            return n - 1 - pos if n - 1 - pos >= 0 else None
    for k, y in enumerate(prods):   # اگه متن دکمه خودش شکل رو داشت
        if key and key in (y.text or "").replace("\ufe0f", "").lower():
            return k
    return None


async def factory_produce_target(chat_id: int, mid: int, cat_name: str, item) -> bool:
    """تولید → دسته → محصول (با شکل/شماره) → درصد → اگه هزینه‌ی ساخت اوکی بود «شروع تولید»"""
    global factory_busy_until
    m = await client.get_messages(chat_id, ids=mid)
    if not m or not _factory_is_main(m):
        if not await factory_home(chat_id, mid):
            return False
        m = await client.get_messages(chat_id, ids=mid)
    b = _pick_btn(m, exact="تولید")
    if not b or not await safe_click(b):
        print("کارخونه: دکمه‌ی تولید پیدا نشد")
        return False
    cat = await wait_msg(chat_id, mid, lambda x: any("تولیدی" in _fa(y.text) for y in _flat_buttons(x)), 8)
    if not cat:
        print("کارخونه: صفحه‌ی دسته‌ها باز نشد (شاید تولید در جریانه)")
        await factory_home(chat_id, mid)
        return False
    target = ("تولیدی " + cat_name).replace(" ", "")
    cb = next((y for y in _flat_buttons(cat) if _fa(y.text).replace(" ", "") == target), None)
    if not cb:
        print(f"کارخونه: دسته‌ی «{cat_name}» پیدا نشد")
        await factory_home(chat_id, mid)
        return False
    await safe_click(cb)
    pg = await wait_msg(chat_id, mid, lambda x: len(_flat_buttons(x)) >= 2 and not any("تولیدی" in _fa(y.text) for y in _flat_buttons(x)), 8)
    if not pg:
        print("کارخونه: صفحه‌ی محصولات باز نشد")
        await factory_home(chat_id, mid)
        return False
    bk = find_back_button(pg)
    prods = [y for y in _flat_buttons(pg) if y is not bk]
    print("کارخونه: دکمه‌های محصول (از سمت راست):", ", ".join(f"{k + 1}={y.text or '?'}[{getattr(y, 'data', None)}]" for k, y in enumerate(reversed(prods))))
    idx = _resolve_item_index(cat_name, item, prods) if prods else None
    if idx is None:
        print(f"کارخونه: محصول «{item}» توی دسته‌ی «{cat_name}» پیدا نشد")
        await factory_home(chat_id, mid)
        return False
    await safe_click(prods[idx])
    pc = await wait_msg(chat_id, mid, lambda x: any("%" in (y.text or "") for y in _flat_buttons(x)), 8)
    if not pc:
        print("کارخونه: صفحه‌ی درصد باز نشد")
        await factory_home(chat_id, mid)
        return False
    pb = next((y for y in _flat_buttons(pc) if "%" in (y.text or "") and re.sub(r"\D", "", _clean(y.text)) == str(factory_percent)), None)
    if not pb:
        print(f"کارخونه: دکمه‌ی {factory_percent}% پیدا نشد")
        await factory_home(chat_id, mid)
        return False
    await safe_click(pb)
    cf = await wait_msg(chat_id, mid, lambda x: _confirm_txt(_clean(x.raw_text)) and _pick_btn(x, contains="شروع") is not None, 8)
    if not cf:
        print("کارخونه: صفحه‌ی تأیید تولید باز نشد")
        await factory_home(chat_id, mid)
        return False
    cost = parse_unit_cost(cf.raw_text)
    market = parse_market_price(cf.raw_text)
    secs = parse_cook_seconds(cf.raw_text) or 3600
    print(f"کارخونه: هزینه‌ی ساخت هر عدد {cost} | قیمت بازار {market} | زمان تولید {secs} ثانیه")
    if factory_cost_max and cost is not None and cost > factory_cost_max:
        print(f"کارخونه: هزینه‌ی ساخت ({cost}) از حد تعیین‌شده ({factory_cost_max}) بیشتره؛ تولید شروع نشد")
        await factory_home(chat_id, mid)
        return False
    need_pts = parse_needed_points(cf.raw_text)
    hot_m = re.search(r"میو\s*پوینت\s*هات[^\d\n]*(\d[\d,]*)", _clean(cf.raw_text))
    print(f"کارخونه: میو پوینت موردنیاز {need_pts} | میو پوینت هات {hot_m.group(1) if hot_m else '؟'}")
    if need_pts and hot_m and _toint(hot_m.group(1)) < need_pts:
        raise FactoryNeedPoints(cf.raw_text)
    res = await confirm_click(chat_id, cf, lambda x: _pick_btn(x, contains="شروع"))
    if res is None:
        print("کارخونه: تأیید شروع تولید انجام نشد")
        await factory_home(chat_id, mid)
        return False
    if res is not None and _factory_no_points(res.raw_text):
        raise FactoryNeedPoints(cf.raw_text + "\n" + res.raw_text)
    factory_busy_until = time.time() + secs + 30
    schedule_factory_check(secs + 60)
    print(f"کارخونه: تولید «{_item_label(item)}» از دسته‌ی «{cat_name}» شروع شد (دکمه‌ی شماره {idx + 1} از چپ) ✅")
    await factory_home(chat_id, mid)
    return True


async def factory_produce_flow(chat_id: int, mid: int) -> bool:
    """لیست اولویت: اولین «محصول از دسته» که بشه تولید میشه؛ اگه نشد (پیدا نشد / هزینه زیاد) بعدی"""
    for t in list(factory_targets):
        if await factory_produce_target(chat_id, mid, t["cat"], t["item"]):
            return True
    return False


def _factory_target_chat():
    return factory_chat_id or farm_chat_id


def parse_needed_points(text: str):
    """«میو پوینت مورد نیاز : 3,000,000» → 3000000 (نه «هزینه‌ی هر محصول : 40»)"""
    m = re.search(r"مورد\s*نیاز[^\d\n]*(\d[\d,]*)", _clean(normalize(text or "")))
    return _toint(m.group(1)) if m else None


class FactoryNeedPoints(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _factory_no_points(text: str) -> bool:
    t = _loose(text)
    return "میوپوینتکافی" in t and "ندارید" in t


async def _factory_session_once(force: bool = False):
    """یک دور بررسی کارخونه؛ اگه میو پوینت کافی نبود متن پیام ربات رو برمی‌گردونه"""
    chat = _factory_target_chat()
    if chat is None:
        print("کارخونه: گروه تنظیم نشده؛ توی گروه بنویس: factory on")
        return
    async with factory_lock:
        if not force and time.time() < factory_busy_until:
            print(f"کارخونه: تولید در جریانه؛ {int((factory_busy_until - time.time()) / 60)} دقیقه‌ی دیگه بررسی میشه")
            return
        sent = await client.send_message(chat, FACTORY_TRIGGER_TEXT)
        fmsg = await wait_reply_to(chat, sent.id, need_buttons=False)
        if not fmsg:
            print("کارخونه: پاسخ کارخونه نیومد")
            return
        if _factory_no_points(fmsg.raw_text):
            return fmsg.raw_text
        mid = fmsg.id
        st = parse_factory_storage(fmsg.raw_text)
        if not st:
            print("کارخونه: ظرفیت انبار خونده نشد")
            return
        print(f"کارخونه: ظرفیت انبار {st[0]:,} / {st[1]:,}")
        if st[0] == st[1]:   # انبار پر
            if factory_sell_min is None:
                print("کارخونه: انبار پره ولی حد فروش تنظیم نشده (factory sell above <n>)")
                return
            await factory_sell_flow(chat, mid)
            await factory_home(chat, mid)
            m = await client.get_messages(chat, ids=mid)
            st = parse_factory_storage(m.raw_text) if m else None
            if not st or st[0] == st[1]:
                print("کارخونه: انبار هنوز پره؛ بعداً دوباره بررسی میشه")
                return
        try:
            await factory_produce_flow(chat, mid)
        except FactoryNeedPoints as e:
            return e.text


async def factory_session(force: bool = False):
    chat = _factory_target_chat()
    need = await _factory_session_once(force)
    if need is not None and chat is not None:
        if await factory_topup(chat, need):
            await asyncio.sleep(2)
            need = await _factory_session_once(True)    # بعد از برداشت: دوباره تولید رو شروع کن
            if need is not None:
                print("کارخونه: بعد از برداشت هم میو پوینت کافی نبود")


def schedule_factory_check(delay: float):
    global factory_timer
    if factory_timer and factory_timer is not asyncio.current_task():
        factory_timer.cancel()
    factory_timer = asyncio.create_task(_factory_timer(delay))


async def _factory_timer(delay: float):
    try:
        await asyncio.sleep(delay)
        if factory_enabled:
            await factory_session()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"کارخونه (تایمر): {e}")


async def factory_loop():
    try:
        while True:
            if factory_enabled and _factory_target_chat() is not None:
                try:
                    await factory_session()
                except Exception as e:
                    print(f"خطا توی کارخونه: {e}")
            await asyncio.sleep(factory_interval_seconds)
    except asyncio.CancelledError:
        pass


def start_factory_loop():
    global factory_task
    if factory_task:
        factory_task.cancel()
    factory_task = asyncio.create_task(factory_loop())


def stop_factory_loop():
    global factory_task, factory_timer
    if factory_task:
        factory_task.cancel()
        factory_task = None
    if factory_timer:
        factory_timer.cancel()
        factory_timer = None


def factory_status_text() -> str:
    sell = f"قیمت بازار {factory_sell_min:,} یا بیشتر" if factory_sell_min is not None else "خاموش (factory sell above <n>)"
    cost = f"حداکثر {factory_cost_max:,}" if factory_cost_max else "بدون محدودیت"
    on = "روشن ✅" if factory_enabled else "خاموش ❌"
    return (f"🏭 کارخونه میویی: {on}\n• فروش محصول: {sell}\n• هزینه‌ی ساخت هر عدد: {cost}\n"
            f"• تولید (به ترتیب اولویت): " + "، ".join(f"{_item_label(t['item'])} از {t['cat']}" for t in factory_targets) + f" | {factory_percent}%\n"
            f"• بررسی هر {factory_interval_seconds/60:g} دقیقه")


def _resolve_category(arg: str):
    arg = arg.strip()
    name = FACTORY_CATEGORIES.get(arg)
    if not name:
        key = _fa(arg).replace("تولیدی", "").replace(" ", "")
        name = next((v for v in FACTORY_CATEGORIES.values() if v.replace(" ", "") == key), None)
    return name


def _parse_item(arg: str):
    arg = arg.strip()
    if not arg:
        return None
    if arg.lower() in POS_WORDS:
        return POS_WORDS[arg.lower()]
    return int(arg) if arg.isdigit() and 1 <= int(arg) <= 9 else arg


def _parse_factory_target(arg: str):
    """«right 6» ، «left 6» ، «middle 6» (موقعیت دکمه + شماره‌ی دسته) ، «ستاره از تکنولوژی پیشرفته» ، «⭐ 6» ، «2 تکنولوژی پیشرفته»"""
    item_txt = cat_txt = None
    for sep in (" از ", " in "):
        if sep in arg:
            item_txt, cat_txt = arg.split(sep, 1)
            break
    if item_txt is None:
        parts = arg.split(None, 1)
        if len(parts) < 2:
            return None
        item_txt, cat_txt = parts
    cat = _resolve_category(cat_txt)
    item = _parse_item(item_txt)
    if not cat and item_txt.strip().lower() not in POS_WORDS and cat_txt.strip().lower() in POS_WORDS:
        cat, item = _resolve_category(item_txt), _parse_item(cat_txt)      # «6 right»
    return {"cat": cat, "item": item} if cat and item is not None else None


def apply_factory_command(sub: str):
    """تنظیم‌های کارخونه (بدون نیاز به چت)؛ متن پاسخ فارسی یا None اگه دستور شناخته نشد"""
    global factory_sell_min, factory_cost_max, factory_targets, factory_percent, factory_interval_seconds, factory_chat_id
    sub = sub.strip()
    if sub.startswith("chat"):
        raw = sub[len("chat"):].strip()
        if not raw.lstrip("-").isdigit():
            return "مثال: factory chat -1001234567890   (آیدی عددی گروهی که میویی توشه)"
        factory_chat_id = int(raw)
        return f"گروه کارخونه روی {factory_chat_id} تنظیم شد ✅"
    if sub == "sell off":
        factory_sell_min = None
        return "فروش محصول کارخونه غیرفعال شد ❌"
    if sub.startswith("sell above"):
        n = parse_int(sub[len("sell above"):].strip())
        if n is None:
            return "مثال: factory sell above 70   (قیمت بازار)"
        factory_sell_min = n
        return f"فروش محصول فعال شد ✅ — وقتی انبار پره و قیمت بازار {n:,} یا بیشتر باشه محصول فروخته میشه"
    if sub == "cost off":
        factory_cost_max = 0
        return "محدودیت هزینه‌ی ساخت برداشته شد ✅"
    if sub.startswith("cost below"):
        n = parse_int(sub[len("cost below"):].strip())
        if n is None or n <= 0:
            return "مثال: factory cost below 50   (هزینه‌ی ساخت هر عدد)"
        factory_cost_max = n
        return f"حد هزینه‌ی ساخت تنظیم شد ✅ — فقط وقتی هزینه‌ی هر عدد {n:,} یا کمتر باشه تولید شروع میشه"
    if sub.startswith("make"):
        arg = sub[len("make"):].strip()
        if arg in ("list", ""):
            return "اولویت تولید:\n" + "\n".join(f"{i + 1}. {_item_label(t['item'])} از {t['cat']}" for i, t in enumerate(factory_targets)) + "\n\nمثال: factory make right 6   (دکمه‌ی سمت راست از دسته‌ی ۶)"
        if arg in ("clear", "reset"):
            factory_targets = [{"cat": "تکنولوژی پیشرفته", "item": 1}]
            return "لیست تولید به حالت پیش‌فرض برگشت ✅ (اولین محصول از تکنولوژی پیشرفته)"
        add = False
        if arg.startswith("add "):
            add, arg = True, arg[4:].strip()
        t = _parse_factory_target(arg)
        if not t:
            return ("مثال (بر اساس جای دکمه):\nfactory make right 6   ← دکمه‌ی سمت راست از دسته‌ی ۶\nfactory make middle 6   ← وسطی\nfactory make left 6   ← سمت چپ\n"
                    "یا با شکل: factory make ⭐ 6\nبرای چند اولویت: factory make add left 5")
        if add:
            factory_targets.append(t)
        else:
            factory_targets = [t]
        return ("به لیست اولویت اضافه شد ✅" if add else "تولید تنظیم شد ✅") + f" — {_item_label(t['item'])} از دسته‌ی «{t['cat']}»"
    if sub.startswith("category"):
        name = _resolve_category(sub[len("category"):].strip())
        if not name:
            return "دسته‌ها: 1 آبنبات | 2 کیک | 3 تکنولوژی | 4 خودرو | 5 هواپیما | 6 تکنولوژی پیشرفته\nمثال: factory category 6"
        factory_targets[0]["cat"] = name
        return f"دسته‌ی تولید روی «{name}» تنظیم شد ✅"
    if sub.startswith("item"):
        item = _parse_item(sub[len("item"):].strip())
        if item is None:
            return "مثال: factory item right   (right / middle / left)   یا   factory item 1   (شماره از سمت راست)"
        factory_targets[0]["item"] = item
        return f"«{_item_label(item)}» از دسته‌ی «{factory_targets[0]['cat']}» انتخاب میشه ✅"
    if sub.startswith("percent"):
        n = parse_int(sub[len("percent"):].strip().rstrip("%"))
        if n not in (25, 50, 75, 100):
            return "مثال: factory percent 100   (فقط 25 / 50 / 75 / 100)"
        factory_percent = n
        return f"درصد تولید روی {n}% تنظیم شد ✅"
    if sub.startswith("interval"):
        seconds = parse_duration_seconds(sub[len("interval"):].strip())
        if seconds is None or seconds < 300:
            return "مثال: factory interval 60  (دقیقه)  یا  factory interval 2h  (حداقل ۵ دقیقه)"
        factory_interval_seconds = seconds
        return f"کارخونه هر {seconds/60:g} دقیقه بررسی میشه ✅"
    return None


def factory_settings_dict() -> dict:
    return {"factory_enabled": factory_enabled, "factory_chat_id": factory_chat_id, "factory_sell_min": factory_sell_min,
            "factory_cost_max": factory_cost_max, "factory_targets": factory_targets,
            "factory_percent": factory_percent, "factory_interval_seconds": factory_interval_seconds,
            "factory_busy_until": factory_busy_until}


def apply_factory_settings(d: dict):
    global factory_enabled, factory_chat_id, factory_sell_min, factory_cost_max, factory_targets
    global factory_percent, factory_interval_seconds, factory_busy_until
    factory_enabled = d.get("factory_enabled", factory_enabled)
    factory_chat_id = d.get("factory_chat_id", factory_chat_id)
    factory_sell_min = d.get("factory_sell_min", factory_sell_min)
    factory_cost_max = d.get("factory_cost_max", factory_cost_max)
    if d.get("factory_targets"):
        factory_targets = d["factory_targets"]
    factory_percent = d.get("factory_percent", factory_percent)
    factory_interval_seconds = d.get("factory_interval_seconds", factory_interval_seconds)
    factory_busy_until = d.get("factory_busy_until", factory_busy_until)


# ---------- ماینر میویی + بانک میویی ----------
MINER_TRIGGER_TEXT = "ماینر میو"
BANK_TRIGGER_TEXT = "بانک میویی"
MINER_CURS = ("USDT", "TON", "BTC", "ETH", "SOL")
miner_cfg = {"enabled": False, "chat": None, "interval": 3600, "convert_cur": "USDT", "convert_pct": 100,
             "sell_enabled": True, "sell_cur": "USDT", "sell_pct": 100,
             "power_enabled": True, "charge_amount": 1_000_000}   # شارژ خودکار اعتبار برق ماینر
bank_cfg = {"enabled": False, "chat": None, "min": None, "margin": 1000,
            "wd_enabled": True, "wd_amount": 3_100_000, "wd_extra": 100_000,
            "tr_enabled": False, "tr_card": None, "tr_interval": 24 * 3600, "tr_margin": 1000, "tr_last": 0.0}   # انتقال دوره‌ای موجودی بانک (کارت به کارت)
bank_transfer_task = None   # برداشت خودکار برای تولید کارخونه
MINER_CREDIT_MIN = 100_000          # حداقل واریز اعتبار برق
MINER_CREDIT_MAX = 50_000_000       # سقف اعتبار برق
POWER_OUT_WORDS = ("اعتبار برق ماینر تمام شده", "اعتبار برق شما دیگر برای پرداخت قبض کافی نیست")
power_last = 0.0
topup_last = 0.0
miner_lock = asyncio.Lock()
bank_lock = asyncio.Lock()
miner_task = None
miner_sell_timer = None
bank_last = 0.0
_CANCEL_SYMS = ("❌", "✖", "✕", "×", "✗", "❎", "🚫")


def _tofloat(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def parse_fa_duration(text: str):
    """«1 ساعت 13 دقیقه 51 ثانیه» → ثانیه (یا None)"""
    t = _clean(text)
    tot, found = 0, False
    for unit, mult in (("ساعت", 3600), ("دقیقه", 60), ("ثانیه", 1)):
        m = re.search(r"(\d+)\s*" + unit, t) or re.search(unit + r"\s*(\d+)", t)
        if m:
            tot += int(m.group(1)) * mult
            found = True
    return tot if found else None


def _miner_chat():
    return miner_cfg["chat"] or farm_chat_id


def _miner_is_main(m) -> bool:
    return any(_fa(b.text) in ("تبدیل توکن", "مدیریت برق", "کیف پول") for b in _flat_buttons(m))


def _cur_btn(m, cur):
    return next((b for b in _flat_buttons(m) if (b.text or "").strip().upper() == cur), None)


def _has_cur_buttons(m) -> bool:
    return any((b.text or "").strip().upper() in MINER_CURS for b in _flat_buttons(m))


def _pct_btn(m, pct):
    for b in _flat_buttons(m):
        t = _clean(b.text or "")
        if "توکن" in t:
            continue
        if re.sub(r"\D", "", t) == str(pct) and re.sub(r"[\d%\s]", "", t) == "" or (re.sub(r"\D", "", t) == str(pct) and "%" in t):
            return b
    return None


def _first_confirm(m):
    btns = _flat_buttons(m)
    ok = [b for b in btns if any(c in (b.text or "") for c in ("✅", "✔", "☑", "✓", "🟢"))]
    if ok:
        return ok[0]
    good = [b for b in btns if not any(c in (b.text or "") for c in _CANCEL_SYMS)]
    return (good or btns or [None])[0]


async def miner_home(chat_id: int, mid: int) -> bool:
    for _ in range(6):
        m = await client.get_messages(chat_id, ids=mid)
        if m and _miner_is_main(m):
            return True
        if not await click_back(chat_id, mid):
            return False
        await asyncio.sleep(0.8)
    m = await client.get_messages(chat_id, ids=mid)
    return bool(m and _miner_is_main(m))


async def miner_convert_flow(chat_id: int, mid: int) -> bool:
    """تبدیل توکن → ارز → درصد → تأیید ✓ → BK"""
    cur, pct = miner_cfg["convert_cur"], miner_cfg["convert_pct"]
    m = await client.get_messages(chat_id, ids=mid)
    if not m or not _miner_is_main(m):
        if not await miner_home(chat_id, mid):
            return False
        m = await client.get_messages(chat_id, ids=mid)
    b = next((x for x in _flat_buttons(m) if _fa(x.text) == "تبدیل توکن"), None)
    if not b or not await safe_click(b):
        print("ماینر: دکمه‌ی تبدیل توکن پیدا نشد")
        return False
    pg = await wait_msg(chat_id, mid, lambda x: not _miner_is_main(x), 8)
    if not pg:
        print("ماینر: صفحه‌ی تبدیل باز نشد")
        return False
    t = _clean(pg.raw_text)
    if "در حال آپلود" in t or "باقی مانده" in t:
        secs = parse_fa_duration(t) or 3600
        print(f"ماینر: تبدیل قبلی در جریانه؛ {int(secs / 60)} دقیقه‌ی دیگه تموم میشه")
        schedule_miner_sell(secs + 120)
        await miner_home(chat_id, mid)
        return False
    cb = _cur_btn(pg, cur) or None
    if not cb:
        pg = await wait_msg(chat_id, mid, _has_cur_buttons, 6) or pg
        cb = _cur_btn(pg, cur)
    if not cb:
        print(f"ماینر: دکمه‌ی {cur} پیدا نشد")
        await miner_home(chat_id, mid)
        return False
    await safe_click(cb)
    pp = await wait_msg(chat_id, mid, lambda x: "موجودی" in _clean(x.raw_text) and _pct_btn(x, pct) is not None, 8)
    if not pp:
        print(f"ماینر: صفحه‌ی درصد باز نشد یا دکمه‌ی {pct}% نبود")
        await miner_home(chat_id, mid)
        return False
    tt = _clean(pp.raw_text)
    bal = _tofloat((re.search(r"موجودی[^\d\n]*(\d[\d,]*\.?\d*)", tt) or [None, None])[1])
    mn = _tofloat((re.search(r"حداقل[^\d\n]*(\d[\d,]*\.?\d*)", tt) or [None, None])[1])
    if bal is not None and mn is not None and bal * pct / 100 < mn:
        print(f"ماینر: موجودی {bal:,.0f} توکن برای تبدیل {pct}% کافی نیست (حداقل {mn:,.0f})")
        await miner_home(chat_id, mid)
        return False
    await safe_click(_pct_btn(pp, pct))
    cf = await wait_msg(chat_id, mid, lambda x: _confirm_txt(_clean(x.raw_text)) and bool(x.buttons), 8)
    if not cf:
        print("ماینر: صفحه‌ی تأیید تبدیل باز نشد")
        await miner_home(chat_id, mid)
        return False
    secs = parse_fa_duration(cf.raw_text) or 3600
    if await confirm_click(chat_id, cf) is None:
        print("ماینر: تأیید تبدیل انجام نشد")
        await miner_home(chat_id, mid)
        return False
    print(f"ماینر: تبدیل {pct}% توکن به {cur} شروع شد ✅ (حدود {int(secs / 60)} دقیقه)")
    schedule_miner_sell(secs + 120)
    await miner_home(chat_id, mid)
    return True


async def miner_sell_flow(chat_id: int, mid: int) -> bool:
    """بازار ارز → ارز → درصد فروش → تأیید ✓ → صبر برای «فروخته شد» → BK"""
    cur, pct = miner_cfg["sell_cur"], miner_cfg["sell_pct"]
    m = await client.get_messages(chat_id, ids=mid)
    if not m or not _miner_is_main(m):
        if not await miner_home(chat_id, mid):
            return False
        m = await client.get_messages(chat_id, ids=mid)
    b = next((x for x in _flat_buttons(m) if _fa(x.text) == "بازار ارز"), None)
    if not b or not await safe_click(b):
        print("ماینر: دکمه‌ی بازار ارز پیدا نشد")
        return False
    pg = await wait_msg(chat_id, mid, _has_cur_buttons, 8)
    cb = _cur_btn(pg, cur) if pg else None
    if not cb:
        print(f"ماینر: دکمه‌ی {cur} توی بازار پیدا نشد")
        await miner_home(chat_id, mid)
        return False
    await safe_click(cb)
    pp = await wait_msg(chat_id, mid, lambda x: "موجودی" in _clean(x.raw_text) and _pct_btn(x, pct) is not None, 8)
    if not pp:
        print(f"ماینر: صفحه‌ی درصد فروش باز نشد یا دکمه‌ی {pct}% نبود")
        await miner_home(chat_id, mid)
        return False
    tt = _clean(pp.raw_text)
    bal = _tofloat((re.search(r"موجودی[^\d\n]*(\d[\d,]*\.?\d*)", tt) or [None, None])[1])
    mn = _tofloat((re.search(r"حداقل[^\d\n]*(\d[\d,]*\.?\d*)", tt) or [None, None])[1])
    print(f"ماینر: موجودی کیف پول {bal} {cur} | حداقل فروش {mn} | درصد فروش {pct}")
    if bal is not None and mn is not None and bal * pct / 100 < mn:
        print("ماینر: موجودی برای فروش کافی نیست")
        await miner_home(chat_id, mid)
        return False
    await safe_click(_pct_btn(pp, pct))
    cf = await wait_msg(chat_id, mid, lambda x: _confirm_txt(_clean(x.raw_text)) and bool(x.buttons), 8)
    if not cf:
        print("ماینر: صفحه‌ی تأیید فروش باز نشد")
        await miner_home(chat_id, mid)
        return False
    if await confirm_click(chat_id, cf) is None:
        print("ماینر: تأیید فروش انجام نشد")
        await miner_home(chat_id, mid)
        return False
    done = await wait_msg(chat_id, mid, lambda x: "فروخته" in _clean(x.raw_text), 10)
    print("ماینر: ارز فروخته شد ✅" if done else "ماینر: تأیید فروش ارسال شد (پیام «فروخته شد» دیده نشد)")
    await wait_msg(chat_id, mid, lambda x: "فروخته" not in _clean(x.raw_text) and bool(x.buttons), 12)
    await miner_home(chat_id, mid)
    return True


def _miner_power_out(text: str) -> bool:
    t = _clean(normalize(text or ""))
    return any(w in t for w in POWER_OUT_WORDS)


def _miner_is_off(text: str) -> bool:
    """ماینر خاموشه؟ (جمله‌ی ثابت پایین پیام «ماینر خاموش میشود» حساب نمیشه)"""
    t = _clean(normalize(text or ""))
    t = re.sub(r"خاموش\s*می\s*شود", "", t)
    return "اعتبار برق ماینر تمام شده" in t or "خاموش" in t


async def _wait_confirm(chat: int, mid: int, words=()):
    """صفحه‌ی «آیا اطمینان دارید؟» (ادیت‌شده روی همون پیام یا پیام جدید) رو برمی‌گردونه"""
    for _ in range(12):
        await asyncio.sleep(0.8)
        m = await client.get_messages(chat, ids=mid)
        if m and _confirm_txt(_clean(m.raw_text)) and m.buttons:
            return m
        async for x in client.iter_messages(chat, limit=15):
            if x.id > mid and not x.out and x.buttons and _confirm_txt(_clean(x.raw_text)) \
                    and (not words or any(w in _clean(x.raw_text) for w in words)):
                return x
    return None


async def miner_power_flow(chat_id: int, mid: int) -> bool:
    """ماینر میو → مدیریت برق → شارژ اعتبار برق → مبلغ (ریپلای) → تأیید ✓ → BK تا صفحه‌ی اول → اگه خاموش بود دکمه‌ی سبز (روشن‌کردن)"""
    m = await client.get_messages(chat_id, ids=mid)
    if not m or not _miner_is_main(m):
        if not await miner_home(chat_id, mid):
            return False
        m = await client.get_messages(chat_id, ids=mid)
    b = _pick_btn(m, exact="مدیریت برق")
    if not b or not await safe_click(b):
        print("شارژ برق: دکمه‌ی مدیریت برق پیدا نشد")
        return False
    pg = await wait_msg(chat_id, mid, lambda x: _pick_btn(x, contains="شارژ") is not None, 8)
    if not pg:
        print("شارژ برق: صفحه‌ی مدیریت برق باز نشد")
        await miner_home(chat_id, mid)
        return False
    t = _clean(pg.raw_text)
    cm = re.search(r"اعتبار\s*برق[^\d\n]*(\d[\d,]*)", t)
    hm = re.search(r"میو\s*پوینت\s*هات[^\d\n]*(\d[\d,]*)", t)
    credit = _toint(cm.group(1)) if cm else None
    hot = _toint(hm.group(1)) if hm else None
    amount = min(max(int(miner_cfg.get("charge_amount", 1_000_000)), MINER_CREDIT_MIN), MINER_CREDIT_MAX)
    if credit is not None:
        amount = min(amount, MINER_CREDIT_MAX - credit)
    if hot is not None:
        amount = min(amount, hot)
    amount = (amount // 1000) * 1000
    if amount < MINER_CREDIT_MIN:
        print(f"شارژ برق: مبلغ قابل شارژ ({amount:,}) از حداقل {MINER_CREDIT_MIN:,} کمتره (میو پوینت هات: {hot}، اعتبار: {credit})")
        await miner_home(chat_id, mid)
        return False
    if not await safe_click(_pick_btn(pg, contains="شارژ")):
        return False
    pp = await wait_msg(chat_id, mid, lambda x: _pick_btn(x, contains="شارژ") is None and ("حداقل" in _clean(x.raw_text) or "ارسال" in _clean(x.raw_text) or "جواب" in _clean(x.raw_text)), 8)
    if not pp:
        print("شارژ برق: صفحه‌ی مبلغ باز نشد")
        await miner_home(chat_id, mid)
        return False
    await client.send_message(chat_id, str(amount), reply_to=mid)
    cf = await _wait_confirm(chat_id, mid)
    if not cf:
        print("شارژ برق: صفحه‌ی تأیید شارژ نیومد")
        await miner_home(chat_id, mid)
        return False
    if await confirm_click(chat_id, cf) is None:
        print("شارژ برق: تأیید انجام نشد")
        return False
    print(f"شارژ برق: {amount:,} میو پوینت به اعتبار برق ماینر اضافه شد ✅")
    # BK تا صفحه‌ی اول ماینر
    await miner_home(chat_id, cf.id)
    home = await client.get_messages(chat_id, ids=cf.id)
    if not home or not _miner_is_main(home):
        home = await client.get_messages(chat_id, ids=mid)
    if home and _miner_is_main(home) and _miner_is_off(home.raw_text):
        btns = _flat_buttons(home)
        if btns and await safe_click(btns[0]):     # دکمه‌ی سبز بالای صفحه (روشن‌کردن ماینر)
            print("شارژ برق: ماینر دوباره روشن شد ✅")
    return True


async def miner_power_session(force: bool = False):
    """ماینر میو بفرست → شارژ اعتبار برق (وقتی پیام پیوی «اعتبار برق کافی نیست» اومد)"""
    global power_last
    chat = _miner_chat()
    if chat is None:
        print("شارژ برق: گروه ماینر تنظیم نشده؛ توی گروه بنویس: miner on")
        return
    if not force and (not miner_cfg.get("power_enabled", True) or time.time() - power_last < 120):
        return
    async with miner_lock:
        power_last = time.time()
        sent = await client.send_message(chat, MINER_TRIGGER_TEXT)
        fmsg = await wait_reply_to(chat, sent.id)
        if not fmsg:
            print("شارژ برق: پاسخ ماینر نیومد")
            return
        await miner_power_flow(chat, fmsg.id)


async def _miner_open(chat: int):
    global power_last
    sent = await client.send_message(chat, MINER_TRIGGER_TEXT)
    fmsg = await wait_reply_to(chat, sent.id)
    if not fmsg:
        print("ماینر: پاسخ ماینر نیومد")
        return None
    if miner_cfg.get("power_enabled", True) and _miner_power_out(fmsg.raw_text):
        print("ماینر: اعتبار برق ماینر تمام شده → شارژ")
        power_last = time.time()
        try:
            await miner_power_flow(chat, fmsg.id)
        except Exception as e:
            print(f"شارژ برق: {e}")
    return fmsg.id


async def miner_session():
    chat = _miner_chat()
    if chat is None:
        print("ماینر: گروه تنظیم نشده؛ توی گروه بنویس: miner on")
        return
    async with miner_lock:
        mid = await _miner_open(chat)
        if mid:
            await miner_convert_flow(chat, mid)


async def miner_sell_session():
    chat = _miner_chat()
    if chat is None:
        print("ماینر: گروه تنظیم نشده")
        return
    if not miner_cfg["sell_enabled"]:
        print("ماینر: فروش خاموشه (miner sell on)")
        return
    async with miner_lock:
        mid = await _miner_open(chat)
        if mid:
            await miner_sell_flow(chat, mid)


def schedule_miner_sell(delay: float):
    global miner_sell_timer
    if miner_sell_timer and miner_sell_timer is not asyncio.current_task():
        miner_sell_timer.cancel()
    miner_sell_timer = asyncio.create_task(_miner_sell_timer(delay))


async def _miner_sell_timer(delay: float):
    try:
        await asyncio.sleep(delay)
        if miner_cfg["enabled"]:
            await miner_sell_session()
            await miner_session()   # بعد از فروش، تبدیل بعدی
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"ماینر (تایمر): {e}")


async def miner_loop():
    try:
        while True:
            if miner_cfg["enabled"] and _miner_chat() is not None:
                try:
                    await miner_session()
                except Exception as e:
                    print(f"خطا توی ماینر: {e}")
            await asyncio.sleep(miner_cfg["interval"])
    except asyncio.CancelledError:
        pass


def start_miner_loop():
    global miner_task
    if miner_task:
        miner_task.cancel()
    miner_task = asyncio.create_task(miner_loop())


def stop_miner_loop():
    global miner_task, miner_sell_timer
    for t in (miner_task, miner_sell_timer):
        if t:
            t.cancel()
    miner_task = miner_sell_timer = None


def miner_status_text() -> str:
    c = miner_cfg
    sell = f"{c['sell_cur']} با {c['sell_pct']}%" if c["sell_enabled"] else "خاموش"
    return (f"⛏ ماینر میویی: {'روشن ✅' if c['enabled'] else 'خاموش ❌'}\n• تبدیل توکن: {c['convert_cur']} با {c['convert_pct']}%\n"
            f"• فروش ارز: {sell}\n• بررسی هر {c['interval'] / 60:g} دقیقه\n• شارژ برق: {'روشن ✅' if c.get('power_enabled', True) else 'خاموش ❌'} ({c.get('charge_amount', 1_000_000):,} میو پوینت)\n• گروه: {_miner_chat()}")


def apply_miner_command(sub: str):
    """تنظیم‌های ماینر؛ متن پاسخ فارسی یا None"""
    toks = sub.strip().lower().split()
    if not toks:
        return None
    c = miner_cfg

    def pct_of(ts):
        for x in ts:
            if x.rstrip("%").isdigit():
                return int(x.rstrip("%"))
        return None
    up = [x.upper() for x in toks]
    if toks[0] == "sell":
        rest, rup = toks[1:], up[1:]
        if rest == ["off"]:
            c["sell_enabled"] = False
            return "فروش ارز ماینر غیرفعال شد ❌"
        if rest == ["on"]:
            c["sell_enabled"] = True
            return f"فروش ارز ماینر فعال شد ✅ ({c['sell_cur']} با {c['sell_pct']}%)"
        cur = next((x for x in rup if x in MINER_CURS), None)
        pct = pct_of(rest)
        if cur is None and pct is None:
            return "مثال: miner sell usdt 100   (ارز + درصد فروش)   یا   miner sell 50   یا   miner sell off"
        if pct is not None and pct not in (25, 50, 75, 100):
            return "درصد فقط 25 / 50 / 75 / 100 میشه"
        if cur:
            c["sell_cur"] = cur
        if pct is not None:
            c["sell_pct"] = pct
        c["sell_enabled"] = True
        return f"فروش ارز تنظیم شد ✅ — {c['sell_cur']} با {c['sell_pct']}%"
    if toks[0] == "charge":
        n = parse_int(" ".join(toks[1:]))
        if n is None or not (MINER_CREDIT_MIN <= n <= MINER_CREDIT_MAX):
            return f"مثال: miner charge 1000000   (یا 1m) — بین {MINER_CREDIT_MIN:,} تا {MINER_CREDIT_MAX:,}"
        c["charge_amount"] = n
        c["power_enabled"] = True
        return f"مبلغ شارژ اعتبار برق روی {n:,} میو پوینت تنظیم شد ✅ (شارژ خودکار فعاله)"
    if toks[0] == "power":
        if toks[1:] == ["off"]:
            c["power_enabled"] = False
            return "شارژ خودکار اعتبار برق غیرفعال شد ❌"
        if toks[1:] == ["on"]:
            c["power_enabled"] = True
            return f"شارژ خودکار اعتبار برق فعال شد ✅ ({c['charge_amount']:,} میو پوینت)"
        if toks[1:] == ["now"]:
            asyncio.create_task(miner_power_session(force=True))
            return "شارژ اعتبار برق شروع شد ⏳"
        return "مثال: miner power on | miner power off | miner power now"
    if toks[0] == "percent":
        pct = pct_of(toks[1:])
        if pct not in (25, 50, 75, 100):
            return "مثال: miner percent 100   (فقط 25 / 50 / 75 / 100)"
        c["convert_pct"] = pct
        return f"درصد تبدیل توکن روی {pct}% تنظیم شد ✅"
    if toks[0] == "chat":
        raw = toks[1] if len(toks) > 1 else ""
        if not raw.lstrip("-").isdigit():
            return "مثال: miner chat -1001234567890"
        c["chat"] = int(raw)
        return f"گروه ماینر روی {c['chat']} تنظیم شد ✅"
    if toks[0] == "interval":
        seconds = parse_duration_seconds(" ".join(toks[1:]))
        if seconds is None or seconds < 300:
            return "مثال: miner interval 60  (دقیقه)  یا  miner interval 2h   (حداقل ۵ دقیقه)"
        c["interval"] = seconds
        return f"ماینر هر {seconds / 60:g} دقیقه بررسی میشه ✅"
    if toks[0] == "convert":
        toks, up = toks[1:], up[1:]
    cur = next((x for x in up if x in MINER_CURS), None)
    if cur is None and toks[:1] != ["percent"]:
        return None
    pct = pct_of(toks)
    if pct is not None and pct not in (25, 50, 75, 100):
        return "درصد فقط 25 / 50 / 75 / 100 میشه"
    if cur:
        c["convert_cur"] = cur
    if pct is not None:
        c["convert_pct"] = pct
    return f"تبدیل توکن روی {c['convert_cur']} با {c['convert_pct']}% تنظیم شد ✅"


# ---- بانک ----
async def bank_session(balance: int):
    """میوپوینت‌ها از حد بیشتره: بانک میویی → واریز → ارسال مبلغ (ریپلای) → ✓ → BK"""
    global bank_last
    chat = bank_cfg["chat"] or farm_chat_id
    if chat is None:
        print("بانک: گروه تنظیم نشده؛ bank chat <id>")
        return
    amount = ((balance - bank_cfg["margin"]) // 1000) * 1000
    if amount <= 0:
        return
    async with bank_lock:
        if time.time() - bank_last < 90:
            return
        bank_last = time.time()
        sent = await client.send_message(chat, BANK_TRIGGER_TEXT)
        pm = await wait_reply_to(chat, sent.id)
        if not pm:
            print("بانک: پاسخ بانک نیومد")
            return
        mid = pm.id
        b = next((x for x in _flat_buttons(pm) if _fa(x.text) == "واریز"), None)
        if not b or not await safe_click(b):
            print("بانک: دکمه‌ی واریز پیدا نشد")
            return
        pg = await wait_msg(chat, mid, lambda x: "ارسال" in _clean(x.raw_text) or "جواب" in _clean(x.raw_text), 8)
        if not pg:
            print("بانک: صفحه‌ی واریز باز نشد")
            return
        amsg = await client.send_message(chat, str(amount), reply_to=mid)
        cid, cmsg = mid, None
        for _ in range(12):
            await asyncio.sleep(0.8)
            m = await client.get_messages(chat, ids=mid)
            if m and _confirm_txt(_clean(m.raw_text)) and m.buttons:
                cmsg = m
                break
            async for x in client.iter_messages(chat, limit=15):
                if x.id > mid and not x.out and x.buttons and _confirm_txt(_clean(x.raw_text)) and "واریز" in _clean(x.raw_text):
                    cmsg = x
                    break
            if cmsg:
                break
        if not cmsg:
            print("بانک: صفحه‌ی تأیید واریز نیومد")
            return
        cid = cmsg.id
        if await confirm_click(chat, cmsg) is None:
            print("بانک: تأیید واریز انجام نشد")
            return
        done = await wait_msg(chat, cid, lambda x: "با موفقیت" in _clean(x.raw_text), 10)
        print(f"بانک: {amount:,} میو پوینت واریز شد ✅" if done else "بانک: تأیید واریز ارسال شد (پیام موفقیت دیده نشد)")
        await click_back(chat, cid)


def _bank_is_home(m) -> bool:
    names = [_fa(b.text) for b in _flat_buttons(m)]
    return "واریز" in names and "برداشت" in names


async def bank_withdraw_session(amount: int) -> bool:
    """بانک میویی → برداشت → مبلغ (ریپلای روی پیام) → تأیید ✓ → BK تا صفحه‌ی اول بانک"""
    chat = bank_cfg["chat"] or farm_chat_id
    if chat is None:
        print("بانک: گروه تنظیم نشده؛ bank chat <id>")
        return False
    async with bank_lock:
        sent = await client.send_message(chat, BANK_TRIGGER_TEXT)
        pm = await wait_reply_to(chat, sent.id)
        if not pm:
            print("بانک: پاسخ بانک نیومد")
            return False
        mid = pm.id
        bm = re.search(r"موجودی\s*حساب[^\d\n]*(\d[\d,]*)", _clean(pm.raw_text))
        if bm:
            amount = min(amount, _toint(bm.group(1)))
        amount = (amount // 1000) * 1000
        if amount <= 0:
            print("بانک: موجودی حساب بانکی برای برداشت کافی نیست")
            return False
        b = next((x for x in _flat_buttons(pm) if _fa(x.text) == "برداشت"), None)
        if not b or not await safe_click(b):
            print("بانک: دکمه‌ی برداشت پیدا نشد")
            return False
        pg = await wait_msg(chat, mid, lambda x: not _bank_is_home(x), 8)
        if not pg:
            print("بانک: صفحه‌ی برداشت باز نشد")
            return False
        await client.send_message(chat, str(amount), reply_to=mid)
        cmsg = await _wait_confirm(chat, mid, ("برداشت",))
        if not cmsg:
            print("بانک: صفحه‌ی تأیید برداشت نیومد")
            return False
        if await confirm_click(chat, cmsg, timeout=10) is None:
            print("بانک: تأیید برداشت انجام نشد")
            return False
        print(f"بانک: {amount:,} میو پوینت برداشت شد ✅")
        for _ in range(5):          # BK تا صفحه‌ی اول بانک میویی
            m = await client.get_messages(chat, ids=cmsg.id)
            if m and _bank_is_home(m):
                break
            if not await click_back(chat, cmsg.id):
                break
        return True


async def factory_topup(chat: int, text: str) -> bool:
    """میو پوینت برای تولید کافی نبود → برداشت از بانک (مقدار موردنیاز + کمی بیشتر)"""
    global topup_last
    if not bank_cfg.get("wd_enabled", True):
        print("کارخونه: میو پوینت کافی نیست ولی برداشت خودکار از بانک خاموشه (bank withdraw on)")
        return False
    if time.time() - topup_last < 120:
        return False
    topup_last = time.time()
    t = _clean(normalize(text or ""))
    needed = parse_needed_points(t)
    if needed:
        amount = needed + int(bank_cfg.get("wd_extra", 100_000))
    else:
        amount = int(bank_cfg.get("wd_amount", 3_100_000))
    amount = ((amount + 999) // 1000) * 1000
    print(f"کارخونه: میو پوینت کافی نیست → برداشت {amount:,} از بانک")
    return await bank_withdraw_session(amount)


def parse_long_duration(raw: str):
    """30m ، 6h ، 2d (و بدون پسوند = دقیقه) → ثانیه"""
    raw = (raw or "").strip().lower()
    try:
        if raw.endswith("d"):
            return float(raw[:-1]) * 86400
        if raw.endswith("h"):
            return float(raw[:-1]) * 3600
        if raw.endswith("m"):
            return float(raw[:-1]) * 60
        return float(raw) * 60
    except ValueError:
        return None


async def bank_transfer_session() -> bool:
    """بانک میویی → کارت به کارت میویی → «مبلغ شماره‌کارت» (ریپلای) → تأیید تراکنش ✓ → BK"""
    chat = bank_cfg["chat"] or farm_chat_id
    card = re.sub(r"\D", "", str(bank_cfg.get("tr_card") or "").translate(_DIGITS))   # فقط رقم‌های انگلیسی، بدون هیچ نویسه‌ی نامرئی
    if chat is None or not card:
        print("انتقال بانک: گروه یا شماره کارت مقصد تنظیم نشده (bank transfer card <شماره>)")
        return False
    async with bank_lock:
        sent = await client.send_message(chat, BANK_TRIGGER_TEXT)
        pm = await wait_reply_to(chat, sent.id)
        if not pm:
            print("انتقال بانک: پاسخ بانک نیومد")
            return False
        mid = pm.id
        bm = re.search(r"موجودی\s*حساب[^\d\n]*(\d[\d,]*)", _clean(pm.raw_text))
        if not bm:
            print("انتقال بانک: موجودی حساب خونده نشد")
            return False
        balance = _toint(bm.group(1))
        # کارمزد کارت به کارت ۲٪ هست و از موجودی کم میشه؛ پس مبلغ طوری انتخاب میشه که با کارمزد جا بشه
        amount = int(balance / 1.02) - int(bank_cfg.get("tr_margin", 1000))
        amount = (amount // 1000) * 1000
        if amount <= 0:
            print(f"انتقال بانک: موجودی ({balance:,}) برای انتقال کافی نیست")
            return False
        b = next((x for x in _flat_buttons(pm) if _fa(x.text) == "کارت به کارت میویی"), None)
        if not b or not await safe_click(b):
            print("انتقال بانک: دکمه‌ی کارت به کارت پیدا نشد")
            return False
        pg = await wait_msg(chat, mid, lambda x: not _bank_is_home(x), 8)
        if not pg:
            print("انتقال بانک: صفحه‌ی کارت به کارت باز نشد")
            return False
        text = f"{amount} {card}"          # اول مبلغ، یه فاصله، بعد شماره کارت (مثلاً 8800000 222967607573)
        print(f"انتقال بانک: ارسال «{text}»")
        await client.send_message(chat, text, reply_to=mid, parse_mode=None)
        cmsg = await _wait_confirm(chat, mid, ("کارت",))
        if not cmsg:
            print("انتقال بانک: صفحه‌ی تأیید کارت به کارت نیومد")
            await click_back(chat, mid)
            return False
        # دکمه‌ی سبز «تایید تراکنش»
        if await confirm_click(chat, cmsg, timeout=10) is None:
            print("انتقال بانک: تأیید تراکنش انجام نشد")
            return False
        bank_cfg["tr_last"] = time.time()
        print(f"انتقال بانک: {amount:,} میو پوینت به کارت {card} منتقل شد ✅ (موجودی قبلی {balance:,})")
        m = await client.get_messages(chat, ids=cmsg.id)
        if m and m.buttons and not _bank_is_home(m):     # دکمه‌ی قرمز BK بعد از انتقال
            await click_back(chat, cmsg.id)
        return True


async def bank_transfer_loop():
    try:
        while True:
            if not (bank_cfg.get("tr_enabled") and bank_cfg.get("tr_card")):
                await asyncio.sleep(60)
                continue
            wait = bank_cfg.get("tr_last", 0.0) + bank_cfg["tr_interval"] - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 60))
                continue
            try:
                await bank_transfer_session()
            except Exception as e:
                print(f"خطا توی انتقال بانک: {e}")
            bank_cfg["tr_last"] = time.time()    # حتی اگه ناموفق بود، تا دوره‌ی بعد صبر کن
    except asyncio.CancelledError:
        pass


def start_bank_transfer_loop():
    global bank_transfer_task
    if bank_transfer_task:
        bank_transfer_task.cancel()
    bank_transfer_task = asyncio.create_task(bank_transfer_loop())


def bank_status_text() -> str:
    c = bank_cfg
    return (f"🏦 بانک میویی: {'روشن ✅' if c['enabled'] else 'خاموش ❌'}\n"
            f"• واریز وقتی میو پوینت هات از {c['min']:,} بیشتر باشه" if c["min"] else f"🏦 بانک میویی: {'روشن' if c['enabled'] else 'خاموش'} — حد واریز تنظیم نشده (bank above <n>)") + \
           f"\n• کم‌کردن از موجودی: {c['margin']:,}\n• گروه: {c['chat'] or farm_chat_id}" + \
           f"\n• انتقال دوره‌ای: {'روشن ✅' if c.get('tr_enabled') else 'خاموش ❌'} — کارت {c.get('tr_card') or 'تنظیم نشده'} — هر {c['tr_interval'] / 3600:g} ساعت"


def apply_bank_command(sub: str):
    toks = sub.strip().lower().split()
    c = bank_cfg
    if toks[:1] == ["above"] or toks[:1] == ["min"]:
        n = parse_int(" ".join(toks[1:]))
        if not n:
            return "مثال: bank above 3000000   (یا 3m)"
        c["min"] = n
        c["enabled"] = True
        return f"بانک فعال شد ✅ — وقتی میو پوینت هات از {n:,} بیشتر باشه، واریز به بانک انجام میشه"
    if toks[:1] == ["transfer"]:
        r = toks[1:]
        if r[:1] == ["card"]:
            card = (r[1] if len(r) > 1 else "").translate(_DIGITS)
            if not card.isdigit() or not card.isascii():
                return "مثال: bank transfer card 222967607573"
            c["tr_card"] = card
            return f"شماره کارت مقصد روی {card} تنظیم شد ✅ (حالا: bank transfer every 6h  و  bank transfer on)"
        if r[:1] == ["every"]:
            sec = parse_long_duration(r[1] if len(r) > 1 else "")
            if sec is None or sec < 600:
                return "مثال: bank transfer every 6h   |   every 1d   |   every 90m   (حداقل ۱۰ دقیقه)"
            c["tr_interval"] = sec
            return f"انتقال بانک هر {sec / 3600:g} ساعت انجام میشه ✅"
        if r[:1] == ["margin"]:
            n = parse_int(" ".join(r[1:]))
            if n is None:
                return "مثال: bank transfer margin 1000   (چقدر کمتر از حداکثر مبلغ قابل انتقال بفرسته)"
            c["tr_margin"] = n
            return f"مقدار کم‌کردن از مبلغ انتقال روی {n:,} تنظیم شد ✅"
        if r == ["on"]:
            if not c.get("tr_card"):
                return "اول شماره کارت رو بذار: bank transfer card <شماره>"
            c["tr_enabled"] = True
            start_bank_transfer_loop()
            return f"انتقال دوره‌ای بانک فعال شد ✅ — هر {c['tr_interval'] / 3600:g} ساعت به {c['tr_card']}"
        if r == ["off"]:
            c["tr_enabled"] = False
            return "انتقال دوره‌ای بانک غیرفعال شد ❌"
        if r == ["now"]:
            if not c.get("tr_card"):
                return "اول شماره کارت رو بذار: bank transfer card <شماره>"
            asyncio.create_task(bank_transfer_session())
            return "انتقال کارت به کارت شروع شد ⏳"
        return "bank transfer card <شماره> | bank transfer every <6h|1d> | bank transfer on | bank transfer off | bank transfer now | bank transfer margin <n>"
    if toks[:1] == ["withdraw"]:
        if toks[1:] == ["off"]:
            c["wd_enabled"] = False
            return "برداشت خودکار از بانک (برای تولید کارخونه) غیرفعال شد ❌"
        if toks[1:] == ["on"]:
            c["wd_enabled"] = True
            return f"برداشت خودکار از بانک فعال شد ✅ ({c['wd_amount']:,})"
        n = parse_int(" ".join(toks[1:]))
        if not n:
            return "مثال: bank withdraw 3100000   (یا 3.1m) — مبلغی که وقتی میو پوینت برای تولید کم بود برداشت میشه | bank withdraw on | bank withdraw off"
        c["wd_amount"] = n
        c["wd_enabled"] = True
        return f"مبلغ برداشت از بانک روی {n:,} تنظیم شد ✅"
    if toks[:1] == ["extra"]:
        n = parse_int(" ".join(toks[1:]))
        if n is None:
            return "مثال: bank extra 100000   (وقتی مبلغ موردنیاز تولید از پیام ربات خونده بشه، این مقدار بیشتر برداشته میشه)"
        c["wd_extra"] = n
        return f"مقدار اضافه‌ی برداشت روی {n:,} تنظیم شد ✅"
    if toks[:1] == ["margin"]:
        n = parse_int(" ".join(toks[1:]))
        if n is None:
            return "مثال: bank margin 1000   (چقدر کمتر از موجودی واریز بشه)"
        c["margin"] = n
        return f"مقدار کم‌کردن از موجودی روی {n:,} تنظیم شد ✅"
    if toks[:1] == ["chat"]:
        raw = toks[1] if len(toks) > 1 else ""
        if not raw.lstrip("-").isdigit():
            return "مثال: bank chat -1001234567890"
        c["chat"] = int(raw)
        return f"گروه بانک روی {c['chat']} تنظیم شد ✅"
    if toks == ["on"]:
        c["enabled"] = True
        return "واریز خودکار به بانک فعال شد ✅" if c["min"] else "فعال شد ✅ ولی حد واریز تنظیم نشده: bank above <n>"
    if toks == ["off"]:
        c["enabled"] = False
        return "واریز خودکار به بانک غیرفعال شد ❌"
    if toks == ["status"]:
        return bank_status_text()
    return None


@client.on(events.NewMessage(incoming=True))
async def meow_balance_watcher(event):
    """بعد از «میو» اگه «میو پوینت هات» از حد بانک بیشتر بود → واریز به بانک"""
    if not bank_cfg["enabled"] or not bank_cfg["min"]:
        return
    t = _clean(event.raw_text or "")
    m = re.search(r"میو\s*پوینت\s*هات[^\d\n]*(\d[\d,]*)", t)
    if not m or "گرفتی" not in t:
        return
    try:
        sender = await event.get_sender()
        if not getattr(sender, "bot", False):
            return
        rep = await event.get_reply_message()
        if not rep or not rep.out:
            return
    except Exception:
        return
    bal = int(m.group(1).replace(",", ""))
    if bal >= bank_cfg["min"]:
        print(f"بانک: موجودی {bal:,} از حد {bank_cfg['min']:,} بیشتره → واریز")
        asyncio.create_task(bank_session(bal))


@client.on(events.NewMessage(incoming=True))
async def meowie_miner_pm(event):
    """وقتی ربات میویی توی پیوی میگه تبدیل توکن تموم شد → فروش ارز توی بازار"""
    if not event.is_private or not miner_cfg["enabled"]:
        return
    t = _clean(normalize(event.raw_text or ""))
    if not pm_gate("miner", t):
        return
    try:
        sender = await event.get_sender()
        if not _pm_bot_ok(sender):
            return
    except Exception:
        return
    print("پیام پایان تبدیل توکن از میویی رسید → فروش ارز")
    global miner_sell_timer
    if miner_sell_timer and miner_sell_timer is not asyncio.current_task():
        miner_sell_timer.cancel()
    try:
        await asyncio.sleep(3)
        await miner_sell_session()
        await miner_session()
    except Exception as e:
        print(f"ماینر (پیام): {e}")


SETTINGS_FILE = f"{session_name}_settings.json"


def save_settings():
    try:
        data = {
            "feed_rules": {str(k): v for k, v in feed_rules.items()},
            "fish_sell_min": fish_sell_min, "fish_sell_enabled": fish_sell_enabled,
            "fish_feed_max": fish_feed_max, "fish_cook_enabled": fish_cook_enabled,
            "fish_cook_min": fish_cook_min, "feed_enabled": feed_enabled,
            "rescue_enabled": rescue_enabled, "rescue_max_tries": rescue_max_tries,
            "farm_chat_id": farm_chat_id, "feed_hunger_texts": feed_hunger_texts, "v": 2,
            "farm_on": farm_task is not None, "farm_interval": farm_interval_seconds,
            "feed_interval": feed_interval_seconds, "meow_default": meow_default_seconds, "fish_cfg": fish_cfg,
            "meow": {str(k): meow_seconds.get(k, meow_default_seconds) for k in active_meow_tasks},
        }
        data.update(factory_settings_dict())
        data["miner"] = miner_cfg
        data["bank"] = bank_cfg
        data["say"] = say_cfg
        data["ai"] = ai_cfg
        data["safe"] = safe_cfg
        data["pm"] = pm_cfg
        data["confirm_texts"] = confirm_texts
        data["jail"] = jail_cfg
        data["jobs"] = jobs
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"ذخیره‌ی تنظیمات: {e}")


def load_settings():
    global fish_sell_min, fish_sell_enabled, fish_feed_max, fish_cook_enabled, fish_cook_min
    global feed_enabled, rescue_enabled, rescue_max_tries, farm_chat_id
    global farm_interval_seconds, feed_interval_seconds, meow_default_seconds
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        apply_factory_settings(d)
        miner_cfg.update(d.get("miner", {}))
        bank_cfg.update(d.get("bank", {}))
        say_cfg.update(d.get("say", {}))
        ai_cfg.update(d.get("ai", {}))
        safe_cfg.update(d.get("safe", {}))
        pm_cfg.update(d.get("pm", {}))
        confirm_texts[:] = d.get("confirm_texts", confirm_texts)
        jail_cfg.update(d.get("jail", {}))
        jobs.update(d.get("jobs", {}))
        feed_rules.clear()
        feed_rules.update({int(k): int(v) for k, v in d.get("feed_rules", {}).items()})
        fish_sell_min = d.get("fish_sell_min", fish_sell_min)
        fish_sell_enabled = d.get("fish_sell_enabled", fish_sell_enabled)
        if d.get("v", 1) >= 2:   # نسخه‌های قبلی حد ۲۰۰۰ رو ذخیره کرده بودن؛ نادیده گرفته میشه
            fish_feed_max = d.get("fish_feed_max", fish_feed_max)
        fish_cook_enabled = d.get("fish_cook_enabled", fish_cook_enabled)
        fish_cook_min = d.get("fish_cook_min", fish_cook_min)
        feed_enabled = d.get("feed_enabled", feed_enabled)
        rescue_enabled = d.get("rescue_enabled", rescue_enabled)
        rescue_max_tries = d.get("rescue_max_tries", rescue_max_tries)
        if d.get("feed_hunger_texts"):
            feed_hunger_texts[:] = d["feed_hunger_texts"]
        if farm_chat_id is None and d.get("farm_chat_id"):
            farm_chat_id = d["farm_chat_id"]
        farm_interval_seconds = d.get("farm_interval", farm_interval_seconds)
        feed_interval_seconds = d.get("feed_interval", feed_interval_seconds)
        meow_default_seconds = d.get("meow_default", meow_default_seconds)
        resume_state.update(farm_on=bool(d.get("farm_on")), meow=d.get("meow", {}))
        fish_cfg.update(d.get("fish_cfg", {}))
        print(f"تنظیمات ذخیره‌شده خونده شد (قانون‌های غذادهی: {feed_rules})")
    except Exception as e:
        print(f"خواندن تنظیمات: {e}")


async def settings_saver_loop():
    """هر ۱۵ ثانیه تنظیمات رو ذخیره می‌کنه تا بعد از ری‌استارت از دست نره"""
    while True:
        await asyncio.sleep(15)
        save_settings()



async def feed_loop():
    """هر feed_interval_seconds یک بار شکم پیشی رو چک می‌کنه و اگه گرسنه بود غذا میده"""
    try:
        while True:
            if farm_chat_id is not None and feed_enabled:
                try:
                    await feed_probe(farm_chat_id, "چک زمان‌دار")
                except Exception as e:
                    print(f"خطا توی چک غذا: {e}")
            await asyncio.sleep(feed_interval_seconds or 3600)
    except asyncio.CancelledError:
        pass


def start_feed_loop():
    global feed_task
    if feed_task:
        feed_task.cancel()
    feed_task = asyncio.create_task(feed_loop())


def stop_feed_loop():
    global feed_task
    if feed_task:
        feed_task.cancel()
        feed_task = None


async def meowie_farm_once(chat_id: int | None = None):
    target = chat_id if chat_id is not None else farm_chat_id
    if target is None:
        return
    sent = await client.send_message(target, MEOWIE_TRIGGER_TEXT)
    await click_farm_button(target, sent.id)


async def farm_loop():
    try:
        while True:
            try:
                await meowie_farm_once()
            except Exception as e:
                print(f"خطا توی فارم میویی: {e}")
            await asyncio.sleep(farm_interval_seconds)
    except asyncio.CancelledError:
        pass


async def toggle_farm() -> str:
    global farm_task
    if farm_task:
        return "فارم میویی فعال می‌باشد 🐱 (از قبل روشن بود)"
    farm_task = asyncio.create_task(farm_loop())
    return f"فارم میویی فعال شد ✅ (هر {farm_interval_seconds/60:g} دقیقه، توی گروه {farm_chat_id})"


async def untoggle_farm() -> str:
    global farm_task
    if farm_task:
        farm_task.cancel()
        farm_task = None
        return "فارم میویی غیرفعال شد ❌"
    return "فارم میویی غیرفعال می‌باشد ❌ (از قبل خاموش بود)"


# ---------- ابزار حذف همه‌ی پیام‌های خودِ اکانت در یک چت ----------
async def resolve_entity(raw: str):
    """آیدی عددی، یوزرنیم یا لینک کانال/چت رو به entity تبدیل می‌کنه"""
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        n = int(raw)
        try:
            return await client.get_entity(n)
        except Exception:
            if str(n).startswith("-100"):
                return await client.get_entity(PeerChannel(int(str(n)[4:])))
            return await client.get_entity(PeerChannel(abs(n)))
    return await client.get_entity(raw)


async def resolve_chat_id(raw: str) -> int:
    """آیدی عددی، یوزرنیم یا لینک رو به chat_id تبدیل می‌کنه"""
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    entity = await client.get_entity(raw)
    return entity.id


async def delete_all_my_messages(chat_id: int) -> int:
    ids = []
    async for m in client.iter_messages(chat_id, from_user="me"):
        ids.append(m.id)
    if ids:
        await client.delete_messages(chat_id, ids)
    return len(ids)


HELP_TEXT = """📖 راهنمای کامل سلف‌بات
(دستورها انگلیسی‌اند، توضیح‌ها فارسی. دستورها رو از خودِ اکانت سلف بفرست.)

🐱 میو
meow on — فعال‌سازی میو توی همین چت (پیش‌فرض هر ۱۰ دقیقه)
meow on 5 — میو هر ۵ دقیقه
meow off — خاموش کردن میو توی همین چت
meow +5 — پنج دقیقه به زمان پیش‌فرض میو اضافه می‌کنه
meow -5 — پنج دقیقه از زمان پیش‌فرض کم می‌کنه

🕐 ساعت خودکار
clock bio on / clock bio off — ساعت و تاریخ شمسی روی بیو
clock bio reverse on / off — فقط اگه ساعت بیو بعد از آپدیت هنوز برعکس بود: ساعت رو از قبل برعکس می‌نویسه تا درست دیده بشه
clock bio style — نمایش سبک‌های بیو؛ clock bio style 3 — انتخاب سبک (۱ تا ۵)
clock name on / clock name off — ساعت روی نام خانوادگی
clock font list — نمایش همه‌ی فونت‌های ساعت
clock bio font <name> — فونت ساعت بیو، مثلاً: clock bio font keycap
clock name font <name> — فونت ساعت نام خانوادگی، مثلاً: clock name font circled
(فونت بیو و نام جدا از هم تنظیم میشن)

🌙 افک (پاسخ خودکار)
afk on — روشن کردن با پیام پیش‌فرض
afk on <message> — روشن کردن با پیام دلخواه، مثلاً: afk on خوابم
afk off — خاموش کردن
(فقط توی پیوی و فقط یک بار به هر نفر جواب میده؛ به ربات‌ها جواب نمیده)

🐾 فارم خودکار میویی
farm on — روشن کردن؛ اگه گروهی تنظیم نشده باشه آیدی عددی یا لینک گروه رو می‌پرسه
farm off — خاموش کردن
farm now — همین الان یک بار فارم کن
farm +5 / farm -5 — پنج دقیقه به فاصله‌ی فارم اضافه/کم می‌کنه
farm interval 30 — فاصله‌ی فارم برحسب دقیقه
farm interval 2h — فاصله‌ی فارم برحسب ساعت
(هر دور «پیشی» می‌فرسته، حداکثر ۱۵ ثانیه منتظر جواب میویی می‌مونه
و روی دکمه‌ی «برداشت میو پوینت ها» می‌زنه)
توجه: فارم باید خودِ اکانت سلف بفرسته؛ پیام اکانت مدیر توی گروه، خودش فارم نمی‌کنه.

🐈 نجات، غذا دادن، پخت و فروش ماهی
rescue on / rescue off — کلیک خودکار روی «نجات پیشی خیابونی» توی همه‌ی گروه‌هایی که عضوی، با فاصله‌ی ۳ ثانیه بین کلیک‌ها (پیش‌فرض روشن)
rescue tries 10 — حداکثر تعداد کلیک روی هر پیام نجات
feed on / feed off — غذا دادن خودکار (پیش‌فرض روشن)
feed interval 30 — هر ۳۰ دقیقه گرسنگی رو چک کن (feed interval 2h = هر ۲ ساعت)
feed interval off — برگشت به حالت «چک بعد از هر فارم»
feed now — همین الان شکم رو چک کن و غذا بده (حتی اگه گرسنه نباشه)
feed debug — نمایش وضعیت غذادهی (برای عیب‌یابی)
fish catch — وضعیت ماهی‌گیری خودکار (هر ۵۰ دقیقه؛ اگه کرم داشته باشی «استفاده از کرم» رو هم می‌زنه)
fish catch on / off — روشن/خاموش | fish catch now — همین الان | fish catch interval 50 — فاصله (دقیقه)

سلف وقتی غذا میده که جلوی «شکم» یکی از متن‌های گرسنگیِ تنظیم‌شده باشه (پیش‌فرض: گشنمیو و دیگه کار نمیکنمیو)؛ عددها فقط برای تعیین جای خالی شکم و انتخاب ماهیِ مناسب خونده میشن. بعد از هر کار با دکمه‌ی BK به یخچال برمیگرده و دوباره بررسی میکنه:
  ۱) ماهی پخته با ارزش ≥ حد فروش → «فروش ماهی» (هر چی بالاتر از حد باشه فروخته میشه)
  ۲) اگه باید غذا بده → ماهی پخته‌ای که ارزش غذاییش تو جای خالی شکم جا بشه (مثلاً ۴ جا خالی → ماهی با ارزش غذایی ۴) → «بده پیشی بخوره» → «پیشی خوردش» → BK
  ۳) همه‌ی ماهی‌های خام بدون استثنا → «بپوخش» → دکمه‌ی سبز ✓ → BK؛ وقتی ربات میویی توی پیوی پیام «ماهی شما پخید» رو فرستاد، سلف توی گروه فارم یخچال رو چک میکنه (غذا دادن یا فروش)
  ۴) اگه یخچال پر نیست (مثلاً 2 / 5) → «ماهی» می‌فرسته و «بندازش تو یخچال» رو میزنه

feed text add من گشنمیووو — متن گرسنگی اضافه میکنه: اگه ربات میویی بعد از «پیشی» جلوی «شکم» این متن (یا متن‌های شبیهش، مثلاً با «و» و «.» اضافه) رو نوشت، سلف به پیشی غذا میده
feed text del <text> — حذف یک متن / feed text list — نمایش متن‌ها / feed text reset — برگشت به پیش‌فرض (گشنمیو ، دیگه کار نمیکنمیو)
(اختیاری) feed 12/7 — قانون عددی هم میشه گذاشت؛ ولی ملاک اصلی همون متن‌هاست
feed rules — نمایش قانون‌ها / feed reset — پاک کردن قانون‌ها

fish sell above 50000 — ماهی پخته‌ی با ارزش ≥ این عدد فروخته میشه (50k هم قبوله)
fish sell off / fish sell on — خاموش/روشن کردن فروش
fish feed below 2000 — ماهی پخته‌ی ارزش < این عدد به پیشی داده میشه (و ماهی خام زیر این عدد پخته میشه)
fish cook on / fish cook off — پخت همه‌ی ماهی‌های خام (پیش‌فرض روشن)
fish cook above 5000 — فقط ماهی خام با ارزش ≥ این عدد پخته بشه (fish cook above 0 = همه)
fish status — نمایش قوانین فعلی
(ماهی‌هایی که ارزششون بین این دو عدده، نه فروخته میشن نه خورانده میشن)

🔒 ذخیره‌ی عکس/ویدیوی نابودشونده
ttl on / ttl off — هر عکس/ویدیو/ویس سکرت (تایمردار یا یک‌بار‌مصرف) که کسی توی پیوی برات بفرسته، خودکار توی Saved Messages ذخیره میشه (پیش‌فرض روشن)

⛏ ماینر میویی
miner on — توی گروهی که میویی توشه بنویس؛ هر ساعت «ماینر میو» می‌فرسته و: تبدیل توکن → ارز انتخابی (دکمه) → درصد تبدیل → تأیید ✓ → BK
miner usdt (یا ton / btc / eth / sol) — ارزی که توکن‌ها بهش تبدیل بشن (میتونی با درصد: miner convert usdt 50)
miner percent 100 — چند درصد از توکن‌ها تبدیل بشه (25 / 50 / 75 / 100)
وقتی تبدیل تموم بشه (پیام ربات توی پیوی، یا بعد از زمان تقریبیِ خودِ ربات) سلف «ماینر میو» می‌فرسته → بازار ارز → ارز → درصد فروش → تأیید ✓ → صبر برای «فروخته شد» → BK
miner sell usdt 100 — ارز و درصد فروش (پیش‌فرض: USDT و 100٪) / miner sell off — خاموش کردن فروش / miner sell on / miner sell now — همین الان بفروش
miner charge 1000000 — مبلغ شارژ اعتبار برق (بین 100,000 تا 50,000,000؛ پیش‌فرض 1,000,000)
وقتی ماینر خاموش بشه و ربات توی پیوی بگه «اعتبار برق کافی نیست» (یا توی پیام ماینر نوشته بود «اعتبار برق ماینر تمام شده است») سلف «ماینر میو» می‌فرسته → مدیریت برق → شارژ اعتبار برق → مبلغ با ریپلای → تأیید ✓ → BK → دکمه‌ی سبز (روشن‌کردن ماینر)
miner power on / miner power off / miner power now — شارژ خودکار برق
miner now — همین الان تبدیل توکن / miner status — نمایش تنظیم‌ها / miner interval 60 — فاصله‌ی بررسی / miner chat -100... — گروه با آیدی عددی / miner off

🏦 بانک میویی
bank above 3000000 — بعد از «میو»، اگه «میو پوینت هات» از این عدد بیشتر بود سلف «بانک میویی» می‌فرسته → واریز → مبلغ (موجودی منهای ۱۰۰۰، رُند شده به هزار؛ مثلاً 3,878,685 → 3,877,000) با ریپلای → تأیید ✓ → BK
bank withdraw 3100000 — اگه موقع تولید کارخونه پیام «میو پوینت کافی برای تولید محصول ندارید» اومد، سلف «بانک میویی» می‌فرسته → برداشت → این مبلغ (یا مبلغ موردنیازِ ربات + bank extra) با ریپلای → تأیید ✓ → BK → دوباره تولید رو شروع می‌کنه / bank withdraw on / bank withdraw off / bank extra 100000
bank transfer card 222967607573 — شماره کارت مقصد / bank transfer every 6h (یا 1d) — هر چند وقت یه بار / bank transfer on / bank transfer off / bank transfer now — همین الان
سلف «بانک میویی» می‌فرسته → کارت به کارت میویی → «مبلغ شماره‌کارت» (مثلاً 8800000 222967607573) با ریپلای → تأیید تراکنش ✓ → BK. مبلغ = موجودی بانک منهای کارمزد ۲٪ (و bank transfer margin)
bank margin 1000 — چقدر کمتر از موجودی واریز بشه / bank chat -100... — گروه بانک (پیش‌فرض: گروه فارم) / bank on / bank off / bank status

🏭 کارخونه میویی
factory on — توی گروهی که میویی توشه بنویس تا فعال بشه؛ هر ساعت (قابل تغییر) «کارخونه میویی» می‌فرسته و:
  • اگه ظرفیت انبار پره (دو عدد برابر، مثل 75,000 / 75,000) → دکمه‌ی انبار → هر محصول → اگه قیمت بازار ≥ حد فروش بود «فروش محصول» (و تیک سبز، اگه خواست) → BK تا صفحه‌ی اول؛ اگه نبود فقط BK
  • اگه انبار پر نیست (مثلاً 0 / 75,000) → تولید → دسته (پیش‌فرض: تکنولوژی پیشرفته) → محصول → درصد (پیش‌فرض 100%) → اگه هزینه‌ی ساخت هر عدد از حد تو بیشتر نبود «شروع تولید»؛ بعد از تموم شدن زمان تولید دوباره بررسی میکنه
factory chat -1001234567890 — گروه کارخونه رو با آیدی عددی تعیین کن (پیش‌فرض: گروهی که factory on توش نوشته شد یا گروه فارم)
وقتی ربات میویی توی پیوی بنویسه «تولید ... به پایان رسید و به انبار منتقل شد»، سلف توی همون گروه «کارخونه میویی» می‌فرسته، انبار رو چک می‌کنه و اگه قیمت بازار به حد فروش رسیده بود می‌فروشه (و بعد دوباره تولید رو شروع میکنه)
factory off — خاموش / factory now — همین الان یه بار بررسی کن / factory status — نمایش تنظیم‌ها
factory sell above 70 — وقتی قیمت بازار ۷۰ یا بیشتر بود محصول فروخته بشه (تا این رو نذاری فروش خاموشه) / factory sell off
factory cost below 50 — فقط وقتی هزینه‌ی ساخت هر عدد ۵۰ یا کمتره تولید کن / factory cost off
factory make right 6 — محصول رو بر اساس جای دکمه انتخاب کن: right = دکمه‌ی سمت راست ، middle = وسطی ، left = سمت چپ ؛ عدد آخر شماره‌ی دسته‌ست (6 = تکنولوژی پیشرفته؛ ⭐ سمت راست، AI وسط، HPC سمت چپ). مثال‌ها: factory make left 6 (HPC) ، factory make middle 6 (AI) ، factory make right 5
با شکل هم میشه: factory make ⭐ 6 ، factory make 2 تکنولوژی پیشرفته (شماره از سمت راست)
factory make add left 5 — اضافه کردن به لیست اولویت؛ اگه اولی نشد (پیدا نشد یا هزینه زیاد بود) بعدی امتحان میشه
factory make list — نمایش اولویت‌ها / factory make clear — برگشت به پیش‌فرض (تکنولوژی پیشرفته، اولین محصول)
دسته‌ها: 1 آبنبات | 2 کیک | 3 تکنولوژی | 4 خودرو | 5 هواپیما | 6 تکنولوژی پیشرفته
شکل‌ها برای هر دسته (از راست): آبنبات 🍫🍭🎊 | کیک 🎂🍰🧁 | تکنولوژی 🤖💻📱 | خودرو 🏎🚕🚗 | هواپیما 🚀🛩✈️ | تکنولوژی پیشرفته ⭐ AI HPC
factory percent 100 — درصد پر کردن انبار: 25 / 50 / 75 / 100
factory interval 60 — هر چند دقیقه بررسی بشه (factory interval 2h = هر ۲ ساعت)

📥 ذخیره‌ی پیام‌های پیوی توی کانال
log channel <id|link> — کانال مقصد رو تنظیم و روشن می‌کنه (اکانت باید عضو/ادمین کانال باشه)
log on / log off — روشن/خاموش
(هر چیزی که کسی توی پیوی برات بفرسته — متن، ویس، عکس، ویدیو، فایل، استیکر، و حتی مدیای نابودشونده — اول خودش توی اون کانال فرستاده میشه و زیرش «پیام از:» با اسم، یوزرنیم، آیدی عددی و ساعت ارسال. هر پیوی‌ای ذخیره میشه، حتی از اکانت‌های پرمیوم، تیک‌دار و ربات‌ها؛ فقط پیام‌هایی که خودِ تلگرام می‌فرسته (مثل کدهای ورود) نادیده گرفته میشن)

🗑 حذف پیام‌ها
clean all — روی یکی از پیام‌های خودت ریپلای کن و بنویس؛ همه‌ی پیام‌های خودت توی اون چت پاک میشه
delete messages — ربات می‌پرسه: group یا private
  • group → آیدی عددی یا لینک گروه رو بفرست، پیام‌های خودت اونجا پاک میشه
  • private → آیدی عددی یا یوزرنیم طرف رو بفرست، پیام‌های خودت توی اون پیوی پاک میشه

❓ راهنما
help — فقط فهرست دستورها (بدون توضیح)
guide — همین راهنمای کامل با توضیح هر دستور

━━━━━━━━━━━━━━━━━━━━
📡 دستورهای مدیر (ADMIN)
(آیدی عددی مدیر باید توی ADMIN_IDS باشه. دستورها رو مدیر توی پیوی همین اکانت می‌فرسته)

همه‌ی دستورهای بالا (meow, afk, clock, farm, delete messages, help) توی پیوی مدیر هم کار می‌کنن.

ping — جواب میده و زمان پاسخ رو برحسب میلی‌ثانیه نشون میده
farm now — توی پیوی: فارم توی گروه تنظیم‌شده. توی یک گروه: خودِ اکانت سلف همون‌جا «پیشی» می‌فرسته و دکمه رو می‌زنه
mio online is gp — ربات آیدی عددی یا لینک گروه رو می‌پرسه (اختیاری: فاصله و عدد دقیقه، مثلاً -1001234567890 5)؛ میو توی اون گروه فعال میشه
rmg <text> — توی هر چتی که اکانت سلف هم هست بفرست؛ اکانت سلف همون متن رو توی همون چت می‌فرسته
  اگه متن یه دستور تنظیمی باشه (rescue on/off ، feed on/off ، fish sell above 28000 ، fish cook ... ، feed 15/5) متن ارسال نمیشه و فقط همون دستور اجرا میشه. مثال: rmg rescue on
  مدیر دستورهای دیگه (meow on 5 ، meow off ، clock ... ، afk ... ، farm off/+5/-5/interval ، feed interval ...) رو هم با rmg یا بدون اون می‌تونه بنویسه؛ سلف خودش اجراشون می‌کنه و پیام تأیید رو بعد ۳۰ ثانیه پاک می‌کنه.
  این دستورها رو مدیر بدون rmg هم می‌تونه بنویسه (مثلاً فقط rescue off توی گروه)؛ همه‌ی سلف‌ها اجراش می‌کنن، پیام تأیید فارسی می‌فرستن و بعد ۳۰ ثانیه پاکش می‌کنن
  اگه مدیر روی پیام یه نفر ریپلای بزنه و rmg سلام بفرسته، سلف هم روی همون پیامِ اون شخص ریپلای می‌زنه و «سلام» می‌فرسته
  مثال: rmg hi ← سلف «hi» می‌فرسته
  مثال: rmg پیشی ← سلف «پیشی» می‌فرسته و دکمه‌ی برداشت رو هم می‌زنه
پاک یا delete — روی پیامی که خودِ اکانت سلف فرستاده ریپلای کن تا همون یک پیام پاک بشه

━━━━━━━━━━━━━━
ساخته شده توسط :
@MRZverse
"""

COMMANDS_TEXT = """📖 Commands

meow on | meow on <min> | meow off | meow +5 | meow -5
clock bio on | clock bio off | clock name on | clock name off
clock bio style [1-5] | clock bio font <name> | clock name font <name>
clock font list | clock bio reverse on|off
afk on | afk on <message> | afk off
farm on | farm off | farm now | farm +5 | farm -5
farm interval <30 | 2h>
feed on | feed off | feed interval <30 | 2h> | feed interval off | feed now | feed debug
feed text add <text> | feed text del <text> | feed text list | feed text reset
fish sell above <n> | fish sell on | fish sell off
fish cook on | fish cook off | fish cook above <n>
miner on | miner off | miner now | miner status | miner chat <id>
miner usdt|ton|btc|eth|sol | miner percent <25|50|75|100>
miner sell <cur> <percent> | miner sell off | miner sell now | miner interval <60 | 2h>
miner charge <n> | miner power on | miner power off | miner power now
bank withdraw <n|on|off> | bank extra <n>
bank above <n> | bank on | bank off | bank margin <n> | bank chat <id> | bank status
factory on | factory off | factory now | factory status | factory chat <id>
factory sell above <n> | factory sell off
factory cost below <n> | factory cost off
factory make <right|middle|left|shape|n> <category> | factory make add ... | factory make list\nfactory category <1-6 | name> | factory item <n | shape>
factory percent <25|50|75|100> | factory interval <30 | 2h>
fish feed below <n> | fish feed off | fish status
fish catch | fish catch on|off|now | fish catch interval <min>
fish catch hungry on|off | fish catch fridge <n> | fish catch recover on|off | fish catch recover sell on|off
say on 5m <word> | say on 10m a|b|c | say off | say list — هر چند وقت یه کلمه (s ثانیه، m دقیقه، h ساعت)
job add <name> | every: 3h | send: <cmd> | path: فروشگاه | target: گیفت | window: 2m — ارسال دستور و کلیک روی دکمه
job list | job now <name> | job on <name> | job off <name> | job del <name>
ai <question> | ai <provider> <question> | ai key <provider> <key> | ai model <provider> <model> | ai default <provider> | ai list | ai add <name> <url> <model> [key] | ai system <text> | ai memory on|off | ai clear
pm list | pm on|off | pm cook|miner|power|factory on|off | pm cook match <a, b> | pm add <name> | match: a, b | action: fridge ; notify | pm test <text> | pm run|del <name> — اعلان‌های پیوی ربات
tr [lang] <text> (یا ریپلای) — مترجم | calc <expr> / «حساب <عبارت>» — ماشین‌حساب
راهنمای فارسی هر بخش: «راهنما» یا «راهنما ماینر» ... (دستورها فارسی هم کار می‌کنن: ماینر روشن، فارم خاموش، ...)
rescue on | rescue off | rescue tries <n>
log channel <id|link> | log on | log off
ttl on | ttl off
confirm text add <text> | confirm text del <text|n> | confirm text list | confirm text reset | confirm check (ریپلای)
jail on | jail off | jail now | jail status
clean all | delete messages
help | guide

📡 Admin (DM)
ping | farm now | mio online is gp
rmg <text>
(reply) پاک / delete

توضیح کامل هر دستور: guide

ساخته شده توسط :
@MRZverse"""


# ======================= کلمه‌ی تکراری (say) =======================
import random as _random

say_cfg: dict = {}       # {"chat_id": {"words": [...], "interval": ثانیه}}
say_tasks: dict = {}


def parse_dur(raw: str):
    """5s ، 30s ، 5m ، 3h ، 1d (بدون پسوند = دقیقه) → ثانیه"""
    raw = (raw or "").strip().lower().translate(_DIGITS)
    try:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(raw[-1:])
        return float(raw[:-1]) * mult if mult else float(raw) * 60
    except (ValueError, IndexError):
        return None


async def say_loop(chat_id: int):
    try:
        while True:
            cfg = say_cfg.get(str(chat_id))
            if not cfg:
                return
            await asyncio.sleep(cfg["interval"])
            cfg = say_cfg.get(str(chat_id))
            if not cfg:
                return
            try:
                await client.send_message(chat_id, _random.choice(cfg["words"]))
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                print(f"کلمه‌ی تکراری: {e}")
    except asyncio.CancelledError:
        pass


def start_say(chat_id: int):
    old = say_tasks.pop(chat_id, None)
    if old:
        old.cancel()
    say_tasks[chat_id] = asyncio.create_task(say_loop(chat_id))


def _fmt_dur(sec: float) -> str:
    sec = float(sec)
    if sec >= 3600 and sec % 3600 == 0: return f"{sec / 3600:g} ساعت"
    if sec >= 60 and sec % 60 == 0: return f"{sec / 60:g} دقیقه"
    return f"{sec:g} ثانیه"


def say_command(text: str, chat_id: int) -> str:
    parts = text.split(None, 3)
    sub = parts[1].lower() if len(parts) > 1 else ""
    key = str(chat_id)
    if sub == "off":
        if key not in say_cfg:
            return "کلمه‌ی تکراری غیرفعال می‌باشد ❌ (از قبل خاموش بود)"
        say_cfg.pop(key, None)
        t = say_tasks.pop(chat_id, None)
        if t:
            t.cancel()
        return "کلمه‌ی تکراری توی این چت غیرفعال شد ❌"
    if sub in ("list", "status"):
        if not say_cfg:
            return "هیچ کلمه‌ی تکراری فعال نیست.\nمثال: کلمه روشن 5m هاپ"
        return "🗣 کلمه‌های تکراری:\n" + "\n".join(
            f"• چت {c}: هر {_fmt_dur(v['interval'])} ← {' | '.join(v['words'])}" for c, v in say_cfg.items())
    if sub == "on":
        sec = parse_dur(parts[2]) if len(parts) > 2 else None
        words = [w.strip() for w in parts[3].split("|") if w.strip()] if len(parts) > 3 else []
        if sec is None or sec < 5 or not words:
            return ("مثال: کلمه روشن 5m هاپ   |   say on 5m هاپ\n"
                    "• زمان: 30s ثانیه | 5m دقیقه | 3h ساعت | 1d روز (حداقل 5 ثانیه)\n"
                    "• چند کلمه با | → هر بار یکی تصادفی: کلمه روشن 10m میو|مع|هاپ|پیو پیو")
        cur = say_cfg.get(key)
        if cur and cur["interval"] == sec and cur["words"] == words and chat_id in say_tasks:
            return f"کلمه‌ی تکراری با همین تنظیم فعال می‌باشد ✅ (از قبل روشن بود)"
        say_cfg[key] = {"words": words, "interval": sec}
        start_say(chat_id)
        return f"کلمه‌ی تکراری فعال شد ✅ — هر {_fmt_dur(sec)} ← {' | '.join(words)}"
    return "کلمه روشن 5m هاپ | کلمه خاموش | کلمه لیست"


# ======================= وظیفه‌ی زمان‌بندی‌شده (job) =======================
jobs: dict = {}          # {name: {chat, every, send, path[], target[], window, poll, enabled, last}}
job_running: set = set()
job_scheduler_task = None
_JOB_KEYS = {"every": "every", "هر": "every", "send": "send", "ارسال": "send", "path": "path", "مسیر": "path",
             "target": "target", "هدف": "target", "window": "window", "مهلت": "window", "poll": "poll",
             "back": "back", "بازگشت": "back",
             "سرعت": "poll", "chat": "chat", "گروه": "chat"}


def _msg_sig(m):
    return (m.raw_text, tuple((b.text or "") for b in _flat_buttons(m)))


async def _wait_sig_change(chat_id: int, mid: int, sig, timeout: float = 3.0):
    """بعد از کلیک، منتظر ادیت پیام می‌مونه (هر 0.2 ثانیه چک می‌کنه تا سریع باشه)"""
    end = time.time() + timeout
    while time.time() < end:
        await asyncio.sleep(0.2)
        m = await client.get_messages(chat_id, ids=mid)
        if m and _btn_texts(m) != sig[1]:      # فقط تغییر دکمه‌ها (تایمر داخل متن ممکنه خودش عوض بشه)
            return m
    return None


def _btn_has(b, words) -> bool:
    t = _clean(normalize(b.text or "")).lower()
    f = _fa(b.text or "")
    return any(w and (w in t or w in f) for w in words)


def _norm_words(lst):
    return [_clean(normalize(w)).lower().strip() for w in lst if w and w.strip()]


def parse_timer_secs(text: str):
    """تایمر داخل پیام: 02:30 | 1:02:30 | «3 دقیقه و 20 ثانیه» | «45 ثانیه» → ثانیه (یا None)"""
    t = _clean(normalize(text or "")).translate(_DIGITS)
    lines = [ln for ln in t.splitlines() if re.search(r"تایمر|زمان|مونده|مانده|باقی|بعد|ریست|ثانیه|دقیقه|ساعت", ln)] or t.splitlines()
    for ln in lines:
        mh = re.search(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})\s*[:：]\s*(\d{2})(?!\d)", ln)
        if mh:
            return int(mh.group(1)) * 3600 + int(mh.group(2)) * 60 + int(mh.group(3))
        h = re.search(r"(\d+)\s*ساعت", ln)
        mi = re.search(r"(\d+)\s*دقیقه", ln)
        se = re.search(r"(\d+)\s*ثانیه", ln)
        if h or mi or se:
            return (int(h.group(1)) * 3600 if h else 0) + (int(mi.group(1)) * 60 if mi else 0) + (int(se.group(1)) if se else 0)
        mm = re.search(r"(?<!\d)(\d{1,3})\s*[:：]\s*(\d{2})(?!\d)", ln)
        if mm:
            return int(mm.group(1)) * 60 + int(mm.group(2))
    return None


def _btn_texts(m):
    return tuple((b.text or "") for b in _flat_buttons(m))


async def _click_fresh(chat_id: int, mid: int, m, btn) -> bool:
    """درست قبل از کلیک، پیام یک بار دیگه خونده میشه؛ اگه دکمه‌ها عوض شده بودن (پیام ادیت شده)
    کلیک انجام نمیشه تا دکمه‌ی صفحه‌ی قبلی روی صفحه‌ی جدید نخوره"""
    btns = _flat_buttons(m)
    idx = next((i for i, x in enumerate(btns) if x is btn), None)
    if idx is None:
        return False
    cur = await client.get_messages(chat_id, ids=mid)
    if not cur or _btn_texts(cur) != _btn_texts(m):
        return False
    return await safe_click(_flat_buttons(cur)[idx])


def _btn_name(b) -> str:
    return _clean(normalize(b.text or "")).lower().strip()


def _pick_named(btns, word: str):
    """دکمه‌ی مسیر: اول اسم دقیق؛ وگرنه فقط اگه دقیقاً یک دکمه شامل کلمه باشه. مبهم → None (کلیک نمی‌کنه)"""
    fa_w = _fa(word)
    exact = [b for b in btns if _btn_name(b) == word or (fa_w and _fa(b.text or "") == fa_w)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    part = [b for b in btns if _btn_has(b, [word])]
    return part[0] if len(part) == 1 else None


def _strict_back(m, custom: str = ""):
    """فقط دکمه‌ای که مطمئنیم «بازگشت»ـه:
    1) اگه کاربر متن بازگشت رو داده، فقط یک دکمه‌ی شامل اون متن
    2) دکمه‌ای با BK/🔙/بازگشت... (فقط اگه یکی باشه)
    3) تک‌دکمه‌ی ردیف آخر که فقط ایموجی/نماد داره (بدون حرف و عدد)
    غیر از این‌ها None → هیچ کلیکی انجام نمیشه"""
    btns = _flat_buttons(m)
    if custom:
        hit = [x for x in btns if custom in _btn_name(x)]
        return hit[0] if len(hit) == 1 else None
    hits = [x for x in btns if any(k.lower() in (x.text or "").lower() for k in BACK_KEYWORDS)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None
    if m.buttons:
        last = m.buttons[-1]
        if len(last) == 1 and not re.search(r"\w", last[0].text or ""):
            return last[0]
    return None


async def run_job(name: str):
    """دستور رو بفرست ← مسیر دکمه‌ها (مثلاً فروشگاه) ← دنبال دکمه‌ی هدف (مثلاً گیفت) بگرد و کلیک کن؛
    اگه نبود تایمر رو بخون، BK بزن و دوباره مسیر رو برو تا مهلت تموم بشه.
    قبل از هر کلیک، پیام تازه خونده میشه و وضعیت صفحه چک میشه؛ اگه مطمئن نباشه کلیک نمی‌کنه"""
    job = jobs.get(name)
    if not job or name in job_running:
        return
    job_running.add(name)

    def log(t):
        print(f"⏰ وظیفه «{name}»: {t}")

    try:
        chat = int(job["chat"])
        poll = max(0.3, float(job.get("poll", 1.0)))
        deadline = time.time() + float(job.get("window", 120))
        path = _norm_words(job.get("path") or [])
        targets = _norm_words(job.get("target") or [])
        back = _clean(normalize(job.get("back") or "")).lower().strip()
        log(f"ارسال «{job['send']}»")
        sent = await client.send_message(chat, job["send"])
        msg = await wait_reply_to(chat, sent.id, timeout=10)
        if not msg:
            log("پاسخ ربات نیومد")
            return
        mid, depth, cycles, stuck = msg.id, 0, 0, 0

        async def fresh():
            return await client.get_messages(chat, ids=mid)

        def at_home(m) -> bool:
            if not path:
                return True
            return _pick_named(_flat_buttons(m), path[0]) is not None and _strict_back(m, back) is None

        while time.time() < deadline:
            m = await fresh()
            if not m:
                log("پیام ربات پیدا نشد")
                return
            btns = _flat_buttons(m)

            # 1) هدف روی همین صفحه هست؟
            hits = [x for x in btns if _btn_has(x, targets)]
            if len(hits) > 1:
                ex = [x for x in hits if _btn_name(x) in targets or _fa(x.text or "") in targets]
                hits = ex if len(ex) == 1 else hits
            if len(hits) > 1:
                log("چند دکمه‌ی مشابه هدف هست: " + " | ".join((x.text or "").strip() for x in hits)
                    + " ← کلیک نشد؛ متن کامل‌تر دکمه رو برای هدف بنویس")
                return
            tb = hits[0] if hits else None
            if tb:
                if not await _click_fresh(chat, mid, m, tb):
                    continue                          # پیام همون لحظه ادیت شد؛ دوباره بخون
                job["last_hit"] = time.time()
                log(f"روی «{(tb.text or '').strip()}» کلیک شد ✅ (بعد از {cycles} دور)")
                return

            # 2) رفتن جلو توی مسیر (فقط دکمه‌ی مشخص و غیرمبهم)
            if depth < len(path):
                pb = _pick_named(btns, path[depth])
                if not pb:
                    log(f"دکمه‌ی مسیر «{path[depth]}» پیدا نشد یا مبهمه؛ کلیکی انجام نشد")
                    return
                sig = _msg_sig(m)
                if not await _click_fresh(chat, mid, m, pb):
                    continue
                if await _wait_sig_change(chat, mid, sig, 3.0) is None:
                    stuck += 1
                    if stuck >= 3:
                        log("صفحه با کلیک عوض نشد؛ متوقف شد")
                        return
                    continue                      # اثر نکرد → دوباره از روی پیام تازه
                stuck = 0
                depth += 1
                continue

            # 3) ته مسیر و هدف نیست → تایمر
            cycles += 1
            rem = parse_timer_secs(m.raw_text)
            left = deadline - time.time()
            if rem is not None:
                log(f"هدف نیست؛ تایمر {rem} ثانیه مونده (مهلت {int(left)} ثانیه)")
                if rem > left + 3:
                    log("تایمر از مهلت بیشتره؛ این نوبت رد شد")
                    if depth:
                        await _job_go_home(chat, mid, depth, back, at_home, log)
                    return
                if rem > 6:
                    await asyncio.sleep(max(0.0, min(rem - 3, left - 1)))     # نزدیک ریست سریع‌تر بشه
            if not path:                                                      # بدون مسیر: دستور رو دوباره بفرست
                sent = await client.send_message(chat, job["send"])
                nm = await wait_reply_to(chat, sent.id, timeout=6)
                if nm:
                    mid = nm.id
                await asyncio.sleep(poll)
                continue

            # 4) برگشت امن به صفحه‌ی اول
            if not await _job_go_home(chat, mid, depth, back, at_home, log):
                return
            depth = 0
            await asyncio.sleep(poll)
        log("تا پایان مهلت دکمه‌ی هدف نیومد")
        if depth:
            await _job_go_home(chat, mid, depth, back, at_home, log)
    except Exception as e:
        log(f"خطا: {e}")
    finally:
        job_running.discard(name)


async def _job_go_home(chat, mid, depth, back, at_home, log) -> bool:
    """با دکمه‌ی BKِ مطمئن به صفحه‌ی اول برمی‌گرده؛ اگه BK امن نبود یا صفحه‌ی اول تأیید نشد، دیگه کلیک نمی‌کنه"""
    for _ in range(max(depth, 1) + 3):
        m = await client.get_messages(chat, ids=mid)
        if not m:
            return False
        if at_home(m) and depth == 0:
            return True
        bb = _strict_back(m, back)
        if not bb:
            log("دکمه‌ی BKِ مطمئن پیدا نشد؛ برای جلوگیری از کلیک اشتباه متوقف شد")
            return False
        sig = _msg_sig(m)
        if not await _click_fresh(chat, mid, m, bb):
            continue
        if await _wait_sig_change(chat, mid, sig, 3.0) is None:
            log("بعد از BK صفحه عوض نشد؛ متوقف شد")
            return False
        depth -= 1
    m = await client.get_messages(chat, ids=mid)
    if m and at_home(m):
        return True
    log("برگشت به صفحه‌ی اول تأیید نشد؛ متوقف شد")
    return False


async def job_scheduler():
    try:
        while True:
            now = time.time()
            for name, job in list(jobs.items()):
                if job.get("enabled", True) and name not in job_running and now >= job.get("last", 0) + job["every"]:
                    job["last"] = now
                    asyncio.create_task(run_job(name))
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


def ensure_job_scheduler():
    global job_scheduler_task
    if job_scheduler_task is None or job_scheduler_task.done():
        job_scheduler_task = asyncio.create_task(job_scheduler())


_JOB_HELP = ("مثال:\nوظیفه افزودن هالووین | هر: 3h | ارسال: هالووین میویی | مسیر: فروشگاه | هدف: گیفت | مهلت: 2m\n"
             "(job add halloween | every: 3h | send: ... | path: ... | target: gift | window: 2m)")


def job_command(text: str, chat_id: int) -> str:
    parts = text.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    arg = parts[2].strip() if len(parts) > 2 else ""
    if sub in ("list", "status"):
        if not jobs:
            return "هیچ وظیفه‌ای ثبت نشده.\n" + _JOB_HELP
        out = ["⏰ وظیفه‌ها:"]
        for n, j in jobs.items():
            nxt = j.get("last", 0) + j["every"] - time.time()
            out.append(f"• {n}: {'روشن ✅' if j.get('enabled', True) else 'خاموش ❌'} — هر {_fmt_dur(j['every'])} — «{j['send']}»"
                       f" ← {' > '.join(j.get('path') or []) or '—'} ← {', '.join(j['target'])} | مهلت {_fmt_dur(j.get('window', 120))}"
                       f" | نوبت بعدی: {'الان' if nxt <= 0 else _fmt_dur(round(nxt))}{' | در حال اجرا' if n in job_running else ''}")
        return "\n".join(out)
    if sub in ("del", "on", "off", "now"):
        name = arg.split()[0] if arg else ""
        j = jobs.get(name)
        if not j:
            return f"وظیفه‌ای به اسم «{name}» نیست. (job list)"
        if sub == "del":
            jobs.pop(name, None)
            return f"وظیفه «{name}» حذف شد 🗑"
        if sub == "now":
            j["last"] = time.time()
            ensure_job_scheduler()
            if name in job_running:
                return f"وظیفه «{name}» همین الان در حال اجراست ⏳"
            asyncio.create_task(run_job(name))
            return f"وظیفه «{name}» شروع شد ⏳"
        want = sub == "on"
        if bool(j.get("enabled", True)) == want:
            return f"وظیفه «{name}» " + ("فعال می‌باشد ✅ (از قبل روشن بود)" if want else "غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
        j["enabled"] = want
        if want:
            j["last"] = time.time()
            ensure_job_scheduler()
        return f"وظیفه «{name}» " + ("فعال شد ✅" if want else "غیرفعال شد ❌")
    if sub == "add":
        segs = [x.strip() for x in re.split(r"[|\n]", arg) if x.strip()]
        if not segs or " " in segs[0] or ":" in segs[0]:
            return "اسم وظیفه باید یک کلمه باشه.\n" + _JOB_HELP
        name, spec = segs[0], {}
        for sg in segs[1:]:
            k, sep, v = re.split(r"([:：])", sg, maxsplit=1) if re.search(r"[:：]", sg) else (sg, "", "")
            key = _JOB_KEYS.get(k.strip().lower())
            if not sep or not key:
                return f"بخش نامعتبر: «{sg}»\n" + _JOB_HELP
            spec[key] = v.strip()
        every = parse_dur(spec.get("every", ""))
        if not spec.get("send") or every is None or every < 5 or not spec.get("target"):
            return "ارسال، هر (حداقل 5s) و هدف الزامیه.\n" + _JOB_HELP
        window = parse_dur(spec.get("window", "2m"))
        if window is None or window < 5:
            return "مهلت نامعتبره؛ مثال: مهلت: 2m"
        try:
            poll = float(spec.get("poll", "1").translate(_DIGITS))
            chat = int(spec["chat"].translate(_DIGITS)) if spec.get("chat") else chat_id
        except ValueError:
            return "سرعت یا گروه نامعتبره.\n" + _JOB_HELP
        existed = name in jobs
        jobs[name] = {
            "chat": chat, "every": every, "send": spec["send"],
            "path": [x.strip() for x in re.split(r"[>،,]", spec.get("path", "")) if x.strip()],
            "target": [x.strip() for x in re.split(r"[،,]", spec["target"]) if x.strip()],
            "window": window, "poll": max(0.3, poll), "enabled": True, "last": time.time(),
            "back": spec.get("back", "").strip(),
        }
        ensure_job_scheduler()
        j = jobs[name]
        return (f"وظیفه «{name}» {'به‌روزرسانی' if existed else 'ثبت'} شد ✅\n"
                f"• هر {_fmt_dur(every)} «{j['send']}» ارسال میشه (چت {chat})\n"
                f"• مسیر: {' > '.join(j['path']) or '—'} | هدف: {', '.join(j['target'])} | مهلت: {_fmt_dur(window)}"
                f"{' | بازگشت: ' + j['back'] if j['back'] else ''}\n"
                f"• نوبت بعدی: {_fmt_dur(every)} دیگه — برای تست: وظیفه الان {name}")
    return _JOB_HELP


# ======================= هوش مصنوعی / مترجم / ماشین‌حساب =======================
import ast
import math
import urllib.error
import urllib.parse
import urllib.request

# ---------- هوش مصنوعی ----------
# name: (نوع API, آدرس پایه, مدل پیش‌فرض, متغیر محیطی کلید)
AI_DEFAULTS = {
    "openai": ("openai", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1", "claude-sonnet-5-5", "ANTHROPIC_API_KEY"),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.0-flash", "GEMINI_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
    "groq": ("openai", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "deepseek": ("openai", "https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "mistral": ("openai", "https://api.mistral.ai/v1", "mistral-small-latest", "MISTRAL_API_KEY"),
    "xai": ("openai", "https://api.x.ai/v1", "grok-2-latest", "XAI_API_KEY"),
    "perplexity": ("openai", "https://api.perplexity.ai", "sonar", "PERPLEXITY_API_KEY"),
    "together": ("openai", "https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
    "ollama": ("openai", "http://localhost:11434/v1", "llama3", ""),
}
AI_ALIASES = {
    "gpt": "openai", "chatgpt": "openai", "چتجیپیتی": "openai", "جیپیتی": "openai", "اوپنایآی": "openai",
    "claude": "anthropic", "کلود": "anthropic", "آنتروپیک": "anthropic",
    "google": "gemini", "جمینای": "gemini", "جمنای": "gemini", "گوگل": "gemini",
    "grok": "xai", "گروک": "xai", "دیپسیک": "deepseek", "میسترال": "mistral", "پرپلکسیتی": "perplexity",
    "لاما": "groq", "llama": "groq", "اولاما": "ollama", "اپنروتر": "openrouter",
}
ai_cfg = {"default": "openai", "providers": {}, "memory": True, "max_turns": 6,
          "system": "تو یک دستیار مفید هستی. به همون زبانی که کاربر می‌نویسه جواب بده و مختصر و دقیق باش."}
ai_hist: dict = {}       # {chat_id: [{"role","content"}]} — فقط توی RAM
_AI_FA_SUBS = {"کلید": "key", "مدل": "model", "پیشفرض": "default", "لیست": "list", "افزودن": "add", "حذف": "del",
               "سیستم": "system", "پاکسازی": "clear", "پاک": "clear", "حافظه": "memory", "وضعیت": "list"}


def _nk(x: str) -> str:
    return re.sub(r"[\s\u200c_\-]+", "", (x or "").lower())


def ai_resolve(name: str):
    """اسم/اسم مستعار → اسم ارائه‌دهنده (یا None)"""
    k = _nk(name)
    for n in list(AI_DEFAULTS) + list(ai_cfg["providers"]):
        if _nk(n) == k:
            return n
    return AI_ALIASES.get(k)


def ai_provider(name: str):
    base = AI_DEFAULTS.get(name)
    d = {"kind": "openai", "url": "", "model": "", "key": "", "env": ""}
    if base:
        d.update(kind=base[0], url=base[1], model=base[2], env=base[3])
    d.update({k: v for k, v in ai_cfg["providers"].get(name, {}).items() if v})
    key = d["key"] or (os.environ.get(d["env"], "") if d["env"] else "")
    return d, key


def _http_json(url, data=None, headers=None, timeout=60):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"HTTP {e.code}: {raw}")


def _ai_call_sync(name: str, messages: list, system: str) -> str:
    d, key = ai_provider(name)
    if not key and name != "ollama" and d["env"]:
        raise RuntimeError(f"کلید «{name}» تنظیم نشده؛ بنویس: هوش کلید {name} <کلید>")
    if d["kind"] == "anthropic":
        data = _http_json(d["url"].rstrip("/") + "/messages",
                          {"model": d["model"], "max_tokens": 2048, "system": system, "messages": messages},
                          {"x-api-key": key, "anthropic-version": "2023-06-01"}, 90)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if d["kind"] == "gemini":
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages]
        data = _http_json(f"{d['url'].rstrip('/')}/models/{d['model']}:generateContent?key={urllib.parse.quote(key)}",
                          {"contents": contents, "systemInstruction": {"parts": [{"text": system}]}}, None, 90)
        cands = data.get("candidates") or []
        if not cands:
            raise RuntimeError(f"پاسخ خالی: {str(data)[:200]}")
        return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", [])).strip()
    # سازگار با OpenAI (OpenAI, OpenRouter, Groq, DeepSeek, Mistral, xAI, Perplexity, Together, Ollama, ...)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = _http_json(d["url"].rstrip("/") + "/chat/completions",
                      {"model": d["model"], "messages": [{"role": "system", "content": system}] + messages}, headers, 90)
    return (data["choices"][0]["message"].get("content") or "").strip()


async def ai_ask(chat_id: int, name: str, question: str, context: str = "", use_memory: bool = True) -> str:
    q = f"{context}\n\n{question}".strip() if context else question
    hist = ai_hist.get(chat_id, []) if (use_memory and ai_cfg.get("memory", True)) else []
    msgs = hist + [{"role": "user", "content": q}]
    ans = await asyncio.to_thread(_ai_call_sync, name, msgs, ai_cfg.get("system", ""))
    if not ans:
        raise RuntimeError("پاسخ خالی بود")
    if use_memory and ai_cfg.get("memory", True):
        ai_hist[chat_id] = (hist + [{"role": "user", "content": q}, {"role": "assistant", "content": ans}])[-2 * int(ai_cfg.get("max_turns", 6)):]
    return ans


async def _send_long(event, text: str, first_prefix: str = ""):
    """متن بلند: بخش اول با ادیت پیام، بقیه با ریپلای (بدون فرمت‌دهی تا علامت‌های * و _ خراب نکنه)"""
    chunks, cur = [], ""
    for line in (first_prefix + text).split("\n"):
        while len(line) > 3900:
            chunks.append(line[:3900]); line = line[3900:]
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    await event.edit(chunks[0] or "…", parse_mode=None)
    for c in chunks[1:]:
        await client.send_message(event.chat_id, c, reply_to=event.id, parse_mode=None)


def ai_list_text() -> str:
    names = list(AI_DEFAULTS) + [n for n in ai_cfg["providers"] if n not in AI_DEFAULTS]
    out = ["🤖 هوش‌های مصنوعی:"]
    for n in names:
        d, key = ai_provider(n)
        ok = bool(key) or n == "ollama"
        out.append(f"{'⭐' if n == ai_cfg['default'] else '•'} {n}: {'✅ آماده' if ok else '❌ بدون کلید'} — {d['model']}")
    out.append(f"حافظه‌ی گفتگو: {'روشن' if ai_cfg.get('memory', True) else 'خاموش'} ({ai_cfg.get('max_turns', 6)} نوبت)")
    out.append("\nنمونه: هوش سلام | هوش claude سلام | هوش کلید openai sk-... | هوش مدل openai gpt-4o | هوش پیش‌فرض claude")
    out.append("(روی یه پیام ریپلای کن و «هوش خلاصه کن» بنویس تا همون پیام هم به هوش داده بشه)")
    return "\n".join(out)


async def ai_handle(event, text: str):
    rest = text[2:].strip()
    parts = rest.split(None, 3)
    sub = parts[0].lower() if parts else ""
    if sub in ("list", "status", ""):
        await event.edit(ai_list_text(), parse_mode=None)
        return
    if sub == "clear":
        ai_hist.pop(event.chat_id, None)
        await event.edit("حافظه‌ی گفتگوی هوش توی این چت پاک شد 🧹", parse_mode=None)
        return
    if sub == "memory":
        arg = parts[1].lower() if len(parts) > 1 else ""
        if arg not in ("on", "off"):
            await event.edit("مثال: هوش حافظه روشن | هوش حافظه خاموش", parse_mode=None)
            return
        want = arg == "on"
        if bool(ai_cfg.get("memory", True)) == want:
            await event.edit(f"حافظه‌ی گفتگو {'فعال می‌باشد ✅ (از قبل روشن بود)' if want else 'غیرفعال می‌باشد ❌ (از قبل خاموش بود)'}", parse_mode=None)
            return
        ai_cfg["memory"] = want
        await event.edit("حافظه‌ی گفتگو فعال شد ✅" if want else "حافظه‌ی گفتگو غیرفعال شد ❌", parse_mode=None)
        return
    if sub == "system":
        prompt = rest[len("system"):].strip()
        if not prompt:
            await event.edit(f"دستور سیستم فعلی:\n{ai_cfg.get('system')}\n\nتغییر: هوش سیستم <متن>", parse_mode=None)
            return
        ai_cfg["system"] = prompt
        await event.edit("دستور سیستم تنظیم شد ✅", parse_mode=None)
        return
    if sub in ("key", "model", "default", "del"):
        name = ai_resolve(parts[1]) if len(parts) > 1 else None
        if sub == "default":
            if not name:
                await event.edit("مثال: هوش پیش‌فرض claude", parse_mode=None)
                return
            ai_cfg["default"] = name
            await event.edit(f"هوش پیش‌فرض روی «{name}» تنظیم شد ✅", parse_mode=None)
            return
        if sub == "del":
            if not name or name in AI_DEFAULTS:
                await event.edit("فقط ارائه‌دهنده‌های اضافه‌شده با «هوش افزودن» حذف میشن؛ برای بقیه فقط کلید رو عوض کن.", parse_mode=None)
                return
            ai_cfg["providers"].pop(name, None)
            await event.edit(f"«{name}» حذف شد 🗑", parse_mode=None)
            return
        if not name or len(parts) < 3:
            await event.edit("مثال: هوش کلید openai sk-...   |   هوش مدل openai gpt-4o", parse_mode=None)
            return
        value = rest.split(None, 2)[2].strip()
        ai_cfg["providers"].setdefault(name, {})["key" if sub == "key" else "model"] = value
        if sub == "key":
            await event.edit(f"🔑 کلید «{name}» ذخیره شد ✅ (پیام با کلید ادیت شد؛ برای امنیت بهتره دستور رو توی Saved Messages بزنی)", parse_mode=None)
        else:
            await event.edit(f"مدل «{name}» روی «{value}» تنظیم شد ✅", parse_mode=None)
        return
    if sub == "add":
        # هوش افزودن <اسم> <آدرس پایه> <مدل> [کلید]   (هر API سازگار با OpenAI)
        a = rest.split()
        if len(a) < 4 or not a[2].startswith("http"):
            await event.edit("مثال: هوش افزودن mine https://my-host/v1 my-model [کلید]\n(برای هر سرویس سازگار با OpenAI)", parse_mode=None)
            return
        ai_cfg["providers"][a[1]] = {"kind": "openai", "url": a[2], "model": a[3], "key": a[4] if len(a) > 4 else ""}
        await event.edit(f"«{a[1]}» اضافه شد ✅ (استفاده: هوش {a[1]} سلام)", parse_mode=None)
        return

    # --- سوال ---
    name = ai_cfg["default"]
    question = rest
    first = ai_resolve(parts[0])
    if first and len(parts) > 1:
        name, question = first, rest.split(None, 1)[1]
    reply = await event.get_reply_message()
    context = (reply.raw_text or "").strip() if reply else ""
    d, key = ai_provider(name)
    if not key and name != "ollama":
        await event.edit(f"کلید «{name}» تنظیم نشده.\nبنویس: هوش کلید {name} <کلید>\nیا متغیر محیطی {d['env']} رو بذار.\n(لیست: هوش لیست)", parse_mode=None)
        return
    await event.edit(f"⏳ {name} ({d['model']}) ...", parse_mode=None)
    try:
        ans = await ai_ask(event.chat_id, name, question, context)
    except Exception as e:
        await event.edit(f"❌ خطا از {name}: {str(e)[:500]}", parse_mode=None)
        return
    head = f"❓ {question}\n\n" if not context else f"❓ {question}\n(روی پیام ریپلای‌شده)\n\n"
    await _send_long(event, ans, head + f"🤖 {name}:\n")


# ---------- مترجم ----------
_LANG_TABLE = {
    "fa": "فارسی|persian|farsi", "en": "انگلیسی|english", "ar": "عربی|arabic", "tr": "ترکی|ترکیاستانبولی|turkish",
    "ru": "روسی|russian", "de": "آلمانی|german", "fr": "فرانسوی|french", "es": "اسپانیایی|spanish", "it": "ایتالیایی|italian",
    "pt": "پرتغالی|portuguese", "zh-CN": "چینی|chinese|zh", "zh-TW": "چینیسنتی", "ja": "ژاپنی|japanese", "ko": "کره‌ای|کرهای|korean",
    "hi": "هندی|hindi", "ur": "اردو|urdu", "he": "عبری|hebrew|iw", "nl": "هلندی|dutch", "sv": "سوئدی|swedish", "no": "نروژی|norwegian",
    "da": "دانمارکی|danish", "fi": "فنلاندی|finnish", "pl": "لهستانی|polish", "uk": "اوکراینی|ukrainian", "cs": "چکی|czech",
    "sk": "اسلواکی|slovak", "ro": "رومانیایی|romanian", "hu": "مجاری|hungarian", "bg": "بلغاری|bulgarian", "el": "یونانی|greek",
    "sr": "صربی|serbian", "hr": "کرواتی|croatian", "sl": "اسلوونیایی|slovenian", "lt": "لیتوانیایی|lithuanian", "lv": "لتونیایی|latvian",
    "et": "استونیایی|estonian", "id": "اندونزیایی|indonesian", "ms": "مالایی|malay", "th": "تایلندی|thai", "vi": "ویتنامی|vietnamese",
    "tl": "فیلیپینی|filipino", "bn": "بنگالی|bengali", "ta": "تامیلی|tamil", "te": "تلوگو|telugu", "mr": "مراتی|marathi",
    "gu": "گجراتی|gujarati", "pa": "پنجابی|punjabi", "ne": "نپالی|nepali", "si": "سینهالی|sinhala", "ps": "پشتو|pashto",
    "ku": "کردی|kurdish", "az": "آذربایجانی|آذری|azerbaijani", "kk": "قزاقی|kazakh", "uz": "ازبکی|uzbek", "ky": "قرقیزی|kyrgyz",
    "tg": "تاجیکی|tajik", "mn": "مغولی|mongolian", "ka": "گرجی|georgian", "hy": "ارمنی|armenian", "sq": "آلبانیایی|albanian",
    "mk": "مقدونی|macedonian", "bs": "بوسنیایی|bosnian", "be": "بلاروسی|belarusian", "is": "ایسلندی|icelandic", "ga": "ایرلندی|irish",
    "cy": "ولزی|welsh", "ca": "کاتالان|catalan", "eu": "باسکی|basque", "gl": "گالیسی|galician", "mt": "مالتی|maltese",
    "af": "آفریکانس|افریقایی|afrikaans", "sw": "سواحیلی|swahili", "am": "امهری|amharic", "so": "سومالی|somali", "ha": "هوسا|hausa",
    "yo": "یوروبا|yoruba", "zu": "زولو|zulu", "km": "خمر|khmer", "lo": "لائوسی|lao", "my": "برمه‌ای|برمهای|burmese",
    "kn": "کانادا|kannada", "ml": "مالایالام|malayalam", "sd": "سندی|sindhi", "eo": "اسپرانتو|esperanto", "la": "لاتین|latin",
    "lb": "لوکزامبورگی|luxembourgish", "yi": "ییدیش|yiddish", "jw": "جاوه‌ای|جاوهای|javanese", "mi": "مائوری|maori",
}
LANG_LOOKUP = {}
LANG_FA_NAME = {}
for _code, _names in _LANG_TABLE.items():
    LANG_LOOKUP[_code.lower()] = _code
    for _i, _n in enumerate(_names.split("|")):
        LANG_LOOKUP[_nk(_n)] = _code
        if _i == 0:
            LANG_FA_NAME[_code] = _n


def _is_rtl_text(t: str) -> bool:
    letters = re.findall(r"[A-Za-z\u0600-\u06FF]", t or "")
    if not letters:
        return False
    return sum(1 for c in letters if c >= "\u0600") / len(letters) > 0.4


def _chunk_text(t: str, n: int = 1800):
    out, cur = [], ""
    for line in t.split("\n"):
        while len(line) > n:
            out.append(line[:n]); line = line[n:]
        if cur and len(cur) + len(line) + 1 > n:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur or not out:
        out.append(cur)
    return out


_gt_block = {"until": 0.0}      # اگه گوگل IP سرور رو محدود کرد (429) چند دقیقه سراغش نمی‌ریم


def _gt_google(text: str, tl: str, sl: str, host: str):
    parts, src = [], None
    for ch in _chunk_text(text):
        url = f"https://{host}/translate_a/single?" + urllib.parse.urlencode(
            {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": ch})
        data = _http_json(url, None, None, 20)
        parts.append("".join(seg[0] for seg in data[0] if seg and seg[0]))
        src = src or (data[2] if len(data) > 2 else None)
    return "\n".join(parts), src


def _gt_lingva(text: str, tl: str, sl: str):
    last = None
    for host in ("lingva.ml", "lingva.thedaviddelta.com", "translate.plausibility.cloud"):
        try:
            parts = []
            for ch in _chunk_text(text, 700):
                data = _http_json(f"https://{host}/api/v1/{sl}/{tl}/" + urllib.parse.quote(ch, safe=""), None, None, 20)
                parts.append(data["translation"])
            return "\n".join(parts), (None if sl == "auto" else sl)
        except Exception as e:
            last = e
    raise RuntimeError(f"lingva: {last}")


def _gt_mymemory(text: str, tl: str, sl: str):
    if sl == "auto":
        sl = "fa" if _is_rtl_text(text) else "en"
    parts = []
    for ch in _chunk_text(text, 450):
        url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": ch, "langpair": f"{sl}|{tl}"})
        data = _http_json(url, None, None, 20)
        if str(data.get("responseStatus")) != "200":
            raise RuntimeError(f"mymemory: {str(data.get('responseDetails'))[:120]}")
        parts.append(data["responseData"]["translatedText"])
    return "\n".join(parts), sl


def _gtranslate_sync(text: str, tl: str, sl: str = "auto"):
    """گوگل ← (اگه محدود شد) گوگل با آدرس دوم ← lingva ← mymemory؛ اولین جواب موفق"""
    errs = []
    if time.time() >= _gt_block["until"]:
        for host in ("translate.googleapis.com", "translate.google.com"):
            try:
                return _gt_google(text, tl, sl, host)
            except Exception as e:
                errs.append(str(e)[:60])
                if "429" in str(e):
                    _gt_block["until"] = time.time() + 600
                    break
    for fn in (_gt_lingva, _gt_mymemory):
        try:
            return fn(text, tl, sl)
        except Exception as e:
            errs.append(str(e)[:80])
    raise RuntimeError(" | ".join(errs))


async def translate_handle(event, text: str):
    rest = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    reply = await event.get_reply_message()
    tl = None
    if rest:
        first, _, remain = rest.partition(" ")
        code = LANG_LOOKUP.get(_nk(first))
        if code:
            tl, rest = code, remain.strip()
    body = rest or ((reply.raw_text or "").strip() if reply else "")
    if not body:
        await event.edit("مثال: ترجمه انگلیسی سلام چطوری؟ | ترجمه fa hello | روی یه پیام ریپلای کن و بنویس «ترجمه en»\n(بدون زبان: فارسی ← انگلیسی و بقیه ← فارسی)", parse_mode=None)
        return
    if not tl:
        tl = "en" if _is_rtl_text(body) else "fa"
    await event.edit("⏳ در حال ترجمه ...", parse_mode=None)
    try:
        out, src = await asyncio.to_thread(_gtranslate_sync, body, tl)
    except Exception as e:
        out, src = None, None
        d, key = ai_provider(ai_cfg["default"])
        if key or ai_cfg["default"] == "ollama":
            try:
                out = await ai_ask(event.chat_id, ai_cfg["default"],
                                   f"Translate the following text to {LANG_FA_NAME.get(tl, tl)} ({tl}). Output only the translation.\n\n{body}",
                                   use_memory=False)
            except Exception as e2:
                await event.edit(f"❌ ترجمه ناموفق: {str(e)[:200]} | {str(e2)[:200]}", parse_mode=None)
                return
        else:
            await event.edit(f"❌ ترجمه ناموفق: {str(e)[:300]}", parse_mode=None)
            return
    srcn = LANG_FA_NAME.get(LANG_LOOKUP.get((src or "").lower(), ""), src or "؟")
    await _send_long(event, out, f"🌐 {srcn} ← {LANG_FA_NAME.get(tl, tl)}\n📝 {body[:300]}{'…' if len(body) > 300 else ''}\n\n✅ ")


# ---------- ماشین‌حساب ----------
_CALC_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
    "gcd": math.gcd, "lcm": math.lcm, "min": min, "max": max, "sum": lambda *a: sum(a), "radians": math.radians,
    "degrees": math.degrees, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "pow": None,
}
_CALC_CONST = {"pi": math.pi, "e": math.e, "tau": math.tau, "پی": math.pi}


class CalcError(Exception):
    pass


def _calc_pow(a, b):
    if isinstance(a, int) and isinstance(b, int) and b >= 0 and abs(a) > 1:
        if b * math.log2(abs(a)) > 200000:
            raise CalcError("عدد خیلی بزرگ میشه")
    if abs(b) > 100000:
        raise CalcError("توان خیلی بزرگه")
    try:
        r = a ** b
    except ZeroDivisionError:
        raise CalcError("تقسیم بر صفر")
    except OverflowError:
        raise CalcError("عدد خیلی بزرگ میشه")
    if isinstance(r, complex):
        raise CalcError("نتیجه مختلط میشه")
    return r


def _calc_ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return n.value
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
        v = _calc_ev(n.operand)
        return -v if isinstance(n.op, ast.USub) else +v
    if isinstance(n, ast.BinOp):
        a, b = _calc_ev(n.left), _calc_ev(n.right)
        try:
            if isinstance(n.op, ast.Add): return a + b
            if isinstance(n.op, ast.Sub): return a - b
            if isinstance(n.op, ast.Mult): return a * b
            if isinstance(n.op, ast.Div): return a / b
            if isinstance(n.op, ast.FloorDiv): return a // b
            if isinstance(n.op, ast.Mod): return a % b
            if isinstance(n.op, ast.Pow): return _calc_pow(a, b)
        except ZeroDivisionError:
            raise CalcError("تقسیم بر صفر")
        except OverflowError:
            raise CalcError("عدد خیلی بزرگ میشه")
    if isinstance(n, ast.Name) and n.id.lower() in _CALC_CONST:
        return _CALC_CONST[n.id.lower()]
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
        fn = n.func.id.lower()
        if fn in _CALC_FUNCS:
            args = [_calc_ev(a) for a in n.args]
            if fn == "pow" and len(args) == 2:
                return _calc_pow(*args)
            if fn == "factorial" and (not args or args[0] > 3000):
                raise CalcError("فاکتوریل فقط تا 3000")
            try:
                return _CALC_FUNCS[fn](*args)
            except (ValueError, TypeError, OverflowError, ZeroDivisionError) as e:
                raise CalcError("ورودی تابع نامعتبره")
    raise CalcError("عبارت نامعتبره")


def calc_eval(expr: str):
    e = (expr or "").translate(_DIGITS).strip()
    for a, b in (("×", "*"), ("÷", "/"), ("−", "-"), ("–", "-"), ("٫", "."), ("^", "**"), ("√", "sqrt"), ("π", "pi"), ("،", ",")):
        e = e.replace(a, b)
    e = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(A-Za-z])", r"(\1/100)", e)      # 20% → 0.2 ؛ 10 % 3 → باقی‌مانده
    e = re.sub(r"(?<=\d)\s*\(", "*(", e)                                        # 2(3+4) → 2*(3+4)
    if not e or len(e) > 400:
        raise CalcError("عبارت خالی یا خیلی بلنده")
    try:
        tree = ast.parse(e, mode="eval")
    except SyntaxError:
        raise CalcError("عبارت نامعتبره")
    return _calc_ev(tree.body)


def fmt_num(x) -> str:
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        t = f"{x:,}"
        return t if len(t) < 120 else f"{str(x)[:20]}… ({len(str(x))} رقم)"
    if x != x or x in (float("inf"), float("-inf")):
        return str(x)
    if float(x).is_integer() and abs(x) < 1e15:
        return f"{int(x):,}"
    t = f"{x:.12g}"
    if "e" in t:
        return t
    ip, _, fp = t.partition(".")
    sign = "-" if ip.startswith("-") else ""
    ip = f"{int(ip.lstrip('-')):,}"
    return f"{sign}{ip}.{fp}" if fp else f"{sign}{ip}"


_CALC_RE = re.compile(r"^(calc|calculate|حساب|محاسبه|ماشین\s*حساب|ماشین\u200cحساب)\s+(.+)$", re.I | re.S)


def calc_extract(text: str):
    """(عبارت، صریح؟) یا None. «calc …» صریحه و خطا نشون میده؛ «حساب …» و «= …» فقط اگه واقعاً عبارت ریاضی باشه"""
    t = (text or "").strip()
    m = _CALC_RE.match(t)
    if m:
        return m.group(2).strip(), m.group(1).lower() == "calc"
    if t.startswith("=") and len(t) > 1:
        return t[1:].strip(), False
    return None


async def calc_handle(event, expr: str, explicit: bool) -> bool:
    try:
        r = calc_eval(expr)
    except CalcError as e:
        if explicit:
            await event.edit(f"🧮 {expr}\n❌ {e}", parse_mode=None)
            return True
        return False
    await event.edit(f"🧮 {expr}\n= {fmt_num(r)}", parse_mode=None)
    return True


# ======================= اعلان‌های پیوی ربات (pm) =======================
pm_cfg = {"enabled": True, "bot": "meowie", "builtin": {}, "rules": {}}
# شرط پیش‌فرض هر اعلانِ آماده (متن پیام نرمال‌شده)
PM_DEFAULTS = {
    "cooked": lambda t: ("ماهی" in t and ("پخید" in t or "پخت" in t) and ("وقت" in t or "شما" in t)) and "زمان مورد نیاز" not in t,
    "miner": lambda t: "ماینر" in t and any(w in t for w in ("تبدیل", "آپلود")) and any(
        w in t for w in ("به پایان رسید", "تکمیل", "انجام شد", "موفقیت", "کامل")),
    "power": lambda t: "اعتبار برق" in t and ("کافی نیست" in t or "ماینر خاموش شد" in t),
    "factory": lambda t: "کارخونه" in t and "به پایان رسید" in t,
}
PM_LABELS = {"cooked": "پخت ماهی ← چک یخچال و غذا", "miner": "پایان تبدیل توکن ← فروش ارز",
             "power": "اتمام اعتبار برق ← شارژ برق ماینر", "factory": "پایان تولید کارخونه ← بررسی انبار/فروش"}
_PM_BUILTIN_ALIAS = {"cook": "cooked", "cooked": "cooked", "fish": "cooked", "miner": "miner", "power": "power", "factory": "factory"}
_PM_ACTIONS = {"fridge": "fridge", "یخچال": "fridge", "fish": "fish", "ماهی": "fish", "ماهیگیری": "fish", "farm": "farm", "فارم": "farm",
               "miner": "miner", "ماینر": "miner", "sell": "sell", "فروش": "sell", "power": "power", "برق": "power",
               "factory": "factory", "کارخونه": "factory", "transfer": "transfer", "انتقال": "transfer",
               "notify": "notify", "forward": "notify", "اطلاع": "notify", "اطلاعرسانی": "notify", "فوروارد": "notify"}
_PM_KEYS = {"match": "match", "عبارت": "match", "شامل": "match", "all": "all", "همه": "all", "not": "not", "نباشد": "not",
            "action": "action", "عمل": "action", "کار": "action", "to": "to", "به": "to", "chat": "chat", "چت": "chat",
            "گروه": "chat", "bot": "bot", "ربات": "bot", "cooldown": "cooldown", "فاصله": "cooldown", "delay": "delay", "تاخیر": "delay"}


def _pm_phr(lst):
    return [_clean(normalize(x)).lower().strip() for x in (lst or []) if x and x.strip()]


def pm_builtin_enabled(key: str) -> bool:
    return bool(pm_cfg.get("enabled", True)) and bool(pm_cfg.get("builtin", {}).get(key, {}).get("enabled", True))


def pm_gate(key: str, t: str) -> bool:
    """اعلان آماده «key» برای متن t اجرا بشه؟ (روشن بودن + عبارت دستیِ تنظیم‌شده یا شرط پیش‌فرض)"""
    if not pm_builtin_enabled(key):
        return False
    ms = _pm_phr(pm_cfg.get("builtin", {}).get(key, {}).get("match"))
    tl = (t or "").lower()
    return any(m in tl for m in ms) if ms else bool(PM_DEFAULTS[key](t))


def _pm_bot_match(sender, spec) -> bool:
    """فرستنده با مشخصات spec می‌خونه؟ (اسم/یوزرنیم/آیدی عددی؛ چند ربات با ویرگول؛ any = هر ربات)"""
    if not getattr(sender, "bot", False):
        return False
    wants = [w for w in (_nk(x).lstrip("@") for x in re.split(r"[،,|]", str(spec))) if w]
    if not wants or any(w in ("any", "all", "همه") for w in wants):
        return True
    un = _nk(getattr(sender, "username", "") or "")
    fn = _nk(getattr(sender, "first_name", "") or "")
    sid = str(getattr(sender, "id", ""))
    return any(w == sid or w in un or w in fn for w in wants)


def _pm_bot_ok(sender) -> bool:
    """فرستنده یکی از رباتای انتخاب‌شده‌ست؟"""
    return _pm_bot_match(sender, pm_cfg.get("bot", "meowie"))


def _pm_rule_bot_ok(r: dict, sender) -> bool:
    """اعلان دلخواه: اگه «ربات:» داشته باشه همون، وگرنه رباتای انتخاب‌شده‌ی کلی"""
    return _pm_bot_match(sender, r["bot"]) if r.get("bot") else _pm_bot_ok(sender)


_PM_BOT_SUBS = {"add": "add", "افزودن": "add", "اضافه": "add", "del": "del", "حذف": "del", "remove": "del",
                "clear": "clear", "پاک": "clear", "پاکسازی": "clear", "list": "list", "لیست": "list",
                "set": "set", "تنظیم": "set"}
pm_bot_cache: list = []      # آخرین «اعلان ربات لیست» (برای انتخاب با شماره)


def _pm_bot_tokens() -> list:
    return [x.strip() for x in re.split(r"[،,|]", str(pm_cfg.get("bot", ""))) if x.strip() and x.strip().lower() not in ("any", "all", "همه")]


def _pm_bot_cur_text() -> str:
    t = _pm_bot_tokens()
    return "، ".join(t) if t else "any (هر ربات)"


def pm_rule_matches(r: dict, t: str) -> bool:
    tl = (t or "").lower()
    ms, al, nt = _pm_phr(r.get("match")), _pm_phr(r.get("all")), _pm_phr(r.get("not"))
    if not ms and not al:
        return False
    if ms and not any(m in tl for m in ms):
        return False
    if al and not all(a in tl for a in al):
        return False
    return not any(n in tl for n in nt)


async def pm_do_fridge():
    """بررسی یخچال (و غذا دادن اگه پیشی گرسنه بود) توی گروه فارم — همون کاری که پیام «ماهی پخته شد» می‌کرد"""
    chat = farm_chat_id
    if chat is None:
        print("اعلان: گروه فارم تنظیم نیست؛ بنویس: farm on")
        return
    old = cook_timers.pop(chat, None)
    if old:
        old.cancel()
    async with feed_lock:
        await asyncio.sleep(3)
        hungry, room = False, None
        if feed_enabled:
            sent = await client.send_message(chat, MEOWIE_TRIGGER_TEXT)
            reply = await wait_reply_to(chat, sent.id, need_buttons=False)
            if reply:
                hungry, room = feed_decision(reply.raw_text)
        await fridge_session(chat, hungry=hungry, allow_fish=False, room=room)


def _pm_target(x):
    x = (x or "").strip()
    return int(x) if x.lstrip("-").isdigit() else (x or "me")


async def pm_exec(kind: str, arg: str, r: dict, name: str, raw: str):
    if kind == "notify":
        await client.send_message(_pm_target(r.get("to")), f"🔔 {name}\n{raw}", parse_mode=None)
    elif kind == "send":
        chat = int(r["chat"]) if r.get("chat") else farm_chat_id
        if chat is None:
            print(f"اعلان «{name}»: چت مقصد پیام مشخص نیست (چت: ...)")
            return
        await client.send_message(chat, arg, parse_mode=None)
    elif kind == "cmd":
        asyncio.create_task(run_as_self("me", arg))
    elif kind == "fridge":
        await pm_do_fridge()
    elif kind == "fish":
        if farm_chat_id is None:
            print("اعلان: گروه فارم تنظیم نیست")
            return
        async with feed_lock:
            await fish_once(farm_chat_id, force=True)
    elif kind == "farm":
        await meowie_farm_once()
    elif kind == "miner":
        await miner_session()
    elif kind == "sell":
        await miner_sell_session()
    elif kind == "power":
        await miner_power_session(force=True)
    elif kind == "factory":
        await factory_session(force=True)
    elif kind == "transfer":
        await bank_transfer_session()


async def pm_run_rule(name: str, raw: str, manual: bool = False):
    r = pm_cfg.get("rules", {}).get(name)
    if not r:
        return
    now = time.time()
    if not manual and now - r.get("last", 0) < float(r.get("cooldown", 10)):
        return
    r["last"] = now
    r["hits"] = r.get("hits", 0) + 1
    print(f"🔔 اعلان «{name}» فعال شد → {', '.join(k if k not in ('send', 'cmd') else f'{k}:{a}' for k, a in r['actions'])}")
    await asyncio.sleep(float(r.get("delay", 2)))
    for kind, arg in r["actions"]:
        try:
            await pm_exec(kind, arg, r, name, raw)
        except Exception as e:
            print(f"🔔 اعلان «{name}» ({kind}): {e}")


@client.on(events.NewMessage(incoming=True))
async def pm_rules_watch(event):
    if not event.is_private or not pm_cfg.get("enabled", True) or not pm_cfg.get("rules"):
        return
    t = _clean(normalize(event.raw_text or ""))
    if not t:
        return
    hits = [n for n, r in pm_cfg["rules"].items() if r.get("enabled", True) and pm_rule_matches(r, t)]
    if not hits:
        return
    try:
        sender = await event.get_sender()
    except Exception:
        return
    for n in hits:
        if _pm_rule_bot_ok(pm_cfg["rules"][n], sender):
            asyncio.create_task(pm_run_rule(n, event.raw_text or ""))


@client.on(events.NewMessage(incoming=True))
async def pm_seen_watch(event):
    """آخرین پیام دیده‌شده‌ی هر ربات (برای «اعلان بررسی جدید»)"""
    if not event.is_private:
        return
    try:
        sender = await event.get_sender()
    except Exception:
        return
    if getattr(sender, "bot", False):
        seen = pm_cfg.setdefault("seen", {})
        seen[str(sender.id)] = max(seen.get(str(sender.id), 0), event.id)


def _parse_pm_actions(raw: str):
    acts = []
    for a in [x.strip() for x in raw.split(";") if x.strip()]:
        m = re.match(r"^(send|ارسال|cmd|دستور)\s*[:：]\s*(.+)$", a, re.S | re.I)
        if m:
            acts.append(("send" if m.group(1).lower() in ("send", "ارسال") else "cmd", m.group(2).strip()))
            continue
        k = _PM_ACTIONS.get(_nk(a))
        if not k:
            return None, a
        acts.append((k, ""))
    return acts, None


def _fmt_phr(lst):
    return " ، ".join(lst) if lst else "—"


def pm_list_text() -> str:
    pm_cfg.setdefault("builtin", {})
    out = [f"🔔 اعلان‌های پیوی ربات: {'روشن ✅' if pm_cfg.get('enabled', True) else 'خاموش ❌'} — ربات: {pm_cfg.get('bot') or 'any'}", "", "آماده:"]
    for k, lab in PM_LABELS.items():
        b = pm_cfg["builtin"].get(k, {})
        out.append(f"• {k} ({lab}): {'روشن ✅' if b.get('enabled', True) else 'خاموش ❌'} — عبارت: "
                   + (_fmt_phr(b.get("match")) if b.get("match") else "پیش‌فرض"))
    out.append("")
    out.append("دلخواه:")
    if not pm_cfg.get("rules"):
        out.append("— (هیچ‌کدوم)")
    for n, r in pm_cfg["rules"].items():
        acts = " ; ".join(k if k not in ("send", "cmd") else f"{k}: {a}" for k, a in r["actions"])
        out.append(f"• {n}: {'روشن ✅' if r.get('enabled', True) else 'خاموش ❌'} — شامل: {_fmt_phr(r.get('match'))}"
                   + (f" | همه: {_fmt_phr(r['all'])}" if r.get("all") else "")
                   + (f" | نباشد: {_fmt_phr(r['not'])}" if r.get("not") else "")
                   + (f" | ربات: {r['bot']}" if r.get("bot") else "") + f" ← {acts} | {r.get('hits', 0)} بار")
    out.append("\nراهنمای کامل: راهنما اعلان")
    return "\n".join(out)


_PM_USAGE = ("مثال:\nاعلان افزودن ماهی | عبارت: پخته شد، آماده شد | عمل: یخچال\n"
             "اعلان افزودن خبر | عبارت: تموم شد | عمل: اطلاع\n"
             "(pm add name | match: a, b | action: fridge ; notify)")


def pm_command(text: str, chat_id: int) -> str:
    pm_cfg.setdefault("builtin", {})
    pm_cfg.setdefault("rules", {})
    parts = text.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else "list"
    rest = parts[2].strip() if len(parts) > 2 else ""

    def toggle(cur: bool, want: bool, label: str):
        if cur == want:
            return f"{label} " + ("فعال می‌باشد ✅ (از قبل روشن بود)" if want else "غیرفعال می‌باشد ❌ (از قبل خاموش بود)"), False
        return f"{label} " + ("فعال شد ✅" if want else "غیرفعال شد ❌"), True

    if sub == "list":
        return pm_list_text()
    if sub in ("on", "off"):
        want = sub == "on"
        name = rest.split()[0] if rest else ""
        if name:
            key = _PM_BUILTIN_ALIAS.get(name.lower())
            if key:
                b = pm_cfg["builtin"].setdefault(key, {})
                msg, ch = toggle(b.get("enabled", True), want, f"اعلان «{key}»")
                b["enabled"] = want
                return msg
            r = pm_cfg["rules"].get(name)
            if r:
                msg, ch = toggle(r.get("enabled", True), want, f"اعلان «{name}»")
                r["enabled"] = want
                return msg
            return f"اعلانی به اسم «{name}» نیست. (اعلان لیست)"
        msg, ch = toggle(pm_cfg.get("enabled", True), want, "اعلان‌های پیوی ربات")
        pm_cfg["enabled"] = want
        return msg
    if sub == "bot":
        a_ = rest.split(None, 1)
        w = _PM_BOT_SUBS.get(_nk(a_[0])) if a_ else None
        arg = a_[1].strip() if len(a_) > 1 else ""
        if not rest.strip():
            return (f"ربات‌های انتخاب‌شده برای بررسی پیوی: {_pm_bot_cur_text()}\n\n"
                    "• اعلان ربات لیست — رباتای پیوی با شماره\n"
                    "• اعلان ربات افزودن 2 (شماره‌ی لیست) | اعلان ربات افزودن @یوزرنیم | اعلان ربات افزودن آیدی\n"
                    "• اعلان ربات حذف @یوزرنیم (یا شماره/آیدی/اسم)\n"
                    "• اعلان ربات تنظیم meowie, otherbot — جایگزین کل لیست | اعلان ربات پاک — هر ربات (any)")
        if w == "clear":
            pm_cfg["bot"] = "any"
            return "لیست رباتا پاک شد؛ حالا پیوی هر رباتی بررسی میشه (any) ✅"
        if w in ("add", "del"):
            tok = arg
            if tok.isdigit() and len(tok) <= 3:
                i = int(tok) - 1
                if not (0 <= i < len(pm_bot_cache)):
                    return "شماره‌ی لیست نامعتبره؛ اول بنویس: اعلان ربات لیست"
                tok = pm_bot_cache[i]
            if not tok:
                return "مثال: اعلان ربات افزودن 2   |   اعلان ربات افزودن @MeowieQIVBot   |   اعلان ربات حذف @MeowieQIVBot"
            cur = _pm_bot_tokens()
            norm = lambda x: _nk(x).lstrip("@")
            if w == "add":
                if any(norm(x) == norm(tok) for x in cur):
                    return f"«{tok}» از قبل توی لیسته ✅ (از قبل بود)\nلیست: {_pm_bot_cur_text()}"
                cur.append(tok)
                verb = "اضافه شد ✅"
            else:
                left = [x for x in cur if norm(x) != norm(tok)]
                if len(left) == len(cur):
                    return f"«{tok}» توی لیست نیست.\nلیست: {_pm_bot_cur_text()}"
                cur = left
                verb = "حذف شد 🗑"
            pm_cfg["bot"] = ", ".join(cur) or "any"
            return f"«{tok}» {verb}\nرباتای بررسی‌شونده: {_pm_bot_cur_text()}"
        if w == "set":
            rest = arg
        if not rest.strip():
            return "مثال: اعلان ربات تنظیم @MeowieQIVBot, @OtherBot"
        pm_cfg["bot"] = rest.strip()
        return (f"فقط پیوی رباتای «{pm_cfg['bot']}» بررسی میشه ✅" if rest.strip().lower() not in ("any", "all", "همه")
                else "پیوی هر رباتی بررسی میشه ✅ (any)")
    if sub in _PM_BUILTIN_ALIAS:
        key = _PM_BUILTIN_ALIAS[sub]
        b = pm_cfg["builtin"].setdefault(key, {})
        a = rest.split(None, 1)
        what = a[0].lower() if a else ""
        if what in ("on", "off"):
            msg, ch = toggle(b.get("enabled", True), what == "on", f"اعلان «{key}» ({PM_LABELS[key]})")
            b["enabled"] = what == "on"
            return msg
        if what == "match":
            arg = a[1].strip() if len(a) > 1 else ""
            if not arg or arg.lower() in ("reset", "ریست"):
                b.pop("match", None)
                return f"عبارت «{key}» به حالت پیش‌فرض برگشت ✅"
            b["match"] = [x.strip() for x in re.split(r"[،,|]", arg) if x.strip()]
            return f"«{key}» حالا وقتی پیام ربات شامل یکی از این عبارت‌ها باشه اجرا میشه: {_fmt_phr(b['match'])} ✅\n(برگشت: اعلان {sub} عبارت ریست)"
        return (f"اعلان «{key}» ({PM_LABELS[key]}): {'روشن ✅' if b.get('enabled', True) else 'خاموش ❌'}\n"
                f"عبارت: {_fmt_phr(b.get('match')) if b.get('match') else 'پیش‌فرض'}\n"
                f"تنظیم: اعلان {sub} روشن/خاموش | اعلان {sub} عبارت <عبارت۱، عبارت۲> | اعلان {sub} عبارت ریست")
    if sub == "test":
        if not rest:
            return "مثال: اعلان تست <متن پیام ربات>   (فقط نشون میده کدوم اعلان‌ها فعال میشن؛ کاری انجام نمیده)"
        t = _clean(normalize(rest))
        lines = ["🧪 نتیجه‌ی تست:"]
        for k in PM_LABELS:
            lines.append(f"• {k}: {'✅ فعال میشه' if pm_gate(k, t) else '— نه'}")
        for n, r in pm_cfg["rules"].items():
            lines.append(f"• {n}: {'✅ فعال میشه' if r.get('enabled', True) and pm_rule_matches(r, t) else '— نه'}")
        return "\n".join(lines)
    if sub == "run":
        name = rest.split()[0] if rest else ""
        if name not in pm_cfg["rules"]:
            return f"اعلانی به اسم «{name}» نیست. (اعلان لیست)"
        asyncio.create_task(pm_run_rule(name, "(اجرای دستی)", manual=True))
        return f"اعلان «{name}» دستی اجرا شد ⏳"
    if sub == "del":
        name = rest.split()[0] if rest else ""
        if pm_cfg["rules"].pop(name, None) is None:
            return f"اعلانی به اسم «{name}» نیست. (اعلان لیست)"
        return f"اعلان «{name}» حذف شد 🗑"
    if sub == "add":
        segs = [x.strip() for x in re.split(r"[|\n]", rest) if x.strip()]
        if not segs or " " in segs[0] or ":" in segs[0] or _PM_BUILTIN_ALIAS.get(segs[0].lower()):
            return "اسم اعلان باید یک کلمه و غیر از cook/miner/power/factory باشه.\n" + _PM_USAGE
        name, spec = segs[0], {}
        for sg in segs[1:]:
            if not re.search(r"[:：]", sg):
                return f"بخش نامعتبر: «{sg}»\n" + _PM_USAGE
            k, _, v = re.split(r"([:：])", sg, maxsplit=1)
            key = _PM_KEYS.get(_nk(k))
            if not key:
                return f"کلید نامعتبر: «{k.strip()}»\n" + _PM_USAGE
            spec[key] = v.strip()
        split = lambda x: [i.strip() for i in re.split(r"[،,]", x or "") if i.strip()]
        match, allw, notw = split(spec.get("match")), split(spec.get("all")), split(spec.get("not"))
        if not match and not allw:
            return "حداقل یکی از «عبارت:» یا «همه:» لازمه.\n" + _PM_USAGE
        acts, bad = _parse_pm_actions(spec.get("action", ""))
        if not acts:
            return ("عمل نامعتبر" + (f": «{bad}»" if bad else " یا خالی") +
                    "\nعمل‌ها: یخچال | ماهیگیری | فارم | ماینر | فروش | برق | کارخونه | انتقال | اطلاع | ارسال: متن | دستور: متن  (با ; جدا کن)")
        try:
            cd = float(spec.get("cooldown", "10").translate(_DIGITS))
            dl = float(spec.get("delay", "2").translate(_DIGITS))
        except ValueError:
            return "فاصله/تاخیر باید عدد (ثانیه) باشه."
        existed = name in pm_cfg["rules"]
        pm_cfg["rules"][name] = {"match": match, "all": allw, "not": notw, "actions": [list(a) for a in acts],
                                 "bot": spec.get("bot", "").strip(), "to": spec.get("to", ""), "chat": spec.get("chat", ""), "cooldown": cd, "delay": dl,
                                 "enabled": True, "hits": 0, "last": 0}
        return (f"اعلان «{name}» {'به‌روزرسانی' if existed else 'ثبت'} شد ✅\n"
                f"• وقتی پیوی ربات ({spec.get('bot') or pm_cfg.get('bot')}) شامل «{_fmt_phr(match)}»" + (f" و همه‌ی «{_fmt_phr(allw)}»" if allw else "") +
                (f" و نه «{_fmt_phr(notw)}»" if notw else "") + " باشه\n"
                f"• انجام میشه: {' ; '.join(k if k not in ('send', 'cmd') else f'{k}: {a}' for k, a in acts)}\n"
                f"• برای تست: اعلان تست <متن> | اعلان اجرا {name}")
    return pm_list_text()


# ---------- پیام «از قبل روشن/خاموش بود» ----------
_TOGGLE_NAMES = {
    "meow": "میو", "clock bio": "ساعت بیو", "clock name": "ساعت اسم", "clock bio reverse": "حالت معکوس بیو",
    "farm": "فارم میویی", "afk": "حالت افک", "miner": "ماینر میویی", "miner power": "شارژ خودکار برق ماینر",
    "miner sell": "فروش خودکار ارز", "bank": "بانک میویی", "bank withdraw": "برداشت خودکار از بانک",
    "bank transfer": "انتقال دوره‌ای بانک", "factory": "کارخونه میویی", "fish catch": "ماهی‌گیری خودکار",
    "fish catch hungry": "ماهی‌گیری فقط وقت گرسنگی", "fish catch recover": "جایگزینی ماهی تکراری",
    "fish catch recover sell": "فروش ماهی در حالت جایگزینی", "fish sell": "فروش ماهی", "fish cook": "پخت ماهی خام",
}


def _toggle_state(key: str, chat_id: int, is_group: bool):
    if key == "meow": return chat_id in active_meow_tasks
    if key == "clock bio": return bool(clock_bio_task)
    if key == "clock name": return bool(clock_name_task)
    if key == "clock bio reverse": return bool(bio_reverse)
    if key == "farm": return bool(farm_task)
    if key == "afk": return bool(afk_enabled)
    if key == "miner": return bool(miner_cfg["enabled"]) and (not is_group or miner_cfg["chat"] == chat_id)
    if key == "miner power": return bool(miner_cfg.get("power_enabled", True))
    if key == "miner sell": return bool(miner_cfg.get("sell_enabled", True))
    if key == "bank": return bool(bank_cfg["enabled"])
    if key == "bank withdraw": return bool(bank_cfg.get("wd_enabled", True))
    if key == "bank transfer": return bool(bank_cfg.get("tr_enabled"))
    if key == "factory": return bool(factory_enabled) and (not is_group or factory_chat_id == chat_id)
    if key == "fish catch": return bool(fish_cfg["auto"])
    if key == "fish catch hungry": return bool(fish_cfg.get("only_hungry"))
    if key == "fish catch recover": return bool(fish_cfg.get("recover", True))
    if key == "fish catch recover sell": return bool(fish_cfg.get("recover_sell"))
    if key == "fish sell": return bool(fish_sell_enabled)
    if key == "fish cook": return bool(fish_cook_enabled)
    return None


def already_msg(lower: str, chat_id: int, is_group: bool):
    """دستور روشن/خاموش تکراری → «از قبل فعال/غیرفعال می‌باشد»؛ وگرنه None"""
    m = re.fullmatch(r"(.+) (on|off)", lower.strip())
    if not m or m.group(1) not in _TOGGLE_NAMES:
        return None
    key, want = m.group(1), m.group(2) == "on"
    cur = _toggle_state(key, chat_id, is_group)
    if cur is None or cur != want:
        return None
    name = _TOGGLE_NAMES[key]
    return f"{name} فعال می‌باشد ✅ (از قبل روشن بود)" if want else f"{name} غیرفعال می‌باشد ❌ (از قبل خاموش بود)"


# ---------- دستورهای فارسی (به دستور انگلیسی معادل تبدیل میشن) ----------
_FA_ROOTS = {
    "میو": "meow", "ساعت": "clock", "افک": "afk", "فارم": "farm", "نجات": "rescue", "غذا": "feed",
    "ماهیگیری": "fish catch", "ماهی": "fish", "ماینر": "miner", "بانک": "bank", "کارخونه": "factory",
    "کارخانه": "factory", "لاگ": "log", "سکرت": "ttl", "پاکسازی": "clean", "کلمه": "say", "وظیفه": "job", "ایمنی": "safe", "اعلان": "pm", "تایید": "confirm", "تأیید": "confirm", "زندان": "jail",
}
_FA_WORDS = {
    "روشن": "on", "فعال": "on", "خاموش": "off", "غیرفعال": "off", "الان": "now", "وضعیت": "status",
    "فاصله": "interval", "هر": "every", "بالای": "above", "زیر": "below", "فروش": "sell", "پخت": "cook",
    "غذا": "feed", "شارژ": "charge", "برق": "power", "بیو": "bio", "اسم": "name", "نام": "name",
    "فونت": "font", "استایل": "style", "سبک": "style", "گروه": "chat", "کانال": "channel", "متن": "text",
    "اضافه": "add", "حذف": "del", "لیست": "list", "ریست": "reset", "کارت": "card", "انتقال": "transfer",
    "برداشت": "withdraw", "اضافی": "extra", "حاشیه": "margin", "درصد": "percent", "هزینه": "cost",
    "دسته": "category", "محصول": "item", "ساخت": "make", "تلاش": "tries", "معکوس": "reverse",
    "گرسنگی": "hungry", "یخچال": "fridge", "بازیابی": "recover", "قوانین": "rules", "پاک": "clear",
    "راست": "right", "وسط": "middle", "چپ": "left", "همه": "all", "ارز": "convert", "افزودن": "add",
    "ماینر": "miner", "کارخونه": "factory", "عبارت": "match", "ربات": "bot", "بررسی": "check", "تصادفی": "jitter", "جدید": "new", "قدیم": "old", "قدیمی": "old", "فقط": "dry", "نمایش": "dry", "تست": "test", "اجرا": "run", "ماهی": "fish",
}


def persian_to_en(text: str):
    """«ماینر روشن» → «miner on» ؛ اگه پیام دستور فارسیِ شناخته‌شده نبود None.
    پیام‌هایی که خودِ سلف به ربات می‌فرسته (ماهی / ماینر میو / بانک میویی / کارخونه میویی ...) دست نمی‌خورن"""
    t = (text or "").strip().replace("ماهی گیری", "ماهیگیری")
    _tk = t.split()
    if _tk and _tk[0] == "ترجمه":
        return "tr " + " ".join(_tk[1:]) if len(_tk) > 1 else "tr"
    if len(_tk) >= 2 and _tk[0] == "هوش":
        sub = _AI_FA_SUBS.get(_nk(_tk[1]))
        if sub and (len(_tk) == 2 or sub not in ("list", "clear")):
            rest_ = " ".join(_tk[2:])
            if sub == "memory":
                rest_ = {"روشن": "on", "فعال": "on", "خاموش": "off", "غیرفعال": "off"}.get(_tk[2] if len(_tk) > 2 else "", rest_)
            return f"ai {sub} {rest_}".strip()
        return "ai " + " ".join(_tk[1:])
    if t.replace("‌", " ") in ("حذف پیام ها", "حذف پیامها"):
        return "delete messages"
    toks = t.split()
    if len(toks) < 2 or toks[0] not in _FA_ROOTS:
        return None
    out = [_FA_ROOTS[toks[0]]]
    for i, tok in enumerate(toks[1:], 1):
        cur = " ".join(out)
        if cur in ("job add", "pm add") or cur in ("job del", "job on", "job off", "job now", "pm del", "pm on", "pm off", "pm test",
                                                     "pm bot", "pm run") or (cur.startswith("pm ") and cur.endswith(" match")):
            out.append(" ".join(toks[i:]))      # اسم/مشخصات وظیفه بدون ترجمه
            break
        if cur in ("confirm text add", "confirm text del"):
            out.append(" ".join(toks[i:]))      # متن آزاد صفحه‌ی تأیید
            break
        if tok in _FA_WORDS:
            out.append(_FA_WORDS[tok])
            continue
        if tok.isascii() or re.fullmatch(r"[\d,.+\-/:]+[kmhd]?", tok.translate(_DIGITS)):
            out.append(tok)
            continue
        if " ".join(out).startswith(("afk on", "feed text add", "feed text del", "say on")):
            out.append(" ".join(toks[i:]))      # متن آزاد (پیام افک / متن گرسنگی)
            break
        return None
    res = " ".join(out)
    return {"fish catch status": "fish catch", "clean all": "clean all"}.get(res, res)


# ---------- راهنمای جدا برای هر بخش ----------
_SIGN = "\n\n━━━━━━━━━━━━━━\nساخته شده توسط :\n@MRZverse"
HELP_DATA = {
"میو": ("🐱", "راهنمای میو", "ارسال خودکار «میو» توی چت برای گرفتن میو پوینت.", [
    ("⚙️ روشن / خاموش", [
        ("میو روشن", "meow on", "فعال‌سازی توی همین چت (هر ۱۰ دقیقه)"),
        ("میو روشن 5", "meow on 5", "هر ۵ دقیقه"),
        ("میو خاموش", "meow off", "خاموش‌کردن توی همین چت"),
    ]),
    ("⏱ تغییر فاصله", [
        ("میو +5  /  میو -5", "meow +5  /  meow -5", "پنج دقیقه به فاصله اضافه/کم میشه"),
    ]),
], ["اگه قابلیت از قبل روشن یا خاموش باشه، سلف می‌گه «از قبل روشن/خاموش بود»."]),

"ساعت": ("🕐", "راهنمای ساعت", "ساعت و تاریخ شمسی روی بیو یا نام.", [
    ("⚙️ روشن / خاموش", [
        ("ساعت بیو روشن / خاموش", "clock bio on / off", "ساعت و تاریخ روی بیو"),
        ("ساعت اسم روشن / خاموش", "clock name on / off", "ساعت روی نام خانوادگی"),
    ]),
    ("🎨 ظاهر", [
        ("ساعت بیو سبک 3", "clock bio style 3", "سبک ۱ تا ۵؛ بدون عدد = پیش‌نمایش"),
        ("ساعت بیو معکوس روشن / خاموش", "clock bio reverse on / off", "فقط اگه ساعت بیو برعکس دیده میشد"),
        ("ساعت فونت لیست", "clock font list", "نمایش فونت‌ها"),
        ("—", "clock bio font <n>  /  clock name font <n>", "فونت ساعت بیو / نام (مثلاً keycap)"),
    ]),
], []),

"افک": ("🌙", "راهنمای افک (پاسخ خودکار)", "وقتی نیستی، توی پیوی خودکار جواب میده.", [
    ("⚙️ دستورها", [
        ("افک روشن", "afk on", "با پیام پیش‌فرض"),
        ("افک روشن خوابم", "afk on <message>", "با پیام دلخواه"),
        ("افک خاموش", "afk off", "خاموش‌کردن"),
    ]),
], ["فقط توی پیوی، یک بار به هر نفر؛ به ربات‌ها جواب نمیده."]),

"فارم": ("🐾", "راهنمای فارم میویی", "هر دور «پیشی» می‌فرسته و میو پوینت‌ها رو برمی‌داره.", [
    ("⚙️ روشن / خاموش", [
        ("فارم روشن", "farm on", "اگه گروه تنظیم نشده، آیدی گروه رو می‌پرسه"),
        ("فارم خاموش", "farm off", ""),
        ("فارم الان", "farm now", "همین الان یک بار"),
    ]),
    ("⏱ فاصله", [
        ("فارم فاصله 30", "farm interval 30", "دقیقه (فارم فاصله 2h = ۲ ساعت)"),
        ("فارم +5  /  فارم -5", "farm +5  /  farm -5", "پنج دقیقه اضافه/کم"),
    ]),
], ["هر دور «پیشی» می‌فرسته و دکمه‌ی «برداشت میو پوینت ها» رو می‌زنه."]),

"نجات": ("🚨", "راهنمای نجات پیشی خیابونی", "کلیک خودکار روی دکمه‌ی «نجات پیشی خیابونی» توی همه‌ی گروه‌ها.", [
    ("⚙️ دستورها", [
        ("نجات روشن / خاموش", "rescue on / off", ""),
        ("نجات تلاش 10", "rescue tries 10", "حداکثر کلیک روی هر پیام (۱ تا ۵۰)"),
    ]),
], []),

"غذا": ("🍖", "راهنمای غذا دادن به پیشی", "گرسنگی رو چک می‌کنه و ماهی می‌خورونه.", [
    ("⚙️ روشن / خاموش", [
        ("غذا روشن / خاموش", "feed on / off", "غذادهی خودکار"),
        ("غذا الان", "feed now", "همین الان شکم رو چک کن"),
    ]),
    ("⏱ فاصله‌ی چک", [
        ("غذا فاصله 30", "feed interval 30", "هر ۳۰ دقیقه (2h = ساعت)"),
        ("غذا فاصله خاموش", "feed interval off", "چک بعد از هر فارم"),
    ]),
    ("💬 متن گرسنگی", [
        ("غذا متن اضافه من گشنمیووو", "feed text add <text>", "متن گرسنگی جدید"),
        ("غذا متن حذف <متن>", "feed text del <text>", ""),
        ("غذا متن لیست  /  غذا متن ریست", "feed text list  /  feed text reset", ""),
    ]),
    ("🔧 پیشرفته", [
        ("—", "feed 12/7", "قانون عددی (اختیاری)"),
        ("غذا قوانین  /  غذا ریست", "feed rules  /  feed reset", ""),
        ("—", "feed debug", "عیب‌یابی"),
    ]),
], ["فروش و پخت ماهی: «راهنما ماهیگیری»"]),

"ماهیگیری": ("🎣", "راهنمای ماهیگیری، پخت و فروش ماهی", "ماهی می‌گیره، می‌پزه، می‌خورونه یا می‌فروشه.", [
    ("🎣 ماهیگیری خودکار", [
        ("ماهیگیری وضعیت", "fish catch", "وضعیت فعلی"),
        ("ماهیگیری روشن / خاموش", "fish catch on / off", ""),
        ("ماهیگیری الان", "fish catch now", "همین الان"),
        ("ماهیگیری فاصله 50", "fish catch interval 50", "دقیقه"),
    ]),
    ("🧠 شرط‌های هوشمند", [
        ("ماهیگیری گرسنگی روشن / خاموش", "fish catch hungry on / off", "فقط وقتی پیشی گرسنه‌ست ماهی بگیر"),
        ("ماهیگیری یخچال 2", "fish catch fridge 2", "فقط وقتی ماهی‌های یخچال کمتر از ۲ تاست (0 = بدون محدودیت)"),
        ("ماهیگیری بازیابی روشن / خاموش", "fish catch recover on / off", "وقتی ربات گفت «این ماهی از قبل تو یخچال موجوده»: «یخچال میویی» ← ماهی هم‌سطح ← «بده پیشی بخوره» ← «بندازش تو یخچال»"),
        ("ماهیگیری بازیابی فروش روشن", "fish catch recover sell on", "اگه ماهی هم‌سطح خورده نشد، فروخته بشه"),
    ]),
    ("💰 فروش، غذا و پخت", [
        ("ماهی فروش بالای 50000", "fish sell above 50000", "ماهی پخته‌ی ≥ این عدد فروخته بشه"),
        ("ماهی فروش روشن / خاموش", "fish sell on / off", ""),
        ("ماهی غذا زیر 2000", "fish feed below 2000", "فقط ماهی پخته‌ی ارزان‌تر به پیشی داده بشه"),
        ("ماهی پخت روشن / خاموش", "fish cook on / off", "پخت ماهی‌های خام"),
        ("ماهی پخت بالای 5000", "fish cook above 5000", "فقط خام‌های ≥ این عدد"),
        ("ماهی وضعیت", "fish status", ""),
    ]),
], []),

"ماینر": ("⛏", "راهنمای ماینر میویی", "تبدیل توکن، فروش ارز و شارژ خودکار برق.", [
    ("⚙️ اصلی", [
        ("ماینر روشن", "miner on", "توی گروه میویی بنویس؛ هر ساعت تبدیل توکن"),
        ("ماینر خاموش  |  ماینر الان  |  ماینر وضعیت", "miner off  |  miner now  |  miner status", ""),
        ("ماینر فاصله 60", "miner interval 60", "دقیقه"),
        ("ماینر گروه -100...", "miner chat -100...", "گروه ماینر"),
    ]),
    ("💱 تبدیل و فروش ارز", [
        ("ماینر usdt  (ton / btc / eth / sol)", "miner usdt", "ارزِ تبدیل"),
        ("ماینر درصد 100", "miner percent 100", ""),
        ("ماینر فروش usdt 100", "miner sell usdt 100", "ارز و درصد فروش"),
        ("ماینر فروش روشن / خاموش / الان", "miner sell on / off / now", ""),
    ]),
    ("🔌 شارژ خودکار برق", [
        ("ماینر شارژ 1000000", "miner charge 1000000", "مبلغ شارژ (۱۰۰ هزار تا ۵۰ میلیون)"),
        ("ماینر برق روشن / خاموش / الان", "miner power on / off / now", "وقتی برق تموم شد خودکار شارژ کن"),
    ]),
], ["مراحل شارژ: «ماینر میو» ← مدیریت برق ← شارژ اعتبار برق ← مبلغ (ریپلای) ← تأیید ✓ ← BK ← روشن‌کردن ماینر"]),

"بانک": ("🏦", "راهنمای بانک میویی", "واریز، برداشت و انتقال خودکار.", [
    ("📥 واریز خودکار", [
        ("بانک بالای 3000000", "bank above 3000000", "بعد از میو، اگه میو پوینت هات بیشتر بود واریز به بانک"),
        ("بانک روشن / خاموش / وضعیت", "bank on / off / status", ""),
        ("بانک حاشیه 1000", "bank margin 1000", "چقدر کمتر از موجودی"),
        ("—", "bank chat -100...", "گروه بانک"),
    ]),
    ("📤 برداشت برای تولید کارخونه", [
        ("بانک برداشت 3100000", "bank withdraw 3100000", "وقتی میو پوینت برای تولید کم بود (مبلغ موردنیازِ ربات + اضافی)"),
        ("بانک برداشت روشن / خاموش", "bank withdraw on / off", ""),
        ("بانک اضافی 100000", "bank extra 100000", ""),
    ]),
    ("💸 انتقال دوره‌ای (کارت به کارت)", [
        ("بانک انتقال کارت 222967607573", "bank transfer card 222967607573", "کارت مقصد"),
        ("بانک انتقال هر 6h  (یا 1d)", "bank transfer every 6h", "فاصله"),
        ("بانک انتقال روشن / خاموش / الان", "bank transfer on / off / now", ""),
        ("—", "bank transfer margin 1000", ""),
    ]),
], ["انتقال: «بانک میویی» ← کارت به کارت میویی ← «مبلغ شماره‌کارت» (ریپلای) ← تأیید تراکنش ✓ ← BK؛ مبلغ = موجودی منهای کارمزد ۲٪"]),

"کارخونه": ("🏭", "راهنمای کارخونه میویی", "تولید، فروش و ساخت خودکار محصول.", [
    ("⚙️ اصلی", [
        ("کارخونه روشن", "factory on", "توی گروه میویی بنویس"),
        ("کارخونه خاموش  |  کارخونه الان  |  کارخونه وضعیت", "factory off  |  factory now  |  factory status", ""),
        ("—", "factory chat -100...", "گروه کارخونه"),
        ("کارخونه فاصله 60", "factory interval 60", "دقیقه (2h = ساعت)"),
    ]),
    ("💰 فروش و هزینه", [
        ("کارخونه فروش بالای 70", "factory sell above 70", "فروش وقتی قیمت بازار ≥ این عدد"),
        ("کارخونه فروش خاموش", "factory sell off", ""),
        ("کارخونه هزینه زیر 50", "factory cost below 50", "فقط وقتی هزینه‌ی ساخت هر عدد ≤ این عدد"),
        ("کارخونه هزینه خاموش", "factory cost off", ""),
    ]),
    ("🛠 انتخاب محصول", [
        ("کارخونه ساخت راست 6", "factory make right 6", "بر اساس جای دکمه (راست/وسط/چپ) و شماره‌ی دسته"),
        ("کارخونه ساخت اضافه چپ 5", "factory make add left 5", ""),
        ("کارخونه ساخت لیست  /  کارخونه ساخت پاک", "factory make list  /  factory make clear", ""),
        ("کارخونه درصد 100", "factory percent 100", ""),
        ("کارخونه دسته 6  |  کارخونه محصول 1", "factory category 6  |  factory item 1", ""),
    ]),
], ["دسته‌ها: 1 آبنبات | 2 کیک | 3 تکنولوژی | 4 خودرو | 5 هواپیما | 6 تکنولوژی پیشرفته",
    "اگه میو پوینت برای تولید کم بود: «بانک میویی» ← برداشت (مبلغ موردنیاز + کمی بیشتر) ← دوباره تولید"]),

"کلمه": ("🗣", "راهنمای کلمه‌ی تکراری", "هر چند وقت یه کلمه‌ی دلخواه می‌گه.", [
    ("⚙️ دستورها", [
        ("کلمه روشن 5m هاپ", "say on 5m هاپ", "هر ۵ دقیقه «هاپ» بگه"),
        ("کلمه روشن 30s میو", "say on 30s میو", "هر ۳۰ ثانیه"),
        ("کلمه روشن 3h مع", "say on 3h مع", "هر ۳ ساعت"),
        ("کلمه روشن 10m میو|مع|هاپ|پیو پیو", "say on 10m a|b|c", "هر بار یکی از کلمه‌ها (تصادفی)"),
        ("کلمه خاموش", "say off", "خاموش‌کردن توی همین چت"),
        ("کلمه لیست", "say list", "کلمه‌های فعال همه‌ی چت‌ها"),
    ]),
], ["زمان: s ثانیه | m دقیقه | h ساعت | d روز (بدون پسوند = دقیقه؛ حداقل ۵ ثانیه)"]),

"وظیفه": ("⏰", "راهنمای وظیفه", "ارسال دستور + کلیک خودکار روی دکمه (مثل ایونت‌ها).", [
    ("➕ ساخت", [
        ("وظیفه افزودن هالووین | هر: 3h | ارسال: هالووین میویی | مسیر: فروشگاه | هدف: گیفت | مهلت: 2m",
         "job add halloween | every: 3h | send: <cmd> | path: <btn> | target: <word> | window: 2m", ""),
    ]),
    ("🔑 کلیدها (فارسی / English)", [
        ("هر", "every", "فاصله‌ی اجرا"),
        ("ارسال", "send", "دستوری که فرستاده میشه"),
        ("مسیر", "path", "دکمه‌هایی که به‌ترتیب زده میشن؛ با > جدا کن (اختیاری)"),
        ("هدف", "target", "کلمه‌ی روی دکمه‌ی هدف؛ چند تا با ویرگول"),
        ("مهلت", "window", "چقدر تلاش کنه (پیش‌فرض 2m)"),
        ("سرعت", "poll", "فاصله‌ی تلاش‌ها به ثانیه (پیش‌فرض 1)"),
        ("گروه", "chat", "آیدی چت (پیش‌فرض همین چت)"),
        ("بازگشت", "back", "متن دکمه‌ی BK اگه خودکار شناخته نشد (اختیاری)"),
    ]),
    ("🕹 مدیریت", [
        ("وظیفه لیست", "job list", ""),
        ("وظیفه الان هالووین", "job now halloween", "اجرای فوری (برای تست)"),
        ("وظیفه روشن / خاموش هالووین", "job on / off halloween", ""),
        ("وظیفه حذف هالووین", "job del halloween", ""),
    ]),
], ["هر نوبت: دستور ارسال میشه ← دکمه‌های مسیر ← اگه هدف بود کلیک ← وگرنه تایمر خونده میشه، BK و دوباره مسیر تا ریست تایمر (تا پایان مهلت).",
    "ایمنی: قبل از هر کلیک پیام تازه خونده میشه؛ دکمه‌ی مسیر باید دقیق و غیرمبهم باشه؛ BK فقط وقتی مطمئن باشه زده میشه؛ وگرنه نوبت متوقف میشه و کلیک اشتباه نمی‌زنه."]),

"هوش": ("🤖", "راهنمای هوش مصنوعی", "ChatGPT، Claude، Gemini، Grok، DeepSeek و بقیه.", [
    ("💬 پرسیدن", [
        ("هوش سلام، یه جوک بگو", "ai <question>", "از هوش پیش‌فرض"),
        ("هوش claude سلام", "ai claude <question>", "از یه هوش مشخص (openai / claude / gemini / grok / deepseek / groq / mistral / openrouter / perplexity / together / ollama)"),
        ("روی یه پیام ریپلای کن + «هوش خلاصه کن»", "reply + ai <question>", "همون پیام هم به هوش داده میشه"),
    ]),
    ("🔧 تنظیمات", [
        ("هوش کلید openai sk-...", "ai key openai sk-...", "کلید API (بهتره توی Saved Messages بزنی؛ متغیر محیطی مثل OPENAI_API_KEY هم کار می‌کنه)"),
        ("هوش مدل openai gpt-4o", "ai model openai gpt-4o", "تغییر مدل"),
        ("هوش پیش‌فرض claude", "ai default claude", ""),
        ("هوش لیست", "ai list", "وضعیت همه (کدوم کلید داره)"),
        ("هوش افزودن mine https://host/v1 مدل [کلید]", "ai add mine https://host/v1 model [key]", "هر سرویس سازگار با OpenAI (حتی Ollama/LM Studio)"),
        ("هوش حذف mine", "ai del mine", ""),
        ("هوش سیستم <متن>", "ai system <text>", "شخصیت/دستور ثابت"),
    ]),
    ("🧠 حافظه", [
        ("هوش حافظه روشن / خاموش", "ai memory on / off", "۶ نوبت آخر هر چت"),
        ("هوش پاکسازی", "ai clear", "پاک‌کردن حافظه‌ی این چت"),
    ]),
], ["کلیدها توی فایل تنظیمات ذخیره میشن؛ فایل رو به کسی نده."]),

"ترجمه": ("🌐", "راهنمای مترجم", "Google Translate، بدون کلید، بیش از ۱۰۰ زبان.", [
    ("🔤 ترجمه", [
        ("ترجمه انگلیسی سلام چطوری؟", "tr en <text>", "به زبان مشخص‌شده"),
        ("ترجمه fa hello", "tr fa hello", "کد زبان هم میشه (en, de, ar, tr, ru, zh-CN, ja, ...)"),
        ("ترجمه سلام", "tr <text>", "بدون زبان: فارسی ← انگلیسی، بقیه ← فارسی"),
        ("روی یه پیام ریپلای کن + «ترجمه» یا «ترجمه آلمانی»", "reply + tr [lang]", "همون پیام ترجمه میشه"),
    ]),
], ["اسم زبان فارسی یا انگلیسی: فارسی، انگلیسی، عربی، ترکی، روسی، آلمانی، فرانسوی، اسپانیایی، ایتالیایی، چینی، ژاپنی، کره‌ای، هندی، اردو، کردی، آذری، ...",
    "اگه ترجمه‌ی گوگل جواب نداد و هوش مصنوعی تنظیم بود، از هوش استفاده میشه."]),

"حساب": ("🧮", "راهنمای ماشین‌حساب", "محاسبه‌ی امن، بدون کتابخونه‌ی اضافه.", [
    ("🔢 مثال‌ها", [
        ("حساب 2+3*4", "calc 2+3*4", ""),
        ("حساب (12.5 + 7) / 3 ^ 2", "calc (12.5 + 7) / 3 ^ 2", ""),
        ("حساب 15% + 100", "calc 15% + 100", "«15%» یعنی 0.15؛ «10 % 3» باقی‌مانده‌ست"),
        ("= 5*5", "= 5*5", "با علامت = هم میشه"),
        ("حساب ۱۲×۳", "calc 12×3", "عدد فارسی هم قبول میشه"),
    ]),
    ("📚 امکانات", [
        ("عملگرها", "", "+ - * / // % ^ (یا **) × ÷ √ ، پرانتز، 2(3+4)"),
        ("توابع", "", "sqrt sin cos tan asin acos atan log ln log10 log2 exp abs round floor ceil factorial gcd lcm min max sum pow sinh cosh tanh radians degrees"),
        ("ثابت‌ها", "", "pi e tau"),
    ]),
], ["«حساب …» فقط وقتی عبارت ریاضی باشه جواب میده؛ جمله‌هایی مثل «حساب بانکی» دست نمی‌خوره."]),

"اعلان": ("🔔", "راهنمای اعلان‌های پیوی ربات", "ربات توی پیوی خبر میده (ماهی پخته شد، تبدیل توکن تموم شد، برق تموم شد، تولید تموم شد)؛ سلف می‌خونه و کار مربوطه رو می‌کنه. همه‌ش قابل تنظیمه.", [
    ("🤖 انتخاب رباتا (چند ربات هم میشه؛ لازم نیست سورس رو عوض کنی)", [
        ("اعلان ربات لیست", "pm bot list", "رباتای پیوی با شماره (✅ = انتخاب‌شده)"),
        ("اعلان ربات افزودن 2", "pm bot add 2", "با شماره‌ی لیست"),
        ("اعلان ربات افزودن @MeowieQIVBot", "pm bot add @MeowieQIVBot", "با یوزرنیم یا آیدی عددی (pm bot add 123456789)"),
        ("اعلان ربات حذف @MeowieQIVBot", "pm bot del @MeowieQIVBot", "وقتی ربات پاک شد و یکی جدید ساختن؛ شماره/آیدی/اسم هم قبوله"),
        ("اعلان ربات تنظیم @a, @b", "pm bot set @a, @b", "جایگزین کل لیست"),
        ("اعلان ربات پاک", "pm bot clear", "هر ربات (any)"),
        ("اعلان ربات", "pm bot", "نمایش لیست فعلی"),
    ]),
    ("🔎 بررسی دستی پیوی", [
        ("اعلان بررسی جدید", "pm check new", "فقط پیام‌هایی که از آخرین بررسی/پیام دیده‌شده به بعد اومدن (مثلاً وقتی سلف خاموش بود)"),
        ("اعلان بررسی قدیم 10", "pm check old 10", "۱۰ پیام آخر هر ربات (پیش‌فرض ۵)"),
        ("اعلان بررسی جدید فقط", "pm check new dry", "فقط نشون بده چی اجرا میشه؛ چیزی اجرا و علامت‌گذاری نمیشه"),
    ]),
    ("✅ اعلان‌های آماده", [
        ("اعلان لیست", "pm list", "وضعیت همه (آماده و دلخواه)"),
        ("اعلان روشن / خاموش", "pm on / off", "کلِ بررسی پیوی"),
        ("اعلان پخت روشن / خاموش", "pm cook on / off", "ماهی پخته شد ← یخچال/غذا"),
        ("اعلان ماینر روشن / خاموش", "pm miner on / off", "پایان تبدیل ← فروش ارز"),
        ("اعلان برق روشن / خاموش", "pm power on / off", "اتمام برق ← شارژ"),
        ("اعلان کارخونه روشن / خاموش", "pm factory on / off", "پایان تولید ← بررسی انبار/فروش"),
        ("اعلان پخت عبارت پخته شد، آماده شد", "pm cook match cooked, ready", "به‌جای شرط پیش‌فرض (با ، جدا کن)"),
        ("اعلان پخت عبارت ریست", "pm cook match reset", "برگشت به پیش‌فرض"),
    ]),
    ("🧩 اعلان دلخواه", [
        ("اعلان افزودن ماهی | عبارت: پخته شد، آماده شد | عمل: یخچال", "pm add fishnote | match: cooked, ready | action: fridge", ""),
        ("اعلان افزودن خبر | عبارت: تموم شد | نباشد: خطا | ربات: @x | عمل: اطلاع ; ارسال: تموم شد", "pm add news | match: done | not: error | bot: @x | action: notify ; send: done", ""),
        ("اعلان تست <متن پیام>", "pm test <text>", "فقط نشون میده کدوم اعلان‌ها فعال میشن"),
        ("اعلان اجرا نام", "pm run <name>", "اجرای دستی"),
        ("اعلان روشن / خاموش نام", "pm on / off <name>", ""),
        ("اعلان حذف نام", "pm del <name>", ""),
    ]),
    ("🔑 کلیدهای اعلان دلخواه (فارسی / English)", [
        ("عبارت", "match", "هرکدوم از این عبارت‌ها"),
        ("همه", "all", "همه‌شون الزامی"),
        ("نباشد", "not", "اگه بود اجرا نشه"),
        ("ربات", "bot", "فقط پیام این ربات (اختیاری)"),
        ("عمل", "action", "با ; پشت‌هم"),
        ("به  /  چت", "to  /  chat", "مقصد «اطلاع» (پیش‌فرض Saved Messages)  /  مقصد «ارسال»"),
        ("فاصله  /  تاخیر", "cooldown  /  delay", "ثانیه بین دو اجرا (۱۰)  /  ثانیه قبل از اجرا (۲)"),
    ]),
    ("🎬 عمل‌ها", [
        ("یخچال | ماهیگیری | فارم | ماینر | فروش | برق | کارخونه | انتقال", "fridge | fish | farm | miner | sell | power | factory | transfer", ""),
        ("اطلاع", "notify", "متن پیام رو به Saved Messages یا «به:» می‌فرسته"),
        ("ارسال: متن  |  دستور: فارم الان", "send: <text>  |  cmd: farm now", "متن بفرست / هر دستور سلف رو اجرا کن"),
    ]),
], ["برای متن دقیق پیام ربات، یه بار پیام رو کپی کن و با «اعلان تست» امتحان کن."]),

"لاگ": ("📥", "راهنمای لاگ پیوی و ذخیره‌ی سکرت", "", [
    ("⚙️ دستورها", [
        ("لاگ کانال <آیدی|لینک>", "log channel <id|link>", "کانال مقصد رو تنظیم و روشن می‌کنه"),
        ("لاگ روشن / خاموش", "log on / off", ""),
        ("سکرت روشن / خاموش", "ttl on / off", "ذخیره‌ی عکس/ویدیوی نابودشونده توی Saved Messages"),
    ]),
], []),

"حذف": ("🗑", "راهنمای حذف پیام‌ها", "", [
    ("⚙️ دستورها", [
        ("پاکسازی همه", "clean all", "روی یکی از پیام‌های خودت ریپلای کن؛ همه‌ی پیام‌هات توی اون چت پاک میشه"),
        ("حذف پیام‌ها", "delete messages", "ربات می‌پرسه group یا private"),
    ]),
], []),

"ادمین": ("📡", "راهنمای دستورهای مدیر (ADMIN)", "", [
    ("⚙️ دستورها", [
        ("—", "ping", "زمان پاسخ"),
        ("—", "rmg <text>", "اکانت سلف همون متن رو توی همون چت می‌فرسته (دستورهای تنظیمی اجرا میشن)"),
        ("پاک  /  —", "delete", "روی پیام سلف ریپلای کن تا پاک بشه"),
    ]),
], ["دستورهای تنظیمی (فارسی یا انگلیسی) رو مدیر بدون rmg هم می‌تونه بفرسته؛ تأیید فارسی میاد و بعد ۳۰ ثانیه پاک میشه."]),
}


def _render_help(key: str) -> str:
    emoji, title, intro, groups, notes = HELP_DATA[key]
    out = [f"{emoji} {title}", "━━━━━━━━━━━━━━━", "🔹 فارسی   🔸 English"]
    if intro:
        out += ["", intro]
    for gt, items in groups:
        out += ["", f"▸ {gt}"]
        for fa, en, desc in items:
            if fa != "—":
                out.append(f"🔹 {fa}")
            if en:
                out.append(f"{'    ' if fa != '—' else ''}🔸 {en}")
            if desc:
                out.append(f"    ↳ {desc}")
    for n in notes:
        out += ["", f"💡 {n}"]
    return "\n".join(out) + _SIGN


HELP_DATA["ایمنی"] = ("🛡", "راهنمای ایمنی (ضد زندان)", "میویی اگه دستورها پشت‌سرهم بیان، سلف رو زندانی می‌کنه؛ این بخش همه‌ی دستورهای خودکار رو صف می‌کنه و بینشون فاصله می‌ندازه.", [
    ("⚙️ دستورها", [
        ("ایمنی وضعیت", "safe status", "وضعیت و فاصله‌ی فعلی"),
        ("ایمنی فاصله 8", "safe gap 8", "حداقل فاصله‌ی دو دستور بازی (ثانیه؛ پیش‌فرض 6)"),
        ("ایمنی تصادفی 3", "safe jitter 3", "تا چند ثانیه‌ی تصادفیِ اضافه (پیش‌فرض 2) تا رفتار شبیه آدم باشه"),
        ("ایمنی روشن / خاموش", "safe on / off", "خاموش‌کردنش خطر زندان داره"),
    ]),
], ["دستورهای صف‌شونده: پیشی، میو، ماهی، یخچال میویی، ماینر میو، بانک میویی، کارخونه میویی و دستور وظیفه‌ها.",
    "جواب‌دادن به پنل‌ها (ریپلای مبلغ) و کلیک روی دکمه‌ها صف نمیشن."])
HELP_DATA["تایید"] = ("✅", "راهنمای تأیید خودکار", "ربات میویی صفحه‌ی «مطمئنی؟» می‌فرسته و سلف دکمه‌ی سبز ✓ رو می‌زنه؛ اگه متن صفحه‌ی تأیید عوض شد، عبارتش رو اینجا یاد بده.", [
    ("⚙️ دستورها", [
        ("تایید متن افزودن با تایید", "confirm text add <text>", "هر پیام ربات (با دکمه) که این عبارت رو داشته باشه صفحه‌ی تأیید حساب میشه و ✓ زده میشه"),
        ("تایید متن حذف <متن یا شماره>", "confirm text del <text|n>", ""),
        ("تایید متن لیست  /  تایید متن ریست", "confirm text list  /  confirm text reset", ""),
        ("روی پیام ربات ریپلای + تایید بررسی", "reply + confirm check", "نشون میده شناخته میشه یا نه و عبارت پیشنهادی میده"),
    ]),
], ["بعد از تأیید پخت (و وقتی جواب «یخچال میویی» صفحه‌ی پخت باشه) اگه دکمه‌ی BK بود سلف می‌زنه تا برگرده صفحه‌ی اصلی یخچال."])
HELP_DATA["زندان"] = ("🚔", "راهنمای آزادسازی از زندان میویی", "ربات اگه دستورها پشت‌هم بیان سلف رو زندانی می‌کنه و تا آزاد نشه هیچ کاری نمیشه کرد؛ سلف خودش آزاد میشه.", [
    ("⚙️ دستورها", [
        ("زندان روشن / خاموش", "jail on / off", "آزادسازی خودکار (پیش‌فرض روشن)"),
        ("زندان الان", "jail now", "همین الان آزاد کن"),
        ("زندان وضعیت", "jail status", ""),
    ]),
], ["مراحل: «زندان میویی» ← پرداخت جریمه ← پرداخت معمولی (یا کارت بانکی) ← تأیید ✓. فقط وقتی فعال میشه که پیام زندان جواب پیام خودِ سلف باشه (یا توی پیوی بیاد).",
    "اگه میو پوینت هات از جریمه کمتر باشه «پرداخت با کارت بانکی» (از موجودی بانک) زده میشه؛ اگه اونم کم بود فقط توی لاگ می‌نویسه. برای کمتر زندانی شدن: safe gap 10"])
if HOSTED:
    HELP_DATA.pop("لاگ", None)
HELP_SECTIONS = {k: _render_help(k) for k in HELP_DATA}

_HELP_ALIAS = {"jail": "زندان", "confirm": "تایید", "تأیید": "تایید", "meow": "میو", "clock": "ساعت", "afk": "افک", "farm": "فارم", "rescue": "نجات", "feed": "غذا",
               "say": "کلمه", "job": "وظیفه", "pm": "اعلان", "notify": "اعلان", "safe": "ایمنی", "jail": "ایمنی", "زندان": "ایمنی", "ai": "هوش", "tr": "ترجمه", "translate": "ترجمه", "calc": "حساب", "ماشینحساب": "حساب", "calculator": "حساب", "محاسبه": "حساب", "fish": "ماهیگیری", "ماهی": "ماهیگیری", "fishing": "ماهیگیری", "miner": "ماینر", "bank": "بانک",
               "factory": "کارخونه", "کارخانه": "کارخونه", "log": "لاگ", "ttl": "لاگ", "سکرت": "لاگ",
               "clean": "حذف", "delete": "حذف", "پاکسازی": "حذف", "admin": "ادمین", "مدیر": "ادمین"}
HELP_MENU = ("📖 راهنمای سلف‌بات ✨\n━━━━━━━━━━━━━━━\n"
             "برای دیدن هر بخش بنویس:\n🔹 راهنما <نام بخش>\n🔸 help <section>\n\n"
             "🐱 میو · meow\n🐾 فارم · farm\n🚨 نجات · rescue\n🍖 غذا · feed\n🎣 ماهیگیری · fish\n"
             "⛏ ماینر · miner\n🏦 بانک · bank\n🏭 کارخونه · factory\n🕐 ساعت · clock\n🌙 افک · afk\n"
             "🗣 کلمه · say\n⏰ وظیفه · job\n🤖 هوش · ai\n🌐 ترجمه · tr\n🧮 حساب · calc\n"
             "🔔 اعلان · pm\n✅ تایید · confirm\n🚔 زندان · jail\n🛡 ایمنی · safe\n📥 لاگ · log\n🗑 حذف · clean\n📡 ادمین · admin\n\n"
             "مثال: «راهنما ماینر» یا «help miner»\n"
             "💡 هر دستور هم فارسی کار می‌کنه هم انگلیسی؛ راهنمای کاملِ قبلی: guide" + _SIGN)


def help_section(text: str):
    """«راهنما ماینر» / «help miner» → متن همون بخش؛ «راهنما» → فهرست؛ بقیه None"""
    parts = (text or "").strip().split(None, 1)
    if not parts or parts[0].lower() not in ("راهنما", "help"):
        return None
    if len(parts) == 1:
        return HELP_MENU if parts[0] == "راهنما" else None
    key = re.sub(r"[\s‌]+", "", parts[1].lower())
    key = _HELP_ALIAS.get(key, key)
    return HELP_SECTIONS.get(key) or (HELP_MENU + "\n\n⚠️ این بخش پیدا نشد.")


async def send_help(event, edit, text=None):
    """متن راهنما از حد ۴۰۹۶ کاراکتر تلگرام بلندتره؛ تکه‌تکه می‌فرستیم"""
    chunks, cur = [], ""
    for block in (text or COMMANDS_TEXT).split("\n\n"):
        if len(cur) + len(block) + 2 > 3500 and cur:
            chunks.append(cur); cur = block
        else:
            cur = cur + "\n\n" + block if cur else block
    if cur:
        chunks.append(cur)
    if edit:
        await event.edit(chunks[0], parse_mode=None)       # بدون فرمت‌دهی تا * و _ و < > توی راهنما خراب نشن
    else:
        await event.reply(chunks[0], parse_mode=None)
    for c in chunks[1:]:
        await client.send_message(event.chat_id, c, parse_mode=None)




# ---------- هندلر اصلی (پیام‌های خودِ اکانت) ----------
_seen_cmd: set = set()


@client.on(events.NewMessage(outgoing=True))
async def handler(event):
    global awaiting_farm_group, farm_chat_id, farm_interval_seconds, meow_default_seconds
    global delete_flow_stage, bio_digit_font, name_digit_font, rescue_enabled, feed_enabled, bio_style, bio_reverse, ttl_save_enabled, feed_interval_seconds, rescue_max_tries, dm_log_enabled, dm_log_target, fish_sell_min, fish_sell_enabled, fish_feed_max, fish_cook_min, fish_cook_enabled, feed_setup_stage, feed_setup_cap, factory_enabled, factory_chat_id

    # هر پیام فقط یک بار پردازش میشه (هم رویداد واقعی تلگرام هم اجرای مستقیمِ دستورِ مدیر)
    _key = (event.chat_id, event.id)
    if _key in _seen_cmd:
        return
    if len(_seen_cmd) > 500:
        _seen_cmd.clear()
    _seen_cmd.add(_key)

    if HOSTED and (event.raw_text or "").strip().lower() in ("منو", "منو سلف", "menu"):
        await open_glass_menu(event)
        return

    text = normalize(event.raw_text)
    _en = persian_to_en(text)          # دستور فارسی → معادل انگلیسی
    if _en:
        text = _en
    lower = text.lower()
    chat_id = event.chat_id
    if HOSTED and lower.split()[:1] in (["log"], ["ttl"]) and lower.split()[1:2] in (["on"], ["off"], ["channel"]):
        await event.edit("این قابلیت توی نسخه‌ی ساخته‌شده با ربات وجود نداره ❌", parse_mode=None)
        return

    # --- فلوی گفتگویی «delete messages» ---
    if delete_flow_stage == "await_type":
        delete_flow_stage = None
        if lower in ("group", "گروه"):
            delete_flow_stage = "await_group"
            await event.edit("آیدی عددی یا لینک گروه رو بفرست.")
        elif lower in ("private", "pv", "پیوی"):
            delete_flow_stage = "await_private"
            await event.edit("آیدی عددی یا یوزرنیم اون شخص رو بفرست.")
        else:
            delete_flow_stage = "await_type"
            await event.edit("لطفاً بنویس: group  یا  private")
        return

    if delete_flow_stage == "await_group":
        delete_flow_stage = None
        try:
            target_chat_id = await resolve_chat_id(text)
            count = await delete_all_my_messages(target_chat_id)
            await event.edit(f"{count} پیام تو اون گروه پاک شد ✅")
        except Exception:
            await event.edit("گروه پیدا نشد. دوباره بنویس: delete messages")
        return

    if delete_flow_stage == "await_private":
        delete_flow_stage = None
        try:
            target_chat_id = await resolve_chat_id(text)
            count = await delete_all_my_messages(target_chat_id)
            await event.edit(f"{count} پیام تو اون چت پاک شد ✅")
        except Exception:
            await event.edit("کاربر پیدا نشد. دوباره بنویس: delete messages")
        return

    if lower == "delete messages":
        delete_flow_stage = "await_type"
        await event.edit("پیام‌ها از کجا پاک بشن؟ بنویس: group  یا  private")
        return

    # --- گام دوم فلوی «farm on»: منتظر آیدی گروه بودیم ---
    if awaiting_farm_group:
        awaiting_farm_group = False
        parts = text.split()
        target = parts[0] if parts else ""
        minutes = None
        if len(parts) >= 2:
            try:
                minutes = float(parts[1])
            except ValueError:
                minutes = None
        if target.lstrip("-").isdigit():
            farm_chat_id = int(target)
            if minutes:
                farm_interval_seconds = minutes * 60
            msg = await toggle_farm()
            await event.edit(f"گروه فارم تنظیم شد ✅ (chat_id: {farm_chat_id})\n{msg}")
        else:
            await event.edit("آیدی نامعتبر بود. دوباره بنویس: farm on")
        return

    # --- فلوی گفتگویی «feed»: ظرفیت شکم، بعد حد غذادهی ---
    if feed_setup_stage:
        digits = text.translate(_DIGITS).strip()
        if digits.isdigit() and int(digits) > 0:
            n = int(digits)
            if feed_setup_stage == "cap":
                feed_setup_cap = n
                feed_setup_stage = "need"
                await event.edit("🍖 ظرفیتی که می‌خواید با دیدن اون به گربه غذا داده بشه وارد کنید\n(وقتی عدد دوم شکم این عدد یا کمتر شد غذا بده؛ مثلاً 8)")
            else:
                cap_v = feed_setup_cap
                feed_setup_stage = None
                feed_rules[cap_v] = n
                await event.edit(f"✅ فعال سازی غذادهی به پیشی با ظرفیت {cap_v} و {n} با موفقیت انجام شد\nوقتی شکم گربه {cap_v}/{n} یا کمتر شد، سلف بهش غذا میده.")
            return
        feed_setup_stage = None

    _sec = help_section(text)
    if _sec is not None:
        await send_help(event, True, _sec)
        return

    _t0 = lower.split(None, 1)[0] if lower.split() else ""
    if _t0 == "safe" and len(lower.split()) > 1:
        await event.edit(safe_command(text), parse_mode=None)
        return
    if _t0 == "jail" and lower.split()[1:2] in (["on"], ["off"], ["now"], ["status"]):
        await event.edit(jail_command(lower, chat_id, bool(event.is_group)), parse_mode=None)
        return
    if _t0 == "confirm" and lower.split()[1:2] in (["text"], ["check"]):
        if lower.split()[1] == "check":
            await confirm_check(event)
        else:
            await event.edit(confirm_command(text), parse_mode=None)
        return
    if _t0 == "ai" and len(lower.split()) > 1:
        try:
            await ai_handle(event, text)
        except Exception as e:
            print(f"ai: {e}")
        return
    if _t0 in ("tr", "translate"):
        if len(lower.split()) > 1 or await event.get_reply_message():
            try:
                await translate_handle(event, text)
            except Exception as e:
                print(f"translate: {e}")
            return
    _ce = calc_extract(text)
    if _ce is not None:
        if await calc_handle(event, _ce[0], _ce[1]):
            return

    if _t0 == "pm" and len(lower.split()) > 1 and (lower.split()[1] == "check" or lower.split()[1:3] in (["bot", "list"], ["bot", "لیست"])):
        _a = lower.translate(_DIGITS).split()
        if _a[1] == "check":
            _mode = "new" if "new" in _a[2:] else "old"
            _n = max(1, min(next((int(x) for x in _a[2:] if x.isdigit()), 50 if _mode == "new" else 5), 100))
            await event.edit("🔎 در حال بررسی پیوی ربات ...", parse_mode=None)
            try:
                await event.edit(await pm_check(_n, "dry" in _a[2:], _mode), parse_mode=None)
            except Exception as e:
                await event.edit(f"❌ بررسی پیوی: {str(e)[:300]}", parse_mode=None)
        else:
            try:
                await event.edit(await pm_bots_text(), parse_mode=None)
            except Exception as e:
                await event.edit(f"❌ {str(e)[:300]}", parse_mode=None)
        return
    if _t0 == "pm" and len(lower.split()) > 1 and lower.split()[1] in (
            "list", "on", "off", "bot", "add", "del", "test", "run", "cook", "cooked", "fish", "miner", "power", "factory"):
        await event.edit(pm_command(text, chat_id), parse_mode=None)
        return

    _w = lower.split()
    if len(_w) > 1 and _w[0] == "say" and _w[1] in ("on", "off", "list", "status"):
        await event.edit(say_command(text, chat_id))
        return
    if len(_w) > 1 and _w[0] == "job" and _w[1] in ("add", "del", "list", "on", "off", "now", "status"):
        await event.edit(job_command(text, chat_id))
        return

    _al = already_msg(lower, chat_id, bool(event.is_group or event.is_channel))
    if _al:
        await event.edit(_al)
        return

    if lower.startswith("meow on"):
        rest = text[len("meow on"):].strip()
        minutes = None
        if rest:
            try:
                minutes = float(rest)
            except ValueError:
                await event.edit("عدد دقیقه نامعتبره. مثال: meow on 5")
                return
        await event.edit(await toggle_meow(chat_id, minutes))

    elif lower == "meow off":
        await event.edit(await untoggle_meow(chat_id))

    elif lower == "clock bio on":
        await event.edit(await toggle_clock_bio())

    elif lower == "clock bio style" or lower.startswith("clock bio style "):
        arg = lower[len("clock bio style"):].strip()
        if arg in BIO_STYLES:
            bio_style = arg
            await event.edit(f"استایل بیو {arg} فعال شد ✅ پیش‌نمایش:\n{fancy_bio_text()}")
        else:
            prev = "\n".join(
                f"{k}: {v.format(d='1405/07/26', t=fancy_time(bio_digit_font), gd=_local_now().day, gm=_local_now().strftime('%b'))}" for k, v in BIO_STYLES.items()
            )
            await event.edit(f"استایل‌های بیو (دستور: clock bio style <عدد>):\n{prev}\n\nفعلی: {bio_style}")

    elif lower == "clock bio reverse on":
        bio_reverse = True
        await event.edit(f"حالت معکوس بیو فعال شد ✅ (فقط اگه ساعت بیو هنوز برعکس نمایش داده میشه)\npreview: {fancy_bio_text()}")

    elif lower == "clock bio reverse off":
        bio_reverse = False
        await event.edit("حالت معکوس بیو غیرفعال شد ❌ (حالت عادی)")

    elif lower == "clock bio off":
        await event.edit(await untoggle_clock_bio())

    elif lower == "clock name on":
        await event.edit(await toggle_clock_name())

    elif lower == "clock name off":
        await event.edit(await untoggle_clock_name())

    elif lower in ("clock font list", "clock bio font list", "clock name font list"):
        preview = "\n".join(
            f"{name}: {fancy_digits_preview(name)}" for name in DIGIT_FONTS
        )
        await event.edit(
            f"Available clock fonts:\n{preview}\n\nBio font: {bio_digit_font}\nName font: {name_digit_font}\n\n"
            "Set bio font:  clock bio font <name>\nSet name font: clock name font <name>"
        )

    elif lower.startswith("clock bio font"):
        name = lower[len("clock bio font"):].strip()
        if name in DIGIT_FONTS:
            bio_digit_font = name
            await event.edit(f"فونت ساعت بیو روی '{name}' تنظیم شد ✅ پیش‌نمایش: {fancy_time(name)}")
        else:
            await event.edit("فونت نامعتبره. لیست: clock font list")

    elif lower.startswith("clock name font"):
        name = lower[len("clock name font"):].strip()
        if name in DIGIT_FONTS:
            name_digit_font = name
            await event.edit(f"فونت ساعت اسم روی '{name}' تنظیم شد ✅ پیش‌نمایش: {fancy_time(name)}")
        else:
            await event.edit("Unknown font. See the list with: clock font list")

    elif lower.startswith("afk on"):
        custom = text[len("afk on"):].strip()
        await event.edit(await toggle_afk(custom))

    elif lower == "afk off":
        await event.edit(await untoggle_afk())

    elif lower == "farm on":
        if farm_chat_id is not None:
            await event.edit(await toggle_farm())
        else:
            awaiting_farm_group = True
            await event.edit(
                "آیدی عددی گروهی که می‌خوای فارم توش انجام بشه رو بفرست.\n"
                "مثال: -1001234567890\n"
                "اگه فاصله‌ی زمانی دلخواه (دقیقه) هم می‌خوای، بعدش با یه فاصله بنویس: "
                "-1001234567890 60"
            )

    elif lower == "farm off":
        await event.edit(await untoggle_farm())

    elif lower.startswith("log channel"):
        arg = text[len("log channel"):].strip()
        if not arg:
            await event.edit("مثال: log channel -1001234567890   (یا لینک / @یوزرنیم کانال)")
        else:
            try:
                dm_log_target = await resolve_entity(arg)
                dm_log_enabled = True
                await event.edit("ذخیره‌ی پیوی‌ها فعال شد ✅ — پیام‌های جدید پیوی توی اون کانال ذخیره میشن.")
            except Exception:
                await event.edit("کانال پیدا نشد. مطمئن شو این اکانت عضو/ادمین کانال هست.")

    elif lower == "log on":
        if dm_log_target is None:
            await event.edit("اول کانال رو تنظیم کن: log channel <آیدی یا لینک>")
        elif dm_log_enabled:
            await event.edit("ذخیره‌ی پیوی فعال می‌باشد ✅ (از قبل روشن بود)")
        else:
            dm_log_enabled = True
            await event.edit("ذخیره‌ی پیوی فعال شد ✅")

    elif lower == "log off":
        if not dm_log_enabled:
            await event.edit("ذخیره‌ی پیوی غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
        else:
            dm_log_enabled = False
            await event.edit("ذخیره‌ی پیوی غیرفعال شد ❌")

    elif lower == "ttl on":
        if ttl_save_enabled:
            await event.edit("ذخیره‌ی فایل نابودشونده فعال می‌باشد ✅ (از قبل روشن بود)")
        else:
            ttl_save_enabled = True
            await event.edit("ذخیره‌ی فایل نابودشونده فعال شد ✅")

    elif lower == "ttl off":
        if not ttl_save_enabled:
            await event.edit("ذخیره‌ی فایل نابودشونده غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
        else:
            ttl_save_enabled = False
            await event.edit("ذخیره‌ی فایل نابودشونده غیرفعال شد ❌")

    elif lower.startswith("miner") or lower.startswith("main "):
        sub = lower.split(None, 1)[1] if " " in lower else ""
        if sub == "on":
            if event.is_group or event.is_channel:
                miner_cfg["chat"] = chat_id
            elif _miner_chat() is None:
                await event.edit("این دستور رو توی گروهی که میویی توشه بنویس (یا اول farm on رو تنظیم کن)")
                return
            miner_cfg["enabled"] = True
            start_miner_loop()
            await event.edit(f"ماینر میویی فعال شد ✅ — هر {miner_cfg['interval'] / 60:g} دقیقه بررسی میشه")
        elif sub == "off":
            miner_cfg["enabled"] = False
            stop_miner_loop()
            await event.edit("ماینر میویی غیرفعال شد ❌")
        elif sub == "now":
            if miner_cfg["chat"] is None and (event.is_group or event.is_channel):
                miner_cfg["chat"] = chat_id
            await event.edit("تبدیل توکن شروع شد ⏳")
            asyncio.create_task(miner_session())
        elif sub == "sell now":
            if miner_cfg["chat"] is None and (event.is_group or event.is_channel):
                miner_cfg["chat"] = chat_id
            await event.edit("فروش ارز شروع شد ⏳")
            asyncio.create_task(miner_sell_session())
        elif sub in ("status", ""):
            await event.edit(miner_status_text())
        else:
            res = apply_miner_command(sub)
            await event.edit(res or "دستور نامعتبر. نمونه‌ها: miner on | miner usdt | miner percent 100 | miner sell usdt 100 | miner sell off | miner status")

    elif lower.startswith("bank"):
        sub = lower[len("bank"):].strip()
        if sub == "on" and (event.is_group or event.is_channel) and bank_cfg["chat"] is None:
            bank_cfg["chat"] = chat_id
        res = apply_bank_command(sub) if sub else bank_status_text()
        await event.edit(res or "دستور نامعتبر. نمونه‌ها: bank above 3000000 | bank margin 1000 | bank chat -100123 | bank on | bank off | bank status")

    elif lower.startswith("factory"):
        sub = lower[len("factory"):].strip()
        if sub == "on":
            if event.is_group or event.is_channel:
                factory_chat_id = chat_id
            elif farm_chat_id is None and factory_chat_id is None:
                await event.edit("این دستور رو توی گروهی که میویی توشه بنویس (یا اول farm on رو تنظیم کن)")
                return
            factory_enabled = True
            start_factory_loop()
            await event.edit(f"کارخونه میویی فعال شد ✅ — هر {factory_interval_seconds/60:g} دقیقه بررسی میشه")
            asyncio.create_task(factory_session())
        elif sub == "off":
            factory_enabled = False
            stop_factory_loop()
            await event.edit("کارخونه میویی غیرفعال شد ❌")
        elif sub == "now":
            if factory_chat_id is None and (event.is_group or event.is_channel):
                factory_chat_id = chat_id
            await event.edit("بررسی کارخونه شروع شد ⏳")
            asyncio.create_task(factory_session(force=True))
        elif sub == "status":
            await event.edit(factory_status_text())
        else:
            res = apply_factory_command(sub)
            await event.edit(res or "دستور نامعتبر. نمونه‌ها: factory on | factory sell above 70 | factory cost below 50 | factory category 6 | factory item 1 | factory percent 100")

    elif lower == "fish catch":
        nxt = fish_state["next"] - time.time()
        await event.edit(f"🎣 ماهی‌گیری خودکار: {'روشن ✅' if fish_cfg['auto'] else 'خاموش ❌'} (هر {fish_cfg['interval'] / 60:g} دقیقه)\n"
                         f"نوبت بعدی: {'الان' if nxt <= 0 else f'{int(nxt / 60) + 1} دقیقه‌ی دیگه'}\n"
                         f"• فقط وقت گرسنگی: {'روشن ✅' if fish_cfg.get('only_hungry') else 'خاموش ❌'}\n"
                         f"• حد ماهی‌های یخچال: {fish_cfg.get('fridge_max', 2) or 'بدون محدودیت'}\n"
                         f"• ماهی تکراری (جایگزینی): {'روشن ✅' if fish_cfg.get('recover', True) else 'خاموش ❌'} | فروش به‌جای خوراندن: {'روشن' if fish_cfg.get('recover_sell') else 'خاموش'}\n"
                         "دستورها: fish catch on | off | now | interval 50 | hungry on/off | fridge 2 | recover on/off | recover sell on/off")

    elif lower in ("fish catch hungry on", "fish catch hungry off"):
        fish_cfg["only_hungry"] = lower.endswith("on")
        await event.edit("ماهی‌گیری فقط وقتی پیشی گرسنه‌ست فعال شد ✅" if fish_cfg["only_hungry"] else "ماهی‌گیری فقط وقت گرسنگی غیرفعال شد ❌ (طبق زمان‌بندی)")

    elif lower.startswith("fish catch fridge"):
        n = parse_int(text[len("fish catch fridge"):].strip())
        if n is None or n > 20:
            await event.edit("مثال: fish catch fridge 2   (فقط وقتی ماهی‌های یخچال کمتر از ۲ تا بود ماهی بگیر؛ 0 = بدون محدودیت)")
        else:
            fish_cfg["fridge_max"] = n
            await event.edit(f"فقط وقتی ماهی‌های یخچال کمتر از {n} تا باشه ماهی می‌گیره ✅" if n else "محدودیت تعداد ماهی یخچال برداشته شد ✅")

    elif lower in ("fish catch recover on", "fish catch recover off"):
        fish_cfg["recover"] = lower.endswith("on")
        await event.edit("جایگزینی ماهی تکراری فعال شد ✅" if fish_cfg["recover"] else "جایگزینی ماهی تکراری غیرفعال شد ❌")

    elif lower in ("fish catch recover sell on", "fish catch recover sell off"):
        fish_cfg["recover_sell"] = lower.endswith("on")
        await event.edit("اگه ماهی هم‌سطح خورانده نشد، فروخته میشه ✅" if fish_cfg["recover_sell"] else "فروش ماهی هم‌سطح در حالت جایگزینی غیرفعال شد ❌")

    elif lower in ("fish catch on", "fish catch off"):
        fish_cfg["auto"] = lower.endswith("on")
        if fish_cfg["auto"]:
            start_fish_loop()
            await event.edit(f"ماهی‌گیری خودکار فعال شد ✅ (هر {fish_cfg['interval'] / 60:g} دقیقه؛ اگه کرم داشته باشی ازش استفاده میشه)")
        else:
            stop_fish_loop()
            await event.edit("ماهی‌گیری خودکار غیرفعال شد ❌")

    elif lower == "fish catch now":
        if farm_chat_id is None:
            await event.edit("اول گروه فارم رو تنظیم کن: farm on")
        else:
            await event.edit("ماهی‌گیری شروع شد ⏳ (نتیجه توی لاگ سرور)")
            asyncio.create_task(fish_once(farm_chat_id, force=True))

    elif lower.startswith("fish catch interval"):
        seconds = parse_duration_seconds(text[len("fish catch interval"):].strip())
        if seconds is None or seconds < 60:
            await event.edit("مثال: fish catch interval 50  (دقیقه)")
        else:
            fish_cfg["interval"] = seconds
            await event.edit(f"فاصله‌ی ماهی‌گیری روی {seconds / 60:g} دقیقه تنظیم شد ✅")

    elif lower == "fish status":
        feed_cap = "بدون محدودیت قیمت" if fish_feed_max >= 10**12 else f"فقط ماهی پخته‌ی ارزان‌تر از {fish_feed_max:,}"
        sell = f"فروش ماهی پخته با ارزش {fish_sell_min:,} و بالاتر" if fish_sell_enabled else "فروش خاموش است"
        cook = (f"پخت ماهی خام: فعال (از ارزش {fish_cook_min:,})" if fish_cook_min else "پخت ماهی خام: فعال (همه‌ی ماهی‌ها)") if fish_cook_enabled else "پخت ماهی خام: غیرفعال"
        rules = "، ".join(f"{k}/{v}" for k, v in sorted(feed_rules.items())) or "هیچ‌کدام"
        await event.edit(f"🐟 وضعیت ماهی‌ها\n• {cook}\n• {sell}\n• غذا دادن: {feed_cap}\n• قانون‌های غذادهی (ظرفیت/پُری): {rules}")

    elif lower in ("fish cook on", "fish cook off"):
        fish_cook_enabled = lower.endswith("on")
        await event.edit("پخت ماهی‌های خام فعال شد ✅ (همه‌ی ماهی‌ها بدون استثنا پخته میشن)" if fish_cook_enabled else "پخت ماهی‌های خام غیرفعال شد ❌")

    elif lower.startswith("fish cook above"):
        n = parse_int(text[len("fish cook above"):].strip())
        if n is None:
            await event.edit("مثال: fish cook above 0   (برای پخت همه‌ی ماهی‌ها) یا fish cook above 5000")
        else:
            fish_cook_min = n
            fish_cook_enabled = True
            await event.edit(f"پخت ماهی خام فعال شد ✅ — ماهی‌های خام با ارزش {n:,} و بالاتر پخته میشن" if n else "پخت ماهی خام فعال شد ✅ — همه‌ی ماهی‌ها بدون استثنا پخته میشن")

    elif lower == "fish sell off":
        fish_sell_enabled = False
        await event.edit("فروش ماهی غیرفعال شد ❌")

    elif lower == "fish sell on":
        fish_sell_enabled = True
        await event.edit(f"فروش ماهی فعال شد ✅ — ماهی پخته با ارزش {fish_sell_min:,} و بالاتر فروخته میشه")

    elif lower.startswith("fish sell above"):
        n = parse_int(text[len("fish sell above"):].strip())
        if n is None or n <= 0:
            await event.edit("مثال: fish sell above 50000   (50k هم قبوله)")
        else:
            fish_sell_min = n
            fish_sell_enabled = True
            warn = "\n⚠️ این عدد از حد غذادهی بیشتر نیست؛ ماهی‌های بین این دو عدد فروخته میشن و غذا داده نمیشن" if n <= fish_feed_max else ""
            await event.edit(f"فروش ماهی فعال شد ✅ — ماهی پخته با ارزش {n:,} و بالاتر فروخته میشه{warn}")

    elif lower in ("fish feed off", "fish feed unlimited"):
        fish_feed_max = 10**12
        await event.edit("حد قیمت غذادهی برداشته شد ✅ — هر ماهی پخته‌ای که ارزش غذاییش تو جای خالی شکم جا بشه به پیشی داده میشه")

    elif lower.startswith("fish feed below"):
        n = parse_int(text[len("fish feed below"):].strip())
        if n is None or n <= 0:
            await event.edit("مثال: fish feed below 2000")
        else:
            fish_feed_max = n
            await event.edit(f"حد غذادهی تنظیم شد ✅ — فقط ماهی پخته‌ی ارزان‌تر از {n:,} به پیشی داده میشه")

    elif lower == "rescue on":
        if rescue_enabled:
            await event.edit("نجات پیشی فعال می‌باشد ✅ (از قبل روشن بود)")
        else:
            rescue_enabled = True
            await event.edit("نجات پیشی خیابونی فعال شد ✅")

    elif lower == "rescue off":
        if not rescue_enabled:
            await event.edit("نجات پیشی غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
        else:
            rescue_enabled = False
            await event.edit("نجات پیشی خیابونی غیرفعال شد ❌")

    elif lower.startswith("feed text"):
        sub = lower[len("feed text"):].strip()
        arg = text[len("feed text"):].strip()
        if sub.startswith("add"):
            p = arg[3:].strip()
            if not p:
                await event.edit("مثال: feed text add من گشنمیووو")
            elif p in feed_hunger_texts:
                await event.edit("این متن از قبل توی لیست هست ✅")
            else:
                feed_hunger_texts.append(p)
                await event.edit(f"متن گرسنگی اضافه شد ✅\nاز این به بعد اگه جلوی «شکم» این متن باشه، سلف به پیشی غذا میده:\n«{p}»")
        elif sub.startswith("del"):
            p = arg[3:].strip()
            if p in feed_hunger_texts:
                feed_hunger_texts.remove(p)
                await event.edit(f"متن حذف شد ✅\n«{p}»")
            else:
                await event.edit("این متن توی لیست نیست. لیست: feed text list")
        elif sub == "reset":
            feed_hunger_texts[:] = ["گشنمیو", "دیگه کار نمیکنمیو"]
            await event.edit("لیست متن‌های گرسنگی به حالت پیش‌فرض برگشت ✅")
        else:
            r = "\n".join(f"• {p}" for p in feed_hunger_texts) or "خالی"
            await event.edit(f"متن‌های گرسنگی (اگه جلوی «شکم» باشن غذا میده):\n{r}")

    elif lower == "feed log":
        await event.edit(("📋 آخرین لاگ:\n" + "\n".join(list(_log_ring)[-18:]))[-3800:] or "لاگی نیست")

    elif lower == "feed check":
        rep = await event.get_reply_message() if event.is_reply else None
        if not rep:
            await event.edit("روی پیامِ ربات (همونی که «شکم» توشه) ریپلای کن و بنویس: feed check")
        else:
            raw = rep.raw_text or ""
            need, room = hunger_in_text(raw)
            line = next((ln for ln in _clean(raw).splitlines() if "شکم" in ln), "(خط شکم پیدا نشد)")
            await event.edit(
                f"🔎 خط شکم: {line}\nظرفیت/پُری: {parse_stomach(raw)}\n"
                f"متن‌های گرسنگی: {', '.join(feed_hunger_texts)}\n"
                f"گرسنه تشخیص داده شد؟ {need} | جای خالی: {room}\n"
                f"فرستنده ربات؟ {await _is_bot_msg(rep)} | گروه فارم؟ {_is_farm_chat(rep.chat_id)} | غذادهی روشن؟ {feed_enabled}")

    elif lower == "feed now":
        if farm_chat_id is None:
            await event.edit("اول گروه فارم رو تنظیم کن: farm on")
        else:
            await event.edit("چک شکم و غذا دادن شروع شد ⏳ (نتیجه توی لاگ سرور)")
            asyncio.create_task(feed_probe(farm_chat_id, "دستور", force=True))

    elif lower == "feed debug":
        await event.edit(
            f"غذادهی: {'روشن' if feed_enabled else 'خاموش'}\nگروه فارم: {farm_chat_id}\n"
            f"چک زمان‌دار: {feed_interval_seconds}\nقفل یخچال درگیره؟ {feed_lock.locked()}\n"
            f"متن‌های گرسنگی: {', '.join(feed_hunger_texts) or '-'}\nحد غذادهی: {fish_feed_max}")

    elif lower == "feed":
        if not feed_enabled:
            await event.edit("غذادهی غیرفعال است ❌ — اول بنویس: feed on")
        else:
            feed_setup_stage = "cap"
            await event.edit("🍖 ظرفیت شکم گربه رو وارد کنید\n(همون عدد اول داخل پرانتز جلوی «شکم»؛ مثلاً 15)")

    elif re.fullmatch(r"feed\s+\d+\s*/\s*\d+", lower):
        a, b = [int(x) for x in re.findall(r"\d+", lower)]
        feed_rules[a] = b
        await event.edit(f"فعال سازی غذادهی به پیشی با ظرفیت {a} و {b} با موفقیت انجام شد ✅")

    elif lower == "feed rules":
        r = "\n".join(f"• ظرفیت {k} → غذا وقتی پُری {v} یا کمتر شد" for k, v in sorted(feed_rules.items())) or "هیچ قانونی تنظیم نشده (با دستور feed تنظیم کن)"
        await event.edit(f"قانون‌های غذادهی:\n{r}")

    elif lower == "feed reset":
        feed_rules.clear()
        await event.edit("همه‌ی قانون‌های غذادهی پاک شد ✅ (تا قانون جدید نذاری سلف غذا نمیده)")

    elif lower == "feed on":
        if feed_enabled:
            await event.edit("غذادهی به پیشی فعال می‌باشد ✅ (از قبل روشن بود)")
            return
        feed_enabled = True
        if feed_interval_seconds is not None and farm_chat_id is not None:
            start_feed_loop()
        hint = "" if feed_rules else "\n⚠️ هنوز قانونی تنظیم نشده؛ با دستور feed ظرفیت رو تنظیم کن"
        await event.edit("غذادهی به پیشی فعال شد ✅" + (f" (هر {feed_interval_seconds/60:g} دقیقه چک میشه)" if feed_interval_seconds else " (بعد از هر فارم چک میشه)") + hint)

    elif lower == "feed off":
        if not feed_enabled:
            await event.edit("غذادهی به پیشی غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
            return
        feed_enabled = False
        stop_feed_loop()
        await event.edit("غذادهی به پیشی غیرفعال شد ❌")

    elif lower == "feed interval off":
        feed_interval_seconds = None
        stop_feed_loop()
        await event.edit("چک زمان‌دار متوقف شد ✅ — از این به بعد بعد از هر فارم چک میشه")

    elif lower.startswith("feed interval"):
        seconds = parse_duration_seconds(text[len("feed interval"):].strip())
        if seconds is None or seconds < 60:
            await event.edit("مثال: feed interval 30  (دقیقه)  یا  feed interval 2h  (ساعت)")
        elif farm_chat_id is None:
            await event.edit("اول گروه فارم رو تنظیم کن: farm on")
        else:
            feed_interval_seconds = seconds
            feed_enabled = True
            start_feed_loop()
            await event.edit(f"چک غذادهی هر {seconds/60:g} دقیقه فعال شد ✅")

    elif lower.startswith("rescue tries"):
        try:
            n = int(lower[len("rescue tries"):].strip())
            if not 1 <= n <= 50:
                raise ValueError
            rescue_max_tries = n
            await event.edit(f"نجات پیشی: حداکثر {n} کلیک برای هر پیام تنظیم شد ✅")
        except ValueError:
            await event.edit("مثال: rescue tries 10   (بین ۱ تا ۵۰)")

    elif lower == "farm now":
        if farm_chat_id is None:
            await event.edit("هنوز گروه فارم تنظیم نشده. بنویس: farm on")
        else:
            await event.edit("در حال برداشت... ⏳")
            await meowie_farm_once()

    elif lower == "farm +5":
        farm_interval_seconds += 5 * 60
        await event.edit(f"فاصله‌ی فارم روی {farm_interval_seconds/60:g} دقیقه تنظیم شد ✅")

    elif lower == "farm -5":
        farm_interval_seconds = max(5 * 60, farm_interval_seconds - 5 * 60)
        await event.edit(f"Farm interval: {farm_interval_seconds/60:g} min")

    elif lower.startswith("farm interval"):
        raw = text[len("farm interval"):].strip()
        seconds = parse_duration_seconds(raw)
        if seconds is None or seconds < 60:
            await event.edit("مثال: farm interval 30  (دقیقه)  یا  farm interval 2h  (ساعت)")
        else:
            farm_interval_seconds = seconds
            await event.edit(f"فاصله‌ی فارم روی {farm_interval_seconds/60:g} دقیقه تنظیم شد ✅")

    elif lower == "meow +5":
        meow_default_seconds += 5 * 60
        await event.edit(f"فاصله‌ی پیش‌فرض میو روی {meow_default_seconds/60:g} دقیقه تنظیم شد ✅")

    elif lower == "meow -5":
        meow_default_seconds = max(60, meow_default_seconds - 5 * 60)
        await event.edit(f"Default meow interval: {meow_default_seconds/60:g} min")

    elif lower == "help":
        await send_help(event, True)

    elif lower == "guide":
        await send_help(event, True, HELP_TEXT)

    elif lower == "clean all":
        if not event.is_reply:
            await event.edit("روی یکی از پیام‌های خودت توی این چت ریپلای کن و بنویس: clean all")
            return
        reply_msg = await event.get_reply_message()
        if not (reply_msg and reply_msg.out):
            await event.edit("باید روی یکی از پیام‌های خودت ریپلای کنی.")
            return
        count = await delete_all_my_messages(chat_id)
        await client.send_message(chat_id, f"Deleted {count} of your messages in this chat ✅")


# ---------- هندلر پیام‌های ورودی: افک + کنترل مدیر ----------

_RELAY_RE = re.compile(r"(meow (on|off)\b|meow [+-]5$|clock (bio|name|font)\b|afk (on|off)\b|farm (off|[+-]5$|interval\b)|fish status$|fish catch\b|miner (on|off|now|status|sell now)$|factory (on|off|now|status)$|feed (rules$|interval\b|text list$)|ttl (on|off)$)")


def is_relay_command(payload: str) -> bool:
    """دستورهایی که مدیر می‌نویسه و خودِ سلف باید اون رو مثل دستور خودش اجرا کنه"""
    payload = persian_to_en(normalize(payload)) or payload
    low = normalize(payload).lower().strip()
    if low == "farm on":
        return farm_chat_id is not None
    return bool(_RELAY_RE.match(low))


async def open_glass_menu(event):
    """منوی شیشه‌ای: از ربات سلف‌ساز (inline) توی همین چت پنل می‌فرسته"""
    bot_un = os.environ.get("MOZI_MENU_BOT", "")
    chat_id = event.chat_id
    try:
        if not bot_un:
            raise RuntimeError("no bot")
        res = await client.inline_query(bot_un, f"menu {chat_id}")
        if not res:
            raise RuntimeError("empty")
        await event.delete()
        await res[0].click(chat_id)
    except Exception as e:
        print(f"menu: {e}")
        try:
            await event.edit("منو باز نشد ❌ (ادمین باید inline mode ربات سلف‌ساز رو با BotFather روشن کنه: /setinline)", parse_mode=None)
        except Exception:
            pass


def _mn(x):
    """ثانیه → دقیقه (متن)"""
    try:
        return f"{float(x) / 60:g}"
    except Exception:
        return None


def menu_state(chat_id: int) -> dict:
    """وضعیت لحظه‌ای همه‌ی دکمه‌های منوی شیشه‌ای (روشن/خاموش + مقدارها)؛ ربات سازنده با همین، دکمه‌ها رو می‌کشه"""
    g = chat_id < 0
    s: dict = {}
    for k in _TOGGLE_NAMES:
        try:
            s[k] = _toggle_state(k, chat_id, g)
        except Exception:
            s[k] = None
    G = globals()

    def put(k, fn):
        try:
            s[k] = fn()
        except Exception:
            s[k] = None

    put("rescue", lambda: bool(G.get("rescue_enabled")))
    put("feed", lambda: bool(G.get("feed_enabled")))
    put("jail", lambda: bool(jail_cfg.get("enabled", True)))
    put("pm", lambda: bool(pm_cfg.get("enabled", True)))
    put("safe", lambda: bool(safe_cfg.get("on", True)))
    put("v_meow", lambda: _mn(meow_seconds.get(chat_id, meow_default_seconds)))
    put("v_farm", lambda: _mn(farm_interval_seconds))
    put("v_feed", lambda: _mn(feed_interval_seconds) if feed_interval_seconds else "همراه فارم")
    put("v_rescue", lambda: str(rescue_max_tries))
    put("v_fish_int", lambda: _mn(fish_cfg.get("interval")))
    put("v_fridge", lambda: str(fish_cfg.get("fridge_max")))
    put("v_fsell", lambda: f"{int(fish_sell_min):,}")
    put("v_ffeed", lambda: "بدون حد" if fish_feed_max >= 10 ** 11 else f"{int(fish_feed_max):,}")
    put("v_fcook", lambda: "همه" if not fish_cook_min else f"{int(fish_cook_min):,}")
    put("v_miner_int", lambda: _mn(miner_cfg.get("interval")))
    put("v_msell", lambda: f"{miner_cfg.get('sell_cur')} {miner_cfg.get('sell_pct')}%")
    put("v_charge", lambda: f"{int(miner_cfg.get('charge_amount')):,}")
    put("miner_cur", lambda: str(miner_cfg.get("convert_cur", "")).upper())
    put("miner_pct", lambda: str(miner_cfg.get("convert_pct")))
    put("v_bank_min", lambda: f"{int(bank_cfg['min']):,}" if bank_cfg.get("min") else "—")
    put("v_bank_margin", lambda: f"{int(bank_cfg.get('margin')):,}")
    put("v_bank_wd", lambda: f"{int(bank_cfg.get('wd_amount')):,}")
    put("v_bank_extra", lambda: f"{int(bank_cfg.get('wd_extra')):,}")
    put("v_bank_card", lambda: str(bank_cfg.get("tr_card") or "—"))
    put("v_bank_tr", lambda: f"{float(bank_cfg.get('tr_interval')) / 3600:g}h")
    put("v_f_sell", lambda: "خاموش" if factory_sell_min is None else f"{factory_sell_min:g}")
    put("v_f_cost", lambda: "بدون حد" if not factory_cost_max else f"{factory_cost_max:g}")
    put("v_f_int", lambda: _mn(factory_interval_seconds))
    put("f_pct", lambda: str(factory_percent))
    put("bio_style", lambda: str(bio_style))
    put("v_bio_font", lambda: str(bio_digit_font))
    put("v_name_font", lambda: str(name_digit_font))
    put("v_gap", lambda: f"{safe_cfg.get('gap'):g}s")
    put("v_jitter", lambda: f"{safe_cfg.get('jitter'):g}s")
    return s


class _SilentEvent:
    """رویداد ساختگی: دستور رو اجرا می‌کنه ولی هیچ پیامی توی چت نمی‌فرسته/ادیت نمی‌کنه؛
    جواب‌های handler فقط جمع میشن و به منو برمی‌گردن"""

    def __init__(self, chat_id: int, text: str):
        self.chat_id = chat_id
        self.raw_text = text
        self.text = text
        self.id = -(time.time_ns() // 1000)
        self.message = self
        self.out = True
        self.sender_id = None
        self.is_group = chat_id < 0
        self.is_channel = False
        self.is_private = chat_id > 0
        self.is_reply = False
        self.reply_to_msg_id = None
        self.client = client
        self.replies: list = []

    async def edit(self, text=None, *a, **k):
        if text is not None:
            self.replies.append(str(text))
        return self

    async def reply(self, text=None, *a, **k):
        if text is not None:
            self.replies.append(str(text))
        return self

    respond = reply

    async def delete(self, *a, **k):
        return None

    async def get_sender(self):
        return await client.get_me()

    async def get_reply_message(self):
        return None


async def run_silent(chat_id: int, cmd: str) -> str:
    ev = _SilentEvent(chat_id, cmd)
    try:
        await asyncio.wait_for(handler(ev), 60)
    except Exception as e:
        ev.replies.append(f"❌ {str(e)[:150]}")
    try:
        save_settings()
    except Exception:
        pass
    return "\n".join(ev.replies).strip()


async def menu_cmd_loop():
    """جاب‌های دکمه‌های منو (پوشه‌ی cmd) رو بی‌صدا اجرا می‌کنه و جواب + وضعیت تازه رو توی پوشه‌ی res می‌ذاره"""
    d = os.environ.get("MOZI_CMD_DIR")
    if not d:
        return
    os.makedirs(d, exist_ok=True)
    rd = os.path.join(os.path.dirname(d.rstrip("/\\")), "res")
    os.makedirs(rd, exist_ok=True)
    lock = asyncio.Lock()

    async def work(job: dict):
        chat = int(job["chat"])
        cmd = str(job.get("cmd") or "").strip()
        reply = ""
        if cmd and cmd != "__state__":
            async with lock:
                reply = await run_silent(chat, cmd)
                await asyncio.sleep(0.4)
        jid = job.get("id")
        if not jid:
            return
        out = {"reply": reply[:3500], "state": menu_state(chat)}
        tmp = os.path.join(rd, f"{jid}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(rd, f"{jid}.json"))

    while True:
        try:
            for fn in sorted(os.listdir(d)):
                fp = os.path.join(d, fn)
                try:
                    with open(fp, encoding="utf-8") as f:
                        job = json.load(f)
                    os.remove(fp)
                    asyncio.create_task(work(job))
                except Exception as e:
                    print(f"menu cmd: {e}")
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            for fn in os.listdir(rd):          # جواب‌های کهنه پاک بشن
                fp = os.path.join(rd, fn)
                try:
                    if time.time() - os.path.getmtime(fp) > 120:
                        os.remove(fp)
                except OSError:
                    pass
        except Exception as e:
            print(f"menu loop: {e}")
        await asyncio.sleep(0.3)


async def run_as_self(chat_id: int, cmd: str, reply_to=None):
    """دستور رو با خودِ اکانت سلف توی همون چت می‌فرسته و اجرا می‌کنه؛ پیام میشه تأیید فارسی
    و بعد ۳۰ ثانیه پاک میشه"""
    try:
        sent = await client.send_message(chat_id, cmd, reply_to=reply_to or None)
        await asyncio.sleep(1.5)
        if (sent.chat_id, sent.id) not in _seen_cmd:   # اگه رویداد خودکار نیومد، مستقیم اجرا کن
            ev = events.NewMessage.Event(sent)
            try:
                ev._set_client(client)
            except Exception:
                pass
            await handler(ev)
        await asyncio.sleep(30)
        await sent.delete()
    except Exception as e:
        print(f"اجرای دستور مدیر: {e}")


def apply_remote_command(payload: str):
    """دستورهای تنظیمیِ کوتاه که مدیر می‌فرسته (با rmg یا بدونش). اجرا میشن و متن تأیید (فارسی) برمی‌گرده؛
    اگه دستور شناخته نشد None"""
    global rescue_enabled, feed_enabled, rescue_max_tries, fish_sell_min, fish_sell_enabled, fish_feed_max, fish_cook_min, fish_cook_enabled
    payload = persian_to_en(normalize(payload)) or payload
    low = normalize(payload).lower().strip()
    msg = None
    if low in ("rescue on", "rescue off"):
        rescue_enabled = low.endswith("on")
        msg = "نجات پیشی خیابونی فعال شد ✅" if rescue_enabled else "نجات پیشی خیابونی غیرفعال شد ❌"
    elif low in ("feed on", "feed off"):
        feed_enabled = low.endswith("on")
        if feed_enabled and feed_interval_seconds is not None and farm_chat_id is not None:
            start_feed_loop()
        elif not feed_enabled:
            stop_feed_loop()
        msg = "غذادهی به پیشی فعال شد ✅" if feed_enabled else "غذادهی به پیشی غیرفعال شد ❌"
    elif low.startswith("rescue tries"):
        try:
            rescue_max_tries = max(1, int(low[len("rescue tries"):].strip()))
        except ValueError:
            return None
        msg = f"نجات پیشی: حداکثر {rescue_max_tries} کلیک برای هر پیام تنظیم شد ✅"
    elif low in ("fish sell on", "fish sell off"):
        fish_sell_enabled = low.endswith("on")
        msg = "فروش ماهی فعال شد ✅" if fish_sell_enabled else "فروش ماهی غیرفعال شد ❌"
    elif low.startswith("fish sell above"):
        n = parse_int(low[len("fish sell above"):].strip())
        if not n:
            return None
        fish_sell_min, fish_sell_enabled = n, True
        msg = f"فروش ماهی فعال شد ✅ — ماهی پخته با ارزش {n:,} و بالاتر فروخته میشه"
    elif low in ("fish feed off", "fish feed unlimited"):
        fish_feed_max = 10**12
        msg = "حد قیمت غذادهی برداشته شد ✅"
    elif low.startswith("fish feed below"):
        n = parse_int(low[len("fish feed below"):].strip())
        if not n:
            return None
        fish_feed_max = n
        msg = f"حد غذادهی تنظیم شد ✅ — فقط ماهی پخته‌ی ارزان‌تر از {n:,} به پیشی داده میشه"
    elif low in ("fish cook on", "fish cook off"):
        fish_cook_enabled = low.endswith("on")
        msg = "پخت ماهی‌های خام فعال شد ✅" if fish_cook_enabled else "پخت ماهی‌های خام غیرفعال شد ❌"
    elif low.startswith("fish cook above"):
        n = parse_int(low[len("fish cook above"):].strip())
        if n is None:
            return None
        fish_cook_min, fish_cook_enabled = n, True
        msg = f"پخت ماهی خام فعال شد ✅ — ماهی‌های خام با ارزش {n:,} و بالاتر پخته میشن" if n else "پخت ماهی خام فعال شد ✅ — همه‌ی ماهی‌ها پخته میشن"
    elif re.fullmatch(r"feed\s+\d+\s*/\s*\d+", low):
        x, y = [int(v) for v in re.findall(r"\d+", low)]
        feed_rules[x] = y
        msg = f"فعال سازی غذادهی به پیشی با ظرفیت {x} و {y} با موفقیت انجام شد ✅"
    elif low.startswith("feed text add "):
        p = normalize(payload)[len("feed text add "):].strip()
        if not p:
            return None
        if p not in feed_hunger_texts:
            feed_hunger_texts.append(p)
        msg = f"متن گرسنگی اضافه شد ✅ «{p}»"
    elif low.startswith("feed text del "):
        p = normalize(payload)[len("feed text del "):].strip()
        if p not in feed_hunger_texts:
            return None
        feed_hunger_texts.remove(p)
        msg = f"متن گرسنگی حذف شد ✅ «{p}»"
    elif low == "feed text reset":
        feed_hunger_texts[:] = ["گشنمیو", "دیگه کار نمیکنمیو"]
        msg = "متن‌های گرسنگی به حالت پیش‌فرض برگشت ✅"
    elif low == "feed reset":
        feed_rules.clear()
        msg = "همه‌ی قانون‌های غذادهی پاک شد ✅"
    elif low.startswith(("miner ", "main ")) and low not in ("miner on", "miner off", "miner now", "miner status", "miner sell now"):
        msg = apply_miner_command(low.split(None, 1)[1])
        if msg is None:
            return None
    elif low.startswith("bank ") and low not in ("bank on", "bank off", "bank status") or low in ("bank on", "bank off"):
        msg = apply_bank_command(low[len("bank "):])
        if msg is None:
            return None
    elif (low.startswith("factory ") and not low.endswith((" on", " off", " now", " status"))) or low in ("factory sell off", "factory cost off"):
        msg = apply_factory_command(low[len("factory "):])
        if msg is None:
            return None
    elif low in ("jail on", "jail off"):
        msg = jail_command(low, 0, False)
    elif low.startswith("confirm text"):
        msg = confirm_command(normalize(payload))
    else:
        return None
    print(f"دستور مدیر اجرا شد: {low}")
    return msg


async def _confirm_and_delete(event, text: str, delay: int = 30):
    """پیام تأیید رو (ریپلای به دستور مدیر) می‌فرسته و بعد از ۳۰ ثانیه پاک می‌کنه"""
    try:
        m = await event.reply(text)
        await asyncio.sleep(delay)
        await m.delete()
    except Exception as e:
        print(f"پیام تأیید: {e}")


@client.on(events.NewMessage(incoming=True))
async def meowie_power_pm(event):
    """پیام پیوی میویی «اعتبار برق شما دیگر برای پرداخت قبض کافی نیست / ماینر خاموش شد» → شارژ اعتبار برق"""
    if not event.is_private or not miner_cfg.get("power_enabled", True):
        return
    t = _clean(normalize(event.raw_text or ""))
    if not pm_gate("power", t):
        return
    try:
        sender = await event.get_sender()
        if not _pm_bot_ok(sender):
            return
    except Exception:
        return
    print("پیام «اعتبار برق ماینر تمام شد» از میویی رسید → شارژ اعتبار برق")
    try:
        await asyncio.sleep(3)
        await miner_power_session()
    except Exception as e:
        print(f"شارژ برق (پیام): {e}")


@client.on(events.NewMessage(incoming=True))
async def meowie_cooked_pm(event):
    """وقتی ربات میویی توی پیوی میگه «ماهی شما پخید»، سلف توی گروه فارم یخچال رو چک می‌کنه"""
    if not event.is_private:
        return
    t = _clean(normalize(event.raw_text or ""))
    if not pm_gate("cooked", t):
        return
    if farm_chat_id is None:
        print("پیام پخت ماهی رسید ولی گروه فارم تنظیم نیست؛ بنویس: farm on")
        return
    print("پیام پخت ماهی از میویی رسید → بررسی یخچال")
    try:
        sender = await event.get_sender()
        if not _pm_bot_ok(sender):
            return
    except Exception:
        return
    try:
        await pm_do_fridge()
    except Exception as e:
        print(f"پیام پخت: {e}")


@client.on(events.NewMessage(incoming=True))
async def meowie_factory_pm(event):
    """وقتی ربات میویی توی پیوی میگه «تولید ... به پایان رسید و به انبار منتقل شد»،
    سلف توی گروه تعیین‌شده «کارخونه میویی» می‌فرسته، انبار رو چک می‌کنه و اگه قیمت بازار به حد رسیده بود می‌فروشه"""
    global factory_busy_until
    if not event.is_private or not factory_enabled:
        return
    t = _clean(normalize(event.raw_text or ""))
    if not pm_gate("factory", t):
        return
    try:
        sender = await event.get_sender()
        if not _pm_bot_ok(sender):
            return
    except Exception:
        return
    if _factory_target_chat() is None:
        print("پیام پایان تولید رسید ولی گروه کارخونه تنظیم نیست؛ بنویس: factory chat <آیدی گروه>")
        return
    print("پیام پایان تولید از میویی رسید → بررسی انبار و فروش")
    factory_busy_until = 0.0
    if factory_timer and factory_timer is not asyncio.current_task():
        factory_timer.cancel()
    try:
        await asyncio.sleep(3)
        await factory_session(force=True)
    except Exception as e:
        print(f"پیام پایان تولید: {e}")


_PM_REQ = {
    "miner": lambda: bool(miner_cfg["enabled"]),
    "power": lambda: bool(miner_cfg.get("power_enabled", True)),
    "factory": lambda: bool(factory_enabled),
    "cooked": lambda: True,
}
_PM_HANDLERS = {"miner": meowie_miner_pm, "power": meowie_power_pm, "cooked": meowie_cooked_pm, "factory": meowie_factory_pm}


async def pm_bot_dialogs(limit: int = 300):
    out = []
    async for d in client.iter_dialogs(limit=limit):
        e = d.entity
        if d.is_user and getattr(e, "bot", False):
            out.append(e)
    return out


async def pm_bots_text() -> str:
    bots = await pm_bot_dialogs()
    pm_bot_cache.clear()
    if not bots:
        return "توی پیوی‌هات رباتی پیدا نشد."
    out = [f"🤖 رباتای پیوی (انتخاب فعلی: {_pm_bot_cur_text()}):"]
    for i, e in enumerate(bots, 1):
        un = f"@{e.username}" if getattr(e, "username", None) else "—"
        pm_bot_cache.append(un if un != "—" else str(e.id))
        out.append(f"{i}. {'✅' if _pm_bot_ok(e) else '•'} {getattr(e, 'first_name', '') or ''} ({un}) — id {e.id}")
    out.append("\nافزودن به لیست: اعلان ربات افزودن <شماره> | حذف: اعلان ربات حذف <شماره/@یوزرنیم> | جایگزین کل لیست: اعلان ربات تنظیم @a, @b")
    return "\n".join(out)


async def pm_check(n: int = 5, dry: bool = False, mode: str = "old") -> str:
    """پیوی رباتای انتخاب‌شده (و رباتِ اختصاصیِ اعلان‌های دلخواه) رو دستی می‌خونه.
    mode=old: n پیام آخر | mode=new: فقط پیام‌های جدیدتر از آخرین بررسی/پیامِ دیده‌شده.
    برای هر ربات و هر اعلان، جدیدترین پیامِ مطابق اجرا میشه"""
    dialogs = await pm_bot_dialogs()
    rules = pm_cfg.get("rules", {}) if pm_cfg.get("enabled", True) else {}
    rule_bot = lambda e: any(r.get("bot") and r.get("enabled", True) and _pm_bot_match(e, r["bot"]) for r in rules.values())
    bots = [e for e in dialogs if _pm_bot_ok(e) or rule_bot(e)]
    if not bots:
        return f"رباتِ «{_pm_bot_cur_text()}» توی پیوی‌هات پیدا نشد. (اعلان ربات لیست)"
    seen_map = pm_cfg.setdefault("seen", {})
    names = ", ".join(f"{getattr(e, 'first_name', '')}" + (f" (@{e.username})" if getattr(e, "username", None) else "") for e in bots)
    lines = [f"🔎 {'بررسی (بدون اجرا)' if dry else 'بررسی'} {'پیام‌های جدید' if mode == 'new' else f'{n} پیام آخر'}: {names}"]
    done, total = set(), 0
    now = datetime.now(timezone.utc)
    for bot in bots:
        base = seen_map.get(str(bot.id))
        kw = {"min_id": base} if (mode == "new" and base) else {}
        msgs = [m async for m in client.iter_messages(bot, limit=n, **kw)]          # جدیدترین اول
        label = getattr(bot, "first_name", "") or str(bot.id)
        if mode == "new" and not base:
            lines.append(f"ℹ️ {label}: هنوز نقطه‌ی شروعی ثبت نشده؛ {n} پیام آخر بررسی شد (از این به بعد فقط جدیدها)")
        elif mode == "new" and not msgs:
            lines.append(f"• {label}: پیام جدیدی نیست")
        for m in msgs:
            if m.out:
                continue
            t = _clean(normalize(m.raw_text or ""))
            if not t:
                continue
            total += 1
            age = max(0, int((now - m.date).total_seconds() // 60)) if getattr(m, "date", None) else 0
            when = f"{age} دقیقه پیش" if age < 120 else f"{age // 60} ساعت پیش"
            if _pm_bot_ok(bot):
                for k in PM_LABELS:
                    if (bot.id, k) in done or not pm_gate(k, t):
                        continue
                    done.add((bot.id, k))
                    if not _PM_REQ[k]():
                        lines.append(f"• {label} / {k} ({when}): پیام هست ولی قابلیتش خاموشه؛ انجام نشد")
                        continue
                    lines.append(f"• {label} / {k} ({when}): {PM_LABELS[k]} " + ("← (فقط نمایش)" if dry else "← اجرا شد ✅"))
                    if not dry:
                        asyncio.create_task(_PM_HANDLERS[k](m))
            for name, r in rules.items():
                if (bot.id, "rule:" + name) in done or not r.get("enabled", True) or not _pm_rule_bot_ok(r, bot) or not pm_rule_matches(r, t):
                    continue
                done.add((bot.id, "rule:" + name))
                lines.append(f"• {label} / {name} ({when}): عمل دلخواه " + ("← (فقط نمایش)" if dry else "← اجرا شد ✅"))
                if not dry:
                    asyncio.create_task(pm_run_rule(name, m.raw_text or "", manual=True))
        if not dry and msgs:
            seen_map[str(bot.id)] = max(seen_map.get(str(bot.id), 0), max(getattr(m, "id", 0) for m in msgs))
    if not done and total:
        lines.append(f"توی {total} پیام بررسی‌شده چیزی که به اعلان‌ها بخوره نبود.")
    return "\n".join(lines)


# ======================= زندان میویی =======================
# ربات اگه دستورها پشت‌هم بیان سلف رو زندانی می‌کنه؛ سلف خودش آزاد میشه:
# «زندان میویی» ← پرداخت جریمه ← پرداخت معمولی ← تأیید ✓
JAIL_TRIGGER_TEXT = "زندان میویی"
JAIL_PHRASES = ("تو پیشی بدی بودی و زندانی شدی", "تا وقتی توی حبسی نمیتونی")
jail_cfg = {"enabled": True}
jail_state = {"busy": False, "last": 0.0}


def _jail_msg(text: str) -> bool:
    lt = _loose(text or "")
    return any(_loose(p) in lt for p in JAIL_PHRASES)


async def jail_release(chat_id: int, source: str = "", force: bool = False) -> bool:
    if jail_state["busy"]:
        return False
    if not force and time.time() - jail_state["last"] < 45:
        return False
    jail_state["busy"] = True
    jail_state["last"] = time.time()
    try:
        print(f"🚔 زندانی شدی ({source}) → آزادسازی توی چت {chat_id}")
        await asyncio.sleep(2)
        sent = await client.send_message(chat_id, JAIL_TRIGGER_TEXT)
        msg = await wait_reply_to(chat_id, sent.id, timeout=15)
        if not msg:
            print("🚔 پاسخ «زندان میویی» نیومد")
            return False
        mid = msg.id
        b = await wait_button(chat_id, mid, "پرداخت جریمه", 8)
        if not b:
            print("🚔 دکمه‌ی «پرداخت جریمه» نیست (شاید زندانی نیستی یا جریمه پرداخت شده)")
            return False
        if not await safe_click(b):
            return False
        pg = await wait_msg(chat_id, mid, lambda x: any("معمولی" in (y.text or "") or "کارت" in (y.text or "") for y in _flat_buttons(x)), 10)
        if not pg:
            latest = await client.get_messages(chat_id, ids=mid)
            print("🚔 دکمه‌های روش پرداخت پیدا نشد؛ متن: " + _clean(latest.raw_text if latest else "")[:120].replace("\n", " | "))
            return False
        t2 = _clean(pg.raw_text or "")
        fm = re.search(r"جریمه\s*نقدی[^\d\n]*(\d[\d,]*)", t2)
        hm = re.search(r"میو\s*پوینت\s*هات[^\d\n]*(\d[\d,]*)", t2)
        bm = re.search(r"موجودی\s*حساب[^\d\n]*(\d[\d,]*)", t2)
        fine = _toint(fm.group(1)) if fm else None
        hot = _toint(hm.group(1)) if hm else None
        bank = _toint(bm.group(1)) if bm else None
        normal = next((y for y in _flat_buttons(pg) if "معمولی" in (y.text or "")), None)
        card = next((y for y in _flat_buttons(pg) if "کارت" in (y.text or "")), None)
        if fine is not None and hot is not None and hot < fine:
            if card and (bank is None or bank >= fine):
                pick, way = card, "کارت بانکی (میو پوینت هات کمه)"
            else:
                print(f"🚔 نه میو پوینت هات ({hot:,}) و نه موجودی بانک ({bank}) برای جریمه ({fine:,}) کافی نیست")
                return False
        else:
            pick, way = (normal or card), ("معمولی" if normal else "کارت بانکی")
        if pick is None:
            print("🚔 دکمه‌ی روش پرداخت پیدا نشد")
            return False
        print(f"🚔 روش پرداخت: {way}")
        if not await safe_click(pick):
            return False
        cf = await wait_msg(chat_id, mid, lambda x: _confirm_txt(x.raw_text) and bool(x.buttons), 10)
        if not cf:
            print("🚔 صفحه‌ی تأیید نیومد (اگه متنش عوض شده: confirm text add <متن>)")
            return False
        res = await confirm_click(chat_id, cf)
        if res is None:
            print("🚔 تأیید پرداخت جریمه انجام نشد")
            return False
        print("🚔 جریمه پرداخت شد ✅ — " + _clean(res.raw_text or "")[:80].replace("\n", " | "))
        if res.buttons and _has_explicit_back(res):
            await click_back(chat_id, mid)
        return True
    except Exception as e:
        print(f"🚔 خطا توی آزادسازی: {e}")
        return False
    finally:
        jail_state["busy"] = False


@client.on(events.NewMessage(incoming=True))
async def jail_watcher(event):
    if not jail_cfg.get("enabled", True) or jail_state["busy"]:
        return
    if not _jail_msg(event.raw_text or ""):
        return
    try:
        sender = await event.get_sender()
        if event.is_private:
            if not _pm_bot_ok(sender):
                return
            chat = farm_chat_id or _miner_chat() or _factory_target_chat()
        else:
            if not getattr(sender, "bot", False):
                return
            rep = await event.get_reply_message()
            if not rep or not rep.out:       # فقط وقتی جواب پیام خودِ سلف باشه (نه زندانی شدن بقیه)
                return
            chat = event.chat_id
    except Exception:
        return
    if chat is None:
        print("🚔 زندانی شدی ولی گروه فارم تنظیم نیست؛ بنویس: farm on")
        return
    asyncio.create_task(jail_release(chat, "پیام ربات"))


def jail_command(text: str, chat_id: int, is_group: bool) -> str:
    a = text.lower().split()
    sub_ = a[1] if len(a) > 1 else "status"
    if sub_ in ("on", "off"):
        want = sub_ == "on"
        if bool(jail_cfg.get("enabled", True)) == want:
            return "آزادسازی از زندان " + ("فعال می‌باشد ✅ (از قبل روشن بود)" if want else "غیرفعال می‌باشد ❌ (از قبل خاموش بود)")
        jail_cfg["enabled"] = want
        return "آزادسازی خودکار از زندان فعال شد ✅" if want else "آزادسازی خودکار از زندان غیرفعال شد ❌"
    if sub_ == "now":
        target = chat_id if is_group else (farm_chat_id or _miner_chat() or _factory_target_chat())
        if target is None:
            return "اول گروه فارم رو تنظیم کن: farm on (یا این دستور رو توی گروه بنویس)"
        asyncio.create_task(jail_release(target, "دستور", force=True))
        return "آزادسازی از زندان شروع شد ⏳ (نتیجه توی لاگ سرور)"
    return (f"🚔 آزادسازی خودکار از زندان: {'روشن ✅' if jail_cfg.get('enabled', True) else 'خاموش ❌'}\n"
            "مراحل: «زندان میویی» ← پرداخت جریمه ← پرداخت معمولی (اگه میو پوینت کم بود: کارت بانکی) ← تأیید ✓\n"
            "jail on | jail off | jail now | jail status\n"
            "💡 برای کمتر زندانی شدن: safe gap 10 (فاصله‌ی بین دستورها)")


@client.on(events.NewMessage(incoming=True))
async def incoming_watcher(event):
    global farm_interval_seconds, delete_flow_stage, bio_digit_font, name_digit_font, rescue_enabled, feed_enabled

    sender_id = event.sender_id
    text = normalize(event.raw_text or "")
    lower = text.lower()

    # --- حذف پیام از راه دور با ریپلای «پاک»/«delete» ---
    if event.is_reply and lower in ("پاک", "delete") and sender_id in ADMIN_IDS:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.out:
            await reply_msg.delete()
            await event.delete()
        return

    # --- rmg <text>: وقتی مدیر این رو تو یه گپ می‌فرسته، این اکانت هم همون متن رو
    # توی همون گپ می‌فرسته (برای هماهنگ‌کردن چند اکانت سلف با هم). اگه متن دقیقاً
    # کلمه‌ی محرک میویی («پیشی») باشه، بعد از ارسال، دنبال دکمه‌ی برداشت هم می‌گرده.
    # --- farm now از طرف مدیر توی گروه: خودِ اکانت سلف همون‌جا «پیشی» می‌فرسته ---
    if sender_id in ADMIN_IDS and not event.is_private and lower == "farm now":
        sent = await client.send_message(event.chat_id, MEOWIE_TRIGGER_TEXT)
        await click_farm_button(event.chat_id, sent.id)
        return

    if sender_id in ADMIN_IDS and lower.startswith("rmg "):
        payload = text[len("rmg "):].strip()
        # اگه متن بعد از rmg یکی از دستورهای تنظیمیِ سلف باشه (مثل rescue on)،
        # چیزی ارسال نمیشه؛ فقط همون دستور روی همین اکانت اجرا میشه
        res = apply_remote_command(payload)
        if res:
            asyncio.create_task(_confirm_and_delete(event, res))
            return
        if is_relay_command(payload):
            asyncio.create_task(run_as_self(event.chat_id, payload, event.reply_to_msg_id))
            return
        if payload:
            # اگه مدیر روی پیام یه نفر ریپلای زده، سلف هم روی همون پیام ریپلای می‌زنه
            sent = await client.send_message(event.chat_id, payload, reply_to=event.reply_to_msg_id or None)
            if payload == MEOWIE_TRIGGER_TEXT:
                await click_farm_button(event.chat_id, sent.id)
        return

    # --- دستور تنظیمی ساده از طرف مدیر (بدون نیاز به rmg، توی گروه یا پیوی): اجرا + تأیید که بعد ۳۰ ثانیه پاک میشه ---
    if sender_id in ADMIN_IDS:
        res = apply_remote_command(text)
        if res:
            asyncio.create_task(_confirm_and_delete(event, res))
            return
        if is_relay_command(text):
            asyncio.create_task(run_as_self(event.chat_id, text))
            return

    if event.is_private and sender_id in ADMIN_IDS:
        admin_chat_id = event.chat_id

        # --- فلوی گفتگویی «delete messages» برای مدیر ---
        if delete_flow_stage == "await_type":
            delete_flow_stage = None
            if lower in ("group", "گروه"):
                delete_flow_stage = "await_group"
                await event.reply("Send the group's numeric ID or link.")
            elif lower in ("private", "pv", "پیوی"):
                delete_flow_stage = "await_private"
                await event.reply("Send the numeric ID or username of the person.")
            else:
                delete_flow_stage = "await_type"
                await event.reply("Please answer with: group  or  private")
            return

        if delete_flow_stage == "await_group":
            delete_flow_stage = None
            try:
                target_chat_id = await resolve_chat_id(text)
                count = await delete_all_my_messages(target_chat_id)
                await event.reply(f"Deleted {count} of your messages in that group ✅")
            except Exception:
                await event.reply("Could not resolve that group. Try again with: delete messages")
            return

        if delete_flow_stage == "await_private":
            delete_flow_stage = None
            try:
                target_chat_id = await resolve_chat_id(text)
                count = await delete_all_my_messages(target_chat_id)
                await event.reply(f"Deleted {count} of your messages in that chat ✅")
            except Exception:
                await event.reply("Could not resolve that user. Try again with: delete messages")
            return

        if lower == "delete messages":
            delete_flow_stage = "await_type"
            await event.reply("Where should I delete messages from? Reply: group  or  private")
            return

        # --- گام دوم فلوی «mio online is gp» ---
        if sender_id in admin_awaiting_group:
            admin_awaiting_group.discard(sender_id)
            parts = text.split()
            target_raw = parts[0] if parts else ""
            minutes = None
            if len(parts) >= 2:
                try:
                    minutes = float(parts[1])
                except ValueError:
                    minutes = None
            try:
                if target_raw.lstrip("-").isdigit():
                    target_chat_id = int(target_raw)
                else:
                    entity = await client.get_entity(target_raw)
                    target_chat_id = entity.id
                msg = await toggle_meow(target_chat_id, minutes)
                await event.reply(f"گروه شناسایی شد ✅\n{msg}")
            except Exception:
                await event.reply(
                    "نتونستم این گروه رو شناسایی کنم. آیدی عددی یا لینک عمومی رو چک کن، "
                    "یا مطمئن شو اکانت اصلی از قبل عضو اون گروهه."
                )
            return

        if lower == "mio online is gp":
            admin_awaiting_group.add(sender_id)
            await event.reply(
                "آیدی عددی گروه یا لینک عمومی‌ش رو بفرست.\n"
                "برای فاصله‌ی زمانی دلخواه، بعدش با یه فاصله عدد دقیقه رو بنویس.\n"
                "مثال: -1001234567890 5"
            )
            return

        if lower == "help":
            await send_help(event, False)
            return

        if lower == "guide":
            await send_help(event, False, HELP_TEXT)
            return

        if lower == "farm now":
            if farm_chat_id is None:
                await event.reply("No farm group set yet. Use: farm on")
            else:
                await event.reply("Collecting now...")
                await meowie_farm_once()
            return

        if lower == "farm +5":
            farm_interval_seconds += 5 * 60
            await event.reply(f"Farm interval: {farm_interval_seconds/60:g} min")
            return

        if lower == "farm -5":
            farm_interval_seconds = max(5 * 60, farm_interval_seconds - 5 * 60)
            await event.reply(f"Farm interval: {farm_interval_seconds/60:g} min")
            return

        if lower.startswith("farm interval"):
            raw = text[len("farm interval"):].strip()
            seconds = parse_duration_seconds(raw)
            if seconds is None or seconds < 60:
                await event.reply("Example: farm interval 30  (minutes)  or  farm interval 2h  (hours)")
            else:
                farm_interval_seconds = seconds
                await event.reply(f"Farm interval set to {farm_interval_seconds/60:g} min")
            return

        if lower == "ping":
            start = datetime.now()
            msg = await event.reply("در حال سنجش...")
            delta = (datetime.now() - start).total_seconds() * 1000
            await msg.edit(f"پونگ! 🏓 {delta:.0f} ms")
            return

        if lower.startswith("meow on"):
            rest = text[len("meow on"):].strip()
            minutes = None
            if rest:
                try:
                    minutes = float(rest)
                except ValueError:
                    await event.reply("عدد دقیقه نامعتبره. مثال: meow on 5")
                    return
            await event.reply(await toggle_meow(admin_chat_id, minutes))
            return
        elif lower == "meow off":
            await event.reply(await untoggle_meow(admin_chat_id))
            return
        elif lower.startswith("afk on"):
            custom = text[len("afk on"):].strip()
            await event.reply(await toggle_afk(custom))
            return
        elif lower == "afk off":
            await event.reply(await untoggle_afk())
            return
        elif lower == "clock bio on":
            await event.reply(await toggle_clock_bio())
            return
        elif lower == "clock bio off":
            await event.reply(await untoggle_clock_bio())
            return
        elif lower == "clock name on":
            await event.reply(await toggle_clock_name())
            return
        elif lower == "clock name off":
            await event.reply(await untoggle_clock_name())
            return
        elif lower in ("clock font list", "clock bio font list", "clock name font list"):
            preview = "\n".join(
                f"{name}: {fancy_digits_preview(name)}" for name in DIGIT_FONTS
            )
            await event.reply(
                f"Available clock fonts:\n{preview}\n\nBio font: {bio_digit_font}\nName font: {name_digit_font}\n\n"
                "Set bio font:  clock bio font <name>\nSet name font: clock name font <name>"
            )
            return

        elif lower.startswith("clock bio font"):
            name = lower[len("clock bio font"):].strip()
            if name in DIGIT_FONTS:
                bio_digit_font = name
                await event.reply(f"Bio clock font set to '{name}' ✅ preview: {fancy_time(name)}")
            else:
                await event.reply("Unknown font. See the list with: clock font list")
            return

        elif lower.startswith("clock name font"):
            name = lower[len("clock name font"):].strip()
            if name in DIGIT_FONTS:
                name_digit_font = name
                await event.reply(f"Name clock font set to '{name}' ✅ preview: {fancy_time(name)}")
            else:
                await event.reply("Unknown font. See the list with: clock font list")
            return

    # --- پاسخ خودکار افک (فقط پیوی، و نه به بات‌ها) ---
    if not afk_enabled:
        return
    if not event.is_private:
        return
    sender = await event.get_sender()
    if sender and getattr(sender, "bot", False):
        return
    chat_id = event.chat_id
    if chat_id in afk_replied_chats:
        return
    afk_replied_chats.add(chat_id)
    await event.reply(afk_message)


# --- نجات خودکار «پیشی خیابونی» توی گروه‌های میو/فارم ---
@client.on(events.NewMessage(incoming=True))
async def rescue_watcher(event):
    if not rescue_enabled or not event.is_group:
        return
    # توی همه‌ی گروه‌هایی که اکانت عضوشه کار می‌کنه
    cid = event.chat_id
    msg = event.message
    if not msg.buttons or (cid, msg.id) in rescued_msg_ids:
        return
    if any(b.text and RESCUE_BUTTON_KEYWORD in b.text for b in _flat_buttons(msg)) and await _is_bot_msg(msg):
        rescued_msg_ids.add((cid, msg.id))
        asyncio.create_task(rescue_loop(cid, msg.id))


async def rescue_loop(chat_id: int, msg_id: int):
    """چند بار پشت‌سرهم روی «نجات پیشی خیابونی» می‌زنه (هر بار ممکنه شکست بخوره: «گربه توسط 🐭 کشته شد»)
    تا وقتی که دکمه از پیام بره، شانس‌ها تموم بشه، یا به سقف تعداد تلاش برسه"""
    for attempt in range(1, rescue_max_tries + 1):
        try:
            m = await client.get_messages(chat_id, ids=msg_id)
            if not m or not m.buttons:
                print("rescue: دکمه دیگه نیست، تموم شد")
                return
            btn = next((x for x in _flat_buttons(m) if x.text and RESCUE_BUTTON_KEYWORD in x.text), None)
            if not btn:
                return
            body = m.raw_text or ""
            if any(w in body for w in RESCUE_DONE_WORDS):
                print("rescue: پیام نهایی شد (نجات/مرگ)، تموم شد")
                return
            chances = re.search(r"شانس[^\n:：]*[:：]\s*([0-9۰-۹]+)", (m.raw_text or ""))
            if chances and int(chances.group(1).translate(_DIGITS)) == 0:
                print("rescue: شانسی باقی نمونده")
                return
            ans = await btn.click()
            reply_text = getattr(ans, "message", None) or ""
            print(f"rescue: تلاش {attempt}/{rescue_max_tries} {reply_text[:60]}")
        except Exception as e:
            print(f"rescue: {e}")
        await asyncio.sleep(rescue_delay_seconds)


# --- ذخیره‌ی عکس/ویدیوی نابودشونده (سکرت/یک‌بار‌مصرف) توی Saved Messages ---
@client.on(events.NewMessage(incoming=True))
async def ttl_watcher(event):
    if not ttl_save_enabled or not event.is_private or not event.message.media:
        return
    ttl = getattr(event.message.media, "ttl_seconds", None)
    if not ttl:
        return
    try:
        sender = await event.get_sender()
        name = " ".join(x for x in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if x) or "Unknown"
        uname = f"@{sender.username}" if getattr(sender, "username", None) else "no username"
        caption = f"🔒 Self-destruct media saved\nFrom: {name} ({uname}) — ID {event.sender_id}\nTimer: {ttl}s"
        tmpdir = tempfile.mkdtemp(prefix="ttl_")
        path = await event.message.download_media(file=tmpdir + os.sep)
        if not path:
            await client.send_message("me", caption + "\n⚠️ Could not download the media.")
            return
        await client.send_file("me", path, caption=caption)
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass
    except Exception as e:
        print(f"ttl: {e}")
        try:
            await client.send_message("me", f"⚠️ Failed to save a self-destruct media: {e}")
        except Exception:
            pass


# --- ذخیره‌ی پیام‌های پیوی (همه‌چیز: متن، ویس، استیکر، مدیا، ...) توی یک کانال ---
# اول خودِ پیام فرستاده میشه، و زیرش اسم، یوزرنیم، آیدی و ساعت فرستنده
@client.on(events.NewMessage(incoming=True))
async def dm_logger(event):
    if not dm_log_enabled or dm_log_target is None or not event.is_private:
        return
    # فقط پیام‌هایی که خودِ تلگرام فرستاده (کدهای ورود و ...) به کانال نمیره (امنیتی)
    if event.sender_id in TELEGRAM_SERVICE_IDS or event.sender_id == OWNER_ID:
        return
    msg = event.message
    try:
        sender = await event.get_sender()
        if sender is None:
            return
        name = " ".join(x for x in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if x) or "Unknown"
        uname = f"@{sender.username}" if getattr(sender, "username", None) else "—"
        bot_tag = " 🤖" if getattr(sender, "bot", False) else ""
        footer = (
            f"👤 {name}{bot_tag}\n🔗 {uname}\n🆔 {event.sender_id}\n"
            f"🕐 {_local_now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        sep = "┄┄┄┄┄┄┄┄┄┄"
        info = f"{sep}\n📩 پیام از:\n{footer}"
        text = msg.message or ""

        if not msg.media:
            if text:
                await client.send_message(dm_log_target, f"{text}\n\n{info}")
            return

        # مدیا: اول خودِ فایل (با ارجاع مستقیم)، اگه نشد (مثلاً تایمردار) دانلود و آپلود
        async def send_media(caption):
            try:
                await client.send_file(dm_log_target, msg.media, caption=caption)
            except Exception:
                tmpdir = tempfile.mkdtemp(prefix="dm_")
                path = await msg.download_media(file=tmpdir + os.sep)
                if not path:
                    raise
                await client.send_file(dm_log_target, path, caption=caption)
                try:
                    os.remove(path)
                    os.rmdir(tmpdir)
                except OSError:
                    pass

        no_caption = bool(msg.sticker or msg.video_note)
        cap = (text + "\n\n" if text else "") + info
        if getattr(msg.media, "ttl_seconds", None):
            cap = f"⏱ مدیای نابودشونده ({msg.media.ttl_seconds}s)\n" + cap
        try:
            if no_caption or len(cap) > 1000:
                await send_media(None)
                await client.send_message(dm_log_target, (text + "\n\n" if text else "") + info)
            else:
                await send_media(cap)
        except Exception:
            # انواعی که با send_file نمیشن (لوکیشن، نظرسنجی، مخاطب، ...): فوروارد + اطلاعات فرستنده
            await client.forward_messages(dm_log_target, msg)
            await client.send_message(dm_log_target, info)
    except Exception as e:
        print(f"dm-log: {e}")


async def main():
    global OWNER_ID, farm_task
    if HOSTED:
        await client.connect()
        if not await client.is_user_authorized():
            print("سشن معتبر نیست (خروج با کد 3)")
            sys.exit(3)
        for _h in (dm_logger, ttl_watcher):          # ذخیره‌ی پیوی و عکس نابودشونده توی این نسخه نیست
            client.remove_event_handler(_h)
    else:
        await client.start()
    me = await client.get_me()
    OWNER_ID = me.id
    load_settings()
    install_game_throttle()      # صف‌بندی دستورهای خودکار (ضد زندان)
    # بعد از ری‌استارت: فارم، چک غذا و میوها دوباره روشن میشن
    if resume_state.get("farm_on") and farm_chat_id is not None and farm_task is None:
        farm_task = asyncio.create_task(farm_loop())
        print(f"فارم بعد از ری‌استارت دوباره روشن شد (هر {farm_interval_seconds / 60:g} دقیقه، گروه {farm_chat_id})")
    if feed_interval_seconds and farm_chat_id is not None and feed_enabled:
        start_feed_loop()
        print(f"چک غذای زمان‌دار دوباره روشن شد (هر {feed_interval_seconds / 60:g} دقیقه)")
    for _c, _secs in (resume_state.get("meow") or {}).items():
        if int(_c) not in active_meow_tasks:
            active_meow_tasks[int(_c)] = asyncio.create_task(meow_loop(int(_c), float(_secs)))
            meow_seconds[int(_c)] = float(_secs)
            print(f"میو توی چت {_c} دوباره روشن شد")
    if fish_cfg["auto"]:
        start_fish_loop()
        print(f"🎣 ماهی‌گیری خودکار روشنه (هر {fish_cfg['interval'] / 60:g} دقیقه)")
    asyncio.create_task(settings_saver_loop())
    if HOSTED:
        asyncio.create_task(menu_cmd_loop())
    if factory_enabled and _factory_target_chat() is not None:
        start_factory_loop()
    if miner_cfg["enabled"] and _miner_chat() is not None:
        start_miner_loop()
    if bank_cfg.get("tr_enabled") and bank_cfg.get("tr_card"):
        start_bank_transfer_loop()
    for _c in list(say_cfg):
        start_say(int(_c))
    ensure_job_scheduler()
    print("سلف‌بات اجرا شد. دستورات:")
    print("  meow on [دقیقه] / meow off")
    print("  clock bio on/off | clock name on/off")
    print("  afk on <پیام> / afk off")
    print("  farm on / farm off")
    print("  help  → show full command list")
    if ADMIN_IDS:
        print(f"  کنترل مدیریتی برای {len(ADMIN_IDS)} اکانت مدیر فعاله")
    else:
        print("  ⚠️ ADMIN_IDS خالیه — قابلیت مدیریتی غیرفعاله تا آیدی مدیر رو اضافه کنی")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
