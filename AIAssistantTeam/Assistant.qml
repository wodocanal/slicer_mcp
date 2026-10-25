import QtQuick 2.2
import QtQuick.Controls 1.3
import QtQuick.Dialogs 1.1
import QtQuick.Layouts 1.1
import QtQuick.Window 2.1

import UM 1.1 as UM

UM.Dialog {
    id: root
    title: "AI Assistant (команда) — подготовка 3D/5D-печати"
    width: 560 * screenScaleFactor
    height: 720 * screenScaleFactor
    minimumWidth: 440 * screenScaleFactor
    minimumHeight: 520 * screenScaleFactor
    modality: Qt.NonModal

    property bool settingsVisible: false
    property bool economicsVisible: false
    property bool optimizationVisible: false
    property bool manualOptimizationSettings: false
    property var chatMessages: []
    property var economics: ({})
    property var optimization: ({})

    function refreshMessages() {
        try {
            chatMessages = JSON.parse(manager.messagesJson)
            messages.positionViewAtEnd()
        } catch (error) {
            chatMessages = []
        }
    }

    function showSettings() {
        settingsVisible = true
        economicsVisible = false
        optimizationVisible = false
    }

    function refreshEconomicsData() {
        try {
            economics = JSON.parse(manager.economicsJson)
        } catch (error) {
            economics = ({error: "Не удалось прочитать данные сметы"})
        }
    }

    function refreshOptimizationData() {
        try {
            optimization = JSON.parse(manager.optimizationJson)
        } catch (error) {
            optimization = ({error: "Не удалось прочитать состояние интеграции"})
        }
    }

    function optimizationTemplateSummary() {
        var values = root.optimization.templates || []
        var names = []
        for (var i = 0; i < values.length; ++i) {
            names.push(values[i].name || values[i].id)
        }
        return names.length ? names.join(", ") : "нет"
    }

    function asNumber(text) {
        var value = String(text).trim().replace(",", ".")
        if (!value.length) return null
        var number = Number(value)
        return isNaN(number) ? value : number
    }

    function costArguments() {
        var prices = []
        for (var i = 0; i < materialRates.count; ++i) {
            prices.push(asNumber(materialRates.itemAt(i).rateText))
        }
        var work = []
        if (outsideLabel.text.trim().length || outsideAmount.text.trim().length) {
            work.push({label: outsideLabel.text, amount_rub: asNumber(outsideAmount.text)})
        }
        var markup = asNumber(markupAmount.text)
        var tax = asNumber(taxAmount.text)
        return {
            machine_hour_rate_rub: asNumber(machineRate.text),
            material_prices_rub_per_kg: prices,
            outside_work_items: work,
            markup_rub: markup === null ? 0 : markup,
            tax_rub: tax === null ? 0 : tax,
            target_budget_rub: asNumber(targetBudget.text)
        }
    }

    function costSummary(quote) {
        if (!quote) return "После завершения слайсинга введите тарифы и нажмите «Рассчитать»."
        var lines = [
            "Время: " + quote.print_time_seconds + " с",
            "Материалы: " + quote.material_cost_rub.toFixed(2) + " ₽",
            "Станок: " + quote.machine_cost_rub.toFixed(2) + " ₽",
            "Стоимость печати: " + quote.print_cost_rub.toFixed(2) + " ₽",
            "Работы вне печати: " + quote.outside_work_cost_rub.toFixed(2) + " ₽",
            "Надбавка: " + quote.markup_rub.toFixed(2) + " ₽; налоги: " + quote.tax_rub.toFixed(2) + " ₽",
            "Цена заказчику: " + quote.customer_price_rub.toFixed(2) + " ₽"
        ]
        if (quote.target_budget_rub !== undefined) {
            lines.push("Бюджет " + quote.target_budget_rub.toFixed(2) + " ₽: " +
                       (quote.budget_met ? "достигнут" : "превышен"))
        }
        return lines.join("\n")
    }

    function comparisonSummary(comparison) {
        if (!comparison) return "Сохраните исходный и изменённый варианты после отдельных завершённых расчётов."
        var change = comparison.changes
        var lines = [
            "Цена заказчику: " + (change.customer_price_rub >= 0 ? "+" : "") +
                change.customer_price_rub.toFixed(2) + " ₽",
            "Стоимость печати: " + (change.print_cost_rub >= 0 ? "+" : "") +
                change.print_cost_rub.toFixed(2) + " ₽",
            "Время: " + (change.print_time_seconds >= 0 ? "+" : "") +
                change.print_time_seconds + " с",
            "Тарифы одинаковы: " + (comparison.same_tariffs ? "да" : "нет"),
            "Объекты сцены одинаковы: " + (comparison.same_scene_objects ? "да" : "нет")
        ]
        var before = comparison.baseline.settings
        var after = comparison.candidate.settings
        var changed = []
        for (var key in before) {
            if (before[key] !== after[key]) changed.push(key + ": " + before[key] + " → " + after[key])
        }
        lines.push("Изменённые настройки: " + (changed.length ? changed.join("; ") : "нет в отслеживаемом наборе"))
        return lines.join("\n")
    }

    Component.onCompleted: {
        refreshMessages()
        refreshEconomicsData()
        refreshOptimizationData()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: UM.Theme.getSize("default_margin").height

        Connections {
            target: manager
            onMessagesChanged: root.refreshMessages()
            onEconomicsChanged: root.refreshEconomicsData()
            onOptimizationChanged: root.refreshOptimizationData()
        }

        FileDialog {
            id: attachmentDialog
            title: "Добавить модель или изображение"
            selectExisting: true
            selectMultiple: true
            nameFilters: [
                "Поддерживаемые файлы (*.stl *.obj *.3mf *.jpg *.jpeg *.png)",
                "3D-модели (*.stl *.obj *.3mf)",
                "Изображения (*.jpg *.jpeg *.png)"
            ]
            onAccepted: {
                var selected = []
                if (fileUrls) {
                    for (var i = 0; i < fileUrls.length; ++i) {
                        selected.push(fileUrls[i].toString())
                    }
                }
                if (selected.length === 0 && fileUrl) {
                    selected.push(fileUrl.toString())
                }
                manager.setAttachmentsJson(JSON.stringify(selected))
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8 * screenScaleFactor

            Button {
                text: "💬 Чат"
                checkable: true
                checked: !root.settingsVisible && !root.economicsVisible &&
                         !root.optimizationVisible
                onClicked: { root.settingsVisible = false; root.economicsVisible = false;
                             root.optimizationVisible = false }
            }
            Button {
                text: "📊 Смета"
                checkable: true
                checked: root.economicsVisible
                onClicked: {
                    root.settingsVisible = false
                    root.economicsVisible = true
                    root.optimizationVisible = false
                    manager.refreshEconomics()
                }
            }
            Button {
                text: "⚡ Оптимизация (TopOpt)"
                checkable: true
                checked: root.optimizationVisible
                onClicked: {
                    root.settingsVisible = false
                    root.economicsVisible = false
                    root.optimizationVisible = true
                    manager.refreshOptimizationStatus()
                }
            }
            Button {
                text: "⚙ Настройки"
                checkable: true
                checked: root.settingsVisible
                onClicked: root.showSettings()
            }
            Item { Layout.fillWidth: true }

            Rectangle {
                Layout.preferredHeight: 26 * screenScaleFactor
                Layout.preferredWidth: statusRow.implicitWidth + 16 * screenScaleFactor
                radius: 13 * screenScaleFactor
                color: manager.busy ? "#fffaf0" : "#f0fff4"
                border.color: manager.busy ? "#fbd38d" : "#9ae6b4"
                border.width: 1

                RowLayout {
                    id: statusRow
                    anchors.centerIn: parent
                    spacing: 5 * screenScaleFactor

                    Rectangle {
                        width: 8 * screenScaleFactor
                        height: 8 * screenScaleFactor
                        radius: 4 * screenScaleFactor
                        color: manager.busy ? "#dd6b20" : "#38a169"
                    }

                    Text {
                        text: manager.busy ? "Агент работает…" : "Готов к работе"
                        font.pixelSize: 11 * screenScaleFactor
                        font.bold: true
                        color: manager.busy ? "#c05621" : "#276749"
                    }
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                visible: !root.settingsVisible && !root.economicsVisible &&
                         !root.optimizationVisible
                spacing: 8 * screenScaleFactor

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#f4f6f8"
                    border.color: "#c9d0d7"
                    radius: 4 * screenScaleFactor

                    ListView {
                        id: messages
                        anchors.fill: parent
                        anchors.margins: 8 * screenScaleFactor
                        clip: true
                        spacing: 8 * screenScaleFactor
                        model: root.chatMessages

                        delegate: Item {
                            id: msgItem
                            width: messages.width
                            height: bubble.height + 6 * screenScaleFactor
                            property bool isUser: modelData.role === "user"
                            property bool isAssistant: modelData.role === "assistant"
                            property bool isTool: modelData.role === "tool"
                            property bool isError: modelData.kind === "error"
                            property bool copiedRecently: false

                            Timer {
                                id: resetCopyTimer
                                interval: 1800
                                repeat: false
                                onTriggered: msgItem.copiedRecently = false
                            }

                            Rectangle {
                                id: bubble
                                width: parent.width * 0.92
                                height: contentColumn.implicitHeight + 16 * screenScaleFactor
                                anchors.right: msgItem.isUser ? parent.right : undefined
                                anchors.left: msgItem.isUser ? undefined : parent.left
                                radius: 8 * screenScaleFactor
                                color: msgItem.isError ? "#fff5f5" :
                                       msgItem.isUser ? "#edf5ff" :
                                       msgItem.isTool ? "#f3f5f7" : "#ffffff"
                                border.color: msgItem.isError ? "#f8b4b4" :
                                              msgItem.isUser ? "#b9daf8" :
                                              msgItem.isTool ? "#d5dbe1" : "#e2e6eb"
                                border.width: 1

                                Column {
                                    id: contentColumn
                                    anchors.fill: parent
                                    anchors.margins: 10 * screenScaleFactor
                                    spacing: 5 * screenScaleFactor

                                    RowLayout {
                                        width: parent.width
                                        spacing: 6 * screenScaleFactor

                                        Rectangle {
                                            width: 18 * screenScaleFactor
                                            height: 18 * screenScaleFactor
                                            radius: 9 * screenScaleFactor
                                            color: msgItem.isError ? "#e53e3e" :
                                                   msgItem.isUser ? "#2b6cb0" :
                                                   msgItem.isTool ? "#718096" : "#2f855a"

                                            Text {
                                                anchors.centerIn: parent
                                                text: msgItem.isError ? "!" :
                                                      msgItem.isUser ? "👤" :
                                                      msgItem.isTool ? "⚙" : "🤖"
                                                font.pixelSize: 10 * screenScaleFactor
                                                color: "#ffffff"
                                            }
                                        }

                                        Text {
                                            text: msgItem.isError ? "Системное предупреждение" :
                                                  msgItem.isUser ? "Пользователь" :
                                                  msgItem.isTool ? (modelData.kind === "tool_start" ? "Запуск инструмента" : "Результат инструмента") :
                                                  "AI Assistant"
                                            font.bold: true
                                            font.pixelSize: 11 * screenScaleFactor
                                            color: msgItem.isError ? "#c53030" :
                                                   msgItem.isUser ? "#2b6cb0" :
                                                   msgItem.isTool ? "#4a5568" : "#22543d"
                                        }

                                        Item { Layout.fillWidth: true }

                                        Button {
                                            id: copyButton
                                            visible: msgItem.isAssistant || msgItem.isUser || msgItem.isTool
                                            text: msgItem.copiedRecently ? "✓ Скопировано" : "Копировать"
                                            Layout.preferredHeight: 22 * screenScaleFactor
                                            onClicked: {
                                                var ok = manager.copyToClipboard(modelData.text)
                                                if (ok) {
                                                    msgItem.copiedRecently = true
                                                    resetCopyTimer.restart()
                                                }
                                            }
                                        }
                                    }

                                    Text {
                                        id: messageText
                                        width: parent.width
                                        text: modelData.text
                                        wrapMode: Text.Wrap
                                        textFormat: Text.PlainText
                                        color: msgItem.isError ? "#9b2c2c" : "#1a202c"
                                        font.pixelSize: 13 * screenScaleFactor
                                        font.family: "Segoe UI, -apple-system, BlinkMacSystemFont, Roboto, sans-serif"
                                    }

                                    Text {
                                        id: attachmentText
                                        width: parent.width
                                        visible: modelData.attachments && modelData.attachments.length > 0
                                        text: visible ? "📎 " + modelData.attachments.join(", ") : ""
                                        wrapMode: Text.Wrap
                                        color: "#4a5568"
                                        font.pixelSize: 11 * screenScaleFactor
                                    }
                                }
                            }
                        }
                    }
                }

                ProgressBar {
                    Layout.fillWidth: true
                    visible: manager.busy
                    indeterminate: true
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34 * screenScaleFactor
                    color: "#f8fafc"
                    border.color: "#e2e8f0"
                    radius: 4 * screenScaleFactor

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4 * screenScaleFactor
                        spacing: 6 * screenScaleFactor

                        Button {
                            text: "📎 Прикрепить"
                            enabled: !manager.busy
                            onClicked: attachmentDialog.open()
                        }
                        Label {
                            Layout.fillWidth: true
                            text: manager.attachmentLabel
                            elide: Text.ElideMiddle
                            color: "#4a5568"
                            font.pixelSize: 11 * screenScaleFactor
                        }
                        Button {
                            text: "✕ Сброс"
                            enabled: !manager.busy && manager.attachmentLabel !== "Файлы не выбраны"
                            onClicked: manager.clearAttachments()
                        }
                    }
                }

                TextArea {
                    id: composer
                    Layout.fillWidth: true
                    Layout.preferredHeight: 88 * screenScaleFactor
                    enabled: !manager.busy
                    wrapMode: TextEdit.Wrap
                    font.pixelSize: 13 * screenScaleFactor
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8 * screenScaleFactor

                    Button {
                        text: "🗑 Очистить историю"
                        enabled: !manager.busy
                        onClicked: manager.clearHistory()
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        text: manager.busy ? "⌛ Выполняется…" : "➤ Отправить"
                        enabled: !manager.busy && composer.text.trim().length > 0
                        onClicked: {
                            var text = composer.text
                            composer.text = ""
                            manager.sendMessage(text)
                        }
                    }
                }
            }

            Flickable {
                id: economicsPane
                anchors.fill: parent
                visible: root.economicsVisible
                clip: true
                contentWidth: width
                contentHeight: economicsForm.implicitHeight + 20 * screenScaleFactor

                ColumnLayout {
                    id: economicsForm
                    x: 10 * screenScaleFactor
                    y: 8 * screenScaleFactor
                    width: economicsPane.width - 20 * screenScaleFactor
                    spacing: 8 * screenScaleFactor

                    Label {
                        Layout.fillWidth: true
                        text: "Смета по завершённому слайсингу"
                        font.bold: true
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: "Время и масса — из STE Slicer. Тарифы, работы, надбавка и налоги — только ваши значения. Прочность не рассчитывается. После изменения проекта нажмите «Обновить»."
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: root.economics.slicing ?
                                  "Слайсинг: " + root.economics.slicing.state :
                                  "Слайсинг: состояние не прочитано"
                        }
                        Button {
                            text: "Обновить"
                            enabled: !manager.busy
                            onClicked: manager.refreshEconomics()
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.economics.error && root.economics.error.length > 0
                        text: "Ошибка: " + (root.economics.error || "")
                        color: "#bb3030"
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Ставка станка, ₽/ч"; Layout.fillWidth: true }
                        TextField {
                            id: machineRate
                            Layout.preferredWidth: 125 * screenScaleFactor
                            placeholderText: "Введите ставку"
                        }
                    }
                    Label {
                        text: "Цена материала по каждому экструдеру, ₽/кг"
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }
                    Repeater {
                        id: materialRates
                        model: root.economics.slicing && root.economics.slicing.material_weights_g ?
                               root.economics.slicing.material_weights_g.length : 0
                        delegate: RowLayout {
                            width: economicsForm.width
                            property alias rateText: materialRateInput.text
                            Label {
                                Layout.fillWidth: true
                                text: "Экструдер " + (index + 1) + " — " +
                                      ((root.economics.slicing.material_names || [])[index] || "материал") +
                                      ", " + root.economics.slicing.material_weights_g[index] + " г"
                                wrapMode: Text.Wrap
                            }
                            TextField {
                                id: materialRateInput
                                Layout.preferredWidth: 125 * screenScaleFactor
                                placeholderText: "₽/кг"
                            }
                        }
                    }
                    Label {
                        text: "Работы вне печати — только явно введённая сумма"
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField {
                            id: outsideLabel
                            Layout.fillWidth: true
                            placeholderText: "Название работы"
                        }
                        TextField {
                            id: outsideAmount
                            Layout.preferredWidth: 125 * screenScaleFactor
                            placeholderText: "Сумма, ₽"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Надбавка, ₽"; Layout.fillWidth: true }
                        TextField {
                            id: markupAmount
                            Layout.preferredWidth: 125 * screenScaleFactor
                            placeholderText: "0"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Налоги, ₽"; Layout.fillWidth: true }
                        TextField {
                            id: taxAmount
                            Layout.preferredWidth: 125 * screenScaleFactor
                            placeholderText: "0"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Целевой бюджет, ₽"; Layout.fillWidth: true }
                        TextField {
                            id: targetBudget
                            Layout.preferredWidth: 125 * screenScaleFactor
                            placeholderText: "Необязательно"
                        }
                    }
                    Button {
                        text: "Рассчитать"
                        enabled: !manager.busy && root.economics.slicing &&
                                 root.economics.slicing.state === "done"
                        onClicked: manager.calculateEconomics(JSON.stringify(root.costArguments()))
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: costText.implicitHeight + 20 * screenScaleFactor
                        color: root.economics.quote ? "#f7fafc" : "#edf2f7"
                        border.color: root.economics.quote ? "#cbd5e0" : "#e2e8f0"
                        radius: 6 * screenScaleFactor

                        Text {
                            id: costText
                            anchors.fill: parent
                            anchors.margins: 10 * screenScaleFactor
                            text: root.costSummary(root.economics.quote)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                            color: "#1a202c"
                            font.pixelSize: 12 * screenScaleFactor
                            font.family: "Segoe UI, -apple-system, BlinkMacSystemFont, Roboto, sans-serif"
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: "#e2e8f0"
                    }

                    Label {
                        Layout.fillWidth: true
                        text: "Сравнение двух фактически прослайсенных вариантов"
                        font.bold: true
                        wrapMode: Text.Wrap
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Перед сменой настроек сохраните проект 3MF. Пробный расчёт меняет живой проект; эта панель его не восстанавливает."
                        color: "#c05621"
                        font.pixelSize: 11 * screenScaleFactor
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8 * screenScaleFactor

                        Button {
                            text: "💾 Сохранить исходный"
                            enabled: !manager.busy && root.economics.slicing &&
                                     root.economics.slicing.state === "done"
                            onClicked: {
                                var args = root.costArguments()
                                args.slot = "baseline"
                                manager.captureEconomics(JSON.stringify(args))
                            }
                        }
                        Button {
                            text: "💾 Сохранить вариант"
                            enabled: !manager.busy && root.economics.slicing &&
                                     root.economics.slicing.state === "done"
                            onClicked: {
                                var args = root.costArguments()
                                args.slot = "candidate"
                                manager.captureEconomics(JSON.stringify(args))
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Исходный: " + (root.economics.baseline ? "✓ зафиксирован" : "не сохранён") +
                              "  |  Вариант: " + (root.economics.candidate ? "✓ зафиксирован" : "не сохранён")
                        font.bold: true
                        color: "#4a5568"
                        wrapMode: Text.Wrap
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: comparisonText.implicitHeight + 20 * screenScaleFactor
                        color: root.economics.comparison ? "#f0fff4" : "#f7fafc"
                        border.color: root.economics.comparison ? "#9ae6b4" : "#e2e8f0"
                        radius: 6 * screenScaleFactor

                        Text {
                            id: comparisonText
                            anchors.fill: parent
                            anchors.margins: 10 * screenScaleFactor
                            text: root.comparisonSummary(root.economics.comparison)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                            color: "#1a202c"
                            font.pixelSize: 12 * screenScaleFactor
                            font.family: "Segoe UI, -apple-system, BlinkMacSystemFont, Roboto, sans-serif"
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: "Сравнение не подтверждает прочность. Для численной оценки нужны нагрузки, закрепления, направление слоёв и испытанные свойства материала."
                        font.pixelSize: 11 * screenScaleFactor
                        color: "#718096"
                        wrapMode: Text.Wrap
                    }
                }
            }

            ColumnLayout {
                anchors.fill: parent
                visible: root.settingsVisible
                spacing: 10 * screenScaleFactor

                Label {
                    Layout.fillWidth: true
                    text: "Подключение к OpenAI-совместимому Chat Completions API"
                    font.bold: true
                    wrapMode: Text.Wrap
                }
                Label { text: "Base URL" }
                TextField {
                    id: baseUrl
                    Layout.fillWidth: true
                    text: manager.baseUrl
                    placeholderText: "https://example.org/v1"
                }
                Label { text: "API key" }
                TextField {
                    id: apiKey
                    Layout.fillWidth: true
                    text: manager.apiKey
                    echoMode: TextInput.Password
                    placeholderText: "Ключ организаторов"
                }
                Label { text: "Модель" }
                TextField {
                    id: modelName
                    Layout.fillWidth: true
                    text: manager.model
                    placeholderText: "Имя модели из материалов кейса"
                }
                Button {
                    text: "Сохранить"
                    onClicked: manager.saveSettings(baseUrl.text, apiKey.text, modelName.text)
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: endpointText.implicitHeight + 24 * screenScaleFactor
                    color: "#edf0f2"
                    radius: 4 * screenScaleFactor
                    Text {
                        id: endpointText
                        anchors.fill: parent
                        anchors.margins: 12 * screenScaleFactor
                        text: "Локальный MCP: http://127.0.0.1:" + manager.mcpPort + "/mcp\n" +
                              "Для вызова нужен заголовок X-Session-Token."
                        wrapMode: Text.Wrap
                        color: "#40464b"
                    }
                }
                Label {
                    text: "Токен MCP (не публикуйте его)"
                }
                TextField {
                    id: mcpToken
                    Layout.fillWidth: true
                    text: manager.mcpToken
                    readOnly: true
                    echoMode: revealMcpToken.checked ? TextInput.Normal : TextInput.Password
                }
                CheckBox {
                    id: revealMcpToken
                    text: "Показать токен для настройки MCP-клиента"
                }
                Label {
                    Layout.fillWidth: true
                    text: "Ключ хранится в настройках STE Slicer и не входит в исходный код или проекты 3MF."
                    wrapMode: Text.Wrap
                    color: "#5b6770"
                }
                Item { Layout.fillHeight: true }
            }

            Flickable {
                anchors.fill: parent
                visible: root.optimizationVisible
                clip: true
                contentWidth: width
                contentHeight: optimizationForm.implicitHeight + 20 * screenScaleFactor

                ColumnLayout {
                    id: optimizationForm
                    x: 10 * screenScaleFactor
                    y: 8 * screenScaleFactor
                    width: parent.width - 20 * screenScaleFactor
                    spacing: 8 * screenScaleFactor

                    Label {
                        Layout.fillWidth: true
                        text: "Топологическая оптимизация конструкции · TopOpt"
                        font.bold: true
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: "TopOpt работает локально и не требует Ansys или платной лицензии. " +
                              "Расчёт выполняется только по подготовленному шаблону с явно " +
                              "заданными закреплением, нагрузкой и целью по массе."
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: "Локальный bridge подключается автоматически. Токен вводить не нужно."
                        color: "#2f855a"
                    }
                    Button {
                        text: root.manualOptimizationSettings ?
                              "Скрыть ручную настройку" : "Ручная настройка подключения"
                        onClicked: root.manualOptimizationSettings =
                                   !root.manualOptimizationSettings
                    }
                    Label {
                        text: "Bridge URL"
                        visible: root.manualOptimizationSettings
                    }
                    TextField {
                        id: optimizationUrl
                        Layout.fillWidth: true
                        visible: root.manualOptimizationSettings
                        text: manager.optimizationUrl
                        placeholderText: "http://127.0.0.1:8765"
                    }
                    Label {
                        text: "X-Bridge-Token"
                        visible: root.manualOptimizationSettings
                    }
                    TextField {
                        id: optimizationToken
                        Layout.fillWidth: true
                        visible: root.manualOptimizationSettings
                        text: manager.optimizationToken
                        echoMode: showOptimizationToken.checked ? TextInput.Normal : TextInput.Password
                        placeholderText: "Подтянется автоматически из локального bridge"
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.manualOptimizationSettings
                        wrapMode: Text.Wrap
                        text: "Оставьте поле пустым: локальный bridge передаст токен автоматически. " +
                              "Ручной токен нужен только для нестандартной конфигурации."
                        color: "#5d6975"
                    }
                    CheckBox {
                        id: showOptimizationToken
                        visible: root.manualOptimizationSettings
                        text: "Показать токен"
                    }
                    RowLayout {
                        Button {
                            text: "Сохранить подключение"
                            visible: root.manualOptimizationSettings
                            onClicked: manager.saveOptimizationSettings(
                                optimizationUrl.text, optimizationToken.text)
                        }
                        Button {
                            text: root.optimization.checking ? "Запуск и проверка…" :
                                  "Запустить и проверить TopOpt"
                            enabled: !root.optimization.checking
                            onClicked: manager.startOptimizationBridge()
                        }
                        Button {
                            text: "Остановить службу"
                            enabled: manager.bridgeRunning
                            onClicked: manager.stopOptimizationBridge()
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.optimization.error && root.optimization.error.length > 0
                        text: "Ошибка: " + (root.optimization.error || "")
                        color: "#bb3030"
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                        color: root.optimization.capabilities &&
                               root.optimization.capabilities.status === "ready" ?
                               "#17823b" : "#202830"
                        text: {
                            var c = root.optimization.capabilities
                            if (!c) return root.optimization.checking ?
                                "Проверяется локальный bridge…" : "Подключение ещё не проверено."
                            var connection = c.connection || {}
                            return "Подключение: " +
                                   (connection.automatic ? "автоматическое" : "ручное") + "\n" +
                                   "Адрес: " + (connection.url || "не определён") + "\n" +
                                   "Bridge: " + c.status + "\n" +
                                   "Движок: " + (c.engine || "TopOpt") + "\n" +
                                   "Решатель: " + (c.solver || "не определён") + "\n" +
                                   "Внешняя лицензия: " +
                                       (c.external_license_required ? "требуется" : "не требуется") + "\n" +
                                   "Инженерная валидация: " +
                                       (c.engineering_validation_available ? "доступна" : "не выполняется") + "\n" +
                                   "Готовых шаблонов: " + c.templates_ready +
                                   (c.reason ? "\nПричина: " + c.reason : "")
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Шаблоны: " + root.optimizationTemplateSummary()
                        wrapMode: Text.Wrap
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Встроенный шаблон создаёт геометрический кандидат для призматической " +
                              "консольной детали: левая грань закреплена, нагрузка приложена справа. " +
                              "Он не рассчитывает 3D-напряжения, усталость и коэффициент запаса. " +
                              "Перед производством нужны инженерная проверка и физические испытания."
                        wrapMode: Text.Wrap
                        color: "#5b6770"
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }

    rightButtons: [
        Button {
            text: "Закрыть"
            onClicked: root.hide()
        }
    ]
}
