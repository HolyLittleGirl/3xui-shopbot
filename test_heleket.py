#!/usr/bin/env python3
"""
Heleket API Test Script
Проверяет правильность подписи и создаёт тестовый платёж
"""

import json
import base64
import hashlib
import aiohttp
import asyncio

# === ВСТАВЬТЕ ВАШИ ДАННЫЕ ===
MERCHANT_ID = "cdcfd096-2546-4a86-a209-af478a7ea4e9"
API_KEY = "FE27smkUgb8ItQa96g2nOky8oPJh3cLEXWQ4W89MdzjZhswRp1uOrh5mv8MFNy7dnZlTICGvyWeBn2irG8fIaAvQNRjuN2GcdpgFaRSM0hvCI8o4Dkd888Q9htZoULsE"  # НОВЫЙ ключ (НЕ ПУБЛИКОВАТЬ!)
# =============================

async def test_heleket_payment():
    print("=" * 60)
    print("Heleket API Test")
    print("=" * 60)
    
    # Тестовые данные - пробуем с int вместо float
    # По документации: https://api.heleket.com/
    # Требуется: merchantId, amount, currency, order_id
    import uuid
    order_id = str(uuid.uuid4())
    
    payload = {
        "merchantId": MERCHANT_ID,
        "amount": 100,  # INT!
        "currency": "RUB",
        "order_id": order_id,  # ✅ Требуется по документации!
        "returnUrl": "https://t.me/testbot",
    }
    
    print(f"\n1. Merchant ID: {MERCHANT_ID}")
    print(f"2. API Key: {API_KEY[:12]}...{API_KEY[-4:]}")
    print(f"3. Payload: {json.dumps(payload, indent=2)}")
    
    # Генерация подписи - тестируем разные варианты JSON
    print("\n4. Тестируем разные варианты JSON encoding:")
    
    # Вариант 1: Python json.dumps с separators
    json1 = json.dumps(payload, separators=(',', ':'))
    b64_1 = base64.b64encode(json1.encode('utf-8')).decode('utf-8')
    sign1 = hashlib.md5((b64_1 + API_KEY).encode('utf-8')).hexdigest()
    print(f"  a) json.dumps(separators=(',',':')): {sign1}")
    
    # Вариант 2: Python json.dumps без separators
    json2 = json.dumps(payload)
    b64_2 = base64.b64encode(json2.encode('utf-8')).decode('utf-8')
    sign2 = hashlib.md5((b64_2 + API_KEY).encode('utf-8')).hexdigest()
    print(f"  b) json.dumps() default: {sign2}")
    
    # Вариант 3: Сортированные ключи
    json3 = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    b64_3 = base64.b64encode(json3.encode('utf-8')).decode('utf-8')
    sign3 = hashlib.md5((b64_3 + API_KEY).encode('utf-8')).hexdigest()
    print(f"  c) json.dumps(sort_keys=True): {sign3}")
    
    # Вариант 4: amount как float 100.0
    payload_float = payload.copy()
    payload_float["amount"] = 100.0
    json4 = json.dumps(payload_float, separators=(',', ':'))
    b64_4 = base64.b64encode(json4.encode('utf-8')).decode('utf-8')
    sign4 = hashlib.md5((b64_4 + API_KEY).encode('utf-8')).hexdigest()
    print(f"  d) amount=100.0 (float): {sign4}")
    
    signatures_to_test = [
        ("int amount, compact JSON", sign1, json1),
        ("int amount, default JSON", sign2, json2),
        ("int amount, sorted keys", sign3, json3),
        ("float amount, compact JSON", sign4, json4),
    ]
    
    # Отправка запроса
    print("\n8. Отправка запроса к Heleket API...")
    
    # Тестируем оба эндпоинта
    endpoints_to_test = [
        "https://api.heleket.com/v1/payment",
        "https://api.heleket.com/v1/payment/services",
    ]
    
    for endpoint in endpoints_to_test:
        print(f"\n   Endpoint: {endpoint}")
        
        for name, test_sign, test_json in signatures_to_test:
            print(f"\n   Тест: {name}")
            print(f"   JSON: {test_json[:80]}...")
            print(f"   Sign: {test_sign}")
            # ПРАВИЛЬНЫЕ заголовки по документации Heleket
            test_headers = {
                "Content-Type": "application/json",
                "merchant": MERCHANT_ID,  # ✅ UUID мерчанта
                "sign": test_sign,        # ✅ Подпись
            }
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        endpoint,
                        json=json.loads(test_json),  # Отправляем тот же JSON что и подписали
                        headers=test_headers
                    ) as response:
                        result = await response.json()
                        print(f"   Response: {json.dumps(result, indent=2)}")
                        if response.status == 200:
                            print(f"   ✅ УСПЕХ! {name} работает!")
                            if result.get('paymentUrl'):
                                print(f"   URL: {result.get('paymentUrl')}")
                                # Сохраняем рабочую формулу в бота
                                print(f"\n   === ФОРМУЛА НАЙДЕНА ===")
                                print(f"   Обновите _create_heleket_payment_request в handlers.py")
                                return
                            elif result.get('url'):
                                print(f"   URL: {result.get('url')}")
                                print(f"\n   === ФОРМУЛА НАЙДЕНА ===")
                                return
                            else:
                                print(f"   (нет paymentUrl в ответе)")
                        else:
                            print(f"   ❌ {response.status}: {result.get('message', 'Unknown')}")
                except Exception as e:
                    print(f"   ❌ Ошибка: {e}")
    
    # Попробуем без description поля
    print("\n\n=== ТЕСТ 2: Payload без description ===")
    payload_simple = {
        "merchantId": MERCHANT_ID,
        "amount": 100.0,
        "currency": "RUB",
        "returnUrl": "https://t.me/testbot",
    }
    
    json_simple = json.dumps(payload_simple, separators=(',', ':'))
    b64_simple = base64.b64encode(json_simple.encode('utf-8')).decode('utf-8')
    sign_simple = hashlib.md5((b64_simple + API_KEY).encode('utf-8')).hexdigest()
    
    print(f"Payload: {json_simple}")
    print(f"Signature: {sign_simple}")
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.heleket.com/v1/payment",
            json=payload_simple,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {sign_simple}"}
        ) as response:
            result = await response.json()
            if response.status == 200:
                print(f"   ✅ УСПЕХ! Работает без description!")
                print(f"   URL: {result.get('paymentUrl', 'N/A')}")
            else:
                print(f"   ❌ {response.status}: {result.get('message', 'Unknown')}")
    
    print("\n" + "=" * 60)
    print("НИ ОДНА ФОРМУЛА НЕ РАБОТАЕТ!")
    print("Возможно API ключ неверный или не активирован")
    print("=" * 60)
    
    # Проверка через другой endpoint
    print("\n\n=== ТЕСТ 3: Проверка через api.heleket.ru ===")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.heleket.ru/v1/payment",
            json=payload_simple,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {sign_simple}"}
        ) as response:
            result = await response.json()
            print(f"Status: {response.status}")
            print(f"Response: {result}")
    
    print("\n\n=== ТЕСТ 4: Проверка через heleket.com (без api) ===")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://heleket.com/api/v1/payment",
            json=payload_simple,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {sign_simple}"}
        ) as response:
            result = await response.json()
            print(f"Status: {response.status}")
            print(f"Response: {result}")

if __name__ == "__main__":
    asyncio.run(test_heleket_payment())
