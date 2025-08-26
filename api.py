from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging
from datetime import datetime
import asyncio

from models import Account, Trade, Position, CopyTradingConfig, SystemLog, get_session
from copy_trading_engine import copy_trading_engine
from binance_client import BinanceClient
from config import Config

# Setup logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="Copy Trading Bot API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

# Pydantic models
class AccountCreate(BaseModel):
    name: str
    api_key: str
    secret_key: str
    is_master: bool = False
    leverage: int = 10
    risk_percentage: float = 10.0

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    leverage: Optional[int] = None
    risk_percentage: Optional[float] = None

class CopyTradingConfigCreate(BaseModel):
    master_account_id: int
    follower_account_id: int
    copy_percentage: float = 100.0
    risk_multiplier: float = 1.0
    max_risk_percentage: float = 50.0

class CopyTradingConfigUpdate(BaseModel):
    is_active: Optional[bool] = None
    copy_percentage: Optional[float] = None
    risk_multiplier: Optional[float] = None
    max_risk_percentage: Optional[float] = None

class TradeCreate(BaseModel):
    account_id: int
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None

# Dependency
def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()

# Authentication middleware (simple implementation)
async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # In production, implement proper JWT token verification
    if credentials.credentials != "butter1011":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow()}

# Account management
@app.post("/accounts", response_model=Dict)
async def create_account(account: AccountCreate, db = Depends(get_db)):
    """Create a new account"""
    try:
        # Check if we should skip credential validation (for testing)
        if not Config.SKIP_CREDENTIAL_VALIDATION:
            # Test connection to Binance
            logger.info(f"Validating API credentials for account: {account.name} (is_master: {account.is_master})")
            client = BinanceClient(
                api_key=account.api_key,
                secret_key=account.secret_key,
                testnet=Config.BINANCE_TESTNET
            )
            
            if not await client.test_connection():
                error_msg = f"API credential validation failed for account '{account.name}'. "
                if account.is_master:
                    error_msg += "Master accounts require futures trading permissions. "
                else:
                    error_msg += "Subaccounts may have limited permissions. "
                error_msg += "Please check your Binance API key, secret, and permissions."
                logger.error(error_msg)
                raise HTTPException(status_code=400, detail=error_msg)
            else:
                logger.info(f"✓ API credentials validated successfully for account: {account.name}")
        else:
            logger.info("Skipping credential validation (test mode)")
        
        # Create account in database
        db_account = Account(
            name=account.name,
            api_key=account.api_key,
            secret_key=account.secret_key,
            is_master=account.is_master,
            leverage=account.leverage,
            risk_percentage=account.risk_percentage
        )
        
        db.add(db_account)
        db.commit()
        db.refresh(db_account)
        
        # Add to copy trading engine
        await copy_trading_engine.add_account(db_account)
        
        return {
            "id": db_account.id,
            "name": db_account.name,
            "is_master": db_account.is_master,
            "message": "Account created successfully"
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error creating account: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/accounts", response_model=List[Dict])
async def get_accounts(db = Depends(get_db)):
    """Get all accounts with live wallet balance when available"""
    try:
        accounts = db.query(Account).all()

        async def fetch_wallet_balance(acc: Account) -> float:
            try:
                client = BinanceClient(
                    api_key=acc.api_key,
                    secret_key=acc.secret_key,
                    testnet=Config.BINANCE_TESTNET
                )
                wallet = await client.get_total_wallet_balance()
                # Fallback to available balance if wallet is zero (limited permissions)
                if wallet <= 0:
                    available = await client.get_balance()
                    return available if available > 0 else acc.balance
                return wallet
            except Exception as e:
                logger.warning(f"Failed to fetch live balance for account {acc.id}: {e}")
                return acc.balance

        # Fetch balances concurrently
        live_balances = await asyncio.gather(*[fetch_wallet_balance(acc) for acc in accounts])

        result = []
        for acc, live_balance in zip(accounts, live_balances):
            result.append({
                "id": acc.id,
                "name": acc.name,
                "is_master": acc.is_master,
                "is_active": acc.is_active,
                "leverage": acc.leverage,
                "risk_percentage": acc.risk_percentage,
                "balance": live_balance,
                "created_at": acc.created_at
            })

        return result
    except Exception as e:
        logger.error(f"Error getting accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/accounts/{account_id}", response_model=Dict)
async def get_account(account_id: int, db = Depends(get_db)):
    """Get account by ID"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        return {
            "id": account.id,
            "name": account.name,
            "is_master": account.is_master,
            "is_active": account.is_active,
            "leverage": account.leverage,
            "risk_percentage": account.risk_percentage,
            "balance": account.balance,
            "created_at": account.created_at
        }
    except Exception as e:
        logger.error(f"Error getting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/accounts/{account_id}", response_model=Dict)
async def update_account(account_id: int, account_update: AccountUpdate, db = Depends(get_db)):
    """Update account"""
    try:
        db_account = db.query(Account).filter(Account.id == account_id).first()
        if not db_account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Update fields
        if account_update.name is not None:
            db_account.name = account_update.name
        if account_update.is_active is not None:
            db_account.is_active = account_update.is_active
        if account_update.leverage is not None:
            db_account.leverage = account_update.leverage
        if account_update.risk_percentage is not None:
            db_account.risk_percentage = account_update.risk_percentage
        
        db_account.updated_at = datetime.utcnow()
        db.commit()
        
        return {"message": "Account updated successfully"}
        
    except Exception as e:
        logger.error(f"Error updating account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db = Depends(get_db)):
    """Delete account"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Remove from copy trading engine
        await copy_trading_engine.remove_account(account_id)
        
        # Delete from database
        db.delete(account)
        db.commit()
        
        return {"message": "Account deleted successfully"}
        
    except Exception as e:
        logger.error(f"Error deleting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Copy trading configuration
@app.post("/copy-trading-config", response_model=Dict)
async def create_copy_trading_config(config: CopyTradingConfigCreate, db = Depends(get_db)):
    """Create copy trading configuration"""
    try:
        # Validate accounts exist
        master = db.query(Account).filter(Account.id == config.master_account_id).first()
        follower = db.query(Account).filter(Account.id == config.follower_account_id).first()
        
        if not master or not follower:
            raise HTTPException(status_code=404, detail="Master or follower account not found")
        
        if not master.is_master:
            raise HTTPException(status_code=400, detail="Master account must be marked as master")
        
        # Create configuration
        db_config = CopyTradingConfig(
            master_account_id=config.master_account_id,
            follower_account_id=config.follower_account_id,
            copy_percentage=config.copy_percentage,
            risk_multiplier=config.risk_multiplier,
            max_risk_percentage=config.max_risk_percentage
        )
        
        db.add(db_config)
        db.commit()
        db.refresh(db_config)
        
        return {
            "id": db_config.id,
            "master_account_id": db_config.master_account_id,
            "follower_account_id": db_config.follower_account_id,
            "message": "Copy trading configuration created successfully"
        }
        
    except Exception as e:
        logger.error(f"Error creating copy trading config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/copy-trading-config", response_model=List[Dict])
async def get_copy_trading_configs(db = Depends(get_db)):
    """Get all copy trading configurations"""
    try:
        configs = db.query(CopyTradingConfig).all()
        return [
            {
                "id": config.id,
                "master_account_id": config.master_account_id,
                "follower_account_id": config.follower_account_id,
                "is_active": config.is_active,
                "copy_percentage": config.copy_percentage,
                "risk_multiplier": config.risk_multiplier,
                "max_risk_percentage": config.max_risk_percentage,
                "created_at": config.created_at
            }
            for config in configs
        ]
    except Exception as e:
        logger.error(f"Error getting copy trading configs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/copy-trading-config/{config_id}", response_model=Dict)
async def update_copy_trading_config(config_id: int, config_update: CopyTradingConfigUpdate, db = Depends(get_db)):
    """Update copy trading configuration"""
    try:
        db_config = db.query(CopyTradingConfig).filter(CopyTradingConfig.id == config_id).first()
        if not db_config:
            raise HTTPException(status_code=404, detail="Configuration not found")
        
        # Update fields
        if config_update.is_active is not None:
            db_config.is_active = config_update.is_active
        if config_update.copy_percentage is not None:
            db_config.copy_percentage = config_update.copy_percentage
        if config_update.risk_multiplier is not None:
            db_config.risk_multiplier = config_update.risk_multiplier
        if config_update.max_risk_percentage is not None:
            db_config.max_risk_percentage = config_update.max_risk_percentage
        
        db_config.updated_at = datetime.utcnow()
        db.commit()
        
        return {"message": "Configuration updated successfully"}
        
    except Exception as e:
        logger.error(f"Error updating copy trading config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/copy-trading-config/{config_id}")
async def delete_copy_trading_config(config_id: int, db = Depends(get_db)):
    """Delete copy trading configuration"""
    try:
        config = db.query(CopyTradingConfig).filter(CopyTradingConfig.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="Configuration not found")
        
        # Delete from database
        db.delete(config)
        db.commit()
        
        return {"message": "Configuration deleted successfully"}
        
    except Exception as e:
        logger.error(f"Error deleting copy trading config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Trade management
@app.post("/trades", response_model=Dict)
async def create_trade(trade: TradeCreate, db = Depends(get_db)):
    """Create a new trade (for manual trading)"""
    try:
        # Validate account exists
        account = db.query(Account).filter(Account.id == trade.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Create trade in database
        db_trade = Trade(
            account_id=trade.account_id,
            symbol=trade.symbol,
            side=trade.side,
            order_type=trade.order_type,
            quantity=trade.quantity,
            price=trade.price,
            stop_price=trade.stop_price,
            take_profit_price=trade.take_profit_price
        )
        
        db.add(db_trade)
        db.commit()
        db.refresh(db_trade)
        
        return {
            "id": db_trade.id,
            "symbol": db_trade.symbol,
            "side": db_trade.side,
            "message": "Trade created successfully"
        }
        
    except Exception as e:
        logger.error(f"Error creating trade: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/trades", response_model=List[Dict])
async def get_trades(account_id: Optional[int] = None, db = Depends(get_db)):
    """Get trades"""
    try:
        query = db.query(Trade)
        if account_id:
            query = query.filter(Trade.account_id == account_id)
        
        trades = query.order_by(Trade.created_at.desc()).limit(100).all()
        
        return [
            {
                "id": trade.id,
                "account_id": trade.account_id,
                "symbol": trade.symbol,
                "side": trade.side,
                "order_type": trade.order_type,
                "quantity": trade.quantity,
                "price": trade.price,
                "status": trade.status,
                "copied_from_master": trade.copied_from_master,
                "created_at": trade.created_at
            }
            for trade in trades
        ]
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# System status and control
@app.get("/status", response_model=Dict)
async def get_system_status():
    """Get system status"""
    try:
        engine_status = await copy_trading_engine.get_engine_status()
        
        return {
            "copy_trading_engine": engine_status,
            "timestamp": datetime.utcnow()
        }
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/start")
async def start_copy_trading():
    """Start copy trading"""
    try:
        await copy_trading_engine.start_monitoring()
        return {"message": "Copy trading started successfully"}
    except Exception as e:
        logger.error(f"Error starting copy trading: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
async def stop_copy_trading():
    """Stop copy trading"""
    try:
        await copy_trading_engine.stop_monitoring()
        return {"message": "Copy trading stopped successfully"}
    except Exception as e:
        logger.error(f"Error stopping copy trading: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/initialize")
async def initialize_system():
    """Initialize the copy trading system"""
    try:
        success = await copy_trading_engine.initialize()
        if success:
            return {"message": "System initialized successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to initialize system")
    except Exception as e:
        logger.error(f"Error initializing system: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/force-check-trades")
async def force_check_trades():
    """Force immediate check for new trades in all master accounts"""
    try:
        logger.info("🔄 Manual trade check triggered")
        results = {}
        
        for master_id, client in copy_trading_engine.master_clients.items():
            try:
                await copy_trading_engine.check_master_trades(master_id, client)
                results[master_id] = "checked"
                logger.info(f"✅ Manually checked trades for master {master_id}")
            except Exception as e:
                results[master_id] = f"error: {str(e)}"
                logger.error(f"❌ Error checking master {master_id}: {e}")
        
        return {
            "message": "Manual trade check completed",
            "results": results,
            "timestamp": datetime.utcnow()
        }
    except Exception as e:
        logger.error(f"Error in manual trade check: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# System logs
@app.get("/logs", response_model=List[Dict])
async def get_logs(level: Optional[str] = None, limit: int = 100, db = Depends(get_db)):
    """Get system logs"""
    try:
        query = db.query(SystemLog)
        if level:
            query = query.filter(SystemLog.level == level.upper())
        
        logs = query.order_by(SystemLog.created_at.desc()).limit(limit).all()
        
        return [
            {
                "id": log.id,
                "level": log.level,
                "message": log.message,
                "account_id": log.account_id,
                "trade_id": log.trade_id,
                "created_at": log.created_at
            }
            for log in logs
        ]
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/logs/cleanup")
async def cleanup_logs(max_logs_per_level: int = 500):
    """Clean up old system logs to prevent database bloat"""
    try:
        from copy_trading_engine import copy_trading_engine
        
        cleaned_count = copy_trading_engine.cleanup_old_logs(max_logs_per_level)
        
        return {
            "message": f"Successfully cleaned up {cleaned_count} old logs",
            "cleaned_count": cleaned_count,
            "max_logs_per_level": max_logs_per_level
        }
    except Exception as e:
        logger.error(f"Error cleaning up logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/logs/clear-all")
async def clear_all_logs(db = Depends(get_db)):
    """Clear ALL system logs from the database"""
    try:
        # Count logs before deletion
        total_logs = db.query(SystemLog).count()
        
        # Delete all logs
        db.query(SystemLog).delete()
        db.commit()
        
        logger.info(f"🧹 Cleared all {total_logs} system logs from database")
        
        return {
            "message": f"Successfully cleared all logs",
            "cleared_count": total_logs,
            "status": "success"
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error clearing all logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
