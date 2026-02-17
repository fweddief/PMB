"""
Database manager for creating connections and sessions.
"""

import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from database.schema import Base
from database.paper_trading_schema import Base as PaperBase


class DatabaseManager:
    """
    Manages database connections and provides session context managers.
    """

    def __init__(self, database_url: str = None):
        """
        Initialize database manager.

        Args:
            database_url: SQLAlchemy database URL. If None, uses DATABASE_URL env var
                         or defaults to SQLite in data/ directory.
        """
        if database_url is None:
            database_url = os.getenv('DATABASE_URL', 'sqlite:///data/polymarket_bot.db')

        self.database_url = database_url
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def create_tables(self):
        """
        Create all tables defined in schema.
        """
        # Create data directory if using SQLite
        if self.database_url.startswith('sqlite'):
            os.makedirs('data', exist_ok=True)

        Base.metadata.create_all(bind=self.engine)
        PaperBase.metadata.create_all(bind=self.engine)
        self._ensure_schema_upgrades()
        print(f"✓ Database tables created at {self.database_url}")

    def drop_tables(self):
        """
        Drop all tables. Use with caution!
        """
        Base.metadata.drop_all(bind=self.engine)
        print("✓ All tables dropped")

    @contextmanager
    def get_session(self) -> Session:
        """
        Provide a transactional scope for database operations.

        Usage:
            with db_manager.get_session() as session:
                session.add(object)
                session.commit()
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_new_session(self) -> Session:
        """
        Get a new session. Remember to close it when done!

        Returns:
            Session: SQLAlchemy session
        """
        return self.SessionLocal()

    def _ensure_schema_upgrades(self):
        """Apply lightweight schema tweaks for existing SQLite databases."""
        if not self.database_url.startswith('sqlite'):
            return

        with self.engine.connect() as conn:
            columns = [
                row[1]
                for row in conn.execute(text("PRAGMA table_info(tweet_data)"))
            ]
            new_columns = {
                'velocity_1h': 'FLOAT',
                'velocity_6h': 'FLOAT',
                'velocity_24h': 'FLOAT',
                'acceleration': 'FLOAT',
            }
            for col, col_type in new_columns.items():
                if col not in columns:
                    conn.execute(text(f"ALTER TABLE tweet_data ADD COLUMN {col} {col_type}"))
