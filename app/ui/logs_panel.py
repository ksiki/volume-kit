import html
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .widgets import copy_to_clipboard, repolish

_LEVEL_COLORS = {
    "INFO": "#0284C7",
    "WARNING": "#D97706",
    "ERROR": "#DC2626",
    "CRITICAL": "#B91C1C",
}


class LogsPanel(QFrame):
    """Правая панель логов выполнения с фильтрами и поиском."""

    close_requested = Signal()
    copied = Signal(str)
    counts_changed = Signal(int, int, int)  # total, warns, errors

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogsDrawer")
        self.setFixedWidth(520)

        self._entries: list[dict[str, Any]] = []
        self._level_filter: str = "ALL"
        self._search_text: str = ""
        self._filter_buttons: dict[str, QPushButton] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Заголовок
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("Логи выполнения")
        title.setObjectName("SettingsSectionTitle")

        self._count_lbl = QLabel("0")
        self._count_lbl.setObjectName("RefPriceChip")

        btn_copy = QPushButton("Копировать")
        btn_copy.setObjectName("SecondaryBtn")
        btn_copy.clicked.connect(self._copy_all_logs)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("SecondaryBtn")
        btn_close.setFixedWidth(34)
        btn_close.setToolTip("Скрыть панель логов (Esc)")
        btn_close.clicked.connect(self.close_requested.emit)

        header.addWidget(title)
        header.addWidget(self._count_lbl)
        header.addStretch(1)
        header.addWidget(btn_copy)
        header.addWidget(btn_close)
        root.addLayout(header)

        # Фильтры + поиск
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        for key, label in (
            ("ALL", "Все"),
            ("INFO", "INFO"),
            ("WARNING", "WARN"),
            ("ERROR", "ERR"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("FilterChip")
            btn.setProperty("active", key == "ALL")
            btn.clicked.connect(lambda _checked=False, k=key: self._set_level_filter(k))
            repolish(btn)
            self._filter_buttons[key] = btn
            toolbar.addWidget(btn)

        toolbar.addSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Поиск по логам...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_input, 1)

        root.addLayout(toolbar)

        # Область вывода
        self._view = QPlainTextEdit()
        self._view.setObjectName("LogView")
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        root.addWidget(self._view, 1)

        # Нижняя панель
        footer = QHBoxLayout()
        self._auto_scroll_cb = QCheckBox("Автопрокрутка")
        self._auto_scroll_cb.setChecked(True)

        hint = QLabel("L — открыть / закрыть  •  Esc — скрыть")
        hint.setObjectName("SettingsHint")

        footer.addWidget(self._auto_scroll_cb)
        footer.addStretch(1)
        footer.addWidget(hint)
        root.addLayout(footer)

    def add_entry(self, entry: dict[str, Any]) -> None:
        self._entries.append(entry)
        if len(self._entries) > 2000:
            self._entries = self._entries[-1500:]
            self._rebuild_view()
        elif self._matches(entry):
            self._append_formatted(entry)
            if self._auto_scroll_cb.isChecked():
                self._scroll_to_bottom()

        self._emit_counts()

    def clear_logs(self) -> None:
        self._entries.clear()
        self._view.clear()
        self._emit_counts()

    def recent_entries(self, limit: int = 8) -> list[dict[str, Any]]:
        return self._entries[-limit:]

    def last_entry(self) -> dict[str, Any] | None:
        return self._entries[-1] if self._entries else None

    def _set_level_filter(self, level: str) -> None:
        self._level_filter = level
        for key, btn in self._filter_buttons.items():
            btn.setProperty("active", key == level)
            repolish(btn)
        self._rebuild_view()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self._rebuild_view()

    def _matches(self, entry: dict[str, Any]) -> bool:
        lvl = entry.get("level", "INFO")
        if self._level_filter != "ALL":
            if self._level_filter == "ERROR":
                if lvl not in ("ERROR", "CRITICAL"):
                    return False
            elif lvl != self._level_filter:
                return False

        if self._search_text:
            hay = (
                f"{entry.get('time', '')} {lvl} "
                f"{entry.get('logger', '')} {entry.get('message', '')}"
            ).lower()
            if self._search_text not in hay:
                return False

        return True

    def _format_entry_html(self, entry: dict[str, Any]) -> str:
        t = html.escape(str(entry.get("time", "")))
        lvl = str(entry.get("level", "INFO"))
        lvl_esc = html.escape(lvl.ljust(7))
        logger_name = html.escape(str(entry.get("logger", ""))[:12].ljust(12))
        msg = html.escape(str(entry.get("message", "")))

        lvl_color = _LEVEL_COLORS.get(lvl, "#475569")
        msg_color = "#991B1B" if lvl in ("ERROR", "CRITICAL") else (
            "#92400E" if lvl == "WARNING" else "#0F172A"
        )

        return (
            f'<span style="color:#64748B;">{t}</span> '
            f'<span style="color:{lvl_color}; font-weight:700;">{lvl_esc}</span> '
            f'<span style="color:#6366F1;">[{logger_name.strip()}]</span> '
            f'<span style="color:{msg_color};">{msg}</span>'
        )

    def _append_formatted(self, entry: dict[str, Any]) -> None:
        self._view.appendHtml(self._format_entry_html(entry))

    def _rebuild_view(self) -> None:
        self._view.clear()
        matched = [e for e in self._entries if self._matches(e)]
        if not matched:
            self._view.appendHtml(
                '<span style="color:#94A3B8;">Нет записей по текущему фильтру</span>'
            )
            return

        for entry in matched:
            self._view.appendHtml(self._format_entry_html(entry))

        if self._auto_scroll_cb.isChecked():
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        sb = self._view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _emit_counts(self) -> None:
        total = len(self._entries)
        warns = sum(1 for e in self._entries if e.get("level") == "WARNING")
        errs = sum(1 for e in self._entries if e.get("level") in ("ERROR", "CRITICAL"))
        self._count_lbl.setText(str(total))
        self.counts_changed.emit(total, warns, errs)

    def _copy_all_logs(self) -> None:
        if not self._entries:
            self.copied.emit("Логи пусты")
            return
        text = "\n".join(
            f"{e['time']} | {e['level']:<7} | {e['logger']:<10} | {e['message']}"
            for e in self._entries
        )
        copy_to_clipboard(text)
        self.copied.emit("✓ Все логи скопированы в буфер обмена")