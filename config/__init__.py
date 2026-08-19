"""
Config module — Configuration management for Capital Expert Bot.
"""
from .settings import get_settings, Settings

# Backward compatibility
_settings = get_settings()

BOT_TOKEN = _settings.bot_token
ADMIN_IDS = _settings.admin_ids
CHANNEL_HANDLE = _settings.channel_handle
CHANNEL_NAME = _settings.channel_name
CHANNEL_ID = _settings.channel_id
PROXY = _settings.proxy
PROXY_FALLBACKS = _settings.proxy_fallbacks
AUTO_DETECT_PROXY = _settings.auto_detect_proxy
TRY_DIRECT_FETCH = _settings.try_direct_fetch
PRICE_SOURCE_PRIORITY = _settings.price_source_priority
TGJU_API_KEY = _settings.tgju_api_key
TSETMC_API_KEY = _settings.tsetmc_api_key
COINGECKO_API_KEY = _settings.coingecko_api_key
COINMARKETCAP_API_KEY = _settings.coinmarketcap_api_key
CRYPTOPANIC_KEY = _settings.cryptopanic_key
CF_ACCOUNT_ID = _settings.cf_account_id
CF_API_TOKEN = _settings.cf_api_token
NEWS_POLL_INTERVAL = _settings.news_poll_interval
GLASS_BUTTONS_RAW = _settings.glass_buttons
DATA_DIR = _settings.data_dir
CAPTION_FILE = _settings.caption_file
ADMINS_FILE = _settings.admins_file
SCHEDULE_FILE = _settings.schedule_file
DEFAULT_CAPTION = _settings.default_caption.format(channel_handle=CHANNEL_HANDLE)

from .settings import load_admins, save_admins, add_admin, remove_admin
from .settings import load_caption, save_caption
from .settings import load_schedule, save_schedule
from .settings import glass_buttons_parsed, fetch_proxies, all_proxy_variants

__all__ = [
    "get_settings", "Settings",
    "BOT_TOKEN", "ADMIN_IDS", "CHANNEL_HANDLE", "CHANNEL_NAME", "CHANNEL_ID",
    "PROXY", "PROXY_FALLBACKS", "AUTO_DETECT_PROXY", "TRY_DIRECT_FETCH",
    "PRICE_SOURCE_PRIORITY", "TGJU_API_KEY", "TSETMC_API_KEY",
    "COINGECKO_API_KEY", "COINMARKETCAP_API_KEY", "CRYPTOPANIC_KEY",
    "CF_ACCOUNT_ID", "CF_API_TOKEN", "NEWS_POLL_INTERVAL",
    "GLASS_BUTTONS_RAW", "DATA_DIR", "CAPTION_FILE", "ADMINS_FILE", "SCHEDULE_FILE",
    "DEFAULT_CAPTION",
    "load_admins", "save_admins", "add_admin", "remove_admin",
    "load_caption", "save_caption",
    "load_schedule", "save_schedule",
    "glass_buttons_parsed", "fetch_proxies", "all_proxy_variants",
]