#!/usr/bin/env python3
"""
Migration script to add velocity_10m column to tweet_data table.
Run this once to add support for 10-minute velocity tracking.
"""
import sys
sys.path.insert(0, 'src')

from database import DatabaseManager
from sqlalchemy import text

def add_velocity_10m_column():
    """Add velocity_10m column to tweet_data table if it doesn't exist."""
    db = DatabaseManager()

    with db.get_session() as session:
        try:
            # Check if column already exists
            result = session.execute(text("""
                SELECT COUNT(*)
                FROM pragma_table_info('tweet_data')
                WHERE name = 'velocity_10m'
            """))
            exists = result.scalar() > 0

            if exists:
                print("✓ velocity_10m column already exists")
                return

            # Add the column
            print("Adding velocity_10m column to tweet_data table...")
            session.execute(text("""
                ALTER TABLE tweet_data
                ADD COLUMN velocity_10m FLOAT
            """))
            session.commit()
            print("✓ velocity_10m column added successfully")

        except Exception as e:
            print(f"Error adding column: {e}")
            session.rollback()
            raise

if __name__ == "__main__":
    print("Running migration: add velocity_10m column")
    add_velocity_10m_column()
    print("\nMigration complete! The bot will now track 10-minute velocities.")
