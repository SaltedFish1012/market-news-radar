from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in ('1', 'true', 'yes', 'on')

@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv('DB_PATH', './data/radar.db')
    config_dir: Path = Path(os.getenv('CONFIG_DIR', './config'))
    token: str = os.getenv('DASHBOARD_TOKEN', '')
    alpaca_key: str = os.getenv('ALPACA_API_KEY', '')
    alpaca_secret: str = os.getenv('ALPACA_SECRET_KEY', '')
    alpaca_url: str = os.getenv('ALPACA_NEWS_URL', 'wss://stream.data.alpaca.markets/v1beta1/news')
    lb_enabled: bool = _bool('LB_ENABLED')
    lb_news_enabled: bool = _bool('LB_NEWS_ENABLED')
    lb_overnight_enabled: bool = _bool('LONGBRIDGE_ENABLE_OVERNIGHT', True)
    lb_app_key: str = field(default=os.getenv('LONGBRIDGE_APP_KEY', ''), repr=False)
    lb_app_secret: str = field(default=os.getenv('LONGBRIDGE_APP_SECRET', ''), repr=False)
    lb_access_token: str = field(default=os.getenv('LONGBRIDGE_ACCESS_TOKEN', ''), repr=False)
    lb_client_id: str = os.getenv('LONGBRIDGE_CLIENT_ID', '')
    alpaca_news_enabled: bool = _bool('ALPACA_NEWS_ENABLED', bool(os.getenv('ALPACA_API_KEY') and os.getenv('ALPACA_SECRET_KEY')))
    ai_enabled: bool = _bool('AI_ENABLED', bool(os.getenv('AI_API_KEY')))
    ai_provider: str = os.getenv('AI_PROVIDER', 'openai' if os.getenv('AI_BASE_URL', 'https://api.openai.com/v1').rstrip('/') == 'https://api.openai.com/v1' else 'compatible')
    ai_key: str = os.getenv('AI_API_KEY', '')
    ai_base_url: str = os.getenv('AI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    ai_model: str = os.getenv('AI_MODEL', 'gpt-4o-mini')
    ai_max_requests_per_hour: int = int(os.getenv('AI_MAX_REQUESTS_PER_HOUR', '60'))
    ai_timeout_seconds: int = int(os.getenv('AI_TIMEOUT_SECONDS', '45'))
    notifications_enabled: bool = _bool('NOTIFICATIONS_ENABLED', True)
    market_provider: str = os.getenv('MARKET_PROVIDER', 'yahoo')
    telegram_token: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    telegram_chat: str = os.getenv('TELEGRAM_CHAT_ID', '')
    bark_key: str = os.getenv('BARK_KEY', '')
    bark_base: str = os.getenv('BARK_BASE_URL', 'https://api.day.app').rstrip('/')
    sec_contact: str = os.getenv('SEC_CONTACT', '').strip()
    rss_interval: int = max(30, int(os.getenv('RSS_INTERVAL_SECONDS', '90')))
    lb_news_interval: int = max(60, int(os.getenv('LB_NEWS_INTERVAL_SECONDS', '120')))
    news_max_age_minutes: int = int(os.getenv('NEWS_MAX_AGE_MINUTES', '25'))
    x_enabled: bool = _bool('X_ENABLED')
    x_bearer_token: str = field(default=os.getenv('X_BEARER_TOKEN', ''), repr=False)
    x_poll_interval: int = int(os.getenv('X_POLL_INTERVAL_SECONDS', '120'))
    x_accounts: list[dict] = field(default_factory=list)
    test_mode: bool = _bool('TEST_MODE')
    watchlist_override: dict | None = field(default=None, repr=False)
    feeds_override: list | None = field(default=None, repr=False)

    def read_json(self, fname: str):
        if fname == 'watchlist.json' and self.watchlist_override is not None:
            return self.watchlist_override
        if fname == 'feeds.json' and self.feeds_override is not None:
            return self.feeds_override
        with open(self.config_dir / fname, encoding='utf-8') as f:
            return json.load(f)

    def validate(self):
        if not self.token or self.token.startswith('replace-') or len(self.token) < 24:
            raise ValueError('DASHBOARD_TOKEN must be at least 24 random characters')

settings = Settings()
