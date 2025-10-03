from pyrogram import Client, filters
from plugins.utils.admin_checker import co_owner
from plugins.utils.helpers import START_MESSAGE, start_replymarkup, resolve_chat_id
from connections.mongo_db import get_tournament, tournaments_col, get_user, add_user, get_player, add_player, players_col, remove_player, teams_col
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, ReplyKeyboardMarkup
import asyncio

@Client.on_message(filters.command(commands="start", prefixes=["/", "!", "."]))
async def view_activity(bot, message):

    if len(message.command) > 1:
        if message.command[1].startswith("reg_"):
            try:
                chat_id = int(message.command[1].split("_", 1)[1])
            except ValueError:
                return await message.reply("❌ Invalid tournament reference.")

            result = await register_user_in_tournament(bot, message.from_user, chat_id)
            return await message.reply(result)
        if message.command[1] == 'register':
            return await show_tournaments(bot,message)
    
    gif_id = "assets/start_vid.mp4"
    # await message.react(emoji="👍")
    await message.reply_video(
        video = gif_id,
        caption = START_MESSAGE,
        reply_markup = start_replymarkup
    )


@Client.on_message(filters.command("start_tour") & filters.group)
@co_owner
async def start_tour(bot, message):
    chat = message.chat
    user = message.from_user

    # Check if already exists
    existing = get_tournament(chat.id)
    if existing:
        return await message.reply("⚠️ Tournament already exists for this group.")
    try:
        response = await bot.ask(
            chat_id = chat.id,
            text = "💰 Please enter team purse (number only):",
            user_id = user.id,
            filters=filters.text,
            timeout=60
        )
        purse = int(response.text.strip())
    except Exception:
        return "❌ Registration failed (timeout or invalid input)."

    new_tour = {
        "chat_id": chat.id,
        "title": chat.title,
        "created_by": user.id,
        "purse":purse,
        "is_active": True
    }
    tournaments_col.insert_one(new_tour)

    invite_link = f"https://t.me/{bot.me.username}?start=reg_{chat.id}"

    await message.reply_text(
        f"✅ Tournament started for **{chat.title}**!\n\n"
        f"Players can join here:\n{invite_link}"
    )

async def register_user_in_tournament(bot, user, chat_id: int):
    """
    Core registration logic (used by both /start and callback).
    Returns a string message to send back to the user.
    """
    tournament = get_tournament(chat_id)
    if not tournament:
        return "⚠️ Tournament not found or inactive."

    # Ensure global user record
    db_user = get_user(user.id)
    if not db_user:
        add_user(user.id, user.username, user.first_name)

    # Check if player exists in this tournament
    player = get_player(user.id, chat_id)
    if player and player.get("base_price"):
        return (
            f"✅ You are already registered in **{tournament['title']}**\n\n"
            "🗑 If you want to deregister then use: /deregister"
        )

    # If player exists but no base_price OR player does not exist -> ask for base price
    # Prepare reply keyboard (tapping a button sends a text message, which bot.ask can capture)
    keyboard = ReplyKeyboardMarkup(
        [["100", "500", "1000"], ["Custom"]],
        one_time_keyboard=True,
        resize_keyboard=True
    )

    try:
        # Ask for selection
        prompt = (
            "💰 Please choose your base price (tap a button) or choose Custom:\n\n"
            "• ₪100  • ₪500  • ₪1000\n"
            "• Custom - enter your own amount (multiple of 100)"
        )
        resp = await bot.ask(
            user.id,
            prompt,
            timeout=60,
            reply_markup=keyboard
        )
        choice = resp.text.strip()
    except asyncio.TimeoutError:
        # Remove keyboard when timing out (best-effort)
        try:
            await bot.send_message(user.id, "⌛ Registration timed out.", reply_markup=ReplyKeyboardRemove())
        except:
            pass
        return "❌ Registration failed (timeout). Please try /register again."

    # If they chose a preset
    if choice in ("100", "500", "1000"):
        base_price = int(choice)
    elif choice.lower() == "custom":
        # Ask for custom amount
        try:
            resp2 = await bot.ask(
                user.id,
                "✏️ Enter custom base price (number, multiple of 100):",
                timeout=60,
                reply_markup=ReplyKeyboardMarkup([["Cancel"]], one_time_keyboard=True, resize_keyboard=True)
            )
            text = resp2.text.strip()
            if text.lower() == "cancel":
                await bot.send_message(user.id, "❌ Registration cancelled.", reply_markup=ReplyKeyboardRemove())
                return "❌ Registration cancelled by user."

            # Validate integer
            base_price = int(text)
        except asyncio.TimeoutError:
            try:
                await bot.send_message(user.id, "⌛ Registration timed out.", reply_markup=ReplyKeyboardRemove())
            except:
                pass
            return "❌ Registration failed (timeout). Please try /register again."
        except ValueError:
            try:
                await bot.send_message(user.id, "❌ Invalid number. Registration aborted.", reply_markup=ReplyKeyboardRemove())
            except:
                pass
            return "❌ Registration failed (invalid number). Please try /register again."
        # Validate multiple of 100 and positive
        if base_price <= 0 or base_price % 100 != 0:
            try:
                await bot.send_message(user.id, "❌ Amount must be a positive multiple of 100.", reply_markup=ReplyKeyboardRemove())
            except:
                pass
            return "❌ Invalid amount. It must be a positive multiple of 100."
    else:
        # They typed something else (not using buttons)
        # Try to parse as integer (allow direct typed amount)
        try:
            base_price = int(choice)
        except Exception:
            try:
                await bot.send_message(user.id, "❌ Invalid selection. Registration aborted.", reply_markup=ReplyKeyboardRemove())
            except:
                pass
            return "❌ Registration failed (invalid selection). Please try /register again."

        # Validate
        if base_price <= 0 or base_price % 100 != 0:
            try:
                await bot.send_message(user.id, "❌ Amount must be a positive multiple of 100.", reply_markup=ReplyKeyboardRemove())
            except:
                pass
            return "❌ Invalid amount. It must be a positive multiple of 100."

    # At this point base_price is a validated int
    # Create player record if not exists, or update existing player with base_price
    if not player:
        add_player(user.id, chat_id, base_price=base_price)
    else:
        # update existing player's base_price
        players_col.update_one(
            {"user_id": user.id, "chat_id": chat_id},
            {"$set": {"base_price": base_price, "status": "unsold"}}
        )

    # Remove reply keyboard (cleanup)
    try:
        await bot.send_message(user.id, f"🎉 Registered with base price {base_price}!", reply_markup=ReplyKeyboardRemove())
    except:
        pass

    return f"🎉 Welcome {user.first_name}! You are now registered in **{tournament['title']}** with base price **{base_price}**."

@Client.on_message(filters.command("register") & filters.group)
async def group_reg(bot, message):
    # Button that redirects to the bot's DM
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📩 Go to my DM", url=f"https://t.me/{bot.me.username}?start=register")]
        ]
    )

    await message.reply(
        "Please continue your registration in DM 👇",
        reply_markup=keyboard
    )

@Client.on_message(filters.command("register") & filters.private)
async def show_tournaments(bot, message):
    # Fetch ongoing tournaments
    tournaments = list(tournaments_col.find({"is_active": True}))

    if not tournaments:
        return await message.reply("⚠️ No active tournaments right now.")

    # Build inline buttons
    buttons = []
    for t in tournaments:
        buttons.append([
            InlineKeyboardButton(
                text=t["title"],
                callback_data=f"reg_{t['chat_id']}"
            )
        ])

    await message.reply_photo(
        photo="assets/register.png",
        caption="🏆 Select a tournament to register:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex(r"^reg_"))
async def handle_register_callback(bot, query):
    try:
        chat_id = int(query.data.split("_", 1)[1])
    except ValueError:
        return await query.answer("❌ Invalid tournament reference.", show_alert=True)

    result = await register_user_in_tournament(bot, query.from_user, chat_id)
    await query.message.reply(result)
    await query.answer()


@Client.on_message(filters.command("deregister") & filters.private)
async def show_deregister_options(bot, message):
    user = message.from_user

    # Find tournaments where user is registered
    player_entries = list(players_col.find({"user_id": user.id}))
    if not player_entries:
        return await message.reply("⚠️ You are not registered in any tournaments.")

    # Build buttons
    buttons = []
    for p in player_entries:
        tournament = get_tournament(p["chat_id"])
        if tournament:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{tournament['title']} (Base: {p['base_price']})",
                    callback_data=f"dereg_{p['chat_id']}"
                )
            ])

    await message.reply(
        "🗑 Select a tournament to deregister from:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex(r"^dereg_"))
async def handle_deregister_callback(bot, query):
    try:
        chat_id = int(query.data.split("_", 1)[1])
    except ValueError:
        return await query.answer("❌ Invalid tournament reference.", show_alert=True)

    user = query.from_user

    # Check if user is registered
    player = players_col.find_one({"user_id": user.id, "chat_id": chat_id})
    if not player:
        return await query.answer("⚠️ You are not registered here.", show_alert=True)

    # Remove player
    remove_player(user.id, chat_id)
    tournament = get_tournament(chat_id)

    await query.message.reply(
        f"🗑 You have been deregistered from **{tournament['title']}**."
    )
    await query.answer("✅ Deregistered successfully!")


from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@Client.on_message(filters.command("stop_tour") & filters.group)
@co_owner
async def stop_tour(bot, message):
    chat_id = resolve_chat_id(message.chat.id)
    tournament = get_tournament(chat_id)

    if not tournament:
        return await message.reply("⚠️ No tournament exists in this group.")

    # Ask for confirmation
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, stop", callback_data=f"confirm_stop_{chat_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])

    await message.reply(
        "⚠️ Are you sure you want to **stop this tournament**?\n"
        "This will delete it permanently from the database.",
        reply_markup=buttons
    )


@Client.on_callback_query(filters.regex(r"^confirm_stop_"))
async def confirm_stop_tour(bot, query):
    chat_id = int(query.data.split("_")[2])

    tournaments_col.delete_one({"chat_id": chat_id})
    await query.message.edit_text("🛑 Tournament has been stopped and removed.")
    await query.answer("✅ Tournament stopped.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^cancel_action$"))
async def cancel_action(bot, query):
    await query.message.edit_text("❌ Action cancelled.")
    await query.answer("Cancelled.")


@Client.on_message(filters.command("clear") & filters.group)
@co_owner
async def clear_all(bot, message):
    chat_id = resolve_chat_id(message.chat.id)

    player_count = players_col.count_documents({"chat_id": chat_id})
    team_count = teams_col.count_documents({"chat_id": chat_id})

    if player_count == 0 and team_count == 0:
        return await message.reply("⚠️ Nothing to clear in this group.")

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, clear all", callback_data=f"confirm_clear_{chat_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])

    await message.reply(
        f"⚠️ Are you sure you want to clear ALL tournament data for this group?\n\n"
        f"👤 Players to remove: {player_count}\n"
        f"🏏 Teams to remove: {team_count}",
        reply_markup=buttons
    )


@Client.on_callback_query(filters.regex(r"^confirm_clear_"))
async def confirm_clear(bot, query):
    chat_id = int(query.data.split("_")[2])

    player_count = players_col.count_documents({"chat_id": chat_id})
    team_count = teams_col.count_documents({"chat_id": chat_id})

    players_col.delete_many({"chat_id": chat_id})
    teams_col.delete_many({"chat_id": chat_id})

    await query.message.edit_text(
        f"🗑 Cleared all tournament data:\n"
        f"👤 Players removed: {player_count}\n"
        f"🏏 Teams removed: {team_count}"
    )
    await query.answer("✅ Data cleared.", show_alert=True)
