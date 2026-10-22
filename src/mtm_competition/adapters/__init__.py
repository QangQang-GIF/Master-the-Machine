"""mtm_competition.adapters — Game Master API adapters (Mock and Official BTC)."""
from .base import GameMasterAdapter
from .mock_game_master import MockGameMasterAdapter
from .official_game_master import OfficialGameMasterAdapter

__all__ = ["GameMasterAdapter", "MockGameMasterAdapter", "OfficialGameMasterAdapter"]
