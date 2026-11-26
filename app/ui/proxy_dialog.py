"""Модальное окно настройки прокси Volume Kit."""

from __future__ import annotations

from contextlib import suppress

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.proxy import (
    ProxyEntry,
    ProxyManager,
    ProxyParseError,
    ProxyValidationError,
    get_proxy_manager,
    parse_proxy_bulk,
)
from app.ui.proxy_web import apply_qt_proxy_from_manager
from app.ui.widgets import prepare_combo_box
from app.ui.workers.proxy_check_worker import ProxyCheckWorker

_STATUS_COLORS = {
    "ok": QColor("#059669"),
    "warn": QColor("#D97706"),
    "error": QColor("#DC2626"),
    "idle": QColor("#94A3B8"),
}


def _make_card(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 16)
    layout.setSpacing(10)

    title_label = QLabel(title)
    title_label.setObjectName("SettingsSectionTitle")
    layout.addWidget(title_label)

    if subtitle:
        hint = QLabel(subtitle)
        hint.setObjectName("SettingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    divider = QFrame()
    divider.setObjectName("SidebarDivider")
    layout.addWidget(divider)
    return card, layout


class ProxyEditDialog(QDialog):
    """Редактирование одной записи прокси."""

    def __init__(self, entry: ProxyEntry | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProxyDialog")
        self.setModal(True)
        self.setWindowTitle("Редактировать прокси" if entry else "Добавить прокси")
        self.resize(440, 360)

        self._entry_id = entry.id if entry else ""
        self._result: ProxyEntry | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(9)

        self._protocol = QComboBox()
        prepare_combo_box(self._protocol)
        self._protocol.addItems(["socks5", "socks4", "http", "https"])

        self._host = QLineEdit()
        self._host.setPlaceholderText("127.0.0.1 или proxy.example.com")

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(entry.port if entry else 1080)

        self._username = QLineEdit()
        self._username.setPlaceholderText("необязательно")

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("необязательно")

        self._label = QLineEdit()
        self._label.setPlaceholderText("например, EU-1")

        self._enabled = QCheckBox("Участвует в случайной ротации")
        self._enabled.setChecked(entry.enabled if entry else True)

        form.addRow("Протокол:", self._protocol)
        form.addRow("Хост:", self._host)
        form.addRow("Порт:", self._port)
        form.addRow("Логин:", self._username)
        form.addRow("Пароль:", self._password)
        form.addRow("Метка:", self._label)
        form.addRow("", self._enabled)
        root.addLayout(form)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("SecondaryBtn")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("PrimaryBtn")
        save.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        if entry is not None:
            index = self._protocol.findText(entry.protocol)
            self._protocol.setCurrentIndex(max(index, 0))
            self._host.setText(entry.host)
            self._username.setText(entry.username)
            self._password.setText(entry.password)
            self._label.setText(entry.label)

    @property
    def result_entry(self) -> ProxyEntry | None:
        return self._result

    def _on_save(self) -> None:
        try:
            entry = ProxyEntry(
                id=self._entry_id,
                protocol=self._protocol.currentText(),
                host=self._host.text().strip(),
                port=self._port.value(),
                username=self._username.text().strip(),
                password=self._password.text(),
                label=self._label.text().strip(),
                enabled=self._enabled.isChecked(),
            )
        except (ProxyValidationError, ProxyParseError, ValueError) as exc:
            QMessageBox.warning(self, "Некорректный прокси", str(exc))
            return

        self._result = entry
        self.accept()


class ProxyDialog(QDialog):
    """Светлая модальная панель управления прокси."""

    toast_requested = Signal(str)
    _state_changed = Signal()

    def __init__(self, manager: ProxyManager | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager if manager is not None else get_proxy_manager()
        self._updating = False
        self._status: dict[str, tuple[str, str, str]] = {}
        self._worker: ProxyCheckWorker | None = None
        self._bulk_visible = False
        self._cleaned = False

        self.setObjectName("ProxyDialog")
        self.setWindowTitle("Настройка прокси")
        self.setModal(True)
        self.resize(900, 720)

        self._build_ui()
        self._unsubscribe = self._manager.subscribe(self._state_changed.emit)
        self._state_changed.connect(self._reload, Qt.ConnectionType.QueuedConnection)
        self.finished.connect(self._cleanup)
        self._reload()

    # ==================== ПОСТРОЕНИЕ ИНТЕРФЕЙСА ====================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("Настройка прокси")
        title.setObjectName("CardTitle")
        subtitle = QLabel(
            "Все HTTP-запросы приложения используют выбранный или случайный прокси. "
            "Пароли не отображаются и не пишутся в логи."
        )
        subtitle.setObjectName("SettingsHint")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        root.addWidget(self._build_controls_card())
        root.addWidget(self._build_list_card(), 1)
        root.addWidget(self._build_add_card())

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("SecondaryBtn")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _build_controls_card(self) -> QFrame:
        card, layout = _make_card(
            "Режим работы",
            "Главный рубильник, ротация и применение настроек к встроенным Web-вкладкам.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)

        self._enabled_cb = QCheckBox("Включить прокси")
        self._enabled_cb.setToolTip("Весь сетевой трафик приложения пойдёт через прокси")
        self._enabled_cb.toggled.connect(self._on_enabled_toggled)

        self._random_cb = QCheckBox("Случайный прокси из списка")
        self._random_cb.setToolTip(
            "Каждый запрос берёт случайный прокси из отмеченных «Вкл». "
            "При сетевой ошибке выполняется одна повторная попытка через другой прокси."
        )
        self._random_cb.toggled.connect(self._on_random_toggled)

        self._remote_dns_cb = QCheckBox("Удалённый DNS (socks5h)")
        self._remote_dns_cb.setToolTip(
            "SOCKS5 использует socks5h://, SOCKS4 — socks4a://. "
            "Помогает обходить DNS-подмену и ошибки SSL Hostname mismatch."
        )
        self._remote_dns_cb.toggled.connect(self._on_remote_dns_toggled)

        self._web_cb = QCheckBox("Применять к Web-вкладкам")
        self._web_cb.setToolTip(
            "QNetworkProxy.setApplicationProxy для Qt. Chromium в QWebEngine может "
            "игнорировать настройку; SOCKS5 с авторизацией и SOCKS4 не поддерживаются."
        )
        self._web_cb.toggled.connect(self._on_web_toggled)

        grid.addWidget(self._enabled_cb, 0, 0)
        grid.addWidget(self._random_cb, 0, 1)
        grid.addWidget(self._remote_dns_cb, 1, 0)
        grid.addWidget(self._web_cb, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self._warning_lbl = QLabel("")
        self._warning_lbl.setObjectName("ProxyWarning")
        self._warning_lbl.setWordWrap(True)
        self._warning_lbl.hide()
        layout.addWidget(self._warning_lbl)
        return card


    def _build_list_card(self) -> QFrame:
        card, layout = _make_card(
            "Список прокси",
            "Радиокнопка выбирает активный прокси. Колонка «Вкл» управляет участием "
            "в случайной ротации.",
        )

        head = QHBoxLayout()
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("SettingsHint")
        head.addWidget(self._count_lbl)
        head.addStretch(1)
        layout.addLayout(head)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["", "Вкл", "Протокол", "Адрес", "Метка", "Статус"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(230)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 48)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 46)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 88)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(4, 130)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 165)

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        self._check_one_btn = QPushButton("Проверить выбранный")
        self._check_one_btn.setObjectName("SecondaryBtn")
        self._check_one_btn.clicked.connect(self._on_check_selected)

        self._check_all_btn = QPushButton("Проверить все")
        self._check_all_btn.setObjectName("SecondaryBtn")
        self._check_all_btn.clicked.connect(self._on_check_all)

        self._edit_btn = QPushButton("Редактировать")
        self._edit_btn.setObjectName("SecondaryBtn")
        self._edit_btn.clicked.connect(self._on_edit)

        self._delete_btn = QPushButton("Удалить")
        self._delete_btn.setObjectName("DangerBtn")
        self._delete_btn.clicked.connect(self._on_delete)

        actions.addWidget(self._check_one_btn)
        actions.addWidget(self._check_all_btn)
        actions.addStretch(1)
        actions.addWidget(self._edit_btn)
        actions.addWidget(self._delete_btn)
        layout.addLayout(actions)
        return card

    def _build_add_card(self) -> QFrame:
        card, layout = _make_card(
            "Добавить прокси",
            "Поддерживаются форматы: protocol://user:pass@host:port, user:pass@host:port, "
            "host:port:user:pass и host:port. Несколько значений можно разделять запятой.",
        )

        row = QHBoxLayout()
        self._protocol_combo = QComboBox()
        prepare_combo_box(self._protocol_combo)
        self._protocol_combo.addItems(["socks5", "http", "https", "socks4"])
        self._protocol_combo.setFixedWidth(110)

        self._input = QLineEdit()
        self._input.setPlaceholderText(
            "ip:port, ip:port:user:pass или socks5://user:pass@ip:port"
        )

        self._label_input = QLineEdit()
        self._label_input.setPlaceholderText("Метка")
        self._label_input.setFixedWidth(130)

        self._add_btn = QPushButton("Добавить")
        self._add_btn.setObjectName("PrimaryBtn")
        self._add_btn.clicked.connect(self._on_add)

        self._bulk_btn = QPushButton("Импорт списком")
        self._bulk_btn.setObjectName("SecondaryBtn")
        self._bulk_btn.clicked.connect(self._toggle_bulk)

        row.addWidget(QLabel("Протокол:"))
        row.addWidget(self._protocol_combo)
        row.addWidget(self._input, 1)
        row.addWidget(self._label_input)
        row.addWidget(self._add_btn)
        layout.addLayout(row)

        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        toggle_row.addWidget(self._bulk_btn)
        layout.addLayout(toggle_row)

        self._bulk_box = QWidget()
        bulk_layout = QVBoxLayout(self._bulk_box)
        bulk_layout.setContentsMargins(0, 0, 0, 0)
        bulk_layout.setSpacing(8)
        self._bulk_input = QPlainTextEdit()
        self._bulk_input.setObjectName("ProxyBulkInput")
        self._bulk_input.setPlaceholderText(
            "Вставьте список прокси — по одному в строке или через запятую/точку с запятой"
        )
        self._bulk_input.setFixedHeight(110)
        bulk_layout.addWidget(self._bulk_input)

        bulk_actions = QHBoxLayout()
        bulk_actions.addStretch(1)
        import_btn = QPushButton("Импортировать")
        import_btn.setObjectName("PrimaryBtn")
        import_btn.clicked.connect(self._on_import)
        bulk_actions.addWidget(import_btn)
        bulk_layout.addLayout(bulk_actions)
        self._bulk_box.hide()
        layout.addWidget(self._bulk_box)

        self._error_lbl = QLabel("")
        self._error_lbl.setObjectName("ErrorText")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.hide()
        layout.addWidget(self._error_lbl)
        return card


    # ==================== СИНХРОНИЗАЦИЯ С МЕНЕДЖЕРОМ ====================

    def _reload(self) -> None:
        if self._updating:
            return

        self._updating = True
        try:
            config = self._manager.snapshot()
            self._enabled_cb.setChecked(config.enabled)
            self._random_cb.setChecked(config.random_mode)
            self._remote_dns_cb.setChecked(config.remote_dns)
            self._web_cb.setChecked(config.apply_to_web)

            warning = apply_qt_proxy_from_manager()
            if warning:
                self._warning_lbl.setText(warning)
                self._warning_lbl.show()
            else:
                self._warning_lbl.hide()

            active_count = sum(1 for entry in config.proxies if entry.enabled)
            self._count_lbl.setText(
                f"Всего: {len(config.proxies)}   Активных: {active_count}"
            )

            self._table.blockSignals(True)
            try:
                self._table.setRowCount(0)
                for entry in config.proxies:
                    self._append_row(entry, config)
            finally:
                self._table.blockSignals(False)
        finally:
            self._updating = False

        if self._table.rowCount() > 0 and self._table.currentRow() < 0:
            self._table.selectRow(0)

    def _append_row(self, entry: ProxyEntry, config) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setRowHeight(row, 34)

        radio = QRadioButton()
        radio.setChecked(config.active_proxy_id == entry.id)
        radio.setEnabled(not config.random_mode)
        radio.setToolTip(
            "Случайный режим включён — активный прокси выбирается автоматически"
            if config.random_mode
            else "Сделать прокси активным"
        )
        radio.toggled.connect(
            lambda checked, proxy_id=entry.id: self._on_radio_toggled(checked, proxy_id)
        )
        self._table.setCellWidget(row, 0, radio)

        enabled_item = QTableWidgetItem()
        enabled_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        )
        enabled_item.setCheckState(
            Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
        )
        enabled_item.setToolTip("Участвует в случайной ротации")
        self._table.setItem(row, 1, enabled_item)

        protocol_item = QTableWidgetItem(entry.protocol.upper())
        protocol_item.setData(Qt.ItemDataRole.UserRole, entry.id)
        self._table.setItem(row, 2, protocol_item)

        self._table.setCellWidget(row, 3, self._address_widget(entry))
        self._table.setItem(row, 4, QTableWidgetItem(entry.label or "—"))

        status_item = QTableWidgetItem()
        self._table.setItem(row, 5, status_item)
        self._apply_status(row, entry.id)

    def _address_widget(self, entry: ProxyEntry) -> QWidget:
        wrap = QWidget()
        wrap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        address = QLabel(f"{entry.host}:{entry.port}")
        address.setObjectName("ProxyAddress")
        address.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(address)

        if entry.has_auth:
            badge = QLabel("Auth")
            badge.setObjectName("AuthBadge")
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            badge.setToolTip("Прокси использует логин/пароль (пароль скрыт)")
            layout.addWidget(badge)

        layout.addStretch(1)
        return wrap

    def _apply_status(self, row: int, proxy_id: str) -> None:
        item = self._table.item(row, 5)
        if item is None:
            return
        state, text, tooltip = self._status.get(proxy_id, ("idle", "—", ""))
        item.setText(text)
        item.setForeground(_STATUS_COLORS.get(state, _STATUS_COLORS["idle"]))
        item.setToolTip(tooltip)

    def _row_id(self, row: int) -> str:
        item = self._table.item(row, 2)
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _selected_id(self) -> str:
        row = self._table.currentRow()
        return self._row_id(row) if row >= 0 else ""

    def _selected_entry(self) -> ProxyEntry | None:
        proxy_id = self._selected_id()
        return self._manager.get_entry(proxy_id) if proxy_id else None


    # ==================== ОБРАБОТЧИКИ ЧЕКБОКСОВ И ТАБЛИЦЫ ====================

    def _on_enabled_toggled(self, checked: bool) -> None:
        if not self._updating:
            self._manager.set_enabled(checked)

    def _on_random_toggled(self, checked: bool) -> None:
        if not self._updating:
            self._manager.set_random_mode(checked)

    def _on_remote_dns_toggled(self, checked: bool) -> None:
        if not self._updating:
            self._manager.set_remote_dns(checked)

    def _on_web_toggled(self, checked: bool) -> None:
        if not self._updating:
            self._manager.set_apply_to_web(checked)
            warning = apply_qt_proxy_from_manager()
            self._warning_lbl.setText(warning or "")
            self._warning_lbl.setVisible(bool(warning))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 1:
            return
        proxy_id = self._row_id(item.row())
        entry = self._manager.get_entry(proxy_id)
        if entry is None:
            return
        entry.enabled = item.checkState() == Qt.CheckState.Checked
        self._manager.update(entry)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column == 1 or self._manager.random_mode:
            return
        proxy_id = self._row_id(row)
        if proxy_id:
            self._manager.set_active(proxy_id)

    def _on_radio_toggled(self, checked: bool, proxy_id: str) -> None:
        if checked and not self._updating:
            self._manager.set_active(proxy_id)

    # ==================== ДОБАВЛЕНИЕ / ИМПОРТ ====================

    def _show_errors(self, errors: list[str]) -> None:
        if not errors:
            self._error_lbl.hide()
            self._error_lbl.clear()
            return
        shown = errors[:6]
        text = "\n".join(shown)
        if len(errors) > len(shown):
            text += f"\n… и ещё {len(errors) - len(shown)}"
        self._error_lbl.setText(text)
        self._error_lbl.show()

    def _add_text(self, text: str, label: str) -> bool:
        if not text.strip():
            self._show_errors(["Введите хотя бы одну строку прокси"])
            return False

        entries, errors = parse_proxy_bulk(
            text,
            default_protocol=self._protocol_combo.currentText(),
            label=label,
        )
        self._show_errors(errors)

        if not entries:
            return False

        added, skipped = self._manager.add_bulk(entries)
        message = f"Добавлено прокси: {added}"
        if skipped:
            message += f", дублей пропущено: {skipped}"
        if errors:
            message += f", ошибок: {len(errors)}"
        self.toast_requested.emit(message)
        return added > 0

    def _on_add(self) -> None:
        if self._add_text(self._input.text(), self._label_input.text().strip()):
            self._input.clear()

    def _toggle_bulk(self) -> None:
        self._bulk_visible = not self._bulk_visible
        self._bulk_box.setVisible(self._bulk_visible)
        self._bulk_btn.setText(
            "Скрыть импорт списком" if self._bulk_visible else "Импорт списком"
        )

    def _on_import(self) -> None:
        if self._add_text(self._bulk_input.toPlainText(), self._label_input.text().strip()):
            self._bulk_input.clear()


    # ==================== РЕДАКТИРОВАНИЕ / УДАЛЕНИЕ ====================

    def _on_edit(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Прокси", "Выберите прокси в таблице.")
            return

        dialog = ProxyEditDialog(entry, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_entry is not None:
            if not self._manager.update(dialog.result_entry):
                QMessageBox.warning(self, "Прокси", "Запись не найдена — возможно, она удалена.")
            else:
                self.toast_requested.emit("Прокси обновлён")

    def _on_delete(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Прокси", "Выберите прокси в таблице.")
            return

        answer = QMessageBox.question(
            self,
            "Удалить прокси?",
            f"Удалить {entry.display_name()} ({entry.host}:{entry.port})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._status.pop(entry.id, None)
        self._manager.remove(entry.id)
        self.toast_requested.emit("Прокси удалён")

    # ==================== ПРОВЕРКА ====================

    def _set_check_buttons_enabled(self, enabled: bool) -> None:
        self._check_one_btn.setEnabled(enabled)
        self._check_all_btn.setEnabled(enabled)
        self._edit_btn.setEnabled(enabled)
        self._delete_btn.setEnabled(enabled)

    def _start_check(self, entries: list[ProxyEntry]) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not entries:
            self.toast_requested.emit("Нет прокси для проверки")
            return

        for entry in entries:
            self._status[entry.id] = ("idle", "…", "Проверка выполняется")
        self._reload()

        self._set_check_buttons_enabled(False)
        worker = ProxyCheckWorker(entries, parent=self)
        worker.checked.connect(self._on_check_result)
        worker.finished_all.connect(self._on_check_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_check_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Прокси", "Выберите прокси в таблице.")
            return
        self._start_check([entry])

    def _on_check_all(self) -> None:
        self._start_check(self._manager.entries())

    def _on_check_result(self, proxy_id: str, state: str, text: str, tooltip: str) -> None:
        self._status[proxy_id] = (state, text, tooltip)
        for row in range(self._table.rowCount()):
            if self._row_id(row) == proxy_id:
                self._apply_status(row, proxy_id)
                break

    def _on_check_finished(self) -> None:
        self._worker = None
        self._set_check_buttons_enabled(True)

    # ==================== ЗАКРЫТИЕ ====================

    def _cleanup(self, _result: int | None = None) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        with suppress(Exception):
            self._unsubscribe()
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            if not self._worker.wait(4000):
                self._worker.terminate()
                self._worker.wait(500)
        self._worker = None

    def closeEvent(self, event) -> None:
        self._cleanup()
        super().closeEvent(event)

