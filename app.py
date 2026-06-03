"""
Telegram Bot Service - микросервис для отправки сообщений через Telegram Bot API.
Разворачивается на Render.com для обхода блокировки Telegram API.

Новые функции:
- Регистрация через кнопку "Передать контакт"
- Восстановление пароля через Telegram
"""
from flask import Flask, request, jsonify
import requests
import os
import secrets
import hashlib
import time
from datetime import datetime, timedelta

app = Flask(__name__)

# Конфигурация
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8610099496:AAHfZIdVbiRF1exnrMq5N88YxD4T0Tkrefw')
API_SECRET = os.environ.get('API_SECRET', 'your-secret-key-here')  # Для защиты API
WEBAPP_URL = os.environ.get('WEBAPP_URL', 'https://your-domain.com')  # URL фронтенда
DJANGO_API_URL = os.environ.get('DJANGO_API_URL', 'http://localhost:8000/api')  # URL Django API
DJANGO_API_KEY = os.environ.get('DJANGO_API_KEY', 'your-django-api-key')  # API ключ Django

# Хранилище (в production лучше использовать Redis или БД)
# Формат: {phone_number: chat_id}
chat_ids = {}

# Хранилище всех chat_id для рассылок (даже без регистрации на сайте)
# Формат: {chat_id: {'phone': str, 'first_name': str, 'username': str, 'added_at': datetime}}
all_bot_users = {}

# Временные токены для регистрации
# Формат: {token: {'phone': str, 'chat_id': int, 'expires': datetime}}
registration_tokens = {}

# Токены для сброса пароля
# Формат: {token: {'phone': str, 'chat_id': int, 'expires': datetime, 'code': str}}
password_reset_tokens = {}


def generate_token() -> str:
    """Генерирует случайный токен"""
    return secrets.token_urlsafe(32)


def cleanup_expired_tokens():
    """Очищает просроченные токены"""
    now = datetime.now()
    expired_reg = [t for t, data in registration_tokens.items() if data['expires'] < now]
    expired_reset = [t for t, data in password_reset_tokens.items() if data['expires'] < now]
    for t in expired_reg:
        del registration_tokens[t]
    for t in expired_reset:
        del password_reset_tokens[t]


def sync_chat_id_to_django(phone: str, chat_id: int):
    """Синхронизирует chat_id с Django базой"""
    try:
        url = f"{DJANGO_API_URL}/accounts/sync-chat-id/"
        headers = {'Authorization': f'Bearer {DJANGO_API_KEY}'}
        response = requests.post(url, json={'phone': phone, 'chat_id': chat_id}, headers=headers, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"Error syncing chat_id to Django: {e}")
        return False


def send_telegram_message(chat_id: int, text: str, reply_markup: dict = None, parse_mode: str = 'HTML') -> dict:
    """Отправляет сообщение через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        return {
            'success': response.status_code == 200,
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def send_telegram_message_with_button(chat_id: int, text: str, button_text: str, url: str) -> dict:
    reply_markup = {
        'inline_keyboard': [[{
            'text': button_text,
            'url': url
        }]]
    }
    return send_telegram_message(chat_id, text, reply_markup)


@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'service': 'Telegram Bot Service',
        'version': '2.0.0'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json()
    
    message = data.get('message')
    callback_query = data.get('callback_query')
    
    if callback_query:
        return handle_callback_query(callback_query)
    
    if not message:
        return jsonify({'ok': True})
    
    chat_id = message.get('chat', {}).get('id')
    text = message.get('text', '')
    contact = message.get('contact')
    
    if not chat_id:
        return jsonify({'ok': True})
    
    if contact:
        return handle_contact(chat_id, contact)
    
    if text.startswith('/start'):
        return handle_start_command(chat_id, text)
    
    return jsonify({'ok': True})


def handle_contact(chat_id: int, contact: dict):
    phone_number = contact.get('phone_number', '')
    first_name = contact.get('first_name', '')

    if not phone_number:
        send_telegram_message(
            chat_id,
            "❌ Не удалось получить номер телефона. Попробуйте ещё раз."
        )
        return jsonify({'ok': True})

    if phone_number.startswith('8') and len(phone_number) == 11:
        phone_number = '+7' + phone_number[1:]
    elif not phone_number.startswith('+'):
        phone_number = '+' + phone_number

    chat_ids[phone_number] = chat_id
    
    # Сохраняем в список всех пользователей бота для рассылок
    all_bot_users[chat_id] = {
        'phone': phone_number,
        'first_name': first_name,
        'added_at': datetime.now()
    }
    
    sync_chat_id_to_django(phone_number, chat_id)
    
    token = generate_token()
    registration_tokens[token] = {
        'phone': phone_number,
        'chat_id': chat_id,
        'first_name': first_name,
        'expires': datetime.now() + timedelta(minutes=30)
    }
    
    webapp_url = f"{WEBAPP_URL}?telegram_token={token}&telegram_action=register"
    
    send_telegram_message_with_button(
        chat_id,
        f"✅ Номер <code>{phone_number}</code> получен!\n\n"
        f"Нажмите кнопку ниже, чтобы продолжить регистрацию на сайте:",
        "📝 Продолжить регистрацию",
        webapp_url
    )
    
    return jsonify({'ok': True})


def handle_start_command(chat_id: int, text: str):
    parts = text.split()
    
    if len(parts) > 1:
        param = parts[1]  
        
        if param.startswith('reg_'):
            phone = param[4:]  
            chat_ids[phone] = chat_id
            send_telegram_message(
                chat_id,
                f"✅ Номер <code>{phone}</code> привязан!\n\n"
                f"Теперь вы можете использовать этот номер для входа на сайт."
            )
        elif param.startswith('reset_'):
            token = param[6:]
            handle_password_reset_token(chat_id, token)
        else:
            send_contact_request_button(chat_id)
    else:
        send_contact_request_button(chat_id)
    
    return jsonify({'ok': True})


def send_contact_request_button(chat_id: int):
    """Отправляет кнопку запроса контакта"""
    reply_markup = {
        'keyboard': [[{
            'text': '📱 Передать номер телефона',
            'request_contact': True
        }]],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    
    send_telegram_message(
        chat_id,
        "👋 Привет! Я бот для авторизации на сайте <b>Понятная Еда</b>.\n\n"
        "Чтобы зарегистрироваться или восстановить пароль, "
        "нажмите кнопку ниже для передачи номера телефона:",
        reply_markup
    )


def handle_callback_query(callback_query: dict):
    """Обработка callback запросов от inline кнопок"""
    # Пока не используем, но может пригодиться
    return jsonify({'ok': True})


def handle_password_reset_token(chat_id: int, token: str):
    """Обработка токена сброса пароля"""
    cleanup_expired_tokens()
    
    # Ищем токен в базе
    reset_data = None
    for t, data in password_reset_tokens.items():
        if t == token:
            reset_data = data
            break
    
    if not reset_data or reset_data['expires'] < datetime.now():
        send_telegram_message(
            chat_id,
            "❌ Ссылка для восстановления пароля устарела или недействительна.\n"
            "Запросите новую ссылку на сайте."
        )
        return jsonify({'ok': True})
    
    # Обновляем chat_id если нужно
    password_reset_tokens[token]['chat_id'] = chat_id
    chat_ids[reset_data['phone']] = chat_id
    
    # Отправляем код подтверждения
    code = reset_data['code']
    send_telegram_message(
        chat_id,
        f"🔐 <b>Восстановление пароля</b>\n\n"
        f"Ваш код для сброса пароля: <code>{code}</code>\n\n"
        f"⏳ Код действителен 10 минут.\n"
        f"Введите этот код на сайте, чтобы установить новый пароль."
    )


# ==================== API Endpoints для фронтенда ====================

@app.route('/api/initiate-registration', methods=['POST'])
def initiate_registration():
    """
    Инициирует процесс регистрации через Telegram.
    Возвращает ссылку для открытия бота.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone', '').strip()
    
    if not phone:
        return jsonify({'error': 'Phone is required'}), 400
    
    # Нормализуем номер
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    phone = '+' + digits
    
    # Проверяем, не зарегистрирован ли уже
    if phone in chat_ids:
        return jsonify({
            'error': 'Phone already registered',
            'message': 'Этот номер уже привязан к Telegram'
        }), 409
    
    # Генерируем токен для регистрации
    token = generate_token()
    registration_tokens[token] = {
        'phone': phone,
        'chat_id': None,
        'expires': datetime.now() + timedelta(minutes=30)
    }
    
    # Формируем ссылку на бота
    bot_username = get_bot_username()
    if not bot_username:
        return jsonify({'error': 'Bot username not available'}), 500
    
    telegram_link = f"https://t.me/{bot_username}?start={token}"
    
    return jsonify({
        'success': True,
        'telegram_link': telegram_link,
        'token': token,
        'expires_in': 1800  # 30 минут в секундах
    })


@app.route('/api/check-registration/<token>', methods=['GET'])
def check_registration(token: str):
    """
    Проверяет статус регистрации по токену.
    Возвращает phone и chat_id если пользователь передал контакт.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    cleanup_expired_tokens()
    
    data = registration_tokens.get(token)
    if not data:
        return jsonify({
            'error': 'Token not found or expired',
            'status': 'expired'
        }), 404
    
    if data['expires'] < datetime.now():
        del registration_tokens[token]
        return jsonify({
            'error': 'Token expired',
            'status': 'expired'
        }), 410
    
    # Если chat_id есть - значит пользователь передал контакт
    if data.get('chat_id'):
        return jsonify({
            'success': True,
            'status': 'completed',
            'phone': data['phone'],
            'chat_id': data['chat_id'],
            'first_name': data.get('first_name', '')
        })
    
    return jsonify({
        'success': True,
        'status': 'pending',
        'phone': data['phone']
    })


@app.route('/api/initiate-password-reset', methods=['POST'])
def initiate_password_reset():
    """
    Инициирует восстановление пароля.
    Принимает phone, возвращает ссылку на бота.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone', '').strip()
    
    if not phone:
        return jsonify({'error': 'Phone is required'}), 400
    
    # Нормализуем номер
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    phone = '+' + digits
    
    # Проверяем, есть ли chat_id для этого номера
    chat_id = chat_ids.get(phone)
    
    # Генерируем код и токен
    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    token = generate_token()
    
    password_reset_tokens[token] = {
        'phone': phone,
        'chat_id': chat_id,
        'code': code,
        'expires': datetime.now() + timedelta(minutes=10)
    }
    
    bot_username = get_bot_username()
    if not bot_username:
        return jsonify({'error': 'Bot username not available'}), 500
    
    # Формируем ссылку для открытия бота (всегда)
    telegram_link = f"https://t.me/{bot_username}?start=reset_{token}"
    
    # Если chat_id известен - отправляем код сразу
    if chat_id:
        send_telegram_message(
            chat_id,
            f"🔐 <b>Восстановление пароля</b>\n\n"
            f"Ваш код для сброса пароля: <code>{code}</code>\n\n"
            f"⏳ Код действителен 10 минут."
        )
        code_sent_directly = True
    else:
        code_sent_directly = False
    
    return jsonify({
        'success': True,
        'telegram_link': telegram_link,
        'token': token,
        'code_sent_directly': code_sent_directly,
        'expires_in': 600  # 10 минут
    })


@app.route('/api/verify-reset-code', methods=['POST'])
def verify_reset_code():
    """
    Проверяет код восстановления пароля.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    token = data.get('token')
    code = data.get('code')
    
    if not token or not code:
        return jsonify({'error': 'Token and code are required'}), 400
    
    cleanup_expired_tokens()
    
    reset_data = password_reset_tokens.get(token)
    if not reset_data:
        return jsonify({'error': 'Invalid or expired token'}), 404
    
    if reset_data['expires'] < datetime.now():
        del password_reset_tokens[token]
        return jsonify({'error': 'Token expired'}), 410
    
    if reset_data['code'] != code:
        return jsonify({'error': 'Invalid code'}), 400
    
    # Код верный - возвращаем временный токен для смены пароля
    temp_token = generate_token()
    password_reset_tokens[temp_token] = {
        'phone': reset_data['phone'],
        'chat_id': reset_data['chat_id'],
        'verified': True,
        'expires': datetime.now() + timedelta(minutes=10)
    }
    
    # Удаляем старый токен
    if token in password_reset_tokens:
        del password_reset_tokens[token]
    
    return jsonify({
        'success': True,
        'temp_token': temp_token,
        'phone': reset_data['phone']
    })


@app.route('/send-code', methods=['POST'])
def send_code():
    """
    API для отправки кода подтверждения (устаревший, для совместимости).
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    phone = data.get('phone')
    code = data.get('code')
    
    if not phone or not code:
        return jsonify({'error': 'Phone and code are required'}), 400
    
    chat_id = chat_ids.get(phone)
    if not chat_id:
        return jsonify({
            'error': 'Phone not linked to Telegram',
            'message': 'Пользователь не привязал номер к Telegram.'
        }), 404
    
    message = (
        f"🔐 <b>Код подтверждения</b>\n\n"
        f"Ваш код: <code>{code}</code>\n\n"
        f"⏳ Код действителен 10 минут."
    )
    
    result = send_telegram_message(chat_id, message)
    
    if result['success']:
        return jsonify({'success': True, 'message': 'Code sent successfully'})
    else:
        return jsonify({
            'error': 'Failed to send message',
            'details': result.get('error') or result.get('response')
        }), 500


@app.route('/check-phone/<phone>', methods=['GET'])
def check_phone(phone: str):
    """Проверяет, привязан ли номер к Telegram"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    chat_id = chat_ids.get(phone)
    return jsonify({
        'phone': phone,
        'linked': chat_id is not None,
        'chat_id': chat_id if chat_id else None
    })


@app.route('/send-broadcast', methods=['POST'])
def send_broadcast():
    """
    API для отправки рассылок.
    Поддерживает отправку текста и фото с подписью.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    chat_id = request.form.get('chat_id') or request.json.get('chat_id')
    text = request.form.get('caption') or request.json.get('text')
    parse_mode = request.form.get('parse_mode') or request.json.get('parse_mode', 'HTML')
    
    if not chat_id:
        return jsonify({'error': 'chat_id is required'}), 400
    
    # Проверяем, есть ли фото в запросе
    if 'photo' in request.files:
        photo = request.files['photo']
        return send_telegram_photo(chat_id, photo, text, parse_mode)
    
    # Отправка только текста
    if not text:
        return jsonify({'error': 'Text or photo is required'}), 400
    
    result = send_telegram_message(chat_id, text, parse_mode=parse_mode)
    
    if result['success']:
        return jsonify({'success': True, 'message': 'Broadcast sent successfully'})
    else:
        return jsonify({
            'error': 'Failed to send broadcast',
            'details': result.get('error') or result.get('response')
        }), 500


def send_telegram_photo(chat_id: int, photo, caption: str = None, parse_mode: str = 'HTML') -> dict:
    """Отправляет фото через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    
    files = {'photo': (photo.filename, photo.stream, photo.content_type)}
    data = {'chat_id': chat_id}
    
    if caption:
        data['caption'] = caption
        data['parse_mode'] = parse_mode
    
    try:
        response = requests.post(url, data=data, files=files, timeout=30)
        return {
            'success': response.status_code == 200,
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@app.route('/api/broadcast-to-all', methods=['POST'])
def broadcast_to_all():
    """
    API для массовой рассылки всем пользователям бота.
    Не требует регистрации на сайте.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    text = data.get('text', '')
    parse_mode = data.get('parse_mode', 'HTML')
    image_url = data.get('image_url')  # URL изображения для отправки
    
    if not text and not image_url:
        return jsonify({'error': 'Text or image is required'}), 400
    
    if not all_bot_users:
        return jsonify({'error': 'No users found'}), 404
    
    sent_count = 0
    failed_count = 0
    
    for chat_id in all_bot_users.keys():
        if image_url:
            # Отправка фото с подписью
            result = send_broadcast_photo_to_chat(chat_id, image_url, text, parse_mode)
        else:
            # Отправка только текста
            result = send_telegram_message(chat_id, text, parse_mode=parse_mode)
        
        if result['success']:
            sent_count += 1
        else:
            failed_count += 1
    
    return jsonify({
        'success': True,
        'sent_count': sent_count,
        'failed_count': failed_count,
        'total': len(all_bot_users)
    })


def send_broadcast_photo_to_chat(chat_id: int, image_url: str, caption: str = None, parse_mode: str = 'HTML') -> dict:
    """Отправляет фото по URL через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    
    try:
        # Скачиваем изображение
        img_response = requests.get(image_url, timeout=30)
        if img_response.status_code != 200:
            return {'success': False, 'error': 'Failed to download image'}
        
        files = {'photo': ('image.jpg', img_response.content, 'image/jpeg')}
        data = {'chat_id': chat_id}
        
        if caption:
            data['caption'] = caption
            data['parse_mode'] = parse_mode
        
        response = requests.post(url, data=data, files=files, timeout=30)
        return {
            'success': response.status_code == 200,
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


@app.route('/api/all-chat-ids', methods=['GET'])
def get_all_chat_ids():
    """
    API для получения всех chat_id пользователей бота.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    
    users = []
    for chat_id, info in all_bot_users.items():
        users.append({
            'chat_id': chat_id,
            'phone': info.get('phone'),
            'first_name': info.get('first_name')
        })
    
    return jsonify({
        'success': True,
        'count': len(users),
        'users': users
    })


def get_bot_username() -> str:
    """Получает username бота через Bot API"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return data['result'].get('username', '')
    except Exception as e:
        print(f"Error getting bot username: {e}")
    return ''


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
