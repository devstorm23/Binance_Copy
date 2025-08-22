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
    """Get all accounts"""
    try:
        accounts = db.query(Account).all()
        return [
            {
                "id": acc.id,
                "name": acc.name,
                "is_master": acc.is_master,
                "is_active": acc.is_active,
                "leverage": acc.leverage,
                "risk_percentage": acc.risk_percentage,
                "balance": acc.balance,
                "created_at": acc.created_at
            }
            for acc in accounts
        ]
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

@app.get("/debug/orders/{account_id}")
async def debug_orders(account_id: int, db = Depends(get_db)):
    """Debug endpoint to check what orders are being detected"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        client = BinanceClient(
            api_key=account.api_key,
            secret_key=account.secret_key,
            testnet=Config.BINANCE_TESTNET
        )
        
        # Test connection
        connected = await client.test_connection()
        if not connected:
            return {"error": "Failed to connect to Binance API"}
        
        # Get open orders
        open_orders = await client.get_open_orders()
        
        # Get recent orders from copy trading engine
        from datetime import timedelta
        since_time = datetime.utcnow() - timedelta(hours=1)
        recent_orders = await copy_trading_engine.get_recent_orders(client, since_time)
        
        return {
            "account_id": account_id,
            "account_name": account.name,
            "connection_status": "connected",
            "open_orders_count": len(open_orders),
            "open_orders": open_orders,
            "recent_orders_count": len(recent_orders),
            "recent_orders": recent_orders,
            "processed_orders": list(copy_trading_engine.processed_orders.get(account_id, set())),
            "timestamp": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error in debug orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/debug/process-order/{account_id}/{order_id}")
async def debug_process_order(account_id: int, order_id: str, db = Depends(get_db)):
    """Debug endpoint to manually process a specific order"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        if not account.is_master:
            raise HTTPException(status_code=400, detail="Account must be a master account")
        
        client = BinanceClient(
            api_key=account.api_key,
            secret_key=account.secret_key,
            testnet=Config.BINANCE_TESTNET
        )
        
        # Get open orders and find the specific order
        open_orders = await client.get_open_orders()
        target_order = None
        for order in open_orders:
            if str(order['orderId']) == order_id:
                target_order = order
                break
        
        if not target_order:
            return {"error": f"Order {order_id} not found in open orders"}
        
        logger.info(f"🔧 Debug: Manually processing order {order_id} for account {account_id}")
        
        # Process the order
        await copy_trading_engine.process_master_order(account_id, target_order)
        
        return {
            "message": f"Order {order_id} processed successfully",
            "order_details": target_order,
            "timestamp": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error in debug process order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/debug/clear-processed/{account_id}")
async def clear_processed_orders(account_id: int):
    """Clear processed orders cache for an account (for debugging)"""
    try:
        if account_id in copy_trading_engine.processed_orders:
            count = len(copy_trading_engine.processed_orders[account_id])
            copy_trading_engine.processed_orders[account_id].clear()
            logger.info(f"🧹 Cleared {count} processed orders for account {account_id}")
            return {"message": f"Cleared {count} processed orders for account {account_id}"}
        else:
            return {"message": f"No processed orders found for account {account_id}"}
            
    except Exception as e:
        logger.error(f"Error clearing processed orders: {e}")
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

# Position sizing and risk management
@app.get("/position-sizing/simulate/{master_account_id}/{follower_account_id}")
async def simulate_position_sizing(
    master_account_id: int, 
    follower_account_id: int,
    symbol: str,
    master_quantity: float,
    master_price: float,
    db = Depends(get_db)
):
    """Simulate position sizing calculation for a trade"""
    try:
        from copy_trading_engine import copy_trading_engine
        from models import Account, CopyTradingConfig, Trade
        
        # Get accounts
        master_account = db.query(Account).filter(Account.id == master_account_id).first()
        follower_account = db.query(Account).filter(Account.id == follower_account_id).first()
        
        if not master_account or not follower_account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Get copy trading config
        config = db.query(CopyTradingConfig).filter(
            CopyTradingConfig.master_account_id == master_account_id,
            CopyTradingConfig.follower_account_id == follower_account_id,
            CopyTradingConfig.is_active == True
        ).first()
        
        if not config:
            raise HTTPException(status_code=404, detail="Copy trading configuration not found")
        
        # Get follower client
        follower_client = copy_trading_engine.follower_clients.get(follower_account_id)
        if not follower_client:
            raise HTTPException(status_code=400, detail="Follower client not available")
        
        # Create a mock trade for simulation
        mock_trade = Trade(
            account_id=master_account_id,
            symbol=symbol,
            side='BUY',
            order_type='MARKET',
            quantity=master_quantity,
            price=master_price,
            status='FILLED'
        )
        
        # Calculate follower quantity using the new system
        follower_quantity = await copy_trading_engine.calculate_follower_quantity(
            mock_trade, config, follower_client
        )
        
        # Get additional metrics
        follower_balance = await follower_client.get_balance()
        mark_price = await follower_client.get_mark_price(symbol)
        
        # Calculate risk metrics
        follower_notional = follower_quantity * mark_price
        risk_percentage = (follower_notional / follower_balance) * 100 if follower_balance > 0 else 0
        effective_leverage = follower_notional / follower_balance if follower_balance > 0 else 0
        
        return {
            "master_trade": {
                "quantity": master_quantity,
                "price": master_price,
                "notional_value": master_quantity * master_price
            },
            "follower_calculation": {
                "quantity": follower_quantity,
                "notional_value": follower_notional,
                "risk_percentage": risk_percentage,
                "effective_leverage": effective_leverage
            },
            "account_info": {
                "follower_balance": follower_balance,
                "follower_risk_setting": follower_account.risk_percentage,
                "follower_leverage_setting": follower_account.leverage,
                "copy_percentage": config.copy_percentage,
                "risk_multiplier": config.risk_multiplier,
                "max_risk_percentage": config.max_risk_percentage
            },
            "symbol_info": {
                "symbol": symbol,
                "mark_price": mark_price
            },
            "safety_analysis": {
                "is_safe": risk_percentage <= 10.0,  # Safe if <= 10% risk
                "risk_level": "LOW" if risk_percentage <= 5.0 else "MEDIUM" if risk_percentage <= 10.0 else "HIGH",
                "leverage_warning": effective_leverage > follower_account.leverage * 0.8
            }
        }
        
    except Exception as e:
        logger.error(f"Error simulating position sizing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/accounts/{account_id}/risk-analysis")
async def get_account_risk_analysis(account_id: int, db = Depends(get_db)):
    """Get detailed risk analysis for an account"""
    try:
        from copy_trading_engine import copy_trading_engine
        
        # Get account
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Get client
        if account.is_master:
            client = copy_trading_engine.master_clients.get(account_id)
        else:
            client = copy_trading_engine.follower_clients.get(account_id)
        
        if not client:
            raise HTTPException(status_code=400, detail="Account client not available")
        
        # Get balance and positions
        balance = await client.get_balance()
        positions = await client.get_positions()
        
        # Calculate portfolio metrics
        total_position_value = 0
        position_details = []
        
        for position in positions:
            if position.get('size', 0) != 0:
                size = float(position.get('size', 0))
                mark_price = float(position.get('markPrice', 0))
                position_value = abs(size) * mark_price
                total_position_value += position_value
                
                position_details.append({
                    "symbol": position.get('symbol'),
                    "side": position.get('side'),
                    "size": size,
                    "mark_price": mark_price,
                    "position_value": position_value,
                    "unrealized_pnl": float(position.get('unrealizedProfit', 0))
                })
        
        portfolio_risk = (total_position_value / balance) * 100 if balance > 0 else 0
        
        return {
            "account_info": {
                "id": account_id,
                "name": account.name,
                "is_master": account.is_master,
                "balance": balance,
                "risk_percentage_setting": account.risk_percentage,
                "leverage_setting": account.leverage
            },
            "portfolio_metrics": {
                "total_position_value": total_position_value,
                "portfolio_risk_percentage": portfolio_risk,
                "number_of_positions": len(position_details),
                "available_margin": balance - (total_position_value / account.leverage) if account.leverage > 0 else balance
            },
            "positions": position_details,
            "risk_analysis": {
                "risk_level": "LOW" if portfolio_risk <= 30 else "MEDIUM" if portfolio_risk <= 60 else "HIGH",
                "is_overexposed": portfolio_risk > 80,
                "margin_ratio": (total_position_value / account.leverage) / balance if balance > 0 and account.leverage > 0 else 0,
                "recommendations": [
                    "Consider reducing position sizes" if portfolio_risk > 60 else "Portfolio risk is acceptable",
                    "Monitor margin closely" if portfolio_risk > 80 else "Margin usage is safe"
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting risk analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Account balance and positions
@app.get("/accounts/{account_id}/balance")
async def get_account_balance(account_id: int, db = Depends(get_db)):
    """Get account balance"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        client = BinanceClient(
            api_key=account.api_key,
            secret_key=account.secret_key,
            testnet=Config.BINANCE_TESTNET
        )
        
        balance = await client.get_balance()
        return {"balance": balance}
        
    except Exception as e:
        logger.error(f"Error getting account balance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/accounts/{account_id}/positions")
async def get_account_positions(account_id: int, db = Depends(get_db)):
    """Get account positions"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        client = BinanceClient(
            api_key=account.api_key,
            secret_key=account.secret_key,
            testnet=Config.BINANCE_TESTNET
        )
        
        positions = await client.get_positions()
        return {"positions": positions}
        
    except Exception as e:
        logger.error(f"Error getting account positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/accounts/{account_id}/open-orders")
async def get_account_open_orders(account_id: int, symbol: Optional[str] = None, db = Depends(get_db)):
    """Get account open orders"""
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        client = BinanceClient(
            api_key=account.api_key,
            secret_key=account.secret_key,
            testnet=Config.BINANCE_TESTNET
        )
        
        open_orders = await client.get_open_orders(symbol)
        return {
            "account_id": account_id,
            "symbol": symbol,
            "open_orders": open_orders,
            "count": len(open_orders)
        }
        
    except Exception as e:
        logger.error(f"Error getting account open orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
