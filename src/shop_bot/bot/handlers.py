import logging
import os
import uuid
import qrcode
import aiohttp
import re
import aiohttp
import hashlib
import json
import base64
import asyncio

from urllib.parse import urlencode
from hmac import compare_digest
from functools import wraps
from yookassa import Payment
from io import BytesIO
from datetime import datetime, timedelta
from aiosend import CryptoPay, TESTNET
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict

from pytonconnect import TonConnect
from pytonconnect.exceptions import UserRejectsError

from aiogram import Bot, Router, F, types, html
from aiogram.types import BufferedInputFile, LabeledPrice, PreCheckoutQuery, InlineKeyboardButton
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Helper для безопасного callback.answer()
async def safe_callback_answer(callback: types.CallbackQuery, text: str | None = None, show_alert: bool = False):
    try:
        await callback.answer(text=text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e) or "query ID is invalid" in str(e):
            pass  # Игнорируем истёкшие query
        else:
            raise
    except Exception:
        pass  # Игнорируем остальные ошибки
from aiogram.enums import ChatMemberStatus
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

from shop_bot.bot import keyboards
from shop_bot.modules import xui_api
from shop_bot.data_manager.database import (
    get_user, add_new_key, get_user_keys, update_user_stats,
    register_user_if_not_exists, get_next_key_number, get_key_by_id,
    update_key_info, set_trial_used, set_terms_agreed, get_setting, get_all_hosts,
    get_plans_for_host, get_plan_by_id, log_transaction, get_referral_count,
    create_pending_transaction, get_all_users,
    create_support_ticket, add_support_message, get_user_tickets,
    get_ticket, get_ticket_messages, set_ticket_status, update_ticket_thread_info,
    get_ticket_by_thread,
    update_key_host_and_info,
    get_balance, deduct_from_balance,
    get_key_by_email, add_to_balance,
    add_to_referral_balance_all, get_referral_balance_all,
    get_referral_balance,
    is_admin,
    set_referral_start_bonus_received,
    get_host,
    set_legal_accepted, is_legal_accepted, set_privacy_agreed,
)

from shop_bot.config import (
    get_profile_text, get_vpn_active_text, VPN_INACTIVE_TEXT, VPN_NO_DATA_TEXT,
    get_key_info_text, CHOOSE_PAYMENT_METHOD_MESSAGE, get_purchase_success_text
)

TELEGRAM_BOT_USERNAME = None
PAYMENT_METHODS = None
ADMIN_ID = None  # устаревшее: используйте is_admin()
CRYPTO_BOT_TOKEN = get_setting("cryptobot_token")

logger = logging.getLogger(__name__)

class KeyPurchase(StatesGroup):
    waiting_for_host_selection = State()
    waiting_for_plan_selection = State()

class Onboarding(StatesGroup):
    waiting_for_subscription_and_agreement = State()

class PaymentProcess(StatesGroup):
    waiting_for_email = State()
    waiting_for_payment_method = State()

 
class TopUpProcess(StatesGroup):
    waiting_for_amount = State()
    waiting_for_topup_method = State()


class SupportDialog(StatesGroup):
    waiting_for_subject = State()
    waiting_for_message = State()
    waiting_for_reply = State()

def is_valid_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(pattern, email) is not None

async def _create_heleket_payment_request(
    user_id: int,
    price: float,
    months: int,
    host_name: str,
    state_data: dict
) -> str | None:
    """Create a Heleket payment request and return the payment URL.
    
    Heleket API: https://api.heleket.com/
    Signature: MD5(base64(json_payload) + API_KEY)
    """
    try:
        import uuid
        
        merchant_id = get_setting("heleket_merchant_id")
        api_key = get_setting("heleket_api_key")

        if not merchant_id or not api_key:
            logger.error("Heleket payment failed: missing merchant_id or api_key")
            return None

        # Generate unique order_id
        order_id = str(uuid.uuid4())
        
        # Prepare metadata for description
        metadata = {
            "user_id": user_id,
            "price": price,
            "months": months,
            "host_name": host_name,
            "action": state_data.get("action", "purchase"),
            "customer_email": state_data.get("customer_email"),
            "plan_id": state_data.get("plan_id"),
            "key_id": state_data.get("key_id"),
        }

        # Heleket API endpoint
        api_url = "https://api.heleket.com/v1/payment"

        # IMPORTANT: amount must be STRING with 2 decimal places!
        # JSON must be with default separators (spaces after colons/commas)
        payload = {
            "merchantId": merchant_id,
            "amount": f"{price:.2f}",  # STRING: "100.00"
            "currency": "RUB",
            "order_id": order_id,  # Required!
            "description": json.dumps(metadata),  # Metadata as JSON string
            "returnUrl": f"https://t.me/{TELEGRAM_BOT_USERNAME}",
        }

        # Generate signature per Heleket documentation:
        # MD5(base64(json_payload) + API_KEY)
        # IMPORTANT: Use json.dumps() WITHOUT separators (default with spaces)
        json_payload = json.dumps(payload)
        b64_payload = base64.b64encode(json_payload.encode('utf-8')).decode('utf-8')
        sign_string = b64_payload + api_key
        sign = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
        
        logger.info(f"Heleket: Created payload with order_id={order_id}, amount={price:.2f}")
        logger.debug(f"Heleket: JSON payload: {json_payload}")
        logger.debug(f"Heleket: Base64: {b64_payload[:50]}...")
        logger.debug(f"Heleket: Signature: {sign}")

        # CORRECT headers per Heleket documentation
        headers = {
            "Content-Type": "application/json",
            "merchant": merchant_id,  # UUID мерчанта
            "sign": sign,             # Подпись
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    # URL can be at top level or inside result object
                    payment_url = (
                        result.get("paymentUrl") or 
                        result.get("url") or
                        (result.get("result") or {}).get("url") or
                        (result.get("result") or {}).get("paymentUrl")
                    )
                    if payment_url:
                        logger.info(f"Heleket payment created for user {user_id}, amount: {price} RUB, order: {order_id}")
                        return payment_url
                    else:
                        logger.error(f"Heleket API returned no paymentUrl: {result}")
                        return None
                else:
                    error_text = await response.text()
                    logger.error(f"Heleket API error: {response.status} - {error_text}")
                    return None
    except Exception as e:
        logger.error(f"Failed to create Heleket payment request: {e}", exc_info=True)
        return None

async def _create_cryptobot_invoice(
    user_id: int,
    amount_rub: float,
    description: str,
    state_data: dict
) -> str | None:
    """Create a CryptoBot invoice using CryptoBot API directly."""
    try:
        cryptobot_token = get_setting("cryptobot_token")
        if not cryptobot_token:
            logger.error("CryptoBot payment failed: token is not set")
            return None

        # CryptoBot API endpoint (Mainnet)
        api_url = "https://pay.crypt.bot/api/createInvoice"
        
        # Prepare metadata/payload
        payload = {
            "amount": amount_rub,
            "currency_type": "fiat",
            "fiat": "RUB",
            "description": description,
            "payload": json.dumps(state_data),
            "paid_btn_name": "openBot",
            "paid_btn_url": f"https://t.me/{TELEGRAM_BOT_USERNAME}",
        }
        
        headers = {
            "Content-Type": "application/json",
            "Crypto-Pay-API-Token": cryptobot_token,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("ok"):
                        invoice_url = result.get("result", {}).get("bot_invoice_url")
                        if invoice_url:
                            logger.info(f"CryptoBot invoice created for user {user_id}, amount: {amount_rub} RUB")
                            return invoice_url
                        logger.error(f"CryptoBot API returned no invoice_url: {result}")
                    else:
                        logger.error(f"CryptoBot API error: {result}")
                else:
                    error_text = await response.text()
                    logger.error(f"CryptoBot API error ({response.status}): {error_text}")
                return None
    except Exception as e:
        logger.error(f"Failed to create CryptoBot invoice: {e}", exc_info=True)
        return None

async def show_main_menu(message: types.Message, edit_message: bool = False):
    user_id = message.chat.id
    user_db_data = get_user(user_id)
    user_keys = get_user_keys(user_id)
    
    trial_available = not (user_db_data and user_db_data.get('trial_used'))
    is_admin_flag = is_admin(user_id)

    text = (
        "🏠 <b>Главное меню</b>\n\n"
        "⚠️ Важно: в связи с блокировками Telegram бот может отвечать медленнее обычного.\n"
        "Прошу понять и простить 🙏\n\n"
        "Выберите действие:"
    )
    keyboard = keyboards.create_main_menu_keyboard(user_keys, trial_available, is_admin_flag)
    # Отправляем только текст без фотографии
    if edit_message:
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
    else:
        await message.answer(text, reply_markup=keyboard)

async def process_successful_onboarding(callback: types.CallbackQuery, state: FSMContext):
    """Завершает онбординг: ставит флаг согласия и открывает главное меню."""
    user_id = callback.from_user.id
    try:
        set_terms_agreed(user_id)
    except Exception as e:
        logger.error(f"Failed to set_terms_agreed for user {user_id}: {e}")
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        await show_main_menu(callback.message, edit_message=True)
    except Exception:
        try:
            await callback.message.answer("✅ Требования выполнены. Открываю меню...")
        except Exception:
            pass
    try:
        await state.clear()
    except Exception:
        pass

def registration_required(f):
    @wraps(f)
    async def decorated_function(event: types.Update, *args, **kwargs):
        user_id = event.from_user.id
        user_data = get_user(user_id)
        if user_data:
            return await f(event, *args, **kwargs)
        else:
            message_text = "Пожалуйста, для начала работы со мной, отправьте команду /start"
            if isinstance(event, types.CallbackQuery):
                await event.answer(message_text, show_alert=True)
            else:
                await event.answer(message_text)
    return decorated_function

def get_user_router() -> Router:
    user_router = Router()

    @user_router.message(CommandStart())
    async def start_handler(message: types.Message, state: FSMContext, bot: Bot, command: CommandObject):
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name
        referrer_id = None

        if command.args and command.args.startswith('ref_'):
            try:
                potential_referrer_id = int(command.args.split('_')[1])
                if potential_referrer_id != user_id:
                    referrer_id = potential_referrer_id
                    logger.info(f"New user {user_id} was referred by {referrer_id}")
            except (IndexError, ValueError):
                logger.warning(f"Invalid referral code received: {command.args}")
                
        register_user_if_not_exists(user_id, username, referrer_id)
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name
        user_data = get_user(user_id)

        # Бонус при старте для пригласившего (fixed_start_referrer): единоразово, когда новый пользователь запускает бота по реферальной ссылке
        try:
            reward_type = (get_setting("referral_reward_type") or "percent_purchase").strip()
        except Exception:
            reward_type = "percent_purchase"
        if reward_type == "fixed_start_referrer" and referrer_id and user_data and not user_data.get('referral_start_bonus_received'):
            try:
                amount_raw = get_setting("referral_on_start_referrer_amount") or "20"
                start_bonus = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
            except Exception:
                start_bonus = Decimal("20.00")
            if start_bonus > 0:
                try:
                    ok = add_to_balance(int(referrer_id), float(start_bonus))
                except Exception as e:
                    logger.warning(f"Referral start bonus: add_to_balance failed for referrer {referrer_id}: {e}")
                    ok = False
                # Увеличиваем суммарный заработок по рефералке
                try:
                    add_to_referral_balance_all(int(referrer_id), float(start_bonus))
                except Exception as e:
                    logger.warning(f"Referral start bonus: failed to increment referral_balance_all for {referrer_id}: {e}")
                # Помечаем, что для этого нового пользователя старт уже обработан, чтобы не дублировать при повторном /start
                try:
                    set_referral_start_bonus_received(user_id)
                except Exception:
                    pass
                # Уведомим пригласившего
                try:
                    await bot.send_message(
                        chat_id=int(referrer_id),
                        text=(
                            "🎁 Начисление за приглашение!\n"
                            f"Новый пользователь: {message.from_user.full_name} (ID: {user_id})\n"
                            f"Бонус: {float(start_bonus):.2f} RUB"
                        )
                    )
                except Exception:
                    pass

        if user_data and user_data.get('agreed_to_terms'):
            await message.answer(
                f"👋 Снова здравствуйте, {html.bold(message.from_user.full_name)}!",
                reply_markup=keyboards.main_reply_keyboard
            )
            await show_main_menu(message)
            return

        terms_url = get_setting("terms_url")
        privacy_url = get_setting("privacy_url")
        channel_url = get_setting("channel_url")

        if not channel_url and (not terms_url or not privacy_url):
            set_terms_agreed(user_id)
            await show_main_menu(message)
            return

        is_subscription_forced = get_setting("force_subscription") == "true"
        
        show_welcome_screen = (is_subscription_forced and channel_url) or (terms_url and privacy_url)

        if not show_welcome_screen:
            set_terms_agreed(user_id)
            await show_main_menu(message)
            return

        welcome_parts = ["🔐 <b>Добро пожаловать!</b>\n\n"
            "Мы предоставляем VPN для безопасного и стабильного доступа к зарубежным сервисам 🌐\n\n"
            "Продолжая, вы принимаете наши условия использования."]

        final_text = "".join(welcome_parts)
        
        await message.answer(
            final_text,
            reply_markup=keyboards.create_welcome_keyboard(
                channel_url=channel_url,
                is_subscription_forced=is_subscription_forced
            ),
            disable_web_page_preview=True
        )
        await state.set_state(Onboarding.waiting_for_subscription_and_agreement)

    @user_router.callback_query(Onboarding.waiting_for_subscription_and_agreement, F.data == "check_subscription_and_agree")
    async def check_subscription_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        user_id = callback.from_user.id
        channel_url = get_setting("channel_url")
        is_subscription_forced = get_setting("force_subscription") == "true"

        # Автоматически принимаем документы при нажатии кнопки
        set_legal_accepted(user_id)

        if not is_subscription_forced or not channel_url:
            await process_successful_onboarding(callback, state)
            return

        try:
            if '@' not in channel_url and 't.me/' not in channel_url:
                logger.error(f"Неверный формат URL канала: {channel_url}. Пропускаем проверку подписки.")
                await process_successful_onboarding(callback, state)
                return

            channel_id = '@' + channel_url.split('/')[-1] if 't.me/' in channel_url else channel_url
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)

            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                await process_successful_onboarding(callback, state)
            else:
                await callback.answer("Вы еще не подписались на канал. Пожалуйста, подпишитесь и попробуйте снова.", show_alert=True)

        except Exception as e:
            logger.error(f"Ошибка при проверке подписки для user_id {user_id} на канал {channel_url}: {e}")
            await callback.answer("Не удалось проверить подписку. Убедитесь, что бот является администратором канала. Попробуйте позже.", show_alert=True)

    @user_router.message(Onboarding.waiting_for_subscription_and_agreement)
    async def onboarding_fallback_handler(message: types.Message):
        await message.answer("Пожалуйста, выполните требуемые действия и нажмите на кнопку в сообщении выше.")

    @user_router.message(F.text == "🏠 Главное меню")
    @registration_required
    async def main_menu_handler(message: types.Message):
        await show_main_menu(message)

    @user_router.callback_query(F.data == "back_to_main_menu")
    @registration_required
    async def back_to_main_menu_handler(callback: types.CallbackQuery):
        await callback.answer()
        await show_main_menu(callback.message, edit_message=True)

    @user_router.callback_query(F.data == "show_main_menu")
    @registration_required
    async def show_main_menu_cb(callback: types.CallbackQuery):
        await callback.answer()
        await show_main_menu(callback.message, edit_message=True)

    @user_router.callback_query(F.data == "show_profile")
    @registration_required
    async def profile_handler_callback(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        user_db_data = get_user(user_id)
        user_keys = get_user_keys(user_id)
        if not user_db_data:
            await callback.answer("Не удалось получить данные профиля.", show_alert=True)
            return
        username = html.bold(user_db_data.get('username', 'Пользователь'))
        total_spent, total_months = user_db_data.get('total_spent', 0), user_db_data.get('total_months', 0)
        now = datetime.now()
        active_keys = [key for key in user_keys if datetime.fromisoformat(key['expiry_date']) > now]
        if active_keys:
            latest_key = max(active_keys, key=lambda k: datetime.fromisoformat(k['expiry_date']))
            latest_expiry_date = datetime.fromisoformat(latest_key['expiry_date'])
            time_left = latest_expiry_date - now
            vpn_status_text = get_vpn_active_text(time_left.days, time_left.seconds // 3600)
        elif user_keys: vpn_status_text = VPN_INACTIVE_TEXT
        else: vpn_status_text = VPN_NO_DATA_TEXT
        final_text = get_profile_text(username, total_spent, total_months, vpn_status_text)
        # Баланс: основной + реферальные метрики
        try:
            main_balance = get_balance(user_id)
        except Exception:
            main_balance = 0.0
        final_text += f"\n\n💼 <b>Основной баланс:</b> {main_balance:.0f} RUB"
        # Реферальная информация
        try:
            referral_count = get_referral_count(user_id)
        except Exception:
            referral_count = 0
        try:
            total_ref_earned = float(get_referral_balance_all(user_id))
        except Exception:
            total_ref_earned = 0.0
        final_text += (
            f"\n🤝 <b>Рефералы:</b> {referral_count}"
            f"\n💰 <b>Заработано по рефералке (всего):</b> {total_ref_earned:.2f} RUB"
        )
        await callback.message.edit_text(final_text, reply_markup=keyboards.create_profile_keyboard())

    @user_router.callback_query(F.data == "top_up_start")
    @registration_required
    async def topup_start_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "Введите сумму пополнения в рублях (например, 300):\nМинимум: 10 RUB, максимум: 100000 RUB",
            reply_markup=keyboards.main_reply_keyboard
        )
        await state.set_state(TopUpProcess.waiting_for_amount)

    @user_router.message(TopUpProcess.waiting_for_amount)
    async def topup_amount_input(message: types.Message, state: FSMContext):
        text = (message.text or "").replace(",", ".").strip()
        try:
            amount = Decimal(text)
        except Exception:
            await message.answer(
                "❌ Введите корректную сумму, например: 300",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        if amount <= 0:
            await message.answer(
                "❌ Сумма должна быть положительной",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        if amount < Decimal("10"):
            await message.answer(
                "❌ Минимальная сумма пополнения: 10 RUB",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        if amount > Decimal("100000"):
            await message.answer(
                "❌ Максимальная сумма пополнения: 100000 RUB",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        final_amount = amount.quantize(Decimal("0.01"))
        await state.update_data(topup_amount=float(final_amount))
        await message.answer(
            f"К пополнению: {final_amount:.2f} RUB\nВыберите способ оплаты:",
            reply_markup=keyboards.create_topup_payment_method_keyboard()
        )
        await state.set_state(TopUpProcess.waiting_for_topup_method)

    @user_router.callback_query(TopUpProcess.waiting_for_topup_method, F.data == "topup_pay_yookassa")
    async def topup_pay_yookassa(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю ссылку на оплату...")
        data = await state.get_data()
        amount = Decimal(str(data.get('topup_amount', 0)))
        if amount <= 0:
            await state.clear()
            await callback.message.answer(
                "❌ Некорректная сумма пополнения. Повторите ввод.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        user_id = callback.from_user.id
        price_str_for_api = f"{amount:.2f}"
        price_float_for_metadata = float(amount)

        try:
            # Сформируем чек, если указан email для чеков
            customer_email = get_setting("receipt_email")
            receipt = None
            if customer_email and is_valid_email(customer_email):
                receipt = {
                    "customer": {"email": customer_email},
                    "items": [{
                        "description": f"Пополнение баланса",
                        "quantity": "1.00",
                        "amount": {"value": price_str_for_api, "currency": "RUB"},
                        "vat_code": "1"
                    }]
                }

            payment_payload = {
                "amount": {"value": price_str_for_api, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": f"https://t.me/{TELEGRAM_BOT_USERNAME}"},
                "capture": True,
                "description": f"Пополнение баланса на {price_str_for_api} RUB",
                "metadata": {
                    "user_id": user_id,
                    "price": price_float_for_metadata,
                    "action": "top_up",
                    "payment_method": "YooKassa"
                }
            }
            if receipt:
                payment_payload['receipt'] = receipt
            payment = Payment.create(payment_payload, uuid.uuid4())
            await state.clear()
            await callback.message.edit_text(
                "Нажмите на кнопку ниже для оплаты:",
                reply_markup=keyboards.create_payment_keyboard(payment.confirmation.confirmation_url)
            )
        except Exception as e:
            logger.error(f"Failed to create YooKassa topup payment: {e}", exc_info=True)
            await callback.message.answer("Не удалось создать ссылку на оплату.")
            await state.clear()

    @user_router.callback_query(TopUpProcess.waiting_for_topup_method, F.data == "topup_pay_yoomoney")
    async def topup_pay_yoomoney(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю ссылку на оплату...")
        data = await state.get_data()
        amount = Decimal(str(data.get('topup_amount', 0)))
        if amount <= 0:
            await state.clear()
            await callback.message.answer(
                "❌ Некорректная сумма пополнения. Повторите ввод.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        
        user_id = callback.from_user.id
        yoomoney_wallet = get_setting("yoomoney_wallet_id")
        yoomoney_api_key = get_setting("yoomoney_api_key")
        
        if not yoomoney_wallet or not yoomoney_api_key:
            await state.clear()
            await callback.message.answer(
                "❌ YooMoney временно недоступен.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return
        
        # Формируем ссылку на оплату YooMoney
        # Формат: https://yoomoney.ru/quickpay/confirm.xml?receiver=XXX&formcomment=XXX&sum=XXX&paymentType=XX&quickpay-form=shop
        yoomoney_url = (
            f"https://yoomoney.ru/quickpay/confirm.xml?"
            f"receiver={yoomoney_wallet}"
            f"&formcomment=Пополнение баланса"
            f"&sum={amount:.2f}"
            f"&paymentType=AC"  # AC = банковская карта
            f"&quickpay-form=shop"
            f"&targets=Пополнение+баланса+для+user_{user_id}"
            f"&successURL=https://t.me/{TELEGRAM_BOT_USERNAME}"
        )
        
        await state.clear()
        await callback.message.answer(
            f"💳 Оплата через YooMoney\n\n"
            f"Сумма: <b>{amount:.2f} RUB</b>\n\n"
            f"Нажмите на кнопку ниже для оплаты:",
            reply_markup=keyboards.create_payment_keyboard(yoomoney_url)
        )

    @user_router.callback_query(TopUpProcess.waiting_for_topup_method, F.data == "topup_pay_cryptobot")
    async def topup_pay_cryptobot_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю счет в CryptoBot...")
        data = await state.get_data()
        user_id = callback.from_user.id
        amount = float(data.get('topup_amount', 0))
        if amount <= 0:
            await state.clear()
            await callback.message.answer(
                "❌ Некорректная сумма пополнения. Повторите ввод.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        cryptobot_token = get_setting('cryptobot_token')
        if not cryptobot_token:
            await state.clear()
            await callback.message.answer(
                "❌ CryptoBot временно недоступен.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        state_data = {"action": "top_up", "customer_email": None, "plan_id": None, "host_name": None, "key_id": None}
        description = f"Пополнение баланса на {amount:.2f} RUB"

        invoice_url = await _create_cryptobot_invoice(
            user_id=user_id,
            amount_rub=amount,
            description=description,
            state_data=state_data
        )

        if invoice_url:
            await callback.message.answer(
                f"💳 Счёт на сумму <b>{amount:.2f} RUB</b>\n\nОплатите по ссылке:\n{invoice_url}",
                reply_markup=keyboards.create_payment_keyboard(invoice_url)
            )
            await state.clear()
        else:
            await state.clear()
            await callback.message.answer(
                "❌ Не удалось создать счет CryptoBot.",
                reply_markup=keyboards.main_reply_keyboard
            )

    @user_router.callback_query(TopUpProcess.waiting_for_topup_method, F.data == "topup_pay_heleket")
    async def topup_pay_heleket_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю счет через Heleket...")
        data = await state.get_data()
        user_id = callback.from_user.id
        amount = float(data.get('topup_amount', 0))
        if amount <= 0:
            await state.clear()
            await callback.message.answer(
                "❌ Некорректная сумма пополнения. Повторите ввод.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        state_data = {"action": "top_up", "customer_email": None, "plan_id": None, "host_name": None, "key_id": None}
        try:
            pay_url = await _create_heleket_payment_request(
                user_id=user_id,
                price=float(amount),
                months=0,
                host_name="",
                state_data=state_data
            )
            if pay_url:
                await callback.message.answer(
                    "Нажмите на кнопку ниже для оплаты:",
                    reply_markup=keyboards.create_payment_keyboard(pay_url)
                )
                await state.clear()
            else:
                await state.clear()
                await callback.message.answer(
                    "❌ Не удалось создать счет. Попробуйте другой способ оплаты.",
                    reply_markup=keyboards.main_reply_keyboard
                )
        except Exception as e:
            logger.error(f"Failed to create Heleket topup payment: {e}", exc_info=True)
            await callback.message.answer(
                "❌ Не удалось создать счёт.",
                reply_markup=keyboards.main_reply_keyboard
            )
            await state.clear()

    @user_router.callback_query(TopUpProcess.waiting_for_topup_method, F.data == "topup_pay_tonconnect")
    async def topup_pay_tonconnect(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Готовлю TON Connect...")
        data = await state.get_data()
        user_id = callback.from_user.id
        amount_rub = Decimal(str(data.get('topup_amount', 0)))
        if amount_rub <= 0:
            await state.clear()
            await callback.message.answer(
                "❌ Некорректная сумма пополнения. Повторите ввод.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        wallet_address = get_setting("ton_wallet_address")
        if not wallet_address:
            await state.clear()
            await callback.message.answer(
                "❌ Оплата через TON временно недоступна.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        usdt_rub_rate = await get_usdt_rub_rate()
        ton_usdt_rate = await get_ton_usdt_rate()
        if not usdt_rub_rate or not ton_usdt_rate:
            await state.clear()
            await callback.message.answer(
                "❌ Не удалось получить курс TON. Попробуйте позже.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        price_ton = (amount_rub / usdt_rub_rate / ton_usdt_rate).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        amount_nanoton = int(price_ton * 1_000_000_000)

        payment_id = str(uuid.uuid4())
        metadata = {
            "user_id": user_id,
            "price": float(amount_rub),
            "action": "top_up",
            "payment_method": "TON Connect"
        }
        create_pending_transaction(payment_id, user_id, float(amount_rub), metadata)

        transaction_payload = {
            'messages': [{'address': wallet_address, 'amount': str(amount_nanoton), 'payload': payment_id}],
            'valid_until': int(datetime.now().timestamp()) + 600
        }

        try:
            connect_url = await _start_ton_connect_process(user_id, transaction_payload)
            qr_img = qrcode.make(connect_url)
            bio = BytesIO(); qr_img.save(bio, "PNG"); qr_file = BufferedInputFile(bio.getvalue(), "ton_qr.png")
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_photo(
                photo=qr_file,
                caption=(
                    f"💎 Оплата через TON Connect\n\n"
                    f"Сумма к оплате: `{price_ton}` TON\n\n"
                    f"Нажмите кнопку ниже, чтобы открыть кошелёк и подтвердить перевод."
                ),
                reply_markup=keyboards.create_ton_connect_keyboard(connect_url)
            )
            await state.clear()
        except Exception as e:
            logger.error(f"Failed to start TON Connect topup: {e}", exc_info=True)
            await callback.message.answer(
                "❌ Не удалось подготовить оплату TON Connect.",
                reply_markup=keyboards.main_reply_keyboard
            )
            await state.clear()

    @user_router.callback_query(TopUpProcess.waiting_for_topup_method, F.data == "topup_pay_stars")
    async def topup_pay_stars_handler(callback: types.CallbackQuery, state: FSMContext):
        """Создание инвойса Telegram Stars для пополнения баланса."""
        await callback.answer("Создаю счет в Telegram Stars...")
        data = await state.get_data()
        amount_rub = Decimal(str(data.get('topup_amount', 0)))
        user_id = callback.from_user.id

        if amount_rub <= 0:
            await state.clear()
            await callback.message.answer(
                "❌ Некорректная сумма пополнения. Повторите ввод.",
                reply_markup=keyboards.main_reply_keyboard
            )
            return

        # Конвертируем рубли в звёзды (1 звезда ≈ 1.25 RUB, минимальное количество — 1 звезда)
        stars_to_rub_rate = Decimal("1.25")
        stars_amount = int((amount_rub / stars_to_rub_rate).to_integral_value(rounding=ROUND_HALF_UP))
        if stars_amount < 1:
            stars_amount = 1

        # Пересчитываем сумму в рублях на основе количества звёзд (для точности)
        final_amount_rub = float(stars_amount * stars_to_rub_rate)

        try:
            bot_info = await callback.bot.get_me()
            bot_name = bot_info.first_name

            # Создаём инвойс Telegram Stars
            await callback.message.answer_invoice(
                title=bot_name,
                description=f"Пополнение баланса на {final_amount_rub:.2f} RUB",
                payload=f"stars_topup:{user_id}:{final_amount_rub}",
                provider_token="",  # Для Stars не нужен
                currency="XTR",  # Валюта Telegram Stars
                prices=[LabeledPrice(label="Пополнение баланса", amount=stars_amount)],
                reply_markup=InlineKeyboardBuilder().row(
                    InlineKeyboardButton(text=f"⭐ Оплатить {stars_amount} XTR", pay=True)
                ).row(
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_topup_amount")
                ).as_markup()
            )
        except Exception as e:
            logger.error(f"Failed to create Telegram Stars topup invoice: {e}", exc_info=True)
            await callback.message.answer(
                "❌ Не удалось создать счет Telegram Stars. Попробуйте позже.",
                reply_markup=keyboards.main_reply_keyboard
            )
            await state.clear()

    @user_router.callback_query(F.data == "back_to_topup_amount")
    async def back_to_topup_amount_handler(callback: types.CallbackQuery, state: FSMContext):
        """Возврат к вводу суммы пополнения."""
        await callback.answer()
        try:
            await callback.message.edit_text(
                "Введите сумму пополнения в рублях (например, 300):\nМинимум: 10 RUB, максимум: 100000 RUB",
            )
        except TelegramBadRequest as e:
            if "message can't be edited" in str(e):
                # Сообщение слишком старое или удалено — отправляем новое
                await callback.message.answer(
                    "Введите сумму пополнения в рублях (например, 300):\nМинимум: 10 RUB, максимум: 100000 RUB",
                )
            else:
                raise
        await state.set_state(TopUpProcess.waiting_for_amount)

    @user_router.callback_query(F.data == "show_referral_program")
    @registration_required
    async def referral_program_handler(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        user_data = get_user(user_id)
        bot_username = (await callback.bot.get_me()).username
        
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        referral_count = get_referral_count(user_id)
        try:
            total_ref_earned = float(get_referral_balance_all(user_id))
        except Exception:
            total_ref_earned = 0.0
        text = (
            "🤝 <b>Реферальная программа</b>\n\n"
            f"<b>Ваша реферальная ссылка:</b>\n<code>{referral_link}</code>\n\n"
            f"<b>Приглашено пользователей:</b> {referral_count}\n"
            f"<b>Заработано по рефералке:</b> {total_ref_earned:.2f} RUB"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад", callback_data="back_to_main_menu")
        await callback.message.edit_text(
            text, reply_markup=builder.as_markup()
        )


    @user_router.callback_query(F.data == "show_about")
    @registration_required
    async def about_handler(callback: types.CallbackQuery):
        await callback.answer()

        about_text = get_setting("about_text")
        terms_url = get_setting("terms_url")
        privacy_url = get_setting("privacy_url")
        channel_url = get_setting("channel_url")

        final_text = about_text if about_text else "Информация о проекте не добавлена."

        keyboard = keyboards.create_about_keyboard(channel_url, terms_url, privacy_url)

        await callback.message.edit_text(
            final_text,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

    # ========== LEGAL DOCUMENTS (WELCOME FLOW) ==========

    TERMS_PAGES = [
        ("🔐 Условия использования\n\n1️⃣ Назначение сервиса\nСервис предоставляет защищённое соединение для повышения безопасности и конфиденциальности в сети.",),
        ("Подходит для:\n🌐 доступа к зарубежным онлайн-сервисам и инструментам\n📶 защиты в публичных Wi-Fi\n🛡️ предотвращения перехвата трафика",),
        ("⚖️ 2️⃣ Правовая информация\nСервис работает в рамках законодательства.\nНе предназначен для противоправного использования.\nПользователь самостоятельно несёт ответственность за соблюдение закона.",),
        ("👤 3️⃣ Доступ к сервису\nИспользуя сервис, вы подтверждаете, что:\n• обладаете правоспособностью\n• или используете сервис с согласия законного представителя",),
        ("💳 4️⃣ Оплата\nОплата производится по выбранному тарифу.\nВозврат платежей в соответствии с законодательством.",),
        ("🔒 5️⃣ Конфиденциальность\nСервис не ведёт логи активности.\nEmail предоставляется пользователем для получения чеков и не хранится.",),
        ("🚫 6️⃣ Ограничения\nЗапрещено использовать сервис для:\n• незаконной деятельности\n• кибератак\n• распространения запрещённого контента\n• нарушений законодательства",),
        ("🛠 7️⃣ Поддержка\nДоступна через Telegram-бот → раздел «Помощь».\n\n📌 Используя сервис, вы принимаете данные условия.",),
    ]

    PRIVACY_PAGES = [
        ("🔐 Политика конфиденциальности\n\n1️⃣ Общие положения\nВ соответствии с 152-ФЗ мы обрабатываем только минимально необходимые данные для работы сервиса.",),
        ("📊 2️⃣ Какие данные собираем\n• Telegram ID\n• username\n\nТакже:\n• данные о подписке\n• информация о факте оплаты",),
        ("🎯 3️⃣ Зачем нужны данные\n• доступ к сервису\n• учёт подписки\n• поддержка\n• безопасность",),
        ("⚖️ 4️⃣ Основания обработки\n• согласие пользователя\n• исполнение пользовательского соглашения",),
        ("🚫 5️⃣ Что мы НЕ делаем\n• не ведём логи активности\n• не отслеживаем сайты\n• не анализируем трафик\n• не продаём данные",),
        ("🤝 6️⃣ Передача данных\nДанные не передаются третьим лицам, кроме случаев, предусмотренных законом.\n\nПлатежи обрабатываются внешними сервисами, данные вводятся пользователем напрямую.",),
        ("👤 7️⃣ Права пользователя\nВы можете:\n• запросить данные\n• изменить или удалить их\n• отозвать согласие",),
        ("⏳ 8️⃣ Хранение\nДанные хранятся только на время использования сервиса.",),
        ("📞 9️⃣ Контакты\nПоддержка через Telegram-бот.\n\n📌 Используя сервис, вы соглашаетесь с данной Политикой.",),
    ]

    def _create_terms_keyboard_welcome(current: int) -> InlineKeyboardMarkup:
        """Keyboard for terms during onboarding."""
        builder = InlineKeyboardBuilder()
        total = len(TERMS_PAGES)
        if current > 0:
            builder.button(text="⬅️ Назад", callback_data=f"terms_welcome_prev_{current}")
        if current < total - 1:
            builder.button(text="Далее ➡️", callback_data=f"terms_welcome_next_{current}")
        builder.button(text="⬅️ Назад к приветствию", callback_data="back_to_welcome")
        builder.adjust(2 if current > 0 or current < total - 1 else 1)
        return builder.as_markup()

    def _create_privacy_keyboard_welcome(current: int) -> InlineKeyboardMarkup:
        """Keyboard for privacy during onboarding."""
        builder = InlineKeyboardBuilder()
        total = len(PRIVACY_PAGES)
        if current > 0:
            builder.button(text="⬅️ Назад", callback_data=f"privacy_welcome_prev_{current}")
        if current < total - 1:
            builder.button(text="Далее ➡️", callback_data=f"privacy_welcome_next_{current}")
        builder.button(text="⬅️ Назад к приветствию", callback_data="back_to_welcome")
        builder.adjust(2 if current > 0 or current < total - 1 else 1)
        return builder.as_markup()

    @user_router.callback_query(F.data == "show_terms_welcome")
    async def show_terms_welcome_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            TERMS_PAGES[0][0],
            reply_markup=_create_terms_keyboard_welcome(0)
        )

    @user_router.callback_query(F.data.startswith("terms_welcome_next_"))
    async def terms_welcome_next_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        next_page = current + 1
        if next_page < len(TERMS_PAGES):
            await callback.message.edit_text(
                TERMS_PAGES[next_page][0],
                reply_markup=_create_terms_keyboard_welcome(next_page)
            )

    @user_router.callback_query(F.data.startswith("terms_welcome_prev_"))
    async def terms_welcome_prev_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        prev_page = current - 1
        if prev_page >= 0:
            await callback.message.edit_text(
                TERMS_PAGES[prev_page][0],
                reply_markup=_create_terms_keyboard_welcome(prev_page)
            )

    @user_router.callback_query(F.data == "show_privacy_welcome")
    async def show_privacy_welcome_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            PRIVACY_PAGES[0][0],
            reply_markup=_create_privacy_keyboard_welcome(0)
        )

    @user_router.callback_query(F.data.startswith("privacy_welcome_next_"))
    async def privacy_welcome_next_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        next_page = current + 1
        if next_page < len(PRIVACY_PAGES):
            await callback.message.edit_text(
                PRIVACY_PAGES[next_page][0],
                reply_markup=_create_privacy_keyboard_welcome(next_page)
            )

    @user_router.callback_query(F.data.startswith("privacy_welcome_prev_"))
    async def privacy_welcome_prev_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        prev_page = current - 1
        if prev_page >= 0:
            await callback.message.edit_text(
                PRIVACY_PAGES[prev_page][0],
                reply_markup=_create_privacy_keyboard_welcome(prev_page)
            )

    @user_router.callback_query(F.data == "back_to_welcome")
    async def back_to_welcome_handler(callback: types.CallbackQuery):
        """Return to welcome screen during onboarding."""
        await callback.answer()
        user_id = callback.from_user.id
        channel_url = get_setting("channel_url")
        is_subscription_forced = get_setting("force_subscription") == "true"
        
        welcome_text = "🔐 <b>Добро пожаловать!</b>\n\n" \
            "Мы предоставляем VPN для безопасного и стабильного доступа к зарубежным сервисам 🌐\n\n" \
            "Продолжая, вы принимаете наши условия использования."
        
        await callback.message.edit_text(
            welcome_text,
            reply_markup=keyboards.create_welcome_keyboard(channel_url, is_subscription_forced)
        )

    @user_router.callback_query(F.data == "show_terms")
    @registration_required
    async def show_terms_handler(callback: types.CallbackQuery):
        """Show terms from main menu (after onboarding)."""
        await callback.answer()
        await callback.message.edit_text(
            TERMS_PAGES[0][0],
            reply_markup=_create_terms_keyboard(0)
        )

    @user_router.callback_query(F.data.startswith("terms_next_"))
    @registration_required
    async def terms_next_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        next_page = current + 1
        if next_page < len(TERMS_PAGES):
            await callback.message.edit_text(
                TERMS_PAGES[next_page][0],
                reply_markup=_create_terms_keyboard(next_page)
            )

    @user_router.callback_query(F.data.startswith("terms_prev_"))
    @registration_required
    async def terms_prev_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        prev_page = current - 1
        if prev_page >= 0:
            await callback.message.edit_text(
                TERMS_PAGES[prev_page][0],
                reply_markup=_create_terms_keyboard(prev_page)
            )

    @user_router.callback_query(F.data == "show_privacy")
    @registration_required
    async def show_privacy_handler(callback: types.CallbackQuery):
        """Show privacy from main menu (after onboarding)."""
        await callback.answer()
        await callback.message.edit_text(
            PRIVACY_PAGES[0][0],
            reply_markup=_create_privacy_keyboard(0)
        )

    @user_router.callback_query(F.data.startswith("privacy_next_"))
    @registration_required
    async def privacy_next_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        next_page = current + 1
        if next_page < len(PRIVACY_PAGES):
            await callback.message.edit_text(
                PRIVACY_PAGES[next_page][0],
                reply_markup=_create_privacy_keyboard(next_page)
            )

    @user_router.callback_query(F.data.startswith("privacy_prev_"))
    @registration_required
    async def privacy_prev_handler(callback: types.CallbackQuery):
        await callback.answer()
        current = int(callback.data.split("_")[-1])
        prev_page = current - 1
        if prev_page >= 0:
            await callback.message.edit_text(
                PRIVACY_PAGES[prev_page][0],
                reply_markup=_create_privacy_keyboard(prev_page)
            )

    @user_router.callback_query(F.data == "accept_terms")
    @registration_required
    async def accept_terms_handler(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        set_legal_accepted(user_id)
        await callback.message.edit_text(
            "✅ Вы приняли Условия использования!\n\n"
            "Теперь ознакомьтесь с Политикой конфиденциальности и примите её.",
            reply_markup=keyboards.create_back_to_menu_keyboard()
        )

    @user_router.callback_query(F.data == "accept_privacy")
    @registration_required
    async def accept_privacy_handler(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        set_privacy_agreed(user_id)
        await callback.message.edit_text(
            "✅ Вы приняли Политику конфиденциальности!\n\n"
            "Теперь ознакомьтесь с Условиями использования и примите их.",
            reply_markup=keyboards.create_back_to_menu_keyboard()
        )

    def _create_terms_keyboard(current: int) -> InlineKeyboardMarkup:
        """Keyboard for terms from main menu (after onboarding)."""
        builder = InlineKeyboardBuilder()
        total = len(TERMS_PAGES)
        if current > 0:
            builder.button(text="⬅️ Назад", callback_data=f"terms_prev_{current}")
        if current < total - 1:
            builder.button(text="Далее ➡️", callback_data=f"terms_next_{current}")
        else:
            builder.button(text="✅ Принять", callback_data="accept_terms")
        builder.button(text="❌ Закрыть", callback_data="back_to_main_menu")
        builder.adjust(2 if current < total - 1 else 1)
        return builder.as_markup()

    def _create_privacy_keyboard(current: int) -> InlineKeyboardMarkup:
        """Keyboard for privacy from main menu (after onboarding)."""
        builder = InlineKeyboardBuilder()
        total = len(PRIVACY_PAGES)
        if current > 0:
            builder.button(text="⬅️ Назад", callback_data=f"privacy_prev_{current}")
        if current < total - 1:
            builder.button(text="Далее ➡️", callback_data=f"privacy_next_{current}")
        else:
            builder.button(text="✅ Принять", callback_data="accept_privacy")
        builder.button(text="❌ Закрыть", callback_data="back_to_main_menu")
        builder.adjust(2 if current < total - 1 else 1)
        return builder.as_markup()

    @user_router.callback_query(F.data == "show_help")
    @registration_required
    async def about_handler(callback: types.CallbackQuery):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        support_text = get_setting("support_text") or "Раздел поддержки. Нажмите кнопку ниже, чтобы открыть чат с поддержкой."
        if support_bot_username:
            await callback.message.edit_text(
                support_text,
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            support_user = get_setting("support_user")
            if support_user:
                await callback.message.edit_text(
                    "Для связи с поддержкой используйте кнопку ниже.",
                    reply_markup=keyboards.create_support_keyboard(support_user)
                )
            else:
                await callback.message.edit_text("Контакты поддержки не настроены.", reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.callback_query(F.data == "support_menu")
    @registration_required
    async def support_menu_handler(callback: types.CallbackQuery):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        support_text = get_setting("support_text") or "Раздел поддержки. Нажмите кнопку ниже, чтобы открыть чат с поддержкой."
        if support_bot_username:
            await callback.message.edit_text(
                support_text,
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            support_user = get_setting("support_user")
            if support_user:
                await callback.message.edit_text(
                    "Для связи с поддержкой используйте кнопку ниже.",
                    reply_markup=keyboards.create_support_keyboard(support_user)
                )
            else:
                await callback.message.edit_text("Контакты поддержки не настроены.", reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.callback_query(F.data == "support_external")
    @registration_required
    async def support_external_handler(callback: types.CallbackQuery):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await callback.message.edit_text(
                get_setting("support_text") or "Раздел поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
            return
        support_user = get_setting("support_user")
        if not support_user:
            await callback.message.edit_text("Внешний контакт поддержки не настроен.", reply_markup=keyboards.create_back_to_menu_keyboard())
            return
        await callback.message.edit_text(
            "Для связи с поддержкой используйте кнопку ниже.",
            reply_markup=keyboards.create_support_keyboard(support_user)
        )

    @user_router.callback_query(F.data == "support_new_ticket")
    @registration_required
    async def support_new_ticket_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await callback.message.edit_text(
                "Раздел поддержки вынесен в отдельного бота.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await callback.message.edit_text(
                "Контакты поддержки не настроены.",
                reply_markup=keyboards.create_back_to_menu_keyboard()
            )

    @user_router.message(SupportDialog.waiting_for_subject)
    @registration_required
    async def support_subject_received(message: types.Message, state: FSMContext):
        await state.clear()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await message.answer(
                "Создание тикетов доступно в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await message.answer("Контакты поддержки не настроены.")

    @user_router.message(SupportDialog.waiting_for_message)
    @registration_required
    async def support_message_received(message: types.Message, state: FSMContext, bot: Bot):
        await state.clear()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await message.answer(
                "Создание тикетов доступно в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await message.answer("Контакты поддержки не настроены.")

    @user_router.callback_query(F.data == "support_my_tickets")
    @registration_required
    async def support_my_tickets_handler(callback: types.CallbackQuery):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await callback.message.edit_text(
                "Список обращений доступен в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await callback.message.edit_text("Контакты поддержки не настроены.", reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.callback_query(F.data.startswith("support_view_"))
    @registration_required
    async def support_view_ticket_handler(callback: types.CallbackQuery):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await callback.message.edit_text(
                "Просмотр тикетов доступен в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await callback.message.edit_text("Контакты поддержки не настроены.", reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.callback_query(F.data.startswith("support_reply_"))
    @registration_required
    async def support_reply_prompt_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await state.clear()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await callback.message.edit_text(
                "Отправка ответов доступна в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await callback.message.edit_text("Контакты поддержки не настроены.", reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.message(SupportDialog.waiting_for_reply)
    @registration_required
    async def support_reply_received(message: types.Message, state: FSMContext, bot: Bot):
        await state.clear()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await message.answer(
                "Отправка ответов доступна в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
        else:
            await message.answer("Контакты поддержки не настроены.")

    @user_router.message(F.is_topic_message == True)
    async def forum_thread_message_handler(message: types.Message, bot: Bot):
        try:
            support_bot_username = get_setting("support_bot_username")
            me = await bot.get_me()
            if support_bot_username and (me.username or "").lower() != support_bot_username.lower():
                return
            if not message.message_thread_id:
                return
            forum_chat_id = message.chat.id
            thread_id = message.message_thread_id
            ticket = get_ticket_by_thread(str(forum_chat_id), int(thread_id))
            if not ticket:
                return
            user_id = int(ticket.get('user_id'))
            if message.from_user and message.from_user.id == me.id:
                return
            # Проверка многоадминная
            is_admin_by_setting = is_admin(message.from_user.id)
            is_admin_in_chat = False
            try:
                member = await bot.get_chat_member(chat_id=forum_chat_id, user_id=message.from_user.id)
                is_admin_in_chat = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
            except Exception:
                pass
            if not (is_admin_by_setting or is_admin_in_chat):
                return
            content = (message.text or message.caption or "").strip()
            if content:
                add_support_message(ticket_id=int(ticket['ticket_id']), sender='admin', content=content)
            header = await bot.send_message(
                chat_id=user_id,
                text=f"💬 Ответ поддержки по тикету #{ticket['ticket_id']}"
            )
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_to_message_id=header.message_id
                )
            except Exception:
                if content:
                    await bot.send_message(chat_id=user_id, text=content)
        except Exception as e:
            logger.warning(f"Failed to relay forum thread message: {e}")

    @user_router.callback_query(F.data.startswith("support_close_"))
    @registration_required
    async def support_close_ticket_handler(callback: types.CallbackQuery):
        await callback.answer()
        support_bot_username = get_setting("support_bot_username")
        if support_bot_username:
            await callback.message.edit_text(
                "Управление тикетами доступно в отдельном боте поддержки.",
                reply_markup=keyboards.create_support_bot_link_keyboard(support_bot_username)
            )
            return
        await callback.message.edit_text("Контакты поддержки не настроены.", reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.callback_query(F.data == "manage_keys")
    @registration_required
    async def manage_keys_handler(callback: types.CallbackQuery):
        await safe_callback_answer(callback)
        user_id = callback.from_user.id
        user_keys = get_user_keys(user_id)
        await callback.message.edit_text(
            "Ваши ключи:" if user_keys else "У вас пока нет ключей.",
            reply_markup=keyboards.create_keys_management_keyboard(user_keys)
        )

    @user_router.callback_query(F.data == "get_trial")
    @registration_required
    async def trial_period_handler(callback: types.CallbackQuery, state: FSMContext):
        user_id = callback.from_user.id
        user_db_data = get_user(user_id)
        if user_db_data and user_db_data.get('trial_used'):
            await callback.answer("Вы уже использовали бесплатный пробный период.", show_alert=True)
            return

        hosts = get_all_hosts()
        if not hosts:
            await callback.message.edit_text("❌ В данный момент нет доступных серверов для создания пробного ключа.")
            return
            
        if len(hosts) == 1:
            await callback.answer()
            await process_trial_key_creation(callback.message, hosts[0]['host_name'])
        else:
            await callback.answer()
            await callback.message.edit_text(
                "Выберите сервер, на котором хотите получить пробный ключ:",
                reply_markup=keyboards.create_host_selection_keyboard(hosts, action="trial")
            )

    @user_router.callback_query(F.data.startswith("select_host_trial_"))
    @registration_required
    async def trial_host_selection_handler(callback: types.CallbackQuery):
        await callback.answer()
        host_name = callback.data[len("select_host_trial_"):]
        await process_trial_key_creation(callback.message, host_name)

    async def process_trial_key_creation(message: types.Message, host_name: str):
        user_id = message.chat.id
        logger.info(f"Starting trial key creation for user {user_id} on host '{host_name}'")
        
        try:
            # Проверка: не использовал ли пользователь уже trial
            user_db_data = get_user(user_id)
            if user_db_data and user_db_data.get('trial_used'):
                logger.warning(f"User {user_id} tried to use trial again")
                await message.edit_text("❌ Вы уже использовали бесплатный пробный период.")
                return

            # Проверка: существует ли хост
            host_data = get_host(host_name)
            if not host_data:
                logger.error(f"Trial failed: host '{host_name}' not found in database")
                await message.edit_text("❌ Ошибка: сервер не найден в базе данных.")
                return

            # Проверка: есть ли данные для подключения
            if not host_data.get('host_url') or not host_data.get('host_username') or not host_data.get('host_pass'):
                logger.error(f"Trial failed: host '{host_name}' missing connection settings")
                await message.edit_text("❌ Ошибка: сервер не настроен (отсутствуют данные для подключения).")
                return

            # Проверка: есть ли inbound_id
            if not host_data.get('host_inbound_id'):
                logger.error(f"Trial failed: host '{host_name}' missing inbound_id")
                await message.edit_text("❌ Ошибка: сервер не настроен (отсутствует Inbound ID).")
                return

            await message.edit_text(f"Отлично! Создаю для вас бесплатный ключ на {get_setting('trial_duration_days')} дня на сервере \"{host_name}\"...")

            # Формат email: trial_{inbound_id}_{username}_{attempt}@bot.local
            # inbound_id - число из настроек хоста (ID входящего подключения)
            user_data = user_db_data or {}
            raw_username = (user_data.get('username') or f'user{user_id}').lower()
            username_slug = re.sub(r"[^a-z0-9._-]", "_", raw_username).strip("_")[:16] or f"user{user_id}"
            # Получаем inbound_id из настроек хоста
            host_data = get_host(host_name)
            inbound_id = host_data.get('host_inbound_id', '1') if host_data else '1'
            base_local = f"trial_{inbound_id}_{username_slug}"
            candidate_local = base_local + "_1"
            candidate_email = None

            # Попытка генерации уникального email (максимум 100 попыток)
            for attempt in range(1, 101):
                if attempt == 1:
                    candidate_local = base_local + "_1"
                else:
                    candidate_local = f"{base_local}_{attempt}"
                candidate_email = f"{candidate_local}@bot.local"

                existing_key = get_key_by_email(candidate_email)
                if not existing_key:
                    break
            else:
                # Если 100 попыток не удались, используем timestamp
                candidate_local = f"{base_local}_{int(datetime.now().timestamp())}"
                candidate_email = f"{candidate_local}@bot.local"
                logger.info(f"Trial email generated with timestamp: {candidate_email}")

            # Создание ключа в панели 3x-ui
            result = await xui_api.create_or_update_key_on_host(
                host_name=host_name,
                email=candidate_email,
                days_to_add=int(get_setting("trial_duration_days") or 1),
                sub_token=f"realruvpnbot{user_id}"
            )
            
            if not result:
                logger.error(f"Trial failed: xui_api returned None for host '{host_name}', email '{candidate_email}'")
                await message.edit_text(
                    "❌ Не удалось создать пробный ключ.\n\n"
                    "Возможные причины:\n"
                    "• Панель 3x-ui недоступна\n"
                    "• Неверный логин/пароль от панели\n"
                    "• Inbound с указанным ID не найден\n"
                    "\nПроверьте настройки сервера в панели администратора."
                )
                return

            # Сохранение в БД с обработкой UNIQUE constraint
            new_key_id = None
            try:
                new_key_id = add_new_key(
                    user_id=user_id,
                    host_name=host_name,
                    xui_client_uuid=result['client_uuid'],
                    key_email=result['email'],
                    expiry_timestamp_ms=result['expiry_timestamp_ms']
                )
            except Exception as db_error:
                logger.error(f"Database error while saving trial key: {db_error}")
                # Пробуем ещё раз с новым email (на случай race condition)
                fallback_email = f"trial_{user_id}_{int(datetime.now().timestamp())}@bot.local"
                result_retry = await xui_api.create_or_update_key_on_host(
                    host_name=host_name,
                    email=fallback_email,
                    days_to_add=int(get_setting("trial_duration_days") or 1),
                    sub_token=f"realruvpnbot{user_id}"
                )
                if result_retry:
                    new_key_id = add_new_key(
                        user_id=user_id,
                        host_name=host_name,
                        xui_client_uuid=result_retry['client_uuid'],
                        key_email=result_retry['email'],
                        expiry_timestamp_ms=result_retry['expiry_timestamp_ms']
                    )

            if not new_key_id:
                logger.error(f"Trial failed: could not save key to database for user {user_id}")
                await message.edit_text("❌ Ошибка при сохранении ключа в базе данных.")
                return

            # Помечаем trial как использованный
            try:
                set_trial_used(user_id)
            except Exception as e:
                logger.error(f"Failed to set trial_used for user {user_id}: {e}")
                # Не прерываем процесс, ключ уже создан

            # Удаляем сообщение с процессом создания (не критично если не получится)
            try:
                await message.delete()
            except Exception as e:
                logger.warning(f"Could not delete message for user {user_id}: {e}")
                # Не прерываем процесс - сообщение просто останется

            new_expiry_date = datetime.fromtimestamp(result['expiry_timestamp_ms'] / 1000)
            final_text = get_purchase_success_text("готов", len(get_user_keys(user_id)), new_expiry_date, result['connection_string'])
            await message.answer(text=final_text, reply_markup=keyboards.create_key_info_keyboard(new_key_id))
            logger.info(f"Trial key created successfully for user {user_id}, key_id={new_key_id}")

        except Exception as e:
            logger.error(f"Error creating trial key for user {user_id} on host {host_name}: {e}", exc_info=True)
            try:
                await message.edit_text(f"❌ Произошла ошибка при создании пробного ключа.\n\nДетали: {e}")
            except Exception:
                try:
                    await message.answer(f"❌ Произошла ошибка при создании пробного ключа.\n\nДетали: {e}")
                except Exception:
                    pass

    @user_router.callback_query(F.data.startswith("show_key_"))
    @registration_required
    async def show_key_handler(callback: types.CallbackQuery):
        key_id_to_show = int(callback.data.split("_")[2])
        await callback.message.edit_text("Загружаю информацию о ключе...")
        user_id = callback.from_user.id
        key_data = get_key_by_id(key_id_to_show)

        if not key_data or key_data['user_id'] != user_id:
            await callback.message.edit_text("❌ Ошибка: ключ не найден.")
            return
            
        try:
            details = await xui_api.get_key_details_from_host(key_data)
            if not details or not details['connection_string']:
                await callback.message.edit_text("❌ Ошибка на сервере. Не удалось получить данные ключа.")
                return

            connection_string = details['connection_string']
            expiry_date = datetime.fromisoformat(key_data['expiry_date'])
            created_date = datetime.fromisoformat(key_data['created_date'])
            
            all_user_keys = get_user_keys(user_id)
            key_number = next((i + 1 for i, key in enumerate(all_user_keys) if key['key_id'] == key_id_to_show), 0)
            
            final_text = get_key_info_text(key_number, expiry_date, created_date, connection_string)
            
            await callback.message.edit_text(
                text=final_text,
                reply_markup=keyboards.create_key_info_keyboard(key_id_to_show)
            )
        except Exception as e:
            logger.error(f"Error showing key {key_id_to_show}: {e}")
            await callback.message.edit_text("❌ Произошла ошибка при получении данных ключа.")

    @user_router.callback_query(F.data.startswith("switch_server_"))
    @registration_required
    async def switch_server_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            key_id = int(callback.data[len("switch_server_"):])
        except ValueError:
            await callback.answer("Некорректный идентификатор ключа.", show_alert=True)
            return

        key_data = get_key_by_id(key_id)
        if not key_data or key_data.get('user_id') != callback.from_user.id:
            await callback.answer("Ключ не найден.", show_alert=True)
            return

        hosts = get_all_hosts()
        if not hosts:
            await callback.answer("Нет доступных серверов.", show_alert=True)
            return

        current_host = key_data.get('host_name')
        hosts = [h for h in hosts if h.get('host_name') != current_host]
        if not hosts:
            await callback.answer("Другие серверы отсутствуют.", show_alert=True)
            return

        await callback.message.edit_text(
            "Выберите новый сервер (локацию) для этого ключа:",
            reply_markup=keyboards.create_host_selection_keyboard(hosts, action=f"switch_{key_id}")
        )

    @user_router.callback_query(F.data.startswith("select_host_switch_"))
    @registration_required
    async def select_host_for_switch(callback: types.CallbackQuery):
        await callback.answer()
        payload = callback.data[len("select_host_switch_"):]
        parts = payload.split("_", 1)
        if len(parts) != 2:
            await callback.answer("Некорректные данные выбора сервера.", show_alert=True)
            return
        try:
            key_id = int(parts[0])
        except ValueError:
            await callback.answer("Некорректный идентификатор ключа.", show_alert=True)
            return
        new_host_name = parts[1]

        key_data = get_key_by_id(key_id)

        if not key_data or key_data.get('user_id') != callback.from_user.id:
            await callback.answer("Ключ не найден.", show_alert=True)
            return

        old_host = key_data.get('host_name')
        if not old_host:
            await callback.answer("Для ключа не указан текущий сервер.", show_alert=True)
            return
        if new_host_name == old_host:
            await callback.answer("Это уже текущий сервер.", show_alert=True)
            return

        # Точное сохранение срока действия при переносе (без увеличения времени)
        try:
            expiry_dt = datetime.fromisoformat(key_data['expiry_date'])
            expiry_timestamp_ms_exact = int(expiry_dt.timestamp() * 1000)
        except Exception:
            # Fallback: хотя бы 1 день, если дата в БД повреждена
            now_dt = datetime.now()
            expiry_timestamp_ms_exact = int((now_dt + timedelta(days=1)).timestamp() * 1000)

        # Переименовываем email для нового inbound_id
        old_email = key_data.get('key_email')
        new_host_data = get_host(new_host_name)
        new_inbound_id = new_host_data.get('host_inbound_id', '1') if new_host_data else '1'
        
        # Извлекаем префикс (trial_/gift_) и username из старого email
        if old_email.startswith('trial_'):
            prefix = 'trial_'
            username_part = old_email.replace('trial_', '').split('@')[0]
            # username_part это "{old_inbound_id}_{username}_{attempt}"
            parts = username_part.split('_', 1)  # Разделяем на inbound_id и остальное
            if len(parts) == 2:
                username_and_attempt = parts[1]  # "{username}_{attempt}"
            else:
                username_and_attempt = parts[0] if parts else 'user'
        elif old_email.startswith('gift_'):
            prefix = 'gift_'
            username_part = old_email.replace('gift_', '').split('@')[0]
            parts = username_part.split('_', 1)
            if len(parts) == 2:
                username_and_attempt = parts[1]
            else:
                username_and_attempt = parts[0] if parts else 'user'
        else:
            prefix = ''
            username_part = old_email.split('@')[0]
            parts = username_part.split('_', 1)
            if len(parts) == 2:
                username_and_attempt = parts[1]
            else:
                username_and_attempt = parts[0] if parts else 'user'
        
        # Новый email с новым inbound_id
        new_email = f'{prefix}{new_inbound_id}_{username_and_attempt}@bot.local'

        await callback.message.edit_text(
            f"⏳ Переношу ключ на сервер \"{new_host_name}\"..."
        )

        try:
            # Передаём точный expiry_timestamp_ms и новый email
            result = await xui_api.create_or_update_key_on_host(
                new_host_name,
                new_email,
                days_to_add=None,
                expiry_timestamp_ms=expiry_timestamp_ms_exact,
                sub_token=f"realruvpnbot{key_data['user_id']}"
            )
            if not result:
                await callback.message.edit_text(
                    f"❌ Не удалось перенести ключ на сервер \"{new_host_name}\". Попробуйте позже."
                )
                return

            # Сначала удаляем на старом сервере со старым email
            try:
                await xui_api.delete_client_on_host(old_host, old_email)
            except Exception:
                pass

            # Затем обновляем локальную БД новым хостом, email и UUID
            update_key_host_and_info(
                key_id=key_id,
                new_host_name=new_host_name,
                new_xui_uuid=result['client_uuid'],
                new_expiry_ms=result['expiry_timestamp_ms'],
                new_email=new_email
            )

            # Показываем сразу обновлённые данные ключа
            try:
                updated_key = get_key_by_id(key_id)
                details = await xui_api.get_key_details_from_host(updated_key)
                if details and details.get('connection_string'):
                    connection_string = details['connection_string']
                    expiry_date = datetime.fromisoformat(updated_key['expiry_date'])
                    created_date = datetime.fromisoformat(updated_key['created_date'])
                    all_user_keys = get_user_keys(callback.from_user.id)
                    key_number = next((i + 1 for i, k in enumerate(all_user_keys) if k['key_id'] == key_id), 0)
                    final_text = get_key_info_text(key_number, expiry_date, created_date, connection_string)
                    await callback.message.edit_text(
                        text=final_text,
                        reply_markup=keyboards.create_key_info_keyboard(key_id)
                    )
                else:
                    # Fallback: показать сообщение об успехе
                    await callback.message.edit_text(
                        f"✅ Готово! Ключ перенесён на сервер \"{new_host_name}\".\n"
                        "Обновите подписку/конфиг в клиенте, если требуется.",
                        reply_markup=keyboards.create_back_to_menu_keyboard()
                    )
            except Exception:
                await callback.message.edit_text(
                    f"✅ Готово! Ключ перенесён на сервер \"{new_host_name}\".\n"
                    "Обновите подписку/конфиг в клиенте, если требуется.",
                    reply_markup=keyboards.create_back_to_menu_keyboard()
                )
        except Exception as e:
            logger.error(f"Error switching key {key_id} to host {new_host_name}: {e}", exc_info=True)
            await callback.message.edit_text(
                "❌ Произошла ошибка при переносе ключа. Попробуйте позже."
            )

    @user_router.callback_query(F.data.startswith("show_qr_"))
    @registration_required
    async def show_qr_handler(callback: types.CallbackQuery):
        await callback.answer("Генерирую QR-код...")
        key_id = int(callback.data.split("_")[2])
        key_data = get_key_by_id(key_id)
        if not key_data or key_data['user_id'] != callback.from_user.id: return
        
        try:
            details = await xui_api.get_key_details_from_host(key_data)
            if not details or not details['connection_string']:
                await callback.answer("Ошибка: Не удалось сгенерировать QR-код.", show_alert=True)
                return

            connection_string = details['connection_string']
            qr_img = qrcode.make(connection_string)
            bio = BytesIO(); qr_img.save(bio, "PNG"); bio.seek(0)
            qr_code_file = BufferedInputFile(bio.read(), filename="vpn_qr.png")
            await callback.message.answer_photo(photo=qr_code_file)
        except Exception as e:
            logger.error(f"Error showing QR for key {key_id}: {e}")

    @user_router.callback_query(F.data.startswith("howto_vless_"))
    @registration_required
    async def show_instruction_handler(callback: types.CallbackQuery):
        await callback.answer()
        key_id = int(callback.data.split("_")[2])

        await callback.message.edit_text(
            "Выберите вашу платформу для инструкции по подключению VLESS:",
            reply_markup=keyboards.create_howto_vless_keyboard_key(key_id),
            disable_web_page_preview=True
        )
    
    @user_router.callback_query(F.data.startswith("howto_vless"))
    @registration_required
    async def show_instruction_handler(callback: types.CallbackQuery):
        await callback.answer()

        await callback.message.edit_text(
            "Выберите вашу платформу для инструкции по подключению VLESS:",
            reply_markup=keyboards.create_howto_vless_keyboard(),
            disable_web_page_preview=True
        )

    @user_router.callback_query(F.data == "howto_android")
    @registration_required
    async def howto_android_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на Android</b>\n\n"
            "1. <b>Установите приложение <a href=\"https://v2raytun.com/\">V2RayTun</a>:</b> Загрузите и установите приложение V2RayTun из <a href=\"https://play.google.com/store/apps/details?id=com.v2raytun.android&pcampaignid=web_share\">Google Play Store</a>.\n"
            "2. <b>Скопируйте свой ключ (vless://)</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "3. <b>Импортируйте конфигурацию:</b>\n"
            "   • Откройте V2RayTun.\n"
            "   • Нажмите на значок + в правом нижнем углу.\n"
            "   • Выберите «Импортировать конфигурацию из буфера обмена» (или аналогичный пункт).\n"
            "4. <b>Выберите сервер:</b> Выберите появившийся сервер в списке.\n"
            "5. <b>Подключитесь к VPN:</b> Нажмите на кнопку подключения (значок «V» или воспроизведения). Возможно, потребуется разрешение на создание VPN-подключения.\n"
            "6. <b>Проверьте подключение:</b> После подключения проверьте свой IP-адрес, например, на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    @user_router.callback_query(F.data == "howto_ios")
    @registration_required
    async def howto_ios_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "📱 <b>Подключение на iOS (iPhone/iPad)</b>\n\n"
            "<b>Шаг 1. Установите приложение</b>\n\n"
            "Можно использовать любое из этих приложений (все работают с нашими ключами):\n\n"
            "• <a href=\"https://apps.apple.com/us/app/v2raytun/id6476628951\">V2RayTun</a>\n"
            "• <a href=\"https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690\">V2Box</a>\n"
            "• <a href=\"https://apps.apple.com/us/app/streisand/id6450534064\">Streisand</a>\n"
            "• <a href=\"https://apps.apple.com/us/app/razze/id6752694105\">Razze</a>\n"
            "• <a href=\"https://apps.apple.com/us/app/happ-proxy-utility/id6504287215\">Happ</a>\n\n"
            "💡 <b>Настройка на примере V2RayTun:</b>\n"
            "1. Откройте приложение и нажмите <b>+</b>.\n"
            "2. Выберите <b>«Импортировать из буфера обмена»</b>.\n"
            "3. Скопируйте ключ (vless://) в нашем боте — приложение подхватит его автоматически.\n"
            "4. Выберите сервер и включите VPN.\n\n"
            "⚠️ <b>Если приложения нет на телефоне и оно не скачивается из App Store:</b>\n\n"
            "С 31.03.2026 эти приложения удалены из российского App Store. Чтобы скачать, нужно сменить регион Apple ID на другую страну (США, Турция, Казахстан и т.д.).\n\n"
            "Нажмите кнопку <b>«Как сменить регион»</b> ниже — там подробная инструкция 👇",
            reply_markup=keyboards.create_howto_ios_keyboard(),
            disable_web_page_preview=True
        )

    @user_router.callback_query(F.data == "howto_ios_change_region")
    @registration_required
    async def howto_ios_change_region_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "📱 <b>Как сменить регион Apple ID для установки VPN-приложений</b>\n\n"
            "1️⃣ Откройте <b>Настройки</b> → нажмите на своё имя → <b>Медиаматериалы и покупки</b>\n\n"
            "2️⃣ Выберите <b>«Просмотреть»</b> → при необходимости авторизуйтесь\n\n"
            "3️⃣ Перейдите в <b>Страна/регион</b> → нажмите <b>«Изменить страну или регион»</b>\n\n"
            "4️⃣ Выберите нужный регион, например:\n"
            "🇺🇸 США  •  🇹🇷 Турция  •  🇰🇿 Казахстан\n\n"
            "5️⃣ Примите условия и заполните данные:\n"
            "• <b>Способ оплаты</b> → выберите <b>None (Нет)</b>\n"
            "• <b>Адрес</b> → можно указать любой валидный\n\n"
            "Пример для США:\n"
            "• Street: <code>123 Main St</code>\n"
            "• City: <code>New York</code>\n"
            "• ZIP: <code>10001</code>\n"
            "• Phone: <code>1234567890</code>\n\n"
            "6️⃣ Сохраните изменения\n\n"
            "7️⃣ Откройте App Store и скачайте необходимое приложение\n\n"
            "⚠️ <b>Если не получается сменить регион:</b>\n"
            "• Убедитесь, что нет активных подписок\n"
            "• Баланс Apple ID должен быть равен <b>0</b>\n"
            "• Выйдите из семейного доступа (если подключены)\n\n"
            "✅ Готово! Теперь вы можете скачать VPN-приложение.",
            reply_markup=keyboards.create_back_to_menu_keyboard(),
            disable_web_page_preview=True
        )

    @user_router.callback_query(F.data == "howto_macos")
    @registration_required
    async def howto_macos_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "🍎 <b>Подключение на macOS</b>\n\n"
            "<b>Шаг 1. Установите приложение V2Box</b>\n"
            "Скачайте <a href=\"https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690\">V2Box</a> из App Store.\n\n"
            "<b>Шаг 2. Скопируйте ключ (vless://)</b>\n"
            "Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n\n"
            "<b>Шаг 3. Импортируйте конфигурацию</b>\n"
            "• Откройте V2Box.\n"
            "• Нажмите <b>+</b> (добавить профиль).\n"
            "• Приложение автоматически предложит импортировать конфигурацию из буфера обмена.\n"
            "• Если этого не произошло — вставьте ключ вручную и нажмите <b>«Добавить»</b>.\n\n"
            "<b>Шаг 4. Подключитесь</b>\n"
            "• Выберите добавленный сервер из списка.\n"
            "• Включите VPN-подключение.\n"
            "• При запросе разрешите создание VPN-конфигурации в системных настройках.\n\n"
            "<b>Шаг 5. Проверьте подключение</b>\n"
            "Откройте браузер и проверьте IP на <a href=\"https://whatismyipaddress.com/\">whatismyipaddress.com</a>. Он должен отличаться от вашего реального.\n\n"
            "💡 <b>Альтернатива:</b> также можно использовать <a href=\"https://github.com/hiddify/hiddify-app/releases\">Hiddify для macOS</a> — установка аналогична.",
            reply_markup=keyboards.create_howto_vless_keyboard(),
            disable_web_page_preview=True
        )

    @user_router.callback_query(F.data == "howto_windows")
    @registration_required
    async def howto_windows_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на Windows</b>\n\n"
            "<b>Вариант 1: Hiddify (рекомендуется)</b>\n"
            "1. <b>Установите приложение <a href=\"https://hiddify.com/\">Hiddify</a>:</b> Загрузите с <a href=\"https://hiddify.com/\">официального сайта</a> или <a href=\"https://github.com/hiddify/hiddify-app/releases\">зеркала на GitHub</a>. Выберите версию <code>Setup-x64.exe</code> (установщик) или <code>Portable-x64.exe</code> (портативная).\n"
            "2. <b>Установите приложение:</b> Запустите установщик и следуйте инструкциям (или распакуйте портативную версию).\n"
            "3. <b>Запустите Hiddify:</b> Откройте приложение.\n"
            "4. <b>Скопируйте свой ключ (vless://):</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "5. <b>Импортируйте конфигурацию:</b>\n"
            "   • В Hiddify нажмите кнопку «Добавить профиль» (или «+»).  \n"
            "   • Выберите «Импортировать из буфера обмена».\n"
            "   • Ключ автоматически добавится в список.\n"
            "6. <b>Выберите сервер:</b> Нажмите на добавленный сервер в списке.\n"
            "7. <b>Подключитесь к VPN:</b> Нажмите большую кнопку подключения в центре экрана.\n"
            "8. <b>Проверьте подключение:</b> Откройте браузер и проверьте IP на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.\n\n"
            "<b>Вариант 2: V2RayTun</b>\n"
            "1. <b>Установите приложение <a href=\"https://v2raytun.com/\">V2RayTun</a>:</b> Загрузите с <a href=\"https://v2raytun.com/\">официального сайта</a>.\n"
            "2. <b>Установите и запустите:</b> Следуйте инструкциям установщика.\n"
            "3. <b>Скопируйте свой ключ (vless://):</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "4. <b>Импортируйте конфигурацию:</b>\n"
            "   • Откройте V2RayTun.\n"
            "   • Нажмите «+» (Добавить).\n"
            "   • Выберите «Импортировать из буфера обмена».\n"
            "5. <b>Подключитесь:</b> Нажмите кнопку подключения.\n"
            "6. <b>Проверьте подключение:</b> Проверьте IP на https://whatismyipaddress.com/.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    @user_router.callback_query(F.data == "howto_linux")
    @registration_required
    async def howto_linux_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на Linux</b>\n\n"
            "<b>Установите приложение Hiddify:</b>\n\n"
            "<b>Способ 1: Автоматическая установка (для Ubuntu)</b>\n"
            "<code>bash &lt;(curl https://i.hiddify.com/release)</code>\n\n"
            "<b>Способ 2: Ручная установка</b>\n"
            "1. <b>Загрузите Hiddify:</b> Перейдите на <a href=\"https://github.com/hiddify/hiddify-app/releases\">GitHub Releases</a> и выберите версию для вашего дистрибутива:\n"
            "   • <code>.deb</code> — для Debian, Ubuntu, Linux Mint\n"
            "   • <code>.rpm</code> — для Fedora, RHEL, CentOS\n"
            "   • <code>.AppImage</code> — портативная версия для любых дистрибутивов\n\n"
            "2. <b>Установите приложение:</b>\n"
            "   • Для <code>.deb</code>: <code>sudo apt install ./hiddify.deb</code>\n"
            "   • Для <code>.rpm</code>: <code>sudo dnf install hiddify.rpm</code>\n"
            "   • Для <code>.AppImage</code>: <code>chmod +x hiddify.AppImage && ./hiddify.AppImage</code>\n\n"
            "3. <b>Запустите Hiddify:</b> Откройте приложение из меню приложений или выполните команду <code>hiddify</code> в терминале.\n\n"
            "4. <b>Скопируйте свой ключ (vless://):</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n\n"
            "5. <b>Импортируйте конфигурацию:</b>\n"
            "   • В Hiddify нажмите кнопку «Добавить профиль» (или «+»).\n"
            "   • Выберите «Импортировать из буфера обмена».\n"
            "   • Ключ автоматически добавится в список.\n\n"
            "6. <b>Выберите сервер:</b> Нажмите на добавленный сервер в списке.\n\n"
            "7. <b>Подключитесь к VPN:</b> Нажмите большую кнопку подключения в центре экрана.\n\n"
            "8. <b>Проверьте подключение:</b> Откройте браузер и проверьте IP на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    @user_router.callback_query(F.data == "buy_new_key")
    @registration_required
    async def buy_new_key_handler(callback: types.CallbackQuery):
        await callback.answer()
        hosts = get_all_hosts()
        if not hosts:
            await callback.message.edit_text("❌ В данный момент нет доступных серверов для покупки.")
            return
        
        await callback.message.edit_text(
            "Выберите сервер, на котором хотите приобрести ключ:",
            reply_markup=keyboards.create_host_selection_keyboard(hosts, action="new")
        )

    @user_router.callback_query(F.data.startswith("select_host_new_"))
    @registration_required
    async def select_host_for_purchase_handler(callback: types.CallbackQuery):
        await callback.answer()
        host_name = callback.data[len("select_host_new_"):]
        plans = get_plans_for_host(host_name)
        if not plans:
            await callback.message.edit_text(f"❌ Для сервера \"{host_name}\" не настроены тарифы.")
            return
        await callback.message.edit_text(
            "Выберите тариф для нового ключа:", 
            reply_markup=keyboards.create_plans_keyboard(plans, action="new", host_name=host_name)
        )

    @user_router.callback_query(F.data.startswith("extend_key_"))
    @registration_required
    async def extend_key_handler(callback: types.CallbackQuery):
        await callback.answer()

        try:
            key_id = int(callback.data.split("_")[2])
        except (IndexError, ValueError):
            await callback.message.edit_text("❌ Произошла ошибка. Неверный формат ключа.")
            return

        key_data = get_key_by_id(key_id)

        if not key_data or key_data['user_id'] != callback.from_user.id:
            await callback.message.edit_text("❌ Ошибка: Ключ не найден или не принадлежит вам.")
            return
        
        host_name = key_data.get('host_name')
        if not host_name:
            await callback.message.edit_text("❌ Ошибка: У этого ключа не указан сервер. Обратитесь в поддержку.")
            return

        plans = get_plans_for_host(host_name)

        if not plans:
            await callback.message.edit_text(
                f"❌ Извините, для сервера \"{host_name}\" в данный момент не настроены тарифы для продления."
            )
            return

        await callback.message.edit_text(
            f"Выберите тариф для продления ключа на сервере \"{host_name}\":",
            reply_markup=keyboards.create_plans_keyboard(
                plans=plans,
                action="extend",
                host_name=host_name,
                key_id=key_id
            )
        )

    @user_router.callback_query(F.data.startswith("buy_"))
    @registration_required
    async def plan_selection_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        
        parts = callback.data.split("_")[1:]
        action = parts[-2]
        key_id = int(parts[-1])
        plan_id = int(parts[-3])
        host_name = "_".join(parts[:-3])

        await state.update_data(
            action=action, key_id=key_id, plan_id=plan_id, host_name=host_name
        )
        
        await callback.message.edit_text(
            "📧 Пожалуйста, введите ваш email для отправки чека об оплате.\n\n"
            "Если вы не хотите указывать почту, нажмите кнопку ниже.",
            reply_markup=keyboards.create_skip_email_keyboard()
        )
        await state.set_state(PaymentProcess.waiting_for_email)

    @user_router.callback_query(PaymentProcess.waiting_for_email, F.data == "back_to_plans")
    async def back_to_plans_handler(callback: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        
        action = data.get('action')

        if action == 'new':
            await buy_new_key_handler(callback)
        elif action == 'extend':
            await extend_key_handler(callback)
        else:
            await back_to_main_menu_handler(callback)

    @user_router.message(PaymentProcess.waiting_for_email)
    async def process_email_handler(message: types.Message, state: FSMContext):
        if is_valid_email(message.text):
            await state.update_data(customer_email=message.text)
            await message.answer(f"✅ Email принят: {message.text}")

            # Показываем опции оплаты с учетом балансов и цены
            await show_payment_options(message, state)
            logger.info(f"User {message.chat.id}: State set to waiting_for_payment_method via show_payment_options")
        else:
            await message.answer("❌ Неверный формат email. Попробуйте еще раз.")

    @user_router.callback_query(PaymentProcess.waiting_for_email, F.data == "skip_email")
    async def skip_email_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await state.update_data(customer_email=None)

        # Показываем опции оплаты с учетом балансов и цены
        await show_payment_options(callback.message, state)
        logger.info(f"User {callback.from_user.id}: State set to waiting_for_payment_method via show_payment_options")

    async def show_payment_options(message: types.Message, state: FSMContext):
        data = await state.get_data()
        user_data = get_user(message.chat.id)
        plan = get_plan_by_id(data.get('plan_id'))

        if not plan:
            try:
                await message.edit_text("❌ Ошибка: Тариф не найден.")
            except TelegramBadRequest:
                await message.answer("❌ Ошибка: Тариф не найден.")
            await state.clear()
            return

        price = Decimal(str(plan['price']))
        final_price = price
        discount_applied = False
        message_text = CHOOSE_PAYMENT_METHOD_MESSAGE

        if user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)

            if discount_percentage > 0:
                discount_amount = (price * discount_percentage / 100).quantize(Decimal("0.01"))
                final_price = price - discount_amount

                message_text = (
                    f"🎉 Как приглашенному пользователю, на вашу первую покупку предоставляется скидка {discount_percentage_str}%!\n"
                    f"Старая цена: <s>{price:.2f} RUB</s>\n"
                    f"<b>Новая цена: {final_price:.2f} RUB</b>\n\n"
                ) + CHOOSE_PAYMENT_METHOD_MESSAGE

        await state.update_data(final_price=float(final_price))

        # Получаем основной баланс для показа кнопки оплаты с баланса
        try:
            main_balance = get_balance(message.chat.id)
        except Exception:
            main_balance = 0.0

        show_balance_btn = main_balance >= float(final_price)
        price_stars = plan.get('price_stars', 0)

        try:
            await message.edit_text(
                message_text,
                reply_markup=keyboards.create_payment_method_keyboard(
                    action=data.get('action'),
                    key_id=data.get('key_id'),
                    show_balance=show_balance_btn,
                    main_balance=main_balance,
                    price=float(final_price),
                    price_stars=price_stars if price_stars > 0 else None
                )
            )
        except TelegramBadRequest:
            await message.answer(
                message_text,
                reply_markup=keyboards.create_payment_method_keyboard(
                    action=data.get('action'),
                    key_id=data.get('key_id'),
                    show_balance=show_balance_btn,
                    main_balance=main_balance,
                    price=float(final_price),
                    price_stars=price_stars if price_stars > 0 else None
                )
            )
        await state.set_state(PaymentProcess.waiting_for_payment_method)
        
    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "back_to_email_prompt")
    async def back_to_email_prompt_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.message.edit_text(
            "📧 Пожалуйста, введите ваш email для отправки чека об оплате.\n\n"
            "Если вы не хотите указывать почту, нажмите кнопку ниже.",
            reply_markup=keyboards.create_skip_email_keyboard()
        )
        await state.set_state(PaymentProcess.waiting_for_email)

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_yookassa")
    async def create_yookassa_payment_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю ссылку на оплату...")
        
        data = await state.get_data()
        user_data = get_user(callback.from_user.id)
        
        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)

        if not plan:
            await callback.message.answer("Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        base_price = Decimal(str(plan['price']))
        price_rub = base_price

        if user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)
            if discount_percentage > 0:
                discount_amount = (base_price * discount_percentage / 100).quantize(Decimal("0.01"))
                price_rub = base_price - discount_amount

        plan_id = data.get('plan_id')
        customer_email = data.get('customer_email')
        host_name = data.get('host_name')
        action = data.get('action')
        key_id = data.get('key_id')
        
        if not customer_email:
            customer_email = get_setting("receipt_email")

        plan = get_plan_by_id(plan_id)
        if not plan:
            await callback.message.answer("Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        months = plan['months']
        user_id = callback.from_user.id

        try:
            price_str_for_api = f"{price_rub:.2f}"
            price_float_for_metadata = float(price_rub)

            receipt = None
            if customer_email and is_valid_email(customer_email):
                receipt = {
                    "customer": {"email": customer_email},
                    "items": [{
                        "description": f"Подписка на {months} мес.",
                        "quantity": "1.00",
                        "amount": {"value": price_str_for_api, "currency": "RUB"},
                        "vat_code": "1"
                    }]
                }
            payment_payload = {
                "amount": {"value": price_str_for_api, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": f"https://t.me/{TELEGRAM_BOT_USERNAME}"},
                "capture": True,
                "description": f"Подписка на {months} мес.",
                "metadata": {
                    "user_id": user_id, "months": months, "price": price_float_for_metadata, 
                    "action": action, "key_id": key_id, "host_name": host_name,
                    "plan_id": plan_id, "customer_email": customer_email,
                    "payment_method": "YooKassa"
                }
            }
            if receipt:
                payment_payload['receipt'] = receipt

            payment = Payment.create(payment_payload, uuid.uuid4())
            
            await state.clear()
            
            await callback.message.edit_text(
                "Нажмите на кнопку ниже для оплаты:",
                reply_markup=keyboards.create_payment_keyboard(payment.confirmation.confirmation_url)
            )
        except Exception as e:
            logger.error(f"Failed to create YooKassa payment: {e}", exc_info=True)
            await callback.message.answer("Не удалось создать ссылку на оплату.")
            await state.clear()

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_cryptobot")
    async def create_cryptobot_invoice_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю счет в Crypto Pay...")

        data = await state.get_data()
        user_id = callback.from_user.id

        cryptobot_token = get_setting('cryptobot_token')
        if not cryptobot_token:
            logger.error(f"CryptoBot token is not set for user {user_id}")
            await callback.message.edit_text("❌ Оплата криптовалютой временно недоступна. (Администратор не указал токен).")
            await state.clear()
            return

        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)
        if not plan:
            await callback.message.edit_text("❌ Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        base_price = Decimal(str(plan['price']))
        price_rub_decimal = base_price

        # Apply referral discount if applicable
        user_data = get_user(user_id)
        if user_data and user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)
            if discount_percentage > 0:
                discount_amount = (base_price * discount_percentage / 100).quantize(Decimal("0.01"))
                price_rub_decimal = base_price - discount_amount

        final_price = float(price_rub_decimal)
        description = f"Оплата тарифа '{plan['plan_name']}' ({plan['months']} мес.)"

        invoice_url = await _create_cryptobot_invoice(
            user_id=user_id,
            amount_rub=final_price,
            description=description,
            state_data=data
        )

        if invoice_url:
            await callback.message.edit_text(
                f"💳 Счёт на сумму <b>{final_price:.2f} RUB</b>\n\n"
                f"Оплатите по ссылке ниже:\n{invoice_url}",
                reply_markup=keyboards.create_payment_keyboard(invoice_url)
            )
            await state.clear()
        else:
            await callback.message.edit_text("❌ Не удалось создать счет CryptoBot. Попробуйте другой способ оплаты.")

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_heleket")
    async def create_heleket_payment_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю счет через Heleket...")

        data = await state.get_data()
        user_id = callback.from_user.id

        heleket_merchant_id = get_setting("heleket_merchant_id")
        heleket_api_key = get_setting("heleket_api_key")

        if not heleket_merchant_id or not heleket_api_key:
            logger.error(f"Heleket credentials not set for user {user_id}")
            await callback.message.edit_text("❌ Оплата через Heleket временно недоступна.")
            await state.clear()
            return

        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)
        if not plan:
            await callback.message.edit_text("❌ Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        base_price = Decimal(str(plan['price']))
        price_rub_decimal = base_price

        # Apply referral discount if applicable
        user_data = get_user(user_id)
        if user_data and user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)
            if discount_percentage > 0:
                discount_amount = (base_price * discount_percentage / 100).quantize(Decimal("0.01"))
                price_rub_decimal = base_price - discount_amount

        final_price = float(price_rub_decimal)

        payment_url = await _create_heleket_payment_request(
            user_id=user_id,
            price=final_price,
            months=plan['months'],
            host_name=data.get('host_name'),
            state_data=data
        )

        if payment_url:
            await callback.message.edit_text(
                f"💳 Счёт на сумму <b>{final_price:.2f} RUB</b>\n\n"
                f"Оплатите по ссылке ниже:\n{payment_url}",
                reply_markup=keyboards.create_payment_keyboard(payment_url)
            )
            await state.clear()
        else:
            await callback.message.edit_text("❌ Не удалось создать счет Heleket. Попробуйте другой способ оплаты.")

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_tonconnect")
    async def create_ton_invoice_handler(callback: types.CallbackQuery, state: FSMContext):
        logger.info(f"User {callback.from_user.id}: Entered create_ton_invoice_handler.")
        data = await state.get_data()
        user_id = callback.from_user.id
        wallet_address = get_setting("ton_wallet_address")
        plan = get_plan_by_id(data.get('plan_id'))
        
        if not wallet_address or not plan:
            await callback.message.edit_text("❌ Оплата через TON временно недоступна.")
            await state.clear()
            return

        await callback.answer("Создаю ссылку и QR-код для TON Connect...")
            
        price_rub = Decimal(str(data.get('final_price', plan['price'])))

        usdt_rub_rate = await get_usdt_rub_rate()
        ton_usdt_rate = await get_ton_usdt_rate()

        if not usdt_rub_rate or not ton_usdt_rate:
            await callback.message.edit_text("❌ Не удалось получить курс TON. Попробуйте позже.")
            await state.clear()
            return

        price_ton = (price_rub / usdt_rub_rate / ton_usdt_rate).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        amount_nanoton = int(price_ton * 1_000_000_000)
        
        payment_id = str(uuid.uuid4())
        metadata = {
            "user_id": user_id, "months": plan['months'], "price": float(price_rub),
            "action": data.get('action'), "key_id": data.get('key_id'),
            "host_name": data.get('host_name'), "plan_id": data.get('plan_id'),
            "customer_email": data.get('customer_email'), "payment_method": "TON Connect"
        }
        create_pending_transaction(payment_id, user_id, float(price_rub), metadata)

        transaction_payload = {
            'messages': [{'address': wallet_address, 'amount': str(amount_nanoton), 'payload': payment_id}],
            'valid_until': int(datetime.now().timestamp()) + 600
        }

        try:
            connect_url = await _start_ton_connect_process(user_id, transaction_payload)
            
            qr_img = qrcode.make(connect_url)
            bio = BytesIO()
            qr_img.save(bio, "PNG")
            qr_file = BufferedInputFile(bio.getvalue(), "ton_qr.png")

            await callback.message.delete()
            await callback.message.answer_photo(
                photo=qr_file,
                caption=(
                    f"💎 **Оплата через TON Connect**\n\n"
                    f"Сумма к оплате: `{price_ton}` **TON**\n\n"
                    f"✅ **Способ 1 (на телефоне):** Нажмите кнопку **'Открыть кошелек'** ниже.\n"
                    f"✅ **Способ 2 (на компьютере):** Отсканируйте QR-код кошельком.\n\n"
                    f"После подключения кошелька подтвердите транзакцию."
                ),
                parse_mode="Markdown",
                reply_markup=keyboards.create_ton_connect_keyboard(connect_url)
            )
            await state.clear()

        except Exception as e:
            logger.error(f"Failed to generate TON Connect link for user {user_id}: {e}", exc_info=True)
            await callback.message.answer("❌ Не удалось создать ссылку для TON Connect. Попробуйте позже.")
            await state.clear()

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_stars")
    async def create_stars_payment_handler(callback: types.CallbackQuery, state: FSMContext):
        """Создание инвойса Telegram Stars."""
        await callback.answer("Создаю счет в Telegram Stars...")

        data = await state.get_data()
        user_id = callback.from_user.id

        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)

        if not plan:
            await callback.message.edit_text("❌ Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        # Проверяем, задана ли цена в звёздах
        price_stars = plan.get('price_stars', 0)
        if not price_stars or price_stars <= 0:
            await callback.message.edit_text("❌ Для этого тарифа не указана цена в Telegram Stars.")
            await state.clear()
            return

        customer_email = data.get('customer_email')
        host_name = data.get('host_name')
        action = data.get('action')
        key_id = data.get('key_id')
        months = plan['months']

        if not customer_email:
            customer_email = get_setting("receipt_email")

        try:
            bot_info = await callback.bot.get_me()
            bot_name = bot_info.first_name

            # Создаём инвойс Telegram Stars
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=f"💫 Оплатить ({price_stars} ⭐)", pay=True)
            keyboard.button(text="⬅️ Назад", callback_data="back_to_email_prompt")
            keyboard.adjust(1)
            
            await callback.message.answer_invoice(
                title=bot_name,
                description=f"Оплата тарифа «{plan['plan_name']}» ({months} мес.)",
                payload=f"stars_plan:{plan_id}:{user_id}:{host_name}:{action or 'new_key'}:{key_id or ''}:{customer_email or ''}",
                provider_token="",  # Для Stars не нужен
                currency="XTR",  # Валюта Telegram Stars
                prices=[LabeledPrice(label=f"Тариф {plan['plan_name']}", amount=price_stars)],
                reply_markup=keyboard.as_markup()
            )
            await callback.message.delete()
        except Exception as e:
            logger.error(f"Failed to create Telegram Stars invoice: {e}", exc_info=True)
            await callback.message.edit_text("❌ Не удалось создать счет Telegram Stars. Попробуйте позже.")
            await state.clear()

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_balance")
    async def pay_with_main_balance_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        await callback.answer()
        data = await state.get_data()
        user_id = callback.from_user.id
        plan = get_plan_by_id(data.get('plan_id'))
        if not plan:
            await callback.message.edit_text("❌ Ошибка: Тариф не найден.")
            await state.clear()
            return
        months = int(plan['months'])
        price = float(data.get('final_price', plan['price']))

        # Пытаемся списать средства с основного баланса
        if not deduct_from_balance(user_id, price):
            await callback.answer("Недостаточно средств на основном балансе.", show_alert=True)
            return

        metadata = {
            "user_id": user_id,
            "months": months,
            "price": price,
            "action": data.get('action'),
            "key_id": data.get('key_id'),
            "host_name": data.get('host_name'),
            "plan_id": data.get('plan_id'),
            "customer_email": data.get('customer_email'),
            "payment_method": "Balance",
            "chat_id": callback.message.chat.id,
            "message_id": callback.message.message_id
        }

        await state.clear()
        await process_successful_payment(bot, metadata)

    # ========================================================================
    # TELEGRAM STARS - PRE-CHECKOUT И ОБРАБОТКА ОПЛАТЫ
    # ========================================================================

    @user_router.pre_checkout_query()
    async def pre_checkout_query_handler(pre_checkout: PreCheckoutQuery):
        """Подтверждение pre-checkout для Telegram Stars."""
        # Всегда подтверждаем — проверки делаем при создании invoice
        await pre_checkout.answer(ok=True)

    @user_router.message(F.successful_payment)
    async def successful_payment_stars_handler(message: types.Message, state: FSMContext, bot: Bot):
        """Обработка успешной оплаты Telegram Stars."""
        payment = message.successful_payment
        payload = payment.invoice_payload
        stars_amount = payment.total_amount  # Количество звёзд

        logger.info(f"Успешная оплата Stars: {payload}, звёзд: {stars_amount}")

        if payload.startswith("stars_plan:"):
            parts = payload.split(":")
            # stars_plan:{plan_id}:{user_id}:{host_name}:{action}:{key_id}:{customer_email}
            try:
                plan_id = int(parts[1])
                user_id = int(parts[2])
                host_name = parts[3] if len(parts) > 3 else ""
                action = parts[4] if len(parts) > 4 else "new_key"
                key_id = int(parts[5]) if len(parts) > 5 and parts[5] else None
                customer_email = parts[6] if len(parts) > 6 else ""
            except (IndexError, ValueError) as e:
                logger.error(f"Failed to parse Stars payload: {e}, payload: {payload}")
                await message.answer("❌ Произошла ошибка при обработке платежа.")
                return

            plan = get_plan_by_id(plan_id)
            if not plan:
                await message.answer("❌ Тариф не найден.")
                return

            # Конвертируем звёзды в рубли для учёта (1 звезда ≈ 1.25 RUB, но можно настроить)
            stars_to_rub_rate = 1.25
            price_rub = stars_amount * stars_to_rub_rate

            metadata = {
                "user_id": user_id,
                "months": plan['months'],
                "price": price_rub,
                "action": action,
                "key_id": key_id,
                "host_name": host_name,
                "plan_id": plan_id,
                "customer_email": customer_email,
                "payment_method": "Stars",
                "chat_id": message.chat.id,
                "message_id": message.message_id
            }

            await process_successful_payment(bot, metadata)

        elif payload.startswith("stars_topup:"):
            parts = payload.split(":")
            # stars_topup:{user_id}:{amount_rub}
            try:
                user_id = int(parts[1])
                amount_rub = float(parts[2])
            except (IndexError, ValueError) as e:
                logger.error(f"Failed to parse Stars topup payload: {e}, payload: {payload}")
                await message.answer("❌ Произошла ошибка при обработке платежа.")
                return

            # Конвертируем звёзды в рубли для учёта
            stars_to_rub_rate = 1.25
            price_rub = stars_amount * stars_to_rub_rate

            metadata = {
                "user_id": user_id,
                "price": price_rub,
                "action": "top_up",
                "payment_method": "Stars",
                "chat_id": message.chat.id,
                "message_id": message.message_id
            }

            await process_successful_payment(bot, metadata)

        else:
            # Другие типы платежей (не Stars)
            pass

    return user_router

async def notify_admin_of_purchase(bot: Bot, metadata: dict):
    try:
        admin_id_raw = get_setting("admin_telegram_id")
        if not admin_id_raw:
            return
        admin_id = int(admin_id_raw)
        user_id = metadata.get('user_id')
        host_name = metadata.get('host_name')
        months = metadata.get('months')
        price = metadata.get('price')
        action = metadata.get('action')
        payment_method = metadata.get('payment_method') or 'Unknown'
        # Локализация методов оплаты для уведомления админу
        payment_method_map = {
            'Balance': 'Баланс',
            'Card': 'Карта',
            'Crypto': 'Крипто',
            'USDT': 'USDT',
            'TON': 'TON',
        }
        payment_method_display = payment_method_map.get(payment_method, payment_method)
        plan_id = metadata.get('plan_id')
        plan = get_plan_by_id(plan_id)
        plan_name = plan.get('plan_name', 'Unknown') if plan else 'Unknown'

        text = (
            "📥 Новая оплата\n"
            f"👤 Пользователь: {user_id}\n"
            f"🗺️ Хост: {host_name}\n"
            f"📦 Тариф: {plan_name} ({months} мес.)\n"
            f"💳 Метод: {payment_method_display}\n"
            f"💰 Сумма: {float(price):.2f} RUB\n"
            f"⚙️ Действие: {'Новый ключ' if action == 'new' else 'Продление'}"
        )
        await bot.send_message(admin_id, text)
    except Exception as e:
        logger.warning(f"notify_admin_of_purchase failed: {e}")

async def process_successful_payment(bot: Bot, metadata: dict):
    try:
        action = metadata.get('action')
        user_id = int(metadata.get('user_id'))
        price = float(metadata.get('price'))
        # Поля ниже нужны только для покупок ключей/продлений
        months = int(metadata.get('months', 0))
        key_id = int(metadata.get('key_id', 0)) if metadata.get('key_id') is not None else 0
        host_name = metadata.get('host_name', '')
        plan_id = int(metadata.get('plan_id', 0)) if metadata.get('plan_id') is not None else 0
        customer_email = metadata.get('customer_email')
        payment_method = metadata.get('payment_method')

        chat_id_to_delete = metadata.get('chat_id')
        message_id_to_delete = metadata.get('message_id')
        
    except (ValueError, TypeError) as e:
        logger.error(f"FATAL: Could not parse metadata. Error: {e}. Metadata: {metadata}")
        return

    if chat_id_to_delete and message_id_to_delete:
        try:
            await bot.delete_message(chat_id=chat_id_to_delete, message_id=message_id_to_delete)
        except TelegramBadRequest as e:
            logger.warning(f"Could not delete payment message: {e}")

    # Спец-ветка: пополнение баланса
    if action == "top_up":
        try:
            ok = add_to_balance(user_id, float(price))
        except Exception as e:
            logger.error(f"Failed to add to balance for user {user_id}: {e}", exc_info=True)
            ok = False
        # Лог транзакции
        try:
            user_info = get_user(user_id)
            log_username = user_info.get('username', 'N/A') if user_info else 'N/A'
            log_transaction(
                username=log_username,
                transaction_id=None,
                payment_id=str(uuid.uuid4()),
                user_id=user_id,
                status='paid',
                amount_rub=float(price),
                amount_currency=None,
                currency_name=None,
                payment_method=payment_method or 'Unknown',
                metadata=json.dumps({"action": "top_up"})
            )
        except Exception:
            pass
        try:
            current_balance = 0.0
            try:
                current_balance = float(get_balance(user_id))
            except Exception:
                pass
            if ok:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ Оплата получена!\n"
                        f"💼 Баланс пополнен на {float(price):.2f} RUB.\n"
                        f"Текущий баланс: {current_balance:.2f} RUB."
                    ),
                    reply_markup=keyboards.create_profile_keyboard()
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⚠️ Оплата получена, но не удалось обновить баланс. "
                        "Обратитесь в поддержку."
                    ),
                    reply_markup=keyboards.create_support_keyboard()
                )
        except Exception:
            pass
        # Админ-уведомление о пополнении (по возможности)
        try:
            admins = [u for u in (get_all_users() or []) if is_admin(u.get('telegram_id') or 0)]
            for a in admins:
                admin_id = a.get('telegram_id')
                if admin_id:
                    await bot.send_message(admin_id, f"📥 Пополнение: пользователь {user_id}, сумма {float(price):.2f} RUB")
        except Exception:
            pass
        return

    processing_message = await bot.send_message(
        chat_id=user_id,
        text=f"✅ Оплата получена! Обрабатываю ваш запрос на сервере \"{host_name}\"..."
    )
    try:
        email = ""
        # Цена нужна ниже вне зависимости от ветки
        price = float(metadata.get('price'))
        result = None
        # Определяем email для операции и вызываем панель для обеих веток (new/extend)
        if action == "new":
            # Формат email: {inbound_id}_{username}_{attempt}@bot.local
            # inbound_id - число из настроек хоста (ID входящего подключения)
            # attempt - номер ключа пользователя на этом inbound_id (начиная с 1)
            # Пример: 2_holylittlegirl_1@bot.local (первый ключ на inbound 2)
            user_data = get_user(user_id) or {}
            raw_username = (user_data.get('username') or f'user{user_id}').lower()
            username_slug = re.sub(r"[^a-z0-9._-]", "_", raw_username).strip("_")[:16] or f"user{user_id}"
            # Получаем inbound_id из настроек хоста
            host_data = get_host(host_name)
            inbound_id = host_data.get('host_inbound_id', '1') if host_data else '1'
            base_local = f"{inbound_id}_{username_slug}"
            candidate_local = base_local + "_1"
            attempt = 1
            while True:
                candidate_email = f"{candidate_local}@bot.local"
                if not get_key_by_email(candidate_email):
                    break
                attempt += 1
                candidate_local = f"{inbound_id}_{username_slug}_{attempt}"
                if attempt > 100:
                    candidate_local = f"{inbound_id}_{username_slug}_{int(datetime.now().timestamp())}"
                    candidate_email = f"{candidate_local}@bot.local"
                    break
        else:
            # Продление существующего ключа — достаём email по key_id
            existing_key = get_key_by_id(key_id)
            if not existing_key or not existing_key.get('key_email'):
                await processing_message.edit_text("❌ Не удалось найти ключ для продления.")
                return
            candidate_email = existing_key['key_email']

        result = await xui_api.create_or_update_key_on_host(
            host_name=host_name,
            email=candidate_email,
            days_to_add=int(months * 30),
            sub_token=f"realruvpnbot{user_id}"
        )
        if not result:
            await processing_message.edit_text("❌ Не удалось создать/обновить ключ в панели.")
            return

        if action == "new":
            key_id = add_new_key(
                user_id=user_id,
                host_name=host_name,
                xui_client_uuid=result['client_uuid'],
                key_email=result['email'],
                expiry_timestamp_ms=result['expiry_timestamp_ms']
            )
        elif action == "extend":
            update_key_info(key_id, result['client_uuid'], result['expiry_timestamp_ms'])

            user_data = get_user(user_id)
            referrer_id = user_data.get('referred_by')

            # Начисляем реферальное вознаграждение по покупке — зависит от типа системы
            if referrer_id:
                try:
                    referrer_id = int(referrer_id)
                except Exception:
                    logger.warning(f"Referral: invalid referrer_id={referrer_id} for user {user_id}")
                    referrer_id = None
            if referrer_id:
                # Выбор логики по типу: процент, фикс за покупку; для fixed_start_referrer — вознаграждение по покупке не начисляем
                try:
                    reward_type = (get_setting("referral_reward_type") or "percent_purchase").strip()
                except Exception:
                    reward_type = "percent_purchase"
                reward = Decimal("0")
                if reward_type == "fixed_start_referrer":
                    reward = Decimal("0")
                elif reward_type == "fixed_purchase":
                    try:
                        amount_raw = get_setting("fixed_referral_bonus_amount") or "50"
                        reward = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
                    except Exception:
                        reward = Decimal("50.00")
                else:
                    # percent_purchase (по умолчанию)
                    try:
                        percentage = Decimal(get_setting("referral_percentage") or "0")
                    except Exception:
                        percentage = Decimal("0")
                    reward = (Decimal(str(price)) * percentage / 100).quantize(Decimal("0.01"))
                logger.info(f"Referral: user={user_id}, referrer={referrer_id}, type={reward_type}, reward={float(reward):.2f}")
                if float(reward) > 0:
                    try:
                        ok = add_to_balance(referrer_id, float(reward))
                    except Exception as e:
                        logger.warning(f"Referral: add_to_balance failed for referrer {referrer_id}: {e}")
                        ok = False
                    try:
                        add_to_referral_balance_all(referrer_id, float(reward))
                    except Exception as e:
                        logger.warning(f"Failed to increment referral_balance_all for {referrer_id}: {e}")
                    referrer_username = user_data.get('username', 'пользователь')
                    if ok:
                        try:
                            await bot.send_message(
                                chat_id=referrer_id,
                                text=(
                                    "💰 Вам начислено реферальное вознаграждение!\n"
                                    f"Пользователь: {referrer_username} (ID: {user_id})\n"
                                    f"Сумма: {float(reward):.2f} RUB"
                                )
                            )
                        except Exception as e:
                            logger.warning(f"Could not send referral reward notification to {referrer_id}: {e}")

        update_user_stats(user_id, price, months)
        
        user_info = get_user(user_id)

        log_username = user_info.get('username', 'N/A') if user_info else 'N/A'
        log_status = 'paid'
        log_amount_rub = float(price)
        log_method = metadata.get('payment_method', 'Unknown')
        
        log_metadata = json.dumps({
            "plan_id": metadata.get('plan_id'),
            "plan_name": get_plan_by_id(metadata.get('plan_id')).get('plan_name', 'Unknown') if get_plan_by_id(metadata.get('plan_id')) else 'Unknown',
            "host_name": metadata.get('host_name'),
            "customer_email": metadata.get('customer_email')
        })

        # Определяем payment_id для лога: берём из metadata, если есть (например, при отложенных транзакциях), иначе генерируем новый UUID
        payment_id_for_log = metadata.get('payment_id') or str(uuid.uuid4())

        log_transaction(
            username=log_username,
            transaction_id=None,
            payment_id=payment_id_for_log,
            user_id=user_id,
            status=log_status,
            amount_rub=log_amount_rub,
            amount_currency=None,
            currency_name=None,
            payment_method=log_method,
            metadata=log_metadata
        )
        
        await processing_message.delete()
        
        connection_string = None
        new_expiry_date = None
        try:
            connection_string = result.get('connection_string') if isinstance(result, dict) else None
            new_expiry_date = datetime.fromtimestamp(result['expiry_timestamp_ms'] / 1000) if isinstance(result, dict) and 'expiry_timestamp_ms' in result else None
        except Exception:
            connection_string = None
            new_expiry_date = None
        
        all_user_keys = get_user_keys(user_id)
        key_number = next((i + 1 for i, key in enumerate(all_user_keys) if key['key_id'] == key_id), len(all_user_keys))

        final_text = get_purchase_success_text(
            action="создан" if action == "new" else "продлен",
            key_number=key_number,
            expiry_date=new_expiry_date or datetime.now(),
            connection_string=connection_string or ""
        )
        
        await bot.send_message(
            chat_id=user_id,
            text=final_text,
            reply_markup=keyboards.create_key_info_keyboard(key_id)
        )

        try:
            await notify_admin_of_purchase(bot, metadata)
        except Exception as e:
            logger.warning(f"Failed to notify admin of purchase: {e}")
        
    except Exception as e:
        logger.error(f"Error processing payment for user {user_id} on host {host_name}: {e}", exc_info=True)
        try:
            await processing_message.edit_text("❌ Ошибка при выдаче ключа.")
        except Exception:
            try:
                await bot.send_message(chat_id=user_id, text="❌ Ошибка при выдаче ключа.")
            except Exception:
                pass
