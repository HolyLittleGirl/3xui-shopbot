# 🔧 Heleket API - Инструкция по проверке

## 📋 Проблема

Heleket API возвращает ошибку `401 Invalid Sign.` при любой попытке создать платёж.

---

## ✅ Как проверить API вручную

### Способ 1: Через тестовый скрипт (рекомендуется)

**Файл:** `/root/3xui-shopbot/test_heleket.py`

**Запуск:**
```bash
cd /root/3xui-shopbot
sudo docker cp test_heleket.py 3xui-shopbot:/app/project/test_heleket.py
sudo docker exec 3xui-shopbot python3 test_heleket.py
```

**Что делает скрипт:**
1. Генерирует подпись по 4 разным формулам
2. Тестирует каждую формулу
3. Показывает какой вариант работает (если работает)

---

### Способ 2: Через curl (быстрая проверка)

**1. Подготовьте данные:**
```bash
# Ваши данные
MERCHANT_ID="cdcfd096-2546-4a86-a209-af478a7ea4e9"
API_KEY="6FLPBMDizZ3nHgejwm0VNR6DEHmnitKRcFOLn1Y0kPdc3VEdXcZ4j8v1AdhDiSQYrgPTCFzU0hN9iUk53a3rQBJPHAx89kirwXYK0a2QXxZB0qQJA4Ct1eEnoPvbQKyK"

# Payload (JSON)
PAYLOAD='{"merchantId":"'$MERCHANT_ID'","amount":100,"currency":"RUB","returnUrl":"https://t.me/testbot"}'

# Base64 encode
B64_PAYLOAD=$(echo -n "$PAYLOAD" | base64 -w 0)

# Signature: MD5(base64 + API_KEY)
SIGNATURE=$(echo -n "${B64_PAYLOAD}${API_KEY}" | md5sum | cut -d' ' -f1)

echo "Payload: $PAYLOAD"
echo "Base64: $B64_PAYLOAD"
echo "Signature: $SIGNATURE"
```

**2. Отправьте запрос:**
```bash
curl -X POST https://api.heleket.com/v1/payment \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SIGNATURE" \
  -d "$PAYLOAD"
```

**Ожидаемый результат:**
- ✅ Успех: `{"paymentUrl":"https://..."}`
- ❌ Ошибка: `{"message":"Invalid Sign."}`

---

### Способ 3: Через Python консоль

**Запуск:**
```bash
sudo docker exec -it 3xui-shopbot python3
```

**Код:**
```python
import json, base64, hashlib, requests

MERCHANT_ID = "cdcfd096-2546-4a86-a209-af478a7ea4e9"
API_KEY = "6FLPBMDizZ3nHgejwm0VNR6DEHmnitKRcFOLn1Y0kPdc3VEdXcZ4j8v1AdhDiSQYrgPTCFzU0hN9iUk53a3rQBJPHAx89kirwXYK0a2QXxZB0qQJA4Ct1eEnoPvbQKyK"

payload = {
    "merchantId": MERCHANT_ID,
    "amount": 100.0,
    "currency": "RUB",
    "returnUrl": "https://t.me/testbot"
}

# Формула подписи
json_payload = json.dumps(payload, separators=(',', ':'))
b64_payload = base64.b64encode(json_payload.encode()).decode()
sign = hashlib.md5((b64_payload + API_KEY).encode()).hexdigest()

print(f"Signature: {sign}")

# Запрос
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {sign}"
}

response = requests.post("https://api.heleket.com/v1/payment", json=payload, headers=headers)
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
```

---

## 🔍 Что проверить в панели Heleket

Откройте https://merchant.heleket.com и проверьте:

### 1. Статус мерчанта
- ✅ **Active** или **Verified**
- ❌ **Pending** или **Inactive** → требуется верификация

### 2. API ключ
- ✅ Сгенерирован и отображается полностью
- ✅ Копирован без пробелов и символов
- ✅ **Платежный** API ключ (не выплатной!)

### 3. Настройки API
- ✅ **API доступ включен**
- ✅ **Webhook URL** (опционально): `https://store.ottomator.ru/heleket-webhook`
- ✅ **IP whitelist** (если есть): добавьте IP сервера

### 4. Документы
- ✅ Все документы загружены
- ✅ Верификация пройдена

---

## 📝 Шаблон обращения в поддержку

**Telegram:** @heleket_support  
**Email:** support@heleket.com

```
Здравствуйте!

Не могу настроить API для приёма платежей.

Мерчант: cdcfd096-2546-4a86-a209-af478a7ea4e9
API ключ: 6FLPBMDizZ3n...QKyK

Получаю ошибку 401 "Invalid Sign." при запросе:
POST https://api.heleket.com/v1/payment

Payload:
{
  "merchantId": "cdcfd096-2546-4a86-a209-af478a7ea4e9",
  "amount": 100,
  "currency": "RUB",
  "returnUrl": "https://t.me/testbot"
}

Signature (MD5 base64(json)+key):
57e1ca88a4e8f429913667fae59bdbba

Проверьте пожалуйста:
1. Активен ли API доступ для моего мерчанта?
2. Правильно ли работает API ключ?
3. Требуется ли дополнительная верификация?
4. Есть ли IP whitelist или другие ограничения?

Пробовал все формулы подписи из документации - все возвращают 401.

Спасибо!
```

---

## 🚀 Когда Heleket заработает

**Включите кнопку в боте:**

1. Откройте `/root/3xui-shopbot/src/shop_bot/bot/keyboards.py`
2. Найдите закомментированный блок Heleket
3. Раскомментируйте строки
4. Перезапустите бота: `sudo docker-compose restart`

**Или попросите меня - я включу!**
