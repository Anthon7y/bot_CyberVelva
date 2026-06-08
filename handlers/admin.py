import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
import datetime
from services.db import get_stats, get_all_users
from services.broadcast import broadcast_message
from services.content import load_practicums, save_practicums
from config import load_admins, set_bot_name, get_bot_name, PRACTICUMS_FILE

logger = logging.getLogger(__name__)

# Состояния ConversationHandler
WAITING_BROADCAST = 1
WAITING_SCHEDULE_TIME = 2
WAITING_NEW_NAME = 3
WAITING_PRACTICUMS = 4


def admin_only(func):
    """Декоратор: пропускает только администраторов."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        admins = load_admins()
        if user_id not in admins:
            await update.message.reply_text("У вас нет доступа к этой команде.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


@admin_only
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats — статистика пользователей."""
    stats = get_stats()
    await update.message.reply_text(
        f"Статистика\n\n"
        f"Всего пользователей: {stats['total']}\n"
        f"Подписаны на рассылку: {stats['subscribed']}\n"
        f"Отписаны: {stats['unsubscribed']}\n"
        f"Активны сегодня (предсказание): {stats['active_today']}",
        parse_mode="Markdown"
    )


# --- /broadcast - рассылка ВСЕМ пользователям ---

@admin_only
def get_schedule_keyboard() -> InlineKeyboardMarkup:
    """Кнопки выбора времени отправки."""
    now = datetime.datetime.now()
    today_12 = now.replace(hour=12, minute=0, second=0, microsecond=0)
    today_18 = now.replace(hour=18, minute=0, second=0, microsecond=0)
    tomorrow_10 = (now + datetime.timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    
    buttons = [
        [InlineKeyboardButton("Сегодня в 12:00", callback_data="time_12")],
        [InlineKeyboardButton("Сегодня в 18:00", callback_data="time_18")],
        [InlineKeyboardButton("Завтра в 10:00", callback_data="time_tomorrow_10")],
        [InlineKeyboardButton("Отправить сейчас", callback_data="time_now")],
    ]
    return InlineKeyboardMarkup(buttons)


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast — начало рассылки."""
    total = len(get_all_users())
    await update.message.reply_text(
        f"Режим рассылки\n\n"
        f"Сообщение будет отправлено *всем {total} пользователям* бота.\n\n"
        f"Отправьте сообщение (текст, фото, видео, аудио, документ).\n"
        f"Для отмены введите /cancel",
        parse_mode="Markdown"
    )
    context.user_data["broadcast_waiting_time_selection"] = True
    return WAITING_BROADCAST


async def broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает сообщение и запускает рассылку."""
    user_id = update.effective_user.id
    admins = load_admins()
    if user_id not in admins:
        return ConversationHandler.END

    # Если это нажатие кнопки выбора времени
    if update.callback_query:
        await query_time_selection(update, context)
        return WAITING_SCHEDULE_TIME

    # Сохраняем сообщение для возможности удаления
    context.user_data["last_broadcast_msg"] = update.message.message_id
    context.user_data["broadcast_message"] = {
        "text": update.message.text,
        "caption": update.message.caption,
        "photo": update.message.photo[-1].file_id if update.message.photo else None,
        "video": update.message.video.file_id if update.message.video else None,
        "voice": update.message.voice.file_id if update.message.voice else None,
    }

    # Спрашиваем время отправки
    await update.message.reply_text(
        "Выберите время отправки:\n\n"
        "Формат: ЧЧ:ММ (например, 12:00, 18:30)\n"
        "Или нажмите кнопку ниже",
        reply_markup=get_schedule_keyboard(),
        parse_mode="Markdown"
    )
    return WAITING_SCHEDULE_TIME


async def query_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор времени через inline-кнопку."""
    query = update.callback_query
    await query.answer()

    time_data = query.data.replace("time_", "")
    
    if time_data == "now":
        # Сразу отправляем
        context.user_data["broadcast_scheduled_time"] = None
    elif time_data.startswith("tomorrow"):
        tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
        context.user_data["broadcast_scheduled_time"] = tomorrow.strftime("%Y-%m-%d 10:00:00")
    else:
        # today 12 or 18
        hour = int(time_data.split("_")[0])
        today = datetime.datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
        context.user_data["broadcast_scheduled_time"] = today.strftime("%Y-%m-%d %H:%M:%S")

    # Переходим к отправке
    status_msg = await query.message.reply_text("Рассылка запущена...")
    user_ids = get_all_users()
    success, failed = await send_scheduled_broadcast(context, user_ids)
    
    try:
        await status_msg.edit_text(
            f"Рассылка завершена!\n\n"
            f"Отправлено: {success}\n"
            f"Ошибок: {failed}"
        )
    except:
        pass
    
    return ConversationHandler.END


async def schedule_time_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает ручной ввод времени."""
    text = update.message.text.strip()
    
    # Проверяем формат ЧЧ:ММ
    import re
    match = re.match(r"^(\d{2}):(\d{2})$", text)
    if not match:
        await update.message.reply_text(
            "Неверный формат. Введите время в формате ЧЧ:ММ (например, 12:00)"
        )
        return WAITING_SCHEDULE_TIME
    
    hour, minute = int(match.group(1)), int(match.group(2))
    
    now = datetime.datetime.now()
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Если время уже прошло сегодня, ставим на завтра
    if scheduled < now:
        scheduled += datetime.timedelta(days=1)
    
    context.user_data["broadcast_scheduled_time"] = scheduled.strftime("%Y-%m-%d %H:%M:%S")
    
    status_msg = await update.message.reply_text("Рассылка запущена...")
    user_ids = get_all_users()
    success, failed = await send_scheduled_broadcast(context, user_ids)
    
    try:
        await status_msg.edit_text(
            f"Рассылка завершена!\n\n"
            f"Отправлено: {success}\n"
            f"Ошибок: {failed}"
        )
    except:
        pass
    
    return ConversationHandler.END


async def send_scheduled_broadcast(context: ContextTypes.DEFAULT_TYPE, user_ids: list[int]):
    """Отправляет рассылку с сохраненными медиа."""
    message_data = context.user_data.get("broadcast_message", {})
    scheduled_time = context.user_data.get("broadcast_scheduled_time")
    
    success, failed = 0, 0
    
    # Если есть scheduled_time — сохраняем в БД и отправляем позже
    # Для простоты — сразу отправляем
    text = message_data.get("text")
    caption = message_data.get("caption") or ""
    
    for user_id in user_ids:
        try:
            if message_data.get("photo"):
                await context.bot.send_photo(chat_id=user_id, photo=message_data["photo"], caption=caption, parse_mode="Markdown")
            elif message_data.get("video"):
                await context.bot.send_video(chat_id=user_id, video=message_data["video"], caption=caption, parse_mode="Markdown")
            elif message_data.get("voice"):
                await context.bot.send_voice(chat_id=user_id, voice=message_data["voice"], caption=caption, parse_mode="Markdown")
            elif text:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
            success += 1
            if success % 10 == 0:
                logger.info(f"Отправлено {success}/{len(user_ids)}")
        except TelegramError as e:
            logger.warning(f"Не удалось отправить {user_id}: {e}")
            failed += 1
        await asyncio.sleep(SEND_DELAY)
    
    # Сохраняем в БД
    from services.db import save_broadcast
    save_broadcast(
        message_text=text or "",
        message_photo=message_data.get("photo"),
        message_document=message_data.get("video") or message_data.get("voice")
    )
    
    return success, failed


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Рассылка отменена. Напишите /start для возврата в меню.")
    return ConversationHandler.END


# --- /setname ---

@admin_only
async def setname_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setname — смена названия бота."""
    current = get_bot_name()
    await update.message.reply_text(
        f"Текущее название: *{current}*\n\n"
        f"Отправьте новое название бота.\n"
        f"Для отмены введите /cancel",
        parse_mode="Markdown"
    )
    return WAITING_NEW_NAME


async def setname_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = load_admins()
    if user_id not in admins:
        return ConversationHandler.END

    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("Название не может быть пустым. Попробуйте ещё раз или /cancel")
        return WAITING_NEW_NAME

    set_bot_name(new_name)
    await update.message.reply_text(
        f"Название бота обновлено: *{new_name}*\n\n"
        f"Изменение вступило в силу немедленно.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def setname_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Смена названия отменена.")
    return ConversationHandler.END


# --- /prac - управление практикумами ---

@admin_only
async def practicum_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/prac — редактирование практикумов."""
    current = load_practicums()
    
    if current:
        text = f"Текущие практикумы:\n\n{current}\n\n"
    else:
        text = "Практикумы пока не добавлены.\n\n"
    
    text += "Отправьте новый текст практикумов.\n"
    text += "Для отмены введите /cancel"
    
    await update.message.reply_text(text, parse_mode="Markdown")
    return WAITING_PRACTICUMS


async def practicum_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = load_admins()
    if user_id not in admins:
        return ConversationHandler.END

    # Если фото — сохраняем caption
    if update.message.caption:
        text = update.message.caption.strip()
        save_practicums(text)
        await update.message.reply_text(
            f"Практикумы обновлены!\n\n{text}",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    new_text = update.message.text.strip()
    if not new_text:
        await update.message.reply_text("Текст не может быть пустым. Попробуйте ещё раз или /cancel")
        return WAITING_PRACTICUMS

    save_practicums(new_text)
    await update.message.reply_text(
        f"Практикумы обновлены!\n\n{new_text}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def practicum_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Редактирование практикумов отменено.")
    return ConversationHandler.END


# ConversationHandler для /prac (алиас для /practicums)
prac_conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("prac", practicum_start),
        CommandHandler("practicums", practicum_start)
    ],
    states={
        WAITING_PRACTICUMS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, practicum_receive)
        ],
    },
    fallbacks=[CommandHandler("cancel", practicum_cancel)],
    per_user=True,
)


# Старый handler для совместимости
practicum_conv_handler = prac_conv_handler


# ConversationHandler для /broadcast
broadcast_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("broadcast", broadcast_start)],
    states={
        WAITING_BROADCAST: [
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
                broadcast_receive
            )
        ],
        WAITING_SCHEDULE_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_time_receive),
            CallbackQueryHandler(query_time_selection, pattern="^time_")
        ],
    },
    fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    per_user=True,
    per_chat=True,
    conversation_timeout=120,  # 2 минуты таймаут
)

# ConversationHandler для /setname
setname_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("setname", setname_start)],
    states={
        WAITING_NEW_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, setname_receive)
        ],
    },
    fallbacks=[CommandHandler("cancel", setname_cancel)],
    per_user=True,
)

# ConversationHandler для /practicums
practicum_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("practicums", practicum_start)],
    states={
        WAITING_PRACTICUMS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, practicum_receive)
        ],
    },
    fallbacks=[CommandHandler("cancel", practicum_cancel)],
    per_user=True,
)
