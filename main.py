import os
import telebot
import requests
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

# ========== НАСТРОЙКИ (БЕРУТСЯ ИЗ RENDER ENVIRONMENT) ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

# Вывод в логи (чтобы видеть, видит ли Render переменные)
print(f"DEBUG: BOT_TOKEN = {repr(BOT_TOKEN)}")
print(f"DEBUG: ADMIN_ID = {repr(ADMIN_ID_RAW)}")

# Жесткая проверка токена перед запуском бота
if not BOT_TOKEN or ":" not in str(BOT_TOKEN):
    raise ValueError("ОШИБКА: Токен не найден или указан неверно! Проверьте Environment в Render.")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except (TypeError, ValueError):
    print("ВНИМАНИЕ: ADMIN_ID не задан или не является числом. Бот не сможет проверять права.")
    ADMIN_ID = 0

bot = telebot.TeleBot(BOT_TOKEN)

# ========== МИНИ ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
# Бесплатный сервис на Render ОБЯЗАН слушать HTTP порт, иначе деплой упадет
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот успешно запущен и работает!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ========== УНИВЕРСАЛЬНАЯ ПРОВЕРКА ССЫЛКИ И ЛОГИНА ==========
def check_link_login(site_url, login):
    """Проверка доступности ссылки и валидации логина"""
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        
        if not site_url.startswith(('http://', 'https://')):
            target_url = 'https://' + site_url
        else:
            target_url = site_url

        response = session.get(target_url, timeout=10, allow_redirects=True)
        
        if response.status_code < 400:
            if len(login.strip()) > 0:
                return f"✅ Доступно ({response.status_code})"
            return "❌ Пустой логин"
        else:
            return f"❌ Ошибка сайта ({response.status_code})"
            
    except requests.exceptions.Timeout:
        return "⚠️ Таймаут соединения"
    except requests.exceptions.ConnectionError:
        return "❌ Сайт недоступен"
    except Exception as e:
        return f"⚠️ Ошибка: {str(e)[:20]}"

def process_item(site_url, login):
    """Обработка строки данных"""
    status = check_link_login(site_url, login)
    return {'url': site_url, 'login': login, 'status': status}

# ========== ПАРСИНГ ФАЙЛА ==========
def parse_file(file_content):
    content = file_content.decode('utf-8', errors='ignore')
    lines = content.splitlines()
    items = []
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        for sep in [':', ';', '\t', '|', ' ']:
            parts = line.split(sep, 1)
            if len(parts) == 2:
                url = parts[0].strip()
                login = parts[1].strip()
                if '.' in url and len(login) > 0:
                    items.append({'url': url, 'login': login})
                    break
    return items

# ========== ОБРАБОТЧИКИ БОТА ==========
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ Доступ запрещен")
        return
    
    bot.reply_to(message, 
        "🚀 **Чекер ссылок и логинов v3.0**\n\n"
        "📂 Отправь текстовый файл со списком в формате:\n"
        "`ссылка : логин` (или через `|`, `;`, табуляцию)\n\n"
        "⚡ Бот проверит доступность сайтов в многопоточном режиме!"
    )

@bot.message_handler(content_types=['document'])
def handle_file(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ Доступ запрещен")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        file_content = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка загрузки файла: {e}")
        return
    
    items = parse_file(file_content)
    if not items:
        bot.reply_to(message, "❌ Не удалось найти строки в нужном формате.")
        return
    
    msg = bot.reply_to(message, f"📊 Найдено записей: {len(items)}. Запуск проверки...")
    
    results = []
    valid = []
    invalid = []
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_item, item['url'], item['login']): item for item in items}
        
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            
            if '✅' in result['status']:
                valid.append(result)
            else:
                invalid.append(result)
            
            if i % 10 == 0 or i == len(items):
                try:
                    bot.edit_message_text(
                        f"⏳ Проверено {i}/{len(items)}...\n✅ Успешно: {len(valid)} | ❌ Ошибок: {len(invalid)}",
                        chat_id=message.chat.id,
                        message_id=msg.message_id
                    )
                except:
                    pass
    
    # Формирование отчета
    report = f"📋 **РЕЗУЛЬТАТЫ ПРОВЕРКИ**\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    report += f"📊 Всего: {len(results)}\n✅ Валидных: {len(valid)}\n❌ Невалидных: {len(invalid)}\n"
    report += "─" * 30 + "\n\n"
    
    if valid:
        report += "🔹 **УСПЕШНЫЕ (первые 30)**:\n"
        for v in valid[:30]:
            report += f"• {v['url']} | {v['login']} — {v['status']}\n"
        if len(valid) > 30:
            report += f"... и еще {len(valid) - 30}\n"
    
    if len(report) > 4000:
        for i in range(0, len(report), 4000):
            bot.send_message(message.chat.id, report[i:i+4000])
    else:
        bot.send_message(message.chat.id, report)
    
    # Файл с результатами
    if valid:
        valid_file = f"checked_links_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(valid_file, 'w', encoding='utf-8') as f:
            for v in valid:
                f.write(f"{v['url']} | {v['login']}\n")
        
        with open(valid_file, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="✅ Список успешных ссылок")

# ========== ЗАПУСК СИСТЕМЫ ==========
if __name__ == "__main__":
    print("🚀 Запуск веб-сервера (в фоне) и Telegram-бота...")
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # Запускаем бота
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
