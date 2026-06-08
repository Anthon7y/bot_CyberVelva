import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters
from telegram.error import TelegramError
from services.db import get_stats, get_all_users
from services.broadcast import broadcast_message
from services.content import load_practicums, save_practicums
from config import load_admins, set_bot_name, get_bot_name, PRACTICUMS_FILE

logger = logging.getLogger(__name__)

# Состояния ConversationHandler
WAITING_BROADCAST = 1
WAITING_NEW_NAME = 2
WAITING_PRACTICUMS = 3


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
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast — начало рассылки."""
    total = len(get_all_users())
    await update.message.reply_text(
        f"Режим рассылки\n\n"
        f"Сообщение будет отправлено *всем {total} пользователям* бота.\n\n"
        f"Отправьте сообщение (текст, фото, видео, документ).\n"
        f"Для отмены введите /cancel",
        parse_mode="Markdown"
    )
    return WAITING_BROADCAST


async def broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает сообщение и запускает рассылку."""
    user_id = update.effective_user.id
    admins = load_admins()
    if user_id not in admins:
        return ConversationHandler.END

    # Сохраняем сообщение для возможности удаления
    context.user_data["last_broadcast_msg"] = update.message.message_id

    status_msg = await update.message.reply_text("Рассылка запущена...")

    # Получаем ВСЕХ пользователей
    user_ids = get_all_users()

    # Если текстовое сообщение — отправляем с parse_mode="Markdown" чтобы сохранить форматирование
    if update.message.text:
        text = update.message.text
        success, failed = 0, 0
        for user_id in user_ids:
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                success += 1
                if success % 10 == 0:
                    logger.info(f"Отправлено {success}/{len(user_ids)}")
            except TelegramError as e:
                logger.warning(f"Не удалось отправить {user_id}: {e}")
                failed += 1
            await asyncio.sleep(SEND_DELAY)
    elif update.message.photo or update.message.video or update.message.voice:
        # Для медиа с caption — отправляем с parse_mode="Markdown"
        caption = update.message.caption or ""
        success, failed = 0, 0
        for user_id in user_ids:
            try:
                if update.message.photo:
                    await context.bot.send_photo(chat_id=user_id, photo=update.message.photo[-1].file_id, caption=caption, parse_mode="Markdown")
                elif update.message.video:
                    await context.bot.send_video(chat_id=user_id, video=update.message.video.file_id, caption=caption, parse_mode="Markdown")
                elif update.message.voice:
                    await context.bot.send_voice(chat_id=user_id, voice=update.message.voice.file_id, caption=caption, parse_mode="Markdown")
                success += 1
                if success % 10 == 0:
                    logger.info(f"Отправлено {success}/{len(user_ids)}")
            except TelegramError as e:
                logger.warning(f"Не удалось отправить {user_id}: {e}")
                failed += 1
            await asyncio.sleep(SEND_DELAY)
    else:
        # Для других типов используем copy_message
        from services.broadcast import broadcast_message
        success, failed = await broadcast_message(context.bot, update.message, user_ids)

    # Сохраняем рассылку в БД
    from services.db import save_broadcast
    text = update.message.text or update.message.caption or ""
    save_broadcast(message_text=text)

    try:
        await status_msg.edit_text(
            f"Рассылка завершена!\n\n"
            f"Отправлено: {success}\n"
            f"Ошибок: {failed}"
        )
    except:
        pass
    
    # ВАЖНО: явно завершаем conversation
    return ConversationHandler.END


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
    },
    fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    per_user=True,
    per_chat=True,
    conversation_timeout=60,  # 1 минута таймаут
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
