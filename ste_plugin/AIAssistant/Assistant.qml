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
    property var chatMessages: []

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
    }

    Component.onCompleted: refreshMessages()

    Connections {
        target: manager
        onMessagesChanged: root.refreshMessages()
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
        onAccepted: manager.setAttachments(fileUrls)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: UM.Theme.getSize("default_margin").height

        RowLayout {
            Layout.fillWidth: true
            Button {
                text: "Чат"
                checkable: true
                checked: !root.settingsVisible
                onClicked: root.settingsVisible = false
            }
            Button {
                text: "Настройки"
                checkable: true
                checked: root.settingsVisible
                onClicked: root.settingsVisible = true
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
                visible: !root.settingsVisible
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
                                width: Math.min(parent.width * 0.9, messageText.implicitWidth + 28 * screenScaleFactor)
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
                    placeholderText: "Опишите назначение детали, материал, нагрузки и ограничения…"
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
                              "Сервер доступен только на этом компьютере."
                        wrapMode: Text.Wrap
                        color: "#40464b"
                    }
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
