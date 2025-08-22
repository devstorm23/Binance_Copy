#!/usr/bin/env python3
"""
Check Copy Trading Configurations Script
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

def check_configurations():
    """Check the current copy trading setup"""
    try:
        session = get_session()
        
        print("=" * 60)
        print("COPY TRADING CONFIGURATION CHECK")
        print("=" * 60)
        
        # Check accounts
        all_accounts = session.query(Account).all()
        print(f"📊 Total accounts: {len(all_accounts)}")
        
        master_accounts = [acc for acc in all_accounts if acc.is_master]
        follower_accounts = [acc for acc in all_accounts if not acc.is_master]
        
        print(f"   - Master accounts: {len(master_accounts)}")
        print(f"   - Follower accounts: {len(follower_accounts)}")
        print()
        
        if all_accounts:
            print("📋 ACCOUNTS:")
            for account in all_accounts:
                account_type = "MASTER" if account.is_master else "FOLLOWER"
                status = "ACTIVE" if account.is_active else "INACTIVE"
                print(f"   - Account {account.id}: {account.name} ({account_type}, {status})")
        else:
            print("❌ NO ACCOUNTS FOUND!")
            print("   You need to add accounts first using the dashboard or API")
            return False
        
        print()
        
        # Check copy trading configurations
        all_configs = session.query(CopyTradingConfig).all()
        print(f"🔗 Total copy trading configurations: {len(all_configs)}")
        
        if all_configs:
            print("📋 COPY TRADING CONFIGURATIONS:")
            for config in all_configs:
                status = "ACTIVE" if config.is_active else "INACTIVE"
                master_name = next((acc.name for acc in all_accounts if acc.id == config.master_account_id), "Unknown")
                follower_name = next((acc.name for acc in all_accounts if acc.id == config.follower_account_id), "Unknown")
                print(f"   - Config {config.id}: {master_name} (ID:{config.master_account_id}) -> {follower_name} (ID:{config.follower_account_id}) ({status})")
        else:
            print("❌ NO COPY TRADING CONFIGURATIONS FOUND!")
            print("   This is why new orders aren't being copied!")
            print()
            print("💡 TO FIX THIS:")
            if master_accounts and follower_accounts:
                print("   1. Go to the dashboard (http://localhost:5000)")
                print("   2. Navigate to 'Copy Trading' section")
                print("   3. Create a new copy trading configuration")
                print(f"   4. Set Master: {master_accounts[0].name} (ID: {master_accounts[0].id})")
                print(f"   5. Set Follower: {follower_accounts[0].name} (ID: {follower_accounts[0].id})")
                print("   6. Set multiplier and activate the configuration")
                return False
            else:
                print("   You need to add both master and follower accounts first!")
                return False
        
        print()
        print("✅ Configuration check completed!")
        session.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Error checking configurations: {e}")
        if session:
            session.close()
        return False

def create_sample_config():
    """Create a sample copy trading configuration if accounts exist"""
    try:
        session = get_session()
        
        # Get accounts
        master_accounts = session.query(Account).filter(Account.is_master == True, Account.is_active == True).all()
        follower_accounts = session.query(Account).filter(Account.is_master == False, Account.is_active == True).all()
        
        if not master_accounts:
            print("❌ No active master accounts found!")
            return False
        
        if not follower_accounts:
            print("❌ No active follower accounts found!")
            return False
        
        # Check if configuration already exists
        existing_config = session.query(CopyTradingConfig).filter(
            CopyTradingConfig.master_account_id == master_accounts[0].id,
            CopyTradingConfig.follower_account_id == follower_accounts[0].id
        ).first()
        
        if existing_config:
            print(f"ℹ️ Configuration already exists between {master_accounts[0].name} and {follower_accounts[0].name}")
            if not existing_config.is_active:
                existing_config.is_active = True
                session.commit()
                print("✅ Activated existing configuration")
            return True
        
        # Create new configuration
        new_config = CopyTradingConfig(
            master_account_id=master_accounts[0].id,
            follower_account_id=follower_accounts[0].id,
            position_multiplier=1.0,  # 1:1 ratio
            is_active=True
        )
        
        session.add(new_config)
        session.commit()
        
        print(f"✅ Created copy trading configuration:")
        print(f"   Master: {master_accounts[0].name} (ID: {master_accounts[0].id})")
        print(f"   Follower: {follower_accounts[0].name} (ID: {follower_accounts[0].id})")
        print(f"   Multiplier: 1.0")
        print(f"   Status: ACTIVE")
        
        session.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Error creating configuration: {e}")
        if session:
            session.rollback()
            session.close()
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Check copy trading configurations')
    parser.add_argument('--create-config', action='store_true', help='Create a sample configuration if accounts exist')
    
    args = parser.parse_args()
    
    if args.create_config:
        print("Creating sample copy trading configuration...")
        create_sample_config()
    else:
        check_configurations()
