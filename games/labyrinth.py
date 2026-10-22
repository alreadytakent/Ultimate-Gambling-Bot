# ============================================================
#  games/labyrinth.py — Labyrinth
#
#  1v1, draws possible.
#
#  Setup phase (DMs):
#    1. Bot sends empty maze image, player chooses start + end: "B2, F5"
#       Bot sends maze image with start/end marked.
#    2. Player places walls incrementally across multiple messages.
#       Each message adds N walls, bot draws them and shows current
#       wall list and asks for remaining.
#       "reset" at any point wipes everything back to step 1.
#       Once 20 walls total are placed bot asks for confirmation.
#
#  Play phase (channel):
#    - Each player navigates their OPPONENT's maze
#    - Players take turns; first mover decided randomly
#    - Commands: U / D / L / R  (up / down / left / right)
#    - A turn ends when the player hits a wall or outer boundary
#    - Player keeps moving freely until their turn ends
#    - Win/draw logic:
#        * Any player escapes → check if they were first to move
#        * If they were NOT the first mover → immediate win
#        * If they WERE the first mover → second player gets exactly
#          1 more turn; if they escape in that turn it's a draw,
#          otherwise the first mover wins
#
#  Image rendering uses Pillow (PIL).
#  Images are sent as discord.File from in-memory BytesIO buffers.
# ============================================================

from __future__ import annotations

import io
import random
from collections import deque
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont

from games.base_game import BaseGame, GameResult

# ── Grid constants ────────────────────────────────────────────
COLS = 6
ROWS = 6
ROW_LABELS = "ABCDEF"

# ── Image constants ───────────────────────────────────────────
CELL   = 64
MARGIN = 40
GAP    = 48

WALL_W = 5
GRID_W = 1

# Colours
C_BG          = (30,  30,  40)
C_GRID        = (80,  80, 100)
C_WALL        = (220, 220, 230)
C_HIDDEN_WALL = (60,  60,  70)
C_START       = (60,  180,  80)
C_END         = (200,  60,  60)
C_PLAYER      = (255, 220,  50)
C_PATH        = (255, 200,  80)
C_LABEL       = (180, 180, 200)
C_BORDER      = (100, 100, 120)


# ════════════════════════════════════════════════════════════
#  COORDINATE HELPERS
# ════════════════════════════════════════════════════════════

def parse_square(s: str) -> Optional[tuple[int, int]]:
    """Parse "B3" → (row=1, col=2). Returns None on error."""
    s = s.strip().upper()
    if len(s) < 2:
        return None
    row_ch = s[0]
    try:
        col = int(s[1:]) - 1
    except ValueError:
        return None
    row = ROW_LABELS.find(row_ch)
    if row == -1 or not (0 <= col < COLS):
        return None
    return (row, col)


def sq_str(row: int, col: int) -> str:
    return f"{ROW_LABELS[row]}{col + 1}"


def parse_wall(s: str) -> Optional[frozenset]:
    """Parse "A2-B2" into a frozenset of two adjacent squares."""
    s = s.strip().upper()
    parts = s.split("-")
    if len(parts) != 2:
        return None
    a = parse_square(parts[0])
    b = parse_square(parts[1])
    if a is None or b is None or a == b:
        return None
    if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
        return None
    return frozenset({a, b})


def fmt_walls(walls: set) -> str:
    """Format a set of frozenset walls as 'A1-A2, B3-C3, ...'"""
    parts = []
    for wall in sorted(walls, key=lambda w: sorted(w)):
        a, b = sorted(wall)
        parts.append(f"{sq_str(*a)}-{sq_str(*b)}")
    return ", ".join(parts)


def neighbors(row: int, col: int) -> list[tuple[int, int]]:
    result = []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = row + dr, col + dc
        if 0 <= nr < ROWS and 0 <= nc < COLS:
            result.append((nr, nc))
    return result


def is_solvable(start: tuple, end: tuple, walls: set) -> bool:
    visited = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == end:
            return True
        for nb in neighbors(*cur):
            if frozenset({cur, nb}) not in walls and nb not in visited:
                visited.add(nb)
                q.append(nb)
    return False


# ════════════════════════════════════════════════════════════
#  IMAGE RENDERING
# ════════════════════════════════════════════════════════════

def _load_font(size: int = 14, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{'-Bold' if bold else '-Regular'}.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _grid_size() -> tuple[int, int]:
    w = MARGIN + COLS * CELL + MARGIN
    h = MARGIN + ROWS * CELL + MARGIN
    return w, h


def _cell_center(row: int, col: int) -> tuple[int, int]:
    return (MARGIN + col * CELL + CELL // 2,
            MARGIN + row * CELL + CELL // 2)


def _cell_topleft(row: int, col: int) -> tuple[int, int]:
    return (MARGIN + col * CELL, MARGIN + row * CELL)


def _draw_wall_segment(draw: ImageDraw.Draw, wall: frozenset,
                       colour: tuple, width: int) -> None:
    a, b = sorted(wall)
    (r1, c1), (r2, c2) = a, b
    if r1 == r2:
        c_right = max(c1, c2)
        x  = MARGIN + c_right * CELL
        y1 = MARGIN + r1 * CELL + 2
        y2 = MARGIN + r1 * CELL + CELL - 2
        draw.line([(x, y1), (x, y2)], fill=colour, width=width)
    else:
        r_bot = max(r1, r2)
        y  = MARGIN + r_bot * CELL
        x1 = MARGIN + c1 * CELL + 2
        x2 = MARGIN + c1 * CELL + CELL - 2
        draw.line([(x1, y), (x2, y)], fill=colour, width=width)


def _draw_maze(
    *,
    walls_all: set,
    walls_revealed: set,
    start: Optional[tuple],
    end: Optional[tuple],
    player_pos: Optional[tuple],
    path: Optional[list],
    full_reveal: bool = False,
) -> Image.Image:
    w, h = _grid_size()
    img  = Image.new("RGB", (w, h), C_BG)
    draw = ImageDraw.Draw(img)
    font = _load_font(14)

    draw.rectangle(
        [MARGIN, MARGIN, MARGIN + COLS * CELL, MARGIN + ROWS * CELL],
        outline=C_BORDER, width=2,
    )

    for r in range(ROWS + 1):
        y = MARGIN + r * CELL
        draw.line([(MARGIN, y), (MARGIN + COLS * CELL, y)], fill=C_GRID, width=GRID_W)
    for c in range(COLS + 1):
        x = MARGIN + c * CELL
        draw.line([(x, MARGIN), (x, MARGIN + ROWS * CELL)], fill=C_GRID, width=GRID_W)

    for c in range(COLS):
        x = MARGIN + c * CELL + CELL // 2 - 5
        draw.text((x, MARGIN - 22), str(c + 1), fill=C_LABEL, font=font)
    for r in range(ROWS):
        y = MARGIN + r * CELL + CELL // 2 - 8
        draw.text((MARGIN - 22, y), ROW_LABELS[r], fill=C_LABEL, font=font)

    if start is not None:
        tx, ty = _cell_topleft(*start)
        draw.rectangle([tx + 4, ty + 4, tx + CELL - 4, ty + CELL - 4], fill=C_START)
    if end is not None:
        tx, ty = _cell_topleft(*end)
        draw.rectangle([tx + 4, ty + 4, tx + CELL - 4, ty + CELL - 4], fill=C_END)

    if path and len(path) >= 2:
        pts = [_cell_center(*sq) for sq in path]
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=C_PATH, width=3)

    if full_reveal:
        for wall in walls_all - walls_revealed:
            _draw_wall_segment(draw, wall, C_HIDDEN_WALL, width=2)

    for wall in walls_revealed:
        _draw_wall_segment(draw, wall, C_WALL, width=WALL_W)

    if player_pos is not None:
        cx, cy = _cell_center(*player_pos)
        r = CELL // 4
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=C_PLAYER, outline=(200, 160, 20), width=2)

    return img


def _maze_to_file(img: Image.Image, filename: str = "maze.png") -> discord.File:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=filename)


def _empty_maze_file() -> discord.File:
    img = _draw_maze(
        walls_all=set(), walls_revealed=set(),
        start=None, end=None, player_pos=None, path=None,
    )
    return _maze_to_file(img, "maze_empty.png")


def _squares_preview_file(start: tuple, end: tuple) -> discord.File:
    img = _draw_maze(
        walls_all=set(), walls_revealed=set(),
        start=start, end=end, player_pos=None, path=None,
    )
    return _maze_to_file(img, "maze_squares.png")


def _builder_preview_file(walls: set, start: tuple, end: tuple) -> discord.File:
    img = _draw_maze(
        walls_all=walls, walls_revealed=walls,
        start=start, end=end, player_pos=None, path=None,
    )
    return _maze_to_file(img, "maze_preview.png")


def _combined_image(
    *,
    maze1_walls_all: set,
    maze1_walls_rev: set,
    maze1_start: tuple,
    maze1_end: tuple,
    maze1_player: Optional[tuple],
    maze1_path: Optional[list],
    maze1_label: str,
    maze2_walls_all: set,
    maze2_walls_rev: set,
    maze2_start: tuple,
    maze2_end: tuple,
    maze2_player: Optional[tuple],
    maze2_path: Optional[list],
    maze2_label: str,
    full_reveal: bool = False,
) -> discord.File:
    single_w, single_h = _grid_size()
    label_h = 24
    total_w = single_w * 2 + GAP
    total_h = single_h + label_h

    combo = Image.new("RGB", (total_w, total_h), C_BG)
    font  = _load_font(15, bold=True)

    m1 = _draw_maze(walls_all=maze1_walls_all, walls_revealed=maze1_walls_rev,
                    start=maze1_start, end=maze1_end,
                    player_pos=maze1_player, path=maze1_path,
                    full_reveal=full_reveal)
    m2 = _draw_maze(walls_all=maze2_walls_all, walls_revealed=maze2_walls_rev,
                    start=maze2_start, end=maze2_end,
                    player_pos=maze2_player, path=maze2_path,
                    full_reveal=full_reveal)

    combo.paste(m1, (0, label_h))
    combo.paste(m2, (single_w + GAP, label_h))

    d = ImageDraw.Draw(combo)
    d.text((single_w // 2 - 60, 4),                  maze1_label, fill=C_LABEL, font=font)
    d.text((single_w + GAP + single_w // 2 - 60, 4), maze2_label, fill=C_LABEL, font=font)

    return _maze_to_file(combo, "labyrinth.png")


# ════════════════════════════════════════════════════════════
#  GAME CLASS
# ════════════════════════════════════════════════════════════

class LabyrinthGame(BaseGame):
    game_name = "Labyrinth"
    can_draw   = True
    is_ffa     = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]

        # Maze data keyed by builder's user_id
        # Keys: "start", "end", "walls" (set[frozenset])
        self._mazes: dict[int, dict] = {p1.id: {}, p2.id: {}}

        # Navigator data keyed by navigator's user_id (filled at play start)
        # Keys: "pos" (tuple), "revealed" (set[frozenset]), "path" (list[tuple])
        self._nav: dict[int, dict] = {}

        # Setup sub-phase per player: "squares" | "walls" | "confirm"
        self._setup_phase: dict[int, str] = {p1.id: "squares", p2.id: "squares"}
        self._setup_done:  dict[int, bool] = {p1.id: False,    p2.id: False}

        # Play state
        self._turn:        Optional[int] = None   # user_id whose turn it is
        self._first_mover: Optional[int] = None   # user_id who moved first
        # When the first mover escapes, this is set to True.
        # The second player then gets exactly one more turn.
        self._final_turn:  bool = False

    # ── Helpers ───────────────────────────────────────────────

    def _opponent(self, user_id: int) -> discord.Member:
        return self.players[1] if self.players[0].id == user_id else self.players[0]

    def _wall_text(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        w1 = self._mazes[p1.id].get("walls", set())
        w2 = self._mazes[p2.id].get("walls", set())
        return (
            f"**{p1.display_name}'s maze walls:**\n"
            f"`{fmt_walls(w1)}`\n\n"
            f"**{p2.display_name}'s maze walls:**\n"
            f"`{fmt_walls(w2)}`"
        )

    # ── Start ─────────────────────────────────────────────────

    async def start(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""
        await self.send(
            f"🌀 **Labyrinth** started between {p1.mention} and {p2.mention}!{bet_str}\n"
            f"Both players: check your **DMs** to build your maze!"
        )
        for player in self.players:
            await self._ask_squares(player)

    async def _ask_squares(self, player: discord.Member) -> None:
        await player.send(
            "🌀 **Labyrinth — Maze Setup**\n\n"
            "The maze is a **6×6 grid**. Columns are numbered **1–6**, "
            "rows are labeled **A–F** (A1 = top-left, F6 = bottom-right).\n\n"
            "Here's your empty grid:\n"
            "🟢 = Start  |  🔴 = End\n\n"
            "Choose your **starting** and **ending** squares.\n"
            "Example: `B2, F5`",
            file=_empty_maze_file(),
        )

    # ── Message routing ───────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        content: str,
        is_dm: bool,
        message: Optional[discord.Message] = None,
    ) -> Optional[GameResult]:
        content = content.strip()

        if is_dm:
            if not self._setup_done.get(user_id, True):
                return await self._handle_setup(user_id, content, message)
            return None

        if self._turn is not None:
            if content.upper() in ("U", "D", "L", "R"):
                return await self._handle_move(user_id, content.upper(), message)

        return None

    # ════════════════════════════════════════════════════════
    #  SETUP PHASE
    # ════════════════════════════════════════════════════════

    async def _handle_setup(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)
        phase  = self._setup_phase[user_id]

        if content.lower() == "reset":
            self._mazes[user_id]       = {}
            self._setup_phase[user_id] = "squares"
            await member.send(
                "🔄 **Reset!** Let's start from scratch.\n"
                "Here's your empty grid again:",
                file=_empty_maze_file(),
            )
            await member.send(
                "Choose your **starting** and **ending** squares.\n"
                "Example: `B2, F5`"
            )
            return None

        if phase == "squares":
            return await self._process_squares(member, content, message)
        if phase == "walls":
            return await self._process_walls(member, content, message)
        if phase == "confirm":
            return await self._process_confirm(member, content, message)
        return None

    # ── Step 1: choose start/end ──────────────────────────────

    async def _process_squares(
        self, member: discord.Member, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        parts = [p.strip() for p in content.split(",")]
        if len(parts) != 2:
            await member.send(
                "❌ Provide exactly two squares separated by a comma. Example: `B2, F5`"
            )
            return None

        start = parse_square(parts[0])
        end   = parse_square(parts[1])

        if start is None:
            await member.send(
                f"❌ `{parts[0]}` isn't a valid square (letter A–F + number 1–6)."
            )
            return None
        if end is None:
            await member.send(
                f"❌ `{parts[1]}` isn't a valid square (letter A–F + number 1–6)."
            )
            return None
        if start == end:
            await member.send("❌ Starting and ending squares must be different!")
            return None

        self._mazes[member.id]["start"] = start
        self._mazes[member.id]["end"]   = end
        self._mazes[member.id]["walls"] = set()

        if message:
            try: await message.add_reaction("✅")
            except Exception: pass

        self._setup_phase[member.id] = "walls"
        await member.send(
            f"✅ **Start: {sq_str(*start)}** (🟢)  |  **End: {sq_str(*end)}** (🔴)\n\n"
            "Now place **20 walls**. Send any number of walls at a time — "
            "I'll draw them as you go.\n"
            "Format: `A2-B2, B2-B3, C1-C2, ...`\n\n"
            "• A wall sits between two adjacent squares\n"
            "• The maze must stay solvable after each batch\n"
            "• Type `reset` at any time to start over",
            file=_squares_preview_file(start, end),
        )
        return None

    # ── Step 2: incremental wall placement ───────────────────

    async def _process_walls(
        self, member: discord.Member, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        maze           = self._mazes[member.id]
        start          = maze["start"]
        end            = maze["end"]
        existing_walls = maze["walls"]

        raw_list = [w.strip() for w in content.split(",") if w.strip()]
        if not raw_list:
            await member.send("❌ No walls found. Format: `A2-B2, B2-B3, ...`")
            return None

        parsed_new: set = set()
        errors: list[str] = []
        for raw in raw_list:
            w = parse_wall(raw)
            if w is None:
                errors.append(f"`{raw}`")
            else:
                parsed_new.add(w)

        if errors:
            await member.send(
                f"❌ Could not parse: {', '.join(errors[:5])}{'…' if len(errors) > 5 else ''}\n"
                "Format: `A2-B2` — two adjacent squares joined by a dash."
            )
            return None

        combined  = existing_walls | parsed_new
        new_count = len(combined) - len(existing_walls)

        if new_count == 0:
            await member.send(
                f"⚠️ All those walls were already placed "
                f"({len(existing_walls)}/20 so far). "
                "Send new walls to continue."
            )
            return None

        if len(combined) > 20:
            over = len(combined) - 20
            await member.send(
                f"❌ That would give you **{len(combined)} walls** — "
                f"{over} too many. You need exactly **20** total "
                f"(currently have {len(existing_walls)})."
            )
            return None

        if not is_solvable(start, end, combined):
            await member.send(
                "❌ Adding those walls makes the maze **inescapable**!\n"
                "Try different walls. Current walls are unchanged."
            )
            return None

        maze["walls"] = combined
        remaining     = 20 - len(combined)

        if message:
            try: await message.add_reaction("✅")
            except Exception: pass

        if remaining > 0:
            await member.send(
                f"✅ Added **{new_count}** wall(s). "
                f"**{len(combined)}/20** placed — **{remaining}** more to go.\n"
                f"Walls so far: `{fmt_walls(combined)}`\n\n"
                "Send more walls, or type `reset` to start over:",
                file=_builder_preview_file(combined, start, end),
            )
        else:
            self._setup_phase[member.id] = "confirm"
            await member.send(
                "✅ All **20 walls** placed and the maze is solvable!\n"
                f"Walls: `{fmt_walls(combined)}`\n\n"
                "🟢 = Start  |  🔴 = End\n\n"
                "Type **`yes`** to confirm this maze, or **`no`** / `reset` to start over:",
                file=_builder_preview_file(combined, start, end),
            )
        return None

    # ── Step 3: confirmation ──────────────────────────────────

    async def _process_confirm(
        self, member: discord.Member, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        lower = content.lower().strip()
        if lower not in ("yes", "no"):
            await member.send(
                "Please type **`yes`** to confirm or **`no`** / `reset` to start over."
            )
            return None

        if lower == "no":
            self._mazes[member.id]       = {}
            self._setup_phase[member.id] = "squares"
            await member.send(
                "🔄 Discarded! Let's start over.\n"
                "Here's your empty grid:",
                file=_empty_maze_file(),
            )
            await member.send(
                "Choose your **starting** and **ending** squares. Example: `B2, F5`"
            )
            return None

        self._setup_done[member.id] = True
        await member.send("✅ Maze confirmed! Waiting for your opponent to finish…")

        if all(self._setup_done.values()):
            await self._begin_play()
        return None

    # ════════════════════════════════════════════════════════
    #  PLAY PHASE
    # ════════════════════════════════════════════════════════

    async def _begin_play(self) -> None:
        p1, p2 = self.players[0], self.players[1]

        self._nav[p1.id] = {
            "pos":      self._mazes[p2.id]["start"],
            "revealed": set(),
            "path":     [self._mazes[p2.id]["start"]],
        }
        self._nav[p2.id] = {
            "pos":      self._mazes[p1.id]["start"],
            "revealed": set(),
            "path":     [self._mazes[p1.id]["start"]],
        }

        self._turn        = random.choice([p1.id, p2.id])
        self._first_mover = self._turn
        self._final_turn  = False

        first = self.get_member(self._turn)
        await self.send(
            "🌀 **Both mazes are ready! The game begins!**\n"
            f"{first.mention} goes first!\n\n"
            "Move with **`U`** (up) · **`D`** (down) · **`L`** (left) · **`R`** (right)\n"
            "You keep moving until you **hit a wall** — then your opponent takes over.\n"
            "If the first mover escapes, the second player gets **1 final turn** to draw!\n"
            "🟢 = your start  |  🔴 = your goal  |  🟡 = you  |  ── = your path"
        )
        await self.channel.send(file=self._render_combined())

    async def _handle_move(
        self, user_id: int, direction: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        if user_id != self._turn:
            return None

        member   = self.get_member(user_id)
        opponent = self._opponent(user_id)
        maze     = self._mazes[opponent.id]
        nav      = self._nav[user_id]
        pos      = nav["pos"]

        dr, dc = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}[direction]
        new_row = pos[0] + dr
        new_col = pos[1] + dc

        # Out of bounds — turn ends
        if not (0 <= new_row < ROWS and 0 <= new_col < COLS):
            self._turn = opponent.id
            # If this was the second player's final turn and they didn't escape
            if self._final_turn:
                return await self._first_mover_wins(member)
            await self.send(
                f"🚧 {member.mention} hits the **outer boundary**! "
                f"Turn passes to {opponent.mention}."
            )
            await self.channel.send(file=self._render_combined())
            return None

        new_pos = (new_row, new_col)
        edge    = frozenset({pos, new_pos})

        # Wall hit — turn ends
        if edge in maze["walls"]:
            nav["revealed"].add(edge)
            self._turn = opponent.id
            # If this was the second player's final turn and they didn't escape
            if self._final_turn:
                return await self._first_mover_wins(member)
            await self.send(
                f"🧱 {member.mention} hits a **wall**! "
                f"It's revealed on the map. {opponent.mention}'s turn."
            )
            await self.channel.send(file=self._render_combined())
            return None

        # Successful move
        nav["pos"] = new_pos
        nav["path"].append(new_pos)

        if message:
            try: await message.add_reaction("✅")
            except Exception: pass

        # Escape check
        if new_pos == maze["end"]:
            # Second player escapes → immediate win (regardless of final_turn flag,
            # if they escape during their final turn it's a draw)
            if user_id != self._first_mover:
                if self._final_turn:
                    # Second player escaped on their final turn → draw
                    await self.send(
                        f"🤝 **{member.mention} also escapes! It's a DRAW!** 🤝\n\n"
                        + self._wall_text()
                    )
                    await self.channel.send(file=self._render_full_reveal())
                    return GameResult(winner_id=None, loser_id=None, is_draw=True)
                else:
                    # Second player escaped before the first mover → immediate win
                    await self.send(
                        f"🎉 **{member.mention} escapes the maze and WINS!** 🎉\n"
                        f"{opponent.mention} went first but {member.mention} got out first!\n\n"
                        + self._wall_text()
                    )
                    await self.channel.send(file=self._render_full_reveal())
                    return GameResult(winner_id=member.id, loser_id=opponent.id, is_draw=False)
            else:
                # First mover escapes → give second player one final turn
                self._final_turn = True
                self._turn = opponent.id
                await self.send(
                    f"🏁 **{member.mention} escaped the maze!**\n"
                    f"{opponent.mention} gets **1 final turn** to escape and draw!"
                )
                await self.channel.send(file=self._render_combined())
                return None

        # Normal move — same player keeps going
        await self.channel.send(
            content=f"{member.mention} moved to **{sq_str(*new_pos)}**! Keep going (U/D/L/R).",
            file=self._render_combined(),
        )
        return None

    async def _first_mover_wins(self, second_player: discord.Member) -> GameResult:
        """Called when the second player's final turn ends without escaping."""
        winner = self.get_member(self._first_mover)
        await self.send(
            f"⌛ {second_player.mention} couldn't escape in time. "
            f"**{winner.mention} wins!** 🎉\n\n"
            + self._wall_text()
        )
        await self.channel.send(file=self._render_full_reveal())
        return GameResult(winner_id=self._first_mover, loser_id=second_player.id, is_draw=False)

    async def on_timeout(self, timed_out_user_id: int) -> Optional[GameResult]:
        """If the second player times out on their final turn, first mover wins."""
        if self._final_turn and timed_out_user_id != self._first_mover:
            second_player = self.get_member(timed_out_user_id)
            return await self._first_mover_wins(second_player)
        return await super().on_timeout(timed_out_user_id)

    # ════════════════════════════════════════════════════════
    #  RENDERING
    # ════════════════════════════════════════════════════════

    def _render_combined(self, full_reveal: bool = False) -> discord.File:
        p1, p2 = self.players[0], self.players[1]
        m1 = self._mazes[p1.id]
        m2 = self._mazes[p2.id]

        return _combined_image(
            maze1_walls_all=m1.get("walls", set()),
            maze1_walls_rev=self._nav.get(p2.id, {}).get("revealed", set()),
            maze1_start=m1.get("start", (0, 0)),
            maze1_end=m1.get("end", (5, 5)),
            maze1_player=self._nav.get(p2.id, {}).get("pos"),
            maze1_path=self._nav.get(p2.id, {}).get("path"),
            maze1_label=f"{p2.display_name} in {p1.display_name}'s maze",
            maze2_walls_all=m2.get("walls", set()),
            maze2_walls_rev=self._nav.get(p1.id, {}).get("revealed", set()),
            maze2_start=m2.get("start", (0, 0)),
            maze2_end=m2.get("end", (5, 5)),
            maze2_player=self._nav.get(p1.id, {}).get("pos"),
            maze2_path=self._nav.get(p1.id, {}).get("path"),
            maze2_label=f"{p1.display_name} in {p2.display_name}'s maze",
            full_reveal=full_reveal,
        )

    def _render_full_reveal(self) -> discord.File:
        """End-of-game image: both mazes with ALL walls shown solid."""
        p1, p2 = self.players[0], self.players[1]
        m1 = self._mazes[p1.id]
        m2 = self._mazes[p2.id]

        return _combined_image(
            maze1_walls_all=m1.get("walls", set()),
            maze1_walls_rev=m1.get("walls", set()),
            maze1_start=m1.get("start", (0, 0)),
            maze1_end=m1.get("end", (5, 5)),
            maze1_player=self._nav.get(p2.id, {}).get("pos"),
            maze1_path=self._nav.get(p2.id, {}).get("path"),
            maze1_label=f"{p2.display_name} in {p1.display_name}'s maze",
            maze2_walls_all=m2.get("walls", set()),
            maze2_walls_rev=m2.get("walls", set()),
            maze2_start=m2.get("start", (0, 0)),
            maze2_end=m2.get("end", (5, 5)),
            maze2_player=self._nav.get(p1.id, {}).get("pos"),
            maze2_path=self._nav.get(p1.id, {}).get("path"),
            maze2_label=f"{p1.display_name} in {p2.display_name}'s maze",
            full_reveal=False,
        )

    # ════════════════════════════════════════════════════════
    #  STATE PERSISTENCE
    # ════════════════════════════════════════════════════════

    def get_state(self) -> dict:
        def ser_walls(s: set) -> list:
            return [sorted([list(sq) for sq in w]) for w in s]

        def ser_maze(m: dict) -> dict:
            return {
                "start": list(m["start"]) if m.get("start") is not None else None,
                "end":   list(m["end"])   if m.get("end")   is not None else None,
                "walls": ser_walls(m.get("walls", set())),
            }

        def ser_nav(n: dict) -> dict:
            return {
                "pos":      list(n["pos"]),
                "revealed": ser_walls(n["revealed"]),
                "path":     [list(sq) for sq in n["path"]],
            }

        return {
            "mazes":        {str(k): ser_maze(v) for k, v in self._mazes.items()},
            "nav":          {str(k): ser_nav(v)  for k, v in self._nav.items()},
            "setup_phase":  {str(k): v for k, v in self._setup_phase.items()},
            "setup_done":   {str(k): v for k, v in self._setup_done.items()},
            "turn":         self._turn,
            "first_mover":  self._first_mover,
            "final_turn":   self._final_turn,
        }

    def load_state(self, state: dict) -> None:
        def de_walls(lst: list) -> set:
            return {frozenset(tuple(sq) for sq in w) for w in lst}

        def de_maze(d: dict) -> dict:
            return {
                "start": tuple(d["start"]) if d.get("start") else None,
                "end":   tuple(d["end"])   if d.get("end")   else None,
                "walls": de_walls(d.get("walls", [])),
            }

        def de_nav(d: dict) -> dict:
            return {
                "pos":      tuple(d["pos"]),
                "revealed": de_walls(d.get("revealed", [])),
                "path":     [tuple(sq) for sq in d.get("path", [])],
            }

        self._mazes       = {int(k): de_maze(v) for k, v in state.get("mazes", {}).items()}
        self._nav         = {int(k): de_nav(v)  for k, v in state.get("nav",   {}).items()}
        self._setup_phase = {int(k): v for k, v in state.get("setup_phase", {}).items()}
        self._setup_done  = {int(k): v for k, v in state.get("setup_done",  {}).items()}
        self._turn        = state.get("turn")
        self._first_mover = state.get("first_mover")
        self._final_turn  = state.get("final_turn", False)