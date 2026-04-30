import asyncio
import gc
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import CommandStart
from aiohttp import web
import yt_dlp


load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения")

TOKEN = TOKEN.strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()

search_results = {}
current_page = {}

DOWNLOAD_DIR = Path("downloads")
MAX_FILE_SIZE = 45 * 1024 * 1024
CLEANUP_AFTER_MINUTES = 30


SEARCH_OPTS = {
    "quiet": True,
    "extract_flat": True,
}


AUDIO_DOWNLOAD_OPTS = {
    "format": "bestaudio/best",
    "outtmpl": str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
    "quiet": True,
    "ignoreerrors": True,
    "noplaylist": True,
}


VIDEO_DOWNLOAD_OPTS = {
    "format": (
        "bestvideo[ext=mp4][height<=720][filesize<45M]+bestaudio[ext=m4a]/"
        "best[ext=mp4][height<=720][filesize<45M]/"
        "best[ext=mp4][height<=720]/"
        "best"
    ),
    "outtmpl": str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
    "quiet": True,
    "ignoreerrors": True,
    "merge_output_format": "mp4",
    "noplaylist": True,
}


def cleanup_downloads(max_age_minutes: int = CLEANUP_AFTER_MINUTES):
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    now = time.time()

    for file_path in DOWNLOAD_DIR.glob("*"):
        if not file_path.is_file():
            continue

        try:
            age = now - file_path.stat().st_mtime
            if age > max_age_minutes * 60:
                file_path.unlink()
        except OSError:
            pass

    gc.collect()


def is_url(text: str) -> bool:
    url_pattern = re.compile(r"^https?://\S+$", re.IGNORECASE)
    return bool(url_pattern.match(text.strip()))


def get_url_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_video_platform_url(url: str) -> bool:
    host = get_url_host(url)
    video_hosts = (
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "instagram.com",
        "instagr.am",
        "pinterest.com",
        "pin.it",
    )
    return any(host == h or host.endswith("." + h) for h in video_hosts)


def find_downloaded_file(media_id: str) -> str | None:
    for file_path in DOWNLOAD_DIR.glob(f"{media_id}.*"):
        return str(file_path)
    return None


async def search_all(query: str, limit: int = 10) -> list[dict]:
    def _search():
        results = []

        with yt_dlp.YoutubeDL(SEARCH_OPTS) as ydl:
            try:
                yt_info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                for entry in yt_info.get("entries", []) or []:
                    if entry:
                        results.append({
                            "title": f"[YT] {entry.get('title', 'Без названия')}",
                            "url": f"https://www.youtube.com/watch?v={entry['id']}",
                        })
            except Exception:
                pass

            try:
                sc_info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                for entry in sc_info.get("entries", []) or []:
                    if entry:
                        results.append({
                            "title": f"[SC] {entry.get('title', 'Без названия')}",
                            "url": entry["url"],
                        })
            except Exception:
                pass

        seen = set()
        unique = []

        for result in results:
            if result["url"] not in seen:
                seen.add(result["url"])
                unique.append(result)

        return unique

    return await asyncio.to_thread(_search)


async def download_audio(url: str):
    def _download():
        cleanup_downloads()

        with yt_dlp.YoutubeDL(AUDIO_DOWNLOAD_OPTS) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None

        media_id = info.get("id")
        title = info.get("title", "Трек")

        if not media_id:
            return None, title

        path = find_downloaded_file(media_id)
        return path, title

    return await asyncio.to_thread(_download)


async def download_video(url: str):
    def _download():
        cleanup_downloads()

        with yt_dlp.YoutubeDL(VIDEO_DOWNLOAD_OPTS) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None

        media_id = info.get("id")
        title = info.get("title", "Видео")

        if not media_id:
            return None, title

        path = find_downloaded_file(media_id)
        return path, title

    return await asyncio.to_thread(_download)


def build_page_keyboard(tracks, page=0, per_page=10):
    total_pages = max(1, -(-len(tracks) // per_page))
    start = page * per_page
    end = start + per_page
    page_tracks = tracks[start:end]

    buttons = []

    for i, track in enumerate(page_tracks, start=start):
        buttons.append([
            InlineKeyboardButton(
                text=f"{i + 1}. {track['title'][:50]}",
                callback_data=f"dl_{i}",
            )
        ])

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"page_{page - 1}",
            )
        )

    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд ➡️",
                callback_data=f"page_{page + 1}",
            )
        )

    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🎵 Привет. Я нахожу и скачиваю музыку и видео.\n\n"
        "▫️ Отправь название или исполнителя — покажу список треков.\n"
        "▫️ Отправь ссылку на TikTok / Instagram / YouTube / Pinterest — скачаю видео.\n"
        "▫️ Отправь другую ссылку — попробую скачать MP3.\n\n"
        "Листай результаты стрелками ⬅️➡️ и выбирай."
    )


@dp.message(F.text)
async def handle_message(message: Message):
    text = message.text.strip()

    if not text:
        return await message.answer("Введи название трека или ссылку.")

    if is_url(text):
        if is_video_platform_url(text):
            state = await message.answer("⏳ Скачиваю видео по ссылке...")
            path, title = await download_video(text)

            if not path:
                return await state.edit_text(
                    "❌ Не удалось скачать видео.\n\n"
                    "Возможные причины: приватный контент, ограничение платформы, "
                    "нужна авторизация или ссылка не поддерживается."
                )

            return await _send_video_and_clean(message.chat.id, path, state, title)

        state = await message.answer("⏳ Скачиваю аудио по ссылке...")
        path, title = await download_audio(text)

        if not path:
            return await state.edit_text("❌ Не удалось скачать.")

        return await _send_audio_and_clean(message.chat.id, path, state, title)

    await message.answer(f"🔍 Ищу везде: {text}...")

    tracks = await search_all(text, limit=10)

    if not tracks:
        return await message.answer("😔 Ничего не найдено.")

    search_results[message.chat.id] = tracks
    current_page[message.chat.id] = 0

    await message.answer(
        "🎶 Результаты поиска:",
        reply_markup=build_page_keyboard(tracks, 0),
    )


@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    data = callback.data

    if data.startswith("dl_"):
        tracks = search_results.get(chat_id)

        if not tracks:
            return await callback.answer("Результаты устарели.")

        try:
            idx = int(data[3:])
        except ValueError:
            return await callback.answer("Ошибка.")

        if idx < 0 or idx >= len(tracks):
            return await callback.answer("Трек не найден.")

        url = tracks[idx]["url"]

        await callback.answer()
        state = await callback.message.answer("⏳ Скачиваю...")

        path, title = await download_audio(url)

        if not path:
            return await state.edit_text("❌ Не удалось скачать трек.")

        return await _send_audio_and_clean(chat_id, path, state, title)

    if data.startswith("page_"):
        try:
            page = int(data[5:])
        except ValueError:
            return await callback.answer("Неверная страница.")

        tracks = search_results.get(chat_id)

        if not tracks:
            return await callback.answer("Результаты устарели.")

        current_page[chat_id] = page

        await callback.message.edit_text(
            f"🎶 Результаты поиска (страница {page + 1}):",
            reply_markup=build_page_keyboard(tracks, page),
        )

        await callback.answer()


async def _check_file_or_report(path: str, status) -> bool:
    try:
        size = os.path.getsize(path)
    except OSError:
        await status.edit_text("❌ Файл недоступен.")
        return False

    if size > MAX_FILE_SIZE:
        await status.edit_text("⚠️ Файл слишком большой (>45 МБ).")

        try:
            os.remove(path)
        except OSError:
            pass

        gc.collect()
        return False

    return True


async def _send_audio_and_clean(chat_id, path, status, title):
    if not await _check_file_or_report(path, status):
        return

    try:
        await bot.send_audio(
            chat_id,
            FSInputFile(path),
            title=title,
        )
    except Exception as e:
        await status.edit_text(f"❌ Ошибка отправки аудио: {e}")
    else:
        await status.delete()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

        gc.collect()


async def _send_video_and_clean(chat_id, path, status, title):
    if not await _check_file_or_report(path, status):
        return

    try:
        await bot.send_video(
            chat_id,
            FSInputFile(path),
            caption=title[:1024] if title else None,
            supports_streaming=True,
        )
    except Exception as e:
        await status.edit_text(f"❌ Ошибка отправки видео: {e}")
    else:
        await status.delete()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

        gc.collect()


async def health(request):
    return web.Response(text="OK")


async def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    cleanup_downloads()

    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
