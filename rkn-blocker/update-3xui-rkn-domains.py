#!/usr/bin/env python3
"""
Добавляет/удаляет RKN блокировку доменов в конфиге 3x-ui через базу данных

Автоматически скачивает список доменов из GitHub (1andrevich/Re-filter-lists)
и добавляет правила в 3x-ui routing.

Usage:
    python3 update-3xui-rkn-domains.py enable   - Добавить RKN правила
    python3 update-3xui-rkn-domains.py disable  - Удалить RKN правила
"""
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
import gzip
import io

DB_PATH = '/etc/x-ui/x-ui.db'
DOMAINS_URL = 'https://github.com/1andrevich/Re-filter-lists/releases/download/13062025/ruleset-domain-refilter_domains.json'
MAX_DOMAINS_PER_RULE = 1000  # Лимит Xray


def download_domains():
    """Скачать список доменов из GitHub"""
    print(f"Downloading domains from {DOMAINS_URL}...")
    
    try:
        # Скачиваем файл
        req = urllib.request.Request(
            DOMAINS_URL,
            headers={'Accept-Encoding': 'gzip'}
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
        
        # Распаковываем если gzip
        if response.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        
        # Парсим JSON
        json_data = json.loads(data.decode('utf-8'))
        
        # Извлекаем домены
        domains = []
        for rule in json_data.get('rules', []):
            for domain in rule.get('domain', []):
                # Убираем префиксы если есть
                clean_domain = domain
                if domain.startswith('domain:'):
                    clean_domain = domain[7:]
                elif domain.startswith('regexp:'):
                    continue  # Пропускаем regexp
                
                if clean_domain and clean_domain not in domains:
                    domains.append(clean_domain)
        
        print(f"Downloaded {len(domains)} domains")
        return domains
    
    except Exception as e:
        print(f"Error downloading domains: {e}")
        return []


def split_domains(domains, max_per_rule=MAX_DOMAINS_PER_RULE):
    """Разбить домены на чанки"""
    chunks = []
    for i in range(0, len(domains), max_per_rule):
        chunks.append(domains[i:i + max_per_rule])
    return chunks


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-domains.py [enable|disable]")
        return 1
    
    action = sys.argv[1]
    
    # Читаем конфиг из базы
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        print("xrayTemplateConfig not found in database")
        return 1
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        print(f"Error parsing config: {e}")
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    if action == 'enable':
        # Скачиваем домены
        domains = download_domains()
        if not domains:
            print("No domains downloaded")
            return 1
        
        # Разбиваем на чанки
        chunks = split_domains(domains)
        print(f"Split into {len(chunks)} rules ({MAX_DOMAINS_PER_RULE} domains each)")
        
        # Сначала удаляем старые RKN правила
        new_rules = []
        old_rkn_count = 0
        for rule in rules:
            domain_list = rule.get('domain', [])
            # Проверяем не RKN ли это правило (первый домен facebook.com или много доменов)
            is_rkn = ('facebook.com' in domain_list) or (len(domain_list) > 10 and rule.get('outboundTag') == 'blocked')
            if is_rkn:
                old_rkn_count += 1
                continue
            new_rules.append(rule)
        rules = new_rules
        print(f"Removed {old_rkn_count} old RKN rules")
        
        # Находим позицию для вставки (ПЕРЕД outbound правилами)
        insert_index = 0
        for i, rule in enumerate(rules):
            if 'inboundTag' not in rule and 'outboundTag' in rule:
                insert_index = i
                break
        print(f"Insert at index {insert_index}")
        
        # Добавляем правила
        for i, chunk in enumerate(chunks):
            rkn_rule = {
                'type': 'field',
                'domain': chunk,
                'network': 'TCP,UDP',
                'outboundTag': 'blocked'
            }
            rules.insert(insert_index + i, rkn_rule)
            print(f"Added RKN rule {i+1}/{len(chunks)} with {len(chunk)} domains")
        
        print(f"Total: {len(chunks)} RKN rules, {len(domains)} domains")
    
    elif action == 'disable':
        # Удаляем ВСЕ RKN правила
        original_count = len(rules)
        new_rules = []
        removed_count = 0
        for rule in rules:
            domain_list = rule.get('domain', [])
            # Проверяем не RKN ли это правило
            is_rkn = ('facebook.com' in domain_list) or (len(domain_list) > 10 and rule.get('outboundTag') == 'blocked')
            if is_rkn:
                removed_count += 1
                continue
            new_rules.append(rule)
        rules = new_rules
        print(f"Removed {removed_count} RKN domain rules")
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем в базу
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        print("Config saved to database")
        
    except Exception as e:
        print(f"Error saving config: {e}")
        return 1
    finally:
        conn.close()
    
    # Перезагружаем x-ui (stop → start)
    try:
        print(f"Stopping x-ui...")
        subprocess.run(['systemctl', 'stop', 'x-ui'], check=True, timeout=30)
        print(f"x-ui stopped")
        time.sleep(2)
        print(f"Starting x-ui...")
        subprocess.run(['systemctl', 'start', 'x-ui'], check=True, timeout=30)
        print(f"x-ui started")
        
    except Exception as e:
        print(f"Error restarting x-ui: {e}")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
