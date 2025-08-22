#!/usr/bin/env python3
"""
Fix Copy Trading - Check and create configurations
"""

import sys
import os
from pathlib import Path

# Add the current directory to Python path
sys.path.append(str(Path(__file__).parent))

from models import get_session, Account, CopyTradingConfig
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Check and fix copy trading configurations"""
    try:
        session = get_session()
        
        print("🔧 COPY TRADING CONFIGURATION FIX")
        print("=" * 50)
        
        # Check accounts
        all_accounts = session.query(Account).all()
        print(f"📊 Total accounts: {len(all_accounts)}")
        
        if not all_accounts:
            print("❌ No accounts found! You need to add accounts first.")
            return False
        
        master_accounts = [acc for acc in all_accounts if acc.is_master and acc.is_active]
        follower_accounts = [acc for acc in all_accounts if not acc.is_master and acc.is_active]
        
        print(f"   - Active Master accounts: {len(master_accounts)}")
        print(f"   - Active Follower accounts: {len(follower_accounts)}")
        
        for account in all_accounts:
            account_type = "MASTER" if account.is_master else "FOLLOWER"
            status = "ACTIVE" if account.is_active else "INACTIVE"
            print(f"   - {account.name} (ID: {account.id}) - {account_type} - {status}")
        
        if not master_accounts:
            print("❌ No active master accounts found!")
            return False
        
        if not follower_accounts:
            print("❌ No active follower accounts found!")
            return False
        
        print()
        
        # Check existing configurations
        configs = session.query(CopyTradingConfig).all()
        print(f"📋 Existing copy trading configurations: {len(configs)}")
        
        active_configs = [c for c in configs if c.is_active]
        print(f"📋 Active configurations: {len(active_configs)}")
        
        if active_configs:
            print("✅ Found active configurations:")
            for config in active_configs:
                master_name = next((acc.name for acc in all_accounts if acc.id == config.master_account_id), "Unknown")
                follower_name = next((acc.name for acc in all_accounts if acc.id == config.follower_account_id), "Unknown")
                print(f"   - {master_name} -> {follower_name} (Multiplier: {config.position_multiplier})")
            return True
        
        # Create configuration if none exist
        print("⚠️ No active configurations found. Creating one...")
        
        master = master_accounts[0]
        follower = follower_accounts[0]
        
        # Check if inactive config exists
        existing_config = session.query(CopyTradingConfig).filter(
            CopyTradingConfig.master_account_id == master.id,
            CopyTradingConfig.follower_account_id == follower.id
        ).first()
        
        if existing_config:
            existing_config.is_active = True
            session.commit()
            print(f"✅ Activated existing configuration: {master.name} -> {follower.name}")
        else:
            new_config = CopyTradingConfig(
                master_account_id=master.id,
                follower_account_id=follower.id,
                position_multiplier=1.0,
                is_active=True
            )
            session.add(new_config)
            session.commit()
            print(f"✅ Created new configuration: {master.name} -> {follower.name} (1:1 ratio)")
        
        print()
        print("🎉 Copy trading should now work!")
        print("💡 Now restart your server and place a NEW order in the master account")
        
        session.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        if session:
            session.rollback()
            session.close()
        return False

if __name__ == "__main__":
    main()
