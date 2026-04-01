"""
RKN Blocker Client
HTTP клиент для управления RKN блокировщиком из 3xui-shopbot

Usage:
    from shop_bot.modules.rkn_client import RKNClient
    
    client = RKNClient()
    status = client.get_status()
    client.enable()
    client.disable()
"""

import requests
import logging
from typing import Optional, Dict, Any
from shop_bot.data_manager.database import get_setting, update_setting

logger = logging.getLogger(__name__)

# Конфигурация по умолчанию
# host.docker.internal — это special DNS для доступа из контейнера к хосту
DEFAULT_API_URL = "http://host.docker.internal:8765"
DEFAULT_TIMEOUT = 10


class RKNClient:
    """Клиент для управления RKN блокировщиком."""
    
    def __init__(self, api_url: Optional[str] = None, token: Optional[str] = None):
        """
        Инициализация клиента.
        
        Args:
            api_url: URL RKN API сервера
            token: API токен для авторизации
        """
        self.api_url = api_url or get_setting("rkn_api_url") or DEFAULT_API_URL
        self.token = token or get_setting("rkn_api_token")
        self.timeout = DEFAULT_TIMEOUT
    
    def _get_headers(self) -> Dict[str, str]:
        """Получить заголовки для запросов."""
        return {
            "X-RKN-Token": self.token,
            "Content-Type": "application/json"
        }
    
    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """
        Выполнить HTTP запрос к API.
        
        Returns:
            dict с результатом или None при ошибке
        """
        url = f"{self.api_url}{endpoint}"
        
        try:
            if method == "GET":
                response = requests.get(url, headers=self._get_headers(), timeout=self.timeout)
            else:
                response = requests.post(
                    url,
                    headers=self._get_headers(),
                    json=data or {"token": self.token},
                    timeout=self.timeout
                )
            
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.ConnectionError:
            logger.error(f"Не удалось подключиться к RKN API: {url}")
            return {"success": False, "error": "API unavailable", "api_offline": True}
        
        except requests.exceptions.Timeout:
            logger.error(f"Таймаут запроса к RKN API: {url}")
            return {"success": False, "error": "Request timeout"}
        
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("Неверный API токен RKN")
                return {"success": False, "error": "Unauthorized", "invalid_token": True}
            logger.error(f"HTTP ошибка RKN API: {e}")
            return {"success": False, "error": str(e)}
        
        except Exception as e:
            logger.error(f"Неожиданная ошибка RKN API: {e}")
            return {"success": False, "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """
        Получить статус блокировщика.
        
        Returns:
            dict с полями:
                - enabled: bool
                - blocked_count: int
                - last_update: str
                - error: str (если есть)
        """
        result = self._request("GET", "/status")
        
        if result is None:
            return {
                "enabled": False,
                "blocked_count": 0,
                "last_update": None,
                "error": "API unavailable"
            }
        
        # Сохраняем статус в БД для быстрого доступа
        if "enabled" in result:
            update_setting("rkn_enabled", "true" if result["enabled"] else "false")
        
        return result
    
    def enable(self) -> Dict[str, Any]:
        """
        Включить блокировку.
        
        Returns:
            dict с результатом операции
        """
        logger.info("Включение RKN блокировки...")
        
        result = self._request("POST", "/enable")
        
        if result and result.get("success"):
            update_setting("rkn_enabled", "true")
            logger.info(f"RKN блокировка включена. Заблокировано IP: {result.get('blocked_count', 0)}")
        else:
            logger.error(f"Ошибка включения RKN блокировки: {result}")
        
        return result or {"success": False, "error": "API unavailable"}
    
    def disable(self) -> Dict[str, Any]:
        """
        Выключить блокировку.
        
        Returns:
            dict с результатом операции
        """
        logger.info("Выключение RKN блокировки...")
        
        result = self._request("POST", "/disable")
        
        if result and result.get("success"):
            update_setting("rkn_enabled", "false")
            logger.info("RKN блокировка выключена")
        else:
            logger.error(f"Ошибка выключения RKN блокировки: {result}")
        
        return result or {"success": False, "error": "API unavailable"}
    
    def toggle(self) -> Dict[str, Any]:
        """
        Переключить состояние блокировки.
        
        Returns:
            dict с результатом и новым состоянием
        """
        current = self.get_status()
        
        if current.get("enabled"):
            result = self.disable()
            result["action"] = "disabled"
        else:
            result = self.enable()
            result["action"] = "enabled"
        
        return result
    
    def update(self) -> Dict[str, Any]:
        """
        Обновить списки блокировки.
        
        Returns:
            dict с результатом операции
        """
        logger.info("Обновление RKN списков...")
        
        result = self._request("POST", "/update")
        
        if result and result.get("success"):
            logger.info(f"RKN списки обновлены. Заблокировано IP: {result.get('blocked_count', 0)}")
        else:
            logger.error(f"Ошибка обновления RKN списков: {result}")
        
        return result or {"success": False, "error": "API unavailable"}
    
    def is_available(self) -> bool:
        """
        Проверить доступность API сервера.
        
        Returns:
            True если API доступен
        """
        try:
            response = requests.get(f"{self.api_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Тестировать соединение с API.
        
        Returns:
            dict с информацией о соединении
        """
        result = {
            "available": False,
            "api_url": self.api_url,
            "token_set": bool(self.token),
            "error": None
        }
        
        # Проверка health endpoint
        try:
            response = requests.get(f"{self.api_url}/health", timeout=5)
            if response.status_code == 200:
                result["available"] = True
                
                # Проверка авторизации
                status = self.get_status()
                result["authorized"] = not status.get("error")
                
        except Exception as e:
            result["error"] = str(e)
        
        return result


# Singleton instance
_client: Optional[RKNClient] = None


def get_client() -> RKNClient:
    """Получить singleton экземпляр клиента."""
    global _client
    if _client is None:
        _client = RKNClient()
    return _client


# Convenience функции для прямого импорта
def get_status() -> Dict[str, Any]:
    """Получить статус блокировщика."""
    return get_client().get_status()


def enable() -> Dict[str, Any]:
    """Включить блокировку."""
    return get_client().enable()


def disable() -> Dict[str, Any]:
    """Выключить блокировку."""
    return get_client().disable()


def toggle() -> Dict[str, Any]:
    """Переключить состояние."""
    return get_client().toggle()


def update() -> Dict[str, Any]:
    """Обновить списки."""
    return get_client().update()


def is_available() -> bool:
    """Проверить доступность API."""
    return get_client().is_available()
