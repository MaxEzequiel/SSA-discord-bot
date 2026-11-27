import asyncio
import io
import os
import time
from datetime import datetime, timezone, timedelta
from datetime import time as day_time
from operator import itemgetter

import discord
from discord.ext import commands, tasks

import config
import database
import transcript

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

COMMAND_GUILDS = [
    discord.Object(id=config.GUILD_ID),
    discord.Object(id=config.STAFF_GUILD_ID),
]

cooldowns_restored = False

DIFFICULTY_NAMES = {
    "basic": "Basic",
    "medium": "Medium",
    "advanced": "Advanced",
    "bypass": "Bypass",
}

DIFFICULTY_POINTS = {
    "basic": config.POINTS_BASIC,
    "medium": config.POINTS_MEDIUM,
    "advanced": config.POINTS_ADVANCED,
    "bypass": config.POINTS_BYPASS,
}

MAX_WITHOUT_POINTS = 5

MAX_STRIKES = 3
WARNS_PER_STRIKE = 5
MIN_WEEKLY_POINTS = 5

PASS_TEST_NAMES = {
    "si": "Sí",
    "no": "No",
    "indefinido": "Indefinido",
}

BYPASS_EMOJIS = {
    "si": "✅",
    "no": "❌",
    "indefinido": "➖",
}

OS_NAMES = {
    "windows": "Windows",
    "linux": "Linux",
    "mac": "Mac",
}

MODIFIED_NAMES = {
    "si": "Sí",
    "no": "No",
}

result_sessions = {}

ACTIVITY_SCHEDULE = day_time(hour=config.ACTIVITY_HOUR, minute=config.ACTIVITY_MINUTE)


def has_any_role(member, role_ids):
    for role_id in role_ids:
        if member.get_role(role_id) is not None:
            return True
    return False


async def send_ephemeral(interaction, text=None, embed=None):
    if interaction.response.is_done():
        await interaction.followup.send(text, embed=embed, ephemeral=True)
        return
    await interaction.response.send_message(text, embed=embed, ephemeral=True)


def is_staff(member):
    return has_any_role(member, config.STAFF_ROLE_IDS)


def is_admin(member):
    return has_any_role(member, config.ADMIN_ROLE_IDS)


def can_manage_points(member):
    return has_any_role(member, config.POINTS_ROLE_IDS)


def get_normal_member(user_id):
    guild = get_normal_guild()
    if guild is None:
        return None
    return guild.get_member(user_id)


def is_normal_staff(user):
    member = get_normal_member(user.id)
    if member is None:
        return False
    return is_staff(member)


def is_normal_admin(user):
    member = get_normal_member(user.id)
    if member is None:
        return False
    return is_admin(member)


def can_manage_normal_points(user):
    member = get_normal_member(user.id)
    if member is None:
        return False
    return can_manage_points(member)


async def member_autocomplete(interaction, current):
    guild = get_normal_guild()
    if guild is None:
        return []
    choices = []
    current_lower = current.lower()
    for member in guild.members:
        if current_lower in member.name.lower():
            choices.append(discord.app_commands.Choice(name=member.name, value=str(member.id)))
        elif current_lower in member.display_name.lower():
            choices.append(discord.app_commands.Choice(name=member.name, value=str(member.id)))
        if len(choices) == 25:
            break
    return choices


def get_normal_guild():
    return bot.get_guild(config.GUILD_ID)


def get_staff_channel(channel_id):
    guild = bot.get_guild(config.STAFF_GUILD_ID)
    if guild is None:
        return None
    return guild.get_channel(channel_id)


def get_normal_channel(channel_id):
    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        return None
    return guild.get_channel(channel_id)


def get_difficulty_for_member(member):
    if member.get_role(config.SS_ADVANCED_ROLE_ID) is not None:
        return "advanced"
    if member.get_role(config.SS_MEDIUM_ROLE_ID) is not None:
        return "advanced"
    if member.get_role(config.SS_BASIC_ROLE_ID) is not None:
        return "medium"
    return "basic"


def get_cooldown_seconds_left(user_id):
    row = database.get_cooldown(user_id)
    if row is None:
        return 0
    remaining = row["cooldown_end"] - int(time.time())
    if remaining < 0:
        return 0
    return remaining


def format_remaining(seconds):
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    text = ""
    if days > 0:
        text = text + str(days) + "d "
    if hours > 0:
        text = text + str(hours) + "h "
    if minutes > 0:
        text = text + str(minutes) + "m"
    if text == "":
        text = "menos de 1m"
    return text.strip()


def format_date(timestamp):
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    return moment.strftime("%d/%m/%Y %H:%M UTC")


def parse_duration(args):
    total = 0
    for item in args:
        unit = item[-1].lower()
        amount = int(item[:-1])
        if unit == "d":
            total = total + amount * 86400
        if unit == "h":
            total = total + amount * 3600
        if unit == "m":
            total = total + amount * 60
    return total


def shorten_text(text, length):
    if len(text) > length:
        return text[: length - 3] + "..."
    return text


def quote_text(text):
    lines = text.split("\n")
    quoted = []
    for line in lines:
        quoted.append("> " + line)
    return "\n".join(quoted)


def get_result_cooldown_seconds(member):
    if member is None:
        return parse_duration(config.TEST_COOLDOWN_TEXT.split())
    if member.get_role(config.BOOSTER_ROLE_ID) is not None:
        return parse_duration(config.BOOSTER_COOLDOWN_TEXT.split())
    staff_guild = bot.get_guild(config.STAFF_GUILD_ID)
    if staff_guild is not None:
        staff_member = staff_guild.get_member(member.id)
        if staff_member is not None:
            if staff_member.get_role(config.STAFF_MONTH_ROLE_ID) is not None:
                return parse_duration(config.STAFF_MONTH_COOLDOWN_TEXT.split())
    if member.get_role(config.VIP_ROLE_ID) is not None:
        return parse_duration(config.VIP_COOLDOWN_TEXT.split())
    return parse_duration(config.TEST_COOLDOWN_TEXT.split())


def get_test_role_id(difficulty):
    if difficulty == "advanced":
        return config.SS_ADVANCED_ROLE_ID
    if difficulty == "medium":
        return config.SS_MEDIUM_ROLE_ID
    return config.SS_BASIC_ROLE_ID


def get_highest_test_role_id(member):
    if member is None:
        return 0
    if member.get_role(config.SS_ADVANCED_ROLE_ID) is not None:
        return config.SS_ADVANCED_ROLE_ID
    if member.get_role(config.SS_MEDIUM_ROLE_ID) is not None:
        return config.SS_MEDIUM_ROLE_ID
    if member.get_role(config.SS_BASIC_ROLE_ID) is not None:
        return config.SS_BASIC_ROLE_ID
    return 0


def get_test_level_name(role_id):
    if role_id == config.SS_ADVANCED_ROLE_ID:
        return "Advanced"
    if role_id == config.SS_MEDIUM_ROLE_ID:
        return "Medium"
    if role_id == config.SS_BASIC_ROLE_ID:
        return "Basic"
    return "User"


def get_test_level_below(level_name):
    if level_name == "Advanced":
        return "Medium"
    if level_name == "Medium":
        return "Basic"
    return "User"


def get_new_test_level_name(current_name, session):
    if session["pass_test"] == "si":
        return DIFFICULTY_NAMES[session["difficulty"]]
    if session.get("downgrade") == "si":
        return get_test_level_below(current_name)
    return current_name


def build_public_result_text(user_id, current_name, new_name):
    return "<@" + str(user_id) + "> " + current_name + " > " + new_name


def get_week_start():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def get_month_start():
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def get_staff_members(guild):
    members = []
    for member in guild.members:
        if is_staff(member) == True:
            members.append(member)
    return members


def get_strike_channel():
    return get_staff_channel(config.STRIKE_CHANNEL_ID)


def format_strike_count(strikes):
    shown = strikes
    if shown > MAX_STRIKES:
        shown = MAX_STRIKES
    return str(shown) + "/" + str(MAX_STRIKES)


async def notify_strike(user_id, strikes, reason):
    channel = get_strike_channel()
    if channel is None:
        return
    await channel.send("<@" + str(user_id) + "> " + format_strike_count(strikes) + " — " + reason)


async def expel_staff(user_id):
    staff_guild = bot.get_guild(config.STAFF_GUILD_ID)
    if staff_guild is not None:
        staff_member = staff_guild.get_member(user_id)
        if staff_member is not None:
            await staff_member.kick(reason="Llegó a " + str(MAX_STRIKES) + " strikes")
    guild = get_normal_guild()
    if guild is None:
        return
    member = guild.get_member(user_id)
    if member is None:
        return
    roles = []
    for role_id in config.STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role is not None:
            if member.get_role(role_id) is not None:
                roles.append(role)
    if len(roles) > 0:
        await member.remove_roles(*roles)


async def add_strikes(user_id, amount, reason):
    row = database.get_staff_counts(user_id)
    warns = 0
    strikes = 0
    if row is not None:
        warns = row["warns"]
        strikes = row["strikes"]
    start = strikes
    strikes = strikes + amount
    database.set_staff_counts(user_id, warns, strikes)
    database.add_strike_history(user_id, "strike", amount, reason, int(time.time()))
    for index in range(amount):
        await notify_strike(user_id, start + index + 1, reason)
    if strikes >= MAX_STRIKES:
        await expel_staff(user_id)
    return strikes


async def add_warns(user_id, amount, reason):
    row = database.get_staff_counts(user_id)
    warns = 0
    strikes = 0
    if row is not None:
        warns = row["warns"]
        strikes = row["strikes"]
    warns = warns + amount
    new_strikes = warns // WARNS_PER_STRIKE
    warns = warns - (new_strikes * WARNS_PER_STRIKE)
    database.set_staff_counts(user_id, warns, strikes)
    database.add_strike_history(user_id, "warn", amount, reason, int(time.time()))
    if new_strikes > 0:
        await add_strikes(user_id, new_strikes, reason)
    return warns


def build_setup_embed():
    embed = discord.Embed(title="SS Test Request", color=discord.Color.green())
    embed.description = (
        "Solicita una ss test para probar tu nivel de SS.\n"
        "Recuerda leer el canal de <#" + str(config.SS_GUIDE_CHANNEL_ID) + ">\n\n"
        "Te deseamos la mejor de las suertes"
    )
    return embed


def build_setup_view():
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(
        label="SS Request",
        style=discord.ButtonStyle.success,
        custom_id="ss_request_button",
    )
    view.add_item(button)
    return view


def build_bypass_setup_embed():
    embed = discord.Embed(title="Bypass Request", color=discord.Color.red())
    embed.description = (
        "Solicita un test para bypass test.\n"
        "Un miembro del staff te atenderá.\n\n"
        "Te deseamos la mejor de las suertes"
    )
    return embed


def build_bypass_setup_view():
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(
        label="Bypass Request",
        style=discord.ButtonStyle.danger,
        custom_id="bypass_request_button",
    )
    view.add_item(button)
    return view


def build_bypasstop_embed(guild, author_id):
    rows = database.get_top_bypassers(5)
    embed = discord.Embed(title="🏆 Top Bypassers", color=discord.Color.gold())
    lines = []
    position = 0
    author_in_top = False
    for row in rows:
        position = position + 1
        member = guild.get_member(row["user_id"])
        if member is None:
            name = "<@" + str(row["user_id"]) + ">"
        else:
            name = member.display_name
        lines.append(str(position) + ". " + name + " — **" + str(row["points"]) + "** puntos")
        if row["user_id"] == author_id:
            author_in_top = True
    if len(lines) == 0:
        embed.description = "Todavía no hay bypassers con puntos."
    else:
        embed.description = "\n".join(lines)
    if author_in_top == False:
        author_position = database.get_bypass_position(author_id)
        if author_position == 0:
            embed.add_field(name="Tu posición", value="No apareces en el ranking todavía.", inline=False)
        else:
            embed.add_field(
                name="Tu posición",
                value="<@" + str(author_id) + "> está en la posición " + str(author_position),
                inline=False,
            )
    return embed


def build_ticket_embed(user_id, user_name, difficulty, claimed_by_id, os_name=None, modified=None):
    difficulty_name = DIFFICULTY_NAMES[difficulty]
    embed = discord.Embed(
        title="Ticket " + difficulty_name + " de " + user_name,
        color=discord.Color.blue(),
    )
    embed.description = (
        "👋 Hola <@" + str(user_id) + ">\n"
        "✅ Tu ticket " + difficulty_name + " ha sido creado.\n"
        "👨‍💼 Un miembro del staff te atenderá.\n"
        "⏳ Cooldown se aplicará al cerrar el ticket."
    )
    if os_name is not None:
        embed.add_field(name="💻 Sistema", value=OS_NAMES[os_name], inline=True)
    if modified is not None:
        embed.add_field(name="🛠 Modificado", value=MODIFIED_NAMES[modified], inline=True)
    if claimed_by_id is None:
        embed.add_field(name="Reclamado por", value="Nadie (libre)", inline=False)
    else:
        embed.add_field(name="Reclamado por", value="<@" + str(claimed_by_id) + ">", inline=False)
    return embed


def build_reminder_embed(thumbnail_name, channel_id):
    embed = discord.Embed(
        title="Recordatorio de ticket",
        description=config.REMINDER_MESSAGE,
        color=discord.Color.purple(),
    )
    embed.set_thumbnail(url="attachment://" + thumbnail_name)
    embed.add_field(
        name="Ticket",
        value="[Ir al ticket](https://discord.com/channels/" + str(config.GUILD_ID) + "/" + str(channel_id) + ")",
        inline=False,
    )
    return embed


def build_ticket_view(claim_disabled):
    view = discord.ui.View(timeout=None)
    claim_button = discord.ui.Button(
        label="Claim Ticket",
        style=discord.ButtonStyle.primary,
        custom_id="claim_ticket_button",
        disabled=claim_disabled,
    )
    close_button = discord.ui.Button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="close_ticket_button",
    )
    view.add_item(claim_button)
    view.add_item(close_button)
    return view


def build_result_text(ticket, tester_id, difficulty, cheat, bypass_text, encontro, notas, cosas_encontradas, razonamiento, puntaje, pass_test_name):
    difficulty_name = DIFFICULTY_NAMES[difficulty]
    timestamp = "<t:" + str(int(time.time())) + ">"
    text = (
        "## Resultado del test · " + difficulty_name + "\n\n"
        "- Tag User: <@" + str(ticket["user_id"]) + ">\n"
        "- Tester: <@" + str(tester_id) + ">\n"
        "- Apply: " + difficulty_name + "\n"
        "- Puntaje: " + shorten_text(puntaje, 50) + "\n"
        "- Pasa la test: " + pass_test_name + "\n\n"
        "- **Cheat:**\n" + shorten_text(quote_text(cheat), 250) + "\n\n"
        "- **Bypass:**\n" + shorten_text(quote_text(bypass_text), 250) + "\n\n"
        "- **Encontró:**\n" + shorten_text(encontro, 100) + "\n\n"
        "- **Cosas encontradas:**\n" + shorten_text(quote_text(cosas_encontradas), 100) + "\n\n"
        "- **Notas:**\n" + shorten_text(quote_text(notas), 250) + "\n\n"
        "- **Razonamiento:**\n" + shorten_text(quote_text(razonamiento), 250) + "\n\n"
        "Resultado registrado el " + timestamp
    )
    return text


def build_result_proofs_view(channel_id):
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(
        label="Finalizar test",
        style=discord.ButtonStyle.success,
        custom_id="finish_result_" + str(channel_id),
    )
    view.add_item(button)
    return view


def build_result_continue_view(channel_id):
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(
        label="Continuar",
        style=discord.ButtonStyle.primary,
        custom_id="continue_result_" + str(channel_id),
    )
    view.add_item(button)
    return view


def build_bypass_proofs_view(channel_id):
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(
        label="Finalizar bypass",
        style=discord.ButtonStyle.danger,
        custom_id="finish_bypass_" + str(channel_id),
    )
    view.add_item(button)
    return view


def build_activity_lines(members, points_rows):
    points_by_staff = {}
    for row in points_rows:
        points_by_staff[row["staff_id"]] = row["total"]
    with_points = []
    without_points = []
    for member in members:
        points = points_by_staff.get(member.id, 0)
        if points > 0:
            with_points.append((member, points))
        else:
            without_points.append(member)
    with_points.sort(key=itemgetter(1), reverse=True)
    lines = []
    position = 1
    for entry in with_points:
        member = entry[0]
        points = entry[1]
        lines.append(str(position) + ". " + member.mention + " — " + str(points) + " puntos")
        position = position + 1
    shown = 0
    for member in without_points:
        if shown >= MAX_WITHOUT_POINTS:
            break
        lines.append(str(position) + ". " + member.mention + " — 0 puntos")
        position = position + 1
        shown = shown + 1
    hidden = len(without_points) - shown
    if hidden > 0:
        lines.append("… y " + str(hidden) + " más sin puntos")
    if len(lines) == 0:
        lines.append("Sin datos.")
    return lines


def split_lines_into_chunks(lines, max_length):
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_length:
            chunks.append(current)
            current = line
        elif current == "":
            current = line
        else:
            current = current + "\n" + line
    if current != "":
        chunks.append(current)
    return chunks


def build_activity_embeds(members, week_rows, month_rows):
    week_chunks = split_lines_into_chunks(build_activity_lines(members, week_rows), 1000)
    month_chunks = split_lines_into_chunks(build_activity_lines(members, month_rows), 1000)
    embeds = []
    for index in range(len(week_chunks)):
        embed = discord.Embed(color=discord.Color.purple())
        if len(embeds) == 0:
            embed.title = "Actividad del Staff"
        name = "📅 Semanal"
        if len(week_chunks) > 1:
            name = name + " (" + str(index + 1) + "/" + str(len(week_chunks)) + ")"
        embed.add_field(name=name, value=week_chunks[index], inline=False)
        embeds.append(embed)
    for index in range(len(month_chunks)):
        embed = discord.Embed(color=discord.Color.purple())
        if len(embeds) == 0:
            embed.title = "Actividad del Staff"
        name = "🗓️ Mensual"
        if len(month_chunks) > 1:
            name = name + " (" + str(index + 1) + "/" + str(len(month_chunks)) + ")"
        embed.add_field(name=name, value=month_chunks[index], inline=False)
        embeds.append(embed)
    return embeds


def build_single_activity_embeds(title, label, members, rows):
    chunks = split_lines_into_chunks(build_activity_lines(members, rows), 1000)
    embeds = []
    for index in range(len(chunks)):
        embed = discord.Embed(color=discord.Color.purple())
        if index == 0:
            embed.title = title
        name = label
        if len(chunks) > 1:
            name = name + " (" + str(index + 1) + "/" + str(len(chunks)) + ")"
        embed.add_field(name=name, value=chunks[index], inline=False)
        embeds.append(embed)
    return embeds


async def send_activity_embeds(channel, embeds):
    group = []
    group_length = 0
    for embed in embeds:
        if len(group) == 10 or group_length + len(embed) > 5000:
            await channel.send(embeds=group)
            group = []
            group_length = 0
        group.append(embed)
        group_length = group_length + len(embed)
    if len(group) > 0:
        await channel.send(embeds=group)


LOG_TITLES = {
    "Ticket creado": ("🎫", None),
    "Ticket reclamado": ("📈", discord.Color.purple()),
    "Ticket unclameado": ("📉", discord.Color.purple()),
    "Ticket cerrado": ("🔒", discord.Color.purple()),
    "Pruebas SS subidas": ("📎", discord.Color.blue()),
    "Pruebas bypass subidas": ("📎", discord.Color.blue()),
    "Resultado SS publicado": ("📝", discord.Color.green()),
    "Resultado bypass publicado": ("📝", discord.Color.green()),
    "Cooldown aplicado": ("⏳", discord.Color.purple()),
    "Cooldown removido": ("🧹", discord.Color.purple()),
    "Puntos bypass sumados": ("➕", discord.Color.orange()),
    "Puntos bypass restados": ("➖", discord.Color.orange()),
    "Puntos SS sumados": ("➕", discord.Color.orange()),
    "Puntos SS restados": ("➖", discord.Color.orange()),
}


async def send_log(ticket_text, user_id, action, extra):
    channel = get_staff_channel(config.LOG_CHANNEL_ID)
    log_emoji = LOG_TITLES[action][0]
    log_color = LOG_TITLES[action][1]
    embed = discord.Embed(title=log_emoji + " " + action, color=log_color)
    embed.add_field(name="Ticket", value=ticket_text, inline=True)
    embed.add_field(name="Usuario", value="<@" + str(user_id) + ">", inline=True)
    embed.add_field(name="Detalles", value=extra, inline=False)
    await channel.send(embed=embed)


async def send_transcript(channel, ticket, closed_by_id):
    messages = []
    async for message in channel.history(limit=None, oldest_first=True):
        messages.append(message)
    transcript_id = database.create_transcript(ticket["id"], channel.name, int(time.time()))
    filename = channel.name + "-" + str(transcript_id).zfill(4) + ".html"
    page = transcript.build_page(channel.name, messages)
    file = discord.File(io.BytesIO(page.encode("utf-8")), filename=filename)
    staff_channel = get_staff_channel(config.TRANSCRIPT_CHANNEL_ID)
    embed = discord.Embed(title="📄 Transcript " + filename, color=discord.Color.purple())
    embed.add_field(name="Usuario", value="<@" + str(ticket["user_id"]) + ">", inline=True)
    embed.add_field(name="Dificultad", value=DIFFICULTY_NAMES[ticket["difficulty"]], inline=True)
    embed.add_field(name="Cerrado por", value="<@" + str(closed_by_id) + ">", inline=True)
    embed.add_field(name="Mensajes", value=str(len(messages)), inline=True)
    await staff_channel.send(embed=embed, file=file)


async def get_ticket_message(channel):
    async for message in channel.history(limit=100):
        if len(message.embeds) > 0:
            if message.embeds[0].title is not None:
                if message.embeds[0].title.startswith("Ticket "):
                    return message
    return None


async def get_setup_message(channel):
    async for message in channel.history(limit=100):
        if len(message.embeds) > 0:
            if message.embeds[0].title == "SS Test Request":
                return message
    return None


async def get_bypass_setup_message(channel):
    async for message in channel.history(limit=100):
        if len(message.embeds) > 0:
            if message.embeds[0].title == "Bypass Request":
                return message
    return None


async def remove_cooldown_role(member):
    if member.get_role(config.COOLDOWN_ROLE_ID) is None:
        return
    cooldown_role = member.guild.get_role(config.COOLDOWN_ROLE_ID)
    await member.remove_roles(cooldown_role)


async def get_active_cooldown_message(member):
    seconds_left = get_cooldown_seconds_left(member.id)
    if seconds_left > 0:
        return "⏳ Tienes un cooldown activo. Tiempo restante: " + format_remaining(seconds_left) + "."
    if database.get_cooldown(member.id) is not None:
        database.delete_cooldown(member.id)
    await remove_cooldown_role(member)
    return None


async def expire_cooldown(user_id, seconds, cooldown_end):
    await asyncio.sleep(seconds)
    row = database.get_cooldown(user_id)
    if row is not None and row["cooldown_end"] > cooldown_end:
        return
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if member is not None:
            await remove_cooldown_role(member)
    database.delete_cooldown(user_id)


async def restore_cooldowns():
    now = int(time.time())
    rows = database.get_all_cooldowns()
    for row in rows:
        remaining = row["cooldown_end"] - now
        if remaining > 0:
            asyncio.create_task(expire_cooldown(row["user_id"], remaining, row["cooldown_end"]))
        else:
            await expire_cooldown(row["user_id"], 0, row["cooldown_end"])


async def handle_ss_request(interaction):
    member = interaction.user
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    open_ticket = database.get_open_ticket_by_user(member.id)
    if open_ticket is not None:
        await interaction.followup.send(
            "⚠️ Ya tienes un ticket abierto en otro lado. Debes esperar a que se cierre para abrir otro.",
            ephemeral=True,
        )
        return
    cooldown_message = await get_active_cooldown_message(member)
    if cooldown_message is not None:
        await interaction.followup.send(cooldown_message, ephemeral=True)
        return
    difficulty = get_difficulty_for_member(member)
    category = guild.get_channel(config.SS_TICKET_CATEGORY_ID)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for role_id in config.STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    channel = await guild.create_text_channel(
        name=member.name + "-" + difficulty,
        category=category,
        overwrites=overwrites,
    )
    now = int(time.time())
    database.create_ticket(member.id, channel.id, difficulty, now)
    await channel.send(
        content="<@&" + str(config.STAFF_ROLE_ID) + ">",
        embed=build_ticket_embed(member.id, member.name, difficulty, None),
        view=build_ticket_view(False),
        allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
    )
    await interaction.followup.send(
        "✅ " + member.mention + " Tu ticket de SS fue creado: " + channel.mention,
        ephemeral=True,
    )
    await send_log(channel.mention, member.id, "Ticket creado", "**Dificultad:** " + DIFFICULTY_NAMES[difficulty])


class BypassOpenForm(discord.ui.Modal, title="Datos del sistema"):
    def __init__(self, member):
        super().__init__()
        self.member = member
        self.os_name = discord.ui.Label(
            text="Sistema operativo",
            component=discord.ui.RadioGroup(
                options=[
                    discord.RadioGroupOption(label="Windows", value="windows"),
                    discord.RadioGroupOption(label="Linux", value="linux"),
                    discord.RadioGroupOption(label="Mac", value="mac"),
                ],
            ),
        )
        self.modified = discord.ui.Label(
            text="¿Es un sistema modificado?",
            component=discord.ui.RadioGroup(
                options=[
                    discord.RadioGroupOption(label="Sí", value="si"),
                    discord.RadioGroupOption(label="No", value="no"),
                ],
            ),
        )
        self.add_item(self.os_name)
        self.add_item(self.modified)

    async def on_submit(self, interaction):
        await create_bypass_ticket(
            interaction,
            self.member,
            self.os_name.component.value,
            self.modified.component.value,
        )


async def handle_bypass_request(interaction):
    member = interaction.user
    if database.is_bypass_banned(member.id) == True:
        await interaction.response.send_message(
            "No puedes hacer esto, estas baneado de hacer test de bypass",
            ephemeral=True,
        )
        return
    if config.BYPASS_TICKET_CATEGORY_ID == 0:
        await interaction.response.send_message(
            "⚠️ La categoría de tickets de bypass no está configurada en el .env.",
            ephemeral=True,
        )
        return
    open_ticket = database.get_open_ticket_by_user(member.id)
    if open_ticket is not None:
        await interaction.response.send_message(
            "⚠️ Ya tienes un ticket abierto en otro lado. Debes esperar a que se cierre para abrir otro.",
            ephemeral=True,
        )
        return
    cooldown_message = await get_active_cooldown_message(member)
    if cooldown_message is not None:
        await interaction.response.send_message(cooldown_message, ephemeral=True)
        return
    await interaction.response.send_modal(BypassOpenForm(member))


async def create_bypass_ticket(interaction, member, os_name, modified):
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    cooldown_message = await get_active_cooldown_message(member)
    if cooldown_message is not None:
        await interaction.followup.send(cooldown_message, ephemeral=True)
        return
    category = guild.get_channel(config.BYPASS_TICKET_CATEGORY_ID)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for role_id in config.STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    number = database.get_next_ticket_number()
    channel_name = member.name.lower() + "-" + os_name + "-" + str(number).zfill(4)
    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
    )
    now = int(time.time())
    database.create_ticket(member.id, channel.id, "bypass", now, os_name, modified)
    await channel.send(
        content="<@&" + str(config.STAFF_ROLE_ID) + ">",
        embed=build_ticket_embed(member.id, member.name, "bypass", None, os_name, modified),
        view=build_ticket_view(False),
        allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
    )
    await interaction.followup.send(
        "✅ " + member.mention + " Tu ticket de bypass fue creado: " + channel.mention,
        ephemeral=True,
    )
    await send_log(
        channel.mention,
        member.id,
        "Ticket creado",
        "**Dificultad:** Bypass\n**Sistema:** " + OS_NAMES[os_name] + "\n**Modificado:** " + MODIFIED_NAMES[modified],
    )


async def handle_claim(interaction):
    member = interaction.user
    if is_staff(member) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para reclamar tickets.",
            ephemeral=True,
        )
        return
    ticket = database.get_open_ticket_by_channel(interaction.channel_id)
    if ticket is None:
        await interaction.response.send_message("Este canal no es un ticket abierto.", ephemeral=True)
        return
    if ticket["claimed_by"] is not None:
        await interaction.response.send_message(
            "⚠️ Este ticket ya fue reclamado por <@" + str(ticket["claimed_by"]) + ">.",
            ephemeral=True,
        )
        return
    now = int(time.time())
    points = DIFFICULTY_POINTS[ticket["difficulty"]]
    database.claim_ticket(ticket["id"], member.id, now)
    database.add_activity(member.id, ticket["id"], ticket["difficulty"], points, now)
    await interaction.response.send_message(
        "✅ Ticket reclamado por " + member.mention + ". Puntos ganados: **" + str(points) + "**."
    )
    creator = await bot.fetch_user(ticket["user_id"])
    ticket_message = await get_ticket_message(interaction.channel)
    await ticket_message.edit(
        embed=build_ticket_embed(
            ticket["user_id"],
            creator.name,
            ticket["difficulty"],
            member.id,
            ticket["os_name"],
            ticket["modified"],
        ),
        view=build_ticket_view(True),
    )
    await send_log(
        interaction.channel.mention,
        ticket["user_id"],
        "Ticket reclamado",
        "**Staff:** <@" + str(member.id) + ">\n**Puntos sumados:** " + str(points),
    )


async def handle_close_button(interaction):
    member = interaction.user
    if is_staff(member) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para cerrar tickets.",
            ephemeral=True,
        )
        return
    ticket = database.get_open_ticket_by_channel(interaction.channel_id)
    if ticket is None:
        await interaction.response.send_message("Este canal no es un ticket abierto.", ephemeral=True)
        return
    now = int(time.time())
    database.close_ticket(ticket["id"], member.id, now)
    creator = interaction.guild.get_member(ticket["user_id"])
    if creator is not None:
        await remove_cooldown_role(creator)
    database.delete_cooldown(ticket["user_id"])
    await interaction.response.send_message("🔒 El ticket se cerrará en unos segundos.")
    await send_transcript(interaction.channel, ticket, member.id)
    await send_log(
        interaction.channel.mention,
        ticket["user_id"],
        "Ticket cerrado",
        "**Staff que cerró:** <@" + str(member.id) + ">\n**Cooldown aplicado:** No",
    )
    await asyncio.sleep(3)
    await interaction.channel.delete()


async def close_ticket_with_cooldown(guild, channel, ticket, member, staff_id, seconds):
    cooldown_end = int(time.time()) + seconds
    database.set_cooldown(ticket["user_id"], cooldown_end)
    cooldown_role = guild.get_role(config.COOLDOWN_ROLE_ID)
    if member is not None:
        if member.get_role(config.COOLDOWN_ROLE_ID) is None:
            await member.add_roles(cooldown_role)
    asyncio.create_task(expire_cooldown(ticket["user_id"], seconds, cooldown_end))
    database.close_ticket(ticket["id"], staff_id, int(time.time()))
    await send_transcript(channel, ticket, staff_id)
    await send_log(
        channel.mention,
        ticket["user_id"],
        "Ticket cerrado",
        "**Staff que cerró:** <@" + str(staff_id) + ">\n**Cooldown aplicado:** Sí (" + format_remaining(seconds) + ")",
    )
    await channel.send(
        "🔒 Ticket cerrado. Cooldown aplicado a <@" + str(ticket["user_id"]) + ">: "
        + format_remaining(seconds) + ". El canal se borrará en unos segundos."
    )
    result_sessions.pop(channel.id, None)
    await asyncio.sleep(3)
    await channel.delete()


async def publish_result(interaction, session):
    ticket = database.get_open_ticket_by_channel(interaction.channel_id)
    guild = interaction.guild
    channel = interaction.channel
    member = guild.get_member(ticket["user_id"])
    results_channel = get_staff_channel(config.RESULT_CHANNEL_ID)
    if results_channel is None:
        await interaction.response.send_message(
            "⚠️ No se encontró el canal de resultados de SS en el servidor de staff. "
            "Revisa RESULT_CHANNEL_ID en el .env (debe ser un canal del servidor de staff).",
            ephemeral=True,
        )
        return
    if get_normal_channel(config.SS_RESULT_DISCORD_PUBLIC) is None:
        await interaction.response.send_message(
            "⚠️ No se encontró el canal público de resultados de SS. "
            "Revisa SS_RESULT_DISCORD_PUBLIC en el .env (debe ser un canal del servidor).",
            ephemeral=True,
        )
        return
    current_level_name = get_test_level_name(get_highest_test_role_id(member))
    new_level_name = get_new_test_level_name(current_level_name, session)
    text = build_result_text(
        ticket,
        session["tester_id"],
        session["difficulty"],
        session["cheat"],
        session["bypass"],
        session["encontro"],
        session["notas"],
        session["cosas_encontradas"],
        session["razonamiento"],
        session["puntaje"],
        PASS_TEST_NAMES[session["pass_test"]],
    )
    await interaction.response.send_message(
        "✅ Resultado publicado. Queda pendiente la aprobación de un admin o superior.",
        ephemeral=True,
    )
    proof_files = []
    for attachment in session["proofs"]:
        proof_files.append(await attachment.to_file())
    if len(proof_files) > 0:
        result_message = await results_channel.send(content=text, files=proof_files)
    else:
        result_message = await results_channel.send(content=text)
    await result_message.add_reaction("✅")
    await result_message.add_reaction("❌")
    database.create_pending_result(
        result_message.id,
        results_channel.id,
        channel.id,
        ticket["user_id"],
        session["tester_id"],
        session["difficulty"],
        session["pass_test"],
        session.get("downgrade"),
        current_level_name,
        new_level_name,
        int(time.time()),
    )
    await send_log(
        channel.mention,
        ticket["user_id"],
        "Resultado SS publicado",
        "**Staff:** <@" + str(interaction.user.id) + ">\n"
        "**Dificultad:** " + DIFFICULTY_NAMES[ticket["difficulty"]] + "\n"
        "**Pasa la test:** " + PASS_TEST_NAMES[session["pass_test"]] + "\n"
        "**Estado:** Pendiente de aprobación",
    )
    seconds = get_result_cooldown_seconds(member)
    await close_ticket_with_cooldown(guild, channel, ticket, member, interaction.user.id, seconds)


async def apply_result_rank(pending):
    guild = get_normal_guild()
    if guild is None:
        return
    member = guild.get_member(pending["user_id"])
    if member is None:
        return
    if pending["pass_test"] == "si":
        test_role = guild.get_role(get_test_role_id(pending["difficulty"]))
        await member.add_roles(test_role)
    if pending["downgrade"] == "si":
        highest_role_id = get_highest_test_role_id(member)
        if highest_role_id != 0:
            await member.remove_roles(guild.get_role(highest_role_id))


async def send_public_result(user_id, current_level, new_level):
    if current_level == new_level:
        return
    public_channel = get_normal_channel(config.SS_RESULT_DISCORD_PUBLIC)
    if public_channel is None:
        return
    await public_channel.send(content=build_public_result_text(user_id, current_level, new_level))


async def handle_result_reaction(payload):
    if payload.guild_id != config.STAFF_GUILD_ID:
        return
    if payload.user_id == bot.user.id:
        return
    emoji = str(payload.emoji)
    if emoji != "✅" and emoji != "❌":
        return
    pending = database.get_pending_result_by_message(payload.message_id)
    if pending is None:
        return
    if pending["status"] != "pendiente":
        return
    normal_guild = get_normal_guild()
    if normal_guild is None:
        return
    staff_member = normal_guild.get_member(payload.user_id)
    if staff_member is None:
        return
    if is_admin(staff_member) == False:
        return
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return
    message = await channel.fetch_message(payload.message_id)
    now = int(time.time())
    if emoji == "✅":
        await apply_result_rank(pending)
        await send_public_result(pending["user_id"], pending["current_level"], pending["new_level"])
        database.set_pending_result_status(pending["id"], "aprobado", staff_member.id, now)
        rank_text = "Sin cambio de rango."
        if pending["current_level"] != pending["new_level"]:
            rank_text = "Rango aplicado: " + pending["current_level"] + " > " + pending["new_level"]
        approval_text = "\n\n✅ Aprobado por <@" + str(staff_member.id) + "> — " + rank_text
        await message.edit(content=message.content + approval_text)
        return
    database.set_pending_result_status(pending["id"], "rechazado", staff_member.id, now)
    approval_text = "\n\n❌ Rechazado por <@" + str(staff_member.id) + "> — No se aplicó ningún rango."
    await message.edit(content=message.content + approval_text)


async def publish_bypass_result(interaction, session):
    ticket = database.get_open_ticket_by_channel(interaction.channel_id)
    guild = interaction.guild
    channel = interaction.channel
    member = guild.get_member(ticket["user_id"])
    results_channel = get_normal_channel(config.BYPASS_RESULT_CHANNEL_ID)
    if results_channel is None:
        await interaction.response.send_message(
            "⚠️ No se encontró el canal de resultados de bypass en el servidor. "
            "Revisa BYPASS_RESULT_CHANNEL_ID en el .env (debe ser un canal del servidor).",
            ephemeral=True,
        )
        return
    user_text = "Anónimo"
    if session["anonymous_user"] == "no":
        user_text = "<@" + str(ticket["user_id"]) + ">"
    text = (
        "## Bypass Result\n"
        "[»] **User:** " + user_text + "\n"
        "[»] **SSER:** <@" + str(interaction.user.id) + ">\n"
        "[»] **Bypassed:** " + BYPASS_EMOJIS[session["bypassed"]] + "\n"
        "[»] **Sistema operativo:** " + OS_NAMES[ticket["os_name"]] + "\n"
        "[»] **Sistema modificado:** " + MODIFIED_NAMES[ticket["modified"]] + "\n"
        "[»] **Notas:**\n" + shorten_text(quote_text(session["notas"]), 1800)
    )
    proof_files = []
    for attachment in session["proofs"]:
        proof_files.append(await attachment.to_file())
    await interaction.response.send_message(
        "✅ Resultado publicado y ticket cerrado.",
        ephemeral=True,
    )
    await results_channel.send(content=text, files=proof_files)
    await send_log(
        channel.mention,
        ticket["user_id"],
        "Resultado bypass publicado",
        "**Staff:** <@" + str(interaction.user.id) + ">\n"
        "**Bypassed:** " + PASS_TEST_NAMES[session["bypassed"]] + "\n"
        "**Usuario anónimo:** " + ("Sí" if session["anonymous_user"] == "si" else "No"),
    )
    if session["bypassed"] == "si":
        database.add_bypass_point(ticket["user_id"])
    seconds = get_result_cooldown_seconds(member)
    await close_ticket_with_cooldown(guild, channel, ticket, member, interaction.user.id, seconds)


class ResultFormStepOne(discord.ui.Modal, title="Resultado del test (1/2)"):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.cheat = discord.ui.Label(
            text="Cheat",
            description="Ej: 420 clicker, xml custom",
            component=discord.ui.TextInput(placeholder="Cheats usados"),
        )
        self.bypass = discord.ui.Label(
            text="Bypass",
            component=discord.ui.TextInput(
                style=discord.TextStyle.paragraph,
                placeholder="Un método por línea",
            ),
        )
        self.encontro = discord.ui.Label(
            text="Encontró",
            component=discord.ui.TextInput(placeholder="Ej: xml"),
        )
        self.cosas_encontradas = discord.ui.Label(
            text="Cosas encontradas",
            component=discord.ui.TextInput(placeholder="Ej: 1/2"),
        )
        self.pass_test = discord.ui.Label(
            text="Pasa la test",
            component=discord.ui.RadioGroup(
                options=[
                    discord.RadioGroupOption(label="Sí", value="si"),
                    discord.RadioGroupOption(label="No", value="no"),
                ],
            ),
        )
        self.add_item(self.cheat)
        self.add_item(self.bypass)
        self.add_item(self.encontro)
        self.add_item(self.cosas_encontradas)
        self.add_item(self.pass_test)

    async def on_submit(self, interaction):
        self.session["cheat"] = self.cheat.component.value
        self.session["bypass"] = self.bypass.component.value
        self.session["encontro"] = self.encontro.component.value
        self.session["cosas_encontradas"] = self.cosas_encontradas.component.value
        self.session["pass_test"] = self.pass_test.component.value
        await interaction.response.send_message(
            "✅ Primera parte guardada. Pulsa **Continuar** para completar el resto del formulario.",
            view=build_result_continue_view(interaction.channel_id),
            ephemeral=True,
        )


class ResultFormStepTwo(discord.ui.Modal, title="Resultado del test (2/2)"):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.notas = discord.ui.Label(
            text="Notas",
            component=discord.ui.TextInput(style=discord.TextStyle.paragraph),
        )
        self.razonamiento = discord.ui.Label(
            text="Razonamiento",
            component=discord.ui.TextInput(style=discord.TextStyle.paragraph),
        )
        self.puntaje = discord.ui.Label(
            text="Puntaje",
            component=discord.ui.TextInput(placeholder="Ej: 3/10"),
        )
        self.add_item(self.notas)
        self.add_item(self.razonamiento)
        self.add_item(self.puntaje)
        if session["pass_test"] == "no":
            self.downgrade = discord.ui.Label(
                text="Downgrade",
                description="Le quita su rango de SS más alto",
                component=discord.ui.RadioGroup(
                    options=[
                        discord.RadioGroupOption(label="Sí", value="si"),
                        discord.RadioGroupOption(label="No", value="no"),
                    ],
                ),
            )
            self.add_item(self.downgrade)

    async def on_submit(self, interaction):
        self.session["notas"] = self.notas.component.value
        self.session["razonamiento"] = self.razonamiento.component.value
        self.session["puntaje"] = self.puntaje.component.value
        if self.session["pass_test"] == "no":
            self.session["downgrade"] = self.downgrade.component.value
        await publish_result(interaction, self.session)


async def handle_continue_result(interaction):
    if is_normal_staff(interaction.user) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para finalizar el test.",
            ephemeral=True,
        )
        return
    session = result_sessions.get(interaction.channel_id)
    if session is None:
        await interaction.response.send_message(
            "⚠️ No hay pruebas registradas en este ticket. Usa `/result`.",
            ephemeral=True,
        )
        return
    if session["tester_id"] != interaction.user.id:
        await interaction.response.send_message(
            "🚫 Solo quien ejecutó `/result` puede abrir este formulario.",
            ephemeral=True,
        )
        return
    if "cheat" not in session:
        await interaction.response.send_message(
            "⚠️ Primero completa la primera parte del formulario.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(ResultFormStepTwo(session))


async def handle_finish_result(interaction):
    if is_normal_staff(interaction.user) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para finalizar el test.",
            ephemeral=True,
        )
        return
    session = result_sessions.get(interaction.channel_id)
    if session is None:
        await interaction.response.send_message(
            "⚠️ No hay pruebas registradas en este ticket. Usa `/result`.",
            ephemeral=True,
        )
        return
    if session["tester_id"] != interaction.user.id:
        await interaction.response.send_message(
            "🚫 Solo quien ejecutó `/result` puede abrir este formulario.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(ResultFormStepOne(session))


class BypassResultForm(discord.ui.Modal, title="Resultado del bypass"):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.bypassed = discord.ui.Label(
            text="Bypassed",
            component=discord.ui.RadioGroup(
                options=[
                    discord.RadioGroupOption(label="Sí", value="si"),
                    discord.RadioGroupOption(label="No", value="no"),
                    discord.RadioGroupOption(label="Indefinido", value="indefinido"),
                ],
            ),
        )
        self.anonymous_user = discord.ui.Label(
            text="Usuario anónimo",
            component=discord.ui.RadioGroup(
                options=[
                    discord.RadioGroupOption(label="Sí", value="si"),
                    discord.RadioGroupOption(label="No", value="no"),
                ],
            ),
        )
        self.notas = discord.ui.Label(
            text="Notas",
            component=discord.ui.TextInput(style=discord.TextStyle.paragraph),
        )
        self.add_item(self.bypassed)
        self.add_item(self.anonymous_user)
        self.add_item(self.notas)

    async def on_submit(self, interaction):
        self.session["bypassed"] = self.bypassed.component.value
        self.session["anonymous_user"] = self.anonymous_user.component.value
        self.session["notas"] = self.notas.component.value
        await publish_bypass_result(interaction, self.session)


async def handle_finish_bypass_result(interaction):
    if is_normal_staff(interaction.user) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para finalizar el test.",
            ephemeral=True,
        )
        return
    session = result_sessions.get(interaction.channel_id)
    if session is None:
        await interaction.response.send_message(
            "⚠️ No hay pruebas registradas en este ticket. Usa `/result` y subí las fotos.",
            ephemeral=True,
        )
        return
    if session["tester_id"] != interaction.user.id:
        await interaction.response.send_message(
            "🚫 Solo quien ejecutó `/result` puede abrir este formulario.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(BypassResultForm(session))


@bot.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data["custom_id"]
    if custom_id == "ss_request_button":
        await handle_ss_request(interaction)
    if custom_id == "bypass_request_button":
        await handle_bypass_request(interaction)
    if custom_id == "claim_ticket_button":
        await handle_claim(interaction)
    if custom_id == "close_ticket_button":
        await handle_close_button(interaction)
    if custom_id.startswith("finish_result_"):
        await handle_finish_result(interaction)
    if custom_id.startswith("continue_result_"):
        await handle_continue_result(interaction)
    if custom_id.startswith("finish_bypass_"):
        await handle_finish_bypass_result(interaction)


@bot.event
async def on_raw_reaction_add(payload):
    await handle_result_reaction(payload)


@bot.event
async def setup_hook():
    global_commands = await bot.tree.sync()
    print("Comandos globales sincronizados: " + str(len(global_commands)))
    synced = await bot.tree.sync(guild=discord.Object(id=config.GUILD_ID))
    print("Comandos sincronizados en el servidor normal: " + str(len(synced)))
    for command in synced:
        print("  /" + command.name)
    synced = await bot.tree.sync(guild=discord.Object(id=config.STAFF_GUILD_ID))
    print("Comandos sincronizados en el servidor de staff: " + str(len(synced)))
    for command in synced:
        print("  /" + command.name)


@bot.event
async def on_ready():
    global cooldowns_restored
    if cooldowns_restored == True:
        return
    cooldowns_restored = True
    await restore_cooldowns()
    daily_activity_task.start()
    weekly_activity_task.start()
    monthly_activity_task.start()
    print("Bot conectado como " + str(bot.user))


@bot.tree.command(name="setupss", description="Publica el panel de SS Request.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def setupss_command(interaction):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if interaction.channel.id != config.SS_GUIDE_CHANNEL_ID:
        await send_ephemeral(interaction, "🚫 Este comando solo funciona en el canal de guía de SS.")
        return
    existing = await get_setup_message(interaction.channel)
    if existing is not None:
        await send_ephemeral(interaction, "⚠️ Ya hay un embed de SS Request publicado en este canal.")
        return
    await interaction.response.send_message(embed=build_setup_embed(), view=build_setup_view())
    category = interaction.guild.get_channel(config.SS_TICKET_CATEGORY_ID)
    await interaction.followup.send(
        "✅ Embed publicado. Los tickets se crearán en la categoría **" + category.name + "**.",
        ephemeral=True,
    )


@bot.tree.command(name="setupbypass", description="Publica el panel de Bypass Request.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def setupbypass_command(interaction):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if config.BYPASS_CHANNEL_ID == 0:
        await send_ephemeral(interaction, "⚠️ El canal de bypass no está configurado en el .env.")
        return
    if config.BYPASS_TICKET_CATEGORY_ID == 0:
        await send_ephemeral(interaction, "⚠️ La categoría de tickets de bypass no está configurada en el .env.")
        return
    if interaction.channel.id != config.BYPASS_CHANNEL_ID:
        await send_ephemeral(interaction, "🚫 Este comando solo funciona en el canal de bypass.")
        return
    existing = await get_bypass_setup_message(interaction.channel)
    if existing is not None:
        await send_ephemeral(interaction, "⚠️ Ya hay un embed de Bypass Request publicado en este canal.")
        return
    await interaction.response.send_message(embed=build_bypass_setup_embed(), view=build_bypass_setup_view())
    category = interaction.guild.get_channel(config.BYPASS_TICKET_CATEGORY_ID)
    await interaction.followup.send(
        "✅ Embed publicado. Los tickets se crearán en la categoría **" + category.name + "**.",
        ephemeral=True,
    )


@bot.tree.command(name="bypassban", description="Banea a un usuario de abrir tickets de bypass.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(usuario="Usuario a banear de los tickets de bypass")
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def bypassban_command(interaction, usuario: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    if database.is_bypass_banned(user_id) == True:
        await send_ephemeral(interaction, "⚠️ Ese usuario ya está baneado de los tickets de bypass.")
        return
    database.add_bypass_ban(user_id)
    await interaction.response.send_message("✅ Usuario <@" + str(user_id) + "> baneado de los tickets de bypass.")


@bot.tree.command(name="bypassunban", description="Quita el ban de bypass a un usuario.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(usuario="Usuario al que se le quita el ban de bypass")
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def bypassunban_command(interaction, usuario: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    if database.is_bypass_banned(user_id) == False:
        await send_ephemeral(interaction, "⚠️ Ese usuario no está baneado de los tickets de bypass.")
        return
    database.remove_bypass_ban(user_id)
    await interaction.response.send_message("✅ Usuario <@" + str(user_id) + "> desbaneado de los tickets de bypass.")


@bot.tree.command(name="bypasstop", description="Muestra el top de los mejores bypassers.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def bypasstop_command(interaction):
    embed = build_bypasstop_embed(get_normal_guild(), interaction.user.id)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="points", description="Administra los puntos de bypass y de SS.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(
    tipo="Tipo de puntos",
    accion="Sumar o restar puntos",
    usuario="Usuario al que se le aplican los puntos",
    cantidad="Cantidad de puntos",
)
@discord.app_commands.choices(
    tipo=[
        discord.app_commands.Choice(name="bypass", value="bypass"),
        discord.app_commands.Choice(name="ss", value="ss"),
    ],
    accion=[
        discord.app_commands.Choice(name="add", value="add"),
        discord.app_commands.Choice(name="remove", value="remove"),
    ],
)
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def points_command(interaction, tipo: str, accion: str, usuario: str, cantidad: int):
    if can_manage_normal_points(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    if cantidad <= 0:
        await send_ephemeral(interaction, "⚠️ La cantidad de puntos debe ser mayor a 0.")
        return
    if tipo == "bypass":
        await handle_points_bypass(interaction, int(usuario), cantidad, accion)
        return
    await handle_points_ss(interaction, int(usuario), cantidad, accion)


async def handle_points_bypass(interaction, user_id, amount, action):
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    if action == "add":
        database.add_bypass_points(user_id, amount)
        await interaction.response.send_message(
            "✅ Se sumaron **" + str(amount) + "** puntos de bypass a " + member.mention + "."
        )
        await send_log(
            "Ninguno",
            user_id,
            "Puntos bypass sumados",
            "**Staff:** <@" + str(interaction.user.id) + ">\n**Puntos:** +" + str(amount),
        )
        return
    database.remove_bypass_points(user_id, amount)
    await interaction.response.send_message(
        "✅ Se restaron **" + str(amount) + "** puntos de bypass a " + member.mention + "."
    )
    await send_log(
        "Ninguno",
        user_id,
        "Puntos bypass restados",
        "**Staff:** <@" + str(interaction.user.id) + ">\n**Puntos:** -" + str(amount),
    )


async def handle_points_ss(interaction, user_id, amount, action):
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    if is_staff(member) == False:
        await send_ephemeral(interaction, "⚠️ Ese usuario no es staff.")
        return
    now = int(time.time())
    if action == "add":
        database.add_activity_adjustment(member.id, amount, now)
        await interaction.response.send_message(
            "✅ Se sumaron **" + str(amount) + "** puntos de SS a " + member.mention + "."
        )
        await send_log(
            "Ninguno",
            member.id,
            "Puntos SS sumados",
            "**Staff:** <@" + str(interaction.user.id) + ">\n**Puntos:** +" + str(amount),
        )
        return
    database.add_activity_adjustment(member.id, amount * -1, now)
    await interaction.response.send_message(
        "✅ Se restaron **" + str(amount) + "** puntos de SS a " + member.mention + "."
    )
    await send_log(
        "Ninguno",
        member.id,
        "Puntos SS restados",
        "**Staff:** <@" + str(interaction.user.id) + ">\n**Puntos:** -" + str(amount),
    )


class ResultProofsForm(discord.ui.Modal, title="Pruebas del test"):
    def __init__(self, ticket):
        super().__init__()
        self.ticket = ticket
        minimum = 0
        required = False
        if ticket["difficulty"] == "bypass":
            minimum = 1
            required = True
        self.proofs = discord.ui.Label(
            text="Pruebas",
            description="Subí las fotos de las pruebas (hasta 10)",
            component=discord.ui.FileUpload(max_values=10, min_values=minimum, required=required),
        )
        self.add_item(self.proofs)

    async def on_submit(self, interaction):
        await save_result_proofs(interaction, self.ticket, self.proofs.component.values)


async def save_result_proofs(interaction, ticket, proofs):
    channel = interaction.channel
    proof_files = []
    for attachment in proofs:
        proof_files.append(await attachment.to_file())
    if ticket["difficulty"] == "bypass":
        result_sessions[channel.id] = {
            "ticket_id": ticket["id"],
            "proofs": proofs,
            "tester_id": interaction.user.id,
            "kind": "bypass",
        }
        await send_ephemeral(interaction, "✅ Pruebas registradas.")
        await channel.send(
            content="📎 **Pruebas subidas:** fotos de las pruebas\n<@" + str(interaction.user.id) + ">",
            files=proof_files,
            view=build_bypass_proofs_view(channel.id),
        )
        await send_log(
            channel.mention,
            ticket["user_id"],
            "Pruebas bypass subidas",
            "**Staff:** <@" + str(interaction.user.id) + ">\n"
            "**Cantidad de imágenes:** " + str(len(proofs)),
        )
        return
    result_sessions[channel.id] = {
        "ticket_id": ticket["id"],
        "proofs": proofs,
        "tester_id": interaction.user.id,
        "difficulty": ticket["difficulty"],
        "kind": "ss",
    }
    if len(proofs) == 0:
        await send_ephemeral(interaction, "✅ Test registrado sin pruebas.")
        await channel.send(
            content="🧪 **Test registrado sin pruebas**\n<@" + str(interaction.user.id) + ">",
            view=build_result_proofs_view(channel.id),
        )
    else:
        await send_ephemeral(interaction, "✅ Pruebas registradas.")
        await channel.send(
            content="📎 **Pruebas subidas:** fotos de las pruebas\n<@" + str(interaction.user.id) + ">",
            files=proof_files,
            view=build_result_proofs_view(channel.id),
        )
    await send_log(
        channel.mention,
        ticket["user_id"],
        "Pruebas SS subidas",
        "**Staff:** <@" + str(interaction.user.id) + ">\n"
        "**Dificultad:** " + DIFFICULTY_NAMES[ticket["difficulty"]] + "\n"
        "**Cantidad de imágenes:** " + str(len(proofs)),
    )


@bot.tree.command(name="result", description="Registra las pruebas del test y abre el formulario del resultado.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def result_command(interaction):
    if is_normal_staff(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    ticket = database.get_open_ticket_by_channel(interaction.channel.id)
    if ticket is None:
        await send_ephemeral(interaction, "Este canal no es un ticket abierto.")
        return
    if ticket["difficulty"] == "bypass":
        if config.BYPASS_RESULT_CHANNEL_ID == 0:
            await send_ephemeral(interaction, "⚠️ El canal de resultados de bypass no está configurado en el .env.")
            return
        if get_normal_channel(config.BYPASS_RESULT_CHANNEL_ID) is None:
            await send_ephemeral(
                interaction,
                "⚠️ No se encontró el canal de resultados de bypass en el servidor. "
                "Revisa BYPASS_RESULT_CHANNEL_ID en el .env.",
            )
            return
    else:
        if config.RESULT_CHANNEL_ID == 0:
            await send_ephemeral(interaction, "⚠️ El canal de resultados no está configurado en el .env.")
            return
        if get_staff_channel(config.RESULT_CHANNEL_ID) is None:
            await send_ephemeral(
                interaction,
                "⚠️ No se encontró el canal de resultados de SS en el servidor de staff. "
                "Revisa RESULT_CHANNEL_ID en el .env.",
            )
            return
        if config.SS_RESULT_DISCORD_PUBLIC == 0:
            await send_ephemeral(interaction, "⚠️ El canal público de resultados de SS no está configurado en el .env.")
            return
        if get_normal_channel(config.SS_RESULT_DISCORD_PUBLIC) is None:
            await send_ephemeral(
                interaction,
                "⚠️ No se encontró el canal público de resultados de SS en el servidor. "
                "Revisa SS_RESULT_DISCORD_PUBLIC en el .env.",
            )
            return
    await interaction.response.send_modal(ResultProofsForm(ticket))


@bot.tree.command(name="unclaim", description="Retira el claim del ticket actual.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def unclaim_command(interaction):
    if is_normal_staff(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    ticket = database.get_open_ticket_by_channel(interaction.channel.id)
    if ticket is None:
        await send_ephemeral(interaction, "Este canal no es un ticket abierto.")
        return
    if ticket["claimed_by"] is None:
        await send_ephemeral(interaction, "⚠️ Este ticket no tiene un claim activo.")
        return
    old_claim = ticket["claimed_by"]
    points = DIFFICULTY_POINTS[ticket["difficulty"]]
    database.unclaim_ticket(ticket["id"])
    database.remove_activity(ticket["id"])
    creator = await bot.fetch_user(ticket["user_id"])
    ticket_message = await get_ticket_message(interaction.channel)
    await ticket_message.edit(
        embed=build_ticket_embed(
            ticket["user_id"],
            creator.name,
            ticket["difficulty"],
            None,
            ticket["os_name"],
            ticket["modified"],
        ),
        view=build_ticket_view(False),
    )
    await interaction.response.send_message(
        "✅ Claim retirado a <@" + str(old_claim) + ">. Puntos restados: **" + str(points) + "**."
    )
    await send_log(
        interaction.channel.mention,
        ticket["user_id"],
        "Ticket unclameado",
        "**Staff que reclamó:** <@" + str(old_claim) + ">\n"
        "**Staff que ejecutó el unclaim:** <@" + str(interaction.user.id) + ">\n"
        "**Puntos restados:** " + str(points),
    )


@bot.tree.command(name="remember", description="Envía un recordatorio por MD al creador del ticket.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def remember_command(interaction):
    if is_normal_staff(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    ticket = database.get_open_ticket_by_channel(interaction.channel.id)
    if ticket is None:
        await send_ephemeral(interaction, "Este canal no es un ticket abierto.")
        return
    user = await bot.fetch_user(ticket["user_id"])
    thumbnail_name = os.path.basename(config.REMINDER_THUMBNAIL)
    embed = build_reminder_embed(thumbnail_name, interaction.channel.id)
    await user.send(embed=embed, file=discord.File(config.REMINDER_THUMBNAIL))
    await interaction.response.send_message("📩 Recordatorio enviado por MD a " + user.mention + ".")


@bot.tree.command(name="cooldown", description="Muestra el cooldown de un usuario.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(usuario="Usuario a consultar (por defecto sos vos)")
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def cooldown_command(interaction, usuario: str = None):
    if usuario is None:
        user_id = interaction.user.id
    else:
        if usuario.isdigit() == False:
            await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
            return
        user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        embed = discord.Embed(title="Cooldown", description="No se encontró ese usuario en el servidor.", color=discord.Color.purple())
        await send_ephemeral(interaction, embed=embed)
        return
    row = database.get_cooldown(user_id)
    seconds_left = get_cooldown_seconds_left(user_id)
    embed = discord.Embed(title="Cooldown de " + member.name, color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Usuario", value=member.mention, inline=False)
    if row is None or seconds_left == 0:
        embed.add_field(name="Estado", value="Sin cooldown activo", inline=False)
        await interaction.response.send_message(embed=embed)
        return
    embed.add_field(name="Estado", value="Cooldown activo", inline=False)
    embed.add_field(name="⏳ Tiempo restante", value=format_remaining(seconds_left), inline=True)
    embed.add_field(name="📅 Termina", value=format_date(row["cooldown_end"]), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="removecooldown", description="Quita el cooldown de un usuario.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(usuario="Usuario al que se le quita el cooldown")
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def removecooldown_command(interaction, usuario: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        embed = discord.Embed(title="Quitar cooldown", description="No se encontró ese usuario en el servidor.", color=discord.Color.purple())
        await send_ephemeral(interaction, embed=embed)
        return
    row = database.get_cooldown(user_id)
    if row is None:
        embed = discord.Embed(title="Quitar cooldown", color=discord.Color.purple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Usuario", value=member.mention, inline=False)
        embed.add_field(name="Estado", value="No tenía un cooldown activo", inline=False)
        await interaction.response.send_message(embed=embed)
        return
    old_end = row["cooldown_end"]
    await remove_cooldown_role(member)
    database.delete_cooldown(user_id)
    embed = discord.Embed(title="Quitar cooldown", color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Usuario", value=member.mention, inline=False)
    embed.add_field(name="Estado", value="Cooldown removido", inline=False)
    embed.add_field(name="📅 Terminaba", value=format_date(old_end), inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(
        "Ninguno",
        user_id,
        "Cooldown removido",
        "**Staff:** <@" + str(interaction.user.id) + ">\n**Terminaba:** " + format_date(old_end),
    )


@bot.tree.command(name="addcooldown", description="Aplica un cooldown a un usuario.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(
    usuario="Usuario al que se le aplica el cooldown",
    duracion="Duración del cooldown: 7d, 12h, 30m",
)
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def addcooldown_command(interaction, usuario: str, duracion: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        embed = discord.Embed(title="Aplicar cooldown", description="No se encontró ese usuario en el servidor.", color=discord.Color.purple())
        await send_ephemeral(interaction, embed=embed)
        return
    seconds = parse_duration(duracion.split())
    if seconds <= 0:
        await send_ephemeral(interaction, "La duración no es válida. Usá formatos como `7d`, `12h` o `30m`.")
        return
    cooldown_end = int(time.time()) + seconds
    database.set_cooldown(user_id, cooldown_end)
    if member.get_role(config.COOLDOWN_ROLE_ID) is None:
        cooldown_role = interaction.guild.get_role(config.COOLDOWN_ROLE_ID)
        await member.add_roles(cooldown_role)
    asyncio.create_task(expire_cooldown(user_id, seconds, cooldown_end))
    embed = discord.Embed(title="Aplicar cooldown", color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Usuario", value=member.mention, inline=False)
    embed.add_field(name="⏳ Tiempo aplicado", value=format_remaining(seconds), inline=True)
    embed.add_field(name="📅 Termina", value=format_date(cooldown_end), inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(
        "Ninguno",
        user_id,
        "Cooldown aplicado",
        "**Staff:** <@" + str(interaction.user.id) + ">\n**Duración:** " + format_remaining(seconds),
    )


async def send_activity_report(channel):
    guild = get_normal_guild()
    week_rows = database.get_activity_points_since(get_week_start())
    month_rows = database.get_activity_points_since(get_month_start())
    members = get_staff_members(guild)
    embeds = build_activity_embeds(members, week_rows, month_rows)
    await send_activity_embeds(channel, embeds)


@tasks.loop(time=ACTIVITY_SCHEDULE)
async def daily_activity_task():
    channel = get_staff_channel(config.ACTIVITY_CHANNEL_ID)
    await send_activity_report(channel)


async def run_weekly_inactivity_check():
    guild = get_normal_guild()
    if guild is None:
        return
    rows = database.get_activity_points_since(get_week_start())
    points_by_staff = {}
    for row in rows:
        points_by_staff[row["staff_id"]] = row["total"]
    staff_role = guild.get_role(config.STAFF_ROLE_ID)
    if staff_role is None:
        return
    for member in staff_role.members:
        if member.bot == True:
            continue
        if database.is_activity_bypassed(member.id) == True:
            continue
        points = 0
        if member.id in points_by_staff:
            points = points_by_staff[member.id]
        if points < MIN_WEEKLY_POINTS:
            await add_strikes(
                member.id,
                1,
                "Inactividad semanal (menos de " + str(MIN_WEEKLY_POINTS) + " puntos)",
            )


@tasks.loop(time=ACTIVITY_SCHEDULE)
async def weekly_activity_task():
    now = datetime.now(timezone.utc)
    if now.weekday() != 6:
        return
    channel = get_staff_channel(config.WEEKLY_ACTIVITY_CHANNEL_ID)
    if channel is not None:
        guild = get_normal_guild()
        members = get_staff_members(guild)
        rows = database.get_activity_points_since(get_week_start())
        embeds = build_single_activity_embeds("Actividad semanal final", "📅 Semanal", members, rows)
        await send_activity_embeds(channel, embeds)
    await run_weekly_inactivity_check()


@tasks.loop(time=ACTIVITY_SCHEDULE)
async def monthly_activity_task():
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(days=1)
    if tomorrow.month == now.month:
        return
    channel = get_staff_channel(config.MONTHLY_ACTIVITY_CHANNEL_ID)
    if channel is None:
        return
    guild = get_normal_guild()
    members = get_staff_members(guild)
    rows = database.get_activity_points_since(get_month_start())
    embeds = build_single_activity_embeds("Actividad mensual final", "🗓️ Mensual", members, rows)
    await send_activity_embeds(channel, embeds)


@bot.tree.command(name="actividad", description="Muestra la actividad del staff.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
async def activity_command(interaction):
    if interaction.guild_id != config.STAFF_GUILD_ID:
        if is_normal_staff(interaction.user) == False:
            await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
            return
    await interaction.response.defer(ephemeral=True)
    await send_activity_report(interaction.channel)
    await interaction.followup.send("✅ Reporte de actividad enviado.", ephemeral=True)


@bot.tree.command(name="clear", description="Elimina una cantidad de mensajes del canal actual.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(numero="Cantidad de mensajes a eliminar (máximo 50)")
async def clear_command(interaction, numero: int):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if numero < 1:
        await send_ephemeral(interaction, "⚠️ El número tiene que ser mayor a 0.")
        return
    if numero > 50:
        await send_ephemeral(interaction, "⚠️ El máximo es 50 mensajes.")
        return
    deleted = await interaction.channel.purge(limit=numero)
    await send_ephemeral(interaction, "🧹 Se eliminaron " + str(len(deleted)) + " mensajes.")


@bot.tree.command(name="warn", description="Suma warns a un staff. Cada 5 warns es un strike.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(
    usuario="Staff al que se le suman los warns",
    cantidad="Cantidad de warns a sumar",
    razon="Razón del warn",
)
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def warn_command(interaction, usuario: str, cantidad: int, razon: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    if cantidad <= 0:
        await send_ephemeral(interaction, "⚠️ La cantidad de warns debe ser mayor a 0.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    await add_warns(user_id, cantidad, razon)
    row = database.get_staff_counts(user_id)
    await interaction.response.send_message(
        "✅ Se sumaron **" + str(cantidad) + "** warns a " + member.mention + ".\n"
        "**Warns:** " + str(row["warns"]) + "/" + str(WARNS_PER_STRIKE) + "\n"
        "**Strikes:** " + format_strike_count(row["strikes"])
    )


@bot.tree.command(name="strike", description="Suma un strike a un staff.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(
    usuario="Staff al que se le suma el strike",
    razon="Razón del strike",
)
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def strike_command(interaction, usuario: str, razon: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    strikes = await add_strikes(user_id, 1, razon)
    text = (
        "✅ Se sumó **1** strike a " + member.mention + ".\n"
        "**Strikes:** " + format_strike_count(strikes)
    )
    if strikes >= MAX_STRIKES:
        text = text + "\n🚪 Fue expulsado del staff."
    await interaction.response.send_message(text)


@bot.tree.command(name="strikeremove", description="Quita strikes a un staff.")
@discord.app_commands.guilds(*COMMAND_GUILDS)
@discord.app_commands.describe(
    usuario="Staff al que se le quitan los strikes",
    cantidad="Cantidad de strikes a quitar",
)
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def strikeremove_command(interaction, usuario: str, cantidad: int):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    if cantidad <= 0:
        await send_ephemeral(interaction, "⚠️ La cantidad tiene que ser mayor a 0.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    row = database.get_staff_counts(user_id)
    warns = 0
    strikes = 0
    if row is not None:
        warns = row["warns"]
        strikes = row["strikes"]
    strikes = strikes - cantidad
    if strikes < 0:
        strikes = 0
    database.set_staff_counts(user_id, warns, strikes)
    database.add_strike_history(
        user_id,
        "strike_remove",
        cantidad,
        "Quitados por <@" + str(interaction.user.id) + ">",
        int(time.time()),
    )
    await interaction.response.send_message(
        "✅ Se quitaron **" + str(cantidad) + "** strikes a " + member.mention + ".\n"
        "**Strikes:** " + format_strike_count(strikes)
    )


bypass_activity_group = discord.app_commands.Group(
    name="bypassactivity",
    description="Excluye staff de los strikes por inactividad.",
)


@bypass_activity_group.command(name="add", description="Excluye a un staff de los strikes por inactividad.")
@discord.app_commands.describe(usuario="Staff a excluir")
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def bypass_activity_add(interaction, usuario: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    database.add_activity_bypass(user_id)
    await interaction.response.send_message(
        "✅ " + member.mention + " ya no recibe strikes por inactividad."
    )


@bypass_activity_group.command(name="remove", description="Vuelve a incluir a un staff en los strikes por inactividad.")
@discord.app_commands.describe(usuario="Staff a incluir de nuevo")
@discord.app_commands.autocomplete(usuario=member_autocomplete)
async def bypass_activity_remove(interaction, usuario: str):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    if usuario.isdigit() == False:
        await send_ephemeral(interaction, "⚠️ Elegí un usuario de la lista.")
        return
    user_id = int(usuario)
    member = get_normal_member(user_id)
    if member is None:
        await send_ephemeral(interaction, "⚠️ No se encontró ese usuario en el servidor.")
        return
    database.remove_activity_bypass(user_id)
    await interaction.response.send_message(
        "✅ " + member.mention + " vuelve a recibir strikes por inactividad."
    )


@bypass_activity_group.command(name="list", description="Muestra los staff exentos de los strikes por inactividad.")
async def bypass_activity_list(interaction):
    if is_normal_admin(interaction.user) == False:
        await send_ephemeral(interaction, "🚫 No estás autorizado para usar este comando.")
        return
    rows = database.get_activity_bypass_list()
    if len(rows) == 0:
        await send_ephemeral(interaction, "No hay staff exento de inactividad.")
        return
    lines = []
    for row in rows:
        lines.append("<@" + str(row["user_id"]) + ">")
    embed = discord.Embed(
        title="Staff exento de inactividad",
        description=shorten_text("\n".join(lines), 1900),
        color=discord.Color.purple(),
    )
    await interaction.response.send_message(embed=embed)


bot.tree.add_command(bypass_activity_group, guilds=COMMAND_GUILDS)


database.init_database()

if __name__ == "__main__":
    bot.run(config.TOKEN)
