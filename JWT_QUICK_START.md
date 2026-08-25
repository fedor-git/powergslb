# PowerGSLB JWT Authentication - Quick Start Guide

Цей посібник допоможе вам швидко почати використовувати JWT аутентифікацію в PowerGSLB.

## 📋 Передумови

- Python 3.12+
- Встановлена бібліотека PyJWT (включена в залежності)
- Запущений PowerGSLB сервіс

## 🚀 Швидкий старт

### 1. Встановлення

```bash
# Інсталюйте PowerGSLB з новими залежностями
pip install -e .

# Або оновіть наявну установку
pip install --upgrade pyjwt
```

### 2. Конфігурація

Встановіть змінну середовища для секретного ключа:

```bash
# На Linux/macOS
export JWT_SECRET_KEY="your-super-secret-key-here"

# На Windows
set JWT_SECRET_KEY=your-super-secret-key-here
```

> **Совіт**: Генеруйте надійний ключ за допомогою:
> ```python
> import secrets
> print(secrets.token_urlsafe(32))
> ```

### 3. Запуск PowerGSLB

```bash
powergslb -c /path/to/config.toml
```

## 🔐 Методи входу

### Метод 1: Веб-интерфейс (Браузер)

1. Відкрийте `http://localhost:8000/admin/login.html`
2. Введіть username та password
3. Натисніть "Login"
4. Ви будете перенаправлені на адміністративну панель

### Метод 2: Curl (API)

#### Отримання токена:
```bash
curl -X POST http://localhost:8000/admin/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin"
  }'
```

**Відповідь:**
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

#### Використання токена:
```bash
TOKEN="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."

curl -X POST http://localhost:8000/admin/w2ui \
  -H "Authorization: Bearer $TOKEN" \
  -d "cmd=get-records&data=users&limit=10"
```

### Метод 3: Python

```python
import requests
import json

# 1. Login
login_response = requests.post(
    'http://localhost:8000/admin/login',
    json={'username': 'admin', 'password': 'admin'}
)
token = login_response.json()['token']

# 2. Use token for authenticated requests
headers = {
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/x-www-form-urlencoded'
}

records_response = requests.post(
    'http://localhost:8000/admin/w2ui',
    data={
        'cmd': 'get-records',
        'data': 'users',
        'limit': '10',
        'offset': '0'
    },
    headers=headers
)

print(json.dumps(records_response.json(), indent=2))
```

### Метод 4: JavaScript (Браузер)

```javascript
// 1. Login
fetch('/admin/login', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
    },
    body: JSON.stringify({
        username: 'admin',
        password: 'admin'
    })
})
.then(response => response.json())
.then(data => {
    const token = data.token;
    localStorage.setItem('powergslb_token', token);
    
    // 2. Use token
    return fetch('/admin/w2ui', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: 'cmd=get-records&data=users&limit=10'
    });
})
.then(response => response.json())
.then(data => console.log('Records:', data.records));
```

## 📚 Типові операції

### Отримання списку користувачів

```bash
TOKEN="your-jwt-token"

curl -X POST http://localhost:8000/admin/w2ui \
  -H "Authorization: Bearer $TOKEN" \
  -d "cmd=get-records&data=users&limit=20&offset=0"
```

### Отримання одного запису

```bash
curl -X POST http://localhost:8000/admin/w2ui \
  -H "Authorization: Bearer $TOKEN" \
  -d "cmd=get-record&data=users&recid=1"
```

### Збереження запису

```bash
curl -X POST http://localhost:8000/admin/w2ui \
  -H "Authorization: Bearer $TOKEN" \
  -d "cmd=save-record&data=users&recid=0&record={\"name\":\"John\",\"email\":\"john@example.com\"}"
```

### Видалення запису

```bash
curl -X POST http://localhost:8000/admin/w2ui \
  -H "Authorization: Bearer $TOKEN" \
  -d "cmd=delete-records&data=users&selected=1"
```

## 🔄 Refresh токена

Оскільки токен проходить (за замовчуванням через 24 години), вам потрібно отримати новий:

```javascript
// Перевірити закінчення терміну токена
function isTokenExpired(token) {
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        return Date.now() >= payload.exp * 1000;
    } catch (e) {
        return true;
    }
}

// Отримати новий токен якщо закінчився
async function ensureValidToken() {
    let token = localStorage.getItem('powergslb_token');
    
    if (isTokenExpired(token)) {
        const response = await fetch('/admin/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                username: localStorage.getItem('powergslb_username'),
                password: prompt('Password:')
            })
        });
        
        const data = await response.json();
        token = data.token;
        localStorage.setItem('powergslb_token', token);
    }
    
    return token;
}
```

## 🛠️ Troubleshooting

### Помилка: "JWT token manager not configured"

**Причина**: Змінна `JWT_SECRET_KEY` не встановлена

**Рішення**:
```bash
export JWT_SECRET_KEY="your-secret-key"
powergslb -c config.toml
```

### Помилка: "Invalid username or password"

**Причина**: Неправильні облікові дані

**Рішення**: Перевірьте username та password в базі даних

### Помилка: "Invalid JWT token"

**Причина**: 
- Токен зіпсований
- Токен видатися з іншим секретним ключем
- Токен закінчився

**Рішення**: Отримайте новий токен через `/admin/login`

### Помилка: "Token not in request headers"

**Причина**: Не додали токен до запиту

**Рішення**: Додайте заголовок `Authorization: Bearer <token>`

## 📖 Додаткові ресурси

- [Повна документація JWT](JWT_AUTHENTICATION.md)
- [Приклад Python клієнта](examples/jwt_auth_client.py)
- [Тести JWT токена](tests/test_jwt_token.py)

## ✅ Перевіркова сумка

Перед переходом в production:

- [ ] Встановлений сильний `JWT_SECRET_KEY`
- [ ] Включений HTTPS (для production)
- [ ] Проведено тестування з браузера
- [ ] Проведено тестування з API клієнта
- [ ] Перевірено логування входів
- [ ] Налаштовано моніторування

## 🎯 Наступні кроки

1. **Читайте детальну документацію**: [JWT_AUTHENTICATION.md](JWT_AUTHENTICATION.md)
2. **Запустіть приклад Python клієнта**: `python examples/jwt_auth_client.py`
3. **Запустіть тести**: `pytest tests/test_jwt_token.py -v`
4. **Налаштуйте для вашого середовища**
5. **Розгорніть в production**

---

**Питання?** Див. розділ Troubleshooting або зверніться в документацію.
