#!/usr/bin/env python3
"""
Copy Trading Bot - Main Application
A comprehensive copy trading system for Binance futures trading.

This application provides:
- Real-time copy trading from master accounts to follower accounts
- Web-based dashboard for monitoring and control
- REST API for programmatic access
- Secure API key management
- Risk management and position sizing
- Comprehensive logging and monitoring

Author: Copy Trading Bot Team
Version: 1.0.0
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Add the current directory to Python path
sys.path.append(str(Path(__file__).parent))

from config import Config
from models import create_database
from copy_trading_engine import copy_trading_engine
import uvicorn
from api import app as api_app
from dashboard import app as dashboard_app, socketio

# Setup logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def check_server_status():
    """Check if both servers are running"""
    import requests
    
    print("🔍 Checking server status...")
    
    # Check API server
    try:
        response = requests.get("http://localhost:8000/health", timeout=3)
        if response.status_code == 200:
            print("✅ API Server (port 8000): RUNNING")
        else:
            print(f"❌ API Server (port 8000): RESPONDING WITH STATUS {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ API Server (port 8000): NOT RUNNING (Connection refused)")
    except Exception as e:
        print(f"❌ API Server (port 8000): ERROR ({e})")
    
    # Check dashboard (try multiple ports)
    dashboard_found = False
    for port in [5000, 5001, 5002]:
        try:
            response = requests.get(f"http://localhost:{port}", timeout=3)
            if response.status_code == 200:
                print(f"✅ Dashboard (port {port}): RUNNING")
                dashboard_found = True
                break
        except:
            continue
    
    if not dashboard_found:
        print("❌ Dashboard: NOT RUNNING on ports 5000-5002")
    
    print("\n💡 If servers are not running, check the logs for error details.")

async def initialize_system():
    """Initialize the copy trading system"""
    try:
        logger.info("Starting Copy Trading Bot...")
        
        # Create database and tables
        logger.info("Creating database...")
        create_database()
        
        # Initialize copy trading engine
        logger.info("Initializing copy trading engine...")
        success = await copy_trading_engine.initialize()
        if not success:
            logger.error("Failed to initialize copy trading engine")
            return False
        
        logger.info("System initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize system: {e}")
        return False

def start_api_server():
    """Start the FastAPI server"""
    try:
        logger.info("Starting API server on port 8000...")
        import uvicorn
        import nest_asyncio
        
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Apply nest_asyncio to allow nested event loops
        nest_asyncio.apply()
        
        # Run uvicorn server
        uvicorn.run(
            api_app,
            host="0.0.0.0",
            port=8000,
            log_level=Config.LOG_LEVEL.lower(),
            access_log=True,
            loop="asyncio"
        )
    except Exception as e:
        logger.error(f"Failed to start API server: {e}")
        import traceback
        logger.error(f"API server error traceback: {traceback.format_exc()}")

def start_dashboard():
    """Start the Flask dashboard"""
    try:
        logger.info("Starting dashboard on port 5000...")
        
        # Check if port is already in use and find available port
        import socket
        def find_available_port(start_port=5000, max_attempts=10):
            for port in range(start_port, start_port + max_attempts):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = sock.connect_ex(('localhost', port))
                sock.close()
                if result != 0:  # Port is available
                    return port
            return None
        
        port = find_available_port()
        if port is None:
            logger.error("No available ports found for dashboard")
            return
        
        if port != 5000:
            logger.warning(f"Port 5000 is already in use. Using port {port}...")
        
        logger.info(f"Dashboard will be available at: http://localhost:{port}")
            
        socketio.run(
            dashboard_app,
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True
        )
    except Exception as e:
        logger.error(f"Failed to start dashboard: {e}")
        import traceback
        logger.error(f"Dashboard error traceback: {traceback.format_exc()}")

async def main():
    """Main application entry point"""
    try:
        # Initialize the system
        if not await initialize_system():
            logger.error("System initialization failed. Exiting.")
            sys.exit(1)
        
        # Start the copy trading engine
        logger.info("Starting copy trading engine...")
        await copy_trading_engine.start_monitoring()
        
        logger.info("Copy Trading Bot is running!")
        logger.info("API Server: http://localhost:8000")
        logger.info("Dashboard: http://localhost:5000")
        logger.info("Press Ctrl+C to stop the application")
        
        # Start both servers in separate threads with proper error handling
        import threading
        import time
        
        # Start API server in background thread
        logger.info("Starting API server thread...")
        api_thread = threading.Thread(target=start_api_server, daemon=True, name="APIServer")
        api_thread.start()
        
        # Wait longer for API server to start and verify it's running
        logger.info("Waiting for API server to start...")
        api_started = False
        for i in range(10):  # Try for 10 seconds
            time.sleep(1)
            try:
                import requests
                response = requests.get("http://localhost:8000/health", timeout=2)
                if response.status_code == 200:
                    logger.info("✅ API server is running successfully")
                    api_started = True
                    break
            except:
                logger.info(f"⏳ Waiting for API server... ({i+1}/10)")
        
        if not api_started:
            logger.warning("⚠️ API server may not have started properly, but continuing with dashboard...")
        
        # Start dashboard in background thread
        logger.info("Starting dashboard thread...")
        dashboard_thread = threading.Thread(target=start_dashboard, daemon=True, name="Dashboard")
        dashboard_thread.start()
        
        # Give dashboard a moment to start
        time.sleep(2)
        
        try:
            # Keep the main thread alive
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down Copy Trading Bot...")
            await copy_trading_engine.stop_monitoring()
            logger.info("Copy Trading Bot stopped successfully")
            
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Copy Trading Bot')
    parser.add_argument('--check', action='store_true', help='Check server status and exit')
    parser.add_argument('--init-db', action='store_true', help='Initialize database and exit')
    args = parser.parse_args()
    
    # Handle special commands
    if args.check:
        check_server_status()
        sys.exit(0)
    
    if args.init_db:
        print("Initializing database...")
        create_database()
        print("Database initialized successfully!")
        sys.exit(0)
    
    # Check if running on Windows
    if os.name == 'nt':
        # Use Windows-specific event loop policy
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Run the main application
    asyncio.run(main())
