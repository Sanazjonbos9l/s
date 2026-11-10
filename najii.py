"""
saver.py — سلف مخصوص «ذخیره»
فقط این کارها رو می‌کنه (هیچ قابلیت دیگه‌ی سلف توش نیست):
  • ذخیره‌ی همه‌ی پیام‌های پیوی (متن، عکس، ویدیو، ویس، استیکر، فایل، ...) توی یک کانال
  • ذخیره‌ی مدیای نابودشونده/یک‌بار‌مصرف (عکس، ویدیو، ویس، ویدیو‌مسیج)
  • ویرایش‌شده: همون پیام توی کانال ادیت میشه (قبل ← ادیت شده ← بعد)
  • حذف‌شده: زیر همون پیام توی کانال علامت «حذف شد» با زمان میاد
  • زیر هر پیام: ارسالی/دریافتی، اسم، یوزرنیم، آیدی، زمان (و ریپلای اگه داشت)

نصب:   pip install telethon jdatetime
اجرا:   SAVER_API_ID=123 SAVER_API_HASH=abc python3 saver.py
        (بار اول شماره و کد تلگرام رو می‌پرسه)

تنظیم کانال و دستورها: توی «Saved Messages» (یا هر چتی) بنویس — «راهنما ذخیره» یا  saver help
"""
import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, errors, events, types, utils

try:
    import jdatetime
except ImportError:          # اختیاریه (فقط برای تاریخ شمسی)
    jdatetime = None

# ====== تنظیمات (یا با متغیر محیطی) ======
API_ID = int(os.environ.get("SAVER_API_ID", "774143"))            # <-- api_id
API_HASH = os.environ.get("SAVER_API_HASH", "62a87b8c12cf0c91f8d5312bfac42371")      # <-- api_hash
SESSION = os.environ.get("SAVER_SESSION", "saver_session")
TZ_HOURS = float(os.environ.get("SAVER_TZ", "3.5"))               # تهران: UTC+3:30
# ==========================================

SETTINGS_FILE = f"{SESSION}_settings.json"
MAP_FILE = f"{SESSION}_map.json"
MAP_MAX = 20000
SEP = "━━━━━━━━━━━━"
SERVICE_IDS = {777000, 42777}     # پیام‌های خودِ تلگرام (کد ورود و ...) هیچ‌وقت ذخیره نمیشن

client = TelegramClient(SESSION, API_ID, API_HASH)

# انواع محتوا: کلید ← (ایموجی، اسم فارسی)
TYPES = {"text": ("📝", "پیام"), "photo": ("📷", "عکس"), "video": ("🎬", "فیلم"), "gif": ("🎞", "گیف"),
         "voice": ("🎙", "ویس"), "videonote": ("⭕", "ویدیو‌مسیج"), "sticker": ("🧩", "استیکر"),
         "audio": ("🎵", "موزیک"), "file": ("📎", "فایل"), "other": ("📍", "سایر (لوکیشن، مخاطب، ...)")}
DEFAULTS = {"channel": None, "enabled": True, "ttl": True, "edits": True, "deleted": True, "bots": False}
S: dict = dict(DEFAULTS)
S["types"] = {k: {"in": True, "out": True} for k in TYPES}      # in = از طرف فرستنده (دریافتی)، out = خودم
MAP: dict = {}          # {str(msg_id): {...اطلاعات پیام ذخیره‌شده...}}
state = {"me_id": None, "premium": False, "target": None, "dirty": False}
send_lock = asyncio.Lock()


# ---------------- ذخیره‌ی تنظیمات ----------------
def load_state():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
        types_saved = loaded.pop("types", {})
        S.update(loaded)
        for k in TYPES:                                    # نوع‌های جدید پیش‌فرض روشن‌اند
            S["types"][k].update({d: bool(v) for d, v in (types_saved.get(k) or {}).items() if d in ("in", "out")})
        if loaded.get("mine") is False and not types_saved:   # سازگاری با نسخه‌ی قبلی
            for k in TYPES:
                S["types"][k]["out"] = False
        S.pop("mine", None)
    except (OSError, ValueError):
        pass
    try:
        with open(MAP_FILE, encoding="utf-8") as f:
            MAP.update(json.load(f))
    except (OSError, ValueError):
        pass


def save_settings():
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(S, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)


def save_map():
    while len(MAP) > MAP_MAX:
        MAP.pop(next(iter(MAP)))
    tmp = MAP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(MAP, f, ensure_ascii=False)
    os.replace(tmp, MAP_FILE)
    state["dirty"] = False


async def flush_loop():
    while True:
        await asyncio.sleep(20)
        if state["dirty"]:
            try:
                save_map()
            except OSError as e:
                print(f"ذخیره‌ی نگاشت پیام‌ها ناموفق: {e}")


# ---------------- ابزارها ----------------
def local_dt(dt=None) -> datetime:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=TZ_HOURS)))


def fmt_time(dt=None) -> str:
    d = local_dt(dt)
    s = d.strftime("%Y-%m-%d %H:%M:%S")
    if jdatetime:
        try:
            j = jdatetime.datetime.fromgregorian(datetime=d.replace(tzinfo=None))
            s = f"{j.strftime('%Y/%m/%d')} — {d.strftime('%H:%M:%S')}  ({d.strftime('%Y-%m-%d')})"
        except Exception:
            pass
    return s


_AR = str.maketrans({"ي": "ی", "ك": "ک", "‌": "", "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
                     "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9"})


def normalize(t: str) -> str:
    return (t or "").translate(_AR)


def classify(msg) -> str:
    """نوع پیام برای تنظیم‌های جدا: text / photo / video / gif / voice / videonote / sticker / audio / file / other"""
    media = msg.media
    if media is None or isinstance(media, types.MessageMediaWebPage):
        return "text"
    for key, attr in (("sticker", "sticker"), ("videonote", "video_note"), ("voice", "voice"), ("gif", "gif"),
                      ("video", "video"), ("audio", "audio"), ("photo", "photo")):
        if getattr(msg, attr, None):
            return key
    return "file" if getattr(msg, "document", None) else "other"


def display_name(ent) -> str:
    if ent is None:
        return "?"
    n = " ".join(x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x)
    return n or getattr(ent, "title", None) or "بدون نام"


def ttl_label(msg, ttl) -> str:
    kind = ("⭕ ویدیو‌مسیج" if getattr(msg, "video_note", None) else "🎙 ویس" if getattr(msg, "voice", None)
            else "🎬 ویدیو" if getattr(msg, "video", None) else "📷 عکس" if getattr(msg, "photo", None) else "⏳ مدیای")
    t = "یک‌بار‌مصرف" if (not isinstance(ttl, int) or ttl >= 2 ** 31 - 1) else f"{ttl} ثانیه"
    return f"{kind} نابودشونده ({t})"


def make_footer(chat, out: bool, when, extra: list) -> str:
    uname = f"@{chat.username}" if getattr(chat, "username", None) else "—"
    lines = [SEP, "📤 ارسالی (از طرف تو)" if out else "📥 دریافتی",
             f"👤 {'به: ' if out else ''}{display_name(chat)}", f"🔗 {uname}", f"🆔 {chat.id}", f"🕐 {fmt_time(when)}"]
    lines += [x for x in extra if x]
    return "\n".join(lines)


def build_text(e: dict, limit: int) -> str:
    """متن نمایش‌داده‌شده توی کانال: نسخه‌ها (اولی، بعد هر «ادیت شده») + پاورقی. اگه بلند بود وسطش کوتاه میشه"""
    v = e["v"]
    parts = [v[0][0]] if v and v[0][0] else []
    for txt, ts in v[1:]:
        parts.append(f"{SEP}\n✏️ ادیت شده ({ts})\n{txt}")
    body = "\n".join(parts)
    foot = e["foot"]
    avail = limit - len(foot) - 6
    if avail < 40:
        foot, avail = foot[: max(0, limit - 46)], 40
    if len(body) > avail:
        h = avail // 2
        body = body[:h] + "\n…\n" + body[-(avail - h - 3):]
    return (body + "\n\n" + foot) if body else foot


# ---------------- ارسال به کانال ----------------
async def retry(fn, *a, **k):
    for _ in range(3):
        try:
            return await fn(*a, **k)
        except errors.FloodWaitError as e:
            await asyncio.sleep(min(e.seconds + 1, 300))
    return await fn(*a, **k)


async def resolve_channel(ref):
    ref = normalize(ref).strip()
    m = re.search(r"t\.me/c/(\d+)", ref)
    if m:
        ref = int("-100" + m.group(1))
    elif re.fullmatch(r"-?\d+", ref):
        n = int(ref)
        ref = n if n < 0 else int("-100" + str(n))
    elif "t.me/" in ref:
        ref = "@" + ref.split("t.me/", 1)[1].split("/")[0].split("?")[0]
    try:
        ent = await client.get_entity(ref)
    except ValueError:
        await client.get_dialogs()
        ent = await client.get_entity(ref)
    if isinstance(ent, types.User):
        raise ValueError("باید کانال یا گروه باشه، نه یه کاربر")
    return utils.get_peer_id(ent), ent


def cap_limit() -> int:
    return 2000 if state["premium"] else 1000


async def post(target, msg, e: dict, is_ttl: bool):
    """پیام رو توی مقصد می‌فرسته؛ آیدی پیام لاگ و اینکه کپشن‌دار (مدیا) هست یا نه رو برمی‌گردونه"""
    media = msg.media
    plain = media is None or isinstance(media, types.MessageMediaWebPage)
    if plain:
        r = await retry(client.send_message, target, build_text(e, 4000), link_preview=False)
        return r.id, False

    no_caption = bool(getattr(msg, "video_note", None) or getattr(msg, "sticker", None))
    sendable = isinstance(media, (types.MessageMediaPhoto, types.MessageMediaDocument))

    async def via_download():
        tmp = tempfile.mkdtemp(prefix="saver_")
        try:
            path = await client.download_media(msg, file=tmp + os.sep)
            if not path:
                raise RuntimeError("دانلود مدیا ناموفق بود")
            kw = dict(voice_note=bool(getattr(msg, "voice", None)), video_note=bool(getattr(msg, "video_note", None)),
                      supports_streaming=bool(getattr(msg, "video", None)))
            if no_caption:
                return await retry(client.send_file, target, path, **kw)
            return await retry(client.send_file, target, path, caption=build_text(e, cap_limit()), **kw)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    sent = None
    if sendable and not is_ttl:
        try:
            if no_caption:
                sent = await retry(client.send_file, target, media)
            else:
                sent = await retry(client.send_file, target, media, caption=build_text(e, cap_limit()))
        except Exception as ex:
            print(f"ارسال مستقیم مدیا نشد ({str(ex)[:80]})؛ با دانلود امتحان می‌کنم")
    if sent is None and sendable:
        try:
            sent = await via_download()
        except Exception as ex:
            r = await retry(client.send_message, target, build_text(e, 4000) + f"\n⚠️ مدیا ذخیره نشد: {str(ex)[:100]}")
            return r.id, False
    if sent is None:                              # لوکیشن، مخاطب، نظرسنجی و ...
        try:
            first = await retry(client.send_file, target, media)
            r = await retry(client.send_message, target, build_text(e, 4000), reply_to=first.id)
        except Exception:
            r = await retry(client.send_message, target, build_text(e, 4000) + "\n⚠️ این نوع پیام قابل کپی نبود")
        return r.id, False
    if isinstance(sent, list):
        sent = sent[0]
    if no_caption:                                # ویدیو‌مسیج و استیکر کپشن ندارن؛ پاورقی جدا زیرش میاد
        r = await retry(client.send_message, target, build_text(e, 4000), reply_to=sent.id)
        return r.id, False
    return sent.id, True


# ---------------- رویدادهای پیام ----------------
async def ignore_chat(chat, out: bool) -> bool:
    if chat is None or chat.id == state["me_id"] or chat.id in SERVICE_IDS:
        return True
    if getattr(chat, "bot", False) and not S["bots"]:
        return True
    return False


@client.on(events.NewMessage(func=lambda e: e.is_private))
async def on_new(event):
    try:
        msg = event.message
        if msg.action:
            return
        out = bool(msg.out)
        chat = await event.get_chat()
        if await ignore_chat(chat, out):
            return
        ttl = getattr(msg.media, "ttl_seconds", None) if msg.media else None
        is_ttl = bool(ttl) and not out
        has_dest = state["target"] is not None
        if is_ttl and not S["ttl"]:
            is_ttl = False
        if not is_ttl:                       # نابودشونده‌ها فقط با «سکرت» کنترل میشن؛ بقیه با نوع × جهت
            if not (S["enabled"] and has_dest):
                return
            if not S["types"][classify(msg)]["out" if out else "in"]:
                return
        extra = [ttl_label(msg, ttl) if is_ttl else ""]
        if msg.is_reply:
            try:
                rep = await msg.get_reply_message()
                snip = (rep.raw_text or "").strip().replace("\n", " ")[:60] if rep else ""
                extra.append(f"↩️ پاسخ به: {snip or '(مدیا)'}")
            except Exception:
                pass
        e = {"chat": chat.id, "out": out, "ttl": is_ttl, "log": 0, "media": False, "del": False,
             "foot": make_footer(chat, out, msg.date, extra),
             "v": [[msg.raw_text or "", fmt_time(msg.date)]], "who": display_name(chat)}
        target = state["target"] if has_dest else "me"      # نابودشونده بدون کانال: توی Saved Messages
        async with send_lock:
            e["log"], e["media"] = await post(target, msg, e, is_ttl)
        MAP[str(msg.id)] = e
        state["dirty"] = True
    except Exception as ex:
        print(f"خطا توی ذخیره‌ی پیام: {ex}")


@client.on(events.MessageEdited(func=lambda e: e.is_private))
async def on_edit(event):
    try:
        if not S["edits"] or state["target"] is None:
            return
        msg = event.message
        e = MAP.get(str(msg.id))
        if not e or e.get("ttl") or e.get("chat") != msg.chat_id:
            return
        new = msg.raw_text or ""
        if new == e["v"][-1][0]:
            return
        e["v"].append([new, fmt_time(getattr(msg, "edit_date", None))])
        state["dirty"] = True
        text = build_text(e, cap_limit() if e["media"] else 4000)
        async with send_lock:
            await retry(client.edit_message, state["target"], e["log"], text)
    except errors.MessageNotModifiedError:
        pass
    except Exception as ex:
        print(f"خطا توی ثبت ویرایش: {ex}")


@client.on(events.MessageDeleted())
async def on_delete(event):
    try:
        if not S["deleted"] or state["target"] is None or event.chat_id is not None:   # حذف توی کانال/ابرگروه مربوط به ما نیست
            return
        hits = [(i, MAP[str(i)]) for i in event.deleted_ids if str(i) in MAP and not MAP[str(i)].get("del")]
        if not hits:
            return
        now = fmt_time()
        async with send_lock:
            if len(hits) > 15:
                await retry(client.send_message, state["target"], f"🗑 {len(hits)} پیام پیوی یه‌جا حذف شد\n🕐 {now}")
                for _, e in hits:
                    e["del"] = True
            else:
                for _, e in hits:
                    e["del"] = True
                    who = "تو (پیام ارسالی)" if e["out"] else e["who"]
                    await retry(client.send_message, state["target"],
                                f"🗑 این پیام حذف شد\n👤 {who}\n🕐 زمان حذف: {now}", reply_to=e["log"])
        state["dirty"] = True
    except Exception as ex:
        print(f"خطا توی ثبت حذف: {ex}")


# ---------------- دستورها ----------------
_TOK = {"لاگ": "log", "ذخیره": "saver", "کانال": "channel", "روشن": "on", "خاموش": "off", "وضعیت": "status",
        "سکرت": "ttl", "نابودشونده": "ttl", "ادیت": "edits", "ویرایش": "edits", "حذف": "deleted",
        "ربات": "bots", "رباتها": "bots", "راهنما": "help",
        # جهت: خودم / فرستنده
        "خودم": "me", "من": "me", "فرستنده": "sender", "طرف": "sender", "دریافتی": "sender", "مقابل": "sender",
        # نوع محتوا
        "پیام": "text", "متن": "text", "عکس": "photo", "تصویر": "photo", "فیلم": "video", "ویدیو": "video", "ویدئو": "video",
        "گیف": "gif", "ویس": "voice", "صدا": "voice", "ویدیومسیج": "videonote", "ویدئومسیج": "videonote",
        "استیکر": "sticker", "موزیک": "audio", "آهنگ": "audio", "فایل": "file", "سایر": "other", "همه": "all"}
_EN_TYPES = set(TYPES) | {"all"}
_FLAGS = {"edits", "deleted", "bots"}

HELP = (
    "📖 راهنمای سلف ذخیره\n"
    "ذخیره می‌کنه: پیام‌های پیوی + مدیای نابودشونده + ویرایش (قبل/بعد) + حذف‌شده‌ها، با زمان و اسم و آیدی فرستنده.\n\n"
    "🔧 کانال و کلی:\n"
    "• کانال لاگ <@یوزرنیم | لینک | آیدی>   — انتخاب کانال (یا توی خودِ کانال بنویس: کانال لاگ)\n"
    "• لاگ روشن / لاگ خاموش — کلید اصلی ذخیره‌ی پیوی\n"
    "• سکرت روشن|خاموش — نابودشونده‌ها\n"
    "• لاگ ادیت روشن|خاموش — ثبت ویرایش‌ها\n"
    "• لاگ حذف روشن|خاموش — ثبت حذف‌شده‌ها\n"
    "• لاگ ربات روشن|خاموش — چت با ربات‌ها (پیش‌فرض خاموش)\n\n"
    "🎛 هر نوع محتوا جدا (برای خودت و فرستنده):\n"
    "انواع: پیام، عکس، فیلم، گیف، ویس، ویدیومسیج، استیکر، موزیک، فایل، سایر، همه\n"
    "• لاگ عکس روشن — عکسِ هر دو طرف\n"
    "• لاگ عکس خودم روشن — فقط عکس‌های خودت\n"
    "• لاگ عکس فرستنده خاموش — عکس طرف مقابل ذخیره نشه\n"
    "• لاگ خودم خاموش — همه‌ی انواعِ ارسالیِ خودت\n"
    "• لاگ فرستنده روشن — همه‌ی انواعِ دریافتی\n"
    "• لاگ همه خاموش — همه‌چیز (هر دو طرف)\n"
    "انگلیسی: log photo on | log photo me off | log photo sender on | log me off | log all on\n"
    "(photo video gif voice videonote sticker audio file text other)\n\n"
    "• لاگ وضعیت — نمایش همه‌ی تنظیم‌ها\n"
    "💡 دستورها رو توی «Saved Messages» بنویس. نابودشونده‌ها اگه کانال تنظیم نباشه توی Saved Messages ذخیره میشن."
)


def parse_cmd(text: str):
    t = normalize(text).strip()
    if not t or len(t) > 300 or "\n" in t:
        return None
    toks = t.split()
    m = [_TOK.get(x.lower(), x.lower()) for x in toks]
    if m[:2] in (["log", "channel"], ["channel", "log"], ["saver", "channel"], ["channel", "saver"]):
        return "channel", " ".join(toks[2:])
    if m in (["help"], ["log", "help"], ["saver", "help"], ["help", "log"], ["help", "saver"]):
        return "help", ""
    if m in (["log", "status"], ["saver", "status"], ["status", "log"], ["status", "saver"]):
        return "status", ""
    if len(m) == 2 and m[0] == "ttl" and m[1] in ("on", "off"):
        return "ttl", m[1]
    if len(m) < 2 or m[0] not in ("log", "saver") or m[-1] not in ("on", "off"):
        return None
    mid = m[1:-1]
    if not mid:
        return "enabled", m[-1]
    if len(mid) == 1 and mid[0] == "ttl":
        return "ttl", m[-1]
    if len(mid) == 1 and mid[0] in _FLAGS:
        return mid[0], m[-1]
    # نوع و/یا جهت:  عکس | عکس خودم | خودم عکس | خودم | فرستنده | همه
    ts = [x for x in mid if x in _EN_TYPES]
    ds = [x for x in mid if x in ("me", "sender")]
    if len(ts) + len(ds) != len(mid) or len(ts) > 1 or len(ds) > 1:
        return None
    return "types", (ts[0] if ts else "all", ds[0] if ds else None, m[-1] == "on")


def set_types(kind: str, direction, val: bool):
    keys = list(TYPES) if kind == "all" else [kind]
    dirs = ["in", "out"] if direction is None else ["out" if direction == "me" else "in"]
    for k in keys:
        for d in dirs:
            S["types"][k][d] = val


def types_result(kind: str, direction, val: bool) -> str:
    who = {None: "خودت و فرستنده", "me": "خودت", "sender": "فرستنده"}[direction]
    what = "همه‌ی انواع" if kind == "all" else f"{TYPES[kind][0]} {TYPES[kind][1]}"
    return f"{what} — {who}: {yn(val)}"


def yn(v) -> str:
    return "روشن ✅" if v else "خاموش ❌"


def tick(v) -> str:
    return "✅" if v else "❌"


def status_text() -> str:
    ch = "—"
    if state["target"] is not None:
        ch = display_name(state["target"]) + (f" (@{state['target'].username})" if getattr(state["target"], "username", None) else "")
    rows = "\n".join(f"{e} {n.split(' (')[0]}:  خودم {tick(S['types'][k]['out'])}  |  فرستنده {tick(S['types'][k]['in'])}"
                     for k, (e, n) in TYPES.items())
    return ("📊 وضعیت سلف ذخیره\n"
            f"کانال: {ch}\nذخیره‌ی پیوی (کلید اصلی): {yn(S['enabled'])}\nنابودشونده‌ها: {yn(S['ttl'])}\n"
            f"ویرایش‌ها: {yn(S['edits'])}\nحذف‌شده‌ها: {yn(S['deleted'])}\nچت ربات‌ها: {yn(S['bots'])}\n\n"
            f"🎛 انواع محتوا:\n{rows}\n\nپیام‌های ردیابی‌شده: {len(MAP)}"
            + ("" if state["target"] is not None else "\n⚠️ کانال تنظیم نشده: «کانال لاگ <لینک یا یوزرنیم>»"))


async def set_channel(event, arg: str) -> str:
    try:
        if arg:
            pid, ent = await resolve_channel(arg)
        else:
            chat = await event.get_chat()
            if isinstance(chat, types.User) or event.is_private:
                return "این دستور رو توی خودِ کانال بنویس، یا آخرش لینک/یوزرنیم/آیدی کانال رو بذار:\nکانال لاگ @mychannel"
            pid, ent = utils.get_peer_id(chat), chat
    except Exception as ex:
        return f"❌ کانال پیدا نشد: {str(ex)[:120]}\n(اکانتت باید عضو یا ادمین کانال باشه)"
    state["target"] = ent
    S["channel"] = pid
    save_settings()
    try:
        await client.send_message(ent, "✅ این کانال به‌عنوان مقصد ذخیره‌ی پیام‌ها تنظیم شد.")
    except Exception as ex:
        return f"⚠️ کانال تنظیم شد ولی نمی‌تونم توش پیام بفرستم ({str(ex)[:100]}). حتماً ادمین با اجازه‌ی ارسال پیام باش."
    return f"✅ کانال تنظیم شد: {display_name(ent)}"


async def del_later(msg, sec=10):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except Exception:
        pass


@client.on(events.NewMessage(outgoing=True))
async def on_cmd(event):
    raw = event.raw_text or ""
    if not raw or len(raw) > 300:
        return
    cmd = parse_cmd(raw)
    if not cmd:
        return
    name, arg = cmd
    if name == "help":
        res = HELP
    elif name == "status":
        res = status_text()
    elif name == "channel":
        res = await set_channel(event, arg)
    elif name == "types":
        set_types(*arg)
        save_settings()
        res = types_result(*arg)
    else:
        key = "enabled" if name == "enabled" else name
        S[key] = arg == "on"
        save_settings()
        label = {"enabled": "ذخیره‌ی پیام‌های پیوی", "ttl": "ذخیره‌ی نابودشونده‌ها", "edits": "ثبت ویرایش‌ها",
                 "deleted": "ثبت حذف‌شده‌ها", "bots": "ذخیره‌ی چت ربات‌ها"}[key]
        res = f"{label}: {yn(S[key])}"
        if key == "enabled" and S[key] and state["target"] is None:
            res += "\n⚠️ هنوز کانال تنظیم نشده: «کانال لاگ <لینک یا یوزرنیم>»"
    try:
        await event.edit(res)
        asyncio.create_task(del_later(event.message, 25 if name in ("help", "status") else 8))
    except Exception:
        await client.send_message("me", res)


# ---------------- اجرا ----------------
async def main():
    await client.start()
    me = await client.get_me()
    state["me_id"] = me.id
    state["premium"] = bool(getattr(me, "premium", False))
    load_state()
    if S.get("channel"):
        try:
            _, state["target"] = await resolve_channel(str(S["channel"]))
        except Exception as ex:
            print(f"⚠️ کانال ذخیره‌شده پیدا نشد ({ex}). دوباره بنویس: کانال لاگ <لینک>")
    asyncio.create_task(flush_loop())
    print(f"✅ سلف ذخیره روشنه ({display_name(me)}) — کانال: {'تنظیم شده' if state['target'] is not None else 'تنظیم نشده'}")
    try:
        await client.run_until_disconnected()
    finally:
        if state["dirty"]:
            save_map()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
