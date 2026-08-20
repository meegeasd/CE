"""
Telegram Bot — Capital Expert Bot for Railway Deployment.

Full-featured Telegram bot with:
- Admin-only access
- Price ticker with glass buttons
- Settings panel (caption, glass buttons, auto-post, admins)
- News monitoring with AI filtering
- Auto-post scheduler
- Webhook support for Railway
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
import tempfile
import time
import traceback
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile,
    Update,
)
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config.settings as cfg
from services.price_fetcher import fetch_all_prices, clear_cache as clear_price_cache
from services.renderer import render_ticker, HAS_RAQM, _FONT_FAMILY

log = logging.getLogger("bot")

# ─── Globals ─────────────────────────────────────────────────────────────
bot: Bot | None = None
dp = Dispatcher()
_render_lock = asyncio.Lock()
_pending: dict[str, dict] = {}
_last_auto_post: dict[str, str] = {}  # date_str -> last posted hour
_waiting_caption: set[int] = set()
_waiting_admin: set[int] = set()
_waiting_auto: set[int] = set()
_news_pending: dict[str, dict] = {}  # hash -> {title, link, source, formatted_text}

# ─── Admin check (dynamic) ──────────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid in cfg.load_admins()

def is_valid_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    try:
        p = urlparse(url)
        return bool(p.scheme in ("http", "https") and p.netloc and "." in p.netloc and not url.endswith("..."))
    except:
        return False

# ─── Keyboards ───────────────────────────────────────────────────────────
def main_kb() -> ReplyKeyboardMarkup:
    """Main keyboard — just 2 buttons."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📊 تابلو قیمتی"), KeyboardButton(text="⚙️ تنظیمات")]],
        resize_keyboard=True,
    )

def glass_kb(include_send: bool = False, callback_id: str | None = None) -> InlineKeyboardMarkup:
    """Glass buttons keyboard. Used BOTH in PM and channel posts."""
    rows = []
    btns = [b for b in cfg.glass_buttons_parsed() if is_valid_url(b[1])]
    for i in range(0, len(btns), 2):
        row = [InlineKeyboardButton(text=n, url=u) for n, u in btns[i:i+2]]
        rows.append(row)
    if include_send and callback_id:
        # Green (positive) button for send-to-channel
        rows.append([InlineKeyboardButton(
            text=f"📤 ارسال به کانال",
            callback_data=f"send:{callback_id}",
            style="positive",  # Telegram colored button feature
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 کپشن", callback_data="set:caption"),
         InlineKeyboardButton(text="🔗 دکمه‌های شیشه‌ای", callback_data="set:glass")],
        [InlineKeyboardButton(text="⏰ ارسال خودکار", callback_data="set:auto"),
         InlineKeyboardButton(text="👥 ادمین‌ها", callback_data="set:admins")],
        [InlineKeyboardButton(text="✅ بستن", callback_data="set:close")],
    ])

def news_approval_kb(news_hash: str) -> InlineKeyboardMarkup:
    """Keyboard for news approval — green approve, red reject."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ ارسال به کانال", callback_data=f"news:approve:{news_hash}", style="positive"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"news:reject:{news_hash}", style="destructive"),
        ],
    ])

# ─── Send ticker (to PM with send button) ───────────────────────────────
async def send_ticker_to_pm(chat_id: int, reply_to: int | None = None) -> bool:
    if not bot:
        return False
    loop = asyncio.get_running_loop()
    t0 = time.time()
    try:
        result = await loop.run_in_executor(None, lambda: asyncio.run(fetch_all_prices()))
        prices = result["prices"]
        log.info(f"Fetched {len(prices)} prices in {time.time()-t0:.1f}s")
    except Exception as e:
        log.error(f"Price fetch failed: {e}")
        await _safe_msg(chat_id, "❌ خطا در دریافت قیمت‌ها.", reply_to)
        return False

    tmp = Path(tempfile.gettempdir())
    cb_id = f"{chat_id}_{int(datetime.now().timestamp())}"
    out = tmp / f"ticker_{cb_id}.png"
    async with _render_lock:
        try:
            await loop.run_in_executor(None, lambda: render_ticker(prices, str(out), handle=cfg.CHANNEL_HANDLE))
        except Exception as e:
            log.error(f"Render failed: {e}")
            await _safe_msg(chat_id, "❌ خطا در ساخت تصویر.", reply_to)
            return False

    caption = cfg.load_caption()
    photo = FSInputFile(str(out))
    kb = glass_kb(include_send=True, callback_id=cb_id)

    try:
        await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption,
            reply_to_message_id=reply_to, allow_sending_without_reply=True, reply_markup=kb)
        log.info(f"Ticker sent to PM in {time.time()-t0:.1f}s")
    except Exception as e:
        log.error(f"Send failed: {e}")
        if "BUTTON_URL_INVALID" in str(e):
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption,
                reply_to_message_id=reply_to, allow_sending_without_reply=True)
        else:
            await _safe_msg(chat_id, "❌ خطا در ارسال.", reply_to)
            return False

    persistent = tmp / f"pending_{cb_id}.png"
    try:
        out.rename(persistent)
    except:
        persistent = out
    _pending[cb_id] = {"chat_id": chat_id, "image_path": str(persistent), "caption": caption}
    return True

async def post_ticker_to_channel() -> bool:
    """Post ticker to channel WITH glass buttons + caption."""
    if not bot or not cfg.CHANNEL_ID:
        return False
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: asyncio.run(fetch_all_prices()))
        prices = result["prices"]
    except Exception as e:
        log.error(f"Price fetch failed for channel post: {e}")
        return False

    tmp = Path(tempfile.gettempdir())
    out = tmp / f"channel_{int(time.time())}.png"
    async with _render_lock:
        try:
            await loop.run_in_executor(None, lambda: render_ticker(prices, str(out), handle=cfg.CHANNEL_HANDLE))
        except Exception as e:
            log.error(f"Render failed: {e}")
            return False

    caption = cfg.load_caption()
    photo = FSInputFile(str(out))
    kb = glass_kb(include_send=False)  # No send button in channel

    try:
        await bot.send_photo(chat_id=cfg.CHANNEL_ID, photo=photo, caption=caption, reply_markup=kb)
        log.info("Posted ticker to channel with glass buttons")
        try:
            out.unlink(missing_ok=True)
        except:
            pass
        return True
    except Exception as e:
        log.error(f"Channel post failed: {e}")
        return False

async def _safe_msg(chat_id, text, reply_to=None):
    if not bot:
        return
    try:
        await bot.send_message(chat_id, text, reply_to_message_id=reply_to)
    except:
        pass

# ─── /start ─────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.answer("⛔ شما به این ربات دسترسی ندارید.")
        return
    name = message.from_user.first_name or "ادمین"
    await message.answer(
        f"سلام {name}! 👋\n\n"
        f"به ربات دستیار <b>{cfg.CHANNEL_NAME}</b> خوش آمدی.\n\n"
        f"📊 تابلو قیمتی دریافت کن\n"
        f"⚙️ تنظیمات ربات\n\n"
        f"🏅 {cfg.CHANNEL_HANDLE}",
        reply_markup=main_kb(),
    )

# ─── Ticker ─────────────────────────────────────────────────────────────
@dp.message(F.text == "📊 تابلو قیمتی")
@dp.message(Command("ticker"))
async def cmd_ticker(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ دسترسی ندارید.")
        return
    await message.answer("⏳ در حال دریافت قیمت‌ها...")
    await send_ticker_to_pm(message.chat.id, reply_to=message.message_id)

# ─── Settings ───────────────────────────────────────────────────────────
@dp.message(F.text == "⚙️ تنظیمات")
async def btn_settings(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ دسترسی ندارید.")
        return
    cap = cfg.load_caption()
    admins = cfg.load_admins()
    schedule = cfg.load_schedule()
    btns = cfg.glass_buttons_parsed()
    await message.answer(
        f"<b>⚙️ تنظیمات</b>\n\n"
        f"📝 <b>کپشن:</b> <code>{cap[:50]}...</code>\n"
        f"🔗 <b>دکمه‌های شیشه‌ای:</b> {len(btns)} عدد\n"
        f"👥 <b>ادمین‌ها:</b> {len(admins)} نفر\n"
        f"⏰ <b>ارسال خودکار:</b> {', '.join(schedule) if schedule else 'غیرفعال'}\n"
        f"🏅 <b>کانال:</b> {cfg.CHANNEL_HANDLE}\n"
        f"🔤 <b>فونت:</b> {_FONT_FAMILY} (libraqm: {HAS_RAQM})\n\n"
        f"برای تغییر، یکی از دکمه‌ها را بزن:",
        reply_markup=settings_kb(),
    )

@dp.callback_query(F.data == "set:close")
async def cb_close(callback: CallbackQuery):
    await callback.message.delete()

@dp.callback_query(F.data == "set:caption")
async def cb_set_caption(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    _waiting_caption.add(callback.from_user.id)
    await callback.message.edit_text(
        "✏️ کپشن جدید را ارسال کنید.\nبرای لغو /cancel\n\n"
        f"کپشن فعلی:\n<pre>{cfg.load_caption()[:300]}</pre>"
    )
    await callback.answer()

@dp.callback_query(F.data == "set:glass")
async def cb_set_glass(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    btns = cfg.glass_buttons_parsed()
    lines = ["<b>🔗 دکمه‌های شیشه‌ای</b>", ""]
    if btns:
        for n, u in btns:
            v = "✅" if is_valid_url(u) else "❌"
            lines.append(f"{v} <b>{n}</b>: <code>{u}</code>")
    else:
        lines.append("هیچ دکمه‌ای تنظیم نشده.")
    lines.append("")
    lines.append("برای تغییر، در فایل <code>.env</code> متغیر GLASS_BUTTONS را ویرایش کنید.")
    lines.append("فرمت: <code>نام1|url1,نام2|url2</code>")
    await callback.message.edit_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ بازگشت", callback_data="set:back")]]))
    await callback.answer()

@dp.callback_query(F.data == "set:auto")
async def cb_set_auto(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    schedule = cfg.load_schedule()
    lines = ["<b>⏰ ارسال خودکار</b>", ""]
    if schedule:
        lines.append(f"زمان‌های فعلی: <code>{', '.join(schedule)}</code>")
    else:
        lines.append("غیرفعال.")
    lines.append("")
    lines.append("برای تنظیم، ساعت‌ها را به فرمت زیر ارسال کنید:")
    lines.append("<code>09:00,21:00</code>")
    lines.append("")
    lines.append("برای غیرفعال کردن: <code>off</code>")
    _waiting_auto.add(callback.from_user.id)
    await callback.message.edit_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ بازگشت", callback_data="set:back")]]))
    await callback.answer()

@dp.callback_query(F.data == "set:admins")
async def cb_set_admins(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    admins = cfg.load_admins()
    lines = ["<b>👥 ادمین‌ها</b>", ""]
    for a in admins:
        env_tag = " (.env)" if a in cfg.ADMIN_IDS else ""
        lines.append(f"• <code>{a}</code>{env_tag}")
    lines.append("")
    lines.append("برای افزودن: <code>/addadmin آیدی</code>")
    lines.append("برای حذف: <code>/deladmin آیدی</code>")
    lines.append("(ادمین‌های .env قابل حذف نیستند)")
    await callback.message.edit_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ بازگشت", callback_data="set:back")]]))
    await callback.answer()

@dp.callback_query(F.data == "set:back")
async def cb_back(callback: CallbackQuery):
    await btn_settings(callback.message)

# ─── Admin commands ──────────────────────────────────────────────────────
@dp.message(Command("addadmin"))
async def cmd_addadmin(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ دسترسی ندارید.")
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("استفاده: <code>/addadmin آیدی عددی</code>")
        return
    uid = int(arg)
    if cfg.add_admin(uid):
        await message.answer(f"✅ ادمین <code>{uid}</code> اضافه شد.")
    else:
        await message.answer(f"⚠️ <code>{uid}</code> قبلاً ادمین است.")

@dp.message(Command("deladmin"))
async def cmd_deladmin(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ دسترسی ندارید.")
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("استفاده: <code>/deladmin آیدی عددی</code>")
        return
    uid = int(arg)
    if cfg.remove_admin(uid):
        await message.answer(f"✅ ادمین <code>{uid}</code> حذف شد.")
    elif uid in cfg.ADMIN_IDS:
        await message.answer("❌ ادمین‌های .env قابل حذف نیستند.")
    else:
        await message.answer(f"⚠️ <code>{uid}</code> ادمین نیست.")

# ─── Send to channel ────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("send:"))
async def cb_send_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین", show_alert=True)
        return
    if not cfg.CHANNEL_ID:
        await callback.answer("❌ CHANNEL_ID تنظیم نشده", show_alert=True)
        return
    cb_id = callback.data.split(":", 1)[1]
    pending = _pending.get(cb_id)
    if not pending:
        await callback.answer("❌ منقضی شده. دوباره بگیرید.", show_alert=True)
        return
    img_path = pending["image_path"]
    if not os.path.exists(img_path):
        await callback.answer("❌ فایل پیدا نشد.", show_alert=True)
        return
    try:
        photo = FSInputFile(img_path)
        kb = glass_kb(include_send=False)
        await bot.send_photo(
            chat_id=cfg.CHANNEL_ID,
            photo=photo,
            caption=pending["caption"],
            reply_markup=kb,
        )
        await callback.answer("✅ به کانال ارسال شد!", show_alert=True)
        log.info(f"Posted to channel by {callback.from_user.id}")
    except Exception as e:
        log.error(f"Channel post failed: {e}")
        await callback.answer(f"❌ {str(e)[:100]}", show_alert=True)

# ─── News approval ───────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("news:approve:"))
async def cb_news_approve(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    news_hash = callback.data.split(":", 2)[2]
    pending = _news_pending.get(news_hash)
    if not pending:
        await callback.answer("❌ خبر منقضی شده.", show_alert=True)
        return

    news = pending["news"]
    formatted = pending.get("formatted_text")

    # Use AI-formatted text if available, otherwise use raw title
    if formatted:
        channel_text = f"{formatted}\n\n🏅 {cfg.CHANNEL_HANDLE}"
    else:
        channel_text = f"📰 <b>{news['title']}</b>\n\n🔗 {news['link']}\n\n🏅 {cfg.CHANNEL_HANDLE}"

    try:
        await bot.send_message(
            chat_id=cfg.CHANNEL_ID,
            text=channel_text,
            parse_mode=ParseMode.HTML,
        )
        await callback.answer("✅ خبر به کانال ارسال شد!", show_alert=True)
        del _news_pending[news_hash]
        log.info(f"News posted to channel: {news['title'][:50]}")
    except Exception as e:
        await callback.answer(f"❌ {e}", show_alert=True)

@dp.callback_query(F.data.startswith("news:reject:"))
async def cb_news_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    news_hash = callback.data.split(":", 2)[2]
    _news_pending.pop(news_hash, None)
    await callback.answer("❌ خبر رد شد.", show_alert=True)
    try:
        await callback.message.delete()
    except:
        pass

# ─── Cancel ─────────────────────────────────────────────────────────────
@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if not is_admin(message.from_user.id):
        return
    _waiting_caption.discard(message.from_user.id)
    _waiting_auto.discard(message.from_user.id)
    await message.answer("❌ لغو شد.", reply_markup=main_kb())

# ─── Catch-all ──────────────────────────────────────────────────────────
@dp.message()
async def catch_all(message: Message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.answer("⛔ شما به این ربات دسترسی ندارید.")
        return

    text = (message.text or "").strip()

    # Caption input
    if uid in _waiting_caption:
        _waiting_caption.discard(uid)
        new_cap = message.text or message.caption or ""
        if not new_cap:
            await message.answer("❌ خالی است.")
            return
        cfg.save_caption(new_cap)
        await message.answer("✅ کپشن ذخیره شد.", reply_markup=main_kb())
        return

    # Auto-post schedule input
    if uid in _waiting_auto:
        _waiting_auto.discard(uid)
        if text.lower() == "off":
            cfg.save_schedule([])
            await message.answer("✅ ارسال خودکار غیرفعال شد.", reply_markup=main_kb())
        else:
            try:
                times = [t.strip() for t in text.split(",")]
                # Validate format
                for t in times:
                    h, m = t.split(":")
                    int(h); int(m)
                cfg.save_schedule(times)
                await message.answer(
                    f"✅ ارسال خودکار تنظیم شد:\n<code>{', '.join(times)}</code>",
                    reply_markup=main_kb(),
                )
            except:
                await message.answer("❌ فرمت اشتباه. مثال: <code>09:00,21:00</code>", reply_markup=main_kb())
        return

    await message.answer(
        "برای دریافت تابلوی قیمتی، دکمه «📊 تابلو قیمتی» را بزنید.",
        reply_markup=main_kb(),
    )

# ─── Errors ──────────────────────────────────────────────────────────────
@dp.errors()
async def on_error(event, exception):
    log.error(f"UNHANDLED: {exception}\n{traceback.format_exc()}")
    return True

# ─── Background tasks ───────────────────────────────────────────────────
async def auto_post_scheduler():
    """Check schedule every 30 seconds. Post at scheduled times."""
    log.info("Auto-post scheduler started")
    while True:
        try:
            await asyncio.sleep(30)
            schedule = cfg.load_schedule()
            if not schedule:
                continue
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            today_key = now.strftime("%Y-%m-%d")

            if current_time in schedule:
                # Check if already posted today at this time
                if _last_auto_post.get(today_key) != current_time:
                    log.info(f"Auto-posting at {current_time}...")
                    await post_ticker_to_channel()
                    _last_auto_post[today_key] = current_time
                    # Clean old entries
                    cutoff = (now - timedelta(days=2)).strftime("%Y-%m-%d")
                    for k in list(_last_auto_post.keys()):
                        if k < cutoff:
                            del _last_auto_post[k]
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Auto-post scheduler error: {e}")
            await asyncio.sleep(60)

async def price_fetch_scheduler():
    """Fetch prices every 6 hours and post to channel."""
    log.info("Price fetch scheduler started (every 6 hours)")
    while True:
        try:
            # Wait 6 hours
            await asyncio.sleep(6 * 3600)
            log.info("Scheduled price fetch (6h)...")
            await post_ticker_to_channel()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Price fetch scheduler error: {e}")
            await asyncio.sleep(60)

# ─── Bot lifecycle ──────────────────────────────────────────────────────
async def create_bot() -> Bot:
    """Create bot instance with proxy support."""
    global bot

    settings = cfg.get_settings()

    # Build proxy variants
    proxy_variants = settings.proxy_variants

    # Try each proxy
    for proxies in proxy_variants:
        try:
            proxy_label = proxies["http"] if proxies else "direct"
            log.info(f"Trying to connect via {proxy_label}...")

            session = AiohttpSession(proxy=proxies["http"] if proxies else None)
            test_bot = Bot(
                token=settings.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                session=session,
            )

            # Test connection
            me = await test_bot.get_me()
            log.info(f"✅ Connected via {proxy_label} as @{me.username}")

            bot = test_bot
            return bot

        except Exception as e:
            log.warning(f"❌ {proxy_label} failed: {str(e)[:100]}")
            try:
                await test_bot.session.close()
            except:
                pass
            continue

    raise RuntimeError("Could not connect to Telegram via any proxy")

async def start_bot():
    """Start the bot (polling or webhook) with always-on web server for healthcheck."""
    global bot

    settings = cfg.get_settings()

    # Create web app (ALWAYS for healthcheck) - start BEFORE bot to pass healthcheck
    app = web.Application()

    # Health check endpoint (always available)
    async def health(request):
        return web.json_response({
            "status": "ok",
            "bot": "running" if bot else "not_configured",
            "mode": "webhook" if settings.webhook_url else "polling" if settings.bot_token else "standby",
            "token_set": bool(settings.bot_token),
        })
    app.router.add_get("/health", health)

    # Start web server FIRST (so healthcheck passes even if bot fails)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    log.info(f"Web server started on port {settings.port}")

    # Check if bot token is configured
    if not settings.bot_token:
        log.error("❌ BOT_TOKEN not set! Bot will run in standby mode.")
        log.error("   Add BOT_TOKEN environment variable in Railway → Variables")
        log.error("   Healthcheck will pass, but bot features won't work until token is set.")
        # Keep web server running for healthcheck
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
        return

    # Create bot (now that token is verified to exist)
    try:
        bot = await create_bot()
    except Exception as e:
        log.error(f"❌ Failed to connect bot: {e}")
        log.error("   Bot will run in standby mode. Fix config and redeploy.")
        # Keep web server running for healthcheck
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
        return

    # Start background tasks (only if bot connected)
    auto_task = asyncio.create_task(auto_post_scheduler())
    price_task = asyncio.create_task(price_fetch_scheduler())

    if settings.webhook_url:
        # Webhook mode for Railway
        log.info(f"Starting webhook on {settings.webhook_url}")
        await bot.set_webhook(settings.webhook_url, drop_pending_updates=True)

        # Register webhook handler
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        mode = "webhook"
    else:
        # Polling mode (development) - still run web server for healthcheck
        log.info("Starting polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        mode = "polling"

    log.info(f"Bot started in {mode} mode")

    # Keep running
    try:
        if settings.webhook_url:
            # Webhook mode - just wait
            await asyncio.Event().wait()
        else:
            # Polling mode - run polling
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        auto_task.cancel()
        price_task.cancel()
        await bot.session.close()
        await runner.cleanup()

# ─── Entry point ────────────────────────────────────────────────────────
async def main():
    settings = cfg.get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    log.info("=" * 50)
    log.info(f"=== {cfg.CHANNEL_NAME} Bot ===")
    log.info("=" * 50)
    log.info(f"Channel: {cfg.CHANNEL_HANDLE} ({cfg.CHANNEL_ID or 'not set'})")
    log.info(f"Admins: {cfg.load_admins()}")
    log.info(f"Font: {_FONT_FAMILY} (libraqm: {HAS_RAQM})")
    log.info(f"Glass buttons: {len(cfg.glass_buttons_parsed())}")
    log.info(f"Auto-post: {cfg.load_schedule() or 'disabled'}")
    log.info(f"Price sources: {settings.price_source_priority}")

    try:
        await start_bot()
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())