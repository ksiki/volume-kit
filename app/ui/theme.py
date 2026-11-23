FONT_FAMILY_UI = "Segoe UI"
FONT_FAMILY_MONO = "Cascadia Code"

PALETTE = {
    "bg": "#F4F6F9",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "surface_hover": "#F1F5F9",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "text": "#0F172A",
    "text_secondary": "#475569",
    "text_muted": "#64748B",
    "accent": "#2563EB",
    "accent_hover": "#1D4ED8",
    "accent_soft": "#EFF6FF",
    "accent_border": "#BFDBFE",
    "spot": "#D97706",
    "fut": "#0284C7",
    "ok": "#059669",
    "warn": "#D97706",
    "err": "#DC2626",
}

APP_STYLESHEET = f"""
/* ==================== БАЗОВЫЕ СТИЛИ ==================== */
QMainWindow, QWidget {{
    color: {PALETTE["text"]};
    font-family: "{FONT_FAMILY_UI}", "Inter", "Arial", sans-serif;
    font-size: 13px;
}}

QMainWindow {{
    background-color: {PALETTE["bg"]};
}}

QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

/* Скроллбары */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: #CBD5E1;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #94A3B8;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    height: 0px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0 2px;
}}
QScrollBar::handle:horizontal {{
    background: #CBD5E1;
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #94A3B8;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
    width: 0px;
}}

QToolTip {{
    background-color: #0F172A;
    color: #F8FAFC;
    border: 1px solid #1E293B;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
}}

/* ==================== БОКОВАЯ ПАНЕЛЬ ==================== */
QFrame#Sidebar {{
    background-color: {PALETTE["surface"]};
    border-right: 1px solid {PALETTE["border"]};
}}

QLabel#BrandTitle {{
    font-size: 16px;
    font-weight: 800;
    color: {PALETTE["text"]};
}}

QLabel#BrandSubtitle {{
    font-size: 11px;
    font-weight: 500;
    color: {PALETTE["text_muted"]};
}}

QLabel#NavSectionLabel {{
    font-size: 10px;
    font-weight: 700;
    color: {PALETTE["text_muted"]};
    padding: 10px 10px 4px 10px;
    letter-spacing: 0.8px;
}}

QPushButton#NavButton {{
    text-align: left;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid transparent;
    background-color: transparent;
    color: {PALETTE["text_secondary"]};
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#NavButton:hover {{
    background-color: {PALETTE["surface_hover"]};
    color: {PALETTE["text"]};
}}
QPushButton#NavButton[active="true"] {{
    background-color: {PALETTE["accent_soft"]};
    border: 1px solid {PALETTE["accent_border"]};
    color: {PALETTE["accent_hover"]};
    font-weight: 700;
}}

QFrame#SidebarDivider {{
    background-color: {PALETTE["border"]};
    max-height: 1px;
    min-height: 1px;
}}

/* ==================== ВЕРХНЯЯ ПАНЕЛЬ ==================== */
QFrame#TopBar {{
    background-color: {PALETTE["surface"]};
    border-bottom: 1px solid {PALETTE["border"]};
}}

QLabel#PageTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {PALETTE["text"]};
}}

QLabel#SymbolPill {{
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #0F172A;
    border-radius: 7px;
    padding: 0 11px;
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.4px;
}}

QFrame#SpotPricePill {{
    background-color: {PALETTE["surface_alt"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 7px;
}}

QLabel#SpotPriceTag {{
    color: {PALETTE["text_muted"]};
    font-size: 11.5px;
    font-weight: 600;
}}

QLabel#SpotPriceValue {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12.5px;
    font-weight: 700;
    color: {PALETTE["text"]};
}}
QLabel#SpotPriceValue[direction="up"] {{
    color: {PALETTE["ok"]};
}}
QLabel#SpotPriceValue[direction="down"] {{
    color: {PALETTE["err"]};
}}

QLabel#RefPriceChip {{
    background-color: {PALETTE["surface_alt"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 6px;
    padding: 3px 9px;
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 11.5px;
    font-weight: 600;
    color: {PALETTE["text_secondary"]};
}}

QFrame#CalibControls {{
    background-color: #FFFFFF;
    border: 1px solid {PALETTE["border"]};
    border-radius: 8px;
}}
QLabel#CalibStatusLabel {{
    color: {PALETTE["text_secondary"]};
    font-size: 12.5px;
    font-weight: 600;
}}
QLabel#CalibStatusLabel[status="running"] {{
    color: #1D4ED8;
}}
QLabel#CalibStatusLabel[status="done"] {{
    color: #15803D;
}}
QLabel#CalibStatusLabel[status="error"] {{
    color: #B91C1C;
}}

/* ==================== КНОПКИ ==================== */
QPushButton#PrimaryBtn {{
    background-color: {PALETTE["accent"]};
    color: #FFFFFF;
    border: 1px solid {PALETTE["accent"]};
    border-radius: 7px;
    padding: 5px 13px;
    font-size: 12.5px;
    font-weight: 600;
    min-height: 20px;
}}
QPushButton#PrimaryBtn:hover {{
    background-color: {PALETTE["accent_hover"]};
    border-color: {PALETTE["accent_hover"]};
}}
QPushButton#PrimaryBtn:disabled {{
    background-color: #93C5FD;
    border-color: #93C5FD;
    color: #EFF6FF;
}}

QPushButton#SecondaryBtn {{
    background-color: {PALETTE["surface"]};
    color: {PALETTE["text"]};
    border: 1px solid {PALETTE["border_strong"]};
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 12.5px;
    font-weight: 600;
    min-height: 20px;
}}
QPushButton#SecondaryBtn:hover {{
    background-color: {PALETTE["surface_hover"]};
    border-color: #94A3B8;
}}
QPushButton#SecondaryBtn:disabled {{
    color: #94A3B8;
    background-color: {PALETTE["surface_alt"]};
}}

QPushButton#DangerBtn {{
    background-color: #FFFFFF;
    color: #B91C1C;
    border: 1px solid #FECACA;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 12.5px;
    font-weight: 600;
    min-height: 20px;
}}
QPushButton#DangerBtn:hover {{
    background-color: #FEF2F2;
    border-color: #F87171;
}}

QPushButton#FilterChip {{
    background-color: transparent;
    color: {PALETTE["text_secondary"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 6px;
    padding: 4px 9px;
    font-size: 11.5px;
    font-weight: 600;
}}
QPushButton#FilterChip:hover {{
    background-color: {PALETTE["surface_hover"]};
}}
QPushButton#FilterChip[active="true"] {{
    background-color: {PALETTE["accent_soft"]};
    border-color: {PALETTE["accent_border"]};
    color: {PALETTE["accent_hover"]};
}}

QProgressBar#ThinProgress {{
    background-color: #E2E8F0;
    border: none;
    border-radius: 3px;
    max-height: 5px;
    min-height: 5px;
}}
QProgressBar#ThinProgress::chunk {{
    background-color: {PALETTE["accent"]};
    border-radius: 3px;
}}

/* ==================== КАРТОЧКИ CALIBRATOR ==================== */
QFrame#Card {{
    background-color: {PALETTE["surface"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 12px;
}}

QFrame#CardHeader {{
    background-color: {PALETTE["surface_alt"]};
    border-bottom: 1px solid {PALETTE["border"]};
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}}

QLabel#CardTitle {{
    font-size: 16px;
    font-weight: 800;
    color: {PALETTE["text"]};
}}

QLabel#SectionLabel {{
    font-size: 11px;
    font-weight: 700;
    color: {PALETTE["text_muted"]};
    letter-spacing: 0.7px;
}}

QFrame#ScaleTile {{
    background-color: {PALETTE["surface_alt"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 10px;
}}

QLabel#ExchangeName {{
    font-size: 13.5px;
    font-weight: 700;
    color: {PALETTE["text"]};
}}

QLabel#ScaleMeta {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 11.5px;
    color: {PALETTE["text_muted"]};
}}

/* Бейджи таймфреймов */
QLabel#TfBadge {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12px;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 6px;
    border: 1px solid #CBD5E1;
    background-color: #F1F5F9;
    color: #334155;
}}
QLabel#TfBadge[compact="true"] {{
    font-size: 11px;
    padding: 2px 6px;
}}
QLabel#TfBadge[tf="1h"] {{
    background-color: #EEF2FF;
    border-color: #C7D2FE;
    color: #4338CA;
}}
QLabel#TfBadge[tf="15m"] {{
    background-color: #E0F2FE;
    border-color: #BAE6FD;
    color: #0369A1;
}}
QLabel#TfBadge[tf="5m"] {{
    background-color: #FFF7ED;
    border-color: #FED7AA;
    color: #C2410C;
}}

/* Блоки Масштаб (кликабельный) и Шаг цены (информационный) */
QFrame#MetricBox {{
    border-radius: 9px;
}}
QFrame#MetricBox[variant="primary"] {{
    background-color: #FFFBEB;
    border: 1.5px solid #F59E0B;
}}
QFrame#MetricBox[variant="primary"][copyable="true"]:hover {{
    background-color: #FEF3C7;
    border: 1.5px solid #D97706;
}}
QFrame#MetricBox[variant="secondary"] {{
    background-color: #FFFFFF;
    border: 1px solid {PALETTE["border"]};
}}
QFrame#MetricBox[variant="secondary"][copyable="true"]:hover {{
    background-color: {PALETTE["surface_hover"]};
    border: 1px solid #94A3B8;
}}
QFrame#MetricBox[variant="secondary"][copyable="false"] {{
    background-color: #FFFFFF;
    border: 1px solid {PALETTE["border"]};
}}

QLabel#MetricLabelPrimary {{
    font-size: 10.5px;
    font-weight: 800;
    color: #B45309;
    letter-spacing: 0.6px;
}}
QLabel#MetricValuePrimary {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 23px;
    font-weight: 800;
    color: #78350F;
}}

QLabel#MetricLabelSecondary {{
    font-size: 10.5px;
    font-weight: 700;
    color: {PALETTE["text_muted"]};
    letter-spacing: 0.6px;
}}
QLabel#MetricValueSecondary {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 18px;
    font-weight: 700;
    color: #334155;
}}

/* Таблица фильтров ClusterSearch */
QLabel#TableHead {{
    font-size: 10.5px;
    font-weight: 700;
    color: {PALETTE["text_muted"]};
    letter-spacing: 0.6px;
}}

QFrame#FilterRow {{
    background-color: {PALETTE["surface_alt"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 8px;
}}
QFrame#FilterRow:hover {{
    background-color: {PALETTE["surface_hover"]};
    border-color: {PALETTE["border_strong"]};
}}

QLabel#FilterMetricName {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12.5px;
    font-weight: 600;
    color: {PALETTE["text_secondary"]};
}}

QLabel#FilterUsd {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12.5px;
    font-weight: 700;
    color: {PALETTE["text"]};
}}

QLabel#FilterPos {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 11.5px;
    color: {PALETTE["text_muted"]};
}}

QPushButton#ThresholdPill {{
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 14.5px;
    font-weight: 800;
    padding: 5px 12px;
    border-radius: 7px;
    min-width: 96px;
    border: 1px solid {PALETTE["border_strong"]};
    background-color: #FFFFFF;
    color: {PALETTE["text"]};
}}
QPushButton#ThresholdPill[metricType="vol_1"] {{
    background-color: #ECFDF5;
    border: 1px solid #6EE7B7;
    color: #047857;
}}
QPushButton#ThresholdPill[metricType="vol_1"]:hover {{
    background-color: #D1FAE5;
    border-color: #10B981;
}}
QPushButton#ThresholdPill[metricType="vol_3"] {{
    background-color: #F0F9FF;
    border: 1px solid #7DD3FC;
    color: #0369A1;
}}
QPushButton#ThresholdPill[metricType="vol_3"]:hover {{
    background-color: #E0F2FE;
    border-color: #0EA5E9;
}}
QPushButton#ThresholdPill[metricType="delta_1"] {{
    background-color: #FAF5FF;
    border: 1px solid #D8B4FE;
    color: #7E22CE;
}}
QPushButton#ThresholdPill[metricType="delta_1"]:hover {{
    background-color: #F3E8FF;
    border-color: #A855F7;
}}

/* ==================== ОШИБКИ И ПОДСКАЗКИ ==================== */
QFrame#ErrorBanner {{
    background-color: #FEF2F2;
    border: 1px solid #FECACA;
    border-radius: 10px;
}}

QLabel#ErrorText {{
    color: #991B1B;
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12px;
}}

QLabel#FooterHint {{
    color: {PALETTE["text_muted"]};
    font-size: 12px;
}}

/* ==================== ПАНЕЛЬ ЛОГОВ ==================== */
QFrame#LogsDrawer {{
    background-color: {PALETTE["surface"]};
    border-left: 1px solid {PALETTE["border"]};
}}

QPlainTextEdit#LogView {{
    background-color: #F8FAFC;
    border: 1px solid {PALETTE["border"]};
    border-radius: 8px;
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 11.5px;
    color: {PALETTE["text"]};
    padding: 6px;
}}

/* ==================== ПОЛЯ ВВОДА И НАСТРОЙКИ ==================== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: #FFFFFF;
    color: {PALETTE["text"]};
    border: 1px solid {PALETTE["border_strong"]};
    border-radius: 7px;
    padding: 6px 10px;
    min-height: 20px;
    font-size: 13px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1.5px solid {PALETTE["accent"]};
    background-color: #FFFFFF;
}}
QComboBox {{
    combobox-popup: 0;
    padding-right: 26px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    background-color: transparent;
}}
QComboBox QAbstractItemView {{
    background-color: #FFFFFF;
    color: {PALETTE["text"]};
    border: 1px solid {PALETTE["border_strong"]};
    border-radius: 7px;
    padding: 4px;
    outline: 0;
    selection-background-color: {PALETTE["accent_soft"]};
    selection-color: {PALETTE["accent_hover"]};
}}
QComboBox QAbstractItemView::item {{
    min-height: 24px;
    padding: 4px 8px;
    color: {PALETTE["text"]};
    background-color: transparent;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {PALETTE["surface_hover"]};
    color: {PALETTE["text"]};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {PALETTE["accent_soft"]};
    color: {PALETTE["accent_hover"]};
}}

QMenu {{
    background-color: #FFFFFF;
    color: {PALETTE["text"]};
    border: 1px solid {PALETTE["border_strong"]};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 18px;
    border-radius: 5px;
    background-color: transparent;
}}
QMenu::item:selected {{
    background-color: {PALETTE["accent_soft"]};
    color: {PALETTE["accent_hover"]};
}}
QMenu::separator {{
    height: 1px;
    background: {PALETTE["border"]};
    margin: 4px 8px;
}}

QCheckBox {{
    spacing: 8px;
    font-weight: 500;
    color: {PALETTE["text"]};
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 4px;
    border: 1px solid {PALETTE["border_strong"]};
    background-color: #FFFFFF;
}}
QCheckBox::indicator:checked {{
    background-color: {PALETTE["accent"]};
    border-color: {PALETTE["accent"]};
}}

QTableWidget {{
    background-color: #FFFFFF;
    alternate-background-color: {PALETTE["surface_alt"]};
    border: 1px solid {PALETTE["border"]};
    border-radius: 8px;
    gridline-color: {PALETTE["border"]};
    selection-background-color: {PALETTE["accent_soft"]};
    selection-color: {PALETTE["text"]};
}}
QHeaderView::section {{
    background-color: {PALETTE["surface_alt"]};
    color: {PALETTE["text_secondary"]};
    font-size: 11.5px;
    font-weight: 700;
    border: none;
    border-bottom: 1px solid {PALETTE["border"]};
    border-right: 1px solid {PALETTE["border"]};
    padding: 7px 10px;
}}

QLabel#SettingsSectionTitle {{
    font-size: 14px;
    font-weight: 700;
    color: {PALETTE["text"]};
}}

QLabel#SettingsHint {{
    font-size: 12px;
    color: {PALETTE["text_muted"]};
}}

/* ==================== ВСТРОЕННЫЕ ВЕБ-ИНСТРУМЕНТЫ ==================== */
QFrame#WebToolbar {{
    background-color: {PALETTE["surface"]};
    border-bottom: 1px solid {PALETTE["border"]};
}}

/* ==================== TOAST УВЕДОМЛЕНИЕ ==================== */
QLabel#ToastNotification {{
    background-color: #0F172A;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 9px 16px;
    font-family: "{FONT_FAMILY_MONO}", "Consolas", monospace;
    font-size: 12.5px;
    font-weight: 600;
}}
"""