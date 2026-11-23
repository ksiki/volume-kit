"""Панель логов: история из JSONL-файлов, фильтры и группировка запусков."""

import html
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import SESSION_ID, available_log_dates, read_recent_logs
from app.core.paths import LOGS_DIR

from .widgets import copy_to_clipboard, prepare_combo_box

_TOOL_COLORS: dict[str, tuple[str, str]] = {
    "Calibrator": ("#DBEAFE", "#1D4ED8"),
    "Web": ("#E0F2FE", "#0369A1"),
    "Settings": ("#EDE9FE", "#6D28D9"),
    "System": ("#E2E8F0", "#334155"),
}
_LEVEL_COLORS: dict[str, tuple[str, str]] = {
    "DEBUG": ("#F1F5F9", "#475569"),
    "INFO": ("#E0F2FE", "#0369A1"),
    "WARNING": ("#FEF3C7", "#B45309"),
    "ERROR": ("#FEE2E2", "#B91C1C"),
    "CRITICAL": ("#FEE2E2", "#7F1D1D"),
}
_LEVEL_LABELS = {"WARNING": "WARN", "CRITICAL": "CRIT"}
_TOOL_ORDER = ["Calibrator", "Web", "Settings", "System"]
_MAX_ENTRIES = 5000


def _badge(text: str, colors: tuple[str, str]) -> str:
    background, foreground = colors
    return (
        f'<span style="background-color:{background}; color:{foreground}; '
        f'font-size:10px; font-weight:700;">&nbsp;{html.escape(text)}&nbsp;</span>'
    )


class LogsPanel(QFrame):
    """Правая панель логов с фильтрами, историей и группировкой запусков."""

    close_requested = Signal()
    copied = Signal(str)
    counts_changed = Signal(int, int, int)  # session total, warns, errors

    def __init__(self, parent: QWidget | None = None, logs_dir: Path = LOGS_DIR) -> None:
        super().__init__(parent)
        self.setObjectName("LogsDrawer")
        self.setFixedWidth(520)

        self._logs_dir = Path(logs_dir)
        self._entries: list[dict[str, Any]] = []
        self._available_dates: list[str] = []
        self._available_tools: list[str] = []
        self._session_id = SESSION_ID
        self._date_filter = datetime.now().strftime("%Y-%m-%d")
        self._tool_filter = "ALL"
        self._level_filter = "ALL"
        self._search_text = ""
        self._last_rendered_date = ""

        self._build_ui()
        self._load_history()
        self._populate_filter_options()
        self._rebuild_view()
        self._emit_counts()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("Логи выполнения")
        title.setObjectName("SettingsSectionTitle")

        self._count_lbl = QLabel("0")
        self._count_lbl.setObjectName("RefPriceChip")
        self._count_lbl.setToolTip("Всего записей загружено (история + текущая сессия)")

        btn_copy = QPushButton("Копировать")
        btn_copy.setObjectName("SecondaryBtn")
        btn_copy.clicked.connect(self._copy_visible_logs)

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

        filters = QHBoxLayout()
        filters.setSpacing(6)

        self._date_combo = QComboBox()
        prepare_combo_box(self._date_combo)
        self._date_combo.setMinimumWidth(150)
        self._date_combo.setToolTip("День логов")
        self._date_combo.currentIndexChanged.connect(self._on_date_filter_changed)

        self._tool_combo = QComboBox()
        prepare_combo_box(self._tool_combo)
        self._tool_combo.setMinimumWidth(130)
        self._tool_combo.setToolTip("Инструмент-источник")
        self._tool_combo.currentIndexChanged.connect(self._on_tool_filter_changed)

        self._level_combo = QComboBox()
        prepare_combo_box(self._level_combo)
        self._level_combo.setMinimumWidth(88)
        self._level_combo.setToolTip("Минимальный уровень")
        self._level_combo.addItem("Все", "ALL")
        self._level_combo.addItem("WARN+", "WARN")
        self._level_combo.addItem("ERROR", "ERROR")
        self._level_combo.currentIndexChanged.connect(self._on_level_filter_changed)

        btn_folder = QPushButton("Папка логов")
        btn_folder.setObjectName("SecondaryBtn")
        btn_folder.setToolTip(str(self._logs_dir))
        btn_folder.clicked.connect(self._open_logs_dir)

        filters.addWidget(self._date_combo)
        filters.addWidget(self._tool_combo)
        filters.addWidget(self._level_combo)
        filters.addWidget(btn_folder, 1)
        root.addLayout(filters)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Поиск по логам...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search_input)

        self._view = QPlainTextEdit()
        self._view.setObjectName("LogView")
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        root.addWidget(self._view, 1)

        footer = QHBoxLayout()
        self._auto_scroll_cb = QCheckBox("Автопрокрутка")
        self._auto_scroll_cb.setChecked(True)

        hint = QLabel("L — открыть / закрыть  •  Esc — скрыть")
        hint.setObjectName("SettingsHint")

        footer.addWidget(self._auto_scroll_cb)
        footer.addStretch(1)
        footer.addWidget(hint)
        root.addLayout(footer)

    # ==================== ИСТОРИЯ И ФИЛЬТРЫ ====================

    def _load_history(self) -> None:
        self._entries = [entry for entry in read_recent_logs(self._logs_dir) if isinstance(entry, dict)]
        self._trim_entries()
        self._refresh_available_values()

    def _refresh_available_values(self) -> None:
        dates = set(available_log_dates(self._logs_dir))
        tools: set[str] = set()

        for entry in self._entries:
            date = str(entry.get("date", "") or "")
            if date:
                dates.add(date)
            tool = str(entry.get("tool", "") or "")
            if tool:
                tools.add(tool)

        self._available_dates = sorted(dates, reverse=True)
        ordered = [tool for tool in _TOOL_ORDER if tool in tools]
        ordered += sorted(tool for tool in tools if tool not in _TOOL_ORDER)
        self._available_tools = ordered

    def _populate_filter_options(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date_filter not in self._available_dates and self._date_filter != "ALL":
            self._date_filter = today

        self._date_combo.blockSignals(True)
        self._date_combo.clear()
        self._date_combo.addItem("Сегодня", today)
        self._date_combo.addItem("Все (7 дней)", "ALL")
        for date in self._available_dates:
            if date == today:
                continue
            self._date_combo.addItem(self._display_date(date), date)
        index = self._date_combo.findData(self._date_filter)
        if index < 0:
            self._date_filter = today
            index = 0
        self._date_combo.setCurrentIndex(index)
        self._date_combo.blockSignals(False)

        self._tool_combo.blockSignals(True)
        self._tool_combo.clear()
        self._tool_combo.addItem("Все", "ALL")
        for tool in self._available_tools:
            self._tool_combo.addItem(tool, tool)
        tool_index = self._tool_combo.findData(self._tool_filter)
        if tool_index < 0:
            self._tool_filter = "ALL"
            tool_index = 0
        self._tool_combo.setCurrentIndex(tool_index)
        self._tool_combo.blockSignals(False)

    @staticmethod
    def _display_date(iso_date: str) -> str:
        try:
            return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            return iso_date

    def _on_date_filter_changed(self, _index: int) -> None:
        self._date_filter = self._date_combo.currentData() or "ALL"
        self._rebuild_view()

    def _on_tool_filter_changed(self, _index: int) -> None:
        self._tool_filter = self._tool_combo.currentData() or "ALL"
        self._rebuild_view()

    def _on_level_filter_changed(self, _index: int) -> None:
        self._level_filter = self._level_combo.currentData() or "ALL"
        self._rebuild_view()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self._rebuild_view()

    def _matches(self, entry: dict[str, Any]) -> bool:
        if self._date_filter != "ALL" and str(entry.get("date", "")) != self._date_filter:
            return False

        tool = str(entry.get("tool", "System"))
        if self._tool_filter != "ALL" and tool != self._tool_filter:
            return False

        level = str(entry.get("level", "INFO"))
        if self._level_filter == "WARN" and level not in ("WARNING", "ERROR", "CRITICAL"):
            return False
        if self._level_filter == "ERROR" and level not in ("ERROR", "CRITICAL"):
            return False

        if self._search_text:
            haystack = " ".join(
                str(entry.get(key, "")) for key in ("time", "ts", "level", "logger", "tool", "message")
            ).lower()
            if self._search_text not in haystack:
                return False

        return True

    def _trim_entries(self) -> None:
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]

    # ==================== ЖИВЫЕ ЗАПИСИ ====================

    def add_entry(self, entry: dict[str, Any]) -> None:
        if not entry.get("time"):
            entry["time"] = str(entry.get("ts", ""))[11:19]

        self._entries.append(entry)
        self._trim_entries()

        date = str(entry.get("date", "") or "")
        tool = str(entry.get("tool", "") or "")
        need_refresh = False
        if date and date not in self._available_dates:
            self._available_dates = sorted(set(self._available_dates) | {date}, reverse=True)
            need_refresh = True
        if tool and tool not in self._available_tools:
            need_refresh = True
        if need_refresh:
            self._refresh_available_values()
            self._populate_filter_options()

        if self._matches(entry):
            self._append_entry(entry)
            if self._auto_scroll_cb.isChecked():
                self._scroll_to_bottom()

        self._emit_counts()

    # ==================== ОТРИСОВКА ====================

    def _format_date_header_html(self, iso_date: str) -> str:
        label = html.escape(self._display_date(iso_date))
        return (
            f'<span style="background-color:#E2E8F0; color:#334155; font-weight:700;">'
            f'&nbsp; {label} &nbsp;</span>'
        )

    def _format_entry_html(self, entry: dict[str, Any]) -> str:
        time_str = html.escape(str(entry.get("time") or str(entry.get("ts", ""))[11:19]))
        tool = str(entry.get("tool", "System"))
        level = str(entry.get("level", "INFO"))
        logger_name = html.escape(str(entry.get("logger", "")))
        message = html.escape(str(entry.get("message", "")))

        level_label = _LEVEL_LABELS.get(level, level)
        tool_colors = _TOOL_COLORS.get(tool, ("#E2E8F0", "#334155"))
        level_colors = _LEVEL_COLORS.get(level, ("#E2E8F0", "#334155"))
        message_color = "#991B1B" if level in ("ERROR", "CRITICAL") else (
            "#92400E" if level == "WARNING" else "#0F172A"
        )

        return (
            f'<span style="color:#94A3B8; font-size:10.5px;">{time_str}</span> '
            f'{_badge(tool, tool_colors)} {_badge(level_label, level_colors)} '
            f'<span style="color:#64748B; font-size:10.5px;">{logger_name}</span> '
            f'<span style="color:{message_color};">{message}</span>'
        )

    def _format_run_start_html(self, entry: dict[str, Any]) -> str:
        time_str = html.escape(str(entry.get("time") or str(entry.get("ts", ""))[11:19]))
        tool = html.escape(str(entry.get("tool", "System")))
        message = html.escape(str(entry.get("message", "")))
        rule = '<span style="color:#BFDBFE;">' + ("─" * 60) + "</span>"
        return (
            f"{rule}<br/>"
            f'<span style="background-color:#EFF6FF; color:#1D4ED8; font-weight:700;">'
            f"▶ {message} · {tool} · {time_str}</span><br/>"
            f"{rule}"
        )

    def _append_entry(self, entry: dict[str, Any]) -> None:
        if self._date_filter == "ALL":
            date = str(entry.get("date", ""))
            if date != self._last_rendered_date:
                if self._last_rendered_date:
                    self._view.appendHtml("")
                self._view.appendHtml(self._format_date_header_html(date))
                self._last_rendered_date = date

        if entry.get("event") == "run_start":
            self._view.appendHtml(self._format_run_start_html(entry))
        else:
            self._view.appendHtml(self._format_entry_html(entry))

    def _rebuild_view(self) -> None:
        self._view.clear()
        self._last_rendered_date = ""

        matched = [entry for entry in self._entries if self._matches(entry)]
        if not matched:
            self._view.appendHtml(
                '<span style="color:#94A3B8;">Нет записей по текущему фильтру</span>'
            )
            return

        for entry in matched:
            self._append_entry(entry)

        if self._auto_scroll_cb.isChecked():
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        sb = self._view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    # ==================== СЧЁТЧИКИ И ДЕЙСТВИЯ ====================

    def _emit_counts(self) -> None:
        self._count_lbl.setText(str(len(self._entries)))

        session_entries = [entry for entry in self._entries if entry.get("session_id") == self._session_id]
        total = len(session_entries)
        warns = sum(1 for entry in session_entries if entry.get("level") == "WARNING")
        errs = sum(
            1 for entry in session_entries if entry.get("level") in ("ERROR", "CRITICAL")
        )
        self.counts_changed.emit(total, warns, errs)

    def _copy_visible_logs(self) -> None:
        visible = [entry for entry in self._entries if self._matches(entry)]
        if not visible:
            self.copied.emit("Логи пусты")
            return

        lines: list[str] = []
        for entry in visible:
            lines.append(
                f"{entry.get('date', '')} {entry.get('time', '')} | "
                f"{str(entry.get('level', 'INFO')):<7} | "
                f"{str(entry.get('tool', 'System')):<10} | "
                f"{entry.get('logger', '')} | {entry.get('message', '')}"
            )
        copy_to_clipboard("\n".join(lines))
        self.copied.emit(f"✓ Скопировано записей: {len(visible)}")

    def _open_logs_dir(self) -> None:
        try:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(self._logs_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self._logs_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self._logs_dir)])
            self.copied.emit(f"Открыта папка логов: {self._logs_dir}")
        except Exception as exc:
            self.copied.emit(f"Не удалось открыть папку логов: {exc}")
