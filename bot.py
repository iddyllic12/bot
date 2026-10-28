import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# Required environment variables:
# DISCORD_TOKEN: your bot token
# GUILD_ID: your Discord server ID (recommended for fast slash-command updates)
# STAFF_ROLE_ID: role ID allowed to report/undo matches (optional if using Manage Server)
# DB_PATH: database file path (defaults to ./data/elo.sqlite3)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0") or "0")
DB_PATH = os.getenv("DB_PATH", "./data/elo.sqlite3")

MODES = {
    "1v1": 1,
    "2v2": 2,
    "3v3": 3,
}
STARTING_ELO = 1000
K_FACTOR = 32


def connect_db():
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_db():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                elo INTEGER NOT NULL DEFAULT 1000,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                matches INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, mode)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                winner TEXT NOT NULL,
                red_team TEXT NOT NULL,
                blue_team TEXT NOT NULL,
                elo_changes TEXT NOT NULL,
                reported_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0
            )
        """)





        conn.execute("""
            CREATE TABLE IF NOT EXISTS elo_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                amount INTEGER NOT NULL,
                old_elo INTEGER NOT NULL,
                new_elo INTEGER NOT NULL,
                reason TEXT NOT NULL,
                adjusted_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

         







def ensure_rating(conn, guild_id: int, user_id: int, mode: str):
    conn.execute(
        """INSERT OR IGNORE INTO ratings
           (guild_id, user_id, mode, elo, wins, losses, matches)
           VALUES (?, ?, ?, ?, 0, 0, 0)""",
        (guild_id, user_id, mode, STARTING_ELO),
    )


def get_rating(conn, guild_id: int, user_id: int, mode: str):
    ensure_rating(conn, guild_id, user_id, mode)
    return conn.execute(
        "SELECT * FROM ratings WHERE guild_id=? AND user_id=? AND mode=?",
        (guild_id, user_id, mode),
    ).fetchone()


def expected_score(team_elo: float, opponent_elo: float) -> float:
    return 1.0 / (1.0 + 10 ** ((opponent_elo - team_elo) / 400.0))


def elo_delta(team_elo: float, opponent_elo: float, won: bool) -> int:
    actual = 1.0 if won else 0.0
    return round(K_FACTOR * (actual - expected_score(team_elo, opponent_elo)))


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
        return True
    if STAFF_ROLE_ID and any(role.id == STAFF_ROLE_ID for role in interaction.user.roles):
        return True
    return False


async def staff_check(interaction: discord.Interaction) -> bool:
    if not is_staff(interaction):
        if interaction.response.is_done():
            await interaction.followup.send(
                "Only authorized tournament staff can use this command.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Only authorized tournament staff can use this command.",
                ephemeral=True,
            )
        return False
    return True


def user_mention(user_id: int) -> str:
    return f"<@{user_id}>"


class EloBot(commands.Bot):
    async def setup_hook(self):
        initialize_db()
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Synced slash commands to test/server guild {GUILD_ID}.")
        else:
            await self.tree.sync()
            print("Synced global slash commands. They can take longer to appear.")


intents = discord.Intents.default()
bot = EloBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.tree.command(name="elo", description="Check a player's ELO for a tournament mode.")
@app_commands.describe(player="Player to check (leave blank to check yourself)", mode="Tournament mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="1v1", value="1v1"),
    app_commands.Choice(name="2v2", value="2v2"),
    app_commands.Choice(name="3v3", value="3v3"),
])
async def elo_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    player: Optional[discord.Member] = None,
):
    if not interaction.guild:
        await interaction.response.send_message("Use this command in your server.", ephemeral=True)
        return

    target = player or interaction.user
    with connect_db() as conn:
        row = get_rating(conn, interaction.guild.id, target.id, mode.value)

    await interaction.response.send_message(
        f"꒰ა・**{mode.value} ELO**\n"
        f"Player: {target.mention}\n"
        f"Rating: **{row['elo']}**\n"
        f"Record: **{row['wins']}W - {row['losses']}L** "
        f"({row['matches']} matches)"
    )


@bot.tree.command(name="leaderboard", description="Show the top ELO players for a mode.")
@app_commands.describe(mode="Which tournament mode to view")
@app_commands.choices(mode=[
    app_commands.Choice(name="1v1", value="1v1"),
    app_commands.Choice(name="2v2", value="2v2"),
    app_commands.Choice(name="3v3", value="3v3"),
])
async def leaderboard_command(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if not interaction.guild:
        await interaction.response.send_message("Use this command in your server.", ephemeral=True)
        return

    with connect_db() as conn:
        rows = conn.execute(
            """SELECT user_id, elo, wins, losses, matches
               FROM ratings
               WHERE guild_id=? AND mode=? AND matches > 0
               ORDER BY elo DESC, wins DESC, matches ASC
               LIMIT 10""",
            (interaction.guild.id, mode.value),
        ).fetchall()

    if not rows:
        await interaction.response.send_message(
            f"꒰ა・**{mode.value} LEADERBOARD**\nNo rated matches have been recorded yet."
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows, start=1):
        marker = medals[i - 1] if i <= 3 else f"`#{i}`"
        lines.append(
            f"{marker} <@{row['user_id']}> — **{row['elo']} ELO** "
            f"({row['wins']}W-{row['losses']}L)"
        )

    await interaction.response.send_message(
        f"꒰ა・**ARSENAL ANGELS — {mode.value} LEADERBOARD**\n\n" + "\n".join(lines)
    )


@bot.tree.command(name="reportmatch", description="Staff: record a completed tournament match.")
@app_commands.describe(
    mode="Tournament mode",
    winner="Which team won",
    red1="Red team player 1",
    blue1="Blue team player 1",
    red2="Red team player 2 (required for 2v2/3v3)",
    blue2="Blue team player 2 (required for 2v2/3v3)",
    red3="Red team player 3 (required for 3v3)",
    blue3="Blue team player 3 (required for 3v3)",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="1v1", value="1v1"),
        app_commands.Choice(name="2v2", value="2v2"),
        app_commands.Choice(name="3v3", value="3v3"),
    ],
    winner=[
        app_commands.Choice(name="Red Team", value="red"),
        app_commands.Choice(name="Blue Team", value="blue"),
    ],
)
async def reportmatch_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    winner: app_commands.Choice[str],
    red1: discord.Member,
    blue1: discord.Member,
    red2: Optional[discord.Member] = None,
    blue2: Optional[discord.Member] = None,
    red3: Optional[discord.Member] = None,
    blue3: Optional[discord.Member] = None,
):
    if not await staff_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("Use this command in your server.", ephemeral=True)
        return

    required_per_team = MODES[mode.value]
    red_team = [red1, red2, red3][:required_per_team]
    blue_team = [blue1, blue2, blue3][:required_per_team]

    if any(member is None for member in red_team + blue_team):
        await interaction.response.send_message(
            f"For {mode.value}, select exactly {required_per_team} player(s) on each team.",
            ephemeral=True,
        )
        return

    # Drop unused optional slots for the chosen mode.
    red_team = [m for m in red_team if m is not None]
    blue_team = [m for m in blue_team if m is not None]
    all_ids = [m.id for m in red_team + blue_team]
    if len(set(all_ids)) != len(all_ids):
        await interaction.response.send_message(
            "Each player must be unique in the match. A player cannot be on both teams or occupy two slots.",
            ephemeral=True,
        )
        return

    if any(member.bot for member in red_team + blue_team):
        await interaction.response.send_message("Bots cannot be entered as tournament players.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    mode_value = mode.value
    red_ids = [m.id for m in red_team]
    blue_ids = [m.id for m in blue_team]

    with connect_db() as conn:
        # Ensure ratings exist before calculating team averages.
        red_rows = [get_rating(conn, guild_id, uid, mode_value) for uid in red_ids]
        blue_rows = [get_rating(conn, guild_id, uid, mode_value) for uid in blue_ids]
        red_avg = sum(row["elo"] for row in red_rows) / len(red_rows)
        blue_avg = sum(row["elo"] for row in blue_rows) / len(blue_rows)

        red_won = winner.value == "red"
        red_change = elo_delta(red_avg, blue_avg, red_won)
        blue_change = elo_delta(blue_avg, red_avg, not red_won)

        changes = {}
        for uid in red_ids:
            old = get_rating(conn, guild_id, uid, mode_value)
            new_elo = old["elo"] + red_change
            changes[str(uid)] = {
                "old_elo": old["elo"], "new_elo": new_elo,
                "old_wins": old["wins"], "old_losses": old["losses"],
                "old_matches": old["matches"],
                "delta": red_change,
            }
            conn.execute(
                """UPDATE ratings SET elo=?, wins=wins+?, losses=losses+?, matches=matches+1
                   WHERE guild_id=? AND user_id=? AND mode=?""",
                (new_elo, 1 if red_won else 0, 0 if red_won else 1, guild_id, uid, mode_value),
            )

        for uid in blue_ids:
            old = get_rating(conn, guild_id, uid, mode_value)
            new_elo = old["elo"] + blue_change
            changes[str(uid)] = {
                "old_elo": old["elo"], "new_elo": new_elo,
                "old_wins": old["wins"], "old_losses": old["losses"],
                "old_matches": old["matches"],
                "delta": blue_change,
            }
            conn.execute(
                """UPDATE ratings SET elo=?, wins=wins+?, losses=losses+?, matches=matches+1
                   WHERE guild_id=? AND user_id=? AND mode=?""",
                (new_elo, 0 if red_won else 1, 1 if red_won else 0, guild_id, uid, mode_value),
            )

        cursor = conn.execute(
            """INSERT INTO matches
               (guild_id, mode, winner, red_team, blue_team, elo_changes, reported_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                guild_id, mode_value, winner.value, json.dumps(red_ids), json.dumps(blue_ids),
                json.dumps(changes), interaction.user.id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        match_id = cursor.lastrowid

    def team_text(team, delta):
        names = ", ".join(m.mention for m in team)
        sign = "+" if delta >= 0 else ""
        return f"{names} ({sign}{delta} ELO each)"

    await interaction.response.send_message(
        f"꒰ა・**MATCH RECORDED — {mode_value}**\n"
        f"Match ID: `#{match_id}`\n"
        f"Winner: **{'Red Team' if red_won else 'Blue Team'}**\n\n"
        f"**Red:** {team_text(red_team, red_change)}\n"
        f"**Blue:** {team_text(blue_team, blue_change)}\n\n"
        f"Reported by {interaction.user.mention}."
    )


@bot.tree.command(name="matchhistory", description="Show the most recent recorded matches.")
@app_commands.describe(mode="Optional mode filter")
@app_commands.choices(mode=[
    app_commands.Choice(name="1v1", value="1v1"),
    app_commands.Choice(name="2v2", value="2v2"),
    app_commands.Choice(name="3v3", value="3v3"),
])
async def matchhistory_command(
    interaction: discord.Interaction,
    mode: Optional[app_commands.Choice[str]] = None,
):
    if not interaction.guild:
        await interaction.response.send_message("Use this command in your server.", ephemeral=True)
        return

    with connect_db() as conn:
        if mode:
            rows = conn.execute(
                """SELECT id, mode, winner, red_team, blue_team, created_at
                   FROM matches WHERE guild_id=? AND mode=? AND undone=0
                   ORDER BY id DESC LIMIT 5""",
                (interaction.guild.id, mode.value),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, mode, winner, red_team, blue_team, created_at
                   FROM matches WHERE guild_id=? AND undone=0
                   ORDER BY id DESC LIMIT 5""",
                (interaction.guild.id,),
            ).fetchall()

    if not rows:
        await interaction.response.send_message("No recorded matches found.")
        return

    lines = []
    for row in rows:
        red = ", ".join(user_mention(uid) for uid in json.loads(row["red_team"]))
        blue = ", ".join(user_mention(uid) for uid in json.loads(row["blue_team"]))
        winner_text = "Red" if row["winner"] == "red" else "Blue"
        lines.append(f"`#{row['id']}` **{row['mode']}** — {winner_text} won\nRed: {red}\nBlue: {blue}")

    await interaction.response.send_message("꒰ა・**RECENT MATCHES**\n\n" + "\n\n".join(lines))


@bot.tree.command(name="undomatch", description="Staff: undo a match and restore ratings.")
@app_commands.describe(match_id="Match ID shown when the match was reported")
async def undomatch_command(interaction: discord.Interaction, match_id: int):
    if not await staff_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("Use this command in your server.", ephemeral=True)
        return

    with connect_db() as conn:
        row = conn.execute(
            """SELECT * FROM matches
               WHERE id=? AND guild_id=? AND undone=0""",
            (match_id, interaction.guild.id),
        ).fetchone()

        if not row:
            await interaction.response.send_message(
                "That match was not found, or it has already been undone.",
                ephemeral=True,
            )
            return

        changes = json.loads(row["elo_changes"])
        for uid_text, data in changes.items():
            uid = int(uid_text)
            # Restore rating state from immediately before this match.
            conn.execute(
                """UPDATE ratings SET elo=?, wins=?, losses=?, matches=?
                   WHERE guild_id=? AND user_id=? AND mode=?""",
                (
                    data["old_elo"], data["old_wins"], data["old_losses"], data["old_matches"],
                    row["guild_id"], uid, row["mode"],
                ),
            )
        conn.execute("UPDATE matches SET undone=1 WHERE id=?", (match_id,))

    await interaction.response.send_message(
        f"꒰ა・Match `#{match_id}` has been undone and its ELO changes were reverted."
    )


ELO_MODE_CHOICES = [
    app_commands.Choice(name="1v1", value="1v1"),
    app_commands.Choice(name="2v2", value="2v2"),
    app_commands.Choice(name="3v3", value="3v3"),
]


async def adjust_elo(
    interaction: discord.Interaction,
    player: discord.Member,
    mode: app_commands.Choice[str],
    amount: app_commands.Range[int, 1, 10000],
    reason: str,
    direction: int,
):
    if not await staff_check(interaction):
        return

    if not interaction.guild:
        await interaction.response.send_message(
            "Use this command in your server.", ephemeral=True
        )
        return

    reason = reason.strip()
    if not reason or len(reason) > 300:
        await interaction.response.send_message(
            "Enter a reason between 1 and 300 characters.",
            ephemeral=True,
        )
        return

    if player.bot:
        await interaction.response.send_message(
            "You cannot adjust ELO for a bot account.", ephemeral=True
        )
        return

    with connect_db() as conn:
        row = get_rating(
            conn, interaction.guild.id, player.id, mode.value
        )
        old_elo = row["elo"]
        new_elo = max(0, old_elo + direction * amount)
        actual_change = new_elo - old_elo

        conn.execute(
            """UPDATE ratings SET elo=?
               WHERE guild_id=? AND user_id=? AND mode=?""",
            (new_elo, interaction.guild.id, player.id, mode.value),
        )

        conn.execute(
            """INSERT INTO elo_adjustments
               (guild_id, user_id, mode, amount, old_elo, new_elo,
                reason, adjusted_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                interaction.guild.id,
                player.id,
                mode.value,
                actual_change,
                old_elo,
                new_elo,
                reason,
                interaction.user.id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    
    sign = "+" if actual_change >= 0 else ""
    await interaction.response.send_message(
        f"꒰ა・ELO ADJUSTED — {mode.value}\n"
        f"Player: {player.mention}\n"
        f"Previous ELO: {old_elo}\n"
        f"Change: {sign}{actual_change}\n"
        f"New ELO: {new_elo}\n"
        f"Reason: {reason}\n"
        f"Adjusted by: {interaction.user.mention}"
    )





@bot.tree.command(name="addelo", description="Staff: add ELO to a player.")
@app_commands.describe(
    player="Player receiving ELO",
    mode="Tournament mode",
    amount="Amount of ELO to add",
    reason="Reason for the adjustment",
)
@app_commands.choices(mode=ELO_MODE_CHOICES)
async def addelo_command(
    interaction: discord.Interaction,
    player: discord.Member,
    mode: app_commands.Choice[str],
    amount: app_commands.Range[int, 1, 10000],
    reason: str,
):
    await adjust_elo(interaction, player, mode, amount, reason, 1)


@bot.tree.command(name="removeelo", description="Staff: remove ELO from a player.")
@app_commands.describe(
    player="Player losing ELO",
    mode="Tournament mode",
    amount="Amount of ELO to remove",
    reason="Reason for the adjustment",
)
@app_commands.choices(mode=ELO_MODE_CHOICES)
async def removeelo_command(
    interaction: discord.Interaction,
    player: discord.Member,
    mode: app_commands.Choice[str],
    amount: app_commands.Range[int, 1, 10000],
    reason: str,
):
    await adjust_elo(interaction, player, mode, amount, reason, -1)

@bot.tree.command(name="elohelp", description="Show how the Arsenal Angels ELO bot works.")
async def elohelp_command(interaction: discord.Interaction):
    await interaction.response.send_message(
       "꒰ა・**ARSENAL ANGELS ELO COMMANDS**\n"
        "• `/elo` — check your rating or another player's rating\n"
        "• `/leaderboard` — view the top 10 for a mode\n"
        "• `/matchhistory` — view recent recorded matches\n"
        "• `/reportmatch` — staff-only match reporting\n"
        "• `/undomatch` — staff-only correction for a reported match\n"
        "• `/addelo` — staff-only manual ELO increase\n"
        "• `/removeelo` — staff-only manual ELO decrease\n\n"
        "**Modes:** 1v1, 2v2, and 3v3 each have separate ratings.\n"
        f"**Starting ELO:** {STARTING_ELO} | **K-factor:** {K_FACTOR}\n"
        "Team matches use each team's average ELO. Match reports should be checked before submission."
    )


if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable. Add it to your hosting provider's secrets/environment settings.")

bot.run(TOKEN)
