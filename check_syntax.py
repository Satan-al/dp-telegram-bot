"""
Проверка синтаксиса и импортов bot.py
"""
import sys

print("🔍 Проверка синтаксиса bot.py...")

try:
    # Пробуем импортировать модуль
    import ast
    
    with open('bot.py', 'r', encoding='utf-8') as f:
        code = f.read()
    
    # Парсим код
    ast.parse(code)
    print("✅ Синтаксис корректен!")
    
    # Проверяем основные функции
    tree = ast.parse(code)
    functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    
    required_functions = [
        'main',
        'start_firebase_listener',
        'process_firebase_messages',
        'firebase_callback',
        'start_command',
        'handle_message'
    ]
    
    print("\n📋 Найденные функции:")
    for func in required_functions:
        if func in functions:
            print(f"  ✅ {func}")
        else:
            print(f"  ❌ {func} - НЕ НАЙДЕНА!")
    
    # Проверяем импорты
    print("\n📦 Проверка импортов...")
    try:
        import telegram
        print("  ✅ telegram")
    except ImportError:
        print("  ❌ telegram - установи: pip install python-telegram-bot")
    
    try:
        import firebase_admin
        print("  ✅ firebase_admin")
    except ImportError:
        print("  ❌ firebase_admin - установи: pip install firebase-admin")
    
    try:
        from dotenv import load_dotenv
        print("  ✅ dotenv")
    except ImportError:
        print("  ❌ dotenv - установи: pip install python-dotenv")
    
    print("\n✅ Проверка завершена!")
    
except SyntaxError as e:
    print(f"❌ Синтаксическая ошибка: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Ошибка: {e}")
    sys.exit(1)
