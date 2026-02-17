#!/usr/bin/env python3
"""
Migration script to add velocity_5m and velocity_20m columns to tweet_data table.
Run this once to add support for 5-minute and 20-minute velocity tracking.
"""
import sys
sys.path.insert(0, 'src')

from database import DatabaseManager
from sqlalchemy import text

def add_velocity_columns():
    """Add velocity_5m and velocity_20m columns to tweet_data table if they don't exist."""
    db = DatabaseManager()

    with db.get_session() as session:
        try:
            # Check if velocity_5m column exists
            result = session.execute(text("""
                SELECT COUNT(*)
                FROM pragma_table_info('tweet_data')
                WHERE name = 'velocity_5m'
            """))
            has_5m = result.scalar() > 0

            # Check if velocity_20m column exists
            result = session.execute(text("""
                SELECT COUNT(*)
                FROM pragma_table_info('tweet_data')
                WHERE name = 'velocity_20m'
            """))
            has_20m = result.scalar() > 0

            if has_5m and has_20m:
                print("✓ velocity_5m and velocity_20m columns already exist")
                return

            # Add velocity_5m column if it doesn't exist
            if not has_5m:
                print("Adding velocity_5m column to tweet_data table...")
                session.execute(text("""
                    ALTER TABLE tweet_data
                    ADD COLUMN velocity_5m FLOAT
                """))
                print("✓ velocity_5m column added successfully")

            # Add velocity_20m column if it doesn't exist
            if not has_20m:
                print("Adding velocity_20m column to tweet_data table...")
                session.execute(text("""
                    ALTER TABLE tweet_data
                    ADD COLUMN velocity_20m FLOAT
                """))
                print("✓ velocity_20m column added successfully")

            session.commit()

        except Exception as e:
            print(f"Error adding columns: {e}")
            session.rollback()
            raise

if __name__ == "__main__":
    print("Running migration: add velocity_5m and velocity_20m columns")
    add_velocity_columns()
    print("\nMigration complete! The bot will now track 5-minute and 20-minute velocities.")
