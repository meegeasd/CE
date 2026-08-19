# Capital Expert Bot — Railway Deployment

Full-featured Telegram bot for **کپیتال اکسپرت** channel with multi-source price fetching, beautiful ticker rendering, and Railway-ready deployment.

## ✨ Features

- **Multi-source price fetching** with priority-based selection:
  1. **TGJU** (tgju.org) — Most reliable for Iranian market (USD, Gold, Crypto)
  2. **DO_L4** (Telegram channel) — Good fallback for IR prices
  3. **Yahoo Finance** — International symbols (XAU, Oil, Indices, Stocks)
  4. **CoinGecko** — Crypto prices (global, reliable, free tier)
  5. **TSETMC** — Iranian stock market (placeholder)

- **Priority-based conflict resolution**: If BTC price comes from multiple sources, the highest-priority source wins (TGJU > DO_L4 > CoinGecko > Yahoo)

- **Beautiful ticker rendering**: 2x supersampling (2160px → 1080px) for razor-sharp Persian text

- **Full Telegram bot features**:
  - Admin-only access with dynamic admin management
  - Price ticker with glass buttons
  - Settings panel (caption, glass buttons, auto-post schedule, admins)
  - Auto-post to channel every 6 hours + scheduled times
  - News monitoring with AI filtering (Cloudflare Workers AI)
  - Webhook support for Railway production

- **Railway-ready**:
  - Dockerfile with multi-stage build
  - Health check endpoint
  - Webhook mode for production
  - Polling mode for development
  - Non-root user for security

## 🚀 Quick Deploy to Railway

### 1. Push to GitHub
```bash
cd railway_bot
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/meegeasd/CE.git
git push -u origin main
```

### 2. Create Railway Project
1. Go to [Railway](https://railway.app)
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your repository (`meegeasd/CE`)
4. Railway will auto-detect the Dockerfile

### 3. Configure Environment Variables
In Railway dashboard → Variables, add:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | From @BotFather |
| `ADMIN_IDS` | ✅ | Comma-separated Telegram user IDs |
| `CHANNEL_HANDLE` | ✅ | Channel username (e.g. `@CapXpert`) |
| `CHANNEL_ID` | ✅ | Numeric channel ID (e.g. `-100...`) |
| `CHANNEL_NAME` | | Channel display name |
| `GLASS_BUTTONS` | | Format: `name1\|url1,name2\|url2` |
| `TGJU_API_KEY` | Recommended | TGJU API key for best IR prices |
| `COINGECKO_API_KEY` | Optional | CoinGecko Pro key for higher rate limits |
| `COINMARKETCAP_API_KEY` | Optional | CoinMarketCap API key |
| `TSETMC_API_KEY` | Optional | TSETMC API key |
| `CF_ACCOUNT_ID` | Optional | Cloudflare Account ID for AI news |
| `CF_API_TOKEN` | Optional | Cloudflare API Token for AI news |
| `CRYPTOPANIC_KEY` | Optional | CryptoPanic API key |
| `PRICE_SOURCE_PRIORITY` | | Default: `tgju,do_l4,yahoo,coingecko,tsetmc` |
| `WEBHOOK_URL` | ✅ (prod) | Railway URL + `/webhook` (e.g. `https://xxx.up.railway.app/webhook`) |
| `AUTO_POST_TIMES` | | Scheduled auto-post times (e.g. `09:00,14:00,21:00`) |
| `LOG_LEVEL` | | `INFO`, `DEBUG`, etc. |

### 4. Set Webhook URL
After deployment, Railway gives you a URL like `https://xxx.up.railway.app`. Set:
```
WEBHOOK_URL=https://xxx.up.railway.app/webhook
```
Then redeploy. The bot will switch to webhook mode automatically.

### 5. Verify Deployment
Check `/health` endpoint:
```
https://xxx.up.railway.app/health
```
Should return `{"status": "ok", "bot": "running"}`

## 📱 Bot Usage

1. **Start**: `/start` → Welcome message with main keyboard
2. **Price Ticker**: Click 📊 تابلو قیمتی → Get ticker in PM with "Send to Channel" button
3. **Settings**: Click ⚙️ تنظیمات → Configure caption, glass buttons, auto-post, admins
4. **Admin Commands**:
   - `/addadmin <user_id>` — Add admin
   - `/deladmin <user_id>` — Remove admin
   - `/ticker` — Force price fetch
   - `/cancel` — Cancel current input

## 🏗 Local Development

```bash
# Clone and setup
cd railway_bot
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values

# Run in polling mode (no webhook)
python -m bot.main
```

## 🐳 Docker Build (Local)

```bash
docker build -t capital-expert-bot .
docker run -d \
  -e BOT_TOKEN=xxx \
  -e ADMIN_IDS=860835914 \
  -e CHANNEL_HANDLE=@CapXpert \
  -e CHANNEL_ID=-100... \
  -p 8080:8080 \
  capital-expert-bot
```

## 📊 Price Sources Detail

| Source | Symbols | Reliability | Rate Limit | API Key |
|--------|---------|-------------|------------|---------|
| TGJU | USD, EUR, GBP, Gold, Coins, Crypto | ⭐⭐⭐⭐⭐ | High | Recommended |
| DO_L4 | All IR prices + BTC/ETH/SOL | ⭐⭐⭐⭐ | Medium | No |
| Yahoo Finance | XAU, XAG, Oil, FX, Indices, Stocks | ⭐⭐⭐⭐ | High | No |
| CoinGecko | BTC, ETH, SOL, USDT, Gold, Silver | ⭐⭐⭐⭐ | 100-500/min | Optional |
| TSETMC | Iranian stocks | ⭐⭐⭐ | Low | Required |

## 🔧 Configuration Details

### Price Source Priority
The `PRICE_SOURCE_PRIORITY` env var controls which source wins when multiple sources have the same symbol.

Example: `tgju,do_l4,yahoo,coingecko,tsetmc`

For BTC:
1. First tries TGJU (if API key configured)
2. Then DO_L4
3. Then CoinGecko
4. Then Yahoo Finance

### Glass Buttons Format
```
GLASS_BUTTONS=وب‌سایت|https://capxpert.com,اینستاگرام|https://instagram.com/capxpert,تلگرام|https://t.me/CapXpert
```

### Auto-post Schedule
```
AUTO_POST_TIMES=09:00,14:00,21:00
```
Plus automatic posting every 6 hours.

## 📁 Project Structure

```
railway_bot/
├── bot/
│   └── main.py           # Main bot entry point
├── config/
│   ├── __init__.py       # Backward compatibility exports
│   └── settings.py       # Pydantic settings management
├── services/
│   ├── __init__.py
│   ├── price_fetcher.py  # Multi-source price fetching
│   └── renderer.py       # High-quality ticker rendering
├── assets/
│   └── logo.png          # Bot logo (copy from original)
├── fonts/
│   ├── Vazirmatn-*.ttf   # Persian fonts (copy from original)
├── data/                 # Runtime data (auto-created)
├── Dockerfile
├── railway.toml
├── requirements.txt
├── .env.example
└── README.md
```

## 🔐 Security Notes

- Bot runs as non-root user in Docker
- All secrets via environment variables (never in code)
- Admin-only access enforced
- Webhook validates Telegram requests
- Proxy support for Iran deployment

## 📝 License

MIT License — Free to use and modify.

---

**Built for Railway** • **Powered by aiogram 3** • **Persian-first design**