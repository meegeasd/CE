"""
Configuration for Capital Expert Bot — Railway Deployment
Uses pydantic-settings for type-safe environment variable loading.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List, Optional
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram Bot
    bot_token: str = Field(..., description="Telegram Bot Token from @BotFather")
    admin_ids: List[int] = Field(default_factory=list, description="Comma-separated admin user IDs")
    channel_handle: str = Field(default="@CapXpert", description="Channel username with @")
    channel_name: str = Field(default="کپیتال اکسپرت", description="Channel display name")
    channel_id: Optional[int] = Field(default=None, description="Channel numeric ID (e.g. -100...)")

    # Proxy (all optional for Railway - can run without proxy if not in Iran)
    proxy: Optional[str] = Field(default=None, description="Primary proxy URL (socks5:// or http://)")
    proxy_fallbacks: List[str] = Field(default_factory=list, description="Comma-separated fallback proxies")
    auto_detect_proxy: bool = Field(default=True, description="Auto-detect common local proxy ports")
    try_direct_fetch: bool = Field(default=True, description="Try fetching without proxy as fallback")

    # Price Sources Priority (higher = more trusted)
    # Sources: "do_l4", "yahoo", "tgju", "tsetmc", "coingecko", "coinmarketcap"
    price_source_priority: List[str] = Field(
        default_factory=lambda: ["tgju", "do_l4", "yahoo", "coingecko", "tsetmc"],
        description="Ordered list of price sources by reliability"
    )

    # API Keys for additional sources
    tgju_api_key: Optional[str] = Field(default=None, description="TGJU API key if available")
    tsetmc_api_key: Optional[str] = Field(default=None, description="TSETMC API key if available")
    coingecko_api_key: Optional[str] = Field(default=None, description="CoinGecko Pro API key (optional)")
    coinmarketcap_api_key: Optional[str] = Field(default=None, description="CoinMarketCap API key (optional)")
    cryptopanic_key: Optional[str] = Field(default=None, description="CryptoPanic API key")

    # Cloudflare Workers AI (for news filtering)
    cf_account_id: Optional[str] = Field(default=None, description="Cloudflare Account ID")
    cf_api_token: Optional[str] = Field(default=None, description="Cloudflare API Token")
    news_poll_interval: int = Field(default=300, description="News check interval in seconds")

    # Glass buttons
    glass_buttons: str = Field(default="", description="Format: name1|url1,name2|url2")

    # Auto-post schedule
    auto_post_times: List[str] = Field(default_factory=list, description="HH:MM format, comma-separated")

    # Railway / Deployment
    railway_environment: Optional[str] = Field(default=None, description="Railway environment name")
    port: int = Field(default=8080, description="Web server port (Railway sets PORT)")
    webhook_url: Optional[str] = Field(default=None, description="Webhook URL for production (optional)")
    log_level: str = Field(default="INFO", description="Logging level")

    # Data paths
    data_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "data")
    assets_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "assets")
    fonts_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "fonts")

    # Caption file
    caption_file: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "data" / "caption.txt")
    admins_file: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "data" / "admins.json")
    schedule_file: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "data" / "schedule.json")

    # Default caption
    default_caption: str = "📊 تابلوی قیمتی لحظه‌ای\n🏅 {channel_handle}"

    @property
    def proxy_variants(self) -> List[Optional[dict]]:
        """Build list of proxy configurations to try in order."""
        variants = []

        if self.proxy:
            variants.append({"http": self.proxy, "https": self.proxy})

        for fb in self.proxy_fallbacks:
            if fb:
                variants.append({"http": fb, "https": fb})

        if self.auto_detect_proxy:
            common_ports = [
                ("socks5", 3067), ("http", 3067),
                ("socks5", 10808), ("http", 10809),
                ("socks5", 10809), ("http", 7890),
                ("socks5", 7891), ("socks5", 1080),
                ("http", 1087), ("socks5", 1086),
            ]
            for scheme, port in common_ports:
                url = f"{scheme}://127.0.0.1:{port}"
                proxy_dict = {"http": url, "https": url}
                if proxy_dict not in variants:
                    variants.append(proxy_dict)

        if self.try_direct_fetch:
            variants.append(None)  # Try direct connection last

        return variants

    def glass_buttons_parsed(self) -> List[tuple[str, str]]:
        """Parse GLASS_BUTTONS string into list of (name, url) tuples."""
        out = []
        if not self.glass_buttons:
            return out
        for item in self.glass_buttons.split(","):
            item = item.strip()
            if "|" not in item:
                continue
            name, url = item.split("|", 1)
            name, url = name.strip(), url.strip()
            if name and url and url.startswith("http"):
                out.append((name, url))
        return out


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


# Backward compatibility - expose as module-level constants
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