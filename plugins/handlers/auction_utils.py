from pyrogram import Client, filters
from plugins.utils.admin_checker import co_owner, group_admin
from connections.mongo_db import players_col, get_tournament, get_user, get_player, add_user, teams_col
from plugins.utils.helpers import resolve_user, resolve_chat_id
from config import Config

def split_message(text, limit=4000):
    """Split text into chunks under Telegram's message limit"""
    for i in range(0, len(text), limit):
        yield text[i:i+limit]

@Client.on_message(filters.command("list") & filters.group)
@co_owner
async def list_players(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)

    if not tournament:
        return await message.reply("⚠️ No active tournament here.")

    players = list(players_col.find({"chat_id": chat_id}))
    if not players:
        return await message.reply("⚠️ No players registered yet.")

    text = f"📋 **Players in {tournament['title']}**\n\n"
    for idx, p in enumerate(players, start=1):
        user_info = get_user(p["user_id"])
        name = user_info["full_name"] if user_info and user_info.get("full_name") else (user_info.get("username") if user_info else "Unknown")
        status = p.get("status", "unsold").capitalize()

        if status.lower() == "sold":
            sold_price = p.get("sold_price", "N/A")
            sold_to = p.get("sold_to", "N/A")
            text += (
                f"{idx}. {name} (ID: `{p['user_id']}`) — Status: {status}\n"
                f"    💰 Sold Price: {sold_price} — Team: {sold_to}\n"
            )
        else:
            text += f"{idx}. {name} (ID: `{p['user_id']}`) — Status: {status}\n"

    # Split and send
    for chunk in split_message(text):
        await message.reply(chunk)


@Client.on_message(filters.command("unsold") & filters.group)
@co_owner
async def unsold_players(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)

    if not tournament:
        return await message.reply("⚠️ No active tournament here.")

    # Fetch unsold players (no sorting, insertion order)
    players = list(players_col.find({"chat_id": chat_id, "status": "unsold"}))
    if not players:
        return await message.reply("🎉 No unsold players left!")

    text = f"❌ **Unsold Players in {tournament['title']}**\n\n"
    for idx, p in enumerate(players, start=1):
        user_info = get_user(p["user_id"])
        name = user_info["full_name"] if user_info and user_info.get("full_name") else (
            user_info.get("username") if user_info else "Unknown"
        )
        text += f"{idx}. {name} (ID: `{p['user_id']}`) — Status: {p.get('status', 'unsold')}\n"

    # Split long text if needed
    for chunk in split_message(text):
        await message.reply(chunk)


@Client.on_message(filters.command("add_player") & filters.group)
@co_owner
async def add_player_cmd(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)
    if not tournament:
        return await message.reply("⚠️ No active tournament here.")

    # --- Identify user and base price ---
    if message.reply_to_message:
        # Reply mode: /add_player {base_price}
        if len(message.command) < 2:
            return await message.reply("⚠️ Usage: Reply with /add_player {base_price}")
        try:
            base_price = int(message.command[1])
        except ValueError:
            return await message.reply("❌ Invalid base_price.")
        user = message.reply_to_message.from_user
    else:
        # Normal mode: /add_player {user_id/username} {base_price}
        if len(message.command) < 3:
            return await message.reply("⚠️ Usage: /add_player {user_id/username} {base_price}")
        identifier = message.command[1]
        try:
            base_price = int(message.command[2])
        except ValueError:
            return await message.reply("❌ Invalid base_price.")
        user = await resolve_user(bot, identifier)
        if not user:
            return await message.reply("❌ Could not resolve user.")

    # --- Check if already registered ---
    existing = get_player(user.id, chat_id)
    if existing:
        return await message.reply("⚠️ Player already registered.")

    # --- Ensure global user ---
    db_user = get_user(user.id)
    if not db_user:
        add_user(user.id, user.username, user.first_name)

    # --- Insert player ---
    new_player = {
        "user_id": user.id,
        "chat_id": chat_id,
        "base_price": base_price,
        "status": "unsold",
        "sold_to": None,
        "sold_price": None
    }
    players_col.insert_one(new_player)

    await message.reply(
        f"✅ Added player {user.first_name} (ID: `{user.id}`) "
        f"with base price {base_price} to **{tournament['title']}**."
    )


@Client.on_message(filters.command("remove_player") & filters.group)
@co_owner
async def remove_player_cmd(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)
    if not tournament:
        return await message.reply("⚠️ No active tournament here.")

    if message.reply_to_message:
        user = message.reply_to_message.from_user
    else:
        args = message.text.split()
        if len(args) < 2:
            return await message.reply("⚠️ Usage: /remove_player {user_id/username} or reply to a user")

        identifier = args[1]
        user = await resolve_user(bot, identifier)
        if not user:
            return await message.reply("❌ Could not resolve user.")

    player = get_player(user.id, chat_id)
    if not player:
        return await message.reply("⚠️ Player not found in this tournament.")

    # --- Remove player ---
    players_col.delete_one({"user_id": user.id, "chat_id": chat_id})

    await message.reply(
        f"🗑 Removed player {user.first_name} (ID: `{user.id}`) "
        f"from **{tournament['title']}**."
    )


@Client.on_message(filters.command("reset") & filters.group)
@co_owner
async def reset_player_cmd(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    args = message.text.split()

    if message.reply_to_message:
        user = message.reply_to_message.from_user
    elif len(args) >= 2:
        identifier = args[1]
        user = await resolve_user(bot, identifier)
    else:
        return await message.reply("⚠️ Usage: /reset {user_id/username} or reply to a user")

    if not user:
        return await message.reply("❌ Could not resolve user.")

    player = get_player(user.id, chat_id)
    if not player:
        return await message.reply("⚠️ Player not found in this tournament.")

    if player.get("status") != "sold":
        return await message.reply("⚠️ This player is not sold yet, nothing to reset.")

    team_name = player.get("sold_to")
    sold_price = player.get("sold_price", 0)

    if not team_name:
        return await message.reply("⚠️ Could not find the team for this player.")

    # Refund purse & remove player from team
    teams_col.update_one(
        {"chat_id": chat_id, "team_name": team_name},
        {
            "$inc": {"purse": sold_price},  # refund purse
            "$pull": {"sold_players": {"player_id": user.id}}  # remove player from sold list
        }
    )

    # Reset player
    players_col.update_one(
        {"user_id": user.id, "chat_id": chat_id},
        {"$set": {"status": "unsold", "sold_to": None, "sold_price": None}}
    )

    await message.reply(
        f"🔄 Player {user.first_name} (ID: `{user.id}`) has been reset to **unsold**.\n"
        f"💰 Refunded {sold_price} back to **{team_name}**."
    )


@Client.on_message(filters.command("add_team") & filters.group)
@co_owner
async def add_team(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)
    if not tournament:
        return await message.reply("⚠️ No active tournament here.")

    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if len(message.command) < 2:
            return await message.reply("⚠️ Usage: Reply with /add_team {team_name}")
        team_name = " ".join(message.command[1:])
    else:
        if len(message.command) < 3:
            return await message.reply("⚠️ Usage: /add_team {user_id/username} {team_name}")

        identifier = message.command[1]
        user = await resolve_user(bot, identifier)
        if not user:
            return await message.reply("❌ Could not resolve user.")
        team_name = " ".join(message.command[2:])

    existing = teams_col.find_one(
        {"chat_id": chat_id, "$or": [{"team_name": team_name}, {"owner_id": user.id}]}
    )
    if existing:
        return await message.reply("⚠️ A team with this name or owner already exists in this tournament.")

    new_team = {
        "chat_id": chat_id,
        "team_name": team_name,
        "owner_id": user.id,
        "bidder_list": [user.id],
        "purse": tournament["purse"],
        "sold_players": []
    }
    teams_col.insert_one(new_team)

    await message.reply(
        f"✅ Team **{team_name}** registered for {user.mention}\n"
        f"💰 Starting purse: {tournament['purse']:,} ⓜ"
    )


@Client.on_message(filters.command("team") & filters.group)
async def fetch_team_players(bot, message):
    if len(message.command) < 2:
        return await message.reply("⚠️ Usage: /team <team_name>")

    team_name = " ".join(message.command[1:])
    chat_id = resolve_chat_id(message.chat.id)

    team_data = teams_col.find_one(
        {"chat_id": chat_id, "team_name": {"$regex": f"^{team_name}$", "$options": "i"}},
        {"_id": 0}
    )

    if not team_data:
        return await message.reply(f"⚠️ Team '{team_name}' not found in this tournament!")

    bidders_text = ""
    for uid in team_data.get("bidder_list", []):
        try:
            user = await bot.get_users(uid)
            bidders_text += f"- {user.mention}\n"
        except:
            bidders_text += f"- `{uid}`\n"

    sold_players = team_data.get("sold_players", [])
    purse = team_data.get("purse", 0)
    total_cost = sum(p.get("sold_price", 0) for p in sold_players)

    response = (
        f"📜 <u>**Team Details:**</u> {team_data['team_name']}\n\n"
        f"👑 **Owner ID:** `{team_data['owner_id']}`\n"
        f"💼 **Bidders:**\n{bidders_text if bidders_text else 'None'}\n"
        f"🛒 **Players Bought:** {len(sold_players)}\n"
        f"💰 **Total Cost:** {total_cost:,}\n\n"
        f"💵 **Purse Left:** {purse:,}\n\n"
    )

    if not sold_players:
        response += f"No players have been sold to {team_data['team_name']} yet."
    else:
        response += "📌 **Sold Players:**\n\n"
        for idx, player in enumerate(sold_players, start=1):
            response += (
                f"{idx}. 👤 {player['player_name']} (ID: `{player['player_id']}`)\n"
                f"   💰 Sold Price: {player.get('sold_price', 0):,}\n\n"
            )

    await message.reply(response)


@Client.on_message(filters.command("add_bidder") & filters.group)
@co_owner
async def add_bidder(bot, message):
    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply("⚠️ Usage:\n- /add_bidder {team_name} {user_id/username}\n- Or reply to a user with /add_bidder {team_name}")

    chat_id = resolve_chat_id(message.chat.id)
    team_name = message.command[1] if len(message.command) > 1 else None

    # Check team name
    if not team_name:
        return await message.reply("⚠️ Please provide a team name.")

    # Get the target user
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(message.command) > 2:
        identifier = message.command[2]
        target_user = await resolve_user(bot, identifier)
    else:
        return await message.reply("⚠️ No user provided (reply or pass username/ID).")

    if not target_user:
        return await message.reply("❌ Could not resolve user.")

    # Find team in this tournament
    team = teams_col.find_one(
        {"chat_id": chat_id, "team_name": {"$regex": f"^{team_name}$", "$options": "i"}}
    )
    if not team:
        return await message.reply(f"⚠️ Team '{team_name}' not found in this tournament.")

    # Check if already in bidder list
    if target_user.id in team.get("bidder_list", []):
        return await message.reply(f"⚠️ {target_user.mention} is already a bidder for **{team['team_name']}**.")

    # Update bidder_list
    teams_col.update_one(
        {"_id": team["_id"]},
        {"$push": {"bidder_list": target_user.id}}
    )

    await message.reply(
        f"✅ Added {target_user.mention} as an extra bidder for **{team['team_name']}**."
    )

@Client.on_message(filters.command("info") & filters.group)
@group_admin
async def get_player_info(bot, message):
    args = message.text.split()
    chat_id = resolve_chat_id(message.chat.id)

    # --- Identify target user ---
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(args) == 2:
        target_user = await resolve_user(bot, args[1])
        if not target_user:
            return await message.reply("❌ Unable to fetch user details.")
    else:
        return await message.reply("⚠️ Usage: Reply to a user with /info or use /info {userid/username}")

    # --- Fetch player data for this tournament ---
    player = players_col.find_one({"user_id": target_user.id, "chat_id": chat_id})
    if not player:
        return await message.reply("⚠️ Player not found in this tournament database.")

    # --- Determine status ---
    status = player.get("status", "unsold").capitalize()
    sold_price = player.get("sold_price", "N/A")

    # --- Get team name if sold ---
    team_name = "N/A"
    if player.get("sold_to"):
        team = teams_col.find_one({"chat_id": chat_id, "team_name": player["sold_to"]})
        if team:
            team_name = team.get("team_name", "N/A")

    base_price = player.get("base_price", "N/A")
    # --- Reply with info ---
    await message.reply(
        f"<b><u>Player Information</u></b>\n\n"
        f"<b>👤 Name:</b> {target_user.mention}\n"
        f"<b>🆔 User ID:</b> <code>{target_user.id}</code>\n"
        f"<b>💵 Base Price:</b> {base_price}\n\n"
        f"<b>📊 Status:</b> {status}\n"
        f"<b>💰 Sold Price:</b> {sold_price}\n"
        f"<b>🏏 Team:</b> {team_name}\n"
    )


@Client.on_message(filters.command("purse") & filters.group)
@co_owner
async def show_team_purses(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)
    if not tournament:
        return await message.reply("⚠️ No active tournament here.")

    teams = list(teams_col.find({"chat_id": chat_id}))
    if not teams:
        return await message.reply("⚠️ No teams registered in this tournament.")

    text = f"💼 **Team Purses in {tournament['title']}**\n\n"
    for idx, team in enumerate(teams, start=1):
        purse = team.get("purse", 0)
        players_count = len(team.get("sold_players", []))
        text += (
            f"{idx}. 🏏 **{team['team_name']}**\n"
            f"   💰 Purse Left: {purse:,} ⓜ\n"
            f"   👥 Players Bought: {players_count}\n\n"
        )

    # split if text is too long
    for chunk in split_message(text):
        await message.reply(chunk)

# @Client.on_message(filters.private)
# async def contactrobot(bot, message):
#     await message.forward(Config.LOG_CHANNEL)