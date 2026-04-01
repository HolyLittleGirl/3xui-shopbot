"""
RKN Blocker Client
Вызывает RKN блокировщик через subprocess (работает на хосте).

Usage:
    from shop_bot.modules.rkn_client import get_client
    
    client = get_client()
    status = client.get_status()
    client.enable()
    client.disable()
"""

import subprocess
import logging
import json
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Путь к RKN скриптам (смонтированы из хоста)
RKN_SCRIPT = "/host-rkn-blocker/block_ips.py"


class RKNClient:
    """Клиент для управления RKN блокировщиком через subprocess."""
    
    def __init__(self):
        pass
    
    def _run_command(self, action: str) -> Dict[str, Any]:
        """Выполнить команду RKN блокировщика."""
        try:
            cmd = ['python3', RKN_SCRIPT, action, '--json']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                return json.loads(result.stdout.strip())
            else:
                logger.error(f"RKN {action} failed: {result.stderr}")
                return {"success": False, "error": result.stderr or "Command failed"}
        
        except subprocess.TimeoutExpired:
            logger.error(f"RKN {action} timeout")
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            logger.error(f"RKN {action} error: {e}")
            return {"success": False, "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """Получить статус блокировщика."""
        result = self._run_command('status')
        
        # Сохраняем статус в БД для быстрого доступа
        if "enabled" in result:
            from shop_bot.data_manager.database import update_setting
            update_setting("rkn_enabled", "true" if result["enabled"] else "false")
        
        return result
    
    def enable(self) -> Dict[str, Any]:
        """Включить блокировку."""
        logger.info("Включение RKN блокировки...")
        
        result = self._run_command('enable')
        
        if result.get("success"):
            from shop_bot.data_manager.database import update_setting
            update_setting("rkn_enabled", "true")
            logger.info(f"RKN блокировка включена. Заблокировано IP: {result.get('blocked_count', 0)}")
        else:
            logger.error(f"Ошибка включения RKN блокировки: {result}")
        
        return result
    
    def disable(self) -> Dict[str, Any]:
        """Выключить блокировку."""
        logger.info("Выключение RKN блокировки...")
        
        result = self._run_command('disable')
        
        if result.get("success"):
            from shop_bot.data_manager.database import update_setting
            update_setting("rkn_enabled", "false")
            logger.info("RKN блокировка выключена")
        else:
            logger.error(f"Ошибка выключения RKN блокировки: {result}")
        
        return result
    
    def toggle(self) -> Dict[str, Any]:
        """Переключить состояние блокировки."""
        current = self.get_status()
        
        if current.get("enabled"):
            result = self.disable()
            result["action"] = "disabled"
        else:
            result = self.enable()
            result["action"] = "enabled"
        
        return result
    
    def update(self) -> Dict[str, Any]:
        """Обновить списки блокировки."""
        logger.info("Обновление RKN списков...")
        
        result = self._run_command('update')
        
        if result.get("success"):
            logger.info(f"RKN списки обновлены. Заблокировано IP: {result.get('blocked_count', 0)}")
        else:
            logger.error(f"Ошибка обновления RKN списков: {result}")
        
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
