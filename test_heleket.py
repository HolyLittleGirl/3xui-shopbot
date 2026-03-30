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
API_KEY = "6FLPBMDizZ3nHgejwm0VNR6DEHmnitKRcFOLn1Y0kPdc3VEdXcZ4j8v1AdhDiSQYrgPTCFzU0hN9iUk53a3rQBJPHAx89kirwXYK0a2QXxZB0qQJA4Ct1eEnoPvbQKyK"  # Ваш полный ключ
# =============================

async def test_heleket_payment():
    print("=" * 60)
    print("Heleket API Test")
    print("=" * 60)
    
    # Тестовые данные
    payload = {
        "merchantId": MERCHANT_ID,
        "amount": 100.0,
        "currency": "RUB",
        "description": json.dumps({"test": True, "user_id": 999999}),
        "returnUrl": "https://t.me/testbot",
    }
    
    print(f"\n1. Merchant ID: {MERCHANT_ID}")
    print(f"2. API Key: {API_KEY[:12]}...{API_KEY[-4:]}")
    print(f"3. Payload: {json.dumps(payload, indent=2)}")
    
    # Генерация подписи - тестируем разные формулы
    json_payload = json.dumps(payload, separators=(',', ':'))
    print(f"\n4. JSON payload: {json_payload[:100]}...")
    
    b64_payload = base64.b64encode(json_payload.encode('utf-8')).decode('utf-8')
    print(f"5. Base64 payload: {b64_payload[:50]}...")
    
    # Формула 1: MD5(base64(json) + API_KEY) - официальная
    sign1 = hashlib.md5((b64_payload + API_KEY).encode('utf-8')).hexdigest()
    print(f"6a. Sign (base64+key): {sign1}")
    
    # Формула 2: MD5(API_KEY + base64(json))
    sign2 = hashlib.md5((API_KEY + b64_payload).encode('utf-8')).hexdigest()
    print(f"6b. Sign (key+base64): {sign2}")
    
    # Формула 3: MD5(base64(API_KEY + json))
    sign3 = hashlib.md5(base64.b64encode((API_KEY + json_payload).encode('utf-8'))).hexdigest()
    print(f"6c. Sign (base64(key+json)): {sign3}")
    
    # Формула 4: MD5(API_KEY + json)
    sign4 = hashlib.md5((API_KEY + json_payload).encode('utf-8')).hexdigest()
    print(f"6d. Sign (key+json): {sign4}")
    
    # Используем формулу 1 для запроса
    sign = sign1
    print(f"\n7. Используем signature: {sign}")
    
    # Отправка запроса
    print("\n8. Отправка запроса к Heleket API...")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {sign}",
    }
    
    signatures_to_test = [
        ("base64+key (official)", sign1),
        ("key+base64", sign2),
        ("base64(key+json)", sign3),
        ("key+json", sign4),
    ]
    
    for name, test_sign in signatures_to_test:
        print(f"\n   Тест: {name}")
        test_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {test_sign}",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.heleket.com/v1/payment",
                json=payload,
                headers=test_headers
            ) as response:
                result = await response.json()
                if response.status == 200:
                    print(f"   ✅ УСПЕХ! {name} работает!")
                    print(f"   URL: {result.get('paymentUrl', 'N/A')}")
                    
                    # Сохраняем рабочую формулу в бота
                    print(f"\n   === ФОРМУЛА НАЙДЕНА ===")
                    print(f"   Обновите _create_heleket_payment_request в handlers.py")
                    return
                else:
                    print(f"   ❌ {response.status}: {result.get('message', 'Unknown')}")
    
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
