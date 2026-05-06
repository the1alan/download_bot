import asyncio
from dotenv import load_dotenv
import os
import re
from pathlib import Path
from urllib.parse import urlparse

load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from dotenv import load_dotenv
import yt_dlp


TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения")

bot = Bot(token=TOKEN)
dp = Dispatcher()

search_results = {}
current_page = {}

DOWNLOAD_DIR = Path("downloads")
MAX_FILE_SIZE = 45 * 1024 * 1024

SEARCH_OPTS = {
    "quiet": True,
    "extract_flat": True,
}

AUDIO_DOWNLOAD_OPTS = {
    "format": "bestaudio/best",
    "outtmpl": str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
    "quiet": True,
    "ignoreerrors": True,
}

VIDEO_DOWNLOAD_OPTS = {
    # Стараемся взять mp4 до 45 МБ. Если точный размер неизвестен,
    # yt-dlp всё равно может скачать файл больше лимита, поэтому ниже есть проверка размера.
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


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", text.strip(), re.IGNORECASE))


def get_url_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_youtube_url(url: str) -> bool:
    host = get_url_host(url)
    youtube_hosts = ("youtube.com", "youtu.be", "music.youtube.com")
    return any(host == h or host.endswith("." + h) for h in youtube_hosts)


def is_video_platform_url(url: str) -> bool:
    host = get_url_host(url)
    video_hosts = (
        "youtube.com",
        "youtu.be",
        "music.youtube.com",
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
                            "title": f"[YT] {entry['title']}",
                            "url": f"https://www.youtube.com/watch?v={entry['id']}",
                        })
            except Exception:
                pass

            try:
                sc_info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                for entry in sc_info.get("entries", []) or []:
                    if entry:
                        results.append({
                            "title": f"[SC] {entry['title']}",
                            "url": entry["url"],
                        })
            except Exception:
                pass

        seen = set()
        unique = []
        for r in results:
            if r["url"] not in seen:
                seen.add(r["url"])
                unique.append(r)
        return unique

    return await asyncio.to_thread(_search)


def find_downloaded_file(video_id: str) -> str | None:
    for file_path in DOWNLOAD_DIR.glob(f"{video_id}.*"):
        return str(file_path)
    return None


async def download_audio(url: str):
    def _download():
        DOWNLOAD_DIR.mkdir(exist_ok=True)

        with yt_dlp.YoutubeDL(AUDIO_DOWNLOAD_OPTS) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None

        video_id = info.get("id")
        title = info.get("title", "Трек")

        if not video_id:
            return None, title

        path = find_downloaded_file(video_id)
        return path, title

    return await asyncio.to_thread(_download)


async def download_video(url: str):
    def _download():
        DOWNLOAD_DIR.mkdir(exist_ok=True)

        with yt_dlp.YoutubeDL(VIDEO_DOWNLOAD_OPTS) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None

        video_id = info.get("id")
        title = info.get("title", "Видео")

        if not video_id:
            return None, title

        path = find_downloaded_file(video_id)
        return path, title

    return await asyncio.to_thread(_download)


def build_page_keyboard(tracks: list[dict], page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    total_pages = max(1, -(-len(tracks) // per_page))
    start = page * per_page
    end = start + per_page
    page_tracks = tracks[start:end]

    buttons = []
    for i, t in enumerate(page_tracks, start=start):
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


def build_youtube_format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎵 Скачать MP3", callback_data="yt_audio"),
                InlineKeyboardButton(text="🎬 Скачать MP4", callback_data="yt_video"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
            ],
        ]
    )


def build_media_action_keyboard(url_key: str = "direct") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Скачать видео", callback_data=f"{url_key}_video"),
            ],
            [
                InlineKeyboardButton(text="🎵 Попробовать как аудио", callback_data=f"{url_key}_audio"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
            ],
        ]
    )


async def check_file_or_report(path: Path, status) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        await status.edit_text("❌ Файл недоступен.")
        return False

    if size > MAX_FILE_SIZE:
        await status.edit_text(
            f"⚠️ Файл слишком большой: {size / 1024 / 1024:.1f} МБ.\n"
            f"Лимит: {MAX_FILE_SIZE / 1024 / 1024:.0f} МБ."
        )
        return False

    return True


# =========================
# YT-DLP
# =========================

SEARCH_OPTS = {
    "quiet": True,
    "extract_flat": True,
    "noplaylist": True,
}


async def search_all(query: str, limit: int = 10) -> list[dict]:
    def _search() -> list[dict]:
        results = []

        with yt_dlp.YoutubeDL(SEARCH_OPTS) as ydl:
            try:
                yt_info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                for entry in yt_info.get("entries", []) or []:
                    if entry and entry.get("id") and entry.get("title"):
                        results.append({
                            "title": f"[YT] {entry['title']}",
                            "url": f"https://www.youtube.com/watch?v={entry['id']}",
                        })
            except Exception as e:
                logging.warning("YouTube search error: %s", e)

            try:
                sc_info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                for entry in sc_info.get("entries", []) or []:
                    if entry and entry.get("url") and entry.get("title"):
                        results.append({
                            "title": f"[SC] {entry['title']}",
                            "url": entry["url"],
                        })
            except Exception as e:
                logging.warning("SoundCloud search error: %s", e)

        seen = set()
        unique = []
        for item in results:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)

        return unique

    return await asyncio.to_thread(_search)


def extract_metadata(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "ignoreerrors": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def validate_metadata(info: dict | None) -> tuple[bool, str]:
    if not info:
        return False, "❌ Не удалось получить информацию о файле."

    duration = info.get("duration")
    if duration and duration > MAX_DURATION_SECONDS:
        return (
            False,
            f"⚠️ Слишком длинное медиа: {duration // 60} мин.\n"
            f"Лимит: {MAX_DURATION_SECONDS // 60} мин."
        )

    return True, ""


async def download_audio(url: str) -> tuple[Path | None, str | None, Path | None, str | None]:
    workdir = None

    def _download():
        nonlocal workdir
        workdir = create_job_dir(chat_id=0)

        info = extract_metadata(url)
        ok, error = validate_metadata(info)
        if not ok:
            return None, None, workdir, error

        title = safe_title(info.get("title") if info else None, "audio")

        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(workdir / "%(title).120s_%(id)s.%(ext)s"),
            "quiet": True,
            "ignoreerrors": True,
            "noplaylist": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

        path = get_largest_media_file(workdir)
        return path, title, workdir, None

    return await asyncio.to_thread(_download)


async def download_video(url: str) -> tuple[Path | None, str | None, Path | None, str | None]:
    workdir = None

    def _download():
        nonlocal workdir
        workdir = create_job_dir(chat_id=0)

        info = extract_metadata(url)
        ok, error = validate_metadata(info)
        if not ok:
            return None, None, workdir, error

        title = safe_title(info.get("title") if info else None, "video")

        opts = {
            "format": (
                "bestvideo[ext=mp4][height<=720][filesize<45M]+bestaudio[ext=m4a]/"
                "best[ext=mp4][height<=720][filesize<45M]/"
                "best[ext=mp4][height<=720]/"
                "best"
            ),
            "outtmpl": str(workdir / "%(title).120s_%(id)s.%(ext)s"),
            "quiet": True,
            "ignoreerrors": True,
            "merge_output_format": "mp4",
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

        path = get_largest_media_file(workdir)
        return path, title, workdir, None

    return await asyncio.to_thread(_download)


# =========================
# SENDERS
# =========================

async def send_audio_and_clean(chat_id: int, path: Path, status, title: str | None, workdir: Path | None):
    try:
        if not await check_file_or_report(path, status):
            return

        await bot.send_audio(
            chat_id=chat_id,
            audio=FSInputFile(path),
            title=title or "Аудио",
        )
        await status.delete()

    except Exception as e:
        logging.exception("Ошибка отправки аудио")
        await status.edit_text(f"❌ Ошибка отправки аудио: {e}")

    finally:
        cleanup_path(workdir or path.parent)


async def send_video_and_clean(chat_id: int, path: Path, status, title: str | None, workdir: Path | None):
    try:
        if not await check_file_or_report(path, status):
            return

        await bot.send_video(
            chat_id=chat_id,
            video=FSInputFile(path),
            caption=(title or "Видео")[:1024],
            supports_streaming=True,
        )
        await status.delete()

    except Exception as e:
        logging.exception("Ошибка отправки видео")
        await status.edit_text(f"❌ Ошибка отправки видео: {e}")

    finally:
        cleanup_path(workdir or path.parent)


async def run_download_job(message_or_callback_message: Message, url: str, mode: str):
    chat_id = message_or_callback_message.chat.id

    async with download_semaphore:
        if mode == "audio":
            status = await message_or_callback_message.answer("⏳ Скачиваю аудио...")
            path, title, workdir, error = await download_audio(url)

            if error:
                cleanup_path(workdir)
                return await status.edit_text(error)

            if not path:
                cleanup_path(workdir)
                return await status.edit_text("❌ Не удалось скачать аудио.")

            return await send_audio_and_clean(chat_id, path, status, title, workdir)

        if mode == "video":
            status = await message_or_callback_message.answer("⏳ Скачиваю видео...")
            path, title, workdir, error = await download_video(url)

            if error:
                cleanup_path(workdir)
                return await status.edit_text(error)

            if not path:
                cleanup_path(workdir)
                return await status.edit_text(
                    "❌ Не удалось скачать видео.\n\n"
                    "Возможные причины: приватный контент, ограничение платформы, "
                    "нужна авторизация или ссылка не поддерживается."
                )

            return await send_video_and_clean(chat_id, path, status, title, workdir)


# =========================
# HANDLERS
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🎵 Привет. Я скачиваю аудио и видео.\n\n"
        "▫️ Отправь название или исполнителя — найду треки в YouTube/SoundCloud.\n"
        "▫️ Отправь YouTube-ссылку — предложу MP3 или MP4.\n"
        "▫️ Отправь TikTok / Instagram / Pinterest — попробую скачать видео.\n\n"
        "Лимиты:\n"
        f"▫️ Размер файла: до {MAX_FILE_SIZE // 1024 // 1024} МБ\n"
        f"▫️ Длительность: до {MAX_DURATION_SECONDS // 60} мин\n"
        f"▫️ Одновременно загрузок: {MAX_CONCURRENT_DOWNLOADS}"
    )


@dp.message(F.text)
async def handle_message(message: Message):
    text = message.text.strip()

    if not text:
        return await message.answer("Введи название трека или ссылку.")

    if is_url(text):
        chat_id = message.chat.id
        pending_urls[chat_id] = text

        if is_youtube_url(text):
            return await message.answer(
                "Выбери формат для YouTube:",
                reply_markup=build_youtube_format_keyboard(),
            )

        if is_video_platform_url(text):
            return await message.answer(
                "Что скачать по этой ссылке?",
                reply_markup=build_media_action_keyboard(),
            )

        return await run_download_job(message, text, "audio")

    await message.answer(f"🔍 Ищу: {text}...")

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
    data = callback.data or ""

    if data == "cancel":
        pending_urls.pop(chat_id, None)
        await callback.message.edit_text("Отменено.")
        return await callback.answer()

    if data in {"yt_audio", "yt_video", "direct_audio", "direct_video"}:
        url = pending_urls.get(chat_id)
        if not url:
            return await callback.answer("Ссылка устарела. Отправь её заново.", show_alert=True)

        mode = "audio" if data.endswith("_audio") else "video"

        await callback.answer()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        return await run_download_job(callback.message, url, mode)

    if data.startswith("dl_"):
        tracks = search_results.get(chat_id)

        if not tracks:
            return await callback.answer("Результаты устарели.", show_alert=True)

        try:
            idx = int(data[3:])
        except ValueError:
            return await callback.answer("Ошибка.", show_alert=True)

        if idx < 0 or idx >= len(tracks):
            return await callback.answer("Трек не найден.", show_alert=True)

        url = tracks[idx]["url"]

        await callback.answer()
        return await run_download_job(callback.message, url, "audio")

    if data.startswith("page_"):
        tracks = search_results.get(chat_id)

        if not tracks:
            return await callback.answer("Результаты устарели.", show_alert=True)

        try:
            page = int(data[5:])
        except ValueError:
            return await callback.answer("Неверная страница.", show_alert=True)

        current_page[chat_id] = page

        await callback.message.edit_text(
            f"🎶 Результаты поиска, страница {page + 1}:",
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
        return False

    return True


async def _send_audio_and_clean(chat_id, path, status, title):
    if not await _check_file_or_report(path, status):
        return

    try:
        await bot.send_audio(chat_id, FSInputFile(path), title=title)
    except Exception as e:
        await status.edit_text(f"❌ Ошибка отправки аудио: {e}")
    else:
        await status.delete()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


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


# Заглушка для Render
async def health(request):
    return web.Response(text="OK")


async def main():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logging.info("Health server started on port %s", PORT)
    logging.info("Bot polling started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())