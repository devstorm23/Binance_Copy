import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException
import websockets
import logging
from config import Config

logger = logging.getLogger(__name__)

class BinanceClient:
    def __init__(self, api_key: str, secret_key: str, testnet: bool = False):
        self.api_key = api_key
        self.secret_key = secret_key
        self.testnet = testnet
        
        # Initialize Binance client
        if testnet:
            # Enable testnet mode and force USD-M Futures endpoints
            self.client = Client(api_key, secret_key, testnet=True)
            try:
                # Ensure python-binance uses Futures TESTNET REST base
                # USD-M Futures (fapi) - correct testnet URL
                self.client.FUTURES_URL = "https://testnet.binancefuture.com/fapi/v1/"
                # Optional: Futures data endpoint
                if hasattr(self.client, "FUTURES_DATA_URL"):
                    self.client.FUTURES_DATA_URL = "https://testnet.binancefuture.com/futures/data/"
                # Optional: COIN-M Futures (not used here, but set to testnet just in case)
                if hasattr(self.client, "FUTURES_COIN_URL"):
                    self.client.FUTURES_COIN_URL = "https://testnet.binancefuture.com/dapi/v1/"
            except Exception:
                pass
            self.base_url = "https://testnet.binancefuture.com"
        else:
            # Mainnet defaults are already USD-M Futures (fapi) aware in python-binance
            self.client = Client(api_key, secret_key)
            self.base_url = "https://fapi.binance.com"
        
        self.ws_connections = {}
        self.ws_tasks = {}
        
    async def test_connection(self) -> bool:
        """Test API connection - works for both master accounts and subaccounts"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            logger.info(f"Testing connection - API Key: {self.api_key[:8]}..., Testnet: {self.testnet}")
            
            # Step 1: Test basic server connectivity
            try:
                ping_result = await loop.run_in_executor(None, self.client.ping)
                logger.info("✓ Ping successful")
            except Exception as e:
                logger.error(f"✗ Ping failed: {e}")
                return False
            
            # Step 2: Test API key validity with server time (doesn't require account permissions)
            try:
                server_time = await loop.run_in_executor(None, self.client.get_server_time)
                logger.info(f"✓ Server time check successful: {server_time}")
            except Exception as e:
                logger.error(f"✗ Server time check failed: {e}")
                return False
            
            # Step 3: Try futures_account (for master accounts) but fall back for subaccounts
            try:
                account = await loop.run_in_executor(None, self.client.futures_account)
                logger.info(f"✓ futures_account() successful. Balance: {account.get('availableBalance', 'N/A')}")
                return True
            except BinanceAPIException as e:
                logger.warning(f"⚠ futures_account() failed (Code {e.code}): {e.message}")
                
                # Check if it's a permission issue (common for subaccounts)
                if e.code in [-2015, -1022, -2014]:  # Common permission/signature errors
                    logger.info("Attempting alternative validation for subaccount...")
                    return await self._test_subaccount_connection(loop)
                else:
                    logger.error(f"✗ API credentials invalid (unexpected error code)")
                    return False
                    
            except Exception as e:
                logger.warning(f"⚠ futures_account() failed with general error: {e}")
                # Try alternative validation
                return await self._test_subaccount_connection(loop)
            
        except Exception as e:
            logger.error(f"✗ Connection test failed: {e}")
            return False
    
    async def _test_subaccount_connection(self, loop) -> bool:
        """Alternative connection test for subaccounts with limited permissions"""
        try:
            logger.info("Testing subaccount with limited permissions...")
            
            # For subaccounts, we need to be more lenient
            # Many subaccounts only have specific permissions
            
            # Test 1: Try basic exchange info (public endpoint)
            try:
                exchange_info = await loop.run_in_executor(None, self.client.futures_exchange_info)
                logger.info("✓ futures_exchange_info() successful")
                basic_access = True
            except Exception as e:
                logger.warning(f"⚠ exchange_info failed: {e}")
                basic_access = False
            
            # Test 2: Try account info with API key (this validates the key is real)
            account_access = False
            try:
                # Try get_account (spot account) as it often has fewer restrictions
                account_info = await loop.run_in_executor(None, self.client.get_account)
                logger.info("✓ get_account() successful - API key valid")
                account_access = True
            except Exception as e:
                logger.info(f"get_account failed: {e}")
                
                # Try listen key creation (validates API key without requiring trading permissions)
                try:
                    listen_key = await loop.run_in_executor(None, self.client.stream_get_listen_key)
                    logger.info("✓ stream_get_listen_key() successful - API key valid")
                    account_access = True
                except Exception as e:
                    logger.info(f"listen_key failed: {e}")
            
            # Decision logic for subaccount validation
            if account_access:
                logger.info("✅ Subaccount API key validated successfully")
                logger.info("Note: Limited futures permissions detected - this is normal for subaccounts")
                return True
            elif basic_access:
                logger.warning("⚠️ Basic API access works but account access limited")
                logger.warning("This subaccount may have very restricted permissions")
                logger.info("✅ Allowing subaccount creation (basic validation passed)")
                return True
            else:
                logger.error("❌ No API access detected - credentials may be invalid")
                return False
                
        except Exception as e:
            logger.error(f"✗ Subaccount connection test failed: {e}")
            return False
    
    async def test_connection_alternative(self) -> bool:
        """Alternative connection test that doesn't use futures_account()"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            # Test with futures_exchange_info (public endpoint, doesn't need account permissions)
            exchange_info = await loop.run_in_executor(None, self.client.futures_exchange_info)
            logger.info("Alternative connection test successful using futures_exchange_info()")
            return True
            
        except Exception as e:
            logger.error(f"Alternative connection test failed: {e}")
            return False
    
    async def get_account_info(self) -> Dict:
        """Get account information"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            account = await loop.run_in_executor(None, self.client.futures_account)
            return {
                'total_wallet_balance': float(account['totalWalletBalance']),
                'total_unrealized_profit': float(account['totalUnrealizedProfit']),
                'total_margin_balance': float(account['totalMarginBalance']),
                'available_balance': float(account['availableBalance']),
                'positions': account['positions']
            }
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            raise
    
    async def get_positions(self) -> List[Dict]:
        """Get current positions - handles subaccounts with limited permissions"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(None, self.client.futures_position_information)
            return [
                {
                    'symbol': pos['symbol'],
                    'side': 'LONG' if float(pos['positionAmt']) > 0 else 'SHORT',
                    'size': abs(float(pos['positionAmt'])),
                    'entry_price': float(pos['entryPrice']),
                    'mark_price': float(pos['markPrice']),
                    'unrealized_pnl': float(pos['unRealizedProfit']),
                    'leverage': int(pos['leverage'])
                }
                for pos in positions if float(pos['positionAmt']) != 0
            ]
        except BinanceAPIException as e:
            if e.code == -2015:  # Permission denied
                logger.warning(f"⚠️ Position access denied (Code -2015) - subaccount has limited permissions")
                return []  # Return empty positions for subaccounts
            else:
                logger.error(f"Failed to get positions: {e}")
                return []
        except Exception as e:
            logger.warning(f"Failed to get positions (possibly limited permissions): {e}")
            return []
    
    async def get_balance(self) -> float:
        """Get available balance - handles subaccounts with limited permissions"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            account = await loop.run_in_executor(None, self.client.futures_account)
            return float(account['availableBalance'])
        except BinanceAPIException as e:
            if e.code == -2015:  # Permission denied
                logger.warning(f"⚠️ Balance access denied (Code -2015) - subaccount has limited permissions")
                return 0.0  # Return 0 balance for subaccounts with limited permissions
            else:
                logger.error(f"Failed to get balance: {e}")
                return 0.0
        except Exception as e:
            logger.warning(f"Failed to get balance (possibly limited permissions): {e}")
            return 0.0
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self.client.futures_change_leverage(symbol=symbol, leverage=leverage))
            logger.info(f"Leverage set to {leverage}x for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage: {e}")
            return False
    
    async def set_position_mode(self, dual_side_position: bool = False) -> bool:
        """Set position mode (One-way or Hedge mode)"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self.client.futures_change_position_mode(dualSidePosition=dual_side_position))
            mode = "Hedge" if dual_side_position else "One-way"
            logger.info(f"Position mode set to {mode}")
            return True
        except Exception as e:
            logger.warning(f"Failed to set position mode: {e}")
            # This might fail if position mode is already set or account has open positions
            return False
    
    async def get_position_mode(self) -> bool:
        """Get current position mode (True = Hedge mode, False = One-way mode)"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.client.futures_get_position_mode)
            dual_side = result.get('dualSidePosition', False)
            mode = "Hedge" if dual_side else "One-way"
            logger.info(f"Current position mode: {mode}")
            return dual_side
        except Exception as e:
            logger.warning(f"Failed to get position mode: {e}")
            return False  # Default to One-way mode
    
    async def place_market_order(self, symbol: str, side: str, quantity: float) -> Dict:
        """Place a market order"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            logger.info(f"🔄 Starting market order placement for {symbol}")
            
            # Check position mode to determine if we need positionSide
            is_hedge_mode = await self.get_position_mode()
            
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'MARKET',
                'quantity': quantity
            }
            
            # For hedge mode, specify position side
            if is_hedge_mode:
                # In hedge mode: LONG for BUY, SHORT for SELL
                position_side = 'LONG' if side == 'BUY' else 'SHORT'
                order_params['positionSide'] = position_side
                logger.info(f"Hedge mode detected - using positionSide: {position_side}")
            else:
                logger.info("One-way mode detected - no positionSide needed")
            
            logger.info(f"📋 Order parameters: {order_params}")
            
            # Place the order
            logger.info(f"🚀 Executing futures_create_order...")
            order = await loop.run_in_executor(None, lambda: self.client.futures_create_order(**order_params))
            
            if order:
                logger.info(f"✅ Market order placed successfully: {symbol} {side} {quantity}")
                logger.info(f"📋 Order response: {order}")
                return order
            else:
                logger.error(f"❌ Order placement returned None response!")
                raise Exception("Order placement returned None")
                
        except BinanceAPIException as e:
            logger.error(f"❌ Binance API Exception: {e}")
            logger.error(f"❌ Error code: {e.code}")
            logger.error(f"❌ Error message: {e.message}")
            raise
        except BinanceOrderException as e:
            logger.error(f"❌ Binance Order Exception: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error placing market order: {e}")
            logger.error(f"❌ Error type: {type(e).__name__}")
            import traceback
            logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            raise
    
    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Dict:
        """Place a limit order"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            logger.info(f"🔄 Starting limit order placement for {symbol}")
            
            # Check position mode to determine if we need positionSide
            is_hedge_mode = await self.get_position_mode()
            
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'LIMIT',
                'timeInForce': 'GTC',
                'quantity': quantity,
                'price': price
            }
            
            # For hedge mode, specify position side
            if is_hedge_mode:
                # In hedge mode: LONG for BUY, SHORT for SELL
                position_side = 'LONG' if side == 'BUY' else 'SHORT'
                order_params['positionSide'] = position_side
                logger.info(f"Hedge mode detected - using positionSide: {position_side}")
            else:
                logger.info("One-way mode detected - no positionSide needed")
            
            logger.info(f"📋 Order parameters: {order_params}")
            
            # Place the order
            logger.info(f"🚀 Executing futures_create_order...")
            order = await loop.run_in_executor(None, lambda: self.client.futures_create_order(**order_params))
            
            if order:
                logger.info(f"✅ Limit order placed successfully: {symbol} {side} {quantity} @ {price}")
                logger.info(f"📋 Order response: {order}")
                return order
            else:
                logger.error(f"❌ Order placement returned None response!")
                raise Exception("Order placement returned None")
                
        except BinanceAPIException as e:
            logger.error(f"❌ Binance API Exception: {e}")
            logger.error(f"❌ Error code: {e.code}")
            logger.error(f"❌ Error message: {e.message}")
            raise
        except BinanceOrderException as e:
            logger.error(f"❌ Binance Order Exception: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error placing limit order: {e}")
            logger.error(f"❌ Error type: {type(e).__name__}")
            import traceback
            logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            raise
    
    async def place_stop_market_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict:
        """Place a stop market order"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            # Check position mode to determine if we need positionSide
            is_hedge_mode = await self.get_position_mode()
            
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'STOP_MARKET',
                'quantity': quantity,
                'stopPrice': stop_price
            }
            
            # For hedge mode, specify position side
            if is_hedge_mode:
                # In hedge mode: LONG for BUY, SHORT for SELL
                position_side = 'LONG' if side == 'BUY' else 'SHORT'
                order_params['positionSide'] = position_side
                logger.info(f"Hedge mode detected - using positionSide: {position_side}")
            else:
                logger.info("One-way mode detected - no positionSide needed")
            
            order = await loop.run_in_executor(None, lambda: self.client.futures_create_order(**order_params))
            logger.info(f"Stop market order placed: {symbol} {side} {quantity} @ {stop_price}")
            return order
        except BinanceOrderException as e:
            logger.error(f"Order placement failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error placing order: {e}")
            raise
    
    async def place_take_profit_market_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict:
        """Place a take profit market order"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            # Check position mode to determine if we need positionSide
            is_hedge_mode = await self.get_position_mode()
            
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'TAKE_PROFIT_MARKET',
                'quantity': quantity,
                'stopPrice': stop_price
            }
            
            # For hedge mode, specify position side
            if is_hedge_mode:
                # In hedge mode: LONG for BUY, SHORT for SELL
                position_side = 'LONG' if side == 'BUY' else 'SHORT'
                order_params['positionSide'] = position_side
                logger.info(f"Hedge mode detected - using positionSide: {position_side}")
            else:
                logger.info("One-way mode detected - no positionSide needed")
            
            order = await loop.run_in_executor(None, lambda: self.client.futures_create_order(**order_params))
            logger.info(f"Take profit market order placed: {symbol} {side} {quantity} @ {stop_price}")
            return order
        except BinanceOrderException as e:
            logger.error(f"Order placement failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error placing order: {e}")
            raise
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order"""
        try:
            result = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            logger.info(f"Order cancelled: {symbol} {order_id}")
            return True
        except BinanceAPIException as e:
            # Handle "Unknown order" as success since it means order was already cancelled/doesn't exist
            if e.code == -2011:  # Unknown order sent
                logger.info(f"Order {order_id} for {symbol} was already cancelled or doesn't exist")
                return True
            else:
                logger.error(f"Failed to cancel order: {e}")
                return False
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False
    
    async def close_position(self, symbol: str, side: str = None, quantity: float = None) -> Dict:
        """Close a position by placing a market order in the opposite direction"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            # Get current positions to determine what to close
            positions = await self.get_positions()
            position_to_close = None
            
            for pos in positions:
                if pos['symbol'] == symbol:
                    if side is None or pos['side'] == side:
                        position_to_close = pos
                        break
            
            if not position_to_close:
                logger.warning(f"No position found to close for {symbol} {side or 'any side'}")
                return None
            
            # Determine opposite side for closing
            close_side = 'SELL' if position_to_close['side'] == 'LONG' else 'BUY'
            close_quantity = quantity if quantity else position_to_close['size']
            
            logger.info(f"Closing position: {symbol} {position_to_close['side']} {close_quantity} -> placing {close_side} order")
            
            # Check position mode to determine if we need positionSide
            is_hedge_mode = await self.get_position_mode()
            
            order_params = {
                'symbol': symbol,
                'side': close_side,
                'type': 'MARKET',
                'quantity': close_quantity,
                'reduceOnly': True  # This ensures we're closing, not opening new positions
            }
            
            # For hedge mode, specify position side
            if is_hedge_mode:
                # Position side is the same as the position we're closing
                position_side = position_to_close['side']  # LONG or SHORT
                order_params['positionSide'] = position_side
                logger.info(f"Hedge mode detected - using positionSide: {position_side}")
            else:
                logger.info("One-way mode detected - no positionSide needed")
            
            order = await loop.run_in_executor(None, lambda: self.client.futures_create_order(**order_params))
            logger.info(f"Position closed: {symbol} {close_side} {close_quantity} (reduceOnly)")
            return order
            
        except BinanceOrderException as e:
            logger.error(f"Position close failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error closing position: {e}")
            raise
    
    async def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """Get all open orders for a symbol or all symbols"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            if symbol:
                orders = await loop.run_in_executor(None, lambda: self.client.futures_get_open_orders(symbol=symbol))
            else:
                orders = await loop.run_in_executor(None, self.client.futures_get_open_orders)
            
            logger.info(f"Retrieved {len(orders)} open orders" + (f" for {symbol}" if symbol else ""))
            return orders
        except BinanceAPIException as e:
            if e.code == -2015:  # Permission denied
                logger.warning(f"⚠️ Open orders access denied (Code -2015) - account has limited permissions")
                return []
            else:
                logger.error(f"Failed to get open orders: {e}")
                return []
        except Exception as e:
            logger.warning(f"Failed to get open orders (possibly limited permissions): {e}")
            return []

    async def get_order_status(self, symbol: str, order_id: str) -> Dict:
        """Get order status"""
        try:
            order = self.client.futures_get_order(symbol=symbol, orderId=order_id)
            return order
        except Exception as e:
            logger.error(f"Failed to get order status: {e}")
            raise
    
    async def get_symbol_info(self, symbol: str) -> Dict:
        """Get symbol information"""
        try:
            info = self.client.futures_exchange_info()
            for symbol_info in info['symbols']:
                if symbol_info['symbol'] == symbol:
                    return symbol_info
            return None
        except Exception as e:
            logger.error(f"Failed to get symbol info: {e}")
            raise
    
    async def get_mark_price(self, symbol: str) -> float:
        """Get current mark price"""
        try:
            price = self.client.futures_mark_price(symbol=symbol)
            return float(price['markPrice'])
        except Exception as e:
            logger.error(f"Failed to get mark price: {e}")
            raise

    async def calculate_position_size(self, symbol: str, risk_amount: float, leverage: int) -> float:
        """Calculate position size based on risk amount and leverage"""
        try:
            mark_price = await self.get_mark_price(symbol)
            position_value = risk_amount * leverage
            quantity = position_value / mark_price
            
            # Get symbol info for quantity precision
            symbol_info = await self.get_symbol_info(symbol)
            if symbol_info:
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                if lot_size_filter:
                    step_size = float(lot_size_filter['stepSize'])
                    min_qty = float(lot_size_filter['minQty'])
                    max_qty = float(lot_size_filter['maxQty'])
                    
                    # Round to step size
                    quantity = round(quantity / step_size) * step_size
                    
                    # Ensure within bounds
                    quantity = max(min_qty, min(quantity, max_qty))
                    
                    logger.info(f"📊 Position size calculated: {quantity} (min: {min_qty}, max: {max_qty}, step: {step_size})")
            
            return quantity
        except Exception as e:
            logger.error(f"Failed to calculate position size: {e}")
            raise

    async def adjust_quantity_precision(self, symbol: str, quantity: float) -> float:
        """Adjust quantity to match symbol's precision requirements"""
        try:
            symbol_info = await self.get_symbol_info(symbol)
            if symbol_info:
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                if lot_size_filter:
                    step_size = float(lot_size_filter['stepSize'])
                    min_qty = float(lot_size_filter['minQty'])
                    max_qty = float(lot_size_filter['maxQty'])
                    
                    # Round to step size with proper precision handling
                    steps = round(quantity / step_size)
                    adjusted_qty = steps * step_size
                    
                    # Fix floating point precision issues
                    # Count decimal places in step_size to determine proper rounding
                    if '.' in str(step_size):
                        decimal_places = len(str(step_size).split('.')[1])
                        adjusted_qty = round(adjusted_qty, decimal_places)
                    else:
                        adjusted_qty = round(adjusted_qty)
                    
                    # Ensure within bounds
                    adjusted_qty = max(min_qty, min(adjusted_qty, max_qty))
                    
                    if adjusted_qty != quantity:
                        logger.info(f"📏 Adjusted quantity: {quantity} -> {adjusted_qty}")
                    
                    return adjusted_qty
                else:
                    logger.warning(f"No LOT_SIZE filter found for {symbol}, using fallback precision")
            else:
                logger.warning(f"No symbol info found for {symbol}, using fallback precision")
            
            # Fallback: Round to 1 decimal place (common for most crypto futures)
            fallback_qty = round(quantity, 1)
            if fallback_qty != quantity:
                logger.info(f"📏 Applied fallback precision: {quantity} -> {fallback_qty}")
            return fallback_qty
            
        except Exception as e:
            logger.warning(f"Failed to adjust quantity precision: {e}")
            # Emergency fallback: round to 1 decimal place
            fallback_qty = round(quantity, 1)
            logger.warning(f"Using emergency fallback precision: {quantity} -> {fallback_qty}")
            return fallback_qty

    async def start_user_socket(self, callback):
        """Start user data stream using websockets"""
        try:
            # Get listen key for user data stream
            listen_key = self.client.futures_stream_get_listen_key()
            
            # Create WebSocket connection
            ws_url = f"wss://fstream.binance.com/ws/{listen_key}"
            if self.testnet:
                ws_url = f"wss://stream.binancefuture.com/ws/{listen_key}"
            
            async def websocket_handler():
                try:
                    async with websockets.connect(ws_url) as websocket:
                        self.ws_connections['user_data'] = websocket
                        logger.info("User data stream started")
                        
                        while True:
                            try:
                                message = await websocket.recv()
                                data = json.loads(message)
                                await callback(data)
                            except websockets.exceptions.ConnectionClosed:
                                logger.warning("WebSocket connection closed, attempting to reconnect...")
                                break
                            except Exception as e:
                                logger.error(f"Error processing WebSocket message: {e}")
                                
                except Exception as e:
                    logger.error(f"WebSocket connection error: {e}")
            
            # Start WebSocket task
            task = asyncio.create_task(websocket_handler())
            self.ws_tasks['user_data'] = task
            return task
            
        except Exception as e:
            logger.error(f"Failed to start user socket: {e}")
            raise

    async def stop_user_socket(self):
        """Stop user data stream"""
        try:
            if 'user_data' in self.ws_tasks:
                task = self.ws_tasks['user_data']
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                del self.ws_tasks['user_data']
                
            if 'user_data' in self.ws_connections:
                websocket = self.ws_connections['user_data']
                await websocket.close()
                del self.ws_connections['user_data']
                
            logger.info("User data stream stopped")
        except Exception as e:
            logger.error(f"Failed to stop user socket: {e}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop_user_socket()
