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

bot = commands.Bot(command_prefix="!", intents=intents)

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

RESULT_COLORS = {
    "basic": discord.Color.green(),
    "medium": discord.Color.blue(),
    "advanced": discord.Color.purple(),
}

RESULT_DIFFICULTY_NAMES = {
    "basic": "Basic",
    "medium": "Medium",
    "advanced": "Advanced",
}

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


async def send_private(ctx, text=None, embed=None):
    if ctx.interaction is not None:
        if ctx.interaction.response.is_done():
            await ctx.interaction.followup.send(text, embed=embed, ephemeral=True)
        else:
            await ctx.interaction.response.send_message(text, embed=embed, ephemeral=True)
        return
    await ctx.send(text, embed=embed, delete_after=10)


async def send_error(ctx, text=None, embed=None):
    await send_private(ctx, text, embed)


async def defer_if_interaction(ctx):
    if ctx.interaction is not None:
        await ctx.interaction.response.defer(ephemeral=True)


def is_staff(member):
    return has_any_role(member, config.STAFF_ROLE_IDS)


def is_admin(member):
    return has_any_role(member, config.ADMIN_ROLE_IDS)


def can_manage_points(member):
    return has_any_role(member, config.POINTS_ROLE_IDS)


def parse_user_id(text):
    cleaned = text.replace("<@", "").replace(">", "").replace("!", "")
    return int(cleaned)


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


def get_message_images(message):
    images = []
    for attachment in message.attachments:
        if attachment.content_type is not None:
            if attachment.content_type.startswith("image/"):
                images.append(attachment)
    return images


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
    seconds = parse_duration(config.TEST_COOLDOWN_TEXT.split())
    if member.get_role(config.VIP_BOOSTER_ROLE_ID) is not None:
        seconds = int(seconds * config.VIP_BOOSTER_MULTIPLIER)
    return seconds


def get_test_role_id(difficulty):
    if difficulty == "advanced":
        return config.SS_ADVANCED_ROLE_ID
    if difficulty == "medium":
        return config.SS_MEDIUM_ROLE_ID
    return config.SS_BASIC_ROLE_ID


def get_highest_test_role_id(member):
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


def build_result_embed(ticket, member, tester_id, difficulty, cheat, bypass_text, encontro, notas, cosas_encontradas, razonamiento, puntaje, pass_test_name):
    difficulty_name = RESULT_DIFFICULTY_NAMES[difficulty]
    embed = discord.Embed(
        title="Resultado del test · " + difficulty_name,
        color=RESULT_COLORS[difficulty],
    )
    embed.set_author(name=member.name, icon_url=member.display_avatar.url)
    embed.description = (
        "**Tag User:** <@" + str(ticket["user_id"]) + ">\n"
        "**Tester:** <@" + str(tester_id) + ">\n"
        "**Apply:** " + difficulty_name + "\n"
        "**Puntaje:** " + shorten_text(puntaje, 100) + "\n"
        "**Pasa la test:** " + pass_test_name
    )
    embed.add_field(name="- Cheat", value=shorten_text(quote_text(cheat), 1024), inline=False)
    embed.add_field(name="- Bypass", value=shorten_text(quote_text(bypass_text), 1024), inline=False)
    embed.add_field(name="- Encontró", value=shorten_text(quote_text(encontro), 1024), inline=True)
    embed.add_field(name="- Cosas encontradas", value=shorten_text(quote_text(cosas_encontradas), 1024), inline=True)
    embed.add_field(name="- Notas", value=shorten_text(quote_text(notas), 1024), inline=False)
    embed.add_field(name="- Razonamiento", value=shorten_text(quote_text(razonamiento), 1024), inline=False)
    embed.set_footer(text="Resultado registrado el " + format_date(int(time.time())))
    return embed


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


async def expire_cooldown(user_id, seconds):
    await asyncio.sleep(seconds)
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
            asyncio.create_task(expire_cooldown(row["user_id"], remaining))
        else:
            await expire_cooldown(row["user_id"], 0)


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
    cooldown_role = member.get_role(config.COOLDOWN_ROLE_ID)
    if cooldown_role is None:
        if database.get_cooldown(member.id) is not None:
            database.delete_cooldown(member.id)
    else:
        seconds_left = get_cooldown_seconds_left(member.id)
        if seconds_left > 0:
            text = "⏳ Tienes un cooldown activo. Tiempo restante: " + format_remaining(seconds_left) + "."
        else:
            text = "⏳ Tienes un cooldown activo."
        await interaction.followup.send(text, ephemeral=True)
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
    await interaction.response.send_modal(BypassOpenForm(member))


async def create_bypass_ticket(interaction, member, os_name, modified):
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
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
    if member.get_role(config.COOLDOWN_ROLE_ID) is None:
        await member.add_roles(cooldown_role)
    asyncio.create_task(expire_cooldown(ticket["user_id"], seconds))
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
    public_channel = get_normal_channel(config.SS_RESULT_DISCORD_PUBLIC)
    if public_channel is None:
        await interaction.response.send_message(
            "⚠️ No se encontró el canal público de resultados de SS. "
            "Revisa SS_RESULT_DISCORD_PUBLIC en el .env (debe ser un canal del servidor).",
            ephemeral=True,
        )
        return
    current_level_name = get_test_level_name(get_highest_test_role_id(member))
    new_level_name = get_new_test_level_name(current_level_name, session)
    embed = build_result_embed(
        ticket,
        member,
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
        "✅ Resultado publicado y ticket cerrado.",
        ephemeral=True,
    )
    result_message = await results_channel.send(embed=embed)
    if len(session["proofs"]) > 0:
        proof_files = []
        for attachment in session["proofs"]:
            proof_files.append(await attachment.to_file())
        await results_channel.send(
            content="📎 Pruebas del test de <@" + str(ticket["user_id"]) + ">",
            files=proof_files,
            reference=result_message,
        )
    await send_log(
        channel.mention,
        ticket["user_id"],
        "Resultado SS publicado",
        "**Staff:** <@" + str(interaction.user.id) + ">\n"
        "**Dificultad:** " + DIFFICULTY_NAMES[ticket["difficulty"]] + "\n"
        "**Pasa la test:** " + PASS_TEST_NAMES[session["pass_test"]],
    )
    if session["pass_test"] == "si":
        test_role = guild.get_role(get_test_role_id(session["difficulty"]))
        await member.add_roles(test_role)
    if session.get("downgrade") == "si":
        highest_role_id = get_highest_test_role_id(member)
        if highest_role_id != 0:
            await member.remove_roles(guild.get_role(highest_role_id))
    if new_level_name != current_level_name:
        await public_channel.send(
            content=build_public_result_text(ticket["user_id"], current_level_name, new_level_name)
        )
    seconds = get_result_cooldown_seconds(member)
    await close_ticket_with_cooldown(guild, channel, ticket, member, interaction.user.id, seconds)


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
    if is_staff(interaction.user) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para finalizar el test.",
            ephemeral=True,
        )
        return
    session = result_sessions.get(interaction.channel_id)
    if session is None:
        await interaction.response.send_message(
            "⚠️ No hay pruebas registradas en este ticket. Usa `!result`.",
            ephemeral=True,
        )
        return
    if session["tester_id"] != interaction.user.id:
        await interaction.response.send_message(
            "🚫 Solo quien ejecutó `!result` puede abrir este formulario.",
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
    if is_staff(interaction.user) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para finalizar el test.",
            ephemeral=True,
        )
        return
    session = result_sessions.get(interaction.channel_id)
    if session is None:
        await interaction.response.send_message(
            "⚠️ No hay pruebas registradas en este ticket. Usa `!result`.",
            ephemeral=True,
        )
        return
    if session["tester_id"] != interaction.user.id:
        await interaction.response.send_message(
            "🚫 Solo quien ejecutó `!result` puede abrir este formulario.",
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
    if is_staff(interaction.user) == False:
        await interaction.response.send_message(
            "🚫 No estás autorizado para finalizar el test.",
            ephemeral=True,
        )
        return
    session = result_sessions.get(interaction.channel_id)
    if session is None:
        await interaction.response.send_message(
            "⚠️ No hay pruebas registradas en este ticket. Usa `!result` con las fotos.",
            ephemeral=True,
        )
        return
    if session["tester_id"] != interaction.user.id:
        await interaction.response.send_message(
            "🚫 Solo quien ejecutó `!result` puede abrir este formulario.",
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
async def on_ready():
    global cooldowns_restored
    if cooldowns_restored == True:
        return
    cooldowns_restored = True
    await restore_cooldowns()
    daily_activity_task.start()
    await bot.tree.sync(guild=discord.Object(id=config.GUILD_ID))
    await bot.tree.sync(guild=discord.Object(id=config.STAFF_GUILD_ID))
    print("Bot conectado como " + str(bot.user))


@bot.hybrid_command(name="setupss", description="Publica el panel de SS Request.")
async def setupss_command(ctx):
    await defer_if_interaction(ctx)
    if is_admin(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if ctx.channel.id != config.SS_GUIDE_CHANNEL_ID:
        await send_error(ctx, "🚫 Este comando solo funciona en el canal de guía de SS.")
        return
    existing = await get_setup_message(ctx.channel)
    if existing is not None:
        await send_error(ctx, "⚠️ Ya hay un embed de SS Request publicado en este canal.")
        return
    await ctx.send(embed=build_setup_embed(), view=build_setup_view())
    category = ctx.guild.get_channel(config.SS_TICKET_CATEGORY_ID)
    await send_private(ctx, "✅ Embed publicado. Los tickets se crearán en la categoría **" + category.name + "**.")


@bot.hybrid_command(name="setupbypass", description="Publica el panel de Bypass Request.")
async def setupbypass_command(ctx):
    await defer_if_interaction(ctx)
    if is_admin(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if config.BYPASS_CHANNEL_ID == 0:
        await send_error(ctx, "⚠️ El canal de bypass no está configurado en el .env.")
        return
    if config.BYPASS_TICKET_CATEGORY_ID == 0:
        await send_error(ctx, "⚠️ La categoría de tickets de bypass no está configurada en el .env.")
        return
    if ctx.channel.id != config.BYPASS_CHANNEL_ID:
        await send_error(ctx, "🚫 Este comando solo funciona en el canal de bypass.")
        return
    existing = await get_bypass_setup_message(ctx.channel)
    if existing is not None:
        await send_error(ctx, "⚠️ Ya hay un embed de Bypass Request publicado en este canal.")
        return
    await ctx.send(embed=build_bypass_setup_embed(), view=build_bypass_setup_view())
    category = ctx.guild.get_channel(config.BYPASS_TICKET_CATEGORY_ID)
    await send_private(ctx, "✅ Embed publicado. Los tickets se crearán en la categoría **" + category.name + "**.")


@bot.hybrid_command(name="bypassban", description="Banea a un usuario de abrir tickets de bypass.")
async def bypassban_command(ctx, target: str = None):
    await defer_if_interaction(ctx)
    if is_admin(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if target is None:
        await send_error(ctx, "Indica el ID o la mención del usuario. Ejemplo: `!bypassban 123456789`.")
        return
    user_id_text = target.replace("<@", "").replace(">", "").replace("!", "")
    user_id = int(user_id_text)
    if database.is_bypass_banned(user_id) == True:
        await send_error(ctx, "⚠️ Ese usuario ya está baneado de los tickets de bypass.")
        return
    database.add_bypass_ban(user_id)
    await ctx.send("✅ Usuario <@" + str(user_id) + "> baneado de los tickets de bypass.")


@bot.hybrid_command(name="bypassunban", description="Quita el ban de bypass a un usuario.")
async def bypassunban_command(ctx, target: str = None):
    await defer_if_interaction(ctx)
    if is_admin(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if target is None:
        await send_error(ctx, "Indica el ID o la mención del usuario. Ejemplo: `!bypassunban 123456789`.")
        return
    user_id_text = target.replace("<@", "").replace(">", "").replace("!", "")
    user_id = int(user_id_text)
    if database.is_bypass_banned(user_id) == False:
        await send_error(ctx, "⚠️ Ese usuario no está baneado de los tickets de bypass.")
        return
    database.remove_bypass_ban(user_id)
    await ctx.send("✅ Usuario <@" + str(user_id) + "> desbaneado de los tickets de bypass.")


@bot.hybrid_command(name="bypasstop", description="Muestra el top de los mejores bypassers.")
async def bypasstop_command(ctx):
    await defer_if_interaction(ctx)
    embed = build_bypasstop_embed(ctx.guild, ctx.author.id)
    await ctx.send(embed=embed)


@bot.group(name="points", description="Administra los puntos de bypass y de SS.", invoke_without_command=True)
async def points_command(ctx):
    await ctx.send(
        "Usa `!points bypass add @usuario 5`, `!points bypass remove @usuario 5`, "
        "`!points ss add @usuario 5` o `!points ss remove @usuario 5`."
    )


@points_command.group(name="bypass", invoke_without_command=True)
async def points_bypass_group(ctx):
    await ctx.send("Usa `!points bypass add @usuario 5` o `!points bypass remove @usuario 5`.")


@points_command.group(name="ss", invoke_without_command=True)
async def points_ss_group(ctx):
    await ctx.send("Usa `!points ss add @usuario 5` o `!points ss remove @usuario 5`.")


async def handle_points_bypass(ctx, target, amount, action):
    if can_manage_points(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if target is None or amount is None:
        await send_error(ctx, "Usa `!points bypass " + action + " <@usuario o ID> <puntos>`.")
        return
    if amount <= 0:
        await send_error(ctx, "⚠️ La cantidad de puntos debe ser mayor a 0.")
        return
    user_id = parse_user_id(target)
    member = ctx.guild.get_member(user_id)
    if member is None:
        await send_error(ctx, "⚠️ No se encontró ese usuario en el servidor.")
        return
    if action == "add":
        database.add_bypass_points(user_id, amount)
        await ctx.send("✅ Se sumaron **" + str(amount) + "** puntos de bypass a " + member.mention + ".")
        await send_log(
            "Ninguno",
            user_id,
            "Puntos bypass sumados",
            "**Staff:** <@" + str(ctx.author.id) + ">\n**Puntos:** +" + str(amount),
        )
        return
    database.remove_bypass_points(user_id, amount)
    await ctx.send("✅ Se restaron **" + str(amount) + "** puntos de bypass a " + member.mention + ".")
    await send_log(
        "Ninguno",
        user_id,
        "Puntos bypass restados",
        "**Staff:** <@" + str(ctx.author.id) + ">\n**Puntos:** -" + str(amount),
    )


async def handle_points_ss(ctx, target, amount, action):
    if can_manage_points(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if target is None or amount is None:
        await send_error(ctx, "Usa `!points ss " + action + " <@usuario o ID> <puntos>`.")
        return
    if amount <= 0:
        await send_error(ctx, "⚠️ La cantidad de puntos debe ser mayor a 0.")
        return
    user_id = parse_user_id(target)
    member = ctx.guild.get_member(user_id)
    if member is None:
        await send_error(ctx, "⚠️ No se encontró ese usuario en el servidor.")
        return
    if is_staff(member) == False:
        await send_error(ctx, "⚠️ Ese usuario no es staff.")
        return
    now = int(time.time())
    if action == "add":
        database.add_activity_adjustment(member.id, amount, now)
        await ctx.send("✅ Se sumaron **" + str(amount) + "** puntos de SS a " + member.mention + ".")
        await send_log(
            "Ninguno",
            member.id,
            "Puntos SS sumados",
            "**Staff:** <@" + str(ctx.author.id) + ">\n**Puntos:** +" + str(amount),
        )
        return
    database.add_activity_adjustment(member.id, amount * -1, now)
    await ctx.send("✅ Se restaron **" + str(amount) + "** puntos de SS a " + member.mention + ".")
    await send_log(
        "Ninguno",
        member.id,
        "Puntos SS restados",
        "**Staff:** <@" + str(ctx.author.id) + ">\n**Puntos:** -" + str(amount),
    )


@points_bypass_group.command(name="add")
async def points_bypass_add_command(ctx, target: str = None, amount: int = None):
    await handle_points_bypass(ctx, target, amount, "add")


@points_bypass_group.command(name="remove")
async def points_bypass_remove_command(ctx, target: str = None, amount: int = None):
    await handle_points_bypass(ctx, target, amount, "remove")


@points_ss_group.command(name="add")
async def points_ss_add_command(ctx, target: str = None, amount: int = None):
    await handle_points_ss(ctx, target, amount, "add")


@points_ss_group.command(name="remove")
async def points_ss_remove_command(ctx, target: str = None, amount: int = None):
    await handle_points_ss(ctx, target, amount, "remove")


@bot.command(name="result", description="Registra las pruebas del test y abre el formulario del resultado.")
async def result_command(ctx):
    if is_staff(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    ticket = database.get_open_ticket_by_channel(ctx.channel.id)
    if ticket is None:
        await send_error(ctx, "Este canal no es un ticket abierto.")
        return
    if ticket["difficulty"] == "bypass":
        if config.BYPASS_RESULT_CHANNEL_ID == 0:
            await send_error(ctx, "⚠️ El canal de resultados de bypass no está configurado en el .env.")
            return
        if get_normal_channel(config.BYPASS_RESULT_CHANNEL_ID) is None:
            await send_error(
                ctx,
                "⚠️ No se encontró el canal de resultados de bypass en el servidor. "
                "Revisa BYPASS_RESULT_CHANNEL_ID en el .env.",
            )
            return
    else:
        if config.RESULT_CHANNEL_ID == 0:
            await send_error(ctx, "⚠️ El canal de resultados no está configurado en el .env.")
            return
        if get_staff_channel(config.RESULT_CHANNEL_ID) is None:
            await send_error(
                ctx,
                "⚠️ No se encontró el canal de resultados de SS en el servidor de staff. "
                "Revisa RESULT_CHANNEL_ID en el .env.",
            )
            return
        if config.SS_RESULT_DISCORD_PUBLIC == 0:
            await send_error(ctx, "⚠️ El canal público de resultados de SS no está configurado en el .env.")
            return
        if get_normal_channel(config.SS_RESULT_DISCORD_PUBLIC) is None:
            await send_error(
                ctx,
                "⚠️ No se encontró el canal público de resultados de SS en el servidor. "
                "Revisa SS_RESULT_DISCORD_PUBLIC en el .env.",
            )
            return
    images = get_message_images(ctx.message)
    if len(images) == 0 and ticket["difficulty"] == "bypass":
        await send_error(ctx, "⚠️ Adjunta las fotos de las pruebas en el mismo mensaje del comando.")
        return
    proof_files = []
    for attachment in images:
        proof_files.append(await attachment.to_file())
    if ticket["difficulty"] == "bypass":
        result_sessions[ctx.channel.id] = {
            "ticket_id": ticket["id"],
            "proofs": images,
            "tester_id": ctx.author.id,
            "kind": "bypass",
        }
        await ctx.channel.send(
            content="📎 **Pruebas subidas:** fotos de las pruebas\n<@" + str(ctx.author.id) + ">",
            files=proof_files,
            view=build_bypass_proofs_view(ctx.channel.id),
        )
        await send_log(
            ctx.channel.mention,
            ticket["user_id"],
            "Pruebas bypass subidas",
            "**Staff:** <@" + str(ctx.author.id) + ">\n"
            "**Cantidad de imágenes:** " + str(len(images)),
        )
        return
    result_sessions[ctx.channel.id] = {
        "ticket_id": ticket["id"],
        "proofs": images,
        "tester_id": ctx.author.id,
        "difficulty": ticket["difficulty"],
        "kind": "ss",
    }
    if len(images) == 0:
        await ctx.channel.send(
            content="🧪 **Test registrado sin pruebas**\n<@" + str(ctx.author.id) + ">",
            view=build_result_proofs_view(ctx.channel.id),
        )
    else:
        await ctx.channel.send(
            content="📎 **Pruebas subidas:** fotos de las pruebas\n<@" + str(ctx.author.id) + ">",
            files=proof_files,
            view=build_result_proofs_view(ctx.channel.id),
        )
    await send_log(
        ctx.channel.mention,
        ticket["user_id"],
        "Pruebas SS subidas",
        "**Staff:** <@" + str(ctx.author.id) + ">\n"
        "**Dificultad:** " + DIFFICULTY_NAMES[ticket["difficulty"]] + "\n"
        "**Cantidad de imágenes:** " + str(len(images)),
    )


@bot.hybrid_command(name="unclaim", description="Retira el claim del ticket actual.")
async def unclaim_command(ctx):
    await defer_if_interaction(ctx)
    if is_staff(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    ticket = database.get_open_ticket_by_channel(ctx.channel.id)
    if ticket is None:
        await send_error(ctx, "Este canal no es un ticket abierto.")
        return
    if ticket["claimed_by"] is None:
        await send_error(ctx, "⚠️ Este ticket no tiene un claim activo.")
        return
    old_claim = ticket["claimed_by"]
    points = DIFFICULTY_POINTS[ticket["difficulty"]]
    database.unclaim_ticket(ticket["id"])
    database.remove_activity(ticket["id"])
    creator = await bot.fetch_user(ticket["user_id"])
    ticket_message = await get_ticket_message(ctx.channel)
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
    await ctx.send(
        "✅ Claim retirado a <@" + str(old_claim) + ">. Puntos restados: **" + str(points) + "**."
    )
    await send_log(
        ctx.channel.mention,
        ticket["user_id"],
        "Ticket unclameado",
        "**Staff que reclamó:** <@" + str(old_claim) + ">\n"
        "**Staff que ejecutó el unclaim:** <@" + str(ctx.author.id) + ">\n"
        "**Puntos restados:** " + str(points),
    )


@bot.hybrid_command(name="remember", description="Envía un recordatorio por MD al creador del ticket.")
async def remember_command(ctx):
    await defer_if_interaction(ctx)
    if is_staff(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    ticket = database.get_open_ticket_by_channel(ctx.channel.id)
    if ticket is None:
        await send_error(ctx, "Este canal no es un ticket abierto.")
        return
    user = await bot.fetch_user(ticket["user_id"])
    thumbnail_name = os.path.basename(config.REMINDER_THUMBNAIL)
    embed = build_reminder_embed(thumbnail_name, ctx.channel.id)
    await user.send(embed=embed, file=discord.File(config.REMINDER_THUMBNAIL))
    await ctx.send("📩 Recordatorio enviado por MD a " + user.mention + ".")


@bot.hybrid_command(name="cooldown", description="Muestra el cooldown de un usuario.")
async def cooldown_command(ctx, target: str = None):
    await defer_if_interaction(ctx)
    if is_staff(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if target is None:
        await send_error(ctx, "Indica el ID o la mención del usuario. Ejemplo: `!cooldown @usuario`.")
        return
    user_id = parse_user_id(target)
    member = ctx.guild.get_member(user_id)
    if member is None:
        embed = discord.Embed(title="Cooldown", description="No se encontró ese usuario en el servidor.", color=discord.Color.purple())
        await send_error(ctx, embed=embed)
        return
    row = database.get_cooldown(user_id)
    seconds_left = get_cooldown_seconds_left(user_id)
    embed = discord.Embed(title="Cooldown de " + member.name, color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Usuario", value=member.mention, inline=False)
    if row is None or seconds_left == 0:
        embed.add_field(name="Estado", value="Sin cooldown activo", inline=False)
        await ctx.send(embed=embed)
        return
    embed.add_field(name="Estado", value="Cooldown activo", inline=False)
    embed.add_field(name="⏳ Tiempo restante", value=format_remaining(seconds_left), inline=True)
    embed.add_field(name="📅 Termina", value=format_date(row["cooldown_end"]), inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="removecooldown", description="Quita el cooldown de un usuario.")
async def removecooldown_command(ctx, target: str = None):
    await defer_if_interaction(ctx)
    if is_admin(ctx.author) == False:
        await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
        return
    if target is None:
        await send_error(ctx, "Indica el ID o la mención del usuario. Ejemplo: `!removecooldown @usuario`.")
        return
    user_id = parse_user_id(target)
    member = ctx.guild.get_member(user_id)
    if member is None:
        embed = discord.Embed(title="Quitar cooldown", description="No se encontró ese usuario en el servidor.", color=discord.Color.purple())
        await send_error(ctx, embed=embed)
        return
    row = database.get_cooldown(user_id)
    if row is None:
        embed = discord.Embed(title="Quitar cooldown", color=discord.Color.purple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Usuario", value=member.mention, inline=False)
        embed.add_field(name="Estado", value="No tenía un cooldown activo", inline=False)
        await ctx.send(embed=embed)
        return
    old_end = row["cooldown_end"]
    await remove_cooldown_role(member)
    database.delete_cooldown(user_id)
    embed = discord.Embed(title="Quitar cooldown", color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Usuario", value=member.mention, inline=False)
    embed.add_field(name="Estado", value="Cooldown removido", inline=False)
    embed.add_field(name="📅 Terminaba", value=format_date(old_end), inline=False)
    await ctx.send(embed=embed)
    await send_log(
        "Ninguno",
        user_id,
        "Cooldown removido",
        "**Staff:** <@" + str(ctx.author.id) + ">\n**Terminaba:** " + format_date(old_end),
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


@bot.hybrid_command(name="actividad", description="Muestra la actividad del staff.")
async def activity_command(ctx):
    await defer_if_interaction(ctx)
    if ctx.guild.id != config.STAFF_GUILD_ID:
        if is_staff(ctx.author) == False:
            await send_error(ctx, "🚫 No estás autorizado para usar este comando.")
            return
    await send_activity_report(ctx.channel)


database.init_database()

if __name__ == "__main__":
    bot.run(config.TOKEN)
