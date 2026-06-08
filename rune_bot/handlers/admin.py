import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters
from services.db import get_stats, get_all_users
from services.broadcast import broadcast_message
from services.content import load_practicums, save_practicums
from config import load_admins, set_bot_name, get_bot_name, PRACTICUMS_FILE, ABOUT_US_ROOT

logger = logging.getLogger(__name__)

# Состояния ConversationHandler
WAITING_BROADCAST = 1
WAITING_ONAS = 2
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


@admin_only
async def onas_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/onas — редактирование раздела "О нас"."""
    current = get_about_text()
    
    await update.message.reply_text(
        f"Текущий текст раздела 'О нас':\n\n{current}\n\n"
        f"Отправьте новый текст.\n"
        f"Для отмены введите /cancel",
        parse_mode="Markdown"
    )
    return WAITING_ONAS


async def onas_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = load_admins()
    if user_id not in admins:
        return ConversationHandler.END

    new_text = update.message.text
    if not new_text:
        await update.message.reply_text("Текст не может быть пустым. Попробуйте ещё раз или /cancel")
        return WAITING_ONAS

    # Сохраняем в корневой файл
    try:
        with open(ABOUT_US_ROOT, "r", encoding="windows-1251") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = ["таро и руны\n"]

    if lines:
        lines[0] = lines[0].strip() + "\n"
        lines[1:] = [line for line in lines[1:] if line.strip()]  # Удаляем пустые
        lines.insert(1, "\n" + new_text + "\n")
    else:
        lines = ["таро и руны\n", "\n", new_text, "\n"]

    with open(ABOUT_US_ROOT, "w", encoding="windows-1251") as f:
        f.writelines(lines)

    await update.message.reply_text(
        f"Раздел 'О нас' обновлен!\n\n{new_text}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def onas_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Редактирование раздела 'О нас' отменено.")
    return ConversationHandler.END


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
            except Exception as e:
                logger.warning(f"Не удалось отправить {user_id}: {e}")
                failed += 1
            import asyncio
            await asyncio.sleep(0.02)
    else:
        # Для фото/видео используем copy_message
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

    new_text = update.message.text.strip()
    if not new_text:
        await update.message.reply_text("Текст не может быть пустым. Попробуйте ещё раз или /cancel")
        return WAITING_PRACTICUMS

    save_practicums(new_text)
    await update.message.reply_text(
        "Практикумы обновлены!\n\n"
        "Изменение вступило в силу немедленно.",
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

# ConversationHandler для /onas
onas_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("onas", onas_start)],
    states={
        WAITING_ONAS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, onas_receive)
        ],
    },
    fallbacks=[CommandHandler("cancel", onas_cancel)],
    per_user=True,
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
