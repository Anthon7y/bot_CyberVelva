import logging
from telegram import InputFile
from telegram import Update
from telegram.ext import ContextTypes
from config import get_about_text, get_bot_name, DATA_DIR
from services.content import load_practicums, get_practicum_image_path
from keyboards.main_menu import get_main_menu_keyboard

logger = logging.getLogger(__name__)


async def about_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_about_text()
    bot_name = get_bot_name()

    if not text:
        text = "Информация о нас пока не добавлена."

    await update.message.reply_text(
        f"*{bot_name}*\n\n{text}",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )


async def practicum_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает практикумы с фото."""
    practicum_text = load_practicums()
    
    prac_image_path = get_practicum_image_path()
    
    if practicum_text:
        try:
            with open(prac_image_path, "rb") as img:
                await update.message.reply_photo(
                    photo=InputFile(img),
                    caption=practicum_text,
                    parse_mode="Markdown"
                )
        except FileNotFoundError:
            await update.message.reply_text(
                f"{practicum_text}",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown"
            )
    else:
        try:
            with open(prac_image_path, "rb") as img:
                await update.message.reply_photo(
                    photo=InputFile(img),
                    caption="Практикумы пока не добавлены. Следите за обновлениями!",
                    parse_mode="Markdown"
                )
        except FileNotFoundError:
            await update.message.reply_text(
                "Практикумы пока не добавлены. Следите за обновлениями!",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown"
            )
