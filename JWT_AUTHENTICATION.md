# JWT Authentication для PowerGSLB Admin Panel

Цей документ описує реалізацію JWT (JSON Web Token) аутентифікації для адміністративної панелі PowerGSLB.

## Огляд

PowerGSLB тепер підтримує два методи аутентифікації:

1. **Basic Auth** (традиційний) - username та password в заголовку `Authorization`
2. **JWT Token** (новий) - Bearer token, отриманий через `/admin/login` endpoint

## Компоненти

### 1. JWT Token Manager (`system/jwt_token.py`)

Клас `JWTTokenManager` отримує та перевіряє JWT токени.

**Можливості:**
- Генерація JWT токенів з інформацією користувача
- Валідація токенів
- Перевірка терміну дії токена
- Підтримка HS256 алгоритму

**Конфігурація:**
```python
jwt_manager = JWTTokenManager(
    secret_key='your-secret-key',      # Секретний ключ для підписання
    algorithm='HS256',                  # Алгоритм (за замовчуванням HS256)
    expiration_hours=24                 # Час видачі токена в годинах
)
```

### 2. Login Endpoint (`/admin/login`)

POST запит до `/admin/login` повертає JWT токен.

**Запит:**
```json
{
    "username": "admin",
    "password": "password123"
}
```

**Успішна відповідь (200):**
```json
{
    "status": "success",
    "message": "Login successful",
    "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
    "user": {
        "id": 1,
        "username": "admin",
        "name": "Administrator"
    }
}
```

**Помилка (400):**
```json
{
    "status": "error",
    "message": "Invalid username or password"
}
```

### 3. Login Page (`resources/admin/login.html`)

Веб-сторінка для введення облікових даних.

**Функціональність:**
- Форма вводу username та password
- Опція "Remember me" для збереження username
- Обробка помилок входу
- Перенаправлення на адмін панель після успішного входу
- Збереження токена в `localStorage`

### 4. Updated Admin Handler

Клас `AdminRequestHandler` оновлено для підтримки обох методів аутентифікації.

**Методи аутентифікації:**
- `_is_authorized()` - перевіряє обидва методи
- `_verify_basic_auth()` - валідація Basic Auth
- `_verify_jwt_token()` - валідація JWT токена

**Нові endpoints:**
- `POST /admin/login` - отримання токена
- `GET/POST /admin/w2ui` - з підтримкою JWT токена

## Використання

### Клієнтська сторона (Браузер)

#### 1. Login (отримання токена)
```javascript
fetch('/admin/login', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
    },
    body: JSON.stringify({
        username: 'admin',
        password: 'password123'
    })
})
.then(response => response.json())
.then(data => {
    if (data.status === 'success') {
        localStorage.setItem('powergslb_token', data.token);
        console.log('Login successful:', data.user);
    } else {
        console.error('Login failed:', data.message);
    }
});
```

#### 2. Виконання запитів з токеном
```javascript
// Вручну
fetch('/admin/w2ui', {
    method: 'POST',
    headers: {
        'Authorization': 'Bearer ' + localStorage.getItem('powergslb_token'),
        'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: 'cmd=get-records&data=users'
})

// З jQuery
$(document).ajaxSend(function(event, jqXHR) {
    const token = localStorage.getItem('powergslb_token');
    if (token) {
        jqXHR.setRequestHeader('Authorization', 'Bearer ' + token);
    }
});
```

#### 3. Logout
```javascript
localStorage.removeItem('powergslb_token');
window.location.href = '/admin/login.html';
```

### Серверна сторона (Curl/API Client)

#### Login
```bash
curl -X POST http://localhost:8000/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "password123"}'
```

#### Запит з JWT токеном
```bash
TOKEN="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
curl -X POST http://localhost:8000/admin/w2ui \
  -H "Authorization: Bearer $TOKEN" \
  -d "cmd=get-records&data=users"
```

#### Запит з Basic Auth (все ще працює)
```bash
curl -X POST http://localhost:8000/admin/w2ui \
  -u "admin:password123" \
  -d "cmd=get-records&data=users"
```

## Конфігурація

### Середовищні змінні

Встановіть наступні змінні середовища для розгортання:

```bash
# Секретний ключ для підписання JWT токенів
export JWT_SECRET_KEY="your-very-secure-secret-key-here"

# Час видачі токена (годин)
export JWT_EXPIRATION_HOURS=24
```

### Запуск PowerGSLB

```bash
# Установка залежностей (включаючи PyJWT)
pip install -e .

# Запуск сервісу
powergslb -c /path/to/config.toml
```

## Безпека

### Рекомендації

1. **Секретний ключ**: Використовуйте довгий, стійкий до атак ключ
   ```python
   import secrets
   secret = secrets.token_urlsafe(32)  # Генерує 32-символьний ключ
   ```

2. **HTTPS**: Завжди використовуйте HTTPS в production
   - JWT токени передаються в заголовках
   - HTTPS шифрує передачу

3. **Термін дії**: Збалансуйте безпеку та зручність
   - Коротше (1-4 години) - більш безпечно
   - Довше (24 години) - більш зручно

4. **Token Storage**: Токен зберігається в `localStorage`
   - Уразливо до XSS атак
   - Розмістіть валідацію на сервері
   - Розглядайте HttpOnly cookies як альтернативу

5. **Перевірка на сервері**: Завжди перевіряйте токен на сервері
   - Дійсність підпису
   - Термін дії
   - Права доступу користувача

### Ротація ключів

Для ротації секретного ключа:

1. Видайте новий токен старим ключем
2. Поступово переходьте на новий ключ
3. Припиніть прийняття старого ключа після певного періоду

## Тестування

### Unit тести JWT токенів

```python
import pytest
from powergslb.system.jwt_token import JWTTokenManager
import time

def test_jwt_generation():
    manager = JWTTokenManager('test-secret')
    token = manager.generate_token(1, 'testuser', 'Test User')
    assert token is not None
    assert isinstance(token, str)

def test_jwt_validation():
    manager = JWTTokenManager('test-secret')
    token = manager.generate_token(1, 'testuser', 'Test User')
    payload = manager.validate_token(token)
    assert payload is not None
    assert payload['user_id'] == 1
    assert payload['username'] == 'testuser'

def test_jwt_expiration():
    manager = JWTTokenManager('test-secret', expiration_hours=0)
    token = manager.generate_token(1, 'testuser', 'Test User')
    time.sleep(1)
    payload = manager.validate_token(token)
    assert payload is None  # Token expired

def test_invalid_token():
    manager = JWTTokenManager('test-secret')
    payload = manager.validate_token('invalid.token.here')
    assert payload is None
```

## Міграція з Basic Auth

Якщо ви вже використовуєте Basic Auth:

1. Старі запити з Basic Auth **все ще працюють**
2. Постіпово переходьте на JWT токени
3. Клієнти можуть використовувати обидва методи

```javascript
// Опція 1: Basic Auth (для легасі клієнтів)
fetch('/admin/w2ui', {
    method: 'POST',
    headers: {
        'Authorization': 'Basic ' + btoa('admin:password123')
    }
});

// Опція 2: JWT (для нових клієнтів)
fetch('/admin/w2ui', {
    method: 'POST',
    headers: {
        'Authorization': 'Bearer ' + token
    }
});
```

## Розробка та налагодження

### Logging

JWT токен-менеджер логує важливі события:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Побачите:
# DEBUG: Generated JWT token for user 'admin'
# DEBUG: JWT token validated for user 'admin'
# WARNING: JWT token has expired
# ERROR: Invalid JWT token: ...
```

### Inspection токена

Декодуйте JWT токен для перевірки (без верифікації):

```bash
# Online: https://jwt.io

# Python
import jwt
token = "your.jwt.token.here"
decoded = jwt.decode(token, options={"verify_signature": False})
print(decoded)
```

## Часті помилки

| Помилка | Причина | Рішення |
|---------|---------|--------|
| "Invalid JWT token" | Токен зіпсований | Отримайте новий токен |
| "JWT token has expired" | Termín ży токена минув | Отримайте новий токен |
| "Missing required field" | Payload неповний | Перевіріть генерацію токена |
| "Invalid authorization scheme" | Схема не `Bearer` або `Basic` | Використовуйте правильну схему |

## Дальша розробка

Можливі покращення:

1. **Refresh токени** - для обновлення без переходу
2. **Revocation list** - список відозваних токенів
3. **Role-based access** - контроль доступу на основі ролей
4. **Multi-factor auth** - двофакторна аутентифікація
5. **OAuth2 integration** - інтеграція з OAuth2 провайдерами

## Посилання

- PyJWT документація: https://pyjwt.readthedocs.io/
- JWT.io: https://jwt.io/
- RFC 7519 (JWT): https://tools.ietf.org/html/rfc7519
- RFC 7617 (Basic Auth): https://tools.ietf.org/html/rfc7617
