# AI Assistant for STE Slicer

Конкурсный плагин для STE Slicer, который добавляет чат с LLM и позволяет
автоматизировать подготовку 3D/5D-печати: выбрать режим, профиль, материал и
параметры, подготовить сцену, запустить слайсинг и получить G-code и проект 3MF.

Целевая среда — STE Slicer 7.10.12 для Windows, plugin API 5 и встроенный
Python 3.5 с PyQt5. Основной профиль для проверки — Stereotech Hybrid 530 V5.2.4.

## Возможности

- немодальный QML-чат внутри STE Slicer;
- OpenAI-совместимый Chat Completions API с tool calling;
- ввод URL, API-ключа и модели через настройки плагина;
- вложения STL, OBJ, 3MF, JPG и PNG;
- режимы `classic`, `cylindrical` и `cylindrical_full`;
- чтение и изменение разрешённых настроек;
- выбор профилей качества и материалов;
- загрузка, перемещение, поворот, масштабирование и расстановка моделей;
- запуск слайсинга, чтение статуса, времени и расхода материала;
- экспорт G-code и проекта 3MF;
- локальный MCP JSON-RPC endpoint с `tools/list` и `tools/call`.

Исходники плагина находятся в [`ste_plugin/AIAssistant`](ste_plugin/AIAssistant).
Полная инструкция по установке, настройке LLM и первичной проверке находится в
[`ste_plugin/AIAssistant/README.md`](ste_plugin/AIAssistant/README.md).

## Сборка

Для разработки требуются [uv](https://docs.astral.sh/uv/) и Python 3.12+.
Сам плагин не требует установки внешних Python-пакетов в STE Slicer.

```sh
uv sync --locked
uv run python scripts/build_ste_plugin.py
```

Готовый архив появится в `dist/AIAssistant-ste-plugin.zip`. Внутри него находится
папка `AIAssistant`, которую нужно установить в каталог `plugins` STE Slicer.
Сборщик дополнительно проверяет каждый Python-файл плагина на совместимость с
синтаксисом Python 3.5.

## Проверки

```sh
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run python scripts/build_ste_plugin.py
uv run python -m zipfile -t dist/AIAssistant-ste-plugin.zip
```

Автономные тесты проверяют Python 3.5-синтаксис, MCP JSON-RPC, loopback HTTP и
ключевые свойства встроенного QML-интерфейса. Полную интеграцию со сценой,
профилями, 3D/5D-бэкендами и экспортом необходимо проверять в Windows-сборке
STE Slicer организаторов с активированной 5D-лицензией.

## Структура

```text
ste_plugin/AIAssistant/
├── plugin.json                 метаданные плагина STE, API 5
├── AIAssistant.py              регистрация меню и окна
├── AssistantController.py      состояние UI и цикл агента
├── Assistant.qml               встроенный чат и настройки
├── core/
│   ├── llm.py                  OpenAI-совместимый клиент и tool loop
│   ├── jsonrpc.py              MCP JSON-RPC dispatcher
│   ├── mcp_server.py           локальный HTTP endpoint
│   └── tool_registry.py        единый реестр инструментов
└── ste/
    ├── adapter.py              доступ к API STE Slicer
    ├── qt_executor.py          выполнение действий в главном Qt-потоке
    └── tools.py                схемы инструментов агента и MCP
```

Материалы задания сохранены в `3D5D_печать/`.
