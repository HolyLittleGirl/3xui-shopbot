# WhiteVPN Integration Plan

## 📋 Analysis Summary

### What is WhiteVPN?
WhiteVPN is a system that blocks access to prohibited resources in the Russian Federation using:
- **iptables/ipset** — IP address blocking
- **unbound** — DNS-level domain blocking
- **Automatic updates** — Fetches blocklists from official sources
- **Telegram bot** — Remote management

### Key Files from WhiteVPN:
| File | Purpose |
|------|---------|
| `blocked-ips/block_ips.py` | Downloads and blocks IPs from Roskomnadzor |
| `blocked-domains/block_domains.py` | Downloads and blocks domains |
| `manage.sh` | Console management menu |
| `bot.py` | Telegram bot for remote control |
| `install.sh` | System installation script |

---

## 🎯 Integration Requirements

### 1. **Default State: DISABLED**
- WhiteVPN functionality OFF by default
- No system changes until user enables it
- Setting in database: `whitevpn_enabled = "false"`

### 2. **Control Points**
- ✅ Web Panel: Settings page toggle
- ✅ Bot Admin Menu: "Block prohibited resources" button
- ✅ Status display: Active/Inactive

### 3. **Architecture**
- Run WhiteVPN scripts **inside the container**
- Use Docker capabilities for iptables/unbound
- Store blocklists in container volume
- Manage via existing bot/webhook infrastructure

---

## 📝 Required Changes

### A. Database Settings (database.py)

```python
# New settings in default_settings:
"whitevpn_enabled": "false",           # Enable/disable blocking
"whitevpn_auto_update": "true",         # Auto-update blocklists
"whitevpn_update_interval": "3600",     # Update interval (seconds)
"whitevpn_block_ips": "true",           # Block IPs via iptables
"whitevpn_block_domains": "true",       # Block domains via unbound
```

### B. Web Panel (webhook_server/)

**settings.html:**
```html
<!-- New section in Settings → Panel -->
<div class="mb-3">
    <div class="form-check">
        <input class="form-check-input" type="checkbox" 
               id="whitevpn_enabled" name="whitevpn_enabled">
        <label class="form-check-label" for="whitevpn_enabled">
            🛡️ Блокировка запрещённых ресурсов (РКН)
        </label>
    </div>
    <div class="form-text">
        Автоматическая блокировка доступа к запрещённым ресурсам РФ
    </div>
</div>
```

**app.py:**
```python
# Add to ALL_SETTINGS_KEYS:
"whitevpn_enabled",
"whitevpn_auto_update",
"whitevpn_update_interval",
"whitevpn_block_ips",
"whitevpn_block_domains"

# New route:
@flask_app.route('/whitevpn/toggle', methods=['POST'])
def whitevpn_toggle():
    enabled = request.form.get('whitevpn_enabled') == 'on'
    update_setting('whitevpn_enabled', 'true' if enabled else 'false')
    if enabled:
        asyncio.create_task(run_whitevpn_update())
    return redirect(url_for('settings_page'))
```

### C. Bot Admin Handlers (bot/admin_handlers.py)

```python
@user_router.callback_query(F.data == "admin_whitevpn_toggle")
async def admin_whitevpn_toggle(callback: types.CallbackQuery):
    enabled = get_setting('whitevpn_enabled') == 'true'
    new_state = 'false' if enabled else 'true'
    update_setting('whitevpn_enabled', new_state)
    
    if new_state == 'true':
        asyncio.create_task(run_whitevpn_update())
        await callback.answer("✅ Блокировка включена")
    else:
        await callback.answer("❌ Блокировка выключена")
```

### D. WhiteVPN Module (modules/whitevpn.py) — NEW FILE

```python
"""
WhiteVPN Integration Module
Handles IP and domain blocking from Roskomnadzor lists
"""

import subprocess
import requests
import logging
from shop_bot.data_manager.database import get_setting

logger = logging.getLogger(__name__)

# Blocklist URLs (from whitevpn project)
IP_LIST_URL = "https://reestr.rkn.gov.ru/treats/export"
DOMAIN_LIST_URL = "https://reestr.rkn.gov.ru/treats/export"

IPSET_NAME = "rkn_blocked_ips"

def ensure_ipset():
    """Create ipset if not exists"""
    try:
        subprocess.run(['ipset', 'create', IPSET_NAME, 'hash:net'], 
                      capture_output=True)
    except subprocess.CalledProcessError:
        pass  # Already exists

def update_ip_blocklist():
    """Download and apply IP blocklist"""
    try:
        response = requests.get(IP_LIST_URL, timeout=30)
        ips = response.text.strip().split('\n')
        
        ensure_ipset()
        
        # Flush old rules
        subprocess.run(['ipset', 'flush', IPSET_NAME], capture_output=True)
        
        # Add new IPs
        for ip in ips:
            if ip.strip():
                subprocess.run(['ipset', 'add', IPSET_NAME, ip.strip()], 
                              capture_output=True)
        
        # Apply iptables rule
        subprocess.run(['iptables', '-C', 'OUTPUT', '-m', 'set', 
                       '--match-set', IPSET_NAME, 'dst', '-j', 'DROP'],
                      capture_output=True)
        subprocess.run(['iptables', '-A', 'OUTPUT', '-m', 'set', 
                       '--match-set', IPSET_NAME, 'dst', '-j', 'DROP'],
                      capture_output=True)
        
        logger.info(f"Blocked {len(ips)} IPs")
        return True
    except Exception as e:
        logger.error(f"IP blocklist update failed: {e}")
        return False

def update_domain_blocklist():
    """Download and apply domain blocklist via unbound"""
    # Similar to IP blocking but for DNS
    pass

async def run_whitevpn_update():
    """Main update function"""
    if get_setting('whitevpn_enabled') != 'true':
        return
    
    block_ips = get_setting('whitevpn_block_ips') == 'true'
    block_domains = get_setting('whitevpn_block_domains') == 'true'
    
    if block_ips:
        update_ip_blocklist()
    
    if block_domains:
        update_domain_blocklist()
```

### E. Docker Configuration (docker-compose.yml)

```yaml
services:
  3xui-shopbot:
    # ... existing config ...
    cap_add:
      - NET_ADMIN      # Required for iptables
      - NET_RAW        # Required for network operations
    volumes:
      # ... existing volumes ...
      - whitevpn-blocklists:/app/project/whitevpn  # Blocklist storage

volumes:
  whitevpn-blocklists:
```

### F. Scheduler Integration (data_manager/scheduler.py)

```python
# In periodic_subscription_check or new function:
async def whitevpn_periodic_update():
    """Periodic update of blocklists"""
    while True:
        await asyncio.sleep(3600)  # Every hour
        if get_setting('whitevpn_enabled') == 'true':
            await run_whitevpn_update()
```

---

## 🚀 Implementation Steps

### Phase 1: Core Module (Priority: HIGH)
1. ✅ Create `modules/whitevpn.py`
2. ✅ Add database settings
3. ✅ Add to docker-compose.yml (cap_add, volumes)
4. ✅ Test IP blocking manually

### Phase 2: Web Panel (Priority: HIGH)
1. ✅ Add toggle to settings.html
2. ✅ Add route to app.py
3. ✅ Add to ALL_SETTINGS_KEYS
4. ✅ Test toggle functionality

### Phase 3: Bot Integration (Priority: MEDIUM)
1. ✅ Add admin button "Block prohibited resources"
2. ✅ Add callback handler
3. ✅ Add status display
4. ✅ Test bot commands

### Phase 4: Automation (Priority: LOW)
1. ✅ Add periodic update to scheduler
2. ✅ Add update interval setting
3. ✅ Add logging
4. ✅ Test auto-update

---

## ⚠️ Important Notes

### Docker Limitations:
- **iptables** requires `NET_ADMIN` capability
- **unbound** may need additional configuration
- Test thoroughly in staging before production

### Performance:
- Large IP lists may slow down startup
- Consider caching blocklists
- Update interval should be reasonable (1-4 hours)

### Legal:
- This feature blocks access to resources prohibited in the Russian Federation
- User can enable/disable at any time
- Disabled by default

---

## 📊 Database Schema

```sql
-- New settings in bot_settings table:
INSERT OR REPLACE INTO bot_settings (key, value) VALUES 
  ('whitevpn_enabled', 'false'),
  ('whitevpn_auto_update', 'true'),
  ('whitevpn_update_interval', '3600'),
  ('whitevpn_block_ips', 'true'),
  ('whitevpn_block_domains', 'true');
```

---

## ✅ Success Criteria

- [ ] WhiteVPN disabled by default
- [ ] Can be enabled via web panel
- [ ] Can be enabled via bot admin menu
- [ ] IP blocking works inside container
- [ ] Domain blocking works (if implemented)
- [ ] Auto-update works on schedule
- [ ] Logs show update status
- [ ] Can be disabled at any time

---

**Created:** 2026-04-01  
**Status:** Ready for implementation
