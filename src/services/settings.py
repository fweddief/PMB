"""
Helpers for persisting runtime bot settings (auto-trading toggle, etc.).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from database import DatabaseManager, BotSetting


class BotSettingsService:
    """Simple wrapper for reading/updating persistent bot settings."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def is_auto_trading_enabled(self) -> bool:
        with self.db.get_session() as session:
            setting = session.get(BotSetting, 1)
            if not setting:
                setting = BotSetting(id=1, auto_trading_enabled=True)
                session.add(setting)
                session.commit()
                return True
            return bool(setting.auto_trading_enabled)

    def set_auto_trading(self, enabled: bool) -> Dict[str, bool]:
        with self.db.get_session() as session:
            setting = session.get(BotSetting, 1)
            if not setting:
                setting = BotSetting(id=1)
                session.add(setting)
            setting.auto_trading_enabled = bool(enabled)
            setting.updated_at = datetime.utcnow()
            session.commit()
            return {"auto_trading_enabled": setting.auto_trading_enabled}

    def get_settings(self) -> Dict[str, bool]:
        return {"auto_trading_enabled": self.is_auto_trading_enabled()}
