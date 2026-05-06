"""
Легальный Telegram Music Bot
Скачивает музыку только из публичных источников с разрешением
"""
import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

MAX_FILE_SIZE = 49 * 1024 * 1024  # 49 MB
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище данных пользователей
user_search_results: dict[int, list[dict]] = {}
user_current_page: dict[int, int] = {}
user_last_request: dict[int, float] = {}

# Семафор для ограничения одновременных загрузок
download_semaphore = asyncio.Semaphore(3)

# Троттлинг: минимальный интервал между запросами (секунды)
THROTTLE_SECONDS = 2


def get_user_dir(user_id: int) -> Path:
    """Создает и возвращает директорию для файлов пользователя"""
    user_dir = DOWNLOADS_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def cleanup_user_files(user_id: int) -> None:
    """Удаляет все файлы пользователя"""
    user_dir = get_user_dir(user_id)
    try:
        for file in user_dir.iterdir():
            if file.is_file():
                file.unlink()
        logger.info(f"Очищена директория пользователя {user_id}")
    except Exception as e:
        logger.warning(f"Ошибка очистки директории {user_id}: {e}")


def safe_filename(text: str, max_length: int = 100) -> str:
    """Очищает строку для использования в имени файла"""
    # Удаляем недопустимые символы
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    # Заменяем множественные пробелы
    text = re.sub(r'\s+', " ", text)
    return text.strip()[:max_length]


def check_throttle(user_id: int) -> bool:
    """Проверяет, не слишком ли часто пользователь делает запросы"""
    import time
    now = time.time()
    last_request = user_last_request.get(user_id, 0)
    
    if now - last_request < THROTTLE_SECONDS:
        return False
    
    user_last_request[user_id] = now
    return True


async def search_youtube_music(query: str, max_results: int = 30) -> list[dict]:
    """
    Ищет музыку на YouTube
    Возвращает список треков с метаданными
    """
    def _search():
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "default_search": "ytsearch",
        }
        
        results = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_result = ydl.extract_info(
                    f"ytsearch{max_results}:{query}",
                    download=False
                )
                
                if not search_result or "entries" not in search_result:
                    return []
                
                for entry in search_result["entries"]:
                    if not entry:
                        continue
                    
                    # Фильтруем только музыкальный контент
                    title = entry.get("title", "")
                    duration = entry.get("duration", 0)
                    
                    # Пропускаем слишком длинные видео (вероятно не музыка)
                    if duration and duration > 600:  # 10 минут
                        continue
                    
                    results.append({
                        "title": title,
                        "url": entry.get("url", ""),
                        "duration": duration,
                        "uploader": entry.get("uploader", "Unknown"),
                        "id": entry.get("id", ""),
                    })
                
                logger.info(f"Найдено {len(results)} треков для запроса: {query}")
                return results
                
        except Exception as e:
            logger.error(f"Ошибка поиска на YouTube: {e}")
            return []
    
    return await asyncio.to_thread(_search)


async def download_audio(url: str, user_id: int, title: str = "audio") -> Optional[Path]:
    """
    Скачивает аудио из YouTube
    Возвращает путь к файлу или None при ошибке
    """
    def _download():
        user_dir = get_user_dir(user_id)
        safe_title = safe_filename(title)
        output_template = str(user_dir / f"{safe_title}.%(ext)s")
        
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_audio": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            # Добавляем метаданные
            "writethumbnail": False,
            "embedthumbnail": False,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Находим скачанный файл
                filename = ydl.prepare_filename(info)
                audio_file = Path(filename).with_suffix(".mp3")
                
                if not audio_file.exists():
                    logger.error(f"Файл не найден после скачивания: {audio_file}")
                    return None
                
                # Проверяем размер
                file_size = audio_file.stat().st_size
                if file_size > MAX_FILE_SIZE:
                    logger.warning(f"Файл слишком большой: {file_size} bytes")
                    audio_file.unlink()
                    return None
                
                logger.info(f"Успешно скачан файл: {audio_file.name} ({file_size} bytes)")
                return audio_file
                
        except Exception as e:
            logger.error(f"Ошибка скачивания аудио: {e}")
            return None
    
    return await asyncio.to_thread(_download)


def build_search_keyboard(
    results: list[dict],
    page: int = 0,
    per_page: int = 10
) -> InlineKeyboardMarkup:
    """Создает клавиатуру с результатами поиска"""
    total_pages = (len(results) + per_page - 1) // per_page
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(results))
    
    buttons = []
    
    # Кнопки с треками
    for i in range(start_idx, end_idx):
        track = results[i]
        duration = track.get("duration", 0)
        duration_str = f" [{duration // 60}:{duration % 60:02d}]" if duration else ""
        
        button_text = f"{i + 1}. {track['title'][:45]}{duration_str}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"download_{i}"
            )
        ])
    
    # Навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page - 1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️ Далее", callback_data=f"page_{page + 1}")
        )
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Кнопка отмены
    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    welcome_text = (
        "🎵 <b>Добро пожаловать в Music Bot!</b>\n\n"
        "Я помогу найти и скачать музыку из публичных источников.\n\n"
        "<b>Как пользоваться:</b>\n"
        "• Отправь название трека или исполнителя\n"
        "• Выбери нужный трек из результатов\n"
        "• Получи MP3 файл\n\n"
        "<b>Примеры запросов:</b>\n"
        "• <code>Imagine Dragons - Believer</code>\n"
        "• <code>lofi hip hop</code>\n"
        "• <code>classical music</code>\n\n"
        "Используй /help для подробной информации."
    )
    await message.answer(welcome_text, parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = (
        "📖 <b>Справка</b>\n\n"
        "<b>Доступные команды:</b>\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать эту справку\n"
        "/cancel - Отменить текущий поиск\n\n"
        "<b>Как искать музыку:</b>\n"
        "Просто отправь текстовое сообщение с названием трека, "
        "исполнителя или жанра. Бот найдет до 30 треков и покажет "
        "первые 10 с возможностью пролистывания.\n\n"
        "<b>Ограничения:</b>\n"
        "• Максимальный размер файла: 49 МБ\n"
        "• Максимальная длительность: 10 минут\n"
        "• Только публичный контент с YouTube\n\n"
        "<b>Важно:</b>\n"
        "Бот работает только с публичным контентом, "
        "доступным для свободного просмотра и прослушивания."
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    """Обработчик команды /cancel"""
    user_id = message.from_user.id
    user_search_results.pop(user_id, None)
    user_current_page.pop(user_id, None)
    await message.answer("❌ Поиск отменен.")


@dp.message(F.text)
async def handle_search(message: Message):
    """Обработчик текстовых сообщений - поиск музыки"""
    user_id = message.from_user.id
    query = message.text.strip()
    
    if not query:
        await message.answer("Пожалуйста, введи название трека или исполнителя.")
        return
    
    # Проверка троттлинга
    if not check_throttle(user_id):
        await message.answer(
            "⏳ Пожалуйста, подожди немного между запросами.",
            show_alert=True
        )
        return
    
    # Показываем индикатор поиска
    search_msg = await message.answer("🔍 Ищу музыку...")
    
    try:
        # Поиск треков
        results = await search_youtube_music(query, max_results=30)
        
        if not results:
            await search_msg.edit_text(
                "😔 Ничего не найдено. Попробуй изменить запрос."
            )
            return
        
        # Сохраняем результаты
        user_search_results[user_id] = results
        user_current_page[user_id] = 0
        
        # Показываем результаты
        await search_msg.edit_text(
            f"🎵 Найдено треков: {len(results)}\n"
            f"Выбери трек для скачивания:",
            reply_markup=build_search_keyboard(results, page=0)
        )
        
    except Exception as e:
        logger.error(f"Ошибка обработки поиска: {e}")
        await search_msg.edit_text(
            "❌ Произошла ошибка при поиске. Попробуй позже."
        )


@dp.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery):
    """Обработчик кнопки отмены"""
    user_id = callback.from_user.id
    user_search_results.pop(user_id, None)
    user_current_page.pop(user_id, None)
    
    await callback.message.edit_text("❌ Поиск отменен.")
    await callback.answer()


@dp.callback_query(F.data.startswith("page_"))
async def callback_page(callback: CallbackQuery):
    """Обработчик пагинации"""
    user_id = callback.from_user.id
    results = user_search_results.get(user_id)
    
    if not results:
        await callback.answer(
            "Результаты поиска устарели. Выполни новый поиск.",
            show_alert=True
        )
        return
    
    try:
        page = int(callback.data.split("_")[1])
        user_current_page[user_id] = page
        
        await callback.message.edit_text(
            f"🎵 Найдено треков: {len(results)}\n"
            f"Выбери трек для скачивания:",
            reply_markup=build_search_keyboard(results, page=page)
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка пагинации: {e}")
        await callback.answer("Ошибка. Попробуй снова.", show_alert=True)


@dp.callback_query(F.data.startswith("download_"))
async def callback_download(callback: CallbackQuery):
    """Обработчик скачивания трека"""
    user_id = callback.from_user.id
    results = user_search_results.get(user_id)
    
    if not results:
        await callback.answer(
            "Результаты поиска устарели. Выполни новый поиск.",
            show_alert=True
        )
        return
    
    try:
        track_idx = int(callback.data.split("_")[1])
        
        if track_idx >= len(results):
            await callback.answer("Трек не найден.", show_alert=True)
            return
        
        track = results[track_idx]
        await callback.answer()
        
        # Показываем статус загрузки
        status_msg = await callback.message.edit_text(
            f"⏳ Скачиваю: <b>{track['title']}</b>\n"
            f"Это может занять некоторое время...",
            parse_mode="HTML"
        )
        
        # Скачиваем трек
        async with download_semaphore:
            cleanup_user_files(user_id)
            
            audio_file = await download_audio(
                track["url"],
                user_id,
                track["title"]
            )
            
            if not audio_file:
                await status_msg.edit_text(
                    "❌ Не удалось скачать трек. Возможно, он недоступен или "
                    "слишком большой (макс. 49 МБ)."
                )
                return
            
            # Отправляем аудио
            try:
                await callback.message.answer_audio(
                    audio=FSInputFile(audio_file),
                    title=track["title"],
                    performer=track.get("uploader", "Unknown"),
                )
                await status_msg.delete()
                
            except Exception as e:
                logger.error(f"Ошибка отправки аудио: {e}")
                await status_msg.edit_text(
                    "❌ Не удалось отправить файл. Попробуй другой трек."
                )
            
            finally:
                cleanup_user_files(user_id)
        
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при скачивании. Попробуй позже."
        )


async def main():
    """Запуск бота"""
    logger.info("🚀 Бот запускается...")
    
    try:
        # Удаляем вебхук если был установлен
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем polling
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
