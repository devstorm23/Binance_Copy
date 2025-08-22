#!/usr/bin/env python3
"""
Database migration to add max_risk_percentage field to copy_trading_config table
"""

import sqlite3
import sys
from datetime import datetime

def add_max_risk_percentage_column():
    """Add max_risk_percentage column to copy_trading_config table"""
    try:
        # Connect to the database
        conn = sqlite3.connect('copy_trading.db')
        cursor = conn.cursor()
        
        # Check if column already exists
        cursor.execute("PRAGMA table_info(copy_trading_config)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'max_risk_percentage' in columns:
            print("✅ Column max_risk_percentage already exists")
            return True
        
        # Add the new column with default value of 50.0
        cursor.execute("""
            ALTER TABLE copy_trading_config 
            ADD COLUMN max_risk_percentage REAL DEFAULT 50.0
        """)
        
        # Update existing records to have the default value
        cursor.execute("""
            UPDATE copy_trading_config 
            SET max_risk_percentage = 50.0 
            WHERE max_risk_percentage IS NULL
        """)
        
        conn.commit()
        conn.close()
        
        print("✅ Successfully added max_risk_percentage column to copy_trading_config table")
        print("   Default value: 50.0% (allows for proper proportional scaling)")
        return True
        
    except Exception as e:
        print(f"❌ Error adding max_risk_percentage column: {e}")
        return False

def main():
    """Main migration function"""
    print("🔄 Starting database migration...")
    print(f"   Timestamp: {datetime.now()}")
    print("   Adding max_risk_percentage column to copy_trading_config table")
    
    success = add_max_risk_percentage_column()
    
    if success:
        print("\n✅ Migration completed successfully!")
        print("\nNext steps:")
        print("1. Restart your copy trading system")
        print("2. New copy trading configurations will use 50% max risk by default")
        print("3. You can adjust max_risk_percentage per configuration via the API or dashboard")
        print("4. Higher values (e.g., 80-100%) allow better proportional scaling")
        print("5. Lower values (e.g., 10-20%) provide more conservative risk management")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
