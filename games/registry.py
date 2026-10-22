# ============================================================
#  games/registry.py — maps canonical game name → game class
# ============================================================

from games.airpoker import AirPokerGame
from games.combination import CombinationGame
from games.contradiction import ContradictionGame
from games.dotty import DottyGame
from games.dth import DTHGame
from games.gops import GOPSGame
from games.knucklebones import KnuckleBonesGame
from games.kod import KODGame
from games.labyrinth import LabyrinthGame
from games.mdth import MDTHGame

GAME_CLASSES = {
    cls.game_name: cls
    for cls in (
        DTHGame, MDTHGame, GOPSGame, DottyGame, CombinationGame,
        AirPokerGame, ContradictionGame, KnuckleBonesGame, LabyrinthGame, KODGame,
    )
}