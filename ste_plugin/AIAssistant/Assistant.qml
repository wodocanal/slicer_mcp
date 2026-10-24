import QtQuick 2.2
import QtQuick.Controls 1.3
import QtQuick.Dialogs 1.1
import QtQuick.Layouts 1.1
import QtQuick.Window 2.1

import UM 1.1 as UM

UM.Dialog {
    id: root
    title: "AI Assistant — подготовка 3D/5D-печати"
    width: 560 * screenScaleFactor
    height: 720 * screenScaleFactor
    minimumWidth: 440 * screenScaleFactor
    minimumHeight: 520 * screenScaleFactor
    modality: Qt.NonModal

    property bool settingsVisible: false
    property bool economicsVisible: false
    property var chatMessages: []
    property var economics: ({})

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
    }

    function refreshEconomicsData() {
        try {
            economics = JSON.parse(manager.economicsJson)
        } catch (error) {
            economics = ({error: "Не удалось прочитать данные сметы"})
        }
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
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: UM.Theme.getSize("default_margin").height

        Connections {
            target: manager
            onMessagesChanged: root.refreshMessages()
            onEconomicsChanged: root.refreshEconomicsData()
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
            Button {
                text: "Чат"
                checkable: true
                checked: !root.settingsVisible && !root.economicsVisible
                onClicked: { root.settingsVisible = false; root.economicsVisible = false }
            }
            Button {
                text: "Смета"
                checkable: true
                checked: root.economicsVisible
                onClicked: {
                    root.settingsVisible = false
                    root.economicsVisible = true
                    manager.refreshEconomics()
                }
            }
            Button {
                text: "Настройки"
                checkable: true
                checked: root.settingsVisible
                onClicked: root.showSettings()
            }
            Item { Layout.fillWidth: true }
            Label {
                text: manager.busy ? "Агент работает…" : "Готов"
                color: manager.busy ? "#d98200" : "#39823c"
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                visible: !root.settingsVisible && !root.economicsVisible
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
                            width: messages.width
                            height: bubble.height
                            Rectangle {
                                id: bubble
                                width: parent.width * 0.9
                                height: messageText.implicitHeight + attachmentText.implicitHeight + 22 * screenScaleFactor
                                anchors.right: modelData.role === "user" ? parent.right : undefined
                                anchors.left: modelData.role === "user" ? undefined : parent.left
                                radius: 8 * screenScaleFactor
                                color: modelData.kind === "error" ? "#fde8e8" :
                                       modelData.role === "user" ? "#dcecff" :
                                       modelData.role === "tool" ? "#edf0f2" : "white"
                                border.color: modelData.kind === "tool_start" ? "#5b8def" : "#d5d9dd"

                                Column {
                                    anchors.fill: parent
                                    anchors.margins: 10 * screenScaleFactor
                                    spacing: 3 * screenScaleFactor
                                    Text {
                                        id: messageText
                                        width: bubble.width - 20 * screenScaleFactor
                                        text: modelData.text
                                        wrapMode: Text.Wrap
                                        textFormat: Text.PlainText
                                        color: "#222222"
                                        font.pixelSize: 13 * screenScaleFactor
                                    }
                                    Text {
                                        id: attachmentText
                                        width: messageText.width
                                        visible: modelData.attachments && modelData.attachments.length > 0
                                        text: visible ? "📎 " + modelData.attachments.join(", ") : ""
                                        wrapMode: Text.Wrap
                                        color: "#5b6770"
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

                RowLayout {
                    Layout.fillWidth: true
                    Button {
                        text: "Прикрепить"
                        enabled: !manager.busy
                        onClicked: attachmentDialog.open()
                    }
                    Label {
                        Layout.fillWidth: true
                        text: manager.attachmentLabel
                        elide: Text.ElideMiddle
                        color: "#5b6770"
                    }
                    Button {
                        text: "Очистить"
                        enabled: !manager.busy
                        onClicked: manager.clearAttachments()
                    }
                }

                TextArea {
                    id: composer
                    Layout.fillWidth: true
                    Layout.preferredHeight: 90 * screenScaleFactor
                    enabled: !manager.busy
                    wrapMode: TextEdit.Wrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    Button {
                        text: "Очистить историю"
                        enabled: !manager.busy
                        onClicked: manager.clearHistory()
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        text: manager.busy ? "Выполняется…" : "Отправить"
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
                    Text {
                        Layout.fillWidth: true
                        text: root.costSummary(root.economics.quote)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                        color: "#202830"
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
                        color: "#8b5a00"
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            text: "Сохранить исходный"
                            enabled: !manager.busy && root.economics.slicing &&
                                     root.economics.slicing.state === "done"
                            onClicked: {
                                var args = root.costArguments()
                                args.slot = "baseline"
                                manager.captureEconomics(JSON.stringify(args))
                            }
                        }
                        Button {
                            text: "Сохранить вариант"
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
                        text: "Исходный: " + (root.economics.baseline ? "зафиксирован" : "нет") +
                              "; вариант: " + (root.economics.candidate ? "зафиксирован" : "нет")
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: root.comparisonSummary(root.economics.comparison)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                        color: "#202830"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Сравнение не подтверждает прочность. Для численной оценки нужны нагрузки, закрепления, направление слоёв и испытанные свойства материала."
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
        }
    }

    rightButtons: [
        Button {
            text: "Закрыть"
            onClicked: root.hide()
        }
    ]
}
