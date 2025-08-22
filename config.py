import os
from dotenv import load_dotenv
import ssl

# Fix OpenSSL issue
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except:
    pass

load_dotenv()

class Config:
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./copy_trading.db")
    
    # Redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # Security
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
    
    # Development/Test mode
    TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
    SKIP_CREDENTIAL_VALIDATION = os.getenv("SKIP_CREDENTIAL_VALIDATION", "false").lower() == "true"
    ALLOW_SUBACCOUNT_BYPASS = os.getenv("ALLOW_SUBACCOUNT_BYPASS", "false").lower() == "true"
    BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    
    # Copy Trading Settings
    DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "10"))
    DEFAULT_RISK_PERCENTAGE = float(os.getenv("DEFAULT_RISK_PERCENTAGE", "10.0"))
    MAX_LEVERAGE = int(os.getenv("MAX_LEVERAGE", "20"))
    
    # WebSocket settings
    WEBSOCKET_PING_INTERVAL = 20
    WEBSOCKET_PING_TIMEOUT = 20
    
    # Trading settings
    MIN_ORDER_SIZE = float(os.getenv("MIN_ORDER_SIZE", "10.0"))
    MAX_ORDER_SIZE = float(os.getenv("MAX_ORDER_SIZE", "10000.0"))
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.getenv("LOG_FILE", "copy_trading.log")
    
    # Monitoring
    HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))
    TRADE_SYNC_DELAY = float(os.getenv("TRADE_SYNC_DELAY", "1.0"))
    
    # Supported symbols (futures)
    SUPPORTED_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "SOLUSDT",
        "DOTUSDT", "LINKUSDT", "MATICUSDT", "AVAXUSDT", "UNIUSDT"
    ]
