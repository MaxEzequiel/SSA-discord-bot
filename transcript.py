import html

PAGE_STYLE = """
body {
    margin: 0;
    padding: 0;
    background-color: #313338;
    font-family: "gg sans", "Noto Sans", Helvetica, Arial, sans-serif;
    color: #dbdee1;
}

.header {
    background-color: #2b2d31;
    padding: 16px 24px;
    border-bottom: 1px solid #1e1f22;
    font-size: 20px;
    font-weight: 700;
    color: #f2f3f5;
}

.header small {
    display: block;
    font-size: 12px;
    font-weight: 400;
    color: #949ba4;
    margin-top: 4px;
}

.messages {
    max-width: 900px;
    margin: 0 auto;
    padding: 16px 8px 40px 8px;
}

.message {
    display: flex;
    padding: 8px 16px;
    border-radius: 4px;
}

.message:hover {
    background-color: #2e3035;
}

.avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    margin-right: 16px;
    flex-shrink: 0;
}

.body {
    flex: 1;
    min-width: 0;
}

.message-header {
    margin-bottom: 4px;
}

.username {
    color: #f2f3f5;
    font-weight: 600;
    font-size: 16px;
}

.bot-tag {
    background-color: #5865f2;
    color: #ffffff;
    font-size: 10px;
    font-weight: 600;
    border-radius: 3px;
    padding: 1px 4px;
    margin-left: 6px;
}

.timestamp {
    color: #949ba4;
    font-size: 12px;
    margin-left: 8px;
}

.content {
    font-size: 16px;
    line-height: 1.375;
    white-space: pre-wrap;
    word-wrap: break-word;
}

.attachment {
    margin-top: 8px;
}

.attachment img {
    max-width: 400px;
    border-radius: 8px;
}

.attachment-file {
    margin-top: 8px;
    background-color: #2b2d31;
    border: 1px solid #1e1f22;
    border-radius: 8px;
    padding: 12px;
    display: inline-block;
}

.attachment-file a {
    color: #00a8fc;
    text-decoration: none;
}

.embed {
    margin-top: 8px;
    background-color: #2b2d31;
    border-left: 4px solid #4f545c;
    border-radius: 4px;
    padding: 12px 16px;
    max-width: 520px;
}

.embed-title {
    font-weight: 700;
    color: #f2f3f5;
    margin-bottom: 6px;
}

.embed-description {
    font-size: 14px;
    line-height: 1.375;
    white-space: pre-wrap;
}

.embed-field-name {
    font-weight: 700;
    color: #f2f3f5;
    margin-top: 8px;
    font-size: 14px;
}

.embed-field-value {
    font-size: 14px;
    white-space: pre-wrap;
}
"""


def escape_html(text):
    return html.escape(text)


def join_parts(parts):
    result = ""
    for part in parts:
        result = result + part
    return result


def build_avatar(message):
    return '<img class="avatar" src="' + message.author.display_avatar.url + '" alt="avatar">'


def build_username(message):
    text = '<span class="username">' + escape_html(message.author.display_name) + "</span>"
    if message.author.bot == True:
        text = text + '<span class="bot-tag">BOT</span>'
    return text


def build_header(message):
    timestamp = message.created_at.strftime("%d/%m/%Y %H:%M")
    return (
        '<div class="message-header">'
        + build_username(message)
        + '<span class="timestamp">' + timestamp + "</span>"
        + "</div>"
    )


def build_attachments(message):
    parts = []
    for attachment in message.attachments:
        part = (
            '<div class="attachment-file">📎 <a href="' + attachment.url + '">'
            + escape_html(attachment.filename)
            + "</a></div>"
        )
        if attachment.content_type is not None:
            if attachment.content_type.startswith("image/"):
                part = (
                    '<div class="attachment"><img src="' + attachment.url
                    + '" alt="' + escape_html(attachment.filename) + '"></div>'
                )
        parts.append(part)
    return join_parts(parts)


def build_embed(embed):
    color = "#4f545c"
    if embed.color is not None:
        color = str(embed.color)
    parts = []
    parts.append('<div class="embed" style="border-left-color: ' + color + '">')
    if embed.title is not None:
        parts.append('<div class="embed-title">' + escape_html(embed.title) + "</div>")
    if embed.description is not None:
        parts.append('<div class="embed-description">' + escape_html(embed.description) + "</div>")
    for field in embed.fields:
        parts.append('<div class="embed-field-name">' + escape_html(field.name) + "</div>")
        parts.append('<div class="embed-field-value">' + escape_html(field.value) + "</div>")
    parts.append("</div>")
    return join_parts(parts)


def build_embeds(message):
    parts = []
    for embed in message.embeds:
        parts.append(build_embed(embed))
    return join_parts(parts)


def build_message(message):
    parts = []
    parts.append('<div class="message">')
    parts.append(build_avatar(message))
    parts.append('<div class="body">')
    parts.append(build_header(message))
    if message.content != "":
        parts.append('<div class="content">' + escape_html(message.content) + "</div>")
    parts.append(build_attachments(message))
    parts.append(build_embeds(message))
    parts.append("</div>")
    parts.append("</div>")
    return join_parts(parts)


def build_messages(messages):
    parts = []
    for message in messages:
        parts.append(build_message(message))
    return join_parts(parts)


def build_range_text(messages):
    if len(messages) == 0:
        return "Sin mensajes"
    first = messages[0].created_at.strftime("%d/%m/%Y %H:%M")
    last = messages[-1].created_at.strftime("%d/%m/%Y %H:%M")
    return "Del " + first + " al " + last + " UTC · " + str(len(messages)) + " mensajes"


def build_page(channel_name, messages):
    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="es">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append("<title>Transcript " + escape_html(channel_name) + "</title>")
    parts.append("<style>" + PAGE_STYLE + "</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append(
        '<div class="header"># ' + escape_html(channel_name)
        + "<small>" + build_range_text(messages) + "</small></div>"
    )
    parts.append('<div class="messages">')
    parts.append(build_messages(messages))
    parts.append("</div>")
    parts.append("</body>")
    parts.append("</html>")
    return join_parts(parts)
