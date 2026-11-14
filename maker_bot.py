"""
ربات سلف‌ساز (Self Maker)
هر کاربر داخل ربات لاگین می‌کنه و یه نسخه‌ی مخصوص خودش از mozi.py (سلف) روی سرور اجرا میشه.
- هر سلف یه پروسه‌ی جداست و با api_id / api_hash خودِ کاربر اجرا میشه
  (ربات می‌تونه بعد از تأیید کدِ my.telegram.org خودش api رو بگیره، یا کاربر دستی می‌فرسته)
- اشتراک زمان‌دار: تریال رایگان + کد تمدید (ادمین کد می‌سازه)
- اجبار عضویت توی کانال (اختیاری)
- نسخه‌ی ساخته‌شده «ذخیره‌ی پیام پیوی» و «ذخیره‌ی عکس/ویدیوی نابودشونده» نداره.

نصب:
    pip install -r requirements.txt

اجرا (متغیرهای محیطی):
    MAKER_BOT_TOKEN      توکن ربات از @BotFather
    MAKER_API_ID         api_id خودِ ربات سازنده (فقط برای لاگین ربات؛ از my.telegram.org)
    MAKER_API_HASH       api_hash خودِ ربات سازنده
    MAKER_ADMINS         آیدی عددی ادمین‌ها با ویرگول: 123,456  (ادمین‌ها اشتراکشون نامحدوده)
    MAKER_MAX_USERS      حداکثر تعداد سلف (پیش‌فرض 50)
    MAKER_TRIAL_DAYS     روزهای تریال رایگان برای هر کاربر جدید (پیش‌فرض 3؛ 0 = بدون تریال)
    MAKER_FORCE_CHANNEL  یوزرنیم کانال برای اجبار عضویت، مثل @mychannel (ربات باید ادمین کانال باشه)
    MAKER_SUPPORT        یوزرنیم پشتیبانی برای خرید اشتراک، مثل @admin
    MAKER_SELF_SCRIPT    مسیر mozi.py (پیش‌فرض: mozi.py کنار همین فایل)
    MAKER_DATA           پوشه‌ی داده‌ها (پیش‌فرض: maker_data)

    python maker_bot.py

⚠️ سشن و api هر کاربر دسترسی کامل به اکانتشه؛ فقط روی سروری اجرا کن که بهش اعتماد داری
و پوشه‌ی maker_data رو به کسی نده. ربات خودش توی /start به کاربر می‌گه چی ذخیره میشه.
"""
import asyncio
import http.cookiejar
import json
import os
import random
import re
import secrets
import shutil
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from telethon import Button, TelegramClient, errors, events, types
from telethon.extensions import markdown as tl_markdown

# ====== تنظیمات (یا با متغیر محیطی) ======
BOT_TOKEN = os.environ.get("MAKER_BOT_TOKEN", "8299449752:AAHTv6oq5Nu7VyJPEyRYQ-Bjy_tuB-7u944")          # <-- توکن ربات
API_ID = int(os.environ.get("MAKER_API_ID", "30213358") or 0)      # <-- api_id خودِ ربات سازنده
API_HASH = os.environ.get("MAKER_API_HASH", "74b35d20203b6670dafcc595f81c4e46")             # <-- api_hash خودِ ربات سازنده
MAKER_ADMINS = [int(x) for x in os.environ.get("MAKER_ADMINS", "").split(",") if x.strip()]
MAX_USERS = int(os.environ.get("MAKER_MAX_USERS", "20"))
TRIAL_DAYS = int(os.environ.get("MAKER_TRIAL_DAYS", "5"))
FORCE_CHANNEL = os.environ.get("MAKER_FORCE_CHANNEL", "@PERSIIA_TEAM").strip()
SUPPORT = os.environ.get("MAKER_SUPPORT", "@naji77k").strip()
SELF_SCRIPT = os.path.abspath(os.environ.get("MAKER_SELF_SCRIPT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mozi.py")))
DATA_DIR = Path(os.environ.get("MAKER_DATA", "maker_data")).resolve()
LOGIN_TIMEOUT = 900          # ثانیه؛ بعدش لاگین نیمه‌کاره لغو میشه
LOG_MAX_BYTES = 2_000_000
MT_BASE = "https://my.telegram.org"
# ==========================================

DATA_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)
except OSError:
    pass
USERS_FILE = DATA_DIR / "users.json"
SUBS_FILE = DATA_DIR / "subs.json"
CODES_FILE = DATA_DIR / "codes.json"
EMOJI_FILE = DATA_DIR / "emoji.json"

bot = TelegramClient(str(DATA_DIR / "maker_bot"), API_ID, API_HASH)
users: dict = {}      # {uid: {"name","created","enabled","invalid","api_id","api_hash","warned","expired"}}
subs: dict = {}       # {uid: زمان انقضا (timestamp)}؛ بعد از حذف سلف هم می‌مونه که با حذف/ساخت دوباره تریال تکرار نشه
codes: dict = {}      # {کد: تعداد روز}
procs: dict = {}      # {uid: {"proc","task","stop","started"}}
logins: dict = {}     # {uid: {...مرحله‌ی لاگین...}}
redeeming: set = set()
emoji_map: dict = {}  # {ایموجی معمولی (بدون FE0F): آیدی ایموجی پرمیوم}

# ---------------- ایموجی پرمیوم (custom emoji) ----------------
# هر ایموجی معمولی که توی emoji_map باشه، توی همه‌ی پیام‌های ربات با ایموجی پرمیوم جایگزین میشه.
# اگه تلگرام اجازه نده (ربات صلاحیت نداشته باشه)، خودکار با ایموجی معمولی ارسال میشه.
_VS = "️"
_orig_send, _orig_edit = bot.send_message, bot.edit_message


def emoji_norm(e: str) -> str:
    return (e or "").replace(_VS, "").strip()


def u16slice(s: str, off: int, ln: int) -> str:
    return s.encode("utf-16-le")[off * 2:(off + ln) * 2].decode("utf-16-le", "ignore")


def apply_premium(text: str, ents):
    if not emoji_map or not text:
        return list(ents or [])
    keys = sorted(emoji_map, key=len, reverse=True)
    rx = re.compile("|".join(re.escape(k) + _VS + "?" for k in keys))
    out = list(ents or [])
    for m in list(rx.finditer(text))[:100]:
        doc = emoji_map.get(emoji_norm(m.group(0)))
        if not doc:
            continue
        out.append(types.MessageEntityCustomEmoji(offset=len(text[:m.start()].encode("utf-16-le")) // 2,
                                                  length=len(m.group(0).encode("utf-16-le")) // 2, document_id=int(doc)))
    return out


def _premium_prep(message, k):
    """(متن، kwargs) ← (متن ساده، kwargs با entityهای مارک‌داون + ایموجی پرمیوم) یا None اگه کاری لازم نیست"""
    if not emoji_map or not isinstance(message, str) or k.get("formatting_entities") is not None:
        return None
    pm = k.get("parse_mode", Ellipsis)
    if pm is Ellipsis or pm in ("md", "markdown"):
        text, ents = tl_markdown.parse(message)
    elif pm is None:
        text, ents = message, []
    else:
        return None
    new = apply_premium(text, ents)
    if len(new) == len(ents):
        return None
    k2 = dict(k)
    k2["formatting_entities"], k2["parse_mode"] = new, None
    return text, k2


async def send_message_pe(entity, message="", *a, **k):
    prep = _premium_prep(message, k)
    if prep:
        try:
            return await _orig_send(entity, prep[0], *a, **prep[1])
        except errors.FloodWaitError:
            raise
        except Exception as e:
            print(f"ایموجی پرمیوم قبول نشد؛ با ایموجی معمولی می‌فرستم: {str(e)[:80]}")
    return await _orig_send(entity, message, *a, **k)


async def edit_message_pe(entity, message=None, text=None, *a, **k):
    prep = _premium_prep(text, k)
    if prep:
        try:
            return await _orig_edit(entity, message, prep[0], *a, **prep[1])
        except (errors.FloodWaitError, errors.MessageNotModifiedError):
            raise
        except Exception as e:
            print(f"ایموجی پرمیوم قبول نشد؛ با ایموجی معمولی ویرایش می‌کنم: {str(e)[:80]}")
    return await _orig_edit(entity, message, text, *a, **k)


bot.send_message, bot.edit_message = send_message_pe, edit_message_pe


def parse_emoji_input(text: str, entities):
    """از پیام ادمین جفت‌های {ایموجی: آیدی} درمیاره: یا خودِ ایموجی پرمیوم، یا «👑 5368324170671202286» (خط به خط)"""
    pairs = {}
    for ent in entities or []:
        if isinstance(ent, types.MessageEntityCustomEmoji):
            ch = emoji_norm(u16slice(text, ent.offset, ent.length))
            if ch:
                pairs[ch] = str(ent.document_id)
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(\S+)\s+(\d{8,25})\s*$", line)
        if m:
            pairs[emoji_norm(m.group(1))] = m.group(2)
    return {k: v for k, v in pairs.items() if k and len(k) <= 8 and not any(c.isalnum() for c in k)}


def used_emojis(limit: int = 45) -> str:
    """ایموجی‌هایی که خودِ ربات توی متن‌ها و پیام‌هاش استفاده می‌کنه (برای انتخاب اینکه کدوم رو پرمیوم کنی)"""
    from collections import Counter
    try:
        code = open(__file__, encoding="utf-8").read()
    except OSError:
        return "—"
    cnt = Counter(emoji_norm(c) for c in re.findall(r"[⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF]", code))
    cnt.pop("", None)
    return " ".join(e for e, _ in cnt.most_common(limit))


def adm_emoji_view():
    lines = "\n".join(f"{k} → `{v}`" for k, v in emoji_map.items()) or "هنوز چیزی ثبت نشده."
    text = ("🎨 ایموجی پرمیوم\n\n" + lines +
            "\n\nهر ایموجی معمولی که اینجا ثبت کنی توی همه‌ی پیام‌های ربات با ایموجی پرمیوم نشون داده میشه. "
            "اگه تلگرام به ربات اجازه نده، همون ایموجی معمولی میاد. (روی خودِ دکمه‌ها ایموجی پرمیوم نمیشه.)")
    kb = [[Button.inline("➕ افزودن / تغییر", b"adm:emoadd")],
          [Button.inline("📋 ایموجی‌های ربات", b"adm:emoused")]]
    if emoji_map:
        kb.append([Button.inline("🗑 حذف یکی", b"adm:emodel"), Button.inline("🧹 حذف همه", b"adm:emoreset")])
    kb.append([Button.inline("🏠 پنل", b"adm:home")])
    return text, kb

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

WELCOME = (
    "👋 به ربات سلف‌ساز خوش اومدی!\n\n"
    "اینجا می‌تونی سلف (یوزربات) مخصوص خودت رو بسازی: میو و فارم خودکار، ماهی‌گیری و غذا دادن، ماینر، بانک، "
    "کارخونه، ساعت روی بیو، افک، مترجم، ماشین‌حساب، آزادسازی از زندان و ...\n\n"
    "🔐 برای ساخت باید به اکانتت لاگین کنی: شماره ← api_id و api_hash خودت ← کد تلگرام ← رمز دومرحله‌ای (اگه داری).\n"
    "• سلف فقط روی اکانت خودت و با api خودت کار می‌کنه و دستورها رو از خودِ اکانتت می‌نویسی.\n"
    "• این نسخه پیام‌های پیوی و عکس/ویدیوی نابودشونده رو ذخیره نمی‌کنه.\n"
    "• سشن و api روی سرور ربات نگه داشته میشه؛ هر وقت خواستی با «حذف سلف و خروج» کامل قطع و پاک میشه "
    "(یا از تلگرام: تنظیمات ← دستگاه‌ها).\n"
    "• رمز دومرحله‌ای ذخیره نمیشه.\n"
    "• شماره‌ات، اسم و آیدی اکانتت برای مدیریت اشتراک و پشتیبانی پیش ادمین ربات ذخیره میشه و با «حذف سلف و خروج» پاک میشه.\n"
    + (f"🎁 {TRIAL_DAYS} روز اشتراک رایگان برای شروع." if TRIAL_DAYS > 0 else "")
)
HELP_USER = (
    "📖 طرز استفاده\n"
    "از خودِ اکانتت (ترجیحاً توی Saved Messages) بنویس:\n"
    "• راهنما  یا  help — فهرست بخش‌ها\n"
    "• راهنما ماینر — راهنمای هر بخش\n"
    "• meow on 5 — میو هر ۵ دقیقه\n"
    "• farm on — فارم میویی\n"
    "• guide — راهنمای کامل\n\n"
    "دستورها هم فارسی کار می‌کنن هم انگلیسی (مثلاً «میو روشن»)."
)


# ---------------- ذخیره‌ی داده‌ها ----------------
def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_all():
    global users, subs, codes, emoji_map
    users = _load(USERS_FILE, {})
    subs = _load(SUBS_FILE, {})
    codes = _load(CODES_FILE, {})
    emoji_map = _load(EMOJI_FILE, {})


def save_users():
    _save(USERS_FILE, users)


def save_subs():
    _save(SUBS_FILE, subs)


def save_emoji():
    _save(EMOJI_FILE, emoji_map)


def save_codes():
    _save(CODES_FILE, codes)


def user_dir(uid: int) -> Path:
    d = DATA_DIR / "users" / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def wipe_session_files(uid: int):
    for f in user_dir(uid).glob("self.session*"):
        try:
            f.unlink()
        except OSError:
            pass


def fmt_dur(sec: float) -> str:
    sec = max(0, int(sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = ([f"{d} روز"] if d else []) + ([f"{h} ساعت"] if h else []) + [f"{m} دقیقه"]
    return " و ".join(parts)


def is_admin(uid: int) -> bool:
    return uid in MAKER_ADMINS


def api_of(uid: int):
    u = users.get(str(uid)) or {}
    L = logins.get(uid) or {}
    return int(u.get("api_id") or L.get("api_id") or API_ID), (u.get("api_hash") or L.get("api_hash") or API_HASH)


# ---------------- اشتراک ----------------
def is_expired(uid: int) -> bool:
    return (not is_admin(uid)) and subs.get(str(uid), 0) < time.time()


def sub_text(uid: int) -> str:
    if is_admin(uid):
        return "♾ نامحدود (ادمین)"
    left = subs.get(str(uid), 0) - time.time()
    return f"⏳ {fmt_dur(left)} مونده" if left > 0 else "⛔️ تموم شده"


def renew_hint() -> str:
    return "برای تمدید، کد اشتراک رو از " + (SUPPORT if SUPPORT else "ادمین") + " بگیر و بزن «🎟 تمدید با کد»."


def extend(uid: int, days: float):
    k = str(uid)
    subs[k] = max(subs.get(k, 0), time.time()) + days * 86400
    save_subs()
    u = users.get(k)
    if u:
        u["warned"] = False
        u["expired"] = False
        if subs[k] > time.time():
            u["enabled"] = True
            save_users()
            if not u.get("invalid") and not is_running(uid):
                start_self(uid)
            return
        save_users()


def new_code() -> str:
    a = string.ascii_uppercase + string.digits
    return "MEOW-" + "".join(random.SystemRandom().choice(a) for _ in range(4)) + "-" + "".join(random.SystemRandom().choice(a) for _ in range(4))


def redeem_code(uid: int, code: str) -> str:
    code = re.sub(r"\s", "", code.translate(_FA)).upper()
    days = codes.pop(code, None)
    if not days:
        return "❌ کد نامعتبره یا قبلاً استفاده شده."
    save_codes()
    extend(uid, days)
    return f"✅ {days} روز به اشتراکت اضافه شد.\n{sub_text(uid)}"


# ---------------- اجبار عضویت ----------------
async def joined(uid: int) -> bool:
    if not FORCE_CHANNEL or is_admin(uid):
        return True
    try:
        p = await bot.get_permissions(FORCE_CHANNEL, uid)
        return p is not None
    except errors.UserNotParticipantError:
        return False
    except Exception as e:
        print(f"بررسی عضویت ناموفق (ربات باید ادمین کانال باشه): {e}")
        return True


def join_kb():
    return [[Button.url("📢 عضویت در کانال", "https://t.me/" + FORCE_CHANNEL.lstrip("@"))],
            [Button.inline("✅ عضو شدم", b"chk")]]


JOIN_TEXT = "برای استفاده از ربات اول توی کانال ما عضو شو، بعد «✅ عضو شدم» رو بزن:"


# ---------------- گرفتن api از my.telegram.org ----------------
class MTError(Exception):
    pass


class MyTelegram:
    """لاگین به my.telegram.org با کدی که تلگرام به خودِ کاربر می‌فرسته و گرفتن/ساخت api_id و api_hash"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            ("Origin", MT_BASE), ("Referer", MT_BASE + "/auth"), ("X-Requested-With", "XMLHttpRequest"),
            ("Accept-Language", "en-US,en;q=0.9")]

    def _req(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self.op.open(urllib.request.Request(MT_BASE + path, data=body), timeout=25) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            raise MTError(f"HTTP {e.code}: " + e.read().decode("utf-8", "ignore")[:100])
        except Exception as e:
            raise MTError(str(e)[:100])

    def send_code(self, phone: str) -> str:
        t = self._req("/auth/send_password", {"phone": phone})
        try:
            return json.loads(t)["random_hash"]
        except Exception:
            raise MTError(t.strip()[:120] or "پاسخ نامعتبر")

    def login(self, phone: str, random_hash: str, code: str):
        t = self._req("/auth/login", {"phone": phone, "random_hash": random_hash, "password": code})
        if t.strip() != "true" or not any(c.name == "stel_token" for c in self.jar):
            raise MTError(t.strip()[:120] or "کد اشتباهه")

    @staticmethod
    def parse_app(html: str):
        mi = re.search(r"api_id[^<]*</label>.*?<strong>\s*(\d{4,12})\s*</strong>", html, re.S | re.I)
        mh = re.search(r"api_hash[^<]*</label>.*?>\s*([0-9a-fA-F]{32})\s*<", html, re.S | re.I)
        if mi and mh:
            return int(mi.group(1)), mh.group(1).lower()
        return None

    def get_or_create_app(self):
        html = self._req("/apps")
        got = self.parse_app(html)
        if got:
            return got
        m = (re.search(r'<input[^>]*name="hash"[^>]*value="([^"]+)"', html)
             or re.search(r'<input[^>]*value="([^"]+)"[^>]*name="hash"', html))
        if not m:
            raise MTError("صفحه‌ی ساخت اپ پیدا نشد")
        short = "".join(random.choice(string.ascii_lowercase) for _ in range(10))
        res = self._req("/apps/create", {"hash": m.group(1), "app_title": "Meow " + short[:5], "app_shortname": short,
                                         "app_url": "", "app_platform": "android", "app_desc": ""})
        if res.strip() and "error" in res.lower():
            raise MTError(res.strip()[:120])
        got = self.parse_app(self._req("/apps"))
        if not got:
            raise MTError("ساخت اپ انجام نشد")
        return got


# ---------------- مدیریت پروسه‌ی سلف ----------------
def is_running(uid: int) -> bool:
    st = procs.get(uid)
    return bool(st and st.get("proc") and st["proc"].returncode is None)


def kill_stale(uid: int):
    """اگه ربات سازنده کرش کرده بود و پروسه‌ی قدیمیِ این کاربر هنوز زنده‌ست، قبل از اجرای جدید می‌کشیمش"""
    pf = user_dir(uid) / "self.pid"
    try:
        pid = int(pf.read_text().strip())
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore")
        if os.path.basename(SELF_SCRIPT) in cmd:
            os.kill(pid, 15)
            time.sleep(1)
    except (OSError, ValueError):
        pass


async def supervise(uid: int):
    st = procs[uid]
    delay = 5
    try:
        while True:
            d = user_dir(uid)
            aid, ahash = api_of(uid)
            env = os.environ.copy()
            for k in ("MAKER_BOT_TOKEN", "MAKER_API_HASH", "MAKER_API_ID"):
                env.pop(k, None)           # سلف نیازی به اطلاعات ربات سازنده نداره
            env.update({"MOZI_HOSTED": "1", "MOZI_API_ID": str(aid), "MOZI_API_HASH": ahash,
                        "MOZI_SESSION": str(d / "self"), "MOZI_ADMINS": ""})
            logf = d / "self.log"
            mode = "wb" if logf.exists() and logf.stat().st_size > LOG_MAX_BYTES else "ab"
            with open(logf, mode) as lf:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-u", SELF_SCRIPT, cwd=str(d), env=env,
                    stdout=lf, stderr=asyncio.subprocess.STDOUT)
                st["proc"], st["started"] = proc, time.time()
                (d / "self.pid").write_text(str(proc.pid))
                rc = await proc.wait()
            if st.get("stop"):
                return
            if rc == 3:       # سشن نامعتبر / خروج از اکانت
                if str(uid) in users:
                    users[str(uid)]["enabled"] = False
                    users[str(uid)]["invalid"] = True
                    save_users()
                try:
                    await bot.send_message(uid, "⚠️ سشن سلفت دیگه معتبر نیست (احتمالاً از «دستگاه‌ها» خارج شدی). "
                                                "برای ساخت دوباره: /start ← حذف سلف و خروج ← ساخت سلف", buttons=main_kb(uid))
                except Exception:
                    pass
                return
            ran = time.time() - st["started"]
            delay = 5 if ran > 300 else min(delay * 2, 120)
            print(f"[{uid}] سلف با کد {rc} خارج شد؛ {delay} ثانیه‌ی دیگه دوباره اجرا میشه")
            await asyncio.sleep(delay)
    finally:
        if procs.get(uid) is st:
            procs.pop(uid, None)


def start_self(uid: int):
    if uid in procs and procs[uid].get("task") and not procs[uid]["task"].done():
        return
    kill_stale(uid)
    st = {"stop": False, "proc": None, "started": time.time()}
    procs[uid] = st
    st["task"] = asyncio.create_task(supervise(uid))


async def stop_self(uid: int):
    st = procs.get(uid)
    if not st:
        return
    st["stop"] = True
    proc = st.get("proc")
    if proc and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    t = st.get("task")
    if t:
        try:
            await asyncio.wait_for(t, 5)
        except Exception:
            pass
    procs.pop(uid, None)


async def stop_all():
    for uid in list(procs):
        await stop_self(uid)


# ---------------- دکمه‌ها ----------------
def main_kb(uid: int):
    if str(uid) in users:
        run = is_running(uid)
        rows = [[Button.inline("📊 وضعیت", b"status"), Button.inline("⏸ خاموش" if run else "▶️ روشن", b"toggle")],
                [Button.inline("📖 راهنما", b"help"), Button.inline("📜 لاگ", b"log")],
                [Button.inline("🎟 تمدید با کد", b"redeem")],
                [Button.inline("🗑 حذف سلف و خروج", b"del")]]
    else:
        rows = [[Button.inline("➕ ساخت سلف", b"create")], [Button.inline("🎟 کد اشتراک", b"redeem")]]
    if is_admin(uid):
        rows.append([Button.inline("👑 پنل مدیریت", b"adm:home")])
    return rows


def api_choice_kb():
    return [[Button.inline("🤖 دریافت خودکار api (پیشنهادی)", b"api:auto")],
            [Button.inline("✍️ ارسال دستی api_id و api_hash", b"api:manual")],
            [Button.inline("❌ انصراف", b"cancel")]]


def keypad(code: str, length: int):
    shown = " ".join(list(code) + ["•"] * (length - len(code)))
    rows = [[Button.inline(str(d), f"k:{d}".encode()) for d in grp] for grp in ((1, 2, 3), (4, 5, 6), (7, 8, 9))]
    rows.append([Button.inline("⌫", b"k:del"), Button.inline("0", b"k:0"), Button.inline("✅", b"k:ok")])
    rows.append([Button.inline("❌ انصراف", b"cancel")])
    return shown, rows


def status_text(uid: int) -> str:
    u = users.get(str(uid))
    if not u:
        return "هنوز سلفی نساخته‌ای."
    if is_running(uid):
        s = f"🟢 روشن (از {fmt_dur(time.time() - procs[uid]['started'])} پیش)"
    elif u.get("invalid"):
        s = "🔴 خاموش — سشن نامعتبر شده، دوباره بساز"
    elif is_expired(uid):
        s = "🔴 خاموش — اشتراکت تموم شده"
    else:
        s = "🔴 خاموش"
    return (f"📊 وضعیت سلف\n{s}\nاکانت: {u.get('name', '—')}\nاشتراک: {sub_text(uid)}\n"
            f"ساخته شده: {time.strftime('%Y-%m-%d %H:%M', time.localtime(u.get('created', 0)))}")


# ---------------- لاگین ----------------
def norm_phone(s: str):
    d = re.sub(r"[^\d+]", "", (s or "").translate(_FA))
    if d.startswith("+"):
        digits = d[1:]
    elif d.startswith("00"):
        digits = d[2:]
    elif d.startswith("09") and len(d) == 11:
        digits = "98" + d[1:]
    else:
        digits = d
    return "+" + digits if digits.isdigit() and 8 <= len(digits) <= 15 else None


def parse_api(text: str, L: dict):
    """api_id و api_hash رو از متن (به هر ترتیب و در یک یا چند پیام) درمیاره"""
    t = text.translate(_FA)
    mh = re.search(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])", t)
    if mh:
        L["api_hash"] = mh.group(0).lower()
        t = t.replace(mh.group(0), " ")
    mi = re.search(r"(?<![0-9a-zA-Z])\d{4,12}(?![0-9a-zA-Z])", t)
    if mi:
        L["api_id"] = int(mi.group(0))


async def drop_login(uid: int, remove_session: bool = True):
    L = logins.pop(uid, None)
    if L and L.get("client"):
        try:
            await L["client"].disconnect()
        except Exception:
            pass
    if remove_session and str(uid) not in users:
        wipe_session_files(uid)


async def send_login_code(uid: int):
    L = logins[uid]
    d = user_dir(uid)
    wipe_session_files(uid)
    c = TelegramClient(str(d / "self"), int(L["api_id"]), L["api_hash"])
    await c.connect()
    try:
        sent = await c.send_code_request(L["phone"])
    except Exception:
        await c.disconnect()
        raise
    length = getattr(getattr(sent, "type", None), "length", None) or 5
    L.update(stage="code", client=c, hash=sent.phone_code_hash, code="", len=int(length), ts=time.time(), busy=False, tries=0)
    shown, kb = keypad("", L["len"])
    m = await bot.send_message(uid, "📩 کد تلگرامی که برات اومد رو با این دکمه‌ها وارد کن "
                                    "(از دکمه‌ها استفاده کن، نه تایپ؛ تلگرام کدِ تایپ‌شده رو باطل می‌کنه):\n\n" + shown, buttons=kb)
    L["msg"] = m.id


_ERR = [
    (errors.PhoneNumberInvalidError, "شماره نامعتبره؛ با کد کشور بنویس (مثلاً +989123456789)."),
    (errors.PhoneNumberBannedError, "این شماره توسط تلگرام مسدود شده."),
    (errors.PhoneNumberFloodError, "تلگرام موقتاً محدودت کرده؛ بعداً امتحان کن."),
]


async def begin_telethon(uid: int):
    """بعد از داشتن api: ارسال کد ورود تلگرام"""
    L = logins.get(uid)
    if not L:
        return
    await bot.send_message(uid, "⏳ در حال ارسال کد ورود تلگرام ...", buttons=Button.clear())
    try:
        await send_login_code(uid)
    except errors.ApiIdInvalidError:
        L.update(stage="api_choice", api_id=None, api_hash=None)
        await bot.send_message(uid, "❌ api_id / api_hash نامعتبره. دوباره امتحان کن:", buttons=api_choice_kb())
    except Exception as e:
        await drop_login(uid)
        msg = next((m for cls, m in _ERR if isinstance(e, cls)), None)
        if isinstance(e, errors.FloodWaitError):
            msg = f"⏳ تلگرام گفت {e.seconds} ثانیه صبر کن."
        await bot.send_message(uid, "❌ " + (msg or f"ارسال کد ناموفق بود: {str(e)[:150]}"), buttons=main_kb(uid))


async def finish_login(uid: int):
    L = logins.get(uid)
    c = L["client"]
    me = await c.get_me()
    await c.disconnect()
    aid, ahash = L["api_id"], L["api_hash"]
    logins.pop(uid, None)
    try:
        os.chmod(user_dir(uid) / "self.session", 0o600)
    except OSError:
        pass
    name = " ".join(x for x in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if x) or str(getattr(me, "id", ""))
    users[str(uid)] = {"name": name, "created": time.time(), "enabled": True, "invalid": False, "phone": L.get("phone", ""),
                       "api_id": int(aid), "api_hash": ahash, "warned": False, "expired": False}
    save_users()
    if str(uid) not in subs:                       # تریال فقط یک بار برای هر کاربر
        subs[str(uid)] = time.time() + TRIAL_DAYS * 86400 if TRIAL_DAYS > 0 else 0
        save_subs()
    if is_expired(uid):
        users[str(uid)].update(enabled=False, expired=True)
        save_users()
        await bot.send_message(uid, f"✅ ورود انجام شد ولی اشتراکت تموم شده، پس سلف روشن نشد.\n{renew_hint()}", buttons=main_kb(uid))
        return
    start_self(uid)
    await bot.send_message(uid, f"✅ سلف ساخته شد و روشن شد!\nاکانت: {name}\nاشتراک: {sub_text(uid)}\n\n" + HELP_USER, buttons=main_kb(uid))


async def submit_code(uid: int):
    L = logins.get(uid)
    if not L or L["busy"] or L["stage"] != "code":
        return
    L["busy"] = True
    c = L["client"]
    try:
        await c.sign_in(L["phone"], L["code"], phone_code_hash=L["hash"])
    except errors.SessionPasswordNeededError:
        L["stage"] = "password"
        await bot.send_message(uid, "🔐 رمز دومرحله‌ای (Two-Step Verification) داری. رمزت رو بفرست؛ پیامت همون لحظه پاک میشه و ذخیره نمیشه.")
        return
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
        L["code"] = ""
        shown, kb = keypad("", L["len"])
        await bot.edit_message(uid, L["msg"], "❌ کد اشتباه بود. دوباره وارد کن:\n\n" + shown, buttons=kb)
        return
    except errors.PhoneCodeExpiredError:
        await drop_login(uid)
        await bot.send_message(uid, "⌛ کد منقضی (یا باطل) شد. دوباره از «ساخت سلف» شروع کن و کد رو فقط با دکمه‌ها وارد کن.", buttons=main_kb(uid))
        return
    except errors.FloodWaitError as e:
        await drop_login(uid)
        await bot.send_message(uid, f"⏳ تلگرام گفت {e.seconds} ثانیه صبر کن و دوباره امتحان کن.", buttons=main_kb(uid))
        return
    except Exception as e:
        await drop_login(uid)
        await bot.send_message(uid, f"❌ ورود ناموفق بود: {str(e)[:150]}", buttons=main_kb(uid))
        return
    finally:
        if uid in logins:
            logins[uid]["busy"] = False
    await finish_login(uid)


async def submit_password(uid: int, pw: str):
    L = logins.get(uid)
    if not L or L["busy"] or L["stage"] != "password":
        return
    L["busy"] = True
    try:
        await L["client"].sign_in(password=pw)
    except errors.PasswordHashInvalidError:
        L["tries"] += 1
        if L["tries"] >= 5:
            await drop_login(uid)
            await bot.send_message(uid, "❌ چند بار رمز اشتباه بود؛ لغو شد.", buttons=main_kb(uid))
        else:
            await bot.send_message(uid, "❌ رمز اشتباهه. دوباره بفرست:")
        return
    except errors.FloodWaitError as e:
        await drop_login(uid)
        await bot.send_message(uid, f"⏳ {e.seconds} ثانیه صبر کن و دوباره امتحان کن.", buttons=main_kb(uid))
        return
    except Exception as e:
        await drop_login(uid)
        await bot.send_message(uid, f"❌ ورود ناموفق بود: {str(e)[:150]}", buttons=main_kb(uid))
        return
    finally:
        if uid in logins:
            logins[uid]["busy"] = False
    await finish_login(uid)


async def mt_start(uid: int):
    """ارسال کد my.telegram.org به تلگرام کاربر"""
    L = logins.get(uid)
    if not L or L.get("busy"):
        return
    L["busy"] = True
    try:
        mt = MyTelegram()
        rh = await asyncio.to_thread(mt.send_code, L["phone"])
    except MTError as e:
        L["stage"] = "api_choice"
        await bot.send_message(uid, f"❌ دریافت خودکار شروع نشد ({e}).\nمی‌تونی دستی بفرستی:", buttons=api_choice_kb())
        return
    finally:
        L["busy"] = False
    L.update(stage="mt_code", mt=mt, mt_hash=rh, mt_tries=0, ts=time.time())
    await bot.send_message(
        uid, "🤖 تلگرام یه پیام «Web login code» از اکانت رسمی Telegram برات فرستاد (توی چت Telegram).\n"
             "اون کد رو اینجا بفرست. اگه تلگرام کدِ تایپ‌شده رو باطل کرد، بین حرف‌ها فاصله یا خط‌تیره بذار "
             "(مثلاً a-B-c-1-2-3)؛ من حذفش می‌کنم. پیامت همون لحظه پاک میشه.\n"
             "ℹ️ بعدش یه کد دیگه‌ی ورود هم میاد که با دکمه‌ها وارد می‌کنی.",
        buttons=[[Button.inline("✍️ ترجیح میدم دستی بفرستم", b"api:manual")], [Button.inline("❌ انصراف", b"cancel")]])


async def mt_finish(uid: int, code: str):
    L = logins.get(uid)
    if not L or L.get("busy") or L["stage"] != "mt_code":
        return
    L["busy"] = True
    try:
        mt = L["mt"]
        await asyncio.to_thread(mt.login, L["phone"], L["mt_hash"], code)
        aid, ahash = await asyncio.to_thread(mt.get_or_create_app)
    except MTError as e:
        L["mt_tries"] += 1
        if L["mt_tries"] >= 3:
            L["stage"] = "api_choice"
            await bot.send_message(uid, f"❌ دریافت خودکار نشد ({e}).", buttons=api_choice_kb())
        else:
            await bot.send_message(uid, f"❌ کد اشتباه یا منقضی بود ({e}). دوباره بفرست:")
        return
    finally:
        L["busy"] = False
    L["api_id"], L["api_hash"] = aid, ahash
    L["mt"] = None
    await bot.send_message(uid, f"✅ api دریافت شد (api_id: {aid}) و برای سلفت ذخیره میشه.")
    await begin_telethon(uid)


async def delete_self(uid: int):
    """سلف رو خاموش می‌کنه، از تلگرام خارج میشه (سشن باطل میشه) و فایل‌ها پاک میشن"""
    await stop_self(uid)
    d = user_dir(uid)
    if (d / "self.session").exists():
        aid, ahash = api_of(uid)
        c = TelegramClient(str(d / "self"), aid, ahash)
        try:
            await c.connect()
            if await c.is_user_authorized():
                await c.log_out()
        except Exception as e:
            print(f"[{uid}] خروج از اکانت: {e}")
        finally:
            try:
                await c.disconnect()
            except Exception:
                pass
    shutil.rmtree(d, ignore_errors=True)
    users.pop(str(uid), None)
    save_users()


# ---------------- هندلرها ----------------
async def show_menu(uid: int, text: str = WELCOME):
    if not await joined(uid):
        await bot.send_message(uid, JOIN_TEXT, buttons=join_kb())
        return
    await bot.send_message(uid, text, buttons=main_kb(uid))


@bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def on_msg(event):
    uid = event.sender_id
    text = (event.raw_text or "").strip()
    low = text.lower()

    if low.startswith(("/start", "/menu")):
        redeeming.discard(uid)
        if uid in logins:
            await drop_login(uid)
        await show_menu(uid)
        return
    if low.startswith("/cancel"):
        redeeming.discard(uid)
        admin_wait.pop(uid, None)
        bc_pending.pop(uid, None)
        await drop_login(uid)
        await show_menu(uid, "لغو شد.")
        return
    if low.startswith("/log") and str(uid) in users:
        await send_log(uid)
        return
    if low.startswith("/redeem"):
        arg = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
        await event.reply(redeem_code(uid, arg) if arg else "مثال: /redeem MEOW-XXXX-XXXX")
        return

    if is_admin(uid) and low.startswith("/"):
        parts = text.split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd == "/admin":
            ptext, pkb = adm_home_view()
            await event.reply(ptext + "\n\nدستورهای متنی: /users  /user <id>  /kick <id>  /grant <id> <روز>  "
                                      "/gencode <روز> [تعداد]  /codes  /broadcast <متن>", buttons=pkb)
            return
        if cmd == "/users":
            rows = [f"{u} — {v.get('name', '—')} — {v.get('phone') or '—'} — {'🟢' if is_running(int(u)) else '🔴'} — {sub_text(int(u))}"
                    for u, v in users.items()]
            txt = "\n".join(rows) or "کاربری نیست."
            for i in range(0, len(txt), 3500):          # فهرست بلند توی چند پیام
                await event.reply(txt[i:i + 3500])
            return
        if cmd == "/user" and args and args[0].isdigit():
            v = users.get(args[0])
            if not v:
                await event.reply("این کاربر نیست.")
                return
            await event.reply(f"👤 {v.get('name', '—')}\n🆔 {args[0]}\n📱 {v.get('phone') or '— (قبل از این قابلیت ساخته شده)'}\n"
                              f"ساخته شده: {time.strftime('%Y-%m-%d %H:%M', time.localtime(v.get('created', 0)))}\n"
                              f"وضعیت: {'🟢 روشن' if is_running(int(args[0])) else '🔴 خاموش'}\nاشتراک: {sub_text(int(args[0]))}")
            return
        if cmd == "/kick" and args and args[0].isdigit():
            if args[0] in users:
                await delete_self(int(args[0]))
                await event.reply(f"کاربر {args[0]} حذف شد ✅")
                try:
                    await bot.send_message(int(args[0]), "سلف تو توسط ادمین حذف شد.")
                except Exception:
                    pass
            else:
                await event.reply("این کاربر نیست.")
            return
        if cmd == "/broadcast" and len(parts) > 1:
            msg = text.split(None, 1)[1]
            ok = 0
            for u in list(users):
                try:
                    await bot.send_message(int(u), msg)
                    ok += 1
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            await event.reply(f"برای {ok} نفر ارسال شد ✅")
            return
        if cmd == "/grant" and len(args) == 2 and args[0].isdigit():
            try:
                days = float(args[1])
            except ValueError:
                await event.reply("مثال: /grant 123456 30")
                return
            extend(int(args[0]), days)
            await event.reply(f"✅ اشتراک {args[0]}: {sub_text(int(args[0]))}")
            try:
                await bot.send_message(int(args[0]), f"🎁 اشتراکت {days:g} روز تغییر کرد.\n{sub_text(int(args[0]))}")
            except Exception:
                pass
            return
        if cmd == "/gencode" and args:
            try:
                days = int(args[0])
                n = min(int(args[1]), 50) if len(args) > 1 else 1
                assert days > 0 and n > 0
            except (ValueError, AssertionError):
                await event.reply("مثال: /gencode 30 5")
                return
            out = []
            for _ in range(n):
                c = new_code()
                codes[c] = days
                out.append(c)
            save_codes()
            await event.reply(f"🎟 {n} کد {days} روزه:\n" + "\n".join(out))
            return
        if cmd == "/emoji":
            sub_ = args[0].lower() if args else "list"
            if sub_ == "add" and len(args) >= 3:
                pairs = parse_emoji_input(f"{args[1]} {args[2]}", [])
                if pairs:
                    emoji_map.update(pairs)
                    save_emoji()
                    await event.reply(f"✅ ثبت شد: {args[1]} → {args[2]}")
                else:
                    await event.reply("مثال: /emoji add 👑 5368324170671202286")
            elif sub_ == "del" and len(args) >= 2:
                k = emoji_norm(args[1])
                await event.reply("حذف شد ✅" if emoji_map.pop(k, None) else "این ایموجی ثبت نشده بود.")
                save_emoji()
            elif sub_ == "reset":
                emoji_map.clear()
                save_emoji()
                await event.reply("همه‌ی ایموجی‌های پرمیوم پاک شد.")
            else:
                vt, vkb = adm_emoji_view()
                await event.reply(vt + "\n\n/emoji add <ایموجی> <آیدی>  |  /emoji del <ایموجی>  |  /emoji reset", buttons=vkb)
            return
        if cmd == "/codes":
            await event.reply("\n".join(f"{c} — {d} روز" for c, d in codes.items()) or "کدی نیست.")
            return

    if is_admin(uid) and admin_wait.get(uid) == "emo" and text and not text.startswith("/"):
        pairs = parse_emoji_input(event.message.raw_text or text, event.message.entities)
        if not pairs:
            await event.reply("❌ چیزی پیدا نشد. یا خودِ ایموجی پرمیوم رو بفرست، یا هر خط به این شکل:\n👑 5368324170671202286\n(/cancel برای لغو)",
                              parse_mode=None)
            return
        admin_wait.pop(uid, None)
        emoji_map.update(pairs)
        save_emoji()
        vt, vkb = adm_emoji_view()
        await event.reply(f"✅ {len(pairs)} ایموجی ثبت شد.\n\n" + vt, buttons=vkb)
        return

    if is_admin(uid) and admin_wait.get(uid) == "bc" and text and not text.startswith("/"):
        admin_wait.pop(uid, None)
        bc_pending[uid] = text
        await event.reply(f"👀 پیش‌نمایش پیام همگانی:\n\n{text}\n\nبرای {len(users)} نفر ارسال بشه؟", parse_mode=None,
                          buttons=[[Button.inline("✅ ارسال", b"adm:bcgo"), Button.inline("❌ لغو", b"adm:home")]])
        return

    if uid in redeeming and text and not text.startswith("/"):
        redeeming.discard(uid)
        await event.reply(redeem_code(uid, text), buttons=main_kb(uid))
        return

    L = logins.get(uid)
    if not L:
        await show_menu(uid, "از منوی زیر انتخاب کن:")
        return
    stage = L["stage"]

    if stage == "phone":
        contact = getattr(event.message, "contact", None)
        phone = norm_phone(contact.phone_number if contact else text)
        if not phone:
            await event.reply("شماره نامعتبره. با کد کشور بنویس (مثلاً +989123456789) یا از دکمه‌ی «ارسال شماره» استفاده کن.")
            return
        L.update(phone=phone, stage="api_choice")
        await bot.send_message(uid, "✅ شماره ثبت شد.", buttons=Button.clear())
        await bot.send_message(
            uid, "🔑 هر سلف با api_id و api_hash خودِ کاربر اجرا میشه. چطور می‌خوای بدیم؟\n\n"
                 "🤖 خودکار: من با کدی که تلگرام برات می‌فرسته توی my.telegram.org لاگین می‌کنم و api رو می‌گیرم (یا می‌سازم).\n"
                 "✍️ دستی: خودت از my.telegram.org ← API development tools می‌گیری و برام می‌فرستی.",
            buttons=api_choice_kb())
        return

    if stage == "mt_code":
        try:
            await event.delete()
        except Exception:
            pass
        code = re.sub(r"[\s\-_.,]", "", text)
        if len(code) < 4:
            await bot.send_message(uid, "کد رو کامل بفرست (می‌تونی بین حرف‌ها فاصله یا خط‌تیره بذاری).")
            return
        await mt_finish(uid, code)
        return

    if stage == "api_manual":
        try:
            await event.delete()                      # api_hash نباید توی چت بمونه
        except Exception:
            pass
        parse_api(text, L)
        if L.get("api_id") and L.get("api_hash"):
            await begin_telethon(uid)
        elif L.get("api_hash"):
            await bot.send_message(uid, "api_hash دریافت شد ✅ حالا api_id (عدد) رو بفرست.")
        elif L.get("api_id"):
            await bot.send_message(uid, "api_id دریافت شد ✅ حالا api_hash (۳۲ حرف) رو بفرست.")
        else:
            await bot.send_message(uid, "نامعتبره. api_id یه عدده و api_hash یه رشته‌ی ۳۲ حرفیِ عدد و حرف انگلیسی. هر دو رو بفرست (یا جدا جدا).")
        return

    if stage == "code":           # اگه کد رو تایپ کرد (بهتره دکمه‌ها)
        digits = re.sub(r"\D", "", text.translate(_FA))
        try:
            await event.delete()
        except Exception:
            pass
        if len(digits) == L["len"]:
            L["code"] = digits
            await submit_code(uid)
        else:
            await bot.send_message(uid, "کد رو با دکمه‌های بالا وارد کن 👆")
        return

    if stage == "password":
        try:
            await event.delete()           # پیام رمز همون لحظه پاک میشه
        except Exception:
            pass
        await submit_password(uid, text)
        return

    await show_menu(uid, "از دکمه‌های بالا ادامه بده یا /cancel بزن.")


# ---------------- پنل مدیریت (دکمه‌ای) ----------------
admin_wait: dict = {}       # {آیدی ادمین: "bc"} وقتی منتظر متن پیام همگانیم
bc_pending: dict = {}       # {آیدی ادمین: متن پیام همگانی که منتظر تأییده}
ADM_PAGE = 8


def adm_home_view():
    run = sum(1 for u in users if is_running(int(u)))
    exp = sum(1 for u in users if is_expired(int(u)))
    text = (f"👑 پنل مدیریت\n\n👥 کاربران: {len(users)} / {MAX_USERS}\n🟢 روشن: {run}\n"
            f"⛔️ اشتراک‌تمام‌شده: {exp}\n🎟 کد استفاده‌نشده: {len(codes)}")
    kb = [[Button.inline("👥 کاربران", b"adm:users:0"), Button.inline("🔄 بروزرسانی", b"adm:home")],
          [Button.inline("🎟 ساخت کد", b"adm:gen"), Button.inline("📋 کدها", b"adm:codes")],
          [Button.inline("📢 پیام همگانی", b"adm:bc"), Button.inline("🎨 ایموجی پرمیوم", b"adm:emo")],
          [Button.inline("🏠 منوی اصلی", b"adm:menu")]]
    return text, kb


def adm_users_view(page: int):
    ids = list(users)
    pages = max(1, (len(ids) + ADM_PAGE - 1) // ADM_PAGE)
    page = min(max(page, 0), pages - 1)
    kb = []
    for u in ids[page * ADM_PAGE:(page + 1) * ADM_PAGE]:
        ic = "🟢" if is_running(int(u)) else ("⛔️" if is_expired(int(u)) else "🔴")
        kb.append([Button.inline(f"{ic} {str(users[u].get('name', '—'))[:20]} — {u}", f"adm:u:{u}".encode())])
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ قبلی", f"adm:users:{page - 1}".encode()))
    nav.append(Button.inline(f"{page + 1}/{pages}", b"adm:noop"))
    if page < pages - 1:
        nav.append(Button.inline("بعدی ▶️", f"adm:users:{page + 1}".encode()))
    kb.append(nav)
    kb.append([Button.inline("🏠 پنل", b"adm:home")])
    return (f"👥 کاربران ({len(ids)}) — یکی رو انتخاب کن:" if ids else "کاربری نیست."), kb


def adm_user_view(u: str):
    v = users.get(u)
    if not v:
        return "این کاربر دیگه نیست.", [[Button.inline("🔙 فهرست", b"adm:users:0")]]
    uid = int(u)
    run = is_running(uid)
    text = (f"👤 {v.get('name', '—')}\n🆔 {u}\n📱 {v.get('phone') or '— (قبل از ذخیره‌ی شماره ساخته شده)'}\n"
            f"ساخته شده: {time.strftime('%Y-%m-%d %H:%M', time.localtime(v.get('created', 0)))}\n"
            f"وضعیت: {'🟢 روشن' if run else '🔴 خاموش'}\nاشتراک: {sub_text(uid)}")
    kb = [[Button.inline("⏸ خاموش" if run else "▶️ روشن", f"adm:ut:{u}".encode()), Button.inline("📜 لاگ", f"adm:ul:{u}".encode())],
          [Button.inline("➕ ۷ روز", f"adm:ug:{u}:7".encode()), Button.inline("➕ ۳۰ روز", f"adm:ug:{u}:30".encode()),
           Button.inline("➕ ۹۰ روز", f"adm:ug:{u}:90".encode())],
          [Button.inline("➖ ۷ روز", f"adm:ug:{u}:-7".encode())],
          [Button.inline("🗑 حذف کاربر", f"adm:uk:{u}".encode())],
          [Button.inline("🔙 فهرست", b"adm:users:0"), Button.inline("🏠 پنل", b"adm:home")]]
    return text, kb


async def admin_cb(event, uid: int, data: str):
    if not is_admin(uid):
        await event.answer("دسترسی نداری.", alert=True)
        return
    p = data.split(":")
    act = p[1] if len(p) > 1 else ""

    async def show(view):
        text, kb = view
        try:
            await event.edit(text, buttons=kb)
        except errors.MessageNotModifiedError:
            await event.answer("بروز بود ✅")
        except Exception as e:
            print(f"پنل ادمین: {e}")

    if act == "home":
        admin_wait.pop(uid, None)
        bc_pending.pop(uid, None)
        await show(adm_home_view())
    elif act == "menu":
        await event.edit(WELCOME, buttons=main_kb(uid))
    elif act == "noop":
        await event.answer()
    elif act == "users":
        await show(adm_users_view(int(p[2]) if len(p) > 2 and p[2].lstrip("-").isdigit() else 0))
    elif act == "u" and len(p) > 2:
        await show(adm_user_view(p[2]))
    elif act == "ut" and len(p) > 2:                  # روشن/خاموش کردن سلف یه کاربر
        u = p[2]
        if u not in users:
            await event.answer("این کاربر نیست.", alert=True)
            return
        tu = int(u)
        if is_running(tu):
            await stop_self(tu)
            users[u]["enabled"] = False
        else:
            if is_expired(tu):
                await event.answer("اشتراکش تموم شده؛ اول تمدیدش کن.", alert=True)
                return
            users[u].update(enabled=True, invalid=False)
            start_self(tu)
        save_users()
        await asyncio.sleep(1)
        await show(adm_user_view(u))
    elif act == "ul" and len(p) > 2:                  # لاگ سلف کاربر برای ادمین
        await event.answer()
        await send_log(int(p[2]), to=uid)
    elif act == "ug" and len(p) > 3:                  # اضافه/کم کردن اشتراک
        u = p[2]
        try:
            days = float(p[3])
        except ValueError:
            return
        extend(int(u), days)
        await event.answer(f"{days:+g} روز اعمال شد ✅")
        try:
            await bot.send_message(int(u), f"🎁 اشتراکت {days:+g} روز تغییر کرد.\n{sub_text(int(u))}")
        except Exception:
            pass
        await show(adm_user_view(u))
    elif act == "uk" and len(p) > 2:                  # حذف کاربر: تأیید
        await show((f"⚠️ مطمئنی کاربر {p[2]} ({users.get(p[2], {}).get('name', '—')}) حذف بشه؟\n"
                    "سلفش خاموش، از اکانتش خارج و همه‌ی فایل‌هاش پاک میشه.",
                    [[Button.inline("✅ بله، حذف کن", f"adm:ukc:{p[2]}".encode()), Button.inline("↩️ نه", f"adm:u:{p[2]}".encode())]]))
    elif act == "ukc" and len(p) > 2:
        u = p[2]
        if u in users:
            await event.edit("⏳ در حال حذف ...")
            await delete_self(int(u))
            try:
                await bot.send_message(int(u), "سلف تو توسط ادمین حذف شد.")
            except Exception:
                pass
        await show(adm_users_view(0))
    elif act == "codes":
        rows = [f"`{c}` — {d} روز" for c, d in list(codes.items())[:40]]
        extra = f"\n… و {len(codes) - 40} کد دیگه" if len(codes) > 40 else ""
        await show((("🎟 کدهای استفاده‌نشده:\n\n" + "\n".join(rows) + extra) if rows else "کدی نیست.",
                    [[Button.inline("🎟 ساخت کد", b"adm:gen")], [Button.inline("🏠 پنل", b"adm:home")]]))
    elif act == "gen":
        if len(p) == 2:                               # مرحله ۱: چند روزه؟
            await show(("🎟 ساخت کد تمدید — چند روزه؟",
                        [[Button.inline(f"{d} روز", f"adm:gen:{d}".encode()) for d in (7, 30)],
                         [Button.inline(f"{d} روز", f"adm:gen:{d}".encode()) for d in (90, 365)],
                         [Button.inline("🏠 پنل", b"adm:home")]]))
        elif len(p) == 3:                             # مرحله ۲: چندتا؟
            await show((f"🎟 کد {p[2]} روزه — چند تا بسازم؟",
                        [[Button.inline(f"{n} تا", f"adm:gen:{p[2]}:{n}".encode()) for n in (1, 5, 10, 20)],
                         [Button.inline("🔙 برگشت", b"adm:gen"), Button.inline("🏠 پنل", b"adm:home")]]))
        else:                                         # مرحله ۳: ساخت
            days, n = int(p[2]), min(int(p[3]), 50)
            out = []
            for _ in range(n):
                c = new_code()
                codes[c] = days
                out.append(c)
            save_codes()
            await show((f"✅ {n} کد {days} روزه ساخته شد (روی هر کد بزن تا کپی بشه):\n\n" + "\n".join(f"`{c}`" for c in out),
                        [[Button.inline("🎟 ساخت کد دیگه", b"adm:gen"), Button.inline("🏠 پنل", b"adm:home")]]))
    elif act == "emo":
        admin_wait.pop(uid, None)
        await show(adm_emoji_view())
    elif act == "emoadd":
        admin_wait[uid] = "emo"
        await show(("🎨 ایموجی پرمیوم رو به یکی از این دو روش بفرست:\n\n"
                    "۱) خودِ ایموجی پرمیوم رو بفرست (اگه اکانتت پرمیومه). همین کافیه.\n"
                    "۲) دستی: ایموجی معمولی + آیدی عددی، هر مورد توی یه خط:\n👑 5368324170671202286\n✅ 5206607081334906820\n\n"
                    "می‌تونی چند مورد رو یه‌جا بفرستی.",
                    [[Button.inline("📋 ایموجی‌های ربات", b"adm:emoused")], [Button.inline("❌ لغو", b"adm:emo")]]))
    elif act == "emoused":
        await show(("📋 ایموجی‌هایی که ربات استفاده می‌کنه (به ترتیب تکرار):\n\n" + used_emojis(),
                    [[Button.inline("➕ افزودن / تغییر", b"adm:emoadd")], [Button.inline("🔙 برگشت", b"adm:emo")]]))
    elif act == "emodel":
        keys = list(emoji_map)
        rows = [[Button.inline(f"🗑 {k}", f"adm:emod:{i}".encode()) for i, k in list(enumerate(keys))[j:j + 4]] for j in range(0, len(keys), 4)]
        await show(("کدوم رو حذف کنم؟", rows + [[Button.inline("🔙 برگشت", b"adm:emo")]]))
    elif act == "emod" and len(p) > 2 and p[2].isdigit():
        keys = list(emoji_map)
        if int(p[2]) < len(keys):
            emoji_map.pop(keys[int(p[2])], None)
            save_emoji()
        await show(adm_emoji_view())
    elif act == "emoreset":
        emoji_map.clear()
        save_emoji()
        await show(adm_emoji_view())
    elif act == "bc":
        admin_wait[uid] = "bc"
        await show(("📢 متن پیام همگانی رو همین‌جا بفرست (برای همه‌ی کاربرها ارسال میشه؛ قبلش پیش‌نمایش می‌بینی).",
                    [[Button.inline("❌ لغو", b"adm:home")]]))
    elif act == "bcgo":
        msg = bc_pending.pop(uid, None)
        if not msg:
            await event.answer("پیامی برای ارسال نیست.", alert=True)
            return
        await event.edit("⏳ در حال ارسال ...")
        ok = 0
        for u in list(users):
            try:
                await bot.send_message(int(u), msg, parse_mode=None)
                ok += 1
            except Exception:
                pass
            await asyncio.sleep(0.1)
        text, kb = adm_home_view()
        await event.edit(f"✅ برای {ok} نفر ارسال شد.\n\n" + text, buttons=kb)
    else:
        await event.answer()


@bot.on(events.CallbackQuery)
async def on_cb(event):
    uid = event.sender_id
    data = event.data.decode()

    if data.startswith("adm:"):
        await admin_cb(event, uid, data)
        return

    if data == "chk":
        if await joined(uid):
            await event.edit(WELCOME, buttons=main_kb(uid))
        else:
            await event.answer("هنوز عضو نشدی!", alert=True)
        return

    if data.startswith("k:"):
        L = logins.get(uid)
        if not L or L["stage"] != "code":
            await event.answer("این مرحله تموم شده؛ /start", alert=True)
            return
        k = data[2:]
        if k == "del":
            L["code"] = L["code"][:-1]
        elif k == "ok":
            if len(L["code"]) != L["len"]:
                await event.answer(f"کد {L['len']} رقمیه", alert=True)
                return
        elif len(L["code"]) < L["len"]:
            L["code"] += k
        shown, kb = keypad(L["code"], L["len"])
        try:
            await event.edit("📩 کد تلگرام رو وارد کن:\n\n" + shown, buttons=kb)
        except Exception:
            pass
        if len(L["code"]) == L["len"]:
            await submit_code(uid)
        else:
            await event.answer()
        return

    if data == "cancel":
        redeeming.discard(uid)
        await drop_login(uid)
        await event.edit("لغو شد.", buttons=main_kb(uid))
        return

    if data.startswith("api:"):
        L = logins.get(uid)
        if not L or L["stage"] not in ("api_choice", "mt_code", "api_manual") or not L.get("phone"):
            await event.answer("این مرحله تموم شده؛ /start", alert=True)
            return
        await event.answer()
        if data == "api:auto":
            L["stage"] = "api_choice"
            await mt_start(uid)
        else:
            L.update(stage="api_manual", api_id=None, api_hash=None, mt=None)
            await bot.send_message(
                uid, "✍️ برو my.telegram.org ← لاگین ← «API development tools» و api_id و api_hash رو برام بفرست "
                     "(توی یه پیام یا جدا جدا؛ پیامت همون لحظه پاک میشه). لغو: /cancel")
        return

    if data == "redeem":
        if not await joined(uid):
            await event.edit(JOIN_TEXT, buttons=join_kb())
            return
        redeeming.add(uid)
        await event.answer()
        await bot.send_message(uid, "🎟 کد اشتراکت رو بفرست (مثلاً MEOW-XXXX-XXXX). لغو: /cancel")
        return

    if data == "create":
        if not await joined(uid):
            await event.edit(JOIN_TEXT, buttons=join_kb())
            return
        if str(uid) in users:
            await event.answer("قبلاً سلف ساخته‌ای.", alert=True)
            return
        if len(users) >= MAX_USERS:
            await event.answer("ظرفیت ربات پر شده.", alert=True)
            return
        if is_expired(uid) and str(uid) in subs:
            await event.answer("اشتراکت تموم شده؛ اول با «🎟 کد اشتراک» تمدید کن.", alert=True)
            return
        await drop_login(uid)
        logins[uid] = {"stage": "phone", "ts": time.time()}
        await event.answer()
        await bot.send_message(uid, "📱 شماره‌ی اکانتی که می‌خوای سلف روش باشه رو بفرست (با دکمه یا تایپ، با کد کشور). برای لغو: /cancel",
                               buttons=[[Button.request_phone("📱 ارسال شماره‌ی من", resize=True, single_use=True)]])
        return

    if str(uid) not in users:
        await event.answer("اول سلف بساز.", alert=True)
        return

    if data == "status":
        await event.edit(status_text(uid), buttons=main_kb(uid))
    elif data == "help":
        await event.edit(HELP_USER, buttons=main_kb(uid))
    elif data == "log":
        await event.answer()
        await send_log(uid)
    elif data == "toggle":
        if is_running(uid):
            await stop_self(uid)
            users[str(uid)]["enabled"] = False
        else:
            if is_expired(uid):
                await event.answer("اشتراکت تموم شده؛ با «🎟 تمدید با کد» تمدید کن.", alert=True)
                return
            if not await joined(uid):
                await event.edit(JOIN_TEXT, buttons=join_kb())
                return
            users[str(uid)]["enabled"] = True
            users[str(uid)]["invalid"] = False
            start_self(uid)
        save_users()
        await asyncio.sleep(1)
        await event.edit(status_text(uid), buttons=main_kb(uid))
    elif data == "del":
        await event.edit("⚠️ مطمئنی؟ سلف خاموش میشه، از اکانتت خارج میشیم (سشن باطل میشه) و همه‌ی فایل‌ها و تنظیماتش پاک میشه.",
                         buttons=[[Button.inline("✅ بله، حذف کن", b"delyes"), Button.inline("↩️ نه", b"status")]])
    elif data == "delyes":
        await event.edit("⏳ در حال حذف ...")
        await delete_self(uid)
        await event.edit("🗑 سلف حذف شد و سشن باطل شد. هر وقت خواستی دوباره بساز.", buttons=main_kb(uid))


async def send_log(uid: int, to=None):
    """لاگ سلف کاربر uid رو برای خودش (یا اگه to داده شد، برای ادمین) می‌فرسته"""
    f = user_dir(uid) / "self.log"
    try:
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 3000))
            tail = fh.read().decode("utf-8", "ignore")
    except OSError:
        tail = ""
    await bot.send_message(to or uid, ("📜 آخرین لاگ سلف:\n\n" + tail[-3500:]) if tail.strip() else "لاگی نیست.", parse_mode=None)


async def housekeeping_loop():
    """لغو لاگین‌های نیمه‌کاره + خاموش کردن سلفِ اشتراک‌تمام‌شده + هشدار یک روز قبل از انقضا"""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for uid, L in list(logins.items()):
            if now - L.get("ts", 0) > LOGIN_TIMEOUT and not L.get("busy"):
                await drop_login(uid)
                try:
                    await bot.send_message(uid, "⌛ زمان ورود تموم شد؛ دوباره /start", buttons=main_kb(uid))
                except Exception:
                    pass
        for k, u in list(users.items()):
            uid = int(k)
            if is_admin(uid):
                continue
            left = subs.get(k, 0) - now
            try:
                if left <= 0 and not u.get("expired"):
                    await stop_self(uid)
                    u.update(enabled=False, expired=True)
                    save_users()
                    await bot.send_message(uid, "⛔️ اشتراکت تموم شد و سلف خاموش شد.\n" + renew_hint(), buttons=main_kb(uid))
                elif 0 < left < 86400 and not u.get("warned"):
                    u["warned"] = True
                    save_users()
                    await bot.send_message(uid, f"⏰ فقط {fmt_dur(left)} از اشتراکت مونده.\n" + renew_hint())
            except Exception as e:
                print(f"[{uid}] اعلان اشتراک: {e}")


async def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        print("ERROR: set MAKER_BOT_TOKEN, MAKER_API_ID and MAKER_API_HASH (see config.env)")
        return
    await bot.start(bot_token=BOT_TOKEN)
    load_all()
    asyncio.create_task(housekeeping_loop())
    try:
        for k, u in list(users.items()):
            if u.get("enabled", True) and not u.get("invalid") and not is_expired(int(k)):
                start_self(int(k))
                await asyncio.sleep(2)          # شروع تدریجی تا تلگرام محدود نکنه
        print(f"Self Maker bot is running. Users: {len(users)}")
        await bot.run_until_disconnected()
    finally:
        await stop_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
