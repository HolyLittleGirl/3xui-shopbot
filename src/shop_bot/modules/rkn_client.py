"""
RKN Blocker Client
HTTP клиент для управления RKN блокировщиком на хосте.

Usage:
    from shop_bot.modules.rkn_client import get_client
    
    client = get_client()
    status = client.get_status()
    client.enable()
    client.disable()
"""

import requests
import logging
from typing import Optional, Dict, Any
from shop_bot.data_manager.database import get_setting, update_setting

logger = logging.getLogger(__name__)

# Конфигурация
DEFAULT_API_URL = "http://host.docker.internal:8765"
DEFAULT_TIMEOUT = 120  # 2 минуты для загрузки 43,000 доменов


class RKNClient:
    """Клиент для управления RKN блокировщиком через HTTP API."""
    
    def __init__(self, api_url: Optional[str] = None, token: Optional[str] = None):
        self.api_url = api_url or get_setting("rkn_api_url") or DEFAULT_API_URL
        self.token = token or get_setting("rkn_api_token")
        self.timeout = DEFAULT_TIMEOUT
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-RKN-Token": self.token,
            "Content-Type": "application/json"
        }
    
    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.api_url}{endpoint}"
        
        try:
            if method == "GET":
                response = requests.get(url, headers=self._get_headers(), timeout=self.timeout)
            else:
                response = requests.post(url, headers=self._get_headers(), json=data or {}, timeout=self.timeout)
            
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.ConnectionError:
            logger.error(f"Не удалось подключиться к RKN API: {url}")
            return {"success": False, "error": "API unavailable", "api_offline": True}
        except requests.exceptions.Timeout:
            logger.error(f"Таймаут RKN API: {url}")
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            logger.error(f"Ошибка RKN API: {e}")
            return {"success": False, "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        result = self._request("GET", "/status")
        if "enabled" in result:
            update_setting("rkn_enabled", "true" if result["enabled"] else "false")
        return result
    
    def enable(self) -> Dict[str, Any]:
        logger.info("Включение RKN блокировки...")
        result = self._request("POST", "/enable", {})
        if result.get("success"):
            update_setting("rkn_enabled", "true")
            logger.info(f"RKN блокировка включена. Заблокировано IP: {result.get('blocked_count', 0)}")
        return result
    
    def disable(self) -> Dict[str, Any]:
        logger.info("Выключение RKN блокировки...")
        result = self._request("POST", "/disable", {})
        if result.get("success"):
            update_setting("rkn_enabled", "false")
            logger.info("RKN блокировка выключена")
        return result
    
    def toggle(self) -> Dict[str, Any]:
        current = self.get_status()
        if current.get("enabled"):
            result = self.disable()
            result["action"] = "disabled"
        else:
            result = self.enable()
            result["action"] = "enabled"
        return result
    
    def update(self) -> Dict[str, Any]:
        logger.info("Обновление RKN списков...")
        result = self._request("POST", "/update", {})
        if result.get("success"):
            logger.info(f"RKN списки обновлены. Заблокировано IP: {result.get('blocked_count', 0)}")
        return result


_client: Optional[RKNClient] = None

def get_client() -> RKNClient:
    global _client
    if _client is None:
        _client = RKNClient()
    return _client

def get_status() -> Dict[str, Any]:
    return get_client().get_status()

def enable() -> Dict[str, Any]:
    return get_client().enable()

def disable() -> Dict[str, Any]:
    return get_client().disable()

def toggle() -> Dict[str, Any]:
    return get_client().toggle()

def update() -> Dict[str, Any]:
    return get_client().update()


def is_available() -> bool:
    """Проверить доступность API."""
    return get_client().is_available()
