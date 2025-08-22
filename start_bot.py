#!/usr/bin/env python3
"""
Copy Trading Engine - Standalone Startup
"""

import asyncio
import sys
import os
from pathlib import Path

# Add the current directory to Python path
sys.path.append(str(Path(__file__).parent))

from config import Config
from models import create_database
from copy_trading_engine import copy_trading_engine

async def main():
    """Start the copy trading engine"""
    print("=" * 60)
    print("COPY TRADING ENGINE - STARTUP")
    print("=" * 60)
    
    try:
        # Initialize database
        print("Creating database...")
        create_database()
        
        # Initialize copy trading engine
        print("Initializing copy trading engine...")
        success = await copy_trading_engine.initialize()
        if not success:
            print("❌ Failed to initialize copy trading engine")
            return
        
        # Start copy trading engine
        print("Starting copy trading engine...")
        await copy_trading_engine.start_monitoring()
        
        print("✅ Copy Trading Engine is running!")
        print("✅ Monitoring for master account trades...")
        print()
        print("Press Ctrl+C to stop the engine")
        print("=" * 60)
        
        # Keep the engine running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down Copy Trading Engine...")
            await copy_trading_engine.stop_monitoring()
            print("✅ Copy Trading Engine stopped successfully")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return

if __name__ == "__main__":
    # Check if running on Windows
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Run the main function
    asyncio.run(main())
