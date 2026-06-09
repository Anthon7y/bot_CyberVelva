import logging
from telegram import InputFile
from telegram import Update
from telegram.ext import ContextTypes
from services.db import get_broadcasts, upsert_user
from config import load_admins, DATA_DIR
from keyboards.main_menu import get_main_menu_keyboard
from middlewares.rate_limit import is_rate_limited

logger = logging.getLogger(__name__)


async def subscription_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает архив рассылок."""
    user = update.effective_user
    if is_rate_limited(user.id):
        await update.message.reply_text("Слишком много запросов. Подождите секунду.")
        return

    # Проверяем, админ ли пользователь
    admins = load_admins()
    is_admin = user.id in admins

    broadcasts = get_broadcasts(limit=5)

    text = "Архив рассылок\n\n"
    if is_admin:
        text += "Используйте команду /broadcast для отправки новой рассылки.\n"
    else:
        text += "Рассылка активна! Чтобы отписаться напишите /unfollow."

    if not broadcasts:
        text += "\nПока нет отправленных рассылок. Следите за обновлениями!"

    for b in broadcasts:
        sent_at = b["sent_at"]
        text += f"\n{sent_at}"
        if b["message_text"]:
            text += f"\n{b['message_text'][:100]}..."
        text += "\n"

    await update.message.reply_text(
        text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )


async def unfollow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает подтверждение отписки."""
    user = update.effective_user
    upsert_user(user.id, user.username or "", user.first_name or "", user.last_name or "", subscribed=1)
    
    unsub_image_path = f"{DATA_DIR}/images/UNSUB.jpg"
    
    try:
        with open(unsub_image_path, "rb") as img:
            await update.message.reply_photo(
                photo=InputFile(img),
                caption="Вы уверены? /yes /no",
                parse_mode="Markdown"
            )
    except FileNotFoundError:
        await update.message.reply_text(
            "Вы уверены? /yes /no",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )


async def yes_unfollow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отписывает пользователя от рассылки."""
    user = update.effective_user
    user_id = user.id
    
    from services.db import get_user
    user_data = get_user(user_id)
    if user_data and user_data["subscribed"] == 1:
        upsert_user(user_id, user.username or "", user.first_name or "", user.last_name or "", subscribed=0)
        await update.message.reply_text(
            "Вы отписаны от рассылки.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Вы уже отписаны.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )


async def no_unfollow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет отписку."""
    await update.message.reply_text(
        "Рассылка активна! Чтобы отписаться напишите /unfollow.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )


async def subscription_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатие кнопок подписки/отписки — теперь только заглушка."""
    query = update.callback_query
    await query.answer()
    # Раньше здесь была кнопка отписки, теперь только команда /unfollow
