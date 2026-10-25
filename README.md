# Slicer MCP — AI Assistant для STE Slicer

Плагин встраивает чат с OpenAI-совместимой LLM и локальный MCP-сервер в STE Slicer 7.10. Ассистент читает реальное состояние слайсера, выбирает доступные профили и материал, меняет разрешённые параметры, готовит сцену для 3D/5D, запускает слайсинг и сохраняет проверенные G-code и 3MF.

## Что покрывает техническое задание

- встроенное окно чата с прикреплением STL/OBJ/3MF и изображений;
- 44 MCP-инструмента для сцены, профилей, параметров, слайсинга, рисков, экономики, TopOpt и парка принтеров;
- классическая 3D-ориентация по фактической сетке;
- отдельная подготовка 5D: ось исходного STL переводится в ось сцены STE, деталь центрируется относительно оси вращения и ставится на поверхность;
- автоматический план параметров для быстрого прототипа или производства с прозрачными причинами и отдельной командой применения;
- рейтинг рисков 0–100, карта первого слоя/свесов и сценарии стоимости брака;
- экспорт G-code с проверкой команд по режиму `classic`/`cylindrical_full`;
- стандартный 3MF с геометрией и метаданными: ZIP, XML, индексы треугольников и build-ссылки проверяются после записи;
- безопасная интеграция OctoPrint/Moonraker: загрузка и старт разделены, принтер всегда выбирается явно;
- экспериментальный Quick TopOpt-кандидат. Это геометрическая 2D SIMP-заготовка, а не расчёт прочности и не замена Ansys/инженерной валидации.

Подтверждённый Windows-прогон из предыдущей версии сохранён в `results/01-gear`: реальный слайсинг classic занял 22,45 с, оценка печати — 43:25, PLA — 3,908 г. Новые изменения 5D и парка принтеров требуют повторной проверки на Windows STE/оборудовании.

## Установка на Windows

1. Установите STE Slicer 7.10 и хотя бы один профиль принтера.
2. Закройте STE Slicer.
3. Распакуйте `dist/AIAssistantTeam-ste-plugin.zip` в:

   ```text
   %APPDATA%\steslicer\7.10\plugins\
   ```

   Должен получиться файл:

   ```text
   %APPDATA%\steslicer\7.10\plugins\AIAssistantTeam\plugin.json
   ```

4. Удалите старую папку `AIAssistant`, если она была установлена отдельно.
5. Запустите STE Slicer и откройте **Extensions → AI Assistant (команда) → Открыть чат**.
6. Во вкладке настроек задайте URL OpenAI-совместимого API, ключ и имя модели. Ключ хранится в настройках STE; в Git его нет.

Для анализа фото нужна мультимодальная модель, принимающая `image_url`. Если провайдер отвергнет изображение, чат должен сообщить, что фото не проанализировано.

## Сквозной прогон 3D/5D

Во вкладке «Настройки» чат показывает локальные MCP port/token текущего сеанса. Затем в PowerShell:

```powershell
$env:STE_MCP_TOKEN = '<временный токен из STE>'
python .\scripts\pipeline_runner.py `
  --url http://127.0.0.1:<PORT>/mcp `
  --config .\scripts\pipeline_models.example.json `
  --results .\results `
  --slice-timeout 600 `
  --http-timeout 120 `
  --overwrite
```

Раннер проверяет эффективный режим, материал, качество и overrides; ждёт стабильных снимков конфигурации; вызывает `auto_orient_object` для classic или `prepare_scene_5d` для 5D; отслеживает одно поколение задания сквозь переходы backend; сохраняет и повторно проверяет артефакты.

Для каждой 5D-модели явно указывайте исходную геометрическую ось в `five_d.source_axis`. Имя файла и похожие радиальные размеры служат только подсказкой. Пример:

```json
{
  "mode": "cylindrical_full",
  "five_d": {
    "source_axis": "Y",
    "target_scene_axis": "Y",
    "center_on_rotary_axis": true,
    "place_on_surface": true,
    "center_tolerance_mm": 0.05
  },
  "slice_timeout": 600,
  "stall_timeout": 120
}
```

## Парк принтеров

Задайте `STE_PRINTER_FLEET_JSON` в среде, из которой запускается STE Slicer. Можно передать JSON напрямую или путь к JSON-файлу:

```json
{
  "printers": [
    {"id":"lab-01","name":"Lab OctoPrint","backend":"octoprint","url":"http://192.168.1.20","token":"..."},
    {"id":"lab-02","name":"Lab Klipper","backend":"moonraker","url":"http://192.168.1.21","token":"..."}
  ]
}
```

Не добавляйте этот файл в Git. Доступны `list_printers`, `get_printer_status`, `get_printer_temperatures`, `upload_gcode`, `start_print`, `pause_print`, `resume_print`, `cancel_print`, `plan_fleet_schedule`. `upload_gcode` сначала локально валидирует файл. Печать не стартует автоматически после загрузки.

## Quick TopOpt · experimental

Для локального bridge нужен внешний Python и NumPy:

```powershell
py -3.12 -m venv "$env:LOCALAPPDATA\SlicerMCP\TopOptBridge\.venv"
& "$env:LOCALAPPDATA\SlicerMCP\TopOptBridge\.venv\Scripts\python.exe" -m pip install "numpy>=2,<3"
```

Плагин автоматически создаёт bridge token. Процесс на порту 8765 останавливается только по `Popen`-дескриптору, созданному самим плагином; PID из discovery-файла не используется для принудительного завершения постороннего процесса.

## Разработка и проверка

```bash
python -m pytest -q
python -m compileall -q AIAssistantTeam scripts
python scripts/build_ste_plugin.py
```

Сборщик проверяет синтаксис всех Python-файлов с `feature_version=(3, 5)`, исключает кэш и формирует детерминированный ZIP с SHA-256.

## Границы подтверждения

- Локально проверяются чистые алгоритмы, схемы MCP, валидаторы G-code/3MF, конфигурация раннера, планировщик парка и Python 3.5 syntax.
- Windows STE 7.10 нужен для подтверждения Cura/STE API, фактического 5D Glicer и восстановления проекта в интерфейсе.
- OctoPrint/Moonraker и физический принтер нужны для подтверждения сетевых команд и печати.
- Quick TopOpt не выдаёт напряжения, коэффициент запаса или инженерную пригодность. Для Ansys требуется отдельная лицензированная среда, нагрузки, закрепления и последующая валидация.
