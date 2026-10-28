# Arsenal Angels ELO Discord Bot

A Python Discord bot with separate persistent ELO ratings for 1v1, 2v2, and 3v3.

## Commands
- `/elo mode [player]` — check a player's rating in a mode
- `/leaderboard mode` — top 10 players with recorded matches
- `/reportmatch mode winner red1 blue1 [red2] [blue2] [red3] [blue3]` — staff-only match result
- `/matchhistory [mode]` — recent recorded matches
- `/undomatch match_id` — staff-only undo
- `/elohelp` — command overview

## Important setup notes
- Use `DISCORD_TOKEN` as an environment variable. Never put the token in source code or share it.
- Set `GUILD_ID` to your Discord server ID for faster slash-command registration.
- Set `STAFF_ROLE_ID` to the ID of the tournament staff role. If not set, members with Manage Server or Administrator permissions can report/undo matches.
- Set `DB_PATH` to a path on a persistent disk/volume provided by your hosting platform. Example: `/data/elo.sqlite3`.
- The bot creates the SQLite database automatically.

## Invite permissions
In Discord Developer Portal, configure the bot to use the `bot` and `applications.commands` scopes. It primarily needs permission to view channels and send messages. It does not need Administrator permission.

## Match reporting
Choose the mode and winner, then provide the players for each side:
- 1v1: red1 and blue1
- 2v2: red1, red2, blue1, blue2
- 3v3: red1, red2, red3, blue1, blue2, blue3

Extra player slots can be left blank for smaller formats. Each player must be unique in the match.

## ELO details
- Every player starts at 1000 in each mode.
- K-factor is 32.
- Team ELO is the average of the players' current ratings.
- All players on the same team receive the same rating change.
- The top 10 leaderboard excludes players with no recorded matches.

## Hosting
Use a hosting service that supports long-running Python processes and persistent storage. Add the environment variables in the host dashboard. If you use a host with ephemeral storage, the database may be lost on redeploy/restart.
