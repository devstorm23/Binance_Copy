import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import Session
import ssl

# Fix OpenSSL issue
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except:
    pass

from models import Account, Trade, Position, CopyTradingConfig, SystemLog, get_session
from binance_client import BinanceClient
from config import Config

logger = logging.getLogger(__name__)

class CopyTradingEngine:
    def __init__(self):
        self.master_clients = {}  # account_id -> BinanceClient
        self.follower_clients = {}  # account_id -> BinanceClient
        self.is_running = False
        self.monitoring_tasks = {}
        self.last_trade_check = {}
        self.processed_orders = {}  # account_id -> set of order_ids to avoid duplicates
        self.last_processed_order_time = {}  # account_id -> datetime to avoid processing old orders on restart
        self.startup_complete = {}  # account_id -> bool to track if startup processing is complete
        self.server_start_time = datetime.utcnow()  # Track when the server started
        logger.info(f"🏗️ CopyTradingEngine initialized at {self.server_start_time}")
        logger.info(f"🕐 Server startup time (timestamp): {self.server_start_time.timestamp()}")
        
    async def initialize(self):
        """Initialize the copy trading engine"""
        try:
            logger.info("Initializing copy trading engine...")
            
            # Load all accounts and configurations
            await self.load_accounts()
            await self.setup_copy_trading_configs()
            
            # Initialize order tracking to prevent duplicate trades on restart
            await self.initialize_order_tracking()
            
            logger.info("Copy trading engine initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize copy trading engine: {e}")
            return False
    
    async def check_position_cleanup_after_cancellation(self, master_trade: Trade, session: Session):
        """
        Check if follower positions should be closed when master cancels orders.
        This is useful when master cancels orders but followers have existing positions.
        """
        try:
            logger.info(f"🔍 Checking if follower positions need cleanup after master order cancellation...")
            
            # Get copy trading configurations for this master
            configs = session.query(CopyTradingConfig).filter(
                CopyTradingConfig.master_account_id == master_trade.account_id,
                CopyTradingConfig.is_active == True
            ).all()
            
            if not configs:
                logger.info(f"ℹ️ No active copy configurations found for position cleanup")
                return
            
            # Check if master currently has any positions in this symbol
            master_client = self.master_clients.get(master_trade.account_id)
            master_has_position = False
            
            if master_client:
                try:
                    master_positions = await master_client.get_positions()
                    for pos in master_positions:
                        if pos['symbol'] == master_trade.symbol and abs(float(pos['size'])) > 0.001:
                            master_has_position = True
                            logger.info(f"📊 Master still has {pos['side']} position: {pos['size']} {master_trade.symbol}")
                            break
                    
                    if not master_has_position:
                        logger.info(f"📊 Master has NO position in {master_trade.symbol}")
                except Exception as e:
                    logger.warning(f"⚠️ Could not check master positions: {e}")
            
            # If master has no position, consider closing follower positions
            # This ensures followers don't hold positions when master has exited
            if not master_has_position:
                logger.info(f"🔄 Master has no {master_trade.symbol} position - checking follower positions for cleanup")
                
                for config in configs:
                    try:
                        follower_client = self.follower_clients.get(config.follower_account_id)
                        if not follower_client:
                            continue
                        
                        # Get follower positions
                        follower_positions = await follower_client.get_positions()
                        
                        for pos in follower_positions:
                            if pos['symbol'] == master_trade.symbol and abs(float(pos['size'])) > 0.001:
                                logger.info(f"🔄 CLEANUP: Closing follower position {pos['side']} {pos['size']} {pos['symbol']} (master has no position)")
                                
                                # Close the follower position
                                try:
                                    close_order = await follower_client.close_position(
                                        pos['symbol'],
                                        pos['side'],
                                        pos['size']
                                    )
                                    logger.info(f"✅ Closed follower position: {pos['symbol']} {pos['side']} {pos['size']}")
                                    self.add_system_log("INFO", f"🔄 Closed position after master cancellation: {pos['symbol']} {pos['side']}", config.follower_account_id)
                                    
                                except Exception as close_error:
                                    logger.error(f"❌ Failed to close follower position: {close_error}")
                                    self.add_system_log("ERROR", f"❌ Failed to close position after cancellation: {close_error}", config.follower_account_id)
                        
                    except Exception as follower_error:
                        logger.error(f"❌ Error checking follower {config.follower_account_id} for cleanup: {follower_error}")
            else:
                logger.info(f"ℹ️ Master still has {master_trade.symbol} position - no follower cleanup needed")
                
        except Exception as e:
            logger.error(f"❌ Error in position cleanup check: {e}")
    
    def add_system_log(self, level: str, message: str, account_id: int = None, trade_id: int = None):
        """Add a system log entry to database with fallback to file logging"""
        try:
            session = get_session()
            
            # Cleanup old logs periodically to prevent massive log accumulation
            # Keep only last 1000 logs per level to prevent database bloat
            try:
                log_count = session.query(SystemLog).filter(SystemLog.level == level.upper()).count()
                if log_count > 1000:
                    # Remove oldest logs of this level, keeping only the most recent 500
                    oldest_logs = session.query(SystemLog).filter(
                        SystemLog.level == level.upper()
                    ).order_by(SystemLog.created_at.asc()).limit(log_count - 500).all()
                    
                    for old_log in oldest_logs:
                        session.delete(old_log)
                    
                    logger.info(f"🧹 Cleaned up {len(oldest_logs)} old {level} logs")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ Log cleanup failed: {cleanup_error}")
            
            log = SystemLog(
                level=level.upper(),
                message=message,
                account_id=account_id,
                trade_id=trade_id
            )
            session.add(log)
            session.commit()
            session.close()
            
            # Also log to file logger for immediate visibility
            log_func = getattr(logger, level.lower(), logger.info)
            log_func(f"[DB_LOG] {message}")
            
        except Exception as e:
            logger.error(f"Failed to add system log to database: {e}")
            # Log to file as fallback
            log_func = getattr(logger, level.lower(), logger.info)
            log_func(f"[FALLBACK] {message}")
    
    def cleanup_old_logs(self, max_logs_per_level: int = 500):
        """Clean up old system logs to prevent database bloat"""
        try:
            session = get_session()
            
            # Get all log levels
            levels = session.query(SystemLog.level).distinct().all()
            total_cleaned = 0
            
            for (level,) in levels:
                log_count = session.query(SystemLog).filter(SystemLog.level == level).count()
                
                if log_count > max_logs_per_level:
                    # Remove oldest logs, keeping only the most recent ones
                    logs_to_remove = log_count - max_logs_per_level
                    oldest_logs = session.query(SystemLog).filter(
                        SystemLog.level == level
                    ).order_by(SystemLog.created_at.asc()).limit(logs_to_remove).all()
                    
                    for old_log in oldest_logs:
                        session.delete(old_log)
                    
                    total_cleaned += len(oldest_logs)
                    logger.info(f"🧹 Cleaned up {len(oldest_logs)} old {level} logs")
            
            session.commit()
            session.close()
            
            if total_cleaned > 0:
                logger.info(f"✅ Total log cleanup: {total_cleaned} old logs removed")
                self.add_system_log("INFO", f"🧹 Log cleanup completed: {total_cleaned} old logs removed")
            
            return total_cleaned
            
        except Exception as e:
            logger.error(f"❌ Error during log cleanup: {e}")
            return 0
    
    async def initialize_order_tracking(self):
        """Initialize order tracking to prevent duplicates on restart"""
        try:
            logger.info("🔄 Initializing order tracking to prevent restart duplicates...")
            current_time = datetime.utcnow()
            
            # Set last processed time to current time for all master accounts
            # This prevents processing old orders when the bot restarts
            for master_id in self.master_clients.keys():
                self.last_processed_order_time[master_id] = current_time
                self.processed_orders[master_id] = set()
                logger.info(f"🕒 Set last processed time for master {master_id} to {current_time}")
                
                # Also log recent database trades to avoid reprocessing
                try:
                    session = get_session()
                    recent_trades = session.query(Trade).filter(
                        Trade.account_id == master_id,
                        Trade.created_at >= current_time - timedelta(hours=24)  # Last 24 hours
                    ).all()
                    
                    for trade in recent_trades:
                        if trade.binance_order_id:
                            self.processed_orders[master_id].add(str(trade.binance_order_id))
                    
                    logger.info(f"📋 Loaded {len(recent_trades)} recent orders for master {master_id} to prevent duplicates")
                    session.close()
                    
                except Exception as db_error:
                    logger.warning(f"⚠️ Could not load recent orders for master {master_id}: {db_error}")
                    
            self.add_system_log("INFO", "🔄 Order tracking initialized - old orders will not be reprocessed on restart")
            
        except Exception as e:
            logger.error(f"Failed to initialize order tracking: {e}")
            self.add_system_log("ERROR", f"Failed to initialize order tracking: {e}")
    
    async def load_accounts(self):
        """Load all accounts from database"""
        try:
            session = get_session()
            accounts = session.query(Account).filter(Account.is_active == True).all()
            
            logger.info(f"Loading {len(accounts)} active accounts...")
            
            for account in accounts:
                logger.info(f"Processing account {account.id}: {account.name} (is_master: {account.is_master})")
                
                client = BinanceClient(
                    api_key=account.api_key,
                    secret_key=account.secret_key,
                    testnet=Config.BINANCE_TESTNET
                )
                
                # Test connection with different requirements for master vs follower
                connection_valid = await client.test_connection()
                
                if connection_valid:
                    if account.is_master:
                        self.master_clients[account.id] = client
                        logger.info(f"✅ Master account loaded: {account.name} (ID: {account.id})")
                    else:
                        self.follower_clients[account.id] = client
                        logger.info(f"✅ Follower account loaded: {account.name} (ID: {account.id})")
                elif not account.is_master:
                    # For follower accounts (subaccounts), be more lenient
                    logger.warning(f"⚠️ Follower account {account.name} has limited API permissions")
                    logger.info(f"🔄 Attempting to load anyway for copy trading...")
                    
                    # Load follower anyway if it's a subaccount - we'll handle errors during trading
                    self.follower_clients[account.id] = client
                    logger.info(f"✅ Follower account loaded with limited permissions: {account.name} (ID: {account.id})")
                else:
                    logger.error(f"❌ Failed to connect to account: {account.name} (ID: {account.id})")
            
            logger.info(f"Loaded {len(self.master_clients)} master accounts and {len(self.follower_clients)} follower accounts")
            session.close()
        except Exception as e:
            logger.error(f"Failed to load accounts: {e}")
            raise
    
    async def setup_copy_trading_configs(self):
        """Setup copy trading configurations"""
        try:
            session = get_session()
            configs = session.query(CopyTradingConfig).filter(CopyTradingConfig.is_active == True).all()
            
            logger.info(f"Loading {len(configs)} active copy trading configurations...")
            logger.info(f"Available master accounts: {list(self.master_clients.keys())}")
            logger.info(f"Available follower accounts: {list(self.follower_clients.keys())}")
            
            for config in configs:
                master_available = config.master_account_id in self.master_clients
                follower_available = config.follower_account_id in self.follower_clients
                
                if master_available and follower_available:
                    logger.info(f"Copy trading config loaded: Master {config.master_account_id} -> Follower {config.follower_account_id}")
                else:
                    if not master_available:
                        logger.warning(f"Master account {config.master_account_id} not available (not loaded or not master)")
                    if not follower_available:
                        logger.warning(f"Follower account {config.follower_account_id} not available (not loaded or is master)")
                    logger.warning(f"Invalid copy trading config: Master {config.master_account_id} -> Follower {config.follower_account_id}")
                    
            session.close()
        except Exception as e:
            logger.error(f"Failed to setup copy trading configs: {e}")
            raise
    
    async def start_monitoring(self):
        """Start monitoring all master accounts"""
        if self.is_running:
            logger.warning("Copy trading engine is already running")
            return
        
        # FIXED: Only set server start time if this is the first time monitoring starts
        # This prevents the uptime calculation issue where server_start_time gets reset
        if not hasattr(self, '_monitoring_started_before') or not self._monitoring_started_before:
            self.server_start_time = datetime.utcnow()
            self._monitoring_started_before = True
            logger.info(f"🕐 INITIAL START: Server start time set to {self.server_start_time}")
            logger.info(f"🕐 Server startup time (timestamp): {self.server_start_time.timestamp()}")
            
            # Clear startup completion flags to ensure startup protection is applied
            self.startup_complete.clear()
            logger.info(f"🧹 Cleared startup completion flags")
        else:
            logger.info(f"🔄 RESTART: Keeping original server start time: {self.server_start_time}")
            current_uptime = datetime.utcnow() - self.server_start_time
            logger.info(f"🕐 Current server uptime: {current_uptime}")
        
        self.is_running = True
        logger.info("Starting copy trading monitoring...")
        
        # Start monitoring each master account
        for master_id, client in self.master_clients.items():
            task = asyncio.create_task(self.monitor_master_account(master_id, client))
            self.monitoring_tasks[master_id] = task
            # Set last trade check to server start time to ensure startup protection
            self.last_trade_check[master_id] = self.server_start_time
            logger.info(f"🕐 Set last_trade_check for master {master_id} to {self.server_start_time}")
            # Initialize processed orders tracking for this master
            if master_id not in self.processed_orders:
                self.processed_orders[master_id] = set()
            # Initialize startup tracking
            if master_id not in self.startup_complete:
                self.startup_complete[master_id] = False
        
        logger.info(f"Started monitoring {len(self.master_clients)} master accounts")
    
    async def stop_monitoring(self):
        """Stop monitoring all master accounts"""
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info("Stopping copy trading monitoring...")
        
        # Cancel all monitoring tasks
        for task in self.monitoring_tasks.values():
            task.cancel()
        
        # Wait for tasks to complete
        await asyncio.gather(*self.monitoring_tasks.values(), return_exceptions=True)
        self.monitoring_tasks.clear()
        
        logger.info("Copy trading monitoring stopped")
    
    async def monitor_master_account(self, master_id: int, client: BinanceClient):
        """Monitor a specific master account for new trades"""
        try:
            logger.info(f"🔍 Starting monitoring for master account {master_id}")
            loop_count = 0
            
            while self.is_running:
                try:
                    loop_count += 1
                    if loop_count % 60 == 0:  # Log every 60 loops (about 1 minute)
                        logger.info(f"📊 Monitoring master {master_id} - Loop {loop_count}")
                    
                    # Get recent trades from master account
                    await self.check_master_trades(master_id, client)
                    
                    # Wait before next check
                    await asyncio.sleep(Config.TRADE_SYNC_DELAY)
                    
                except asyncio.CancelledError:
                    logger.info(f"⏹️ Monitoring cancelled for master {master_id}")
                    break
                except Exception as e:
                    logger.error(f"❌ Error monitoring master account {master_id}: {e}")
                    await asyncio.sleep(5)  # Wait before retrying
                    
        except Exception as e:
            logger.error(f"💥 Failed to monitor master account {master_id}: {e}")
        finally:
            logger.info(f"🔚 Stopped monitoring master account {master_id}")
    
    async def check_master_trades(self, master_id: int, client: BinanceClient):
        """Check for new trades in master account using Binance API"""
        try:
            # Get the last trade timestamp for this master
            # STARTUP PROTECTION: On startup, only look back 5 minutes maximum to catch more NEW orders
            if master_id not in self.startup_complete:
                # On startup, only look back 5 minutes or server start time, whichever is later
                five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
                default_check_time = max(five_minutes_ago, self.server_start_time)
                logger.info(f"🚀 STARTUP MODE: Only looking back to {default_check_time} (max 5 minutes)")
            else:
                # Normal operation - never go back further than server startup time
                default_check_time = max(datetime.utcnow() - timedelta(hours=1), self.server_start_time)
            
            last_check = self.last_trade_check.get(master_id, default_check_time)
            logger.info(f"🕐 Default check time: {default_check_time}, Last check: {last_check}")
            
            # STARTUP PROTECTION: On first run, only process orders created after server startup time
            if master_id not in self.startup_complete:
                logger.info(f"🚀 First run for master {master_id} - only processing orders created after server startup")
                # For first run, only look at orders created after the server started
                effective_last_check = max(last_check, self.server_start_time)
                logger.info(f"📅 Server started at {self.server_start_time}, adjusted time window: {last_check} -> {effective_last_check}")
                # Mark startup as complete after first check
                self.startup_complete[master_id] = True
            else:
                effective_last_check = last_check
            
            # Get recent trades from Binance API directly
            logger.debug(f"Checking trades for master {master_id} since {effective_last_check}")
            
            try:
                # Get recent orders from Binance
                recent_orders = await self.get_recent_orders(client, effective_last_check)
                
                if recent_orders:
                    logger.info(f"Found {len(recent_orders)} recent orders for master {master_id}")
                    
                    # Sort orders with priority: NEW orders first, then by time
                    def order_priority(order):
                        status = order.get('status', 'UNKNOWN')
                        time_value = order.get('time', order.get('updateTime', 0))
                        
                        # Priority order: NEW (0), PARTIALLY_FILLED (1), FILLED (2), others (3)
                        if status == 'NEW':
                            priority = 0  # Highest priority for NEW orders
                        elif status == 'PARTIALLY_FILLED':
                            priority = 1
                        elif status == 'FILLED':
                            priority = 2
                        else:
                            priority = 3
                        
                        return (priority, time_value)
                    
                    recent_orders.sort(key=order_priority)
                    logger.info(f"📊 Sorted orders by priority (NEW orders first)")
                    
                    # Count different order types for debugging
                    order_counts = {}
                    for order in recent_orders:
                        status = order.get('status', 'UNKNOWN')
                        order_counts[status] = order_counts.get(status, 0) + 1
                    
                    logger.info(f"📊 Order breakdown: {order_counts}")
                    
                    for order in recent_orders:
                        try:
                            order_status = order.get('status', 'UNKNOWN')
                            order_time = datetime.utcfromtimestamp(order.get('time', order.get('updateTime', 0)) / 1000)
                            
                            if order_status in ['NEW', 'PARTIALLY_FILLED']:
                                logger.info(f"🚀 DETECTED NEW ORDER: {order['orderId']} ({order_status}) from {order_time} for master {master_id}")
                            
                            logger.info(f"📝 About to process order {order['orderId']} (Status: {order_status}) for master {master_id}")
                            await self.process_master_order(master_id, order)
                            logger.info(f"✅ Successfully processed order {order['orderId']} for master {master_id}")
                        except Exception as order_error:
                            logger.error(f"❌ Error processing order {order['orderId']} for master {master_id}: {order_error}")
                            import traceback
                            logger.error(f"Full traceback: {traceback.format_exc()}")
                else:
                    logger.debug(f"No recent orders found for master {master_id}")
                    
            except Exception as e:
                logger.warning(f"Failed to get orders from Binance for master {master_id}: {e}")
                # Fallback to database check
                await self.check_database_trades(master_id, last_check)
            
            # Update last check time
            self.last_trade_check[master_id] = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"Error checking master trades: {e}")
    
    async def get_recent_orders(self, client: BinanceClient, since_time: datetime):
        """Get recent orders from Binance API - includes both open and filled orders"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            # During startup, ensure we don't fetch orders from before the server started
            effective_since_time = max(since_time, self.server_start_time)
            
            # Convert datetime to timestamp
            start_time = int(effective_since_time.timestamp() * 1000)
            
            if effective_since_time != since_time:
                logger.info(f"🔍 Startup protection: Adjusted time from {since_time} to {effective_since_time}")
            logger.info(f"🔍 Fetching orders since {effective_since_time}")
            
            # Get both open orders and recent historical orders
            all_orders = []
            
            # 1. Get current open orders (these should be copied immediately)
            try:
                open_orders = await client.get_open_orders()
                if open_orders:
                    logger.info(f"📋 Retrieved {len(open_orders)} open orders")
                    new_order_count = 0
                    for order in open_orders:
                        # Fix timestamp display in logs
                        order_time = int(order['time'])
                        current_time_ms = int(datetime.utcnow().timestamp() * 1000)
                        if order_time > current_time_ms + 86400000:  # More than 1 day in future
                            timestamp_display = "INVALID_FUTURE_TIME"
                        else:
                            timestamp_display = datetime.utcfromtimestamp(order_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                        
                        # Count NEW orders specifically
                        if order.get('status') == 'NEW':
                            new_order_count += 1
                            logger.info(f"🆕 NEW OPEN ORDER: ID={order['orderId']}, Symbol={order['symbol']}, Side={order['side']}, Status={order['status']}, Time={timestamp_display}")
                        else:
                            logger.info(f"📋 Open order details: ID={order['orderId']}, Symbol={order['symbol']}, Side={order['side']}, Status={order['status']}, Time={timestamp_display}")
                    
                    if new_order_count > 0:
                        logger.info(f"🚨 PRIORITY: Found {new_order_count} NEW orders that need immediate copying!")
                    
                    all_orders.extend(open_orders)
                else:
                    logger.debug("📋 No open orders found")
            except Exception as e:
                logger.warning(f"⚠️ Failed to get open orders: {e}")
            
            # 2. Get recent historical orders - STARTUP PROTECTION to prevent processing old orders
            try:
                # During startup, never look back further than server start time
                # After startup, limit to 10 minutes to avoid processing old cancelled orders
                server_start_time_ms = int(self.server_start_time.timestamp() * 1000)
                ten_minutes_ago = int((datetime.utcnow() - timedelta(minutes=10)).timestamp() * 1000)
                # Use the latest of: requested start time, 10 minutes ago, or server start time
                effective_start_time = max(start_time, ten_minutes_ago, server_start_time_ms)
                
                historical_orders = await loop.run_in_executor(
                    None, 
                    lambda: client.client.futures_get_all_orders(startTime=effective_start_time, limit=50)
                )
                logger.info(f"📊 Retrieved {len(historical_orders)} historical orders from Binance (since {datetime.utcfromtimestamp(effective_start_time / 1000)})")
                
                # Debug log to show startup protection is working
                if effective_start_time > start_time:
                    logger.info(f"🛡️ Startup protection active: Limited from {datetime.utcfromtimestamp(start_time / 1000)} to {datetime.utcfromtimestamp(effective_start_time / 1000)}")
                
                all_orders.extend(historical_orders)
            except Exception as e:
                logger.error(f"❌ Failed to get historical orders: {e}")
            
            # Remove duplicates based on orderId and filter for relevant orders
            seen_orders = set()
            relevant_orders = []
            
            for order in all_orders:
                order_id = order['orderId']
                order_time = int(order['time'])
                order_status = order['status']
                
                # Fix timestamp issue: Binance sometimes returns future timestamps
                # Validate timestamp is reasonable (not in the far future)
                current_time_ms = int(datetime.utcnow().timestamp() * 1000)
                if order_time > current_time_ms + 86400000:  # More than 1 day in future
                    logger.warning(f"⚠️ Order {order_id} has invalid future timestamp: {order_time}, using current time")
                    order_time = current_time_ms
                
                # STRICT FILTERING: Only process orders that are:
                # 1. Open orders (NEW/PARTIALLY_FILLED) - regardless of time
                # 2. Very recent filled orders (within last 5 minutes)
                # 3. Very recent cancelled orders (within last 5 minutes) - but NEVER from before server startup
                is_open_order = order_status in ['NEW', 'PARTIALLY_FILLED']
                five_minutes_ago = int((datetime.utcnow() - timedelta(minutes=5)).timestamp() * 1000)
                server_start_time_ms = int(self.server_start_time.timestamp() * 1000)
                is_recently_filled = order_status == 'FILLED' and order_time >= five_minutes_ago
                # For cancelled orders, they must be both recent AND after server startup
                is_recently_cancelled = (order_status in ['CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED'] and 
                                       order_time >= five_minutes_ago and 
                                       order_time >= server_start_time_ms)
                
                # Debug logging for cancelled orders that are being filtered out
                if order_status in ['CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED']:
                    order_time_readable = datetime.utcfromtimestamp(order_time / 1000)
                    server_start_readable = datetime.utcfromtimestamp(server_start_time_ms / 1000)
                    if order_time < server_start_time_ms:
                        logger.info(f"🛡️ FILTERED: Cancelled order {order_id} from {order_time_readable} (before server start {server_start_readable})")
                    elif not is_recently_cancelled:
                        logger.debug(f"🛡️ FILTERED: Old cancelled order {order_id} from {order_time_readable}")
                
                if (order_id not in seen_orders and 
                    order['side'] in ['BUY', 'SELL'] and 
                    order_status in ['NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED'] and
                    (is_open_order or is_recently_filled or is_recently_cancelled)):
                    seen_orders.add(order_id)
                    relevant_orders.append(order)
                    if is_open_order:
                        if order_status == 'NEW':
                            status_note = "🆕 NEW ORDER (PRIORITY)"
                        else:
                            status_note = "📋 PARTIALLY FILLED"
                    elif is_recently_cancelled:
                        status_note = "❌ RECENTLY CANCELLED"
                    elif is_recently_filled:
                        status_note = "🏁 RECENTLY FILLED"
                    else:
                        status_note = "🏁 RECENT"
                    # Fix timestamp display for logging
                    timestamp_display = datetime.utcfromtimestamp(order_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(f"🎯 Found order {status_note}: {order['symbol']} {order['side']} {order['origQty']} - Status: {order_status} - Time: {timestamp_display}")
                else:
                    # Log why orders are being filtered out (only for debug level)
                    if order_id in seen_orders:
                        logger.debug(f"⏭️ Skipping duplicate order: {order_id}")
                    elif order['side'] not in ['BUY', 'SELL']:
                        logger.debug(f"⏭️ Skipping non-trading order: {order_id} (side: {order['side']})")
                    elif not (is_open_order or is_recently_filled or is_recently_cancelled):
                        timestamp_display = datetime.utcfromtimestamp(order_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                        five_min_display = datetime.utcfromtimestamp(five_minutes_ago / 1000).strftime('%Y-%m-%d %H:%M:%S')
                        logger.debug(f"⏭️ Skipping old order: {order_id} (time: {timestamp_display}, threshold: {five_min_display})")
            
            logger.info(f"✅ Found {len(relevant_orders)} relevant orders (open + recent)")
            return relevant_orders
            
        except Exception as e:
            logger.error(f"❌ Error getting recent orders: {e}")
            return []
    
    async def check_database_trades(self, master_id: int, last_check: datetime):
        """Fallback method to check database for trades"""
        try:
            session = get_session()
            
            recent_trades = session.query(Trade).filter(
                Trade.account_id == master_id,
                Trade.created_at > last_check,
                Trade.copied_from_master == False
            ).all()
            
            for trade in recent_trades:
                await self.copy_trade_to_followers(trade, session)
            
            session.close()
            
        except Exception as e:
            logger.error(f"Error checking database trades: {e}")
    
    async def process_master_order(self, master_id: int, order: dict):
        """Process an order from master account (open, partially filled, or filled)"""
        session = None
        try:
            order_id = str(order['orderId'])
            order_status = order['status']
            executed_qty = float(order.get('executedQty', 0))
            original_qty = float(order['origQty'])
            order_time = datetime.utcfromtimestamp(order.get('time', order.get('updateTime', 0)) / 1000)
            
            logger.info(f"🎯 Starting to process master order: {order['symbol']} {order['side']} {original_qty} - Status: {order_status} - Time: {order_time}")
            logger.info(f"🔍 Order details: ID={order_id}, ExecutedQty={executed_qty}, Type={order.get('type', 'UNKNOWN')}")
            
            # STARTUP PROTECTION: Skip orders from before server startup time
            logger.info(f"🕐 Comparing order time {order_time} vs server start {self.server_start_time}")
            if order_time < self.server_start_time:
                logger.info(f"🛡️ STARTUP PROTECTION: Skipping order {order_id} from {order_time} (before server start {self.server_start_time})")
                return
            
            # AGGRESSIVE PROTECTION: Only process very recent orders
            five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
            
            # IMPROVED CANCELLATION HANDLING: Process recent cancellations even during startup
            if order_status in ['CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED']:
                # Calculate how long the server has been running
                server_uptime = datetime.utcnow() - self.server_start_time
                logger.info(f"🕐 Server uptime: {server_uptime}")
                
                # Only process very recent cancelled orders (within last 2 minutes)
                two_minutes_ago = datetime.utcnow() - timedelta(minutes=2)
                if order_time < two_minutes_ago:
                    logger.info(f"🛡️ OLD CANCELLED ORDER: Skipping cancelled order {order_id} from {order_time} (older than 2 minutes)")
                    return
                
                # Process recent cancellations to cancel follower orders
                logger.info(f"🔄 PROCESSING RECENT CANCELLATION: {order_id} from {order_time} - will cancel follower orders")
                
                # For cancelled orders, we need to cancel corresponding follower orders
                # Don't return here - let it process the cancellation
            
            # For NEW orders (most important), be more lenient - allow up to 10 minutes
            elif order_status in ['NEW', 'PARTIALLY_FILLED']:
                ten_minutes_ago = datetime.utcnow() - timedelta(minutes=10)
                if order_time < ten_minutes_ago:
                    logger.info(f"🛡️ OLD NEW ORDER FILTER: Skipping old NEW order {order_id} from {order_time} (older than 10 minutes)")
                    return
                else:
                    logger.info(f"🚀 NEW ORDER DETECTED: Processing {order_id} from {order_time} - PRIORITY")
            
            # For FILLED orders, allow up to 5 minutes
            elif order_status == 'FILLED':
                if order_time < five_minutes_ago:
                    logger.info(f"🛡️ OLD FILLED ORDER FILTER: Skipping old FILLED order {order_id} from {order_time} (older than 5 minutes)")
                    return
                else:
                    logger.info(f"✅ FILLED ORDER: Processing {order_id} from {order_time}")
            
            # For all other orders, only process if within 5 minutes
            elif order_time < five_minutes_ago:
                logger.info(f"🛡️ OLD ORDER FILTER: Skipping old order {order_id} from {order_time} (older than 5 minutes)")
                return
            else:
                logger.info(f"✅ Order {order_id} is recent - processing")
            
            logger.info(f"🎯 TIME FILTERING COMPLETE: Order {order_id} ({order_status}) passed time checks - proceeding to duplicate check")
            
            # Check if this order is from before restart (prevent duplicate processing)
            if master_id in self.last_processed_order_time:
                if order_time < self.last_processed_order_time[master_id]:
                    logger.info(f"⏭️ Skipping old order {order_id} from {order_time} (before restart time {self.last_processed_order_time[master_id]})")
                    return
            
            # Check if we've already processed this order
            if master_id not in self.processed_orders:
                self.processed_orders[master_id] = set()
                logger.info(f"🆕 Initialized processed_orders for master {master_id}")
            
            logger.info(f"🔍 DUPLICATE CHECK: Checking if order {order_id} was already processed...")
            logger.info(f"🔍 Current processed_orders for master {master_id}: {len(self.processed_orders[master_id])} orders")
            if order_id in self.processed_orders[master_id]:
                logger.info(f"⏭️ Order {order_id} found in processed_orders cache - checking database...")
                # Check if the order actually exists in the database with proper error handling
                session_check = None
                try:
                    session_check = get_session()
                    existing_trade = session_check.query(Trade).filter(
                        Trade.binance_order_id == order_id,
                        Trade.account_id == master_id
                    ).first()
                    
                    if existing_trade:
                        logger.info(f"✅ Order {order_id} exists in database, skipping duplicate processing")
                        # For cancelled orders, still check if we need to handle follower cancellations
                        if order_status in ['CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED'] and existing_trade.status != 'CANCELLED':
                            logger.info(f"🔄 Order {order_id} status changed to CANCELLED - handling follower cancellations")
                            existing_trade.status = 'CANCELLED'
                            session_check.commit()
                            await self.handle_master_order_cancellation_with_trade(existing_trade, session_check)
                            session_check.close()
                        return
                    else:
                        logger.warning(f"🔄 Order {order_id} NOT in database but was in processed_orders - reprocessing...")
                        # Remove from processed set so we can reprocess
                        self.processed_orders[master_id].discard(order_id)
                        
                except Exception as db_error:
                    logger.error(f"❌ Database check failed for order {order_id}: {db_error}")
                    # Continue processing the order despite database check failure
                    self.processed_orders[master_id].discard(order_id)
                finally:
                    if session_check:
                        try:
                            session_check.close()
                        except Exception as cleanup_error:
                            logger.error(f"❌ Error closing database session: {cleanup_error}")
            else:
                logger.info(f"✅ FRESH ORDER: {order_id} not in processed_orders cache - proceeding with processing")
            
            logger.info(f"🎯 CONTINUING TO PROCESS: Order {order_id} passed all checks - proceeding to database creation")
            logger.info(f"📋 Processing NEW master order: {order['symbol']} {order['side']} {original_qty} - Status: {order_status}")
            
            # Mark this order as processed (with cleanup to prevent memory leaks)
            self.processed_orders[master_id].add(order_id)
            logger.debug(f"✔️ Marked order {order_id} as processed")
            
            # Clean up old processed orders to prevent memory leaks (keep only last 1000)
            if len(self.processed_orders[master_id]) > 1000:
                # Convert to list, sort by order_id (assuming newer orders have higher IDs)
                sorted_orders = sorted(self.processed_orders[master_id])
                # Keep only the most recent 500 orders
                self.processed_orders[master_id] = set(sorted_orders[-500:])
                logger.debug(f"🧹 Cleaned up processed orders for master {master_id}")
            
            # Create trade record in database
            logger.info(f"💾 Creating database session...")
            session = get_session()
            logger.info(f"💾 Database session created successfully")
            
            # Log master trade detection
            self.add_system_log("INFO", f"🔍 Master trade detected: {order.get('symbol')} {order.get('side')} {executed_qty} (Status: {order_status})", master_id)
            
            # Determine the status and quantity to record
            if order_status == 'NEW':
                db_status = 'PENDING'
                quantity_to_record = original_qty
                price_to_record = float(order.get('price', 0))
            elif order_status == 'PARTIALLY_FILLED':
                db_status = 'PARTIALLY_FILLED'
                quantity_to_record = executed_qty
                price_to_record = float(order.get('avgPrice', order.get('price', 0)))
            elif order_status == 'FILLED':
                db_status = 'FILLED'
                quantity_to_record = executed_qty
                price_to_record = float(order.get('avgPrice', order.get('price', 0)))
            elif order_status in ['CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED']:
                # Handle cancelled/expired orders - MUST cancel follower orders
                logger.info(f"🚫 PROCESSING MASTER ORDER CANCELLATION: {order_id}")
                logger.info(f"📊 Order details: Symbol={order.get('symbol')}, Side={order.get('side')}, Qty={order.get('origQty')}, Type={order.get('type')}")
                logger.info(f"🕐 Order time: {order_time}, Current time: {datetime.utcnow()}")
                
                # First, try to find existing master trade record for this order
                existing_master_trade = session.query(Trade).filter(
                    Trade.account_id == master_id,
                    Trade.binance_order_id == str(order_id)
                ).first()
                
                if existing_master_trade:
                    logger.info(f"✅ Found existing master trade {existing_master_trade.id} for cancelled order - Current status: {existing_master_trade.status}")
                    # Update the existing trade status
                    if existing_master_trade.status != 'CANCELLED':
                        existing_master_trade.status = 'CANCELLED'
                        session.commit()
                        logger.info(f"📝 Updated master trade {existing_master_trade.id} status to CANCELLED")
                    
                    # CRITICAL: Handle follower cancellations using the existing trade
                    logger.info(f"🔄 Initiating follower order cancellations...")
                    await self.handle_master_order_cancellation_with_trade(existing_master_trade, session)
                    logger.info(f"✅ Completed follower order cancellations for trade {existing_master_trade.id}")
                else:
                    logger.info(f"⚠️ No existing master trade found for cancelled order {order_id}")
                    logger.info(f"🤔 This could happen if:")
                    logger.info(f"   1. Master order was cancelled before followers were created")
                    logger.info(f"   2. Master order was cancelled very quickly after placement") 
                    logger.info(f"   3. System was restarted and trade records were lost")
                    
                    # Search for follower trades by order symbol, side, and time range
                    # This catches cases where the master order was cancelled before the trade record was created
                    logger.info(f"🔍 Searching for follower trades by order details: {order.get('symbol')} {order.get('side')} {order.get('origQty')}")
                    await self.handle_cancellation_by_order_details(master_id, order, session)
                    
                    # Also search for recent follower orders that might be related
                    await self.cancel_recent_follower_orders_by_pattern(master_id, order, session)
                    
                    # Log the cancellation
                    self.add_system_log("INFO", f"🚫 Master order cancelled: {order.get('symbol')} {order.get('side')} {order_id}", master_id)
                
                logger.info(f"🔚 COMPLETED processing cancelled order {order_id}")
                session.close()
                return
            else:
                logger.warning(f"⚠️ Unsupported order status: {order_status}")
                session.close()
                return
            
            db_trade = Trade(
                account_id=master_id,
                symbol=order['symbol'],
                side=order['side'],
                order_type=order['type'],
                quantity=quantity_to_record,
                price=price_to_record,
                status=db_status,
                binance_order_id=str(order['orderId']),
                copied_from_master=False
            )
            
            logger.info(f"💾 Adding trade to database...")
            session.add(db_trade)
            logger.info(f"💾 Committing trade to database...")
            session.commit()
            logger.info(f"💾 Refreshing trade from database...")
            session.refresh(db_trade)
            logger.info(f"✅ Trade {db_trade.id} saved to database successfully")
            
            # Copy to followers for NEW orders and FILLED orders  
            # Also handle case where we missed the NEW state and only see FILLED
            if order_status in ['NEW', 'FILLED']:
                logger.info(f"🚀 Copying {order_status.lower()} order to followers immediately")
                
                # For FILLED orders, check if we already copied this as NEW to avoid duplicates
                if order_status == 'FILLED':
                    existing_copy = session.query(Trade).filter(
                        Trade.master_trade_id == db_trade.id,
                        Trade.copied_from_master == True
                    ).first()
                    
                    if existing_copy:
                        logger.info(f"📝 FILLED order already copied when it was NEW, skipping duplicate")
                        session.close()
                        return
                    else:
                        logger.info(f"🎯 FILLED order was not copied as NEW - copying now (this handles fast-filling orders)")
                
                # ENHANCED: Check if this is a position closing order with multiple detection methods
                is_reduce_only = order.get('reduceOnly', False)
                is_position_closing = await self.is_position_closing_order(master_id, db_trade, session)
                
                if is_reduce_only:
                    logger.info(f"🔄 REDUCE_ONLY flag detected - closing follower positions")
                    await self.close_follower_positions(db_trade, session)
                elif is_position_closing:
                    logger.info(f"🔄 Position closing detected via analysis - closing follower positions")
                    await self.close_follower_positions(db_trade, session)
                else:
                    logger.info(f"📈 Regular trade order - copying to followers")
                    await self.copy_trade_to_followers(db_trade, session)
                    
            elif order_status == 'PARTIALLY_FILLED':
                # For partially filled orders, check if we already copied this order
                # to avoid duplicate trades
                logger.info(f"📝 Partially filled order recorded, checking if already copied")
                existing_copy = session.query(Trade).filter(
                    Trade.master_trade_id == db_trade.id,
                    Trade.copied_from_master == True
                ).first()
                
                if not existing_copy:
                    logger.info(f"🚀 Copying partially filled order to followers")
                    
                    # ENHANCED: Check if this is a position closing order with multiple detection methods
                    is_reduce_only = order.get('reduceOnly', False)
                    is_position_closing = await self.is_position_closing_order(master_id, db_trade, session)
                    
                    if is_reduce_only:
                        logger.info(f"🔄 REDUCE_ONLY flag detected - closing follower positions")
                        await self.close_follower_positions(db_trade, session)
                    elif is_position_closing:
                        logger.info(f"🔄 Position closing detected via analysis - closing follower positions")
                        await self.close_follower_positions(db_trade, session)
                    else:
                        logger.info(f"📈 Regular trade order - copying to followers")
                        await self.copy_trade_to_followers(db_trade, session)
                else:
                    logger.info(f"📝 Order already copied, skipping duplicate")
            else:
                logger.info(f"📝 Order recorded but not copied (status: {order_status})")
            
            logger.info(f"🔒 Closing database session...")
            session.close()
            logger.info(f"✅ Master order {order_id} processed completely")
            
        except Exception as e:
            logger.error(f"❌ Error processing master order: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            if session:
                try:
                    session.rollback()
                    session.close()
                    logger.info(f"🔒 Database session closed after error")
                except Exception as cleanup_error:
                    logger.error(f"❌ Error cleaning up database session: {cleanup_error}")
    
    async def copy_trade_to_followers(self, master_trade: Trade, session: Session):
        """Copy a master trade to all follower accounts"""
        try:
            logger.info(f"Copying trade {master_trade.id} to followers")
            
            # Get copy trading configurations for this master
            configs = session.query(CopyTradingConfig).filter(
                CopyTradingConfig.master_account_id == master_trade.account_id,
                CopyTradingConfig.is_active == True
            ).all()
            
            logger.info(f"📋 Found {len(configs)} active copy trading configurations for master {master_trade.account_id}")
            if len(configs) == 0:
                logger.error(f"❌ NO COPY TRADING CONFIGURATIONS FOUND for master {master_trade.account_id}")
                logger.error(f"🔧 THIS IS WHY FOLLOWER ORDERS ARE NOT BEING PLACED!")
                logger.error(f"💡 To fix this issue:")
                logger.error(f"   1. Check the database tables 'accounts' and 'copy_trading_configs'")
                logger.error(f"   2. Ensure master account {master_trade.account_id} has active copy configurations")
                logger.error(f"   3. Run: SELECT * FROM copy_trading_configs WHERE master_account_id = {master_trade.account_id};")
                
                # Also log available accounts and configurations for debugging
                try:
                    all_accounts = session.query(Account).all()
                    logger.info(f"🔍 Total accounts in database: {len(all_accounts)}")
                    for account in all_accounts:
                        account_type = "MASTER" if account.is_master else "FOLLOWER"
                        status = "ACTIVE" if account.is_active else "INACTIVE"
                        logger.info(f"   - Account {account.id}: {account.name} ({account_type}, {status})")
                    
                    all_configs = session.query(CopyTradingConfig).all()
                    logger.info(f"🔍 Total copy trading configurations in database: {len(all_configs)}")
                    if all_configs:
                        for config in all_configs:
                            status = "ACTIVE" if config.is_active else "INACTIVE"
                            logger.info(f"   - Config {config.id}: Master {config.master_account_id} -> Follower {config.follower_account_id} ({status})")
                    else:
                        logger.error(f"❌ NO COPY TRADING CONFIGURATIONS EXIST AT ALL!")
                        logger.error(f"   You need to create copy trading configurations in the database")
                        
                except Exception as debug_error:
                    logger.error(f"❌ Error fetching debug information: {debug_error}")
                
                return
            
            for config in configs:
                logger.info(f"🔗 Processing copy config: Master {config.master_account_id} -> Follower {config.follower_account_id} (Copy: {config.copy_percentage}%)")
                
                follower_client = self.follower_clients.get(config.follower_account_id)
                if not follower_client:
                    logger.error(f"❌ FOLLOWER CLIENT NOT FOUND for account {config.follower_account_id}")
                    logger.error(f"🔧 Available follower clients: {list(self.follower_clients.keys())}")
                    logger.error(f"💡 This means:")
                    logger.error(f"   - The follower account {config.follower_account_id} is not loaded")
                    logger.error(f"   - Check if the account is active and has valid API credentials")
                    logger.error(f"   - Restart the bot to reload accounts")
                    continue
                
                logger.info(f"✅ Found follower client for account {config.follower_account_id}")
                
                # Calculate position size for follower
                follower_quantity = await self.calculate_follower_quantity(
                    master_trade, config, follower_client
                )
                
                if follower_quantity <= 0:
                    logger.warning(f"Invalid quantity calculated for follower {config.follower_account_id}")
                    continue
                
                # Place the trade on follower account
                try:
                    logger.info(f"🚀 About to place follower trade: {master_trade.symbol} {master_trade.side} {follower_quantity}")
                    # Add detailed log before attempting trade
                    self.add_system_log("INFO", f"Attempting to copy trade: {master_trade.symbol} {master_trade.side} Qty: {follower_quantity} to follower {config.follower_account_id}", config.follower_account_id)
                    
                    success = await self.place_follower_trade(master_trade, config, follower_quantity, session)
                    if success:
                        logger.info(f"✅ Successfully placed follower trade for account {config.follower_account_id}")
                        self.add_system_log("INFO", f"✅ Successfully placed follower trade: {master_trade.symbol} {master_trade.side} Qty: {follower_quantity}", config.follower_account_id)
                    else:
                        logger.warning(f"⚠️ Follower trade was skipped for account {config.follower_account_id} (likely due to validation issue)")
                        self.add_system_log("WARNING", f"⚠️ Follower trade skipped: {master_trade.symbol} (validation issue)", config.follower_account_id)
                except Exception as follower_error:
                    error_msg = f"❌ FAILED TO PLACE FOLLOWER TRADE for account {config.follower_account_id}: {follower_error}"
                    logger.error(error_msg)
                    self.add_system_log("ERROR", error_msg, config.follower_account_id)
                    import traceback
                    logger.error(f"Full error traceback: {traceback.format_exc()}")
                    # Continue with other followers instead of stopping completely
                    continue
            
            # Mark master trade as copied
            master_trade.copied_from_master = True
            session.commit()
            
        except Exception as e:
            logger.error(f"Error copying trade to followers: {e}")
            session.rollback()
    
    async def calculate_follower_quantity(self, master_trade: Trade, config: CopyTradingConfig, follower_client: BinanceClient) -> float:
        """Calculate the quantity for follower trade based on balance, risk management, and leverage"""
        try:
            session = get_session()
            follower_account = session.query(Account).filter(Account.id == config.follower_account_id).first()
            master_account = session.query(Account).filter(Account.id == master_trade.account_id).first()
            session.close()
            
            if not follower_account:
                logger.error(f"❌ Follower account {config.follower_account_id} not found in database")
                return 0
            
            if not master_account:
                logger.error(f"❌ Master account {master_trade.account_id} not found in database")
                return 0
            
            # Get current account balances
            follower_balance = await follower_client.get_balance()
            if follower_balance <= 0:
                logger.warning(f"⚠️ Could not get follower balance or balance is zero: {follower_balance}")
                logger.warning(f"⚠️ Falling back to stored balance calculation for proportional copying")
                return await self.calculate_fallback_quantity(master_trade, config)
            
            # Get master balance
            master_balance = 0
            master_client = self.master_clients.get(master_trade.account_id)
            if master_client:
                try:
                    master_balance = await master_client.get_balance()
                    logger.info(f"📊 Got live master balance: ${master_balance:.2f}")
                    
                    # Update stored balance if it's significantly different
                    if abs(master_balance - master_account.balance) > (master_account.balance * 0.05):  # 5% difference
                        old_balance = master_account.balance
                        master_account.balance = master_balance
                        session = get_session()
                        session.merge(master_account)
                        session.commit()
                        session.close()
                        logger.info(f"📊 Updated master account balance: ${old_balance:.2f} → ${master_balance:.2f}")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Could not get live master balance: {e}")
                    master_balance = master_account.balance  # Use stored balance as fallback
                    logger.info(f"📊 Using stored master balance: ${master_balance:.2f}")
            else:
                master_balance = master_account.balance  # Use stored balance
                logger.info(f"📊 Using stored master balance (no client): ${master_balance:.2f}")
            
            # Update follower balance in database if significantly different
            if abs(follower_balance - follower_account.balance) > (follower_account.balance * 0.05):  # 5% difference
                old_balance = follower_account.balance
                follower_account.balance = follower_balance
                session = get_session()
                session.merge(follower_account)
                session.commit()
                session.close()
                logger.info(f"📊 Updated follower account balance: ${old_balance:.2f} → ${follower_balance:.2f}")
            
            # Get mark price for the symbol
            try:
                mark_price = await follower_client.get_mark_price(master_trade.symbol)
                if mark_price <= 0:
                    mark_price = master_trade.price if master_trade.price > 0 else 1.0
            except Exception:
                mark_price = master_trade.price if master_trade.price > 0 else 1.0
            
            logger.info(f"📊 Position sizing calculation starting:")
            logger.info(f"   Master balance: ${master_balance:.2f}")
            logger.info(f"   Follower balance: ${follower_balance:.2f}")
            logger.info(f"   Follower risk%: {follower_account.risk_percentage}%")
            logger.info(f"   Follower leverage: {follower_account.leverage}x")
            logger.info(f"   Symbol price: ${mark_price:.4f}")
            logger.info(f"🔍 DIAGNOSTIC - Input trade: {master_trade.quantity} {master_trade.symbol} @ ${master_trade.price}")
            
            # OPTION 1: Balance Ratio Position Sizing (Primary method - maintains proportional risk)
            if master_balance > 0 and follower_balance > 0:
                quantity = await self.calculate_balance_ratio_quantity(
                    master_trade, master_balance, follower_balance, mark_price, config
                )
                logger.info(f"📊 Using balance-ratio sizing: {quantity}")
            # OPTION 2: Risk-Based Position Sizing (Fallback)
            elif follower_account.risk_percentage > 0:
                quantity = await self.calculate_risk_based_quantity(
                    follower_balance, follower_account, mark_price, master_trade, config
                )
                logger.info(f"📊 Using risk-based sizing: {quantity}")
            else:
                # OPTION 3: Balance-proportional sizing (Final fallback)
                quantity = await self.calculate_balance_proportional_quantity(
                    follower_balance, mark_price, master_trade, config
                )
                logger.info(f"📊 Using balance-proportional sizing: {quantity}")
            
            # Apply copy percentage as final scaling factor
            quantity *= (config.copy_percentage / 100.0)
            logger.info(f"📊 After copy percentage {config.copy_percentage}%: {quantity}")
            
            # Apply risk multiplier
            if config.risk_multiplier != 1.0:
                quantity *= config.risk_multiplier
                logger.info(f"📊 After risk multiplier {config.risk_multiplier}: {quantity}")
            
            # Safety checks and limits  
            # Use master trade price for consistency with order execution
            trade_price = master_trade.price if master_trade.price > 0 else mark_price
            quantity = await self.apply_safety_limits(quantity, follower_balance, trade_price, follower_account, master_trade, config)
            
            # Fix floating point precision
            quantity = round(quantity, 8)
            
            # Final validation
            if quantity <= 0:
                logger.warning(f"⚠️ Calculated quantity is zero or negative: {quantity}")
                return 0
            
            # Calculate notional value for logging
            notional_value = quantity * mark_price
            risk_percentage_actual = (notional_value / follower_balance) * 100
            
            logger.info(f"📊 FINAL CALCULATION RESULT:")
            logger.info(f"   Quantity: {quantity}")
            logger.info(f"   Notional value: ${notional_value:.2f}")
            logger.info(f"   Risk percentage: {risk_percentage_actual:.2f}%")
            logger.info(f"   Master quantity: {master_trade.quantity} (for comparison)")
            
            return quantity
            
        except Exception as e:
            logger.error(f"Error calculating follower quantity: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            logger.warning(f"⚠️ Main calculation failed, falling back to proportional calculation using stored balances")
            return await self.calculate_fallback_quantity(master_trade, config)
    
    async def calculate_risk_based_quantity(self, follower_balance: float, follower_account, mark_price: float, master_trade: Trade, config: CopyTradingConfig) -> float:
        """Calculate position size based on account risk percentage and leverage"""
        try:
            # Calculate the maximum risk amount per trade
            risk_amount = follower_balance * (follower_account.risk_percentage / 100.0)
            
            # With leverage, we can control more value than our risk amount
            # Position Value = Risk Amount × Leverage
            max_position_value = risk_amount * follower_account.leverage
            
            # Calculate quantity based on position value
            quantity = max_position_value / mark_price
            
            logger.info(f"📊 Risk-based calculation:")
            logger.info(f"   Risk amount: ${risk_amount:.2f} ({follower_account.risk_percentage}% of ${follower_balance:.2f})")
            logger.info(f"   Max position value: ${max_position_value:.2f} (risk × {follower_account.leverage}x leverage)")
            logger.info(f"   Calculated quantity: {quantity}")
            
            return quantity
            
        except Exception as e:
            logger.error(f"Error in risk-based calculation: {e}")
            return 0
    
    async def calculate_balance_proportional_quantity(self, follower_balance: float, mark_price: float, master_trade: Trade, config: CopyTradingConfig) -> float:
        """Calculate position size proportional to account balance"""
        try:
            # Use a conservative approach: risk 2% of balance per trade
            conservative_risk_percentage = 2.0
            risk_amount = follower_balance * (conservative_risk_percentage / 100.0)
            
            # Calculate quantity based on risk amount
            quantity = risk_amount / mark_price
            
            logger.info(f"📊 Balance-proportional calculation:")
            logger.info(f"   Conservative risk: ${risk_amount:.2f} ({conservative_risk_percentage}% of ${follower_balance:.2f})")
            logger.info(f"   Calculated quantity: {quantity}")
            
            return quantity
            
        except Exception as e:
            logger.error(f"Error in balance-proportional calculation: {e}")
            return 0
    
    async def calculate_balance_ratio_quantity(self, master_trade: Trade, master_balance: float, follower_balance: float, mark_price: float, config: CopyTradingConfig) -> float:
        """Calculate position size based on balance ratio between master and follower accounts"""
        try:
            # Calculate the ratio of follower balance to master balance
            balance_ratio = follower_balance / master_balance
            
            # Calculate master trade's notional value - use master's actual price for consistency
            # Ensure price is treated as float and handle potential None/string values
            try:
                master_trade_price = float(master_trade.price) if master_trade.price is not None else 0.0
            except (ValueError, TypeError):
                master_trade_price = 0.0
            
            master_notional = master_trade.quantity * mark_price
            
            # Debug logging to verify prices
            logger.info(f"🔍 PRICE DEBUG:")
            logger.info(f"   Master trade price: {master_trade.price}")
            logger.info(f"   Mark price: {mark_price}")  
            logger.info(f"   Master notional: {master_trade.quantity} × {mark_price} = ${master_notional:.2f}")
            
            # Calculate master's risk percentage on this trade
            master_risk_percentage = (master_notional / master_balance) * 100 if master_balance > 0 else 0
            
            # Scale the quantity based on balance ratio, maintaining similar risk percentage
            # This ensures follower takes proportionally similar risk as master
            follower_notional = master_notional * balance_ratio
            # IMPORTANT: Use the same price as master trade to maintain consistent ratios
            quantity = follower_notional / mark_price
            
            logger.info(f"📊 Balance-ratio calculation:")
            logger.info(f"   Master balance: ${master_balance:.2f}")
            logger.info(f"   Follower balance: ${follower_balance:.2f}")
            logger.info(f"   Balance ratio: {balance_ratio:.4f}")
            logger.info(f"   Master notional: ${master_notional:.2f}")
            logger.info(f"   Master risk %: {master_risk_percentage:.2f}%")
            logger.info(f"   Follower notional: ${follower_notional:.2f}")
            logger.info(f"   Calculated quantity: {quantity}")
            
            return quantity
            
        except Exception as e:
            logger.error(f"Error in balance-ratio calculation: {e}")
            return 0
    
    async def apply_safety_limits(self, quantity: float, follower_balance: float, trade_price: float, follower_account, master_trade: Trade, config: CopyTradingConfig) -> float:
        """Apply safety limits to prevent excessive risk"""
        try:
            original_quantity = quantity
            
            # Calculate position value for risk assessment using the actual trade price
            position_value = quantity * trade_price
            risk_percentage = (position_value / follower_balance) * 100 if follower_balance > 0 else 0
            
            logger.info(f"📊 Safety limits check at price ${trade_price:.4f}:")
            
            # 1. Maximum leverage check: prevent over-leveraging (most critical safety check)
            effective_leverage = position_value / follower_balance if follower_balance > 0 else 0
            max_allowed_leverage = follower_account.leverage * 0.9  # Use 90% of max leverage for safety
            
            if effective_leverage > max_allowed_leverage:
                safe_quantity = (follower_balance * max_allowed_leverage) / trade_price
                logger.warning(f"⚠️ Quantity reduced by leverage limit: {quantity:.6f} -> {safe_quantity:.6f}")
                logger.warning(f"   Effective leverage would be {effective_leverage:.1f}x, max allowed: {max_allowed_leverage:.1f}x")
                quantity = safe_quantity
                position_value = quantity * trade_price
                risk_percentage = (position_value / follower_balance) * 100
            
            # 2. Maximum single trade risk: Configurable limit for proportional trading
            # Use the configured max_risk_percentage from the copy trading config
            # This allows for proper proportional scaling while maintaining configurable safety
            max_risk_percentage = getattr(config, 'max_risk_percentage', 50.0)  # Default 50% if not set
            max_risk_value = follower_balance * (max_risk_percentage / 100.0)
            max_quantity_by_risk = max_risk_value / trade_price
            
            if quantity > max_quantity_by_risk:
                logger.warning(f"⚠️ Quantity reduced by risk limit: {quantity:.6f} -> {max_quantity_by_risk:.6f}")
                logger.warning(f"   Risk would be {risk_percentage:.1f}%, max allowed: {max_risk_percentage}%")
                quantity = max_quantity_by_risk
                position_value = quantity * trade_price
                risk_percentage = (position_value / follower_balance) * 100
            
            # 3. Maximum position size: More generous limit (removed the 20% hard cap)
            # The leverage and risk limits above are more appropriate safety measures
            
            # 4. Log final risk assessment
            if quantity != original_quantity:
                logger.info(f"📊 Safety limits applied: {original_quantity:.8f} -> {quantity:.8f}")
                logger.info(f"   Final position value: ${position_value:.2f}")
                logger.info(f"   Final risk percentage: {risk_percentage:.2f}%")
                logger.info(f"   Effective leverage: {effective_leverage:.2f}x")
            else:
                logger.info(f"📊 No safety limits triggered")
                logger.info(f"   Position value: ${position_value:.2f}")
                logger.info(f"   Risk percentage: {risk_percentage:.2f}%")
                logger.info(f"   Effective leverage: {effective_leverage:.2f}x")
            
            return quantity
            
        except Exception as e:
            logger.error(f"Error applying safety limits: {e}")
            return quantity
    
    async def calculate_fallback_quantity(self, master_trade: Trade, config: CopyTradingConfig) -> float:
        """Fallback calculation when balance-based sizing fails - still tries to maintain proportional logic"""
        try:
            session = get_session()
            follower_account = session.query(Account).filter(Account.id == config.follower_account_id).first()
            master_account = session.query(Account).filter(Account.id == master_trade.account_id).first()
            session.close()
            
            # Try to use stored balances for proportional calculation
            if (follower_account and master_account and 
                follower_account.balance > 0 and master_account.balance > 0):
                
                # Calculate balance ratio using stored balances
                balance_ratio = follower_account.balance / master_account.balance
                
                master_notional = master_trade.quantity * mark_price
                
                # Scale proportionally based on balance ratio
                follower_notional = master_notional * balance_ratio
                fallback_quantity = follower_notional / mark_price
                
                # Apply copy percentage and safety reduction
                fallback_quantity *= (config.copy_percentage / 100.0) * 0.8  # 20% safety reduction
                fallback_quantity = round(fallback_quantity, 8)
                
                logger.warning(f"⚠️ Using proportional fallback calculation: {fallback_quantity}")
                logger.warning(f"   Master balance (stored): ${master_account.balance:.2f}")
                logger.warning(f"   Follower balance (stored): ${follower_account.balance:.2f}")
                logger.warning(f"   Balance ratio: {balance_ratio:.4f}")
                logger.warning(f"   Master notional: ${master_notional:.2f}")
                logger.warning(f"   Copy%: {config.copy_percentage}%, Safety reduction: 20%")
                
                return fallback_quantity
            
            # Final fallback: conservative fixed percentage
            fallback_quantity = master_trade.quantity * (config.copy_percentage / 100.0) * 0.5  # 50% reduction for safety
            fallback_quantity = round(fallback_quantity, 8)
            
            logger.warning(f"⚠️ Using conservative fallback quantity calculation: {fallback_quantity}")
            logger.warning(f"   Master quantity: {master_trade.quantity}, Copy%: {config.copy_percentage}%, Safety reduction: 50%")
            logger.warning(f"   Reason: Could not get balance information for proportional calculation")
            
            return fallback_quantity
            
        except Exception as e:
            logger.error(f"Error in fallback calculation: {e}")
            return 0
    
    async def get_portfolio_risk(self, follower_client: BinanceClient, follower_balance: float) -> float:
        """Calculate current portfolio risk percentage"""
        try:
            positions = await follower_client.get_positions()
            total_position_value = 0
            
            for position in positions:
                if position.get('size', 0) != 0:  # Only count open positions
                    position_value = abs(float(position.get('size', 0))) * float(position.get('markPrice', 0))
                    total_position_value += position_value
            
            portfolio_risk_percentage = (total_position_value / follower_balance) * 100 if follower_balance > 0 else 0
            
            logger.info(f"📊 Portfolio risk: ${total_position_value:.2f} ({portfolio_risk_percentage:.1f}% of balance)")
            
            return portfolio_risk_percentage
            
        except Exception as e:
            logger.warning(f"⚠️ Could not calculate portfolio risk: {e}")
            return 0
    
    async def place_follower_trade(self, master_trade: Trade, config: CopyTradingConfig, quantity: float, session: Session):
        """Place the trade on follower account"""
        try:
            logger.info(f"🔄 Starting follower trade placement process...")
            logger.info(f"📋 Master trade details:")
            logger.info(f"   Symbol: {master_trade.symbol}")
            logger.info(f"   Side: {master_trade.side}")
            logger.info(f"   Order Type: {master_trade.order_type}")
            logger.info(f"   Master Quantity: {master_trade.quantity}")
            logger.info(f"   Follower Quantity: {quantity}")
            logger.info(f"   Price: {master_trade.price}")
            logger.info(f"   Stop Price: {master_trade.stop_price}")
            logger.info(f"   Take Profit Price: {master_trade.take_profit_price}")
            logger.info(f"📋 Copy config: {config.follower_account_id} -> {config.copy_percentage}%")
            
            follower_client = self.follower_clients[config.follower_account_id]
            
            # Set leverage and position mode if needed (handle subaccount limitations)
            follower_account = session.query(Account).filter(Account.id == config.follower_account_id).first()
            try:
                await follower_client.set_leverage(master_trade.symbol, follower_account.leverage)
                logger.info(f"✅ Set leverage {follower_account.leverage}x for {master_trade.symbol}")
            except Exception as leverage_error:
                logger.warning(f"⚠️ Could not set leverage for subaccount (normal for limited permissions): {leverage_error}")
                # Continue without setting leverage - subaccounts often can't change leverage
            
            # Ensure position mode is set to One-way (default) to avoid position side conflicts
            try:
                current_mode = await follower_client.get_position_mode()
                if current_mode:  # If in hedge mode, try to switch to one-way mode
                    logger.info(f"📊 Follower account is in hedge mode, attempting to switch to one-way mode")
                    await follower_client.set_position_mode(dual_side_position=False)
                else:
                    logger.info(f"📊 Follower account is already in one-way mode")
            except Exception as mode_error:
                logger.warning(f"⚠️ Could not check/set position mode (may have open positions or limited permissions): {mode_error}")
                # Continue - this is not critical for trading
            
            # Adjust quantity precision for symbol requirements
            try:
                adjusted_quantity = await follower_client.adjust_quantity_precision(master_trade.symbol, quantity)
                if adjusted_quantity != quantity:
                    logger.info(f"📏 Quantity adjusted for precision: {quantity} -> {adjusted_quantity}")
                    quantity = adjusted_quantity
                
                # Final safety check: ensure no floating point precision issues remain
                quantity = round(quantity, 8)  # Round to 8 decimal places as final safety check
                
            except Exception as precision_error:
                logger.warning(f"⚠️ Could not adjust quantity precision: {precision_error}")
                # Fallback: round to 1 decimal place as safety measure
                quantity = round(quantity, 1)
                logger.info(f"📏 Applied safety precision rounding: -> {quantity}")
            
            # Calculate notional value and handle Binance's $5 minimum requirement
            notional_value = quantity * master_trade.price if master_trade.price else 0
            binance_min_notional = 5.0  # Binance enforces this regardless of our settings
            
            # Store original proportional quantity for potential scaling
            original_proportional_quantity = quantity
            
            # DIAGNOSTIC LOGGING for troubleshooting
            master_notional = master_trade.quantity * master_trade.price
            logger.info(f"🔍 DIAGNOSTIC - Before minimum adjustment:")
            logger.info(f"   Master trade: {master_trade.quantity} XRP × ${master_trade.price} = ${master_notional:.2f}")
            logger.info(f"   Follower calculated: {quantity:.6f} XRP × ${master_trade.price} = ${notional_value:.2f}")
            logger.info(f"   Meets minimum ${binance_min_notional}: {notional_value >= binance_min_notional}")
            
            if notional_value < binance_min_notional and master_trade.price > 0:
                logger.warning(f"⚠️ Order value ${notional_value:.2f} is below Binance's ${binance_min_notional} minimum")
                logger.warning(f"📊 Current quantity: {quantity}, Price: {master_trade.price}")
                
                # Calculate master trade's notional value 
                master_notional = master_trade.quantity * master_trade.price
                logger.info(f"📊 Master notional: ${master_notional:.2f}")
                
                # MAINTAIN PROPORTIONAL SCALING even above minimum
                # Calculate the proportion of master trade and apply the same proportion above minimum
                master_min_ratio = master_notional / binance_min_notional
                
                if master_min_ratio > 1.5:  # Master trade is significantly above minimum
                    # Scale follower proportionally above minimum
                    # Formula: follower_quantity = (minimum_quantity) * (master_proportion_above_minimum)
                    base_min_quantity = binance_min_notional / master_trade.price
                    proportional_scaling = master_min_ratio * 0.7  # Use 70% of master's ratio for safety
                    min_quantity_needed = base_min_quantity * proportional_scaling
                    
                    logger.info(f"📊 PROPORTIONAL SCALING:")
                    logger.info(f"   Master ratio above minimum: {master_min_ratio:.2f}x")
                    logger.info(f"   Follower scaling factor: {proportional_scaling:.2f}x")
                    logger.info(f"   Base minimum needed: {base_min_quantity:.6f}")
                    logger.info(f"   Scaled quantity: {min_quantity_needed:.6f}")
                    
                else:
                    # Master trade is small, use basic minimum
                    min_quantity_needed = binance_min_notional / master_trade.price
                    logger.info(f"📊 Master trade small, using basic minimum: {min_quantity_needed:.6f}")
                
                # Try to adjust quantity to meet minimum notional requirement
                try:
                    adjusted_min_quantity = await follower_client.adjust_quantity_precision(master_trade.symbol, min_quantity_needed)
                    new_notional = adjusted_min_quantity * master_trade.price
                    
                    logger.info(f"🔧 Adjusting quantity to meet Binance minimum: {quantity} -> {adjusted_min_quantity}")
                    logger.info(f"💰 New order value: ${new_notional:.2f} (meets ${binance_min_notional} requirement)")
                    
                    quantity = adjusted_min_quantity
                    notional_value = new_notional
                    
                except Exception as adjust_error:
                    logger.error(f"⚠️ Failed to adjust quantity for Binance minimum: {adjust_error}")
                    logger.warning(f"⚠️ Skipping this trade - cannot meet Binance's ${binance_min_notional} minimum")
                    return False
            
            # Validate trade parameters before placing order
            logger.info(f"🎯 Placing follower order: {master_trade.symbol} {master_trade.side} {quantity} ({master_trade.order_type})")
            if notional_value > 0:
                logger.info(f"💰 Order notional value: ${notional_value:.2f}")
            
            # Place the order based on order type
            logger.info(f"🔄 Attempting to place {master_trade.order_type} order...")
            order = None
            
            try:
                if master_trade.order_type == "MARKET":
                    logger.info(f"📊 Placing MARKET order: {master_trade.symbol} {master_trade.side} {quantity}")
                    order = await follower_client.place_market_order(
                        master_trade.symbol,
                        master_trade.side,
                        quantity
                    )
                elif master_trade.order_type == "LIMIT":
                    # Validate price for LIMIT orders
                    if not master_trade.price or master_trade.price <= 0:
                        logger.error(f"❌ Invalid price for LIMIT order: {master_trade.price}")
                        logger.error(f"❌ LIMIT orders require a valid positive price")
                        return False
                    
                    logger.info(f"📊 Placing LIMIT order: {master_trade.symbol} {master_trade.side} {quantity} @ {master_trade.price}")
                    order = await follower_client.place_limit_order(
                        master_trade.symbol,
                        master_trade.side,
                        quantity,
                        master_trade.price
                    )
                elif master_trade.order_type == "STOP_MARKET":
                    logger.info(f"📊 Placing STOP_MARKET order: {master_trade.symbol} {master_trade.side} {quantity} @ {master_trade.stop_price}")
                    order = await follower_client.place_stop_market_order(
                        master_trade.symbol,
                        master_trade.side,
                        quantity,
                        master_trade.stop_price
                    )
                elif master_trade.order_type == "TAKE_PROFIT_MARKET":
                    logger.info(f"📊 Placing TAKE_PROFIT_MARKET order: {master_trade.symbol} {master_trade.side} {quantity} @ {master_trade.take_profit_price}")
                    order = await follower_client.place_take_profit_market_order(
                        master_trade.symbol,
                        master_trade.side,
                        quantity,
                        master_trade.take_profit_price
                    )
                else:
                    logger.warning(f"❌ Unsupported order type: {master_trade.order_type}")
                    return False
                
                if order:
                    logger.info(f"✅ Follower order placed successfully!")
                    logger.info(f"📋 Order details: Order ID {order.get('orderId', 'Unknown')}")
                    logger.info(f"📋 Order status: {order.get('status', 'Unknown')}")
                    logger.info(f"📋 Full order response: {order}")
                else:
                    logger.error(f"❌ Order placement returned None - this should not happen!")
                    return False
                    
            except Exception as order_error:
                logger.error(f"❌ CRITICAL: Order placement failed with exception: {order_error}")
                logger.error(f"❌ Order type: {master_trade.order_type}")
                logger.error(f"❌ Symbol: {master_trade.symbol}")
                logger.error(f"❌ Side: {master_trade.side}")
                logger.error(f"❌ Quantity: {quantity}")
                logger.error(f"❌ Price: {master_trade.price}")
                raise order_error  # Re-raise to be caught by outer exception handler
            
            # Save follower trade to database
            follower_trade = Trade(
                account_id=config.follower_account_id,
                symbol=master_trade.symbol,
                side=master_trade.side,
                order_type=master_trade.order_type,
                quantity=quantity,
                price=master_trade.price,
                stop_price=master_trade.stop_price,
                take_profit_price=master_trade.take_profit_price,
                status="PENDING",
                binance_order_id=order.get('orderId'),
                copied_from_master=True,
                master_trade_id=master_trade.id
            )
            
            session.add(follower_trade)
            session.commit()
            
            # Log the copy trade with more details
            success_message = f"✅ Successfully copied trade: {master_trade.symbol} {master_trade.side} - Master: {master_trade.quantity}, Follower: {follower_trade.quantity} (Copy%: {config.copy_percentage}%)"
            log = SystemLog(
                level="INFO",
                message=success_message,
                account_id=config.follower_account_id,
                trade_id=follower_trade.id
            )
            session.add(log)
            session.commit()
            
            # Also use our centralized logging function
            self.add_system_log("INFO", f"Trade copied: {master_trade.symbol} {master_trade.side} - Master: {master_trade.quantity}, Follower: {follower_trade.quantity}", config.follower_account_id, follower_trade.id)
            
            logger.info(f"Successfully copied trade to follower {config.follower_account_id}")
            return True
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error placing follower trade: {e}")
            
            # Provide specific guidance based on error type
            if "code=-4061" in error_msg:
                logger.error("❌ Position side mismatch error - this should be fixed with the recent updates")
                logger.info("🔧 Try restarting the application to ensure the position mode fixes are active")
            elif "code=-1022" in error_msg:
                logger.error("❌ Signature validation error - check API key permissions for subaccount")
            elif "code=-2015" in error_msg:
                logger.error("❌ Permission denied - subaccount may not have futures trading permissions")
            elif "code=-2019" in error_msg:
                logger.error("❌ Margin insufficient - subaccount may not have enough balance")
            elif "code=-1013" in error_msg:
                logger.error("❌ Invalid quantity - check minimum order size requirements")
            elif "code=-4003" in error_msg:
                logger.error("❌ Quantity precision error - adjusting quantity precision")
            elif "code=-1111" in error_msg:
                logger.error("❌ PRECISION ERROR - This has been fixed!")
                logger.error(f"🔧 The quantity precision fix should prevent this error")
                logger.error(f"💡 If you still see this error, please restart the copy trading service")
                logger.error(f"📊 Problem quantity was: {quantity}")
            elif "code=-4164" in error_msg:
                notional_value = quantity * master_trade.price if master_trade.price else 0
                logger.error("❌ BINANCE MINIMUM NOTIONAL ERROR!")
                logger.error(f"📊 Order value: ${notional_value:.2f} (Binance requires $5.00 minimum)")
                logger.error(f"📊 Quantity: {quantity}, Price: {master_trade.price}")
                logger.error(f"💡 This error should have been handled by pre-validation")
                logger.error(f"🔧 If you see this error, there may be a precision issue")
                logger.warning(f"⚠️ Order failed - Binance rejected due to minimum notional requirement")
                # Don't rollback the session for this error - continue processing
                return False
            else:
                logger.error(f"❌ Unhandled error: {error_msg}")
            
            session.rollback()
            return False
    
    async def get_engine_status(self) -> Dict:
        """Get the current status of the copy trading engine"""
        return {
            'is_running': self.is_running,
            'master_accounts': len(self.master_clients),
            'follower_accounts': len(self.follower_clients),
            'monitoring_tasks': len(self.monitoring_tasks),
            'last_trade_checks': self.last_trade_check
        }
    
    async def add_account(self, account: Account):
        """Add a new account to the engine"""
        try:
            client = BinanceClient(
                api_key=account.api_key,
                secret_key=account.secret_key,
                testnet=Config.BINANCE_TESTNET
            )
            
            if await client.test_connection():
                if account.is_master:
                    self.master_clients[account.id] = client
                    logger.info(f"Added master account: {account.name}")
                    
                    # Start monitoring if engine is running
                    if self.is_running:
                        task = asyncio.create_task(self.monitor_master_account(account.id, client))
                        self.monitoring_tasks[account.id] = task
                        self.last_trade_check[account.id] = datetime.utcnow()
                else:
                    self.follower_clients[account.id] = client
                    logger.info(f"Added follower account: {account.name}")
            else:
                logger.error(f"Failed to connect to new account: {account.name}")
                
        except Exception as e:
            logger.error(f"Error adding account: {e}")
    
    async def is_position_closing_order(self, master_id: int, trade: Trade, session: Session) -> bool:
        """Determine if this trade is closing an existing position - IMPROVED DETECTION"""
        try:
            logger.info(f"🔍 Analyzing if order is position closing: {trade.symbol} {trade.side} {trade.quantity}")
            
            # Get master account client to check positions
            master_client = self.master_clients.get(master_id)
            if not master_client:
                logger.warning(f"⚠️ Master client not found for position check: {master_id}")
                return False
            
            # STEP 0: DIRECT FOLLOWER POSITION CHECK (Most reliable method)
            # Check if there are follower positions that could be closed by this trade
            logger.info(f"🔍 Checking follower positions for potential closing...")
            
            # Get copy trading configurations
            configs = session.query(CopyTradingConfig).filter(
                CopyTradingConfig.master_account_id == master_id,
                CopyTradingConfig.is_active == True
            ).all()
            
            has_follower_positions_to_close = False
            for config in configs:
                follower_client = self.follower_clients.get(config.follower_account_id)
                if follower_client:
                    try:
                        follower_positions = await follower_client.get_positions()
                        for pos in follower_positions:
                            if (pos['symbol'] == trade.symbol and 
                                abs(float(pos['size'])) > 0.001 and
                                ((pos['side'] == 'LONG' and trade.side == 'SELL') or 
                                 (pos['side'] == 'SHORT' and trade.side == 'BUY'))):
                                logger.info(f"🎯 FOLLOWER POSITION FOUND: {pos['symbol']} {pos['side']} {pos['size']} - can be closed by master {trade.side} order")
                                has_follower_positions_to_close = True
                                break
                    except Exception as e:
                        logger.warning(f"⚠️ Could not check follower positions for account {config.follower_account_id}: {e}")
            
            if has_follower_positions_to_close:
                logger.info(f"🔄 FOLLOWER POSITIONS DETECTED: Master {trade.side} order can close existing follower positions")
                return True
            
            # STEP 1: Check current positions from Binance API
            positions = []
            try:
                positions = await master_client.get_positions()
                logger.info(f"📊 Retrieved {len(positions)} current positions from Binance")
            except Exception as pos_error:
                logger.warning(f"⚠️ Failed to get current positions, using database fallback: {pos_error}")
            
            # STEP 2: Check current positions for direct closing detection
            for position in positions:
                if position['symbol'] == trade.symbol:
                    logger.info(f"📊 Found position: {position['symbol']} {position['side']} size={position['size']}")
                    # If we have a LONG position and the trade is SELL, it's closing
                    # If we have a SHORT position and the trade is BUY, it's closing
                    if (position['side'] == 'LONG' and trade.side == 'SELL') or \
                       (position['side'] == 'SHORT' and trade.side == 'BUY'):
                        logger.info(f"🔄 DIRECT POSITION CLOSING: {trade.symbol} {position['side']} position (size: {position['size']}), {trade.side} order (qty: {trade.quantity})")
                        return True
                    else:
                        logger.info(f"📈 Same direction trade: {position['side']} position, {trade.side} order (position building)")
            
            if positions:
                logger.info(f"ℹ️ No {trade.symbol} position found in current positions, checking trade history...")
            
            # STEP 3: ENHANCED trade history analysis for position closing detection
            # This handles cases where master position was already closed by the time we check
            logger.info(f"🔍 Analyzing trade history for position detection...")
            
            # Look for trades in the last 6 hours (more comprehensive than before)
            recent_trades = session.query(Trade).filter(
                Trade.account_id == master_id,
                Trade.symbol == trade.symbol,
                Trade.status.in_(['FILLED', 'PARTIALLY_FILLED']),
                Trade.created_at >= datetime.utcnow() - timedelta(hours=6),  # Extended to 6 hours
                Trade.id != trade.id  # Exclude the current trade we're analyzing
            ).order_by(Trade.created_at.desc()).limit(50).all()  # Increased limit to catch more trades
            
            logger.info(f"📚 Found {len(recent_trades)} recent trades for analysis")
            
            # ENHANCED POSITION CLOSING DETECTION: Look for clear patterns
            opposite_side = 'BUY' if trade.side == 'SELL' else 'SELL'
            
            # Strategy 1: Check if there's a recent position-opening trade in opposite direction
            logger.info(f"🔍 Looking for recent {opposite_side} trades that opened positions...")
            
            # Find the most recent trades in opposite direction (these likely opened positions)
            recent_opposite_trades = [t for t in recent_trades if t.side == opposite_side]
            same_side_trades = [t for t in recent_trades if t.side == trade.side]
            
            if recent_opposite_trades:
                # Get the most recent opposite trade (likely the position opener)
                most_recent_opposite = recent_opposite_trades[0]  # Already sorted by created_at desc
                time_since_opposite = datetime.utcnow() - most_recent_opposite.created_at
                
                logger.info(f"📊 Found recent {opposite_side} trade: {most_recent_opposite.quantity} at {most_recent_opposite.created_at}")
                logger.info(f"🕐 Time since opposite trade: {time_since_opposite}")
                
                # If there was a recent opposite trade and no same-side trades since then, this is likely closing
                same_side_after_opposite = [t for t in same_side_trades if t.created_at > most_recent_opposite.created_at]
                
                if len(same_side_after_opposite) == 0:
                    logger.info(f"🔄 POSITION CLOSING DETECTED: {trade.side} order after recent {opposite_side} trade with no same-side trades in between")
                    return True
                
                # Calculate running position to see if this trade closes it
                net_position = 0
                for t in recent_trades:
                    if t.side == 'BUY':
                        net_position += t.quantity
                    else:  # SELL
                        net_position -= t.quantity
                
                logger.info(f"📊 Position analysis: Net={net_position}, Most recent opposite={most_recent_opposite.quantity}")
                
                # Enhanced closing detection: if current trade would significantly reduce the net position
                if trade.side == 'SELL' and net_position > 0:
                    if trade.quantity >= net_position * 0.5:  # Closing at least 50% of position
                        logger.info(f"🔄 SIGNIFICANT POSITION REDUCTION: SELL {trade.quantity} reduces LONG position {net_position} by {trade.quantity/net_position*100:.1f}%")
                        return True
                elif trade.side == 'BUY' and net_position < 0:
                    abs_net = abs(net_position)
                    if trade.quantity >= abs_net * 0.5:  # Closing at least 50% of position  
                        logger.info(f"🔄 SIGNIFICANT POSITION REDUCTION: BUY {trade.quantity} reduces SHORT position {abs_net} by {trade.quantity/abs_net*100:.1f}%")
                        return True
                
                # ENHANCED HEURISTIC: If net position is opposite to current trade direction, it's likely closing
                if net_position > 0 and trade.side == 'SELL':
                    logger.info(f"🔄 POSITION CLOSING: Net LONG position {net_position}, SELL order {trade.quantity}")
                    return True
                elif net_position < 0 and trade.side == 'BUY':
                    logger.info(f"🔄 POSITION CLOSING: Net SHORT position {abs(net_position)}, BUY order {trade.quantity}")
                    return True
                
                # If we have more quantity in opposite direction, this trade is likely closing
                if total_opposite_qty > total_same_qty:
                    logger.info(f"🔄 Position closing heuristic: recent opposite trades {total_opposite_qty} > same side {total_same_qty}")
                    return True
            
            # STEP 4: Enhanced quantity matching - only for legitimate closing scenarios
            # If we already determined this is same-direction (position building), skip quantity matching entirely
            same_direction_position = any(
                pos['symbol'] == trade.symbol and 
                abs(float(pos['size'])) > 0.001 and
                ((pos['side'] == 'LONG' and trade.side == 'BUY') or 
                 (pos['side'] == 'SHORT' and trade.side == 'SELL'))
                for pos in positions
            )
            
            if same_direction_position:
                logger.info(f"🔍 Skipping quantity matching - same direction trade (position building)")
            else:
                # Only do quantity matching if:
                # 1. No current position exists, OR
                # 2. Current position is opposite direction (legitimate closing)
                logger.info(f"🔍 Final check: quantity matching analysis...")
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                
                recent_opposite_in_hour = session.query(Trade).filter(
                    Trade.account_id == master_id,
                    Trade.symbol == trade.symbol,
                    Trade.side == opposite_side,
                    Trade.status.in_(['FILLED', 'PARTIALLY_FILLED']),
                    Trade.created_at >= one_hour_ago
                ).all()
                
                if recent_opposite_in_hour:
                    total_recent_opposite = sum(t.quantity for t in recent_opposite_in_hour)
                    logger.info(f"📊 Recent {opposite_side} trades in last hour: {total_recent_opposite}")
                    
                    if total_recent_opposite > 0:
                        qty_ratio = abs(trade.quantity - total_recent_opposite) / total_recent_opposite
                        if qty_ratio < 0.15:  # Within 15% tolerance
                            logger.info(f"🔄 QUANTITY MATCH CLOSING: Trade {trade.quantity} ≈ recent opposite {total_recent_opposite} (diff: {qty_ratio:.2%})")
                            return True
            
            # STEP 5: TIME-BASED FALLBACK - detect closing patterns even when logic fails
            # This helps with the "5 minute delay" issue where positions are already closed
            logger.info(f"🔍 Final fallback: Time-based pattern detection...")
            
            if recent_opposite_trades:
                # If there was a recent opposite trade and this trade is smaller, it might be closing
                most_recent_opposite = recent_opposite_trades[0]
                time_diff = datetime.utcnow() - most_recent_opposite.created_at
                
                # If the opposite trade was within last 2 hours and current trade is opposite direction
                if time_diff.total_seconds() < 7200:  # 2 hours
                    logger.info(f"🔄 TIME-BASED CLOSING DETECTED: {trade.side} order {time_diff} after {opposite_side} trade")
                    logger.info(f"   Recent {opposite_side}: {most_recent_opposite.quantity}, Current {trade.side}: {trade.quantity}")
                    
                    # More lenient closing detection for time-based fallback
                    if trade.quantity >= most_recent_opposite.quantity * 0.3:  # At least 30% of the opposite trade
                        logger.info(f"🔄 FALLBACK POSITION CLOSING: {trade.quantity} >= 30% of recent opposite trade {most_recent_opposite.quantity}")
                        return True
            
            logger.info(f"📈 FINAL DETERMINATION: Regular trade order (not position closing)")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error checking if order is position closing: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # SAFE DEFAULT: Treat as regular trade to ensure copying continues
            return False
    
    async def close_follower_positions(self, master_trade: Trade, session: Session):
        """Close corresponding positions in follower accounts - IMPROVED VERSION"""
        try:
            logger.info(f"🔄 STARTING follower position closing for master trade: {master_trade.symbol} {master_trade.side} {master_trade.quantity}")
            
            # Get copy trading configurations for this master
            configs = session.query(CopyTradingConfig).filter(
                CopyTradingConfig.master_account_id == master_trade.account_id,
                CopyTradingConfig.is_active == True
            ).all()
            
            if not configs:
                logger.warning(f"⚠️ No active copy trading configurations found for master {master_trade.account_id}")
                self.add_system_log("WARNING", f"No active followers found for position closing", master_trade.account_id, master_trade.id)
                return
            
            logger.info(f"📋 Found {len(configs)} active follower accounts to process")
            
            closed_count = 0
            for config in configs:
                try:
                    logger.info(f"🔄 Processing follower {config.follower_account_id} (copy %: {config.copy_percentage}%)")
                    
                    follower_client = self.follower_clients.get(config.follower_account_id)
                    if not follower_client:
                        logger.error(f"❌ Follower client not found for account {config.follower_account_id}")
                        self.add_system_log("ERROR", f"Follower client not available for position closing", config.follower_account_id)
                        continue
                    
                    # Get follower positions with error handling
                    follower_positions = []
                    try:
                        follower_positions = await follower_client.get_positions()
                        logger.info(f"📊 Retrieved {len(follower_positions)} positions from follower {config.follower_account_id}")
                    except Exception as pos_error:
                        logger.error(f"❌ Failed to get positions from follower {config.follower_account_id}: {pos_error}")
                        self.add_system_log("ERROR", f"Failed to get positions: {pos_error}", config.follower_account_id)
                        continue
                    
                    position_to_close = None
                    
                    # Find the position that corresponds to what the master is closing
                    for pos in follower_positions:
                        if pos['symbol'] == master_trade.symbol:
                            logger.info(f"📊 Found follower position: {pos['symbol']} {pos['side']} size={pos['size']}")
                            # Master is selling (closing long) -> close follower's long position
                            # Master is buying (closing short) -> close follower's short position
                            if (master_trade.side == 'SELL' and pos['side'] == 'LONG') or \
                               (master_trade.side == 'BUY' and pos['side'] == 'SHORT'):
                                position_to_close = pos
                                logger.info(f"🎯 MATCH: Master {master_trade.side} matches follower {pos['side']} position to close")
                                break
                            else:
                                logger.info(f"ℹ️ No match: Master {master_trade.side} vs follower {pos['side']} position")
                    
                    if position_to_close:
                        # Calculate quantity to close (proportional to copy percentage)
                        raw_close_quantity = position_to_close['size'] * (config.copy_percentage / 100.0)
                        
                        # Ensure minimum quantity and precision
                        close_quantity = max(0.001, round(raw_close_quantity, 8))  # Minimum 0.001 with 8 decimal precision
                        
                        # Don't close more than the position size
                        close_quantity = min(close_quantity, position_to_close['size'])
                        
                        logger.info(f"🔄 CLOSING follower position: Account={config.follower_account_id}, Symbol={master_trade.symbol}, Side={position_to_close['side']}, CloseQty={close_quantity}, PositionSize={position_to_close['size']}")
                        
                        # Close the position with enhanced error handling
                        close_order = None
                        try:
                            close_order = await follower_client.close_position(
                                master_trade.symbol, 
                                position_to_close['side'], 
                                close_quantity
                            )
                            logger.info(f"✅ Position close order executed successfully: {close_order.get('orderId') if close_order else 'No orderId'}")
                        except Exception as close_error:
                            logger.error(f"❌ Failed to close position for follower {config.follower_account_id}: {close_error}")
                            self.add_system_log("ERROR", f"Position close failed: {close_error}", config.follower_account_id)
                            continue
                        
                        if close_order:
                            # Record the position close as a trade
                            close_side = 'SELL' if position_to_close['side'] == 'LONG' else 'BUY'
                            follower_trade = Trade(
                                account_id=config.follower_account_id,
                                symbol=master_trade.symbol,
                                side=close_side,
                                order_type='MARKET',
                                quantity=close_quantity,
                                price=0,  # Market order, price determined by market
                                status='FILLED',
                                binance_order_id=close_order.get('orderId'),
                                copied_from_master=True,
                                master_trade_id=master_trade.id
                            )
                            
                            session.add(follower_trade)
                            session.commit()
                            closed_count += 1
                            
                            logger.info(f"✅ Closed follower position: {config.follower_account_id} {master_trade.symbol}")
                            self.add_system_log("INFO", f"🔄 Position closed: {master_trade.symbol} {position_to_close['side']} {close_quantity} (master position closing)", config.follower_account_id, follower_trade.id)
                        else:
                            logger.warning(f"⚠️ Failed to close position for follower {config.follower_account_id}")
                    else:
                        # No position found to close - this might be normal
                        if follower_positions:
                            logger.info(f"ℹ️ No {master_trade.symbol} position found to close for follower {config.follower_account_id}")
                            # Log what positions they do have for debugging
                            symbol_positions = [f"{pos['symbol']} {pos['side']}" for pos in follower_positions if pos['symbol'] == master_trade.symbol]
                            if symbol_positions:
                                logger.info(f"📊 Follower has different {master_trade.symbol} positions: {symbol_positions}")
                            else:
                                logger.info(f"📊 Follower has no {master_trade.symbol} positions at all")
                        else:
                            logger.info(f"ℹ️ Follower {config.follower_account_id} has no positions")
                        
                        self.add_system_log("INFO", f"No {master_trade.symbol} position to close (master closed {master_trade.side})", config.follower_account_id)
                        
                except Exception as follower_error:
                    logger.error(f"❌ Error processing follower {config.follower_account_id}: {follower_error}")
                    import traceback
                    logger.error(f"Full traceback: {traceback.format_exc()}")
                    self.add_system_log("ERROR", f"Error in position closing: {follower_error}", config.follower_account_id)
            
            if closed_count > 0:
                logger.info(f"✅ Successfully closed positions for {closed_count}/{len(configs)} followers")
                self.add_system_log("INFO", f"🔄 Master position closing - {closed_count} follower positions closed", master_trade.account_id, master_trade.id)
            else:
                logger.warning(f"⚠️ No follower positions were closed for master position closing")
                
            # Mark master trade as copied
            master_trade.copied_from_master = True
            session.commit()
            
        except Exception as e:
            logger.error(f"❌ Error closing follower positions: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            session.rollback()
    
    async def handle_master_order_cancellation_with_trade(self, master_trade: Trade, session: Session):
        """Handle cancellation of master orders using existing trade record"""
        try:
            logger.info(f"🚫 Handling master order cancellation for trade {master_trade.id}")
            
            # Find all follower trades that were copied from this master trade
            follower_trades = session.query(Trade).filter(
                Trade.master_trade_id == master_trade.id,
                Trade.copied_from_master == True,
                Trade.status.in_(['PENDING', 'PARTIALLY_FILLED'])  # Only cancel active orders
            ).all()
            
            if not follower_trades:
                logger.info(f"ℹ️ No active follower trades found for cancelled master trade {master_trade.id}")
                return
            
            logger.info(f"🔍 Found {len(follower_trades)} follower trades to cancel")
            
            # Cancel each follower trade
            cancelled_count = 0
            for follower_trade in follower_trades:
                try:
                    follower_client = self.follower_clients.get(follower_trade.account_id)
                    if not follower_client:
                        logger.error(f"❌ Follower client not found for account {follower_trade.account_id}")
                        continue
                    
                    if follower_trade.binance_order_id:
                        # Determine order type for enhanced logging
                        order_type_desc = "order"
                        if follower_trade.order_type == "STOP_MARKET":
                            order_type_desc = "stop-loss order"
                        elif follower_trade.order_type == "TAKE_PROFIT_MARKET":
                            order_type_desc = "take-profit order"
                        elif follower_trade.order_type == "LIMIT":
                            order_type_desc = "limit order"
                        elif follower_trade.order_type == "MARKET":
                            order_type_desc = "market order"
                        
                        logger.info(f"🚫 Cancelling follower {order_type_desc}: {follower_trade.symbol} {follower_trade.side} for account {follower_trade.account_id}")
                        
                        # Cancel the order on Binance
                        success = await follower_client.cancel_order(
                            follower_trade.symbol, 
                            str(follower_trade.binance_order_id)
                        )
                        
                        if success:
                            # Update follower trade status
                            follower_trade.status = 'CANCELLED'
                            session.commit()
                            cancelled_count += 1
                            
                            logger.info(f"✅ Cancelled follower {order_type_desc} {follower_trade.binance_order_id} for account {follower_trade.account_id}")
                            
                            # Enhanced logging for different order types
                            if follower_trade.order_type in ["STOP_MARKET", "TAKE_PROFIT_MARKET"]:
                                self.add_system_log("INFO", f"🚫 Cancelled follower {order_type_desc}: {follower_trade.symbol} (master {order_type_desc} cancelled)", follower_trade.account_id, follower_trade.id)
                            else:
                                self.add_system_log("INFO", f"🚫 Cancelled follower {order_type_desc}: {follower_trade.symbol} (master order cancelled)", follower_trade.account_id, follower_trade.id)
                        else:
                            logger.error(f"❌ Failed to cancel follower {order_type_desc} {follower_trade.binance_order_id} for account {follower_trade.account_id}")
                            self.add_system_log("ERROR", f"❌ Failed to cancel follower {order_type_desc}: {follower_trade.symbol}", follower_trade.account_id, follower_trade.id)
                    else:
                        logger.warning(f"⚠️ No Binance order ID found for follower trade {follower_trade.id}")
                        
                except Exception as cancel_error:
                    logger.error(f"❌ Error cancelling follower trade {follower_trade.id}: {cancel_error}")
                    self.add_system_log("ERROR", f"❌ Error cancelling follower order: {cancel_error}", follower_trade.account_id, follower_trade.id)
            
            if cancelled_count > 0:
                logger.info(f"✅ Successfully cancelled {cancelled_count}/{len(follower_trades)} follower orders")
                self.add_system_log("INFO", f"🚫 Master order cancelled - {cancelled_count} follower orders cancelled", master_trade.account_id, master_trade.id)
            else:
                logger.warning(f"⚠️ No follower orders were successfully cancelled for master trade {master_trade.id}")
            
            # Check if we should also close follower positions when master cancels orders
            await self.check_position_cleanup_after_cancellation(master_trade, session)
                
        except Exception as e:
            logger.error(f"❌ Error handling master order cancellation with trade: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            session.rollback()

    async def handle_cancellation_by_order_details(self, master_id: int, order: dict, session: Session):
        """Handle cancellation by searching for follower trades using order details"""
        try:
            order_symbol = order.get('symbol')
            order_side = order.get('side')
            order_time = datetime.utcfromtimestamp(order.get('time', order.get('updateTime', 0)) / 1000)
            order_quantity = float(order.get('origQty', 0))
            
            logger.info(f"🔍 Searching for follower trades to cancel: {order_symbol} {order_side} {order_quantity}")
            
            # Search for recent follower trades that match this order criteria
            # Look for orders placed within a reasonable time window (last 30 minutes)
            time_window = order_time - timedelta(minutes=30), order_time + timedelta(minutes=30)
            
            follower_trades = session.query(Trade).filter(
                Trade.symbol == order_symbol,
                Trade.side == order_side,
                Trade.copied_from_master == True,
                Trade.status.in_(['PENDING', 'PARTIALLY_FILLED']),  # Only active orders
                Trade.created_at >= time_window[0],
                Trade.created_at <= time_window[1]
            ).all()
            
            logger.info(f"🔍 Found {len(follower_trades)} potential follower trades to cancel")
            
            # Get copy trading configurations for this master to filter relevant followers
            configs = session.query(CopyTradingConfig).filter(
                CopyTradingConfig.master_account_id == master_id,
                CopyTradingConfig.is_active == True
            ).all()
            
            relevant_follower_ids = {config.follower_account_id for config in configs}
            
            # Filter trades to only those from relevant followers
            relevant_trades = [
                trade for trade in follower_trades 
                if trade.account_id in relevant_follower_ids
            ]
            
            logger.info(f"🔍 Found {len(relevant_trades)} relevant follower trades to cancel")
            
            cancelled_count = 0
            for follower_trade in relevant_trades:
                try:
                    follower_client = self.follower_clients.get(follower_trade.account_id)
                    if not follower_client:
                        logger.error(f"❌ Follower client not found for account {follower_trade.account_id}")
                        continue
                    
                    if follower_trade.binance_order_id:
                        logger.info(f"🚫 Cancelling follower order: {follower_trade.symbol} {follower_trade.side} {follower_trade.quantity} for account {follower_trade.account_id}")
                        
                        # Cancel the order on Binance
                        success = await follower_client.cancel_order(
                            follower_trade.symbol, 
                            str(follower_trade.binance_order_id)
                        )
                        
                        if success:
                            # Update follower trade status
                            follower_trade.status = 'CANCELLED'
                            session.commit()
                            cancelled_count += 1
                            
                            logger.info(f"✅ Cancelled follower order {follower_trade.binance_order_id} for account {follower_trade.account_id}")
                            self.add_system_log("INFO", f"🚫 Cancelled follower order: {follower_trade.symbol} {follower_trade.side} (master order cancelled)", follower_trade.account_id, follower_trade.id)
                        else:
                            logger.error(f"❌ Failed to cancel follower order {follower_trade.binance_order_id} for account {follower_trade.account_id}")
                            self.add_system_log("ERROR", f"❌ Failed to cancel follower order: {follower_trade.symbol}", follower_trade.account_id, follower_trade.id)
                    else:
                        logger.warning(f"⚠️ No Binance order ID found for follower trade {follower_trade.id}")
                        
                except Exception as cancel_error:
                    logger.error(f"❌ Error cancelling follower trade {follower_trade.id}: {cancel_error}")
                    self.add_system_log("ERROR", f"❌ Error cancelling follower order: {cancel_error}", follower_trade.account_id, follower_trade.id)
            
            if cancelled_count > 0:
                logger.info(f"✅ Successfully cancelled {cancelled_count} follower orders by order details")
                self.add_system_log("INFO", f"🚫 Master order cancelled - {cancelled_count} follower orders cancelled by details search", master_id)
            else:
                logger.info(f"ℹ️ No follower orders found to cancel for master order cancellation")
                
        except Exception as e:
            logger.error(f"❌ Error handling cancellation by order details: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            session.rollback()
    
    async def cancel_recent_follower_orders_by_pattern(self, master_id: int, master_order: dict, session: Session):
        """Cancel recent follower orders that match the master order pattern - backup cancellation method"""
        try:
            logger.info(f"🔍 BACKUP CANCELLATION: Searching for recent follower orders matching master order pattern")
            
            order_symbol = master_order.get('symbol')
            order_side = master_order.get('side')
            order_time = datetime.utcfromtimestamp(master_order.get('time', master_order.get('updateTime', 0)) / 1000)
            
            # Get copy trading configs for this master to find follower accounts
            configs = session.query(CopyTradingConfig).filter(
                CopyTradingConfig.master_account_id == master_id,
                CopyTradingConfig.is_active == True
            ).all()
            
            if not configs:
                logger.info(f"ℹ️ No active follower configs found for master {master_id}")
                return
            
            logger.info(f"🔍 Checking {len(configs)} follower accounts for recent orders to cancel")
            
            # Check each follower account for recent matching orders
            cancelled_count = 0
            for config in configs:
                try:
                    # Look for recent follower orders within 5 minutes of the master order
                    time_window_start = order_time - timedelta(minutes=2)
                    time_window_end = order_time + timedelta(minutes=3)
                    
                    recent_follower_orders = session.query(Trade).filter(
                        Trade.account_id == config.follower_account_id,
                        Trade.symbol == order_symbol,
                        Trade.side == order_side,
                        Trade.status.in_(['PENDING', 'PARTIALLY_FILLED']),
                        Trade.created_at >= time_window_start,
                        Trade.created_at <= time_window_end,
                        Trade.copied_from_master == True
                    ).all()
                    
                    if recent_follower_orders:
                        logger.info(f"🎯 Found {len(recent_follower_orders)} recent follower orders to cancel for account {config.follower_account_id}")
                        
                        # Cancel each order
                        for follower_order in recent_follower_orders:
                            try:
                                follower_client = self.follower_clients.get(config.follower_account_id)
                                if follower_client and follower_order.binance_order_id:
                                    success = await follower_client.cancel_order(
                                        follower_order.symbol,
                                        str(follower_order.binance_order_id)
                                    )
                                    
                                    if success:
                                        follower_order.status = 'CANCELLED'
                                        session.commit()
                                        cancelled_count += 1
                                        logger.info(f"✅ BACKUP CANCEL: Cancelled follower order {follower_order.binance_order_id}")
                                        self.add_system_log("INFO", f"🚫 Backup cancellation: {follower_order.symbol} order cancelled", config.follower_account_id, follower_order.id)
                                    
                            except Exception as cancel_error:
                                logger.error(f"❌ Error in backup cancellation: {cancel_error}")
                    else:
                        logger.debug(f"ℹ️ No recent matching orders found for follower {config.follower_account_id}")
                        
                except Exception as follower_error:
                    logger.error(f"❌ Error checking follower {config.follower_account_id}: {follower_error}")
            
            if cancelled_count > 0:
                logger.info(f"✅ BACKUP CANCELLATION: Successfully cancelled {cancelled_count} follower orders")
            else:
                logger.info(f"ℹ️ BACKUP CANCELLATION: No additional follower orders found to cancel")
                
        except Exception as e:
            logger.error(f"❌ Error in backup cancellation method: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def handle_master_order_cancellation(self, master_id: int, master_order_id: str, session: Session):
        """Handle cancellation of master orders by cancelling corresponding follower orders (Legacy method)"""
        try:
            logger.info(f"🚫 Handling master order cancellation: {master_order_id}")
            
            # Find the master trade record
            master_trade = session.query(Trade).filter(
                Trade.account_id == master_id,
                Trade.binance_order_id == str(master_order_id)
            ).first()
            
            if not master_trade:
                logger.warning(f"⚠️ Master trade not found for cancelled order {master_order_id}")
                return
            
            # Update master trade status
            master_trade.status = 'CANCELLED'
            session.commit()
            
            logger.info(f"📝 Updated master trade {master_trade.id} status to CANCELLED")
            
            # Use the new method with the trade record
            await self.handle_master_order_cancellation_with_trade(master_trade, session)
            
        except Exception as e:
            logger.error(f"❌ Error handling master order cancellation: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            session.rollback()
    
    async def handle_position_closing(self, master_id: int, order: dict, session: Session):
        """Handle position closing orders (market orders that close existing positions)"""
        try:
            order_id = str(order['orderId'])
            logger.info(f"🔄 Handling position closing order: {order_id}")
            
            # Create a temporary trade object to use existing closing logic
            temp_trade = Trade(
                account_id=master_id,
                symbol=order['symbol'],
                side=order['side'],
                order_type=order['type'],
                quantity=float(order.get('executedQty', order.get('origQty', 0))),
                price=float(order.get('avgPrice', order.get('price', 0))),
                status='FILLED',
                binance_order_id=str(order['orderId']),
                copied_from_master=False
            )
            
            # Add to database
            session.add(temp_trade)
            session.commit()
            session.refresh(temp_trade)
            
            logger.info(f"✅ Created trade record {temp_trade.id} for position closing")
            
            # Check if this is a position closing order and close follower positions
            if await self.is_position_closing_order(master_id, temp_trade, session):
                logger.info(f"🔄 Confirmed position closing - closing follower positions")
                await self.close_follower_positions(temp_trade, session)
            else:
                logger.info(f"📈 Not a position closing order - copying as regular trade")
                await self.copy_trade_to_followers(temp_trade, session)
            
        except Exception as e:
            logger.error(f"❌ Error handling position closing: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            session.rollback()
    
    async def remove_account(self, account_id: int):
        """Remove an account from the engine"""
        try:
            if account_id in self.master_clients:
                # Stop monitoring task
                if account_id in self.monitoring_tasks:
                    self.monitoring_tasks[account_id].cancel()
                    del self.monitoring_tasks[account_id]
                
                # Close client
                client = self.master_clients[account_id]
                client.stop_user_socket()
                del self.master_clients[account_id]
                
                logger.info(f"Removed master account: {account_id}")
                
            elif account_id in self.follower_clients:
                # Close client
                client = self.follower_clients[account_id]
                client.stop_user_socket()
                del self.follower_clients[account_id]
                
                logger.info(f"Removed follower account: {account_id}")
                
        except Exception as e:
            logger.error(f"Error removing account: {e}")

# Global instance
copy_trading_engine = CopyTradingEngine()
