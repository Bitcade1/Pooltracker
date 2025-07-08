#!/usr/bin/env python3

"""
Pool Table Inventory Tracker
Desktop client for the Pool Table Tracker API

Shows production data, inventory, assembly deficits, and a top rail dashboard.
Modern UI with monthly data selection for production.
Optimized to fetch monthly production data in a single API call.
Colors table finish boxes in Assembly Capacity tab.
"""
import sys
import os
import warnings
import requests
from datetime import datetime, timedelta, date
import json
import calendar
import re

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox, QFormLayout,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QComboBox, QCheckBox, QProgressBar, QFrame,
    QSizePolicy, QSpacerItem, QGridLayout, QStackedWidget, QScrollArea
)
from PyQt5.QtGui import QFont, QColor, QPalette, QBrush, QIcon, QIntValidator, QPixmap
from PyQt5.QtCore import Qt, QTimer

from LoadingScreen import LoadingScreen  # Import the LoadingScreen class

# --- Modern UI Styling ---
STYLESHEET = """
QMainWindow {
    background-color: #f0f2f5; 
}
QGroupBox {
    font-size: 10pt;
    font-weight: bold;
    color: #333; /* Default title color, may need adjustment if background is too dark */
    border: 1px solid #d0d0d0;
    border-radius: 8px;
    margin-top: 10px;
    background-color: #ffffff; 
    padding: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px 0 5px;
    left: 10px;
    color: #2c3e50; 
}
QLabel {
    font-size: 9pt;
    color: #444;
}
QPushButton {
    font-size: 9pt;
    background-color: #3498db; 
    color: white;
    border: none;
    padding: 6px 12px;
    border-radius: 5px;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #2980b9; 
}
QPushButton:pressed {
    background-color: #1f618d;
}
QLineEdit, QComboBox {
    font-size: 9pt;
    padding: 5px;
    border: 1px solid #ccc;
    border-radius: 5px;
    background-color: #fdfdfd;
}
QComboBox::drop-down {
    border: none;
}
QComboBox::down-arrow {
    image: url(noexist.png); 
    width: 12px;
    height: 12px;
    margin-right: 5px;
}
QTableWidget {
    font-size: 8pt;
    border: 1px solid #e0e0e0;
    border-radius: 5px;
    gridline-color: #e0e0e0;
    background-color: #ffffff;
    alternate-background-color: #f9f9f9;
}
QHeaderView::section {
    background-color: #e9edf0; 
    padding: 4px;
    border: none;
    border-bottom: 1px solid #d0d0d0;
    font-size: 9pt;
    font-weight: bold;
    color: #333;
}
QTabWidget::pane {
    border: 1px solid #d0d0d0;
    border-top: none;
    border-radius: 0 0 8px 8px;
    background-color: #ffffff;
    padding: 10px;
}
QTabBar::tab {
    background-color: #e9edf0;
    color: #555;
    padding: 8px 15px;
    border: 1px solid #d0d0d0;
    border-bottom: none; 
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #ffffff; 
    color: #3498db; 
    font-weight: bold;
    border-bottom: 1px solid #ffffff; 
}
QTabBar::tab:hover {
    background-color: #f5f7fa;
}
QStatusBar {
    font-size: 9pt;
    color: #555;
}
QProgressBar {
    border: 1px solid #ccc;
    border-radius: 5px;
    text-align: center;
    font-size: 8pt;
    color: #333;
}
QGroupBox#BodiesGroup { background-color: #e0f7fa; border-left: 5px solid #0288d1; }
QGroupBox#PodsGroup { background-color: #e8f5e9; border-left: 5px solid #388e3c; }
QGroupBox#RailsGroup { background-color: #fff3e0; border-left: 5px solid #f57c00; }

QLabel#BodiesCountLabel { color: #01579b; }
QLabel#PodsCountLabel { color: #1b5e20; }
QLabel#RailsCountLabel { color: #e65100; }

QLabel#ApiStatusLabel { font-weight: bold; padding: 5px; border-radius: 4px; }
QLabel#ApiStatusLabel[status="connected"] { background-color: #c8e6c9; color: #2e7d32; }
QLabel#ApiStatusLabel[status="disconnected"] { background-color: #ffcdd2; color: #c62828; }
QLabel#ApiStatusLabel[status="checking"] { background-color: #fff9c4; color: #f57f17; }

/* Assembly Deficit Tab Styling */
QLabel.StatusNeeded { font-size: 9pt; font-weight: bold; color: #c62828; } /* Red for "needed" status */
QLabel.StatusCanAssemble { font-size: 9pt; font-weight: bold; color: #2e7d32; } /* Green for "can assemble" */
QLabel.StatusNeutral { font-size: 9pt; color: #555; } /* Neutral for no components */
QLabel.StockValue { font-size: 9pt; }
QLabel.StockValueShortage { font-size: 9pt; color: #c62828; font-weight: bold; } /* Red for stock value that is a bottleneck */

/* Top Rail Dashboard Styling */
QWidget#DashboardPage {
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 5px;
}
QLabel.DashboardHeader {
    font-size: 22pt;  /* Increased from 16pt */
    font-weight: bold;
    color: #3498db;
    padding-bottom: 10px;
    border-bottom: 2px solid #3498db;
    margin-bottom: 15px;
}
QLabel.DashboardMetricLabel {
    font-size: 14pt;  /* Increased from 12pt */
    color: #2c3e50;
}
QLabel.DashboardMetricValue {
    font-size: 26pt;  /* Increased from 18pt */
    font-weight: bold;
    color: #2980b9; /* Medium Blue */
}
QLabel.DashboardPartName {
    font-size: 11pt;
}
QLabel.DashboardPartStock {
    font-size: 11pt;
    font-weight: bold;
}
QLabel.DashboardPartStockLow {
    font-size: 11pt;
    font-weight: bold;
    color: #c62828; /* Red for low stock */
}
"""

# Configuration
DEFAULT_CONFIG = {
    "API_URL": "https://pooltabletracker.com",
    "API_PORT": None,
    "API_TOKEN": "bitcade_api_key_1",
    "SCROLL_TIMER": 10,  # Default 10 seconds
    "DEFAULT_TAB": 0  # Default to first tab
}

def save_config(config):
    config_file = os.path.join(os.path.expanduser("~"), ".pool_tracker_config.json")
    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)
    return config_file

def load_config():
    config_file = os.path.join(os.path.expanduser("~"), ".pool_tracker_config.json")
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                return json.load(f)
        except Exception:
            print(f"Warning: Could not load config file {config_file}. Using defaults.")
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

class APIClient:
    def __init__(self, api_url, api_token, api_port=None):
        self.api_url = api_url
        self.api_port = api_port
        self.headers = {"X-API-Token": api_token}
        
        if api_port:
            base_url_str = str(self.api_url)
            # Ensure protocol is present
            if not base_url_str.startswith(("http://", "https://")):
                base_url_str = "http://" + base_url_str # Default to http if not specified

            # Split URL and safely add port
            # Example: "http://domain.com/path" or "domain.com/path"
            parts = base_url_str.split("/")
            if len(parts) > 2: # Check if there's a host part (e.g. parts[2] for "http://host/...")
                host_part = parts[2]
                if ":" in host_part: # Already has a port
                    host = host_part.split(":")[0]
                    parts[2] = f"{host}:{api_port}"
                else: # No port yet
                    parts[2] = f"{host_part}:{api_port}"
                self.base_url = "/".join(parts)
            else: # Handle cases like "domain.com" (less likely for API but good to be robust)
                 # This logic might need adjustment if URL format is very different
                if ":" in base_url_str:
                     host = base_url_str.split(":")[0]
                     self.base_url = f"{host}:{api_port}"
                else:
                     self.base_url = f"{base_url_str}:{api_port}"

        else: # No custom port, use URL as is
            self.base_url = str(self.api_url)
            if not self.base_url.startswith(("http://", "https://")):
                 self.base_url = "http://" + self.base_url # Default to http

    def test_connection(self):
        try:
            response = requests.get(f"{self.base_url}/api/status", headers=self.headers, timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"Connection error: {e}")
            return False
            
    def get_production_for_month(self, year, month):
        endpoint_url = f"{self.base_url}/api/work/monthly/{year}/{month}"
        print(f"Fetching monthly production data for {year}-{month:02d} from: {endpoint_url}")
        try:
            response = requests.get(endpoint_url, headers=self.headers, timeout=15)
            if response.status_code == 200:
                return response.json() 
            else:
                error_msg = f"API Error {response.status_code} for {year}-{month:02d}: {response.text[:100]}"
                print(error_msg)
                num_days_in_month = calendar.monthrange(year, month)[1]
                return [{
                    "date": date(year, month, day_num).strftime("%Y-%m-%d"),
                    "bodies": 0, "pods": 0, "top_rails": 0, "error_info": error_msg 
                } for day_num in range(1, num_days_in_month + 1)]
        except requests.exceptions.RequestException as e:
            error_msg = f"Request Exception for {year}-{month:02d}: {e}"
            print(error_msg)
            num_days_in_month = calendar.monthrange(year, month)[1]
            return [{
                "date": date(year, month, day_num).strftime("%Y-%m-%d"),
                "bodies": 0, "pods": 0, "top_rails": 0, "error_info": error_msg
            } for day_num in range(1, num_days_in_month + 1)]

    def get_inventory_summary(self):
        try:
            response = requests.get(f"{self.base_url}/api/inventory/summary", headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            print(f"Inventory API error: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            print(f"Error getting inventory data: {e}")
            return None

    def get_production_summary(self, year, month): 
        endpoint_url = f"{self.base_url}/api/production/summary/{year}/{month}"
        print(f"Fetching production summary for {year}-{month:02d} from: {endpoint_url}")
        try:
            response = requests.get(endpoint_url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Production Summary API Error {response.status_code} for {year}-{month:02d}: {response.text[:100]}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Request Exception for Production Summary ({year}-{month:02d}): {e}")
            return None


class MainWindow(QMainWindow):
    # Get absolute path for images
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    TABLE_FINISH_COLORS = {
        "Black": os.path.abspath(os.path.join(BASE_DIR, "images", "Black Oak.jpg")),
        "Rustic Oak": os.path.abspath(os.path.join(BASE_DIR, "images", "Rustic Oak.jpg")), 
        "Grey Oak": os.path.abspath(os.path.join(BASE_DIR, "images", "Grey Oak.jpg")),
        "Stone": os.path.abspath(os.path.join(BASE_DIR, "images", "Stone.jpg")),
        "Default": "#E0E0E0"  # Fallback color if image not found
    }

    BODY_PARTS_COMMON = {
        "Paddle": 1, "Laminate": 4, "Spring Mount": 1, "Spring Holder": 1,
        "Small Ramp": 1, "Bushing": 2, "Table legs": 4,
        "Ball Gullies 1 (Untouched)": 2, "Ball Gullies 2": 1, "Ball Gullies 3": 1,
        "Ball Gullies 4": 1, "Ball Gullies 5": 1, "Feet": 4, "Triangle trim": 1,
        "White ball return trim": 1, "Color ball trim": 1, "Ball window trim": 1,
        "Aluminum corner": 4, "Ramp 170mm": 1, "Ramp 158mm": 1, "Ramp 918mm": 1,
        "Ramp 376mm": 1, "Chrome handles": 1, "Sticker Set": 1,
        "4.8x16mm Self Tapping Screw": 37, "4.0 x 50mm Wood Screw": 4,
        "Plastic Window": 1, "Latch": 12, "Spring": 1, "Handle Tube": 1
    }
    BODY_PARTS_7FT = {**BODY_PARTS_COMMON, "Large Ramp": 1, "Cue Ball Separator": 1}
    BODY_PARTS_6FT = {**BODY_PARTS_COMMON, "6ft Large Ramp": 1, "6ft Cue Ball Separator": 1}

    def __init__(self, config=None):
        super().__init__()
        # Suppress specific warnings (adjust as needed)
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        warnings.filterwarnings("ignore", category=PendingDeprecationWarning)

        # Initialize timers first
        self.refresh_timer = QTimer(self)
        self.dashboard_scroll_timer = QTimer(self)

        self.config = config if config else load_config()
        self.api_client = APIClient(
            self.config.get("API_URL", DEFAULT_CONFIG["API_URL"]),
            self.config.get("API_TOKEN", DEFAULT_CONFIG["API_TOKEN"]),
            self.config.get("API_PORT") 
        )
        
        self.inventory_data = None 
        
        self.table_configurations = [
            {"name": "7ft Black", "size": "7ft", "color_display": "Black", "body_key": "body_7ft_black", "rail_key": "top_rail_7ft_black"},
            {"name": "7ft Rustic Oak", "size": "7ft", "color_display": "Rustic Oak", "body_key": "body_7ft_rustic_oak", "rail_key": "top_rail_7ft_rustic_oak"},
            {"name": "7ft Grey Oak", "size": "7ft", "color_display": "Grey Oak", "body_key": "body_7ft_grey_oak", "rail_key": "top_rail_7ft_grey_oak"},
            {"name": "7ft Stone", "size": "7ft", "color_display": "Stone", "body_key": "body_7ft_stone", "rail_key": "top_rail_7ft_stone"},
            {"name": "6ft Black", "size": "6ft", "color_display": "Black", "body_key": "body_6ft_black", "rail_key": "top_rail_6ft_black"},
            {"name": "6ft Rustic Oak", "size": "6ft", "color_display": "Rustic Oak", "body_key": "body_6ft_rustic_oak", "rail_key": "top_rail_6ft_rustic_oak"},
            {"name": "6ft Grey Oak", "size": "6ft", "color_display": "Grey Oak", "body_key": "body_6ft_grey_oak", "rail_key": "top_rail_6ft_grey_oak"},
            {"name": "6ft Stone", "size": "6ft", "color_display": "Stone", "body_key": "body_6ft_stone", "rail_key": "top_rail_6ft_stone"},
        ]
        self.assembly_deficit_widgets = {} 
        self.top_rail_dashboard_widgets = {} # For the new dashboard

        # Add full screen warning widget
        self.low_stock_warning = QLabel()
        self.low_stock_warning.setStyleSheet("""
            font-size: 72pt;
            font-weight: bold;
            color: white;
            background-color: #c62828;
            padding: 50px;
            border-radius: 10px;
        """)
        self.low_stock_warning.setAlignment(Qt.AlignCenter)
        self.low_stock_warning.setWordWrap(True)
        self.low_stock_warning.hide()

        self.setup_ui()
        self.setStyleSheet(STYLESHEET) 
        
        # Set default tab if configured
        default_tab = self.config.get("DEFAULT_TAB", 0)
        if hasattr(self, 'tabs'):
            self.tabs.setCurrentIndex(default_tab)
            
        self.check_api_connection() 

        # Configure timers
        self.refresh_timer.timeout.connect(self.refresh_all_data)
        self.refresh_timer.start(60000)  # 1 minute

        self.dashboard_scroll_timer.timeout.connect(self.scroll_dashboard_page)
        scroll_time = self.config.get("SCROLL_TIMER", 10)
        self.dashboard_scroll_timer.start(scroll_time * 1000)

    def setup_ui(self):
        self.setWindowTitle("Pool Table Factory Tracker")
        # Remove fixed geometry and add fullscreen
        self.showMaximized()  # Makes window full screen while preserving taskbar
        # Alternative: self.showFullScreen()  # Complete full screen (hides taskbar)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Wrap entire content in a scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(10)
        # Reduce top margin to reclaim space from header
        content_layout.setContentsMargins(10, 0, 10, 10)

        # Add tabs with proper sizing
        self.tabs = QTabWidget()
        # Set larger font for all tabs
        tab_font = QFont()
        tab_font.setPointSize(10)  # Increase tab font size
        self.tabs.setFont(tab_font)  # Apply the larger font to tabs
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout.addWidget(self.tabs, 1)  # Give tabs more stretch
        
        self.prod_tab = QWidget()
        self.tabs.addTab(self.prod_tab, "Monthly Production")
        self.setup_production_tab()

        self.assembly_deficit_tab = QWidget() 
        self.tabs.addTab(self.assembly_deficit_tab, "Assembly Capacity")
        self.setup_assembly_deficit_tab()

        self.top_rail_dashboard_tab = QWidget() # New Tab
        self.tabs.addTab(self.top_rail_dashboard_tab, "Top Rail Dashboard")
        self.setup_top_rail_dashboard_tab()
        
        self.body_build_dashboard_tab = QWidget()
        self.tabs.addTab(self.body_build_dashboard_tab, "Body Build Dashboard")
        self.setup_body_build_dashboard_tab()

        self.parts_tab = QWidget()
        self.tabs.addTab(self.parts_tab, "Inventory Parts")
        self.setup_parts_inventory_tab()
        
        settings_tab = QWidget()
        self.tabs.addTab(settings_tab, "Settings")
        self.setup_settings_tab(settings_tab)
        
        # Refresh button layout
        refresh_button_layout = QHBoxLayout()
        refresh_button_layout.addStretch()
        self.refresh_button = QPushButton("Refresh All Data") 
        self.refresh_button.setIcon(QIcon.fromTheme("view-refresh")) 
        self.refresh_button.clicked.connect(self.refresh_all_data)
        refresh_button_layout.addWidget(self.refresh_button)
        content_layout.addLayout(refresh_button_layout)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # --- Setup Status Bar ---
        self.connection_label = QLabel("API: Checking...")
        self.connection_label.setObjectName("ApiStatusLabel")
        self.connection_label.setProperty("status", "checking")
        self.statusBar().addPermanentWidget(self.connection_label)

        self.server_info_label = QLabel(f"Server: {self.api_client.base_url}")
        self.server_info_label.setStyleSheet("font-size: 8pt; color: #7f8c8d; padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.server_info_label)
        
        self.statusBar().showMessage("Ready")

    def setup_production_tab(self):
        prod_layout = QVBoxLayout(self.prod_tab)
        prod_layout.setSpacing(10)
        date_picker_group = QGroupBox("Select Month and Year")
        date_picker_layout = QHBoxLayout(date_picker_group)
        date_picker_layout.addWidget(QLabel("Year:"))
        self.prod_year_combo = QComboBox() 
        current_year = datetime.now().year
        for year_offset in range(-3, 3): 
            self.prod_year_combo.addItem(str(current_year + year_offset))
        self.prod_year_combo.setCurrentText(str(current_year))
        self.prod_year_combo.currentIndexChanged.connect(self.refresh_production_data)
        date_picker_layout.addWidget(self.prod_year_combo)
        date_picker_layout.addWidget(QLabel("Month:"))
        self.prod_month_combo = QComboBox()
        for month_num in range(1, 13):
            self.prod_month_combo.addItem(date(2000, month_num, 1).strftime("%B"), month_num) 
        self.prod_month_combo.setCurrentIndex(datetime.now().month - 1)
        self.prod_month_combo.currentIndexChanged.connect(self.refresh_production_data)
        date_picker_layout.addWidget(self.prod_month_combo)
        date_picker_layout.addStretch()
        prod_layout.addWidget(date_picker_group)
        summary_layout = QHBoxLayout(); summary_layout.setSpacing(10)
        self.bodies_count_label = QLabel("0"); self.bodies_count_label.setObjectName("BodiesCountLabel")
        self.bodies_count_label.setAlignment(Qt.AlignCenter); self.bodies_count_label.setStyleSheet("font-size: 24pt; font-weight: bold;")
        bodies_group = self.create_summary_group("Bodies (Selected Month)", self.bodies_count_label, "BodiesGroup")
        summary_layout.addWidget(bodies_group)
        self.pods_count_label = QLabel("0"); self.pods_count_label.setObjectName("PodsCountLabel")
        self.pods_count_label.setAlignment(Qt.AlignCenter); self.pods_count_label.setStyleSheet("font-size: 24pt; font-weight: bold;")
        pods_group = self.create_summary_group("Pods (Selected Month)", self.pods_count_label, "PodsGroup")
        summary_layout.addWidget(pods_group)
        self.rails_count_label = QLabel("0"); self.rails_count_label.setObjectName("RailsCountLabel")
        self.rails_count_label.setAlignment(Qt.AlignCenter); self.rails_count_label.setStyleSheet("font-size: 24pt; font-weight: bold;")
        rails_group = self.create_summary_group("Top Rails (Selected Month)", self.rails_count_label, "RailsGroup")
        summary_layout.addWidget(rails_group); prod_layout.addLayout(summary_layout)
        self.production_table = QTableWidget(); self.production_table.setColumnCount(4)
        self.production_table.setHorizontalHeaderLabels(["Date", "Bodies", "Pods", "Top Rails"])
        self.production_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.production_table.setAlternatingRowColors(True); prod_layout.addWidget(self.production_table)
        note_label = QLabel("Data for the selected month is fetched from the server. Days with no production or API errors will show 0.")
        note_label.setStyleSheet("color: #666; font-style: italic; font-size: 8pt;"); note_label.setAlignment(Qt.AlignCenter)
        note_label.setWordWrap(True); prod_layout.addWidget(note_label)

        # Add size policies to make tables stretch
        self.production_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setup_assembly_deficit_tab(self):
        main_tab_layout = QVBoxLayout(self.assembly_deficit_tab)
        main_tab_layout.setSpacing(10)
        info_label = QLabel(
            "This tab shows current stock for Bodies and Top Rails for each table type, "
            "and indicates what is needed to assemble more tables based on the component with the highest stock."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-size: 8pt; color: #555; margin-bottom: 5px; padding: 5px; background-color: #e9edf0; border-radius: 5px;")
        main_tab_layout.addWidget(info_label)
        
        content_layout = QHBoxLayout()
        group_7ft = QGroupBox("7ft Tables"); layout_7ft = QVBoxLayout(group_7ft)
        content_layout.addWidget(group_7ft)
        group_6ft = QGroupBox("6ft Tables"); layout_6ft = QVBoxLayout(group_6ft)
        content_layout.addWidget(group_6ft)
        main_tab_layout.addLayout(content_layout)
        main_tab_layout.addStretch()

        for config in self.table_configurations:
            config_name = config["name"]
            self.assembly_deficit_widgets[config_name] = {} 
            
            color_display_name = config["color_display"]
            color_group = QGroupBox(color_display_name) 
            color_layout = QFormLayout(color_group) 
            color_layout.setLabelAlignment(Qt.AlignRight)
            color_layout.setRowWrapPolicy(QFormLayout.WrapAllRows)

            # --- Apply background color to the QGroupBox ---
            hex_color_code = self.TABLE_FINISH_COLORS.get(color_display_name, self.TABLE_FINISH_COLORS["Default"])
            q_color = QColor(hex_color_code)
            
            color_group.setAutoFillBackground(True)
            palette = color_group.palette()
            palette.setColor(QPalette.Window, q_color) # QPalette.Window is the background for QWidget
            color_group.setPalette(palette)

            # --- Determine text color for contrast ---
            text_color_hex = 'black' if q_color.lightnessF() > 0.5 else 'white'
            label_stylesheet = f"color: {text_color_hex}; font-size: 9pt;" # Base style for labels in this box

            # --- Create and style labels ---
            body_field_label = QLabel("Body Stock:")
            body_field_label.setStyleSheet(label_stylesheet)
            body_stock_value_label = QLabel("N/A")
            body_stock_value_label.setObjectName(f"body_stock_{config_name.replace(' ', '_')}")
            body_stock_value_label.setStyleSheet(label_stylesheet) # Apply base text color
            self.assembly_deficit_widgets[config_name]["body_stock_value_label"] = body_stock_value_label
            color_layout.addRow(body_field_label, body_stock_value_label)

            rail_field_label = QLabel("Top Rail Stock:")
            rail_field_label.setStyleSheet(label_stylesheet)
            rail_stock_value_label = QLabel("N/A")
            rail_stock_value_label.setObjectName(f"rail_stock_{config_name.replace(' ', '_')}")
            rail_stock_value_label.setStyleSheet(label_stylesheet) # Apply base text color
            self.assembly_deficit_widgets[config_name]["rail_stock_value_label"] = rail_stock_value_label
            color_layout.addRow(rail_field_label, rail_stock_value_label)

            status_field_label = QLabel("Status:")
            status_field_label.setStyleSheet(label_stylesheet)
            status_label = QLabel("Calculating...")
            status_label.setWordWrap(True)
            status_label.setStyleSheet(label_stylesheet) # Apply base text color
            self.assembly_deficit_widgets[config_name]["status_label"] = status_label
            color_layout.addRow(status_field_label, status_label)
            
            if config["size"] == "7ft": layout_7ft.addWidget(color_group)
            else: layout_6ft.addWidget(color_group)
            
        layout_7ft.addStretch(); layout_6ft.addStretch()

    def setup_top_rail_dashboard_tab(self):
        """Sets up the UI for the Top Rail Dashboard tab."""
        main_layout = QVBoxLayout(self.top_rail_dashboard_tab)
        
        # Initialize stacked widget first
        self.dashboard_stacked_widget = QStackedWidget()
        main_layout.addWidget(self.dashboard_stacked_widget)
        
        # Add navigation controls at the top
        nav_layout = QHBoxLayout()
        nav_layout.addStretch()
        
        self.next_page_button = QPushButton("Next →")
        self.next_page_button.setFixedWidth(100)
        self.next_page_button.clicked.connect(self.manual_dashboard_scroll)
        nav_layout.addWidget(self.next_page_button)
        main_layout.addLayout(nav_layout)

        # Create warning section that will be shared across all pages
        self.tr_warning_section = QWidget()
        warning_layout = QVBoxLayout(self.tr_warning_section)
        warning_layout.setContentsMargins(20, 20, 20, 20)
        
        warning_header = QLabel("⚠️ LOW STOCK WARNING")
        warning_header.setStyleSheet("""
            font-size: 30pt;
            color: #c62828;
            padding: 15px;
            background-color: #ffebee;
            border-radius: 10px;
            font-weight: bold;
        """)
        warning_header.setAlignment(Qt.AlignCenter)
        warning_layout.addWidget(warning_header)
        
        self.tr_warning_text = QLabel()
        self.tr_warning_text.setStyleSheet("""
            font-size: 20pt;
            color: #c62828;
            padding: 15px;
        """)
        self.tr_warning_text.setAlignment(Qt.AlignCenter)
        self.tr_warning_text.setWordWrap(True)
        warning_layout.addWidget(self.tr_warning_text)
        
        self.tr_warning_section.hide()  # Hidden by default

        # Page 1: Performance
        page1 = QWidget(); page1.setObjectName("DashboardPage")
        layout1 = QVBoxLayout(page1)
        header1 = QLabel("Top Rail Performance"); header1.setObjectName("DashboardHeader"); header1.setAlignment(Qt.AlignCenter)
        layout1.addWidget(header1)
        
        # Current Performance
        current_perf_group = QGroupBox("Current Performance")
        current_perf_layout = QFormLayout()
        
        # Rest of the current performance metrics
        self.tr_dash_current_time_label = QLabel("N/A")
        self.tr_dash_current_time_label.setObjectName("DashboardMetricValue")
        current_perf_layout.addRow(QLabel("Time on Current Rail:", objectName="DashboardMetricLabel"),
                                 self.tr_dash_current_time_label)
        self.tr_dash_avg_time_label = QLabel("N/A"); self.tr_dash_avg_time_label.setObjectName("DashboardMetricValue")
        current_perf_layout.addRow(QLabel("Average Rail Time:", objectName="DashboardMetricLabel"), self.tr_dash_avg_time_label)
        self.tr_dash_predicted_label = QLabel("N/A"); self.tr_dash_predicted_label.setObjectName("DashboardMetricValue")
        current_perf_layout.addRow(QLabel("Predicted Today:", objectName="DashboardMetricLabel"), self.tr_dash_predicted_label)
        current_perf_group.setLayout(current_perf_layout)
        layout1.addWidget(current_perf_group)
        
        # Production Statistics - Make it bigger and bolder
        prod_stats_group = QGroupBox("Production Statistics")
        prod_stats_layout = QGridLayout()
        prod_stats_layout.setSpacing(20)  # Increase spacing between items
        prod_stats_layout.setContentsMargins(20, 30, 20, 30)  # Add more padding

        # Initialize labels first
        self.tr_dash_daily_label = QLabel("0")
        self.tr_dash_monthly_label = QLabel("0")
        self.tr_dash_yearly_label = QLabel("0") 

        # Today's Production
        today_label = QLabel("Today's Production")
        today_label.setStyleSheet("font-size: 14pt; color: #2c3e50; font-weight: bold;")
        today_label.setAlignment(Qt.AlignCenter)
        
        self.tr_dash_daily_label.setStyleSheet("""
            font-size: 40pt;
            font-weight: bold; 
            color: #2980b9;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 8px;
        """)
        self.tr_dash_daily_label.setAlignment(Qt.AlignCenter)
        prod_stats_layout.addWidget(today_label, 0, 0)
        prod_stats_layout.addWidget(self.tr_dash_daily_label, 1, 0)

        # Monthly Production
        month_label = QLabel("This Month")
        month_label.setStyleSheet("font-size: 14pt; color: #2c3e50; font-weight: bold;")
        month_label.setAlignment(Qt.AlignCenter)
        self.tr_dash_monthly_label.setStyleSheet("""
            font-size: 40pt;
            font-weight: bold;
            color: #27ae60;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 8px;
        """)
        self.tr_dash_monthly_label.setAlignment(Qt.AlignCenter)
        prod_stats_layout.addWidget(month_label, 0, 1)
        prod_stats_layout.addWidget(self.tr_dash_monthly_label, 1, 1)

        # Yearly Production
        year_label = QLabel("This Year")
        year_label.setStyleSheet("font-size: 14pt; color: #2c3e50; font-weight: bold;")
        year_label.setAlignment(Qt.AlignCenter)
        self.tr_dash_yearly_label.setStyleSheet("""
            font-size: 40pt;
            font-weight: bold;
            color: #8e44ad;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 8px;
        """)
        self.tr_dash_yearly_label.setAlignment(Qt.AlignCenter)
        prod_stats_layout.addWidget(year_label, 0, 2)
        prod_stats_layout.addWidget(self.tr_dash_yearly_label, 1, 2)

        # Add Next Serial Number section below production stats
        next_serial_label = QLabel("Next Serial Number")
        next_serial_label.setStyleSheet("font-size: 14pt; color: #2c3e50; font-weight: bold;")
        next_serial_label.setAlignment(Qt.AlignCenter)
        prod_stats_layout.addWidget(next_serial_label, 2, 0, 1, 3)  # Span all columns

        self.tr_dash_next_serial_label = QLabel("N/A")
        self.tr_dash_next_serial_label.setObjectName("DashboardMetricValue")
        self.tr_dash_next_serial_label.setStyleSheet("""
            font-size: 40pt;
            font-weight: bold;
            color: #e74c3c;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 8px;
            margin-top: 10px;
        """)
        self.tr_dash_next_serial_label.setAlignment(Qt.AlignCenter)
        prod_stats_layout.addWidget(self.tr_dash_next_serial_label, 3, 0, 1, 3)  # Span all columns

        prod_stats_group.setLayout(prod_stats_layout)
        layout1.addWidget(prod_stats_group, 1) # Add stretch factor
        
        self.dashboard_stacked_widget.addWidget(page1)

        # Page 2: Parts Inventory - Now as a grid of bubbles
        page2 = QWidget(); page2.setObjectName("DashboardPage")
        layout2 = QVBoxLayout(page2)

        # Add warning section at top that's hidden by default
        layout2.addWidget(self.tr_warning_section)  # Add warning section to top

        header2 = QLabel("Top Rail - Parts Inventory")
        header2.setObjectName("DashboardHeader")
        layout2.addWidget(header2)

        # Bubble grid for parts
        self.tr_parts_grid_scroll = QScrollArea()
        self.tr_parts_grid_scroll.setWidgetResizable(True)
        self.tr_parts_grid_scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.tr_parts_grid_widget = QWidget()
        self.tr_parts_grid_layout = QGridLayout(self.tr_parts_grid_widget)
        self.tr_parts_grid_layout.setSpacing(15)
        self.tr_parts_grid_scroll.setWidget(self.tr_parts_grid_widget)
        layout2.addWidget(self.tr_parts_grid_scroll, 1)

        self.dashboard_stacked_widget.addWidget(page2)

        # Page 3: Deficits (Top Rails vs Bodies)
        page3 = QWidget(); page3.setObjectName("DashboardPage")
        layout3 = QVBoxLayout(page3)
        header3 = QLabel("Top Rail - Assembly Needs"); header3.setObjectName("DashboardHeader"); header3.setAlignment(Qt.AlignCenter)
        layout3.addWidget(header3)
        self.tr_dash_deficits_layout_7ft = QVBoxLayout() # For 7ft tables
        self.tr_dash_deficits_layout_6ft = QVBoxLayout() # For 6ft tables
        
        deficit_content_layout = QHBoxLayout()
        group_7ft_deficit = QGroupBox("7ft Tables"); group_7ft_deficit.setLayout(self.tr_dash_deficits_layout_7ft)
        group_6ft_deficit = QGroupBox("6ft Tables"); group_6ft_deficit.setLayout(self.tr_dash_deficits_layout_6ft)
        deficit_content_layout.addWidget(group_7ft_deficit)
        deficit_content_layout.addWidget(group_6ft_deficit)
        layout3.addLayout(deficit_content_layout, 1)
        self.dashboard_stacked_widget.addWidget(page3)

        # Initialize dashboard widgets dictionary (for parts and deficits)
        self.top_rail_dashboard_widgets["parts"] = {}
        self.top_rail_dashboard_widgets["deficits_7ft"] = {}
        self.top_rail_dashboard_widgets["deficits_6ft"] = {}


    def setup_body_build_dashboard_tab(self):
        """Sets up the UI for the Body Build Dashboard tab using a grid of bubbles."""
        main_layout = QVBoxLayout(self.body_build_dashboard_tab)
        main_layout.setSpacing(10)

        header = QLabel("Body Build - Parts Inventory")
        header.setObjectName("DashboardHeader")
        main_layout.addWidget(header)

        # Low stock warning section
        self.body_low_stock_warning_section = QWidget()
        warning_layout = QVBoxLayout(self.body_low_stock_warning_section)
        warning_layout.setContentsMargins(10, 10, 10, 10)
        
        warning_header = QLabel("⚠️ BODY PARTS LOW STOCK WARNING")
        warning_header.setStyleSheet("""
            font-size: 22pt; color: #c62828; padding: 10px;
            background-color: #ffebee; border-radius: 8px; font-weight: bold;
        """)
        warning_header.setAlignment(Qt.AlignCenter)
        warning_layout.addWidget(warning_header)
        
        self.body_low_stock_warning_text = QLabel()
        self.body_low_stock_warning_text.setStyleSheet("font-size: 14pt; color: #c62828; padding: 10px;")
        self.body_low_stock_warning_text.setAlignment(Qt.AlignCenter)
        self.body_low_stock_warning_text.setWordWrap(True)
        warning_layout.addWidget(self.body_low_stock_warning_text)
        
        main_layout.addWidget(self.body_low_stock_warning_section)
        self.body_low_stock_warning_section.hide()

        # Scroll area for the grid of bubbles
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        # Container widget and grid layout for the bubbles
        self.body_parts_grid_widget = QWidget()
        self.body_parts_grid_layout = QGridLayout(self.body_parts_grid_widget)
        self.body_parts_grid_layout.setSpacing(15)
        
        scroll_area.setWidget(self.body_parts_grid_widget)
        main_layout.addWidget(scroll_area, 1)

    def manual_dashboard_scroll(self):
        """Manually cycle to next dashboard page and reset timer."""
        if hasattr(self, 'dashboard_stacked_widget'):
            # Stop the auto-scroll timer temporarily
            if self.dashboard_scroll_timer.isActive():
                self.dashboard_scroll_timer.stop()
            
            # Perform the page change
            current_index = self.dashboard_stacked_widget.currentIndex()
            next_index = (current_index + 1) % self.dashboard_stacked_widget.count()
            self.dashboard_stacked_widget.setCurrentIndex(next_index)
            
            # Restart the timer
            scroll_time = self.config.get("SCROLL_TIMER", 10)
            self.dashboard_scroll_timer.start(scroll_time * 1000)

    def scroll_dashboard_page(self):
        """Auto-cycles through the pages of the Top Rail Dashboard."""
        if hasattr(self, 'dashboard_stacked_widget') and self.dashboard_stacked_widget.count() > 0:
            # Always scroll regardless of warning status
            self.manual_dashboard_scroll()

    def create_summary_group(self, title, count_label, object_name):
        group_box = QGroupBox(title)
        group_box.setObjectName(object_name)
        layout = QVBoxLayout()
        layout.addWidget(count_label)
        group_box.setLayout(layout)
        return group_box

    def setup_parts_inventory_tab(self):
        parts_layout = QVBoxLayout(self.parts_tab)
        parts_layout.setSpacing(10)
        
        # Make table much bigger
        self.parts_table = QTableWidget()
        self.parts_table.setColumnCount(4)
        self.parts_table.setHorizontalHeaderLabels(["Part Name", "Current Stock", "Rails Per Part", "Tables Possible"])
        self.parts_table.horizontalHeader().setFixedHeight(40)  # Make header taller
        self.parts_table.verticalHeader().setVisible(False)
        
        # Increase font sizes and row height
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        self.parts_table.horizontalHeader().setFont(header_font)
        self.parts_table.verticalHeader().setDefaultSectionSize(40)
        
        # Take up most of the screen
        self.parts_table.setMinimumHeight(400)
        parts_layout.addWidget(self.parts_table, stretch=1)
        
        info_group = QGroupBox("Inventory Summary")
        info_layout = QFormLayout()
        info_layout.setSpacing(10)  # Increased spacing
        
        # Bigger summary labels
        self.total_parts_label = QLabel("0")
        self.low_stock_label = QLabel("0")
        self.last_update_label = QLabel("Never")
        
        for label in [self.total_parts_label, self.low_stock_label, self.last_update_label]:
            label.setStyleSheet("font-size: 11pt;")
        self.low_stock_label.setStyleSheet("font-size: 11pt; font-weight: bold; color: #c0392b;")
        
        info_layout.addRow(QLabel("Total Parts in Stock:"), self.total_parts_label)
        info_layout.addRow(QLabel("Parts Low on Stock:"), self.low_stock_label)
        info_layout.addRow(QLabel("Last Updated:"), self.last_update_label)
        info_group.setLayout(info_layout)
        parts_layout.addWidget(info_group)

    def update_parts_inventory_table(self, inventory_data):
        if not inventory_data:
            self.parts_table.setRowCount(0); self.total_parts_label.setText("N/A")
            self.low_stock_label.setText("N/A")
            if inventory_data is None: self.last_update_label.setText(f"Update Failed: {datetime.now().strftime('%H:%M:%S')}")
            return

        sorted_parts = sorted(inventory_data.get('printed_parts_current', {}).items())
        self.parts_table.setRowCount(len(sorted_parts))
        total_parts = 0
        low_stock_count = 0

        table_font = QFont()
        table_font.setPointSize(10)
        table_font.setBold(True)

        low_stock_items = []  # Track all low stock items
        
        for row, (part_name, count) in enumerate(sorted_parts):
            actual_count = count if count is not None else 0 # Fixed ternary operator syntax
            
            # Create items
            name_item = QTableWidgetItem(part_name)
            count_item = QTableWidgetItem(str(actual_count))
            rails_per_part = QTableWidgetItem("2")  # Default to 2 per table
            tables_possible = actual_count // 2  # Most parts need 2 per table
            tables_item = QTableWidgetItem(str(tables_possible))
            
            # Center align and set fonts
            for item in [name_item, count_item, rails_per_part, tables_item]:
                item.setTextAlignment(Qt.AlignCenter)
                item.setFont(table_font)
            
            # Color coding for low stock
            if tables_possible < 5:
                color = QColor("#c62828")  # Red
                for item in [name_item, count_item, rails_per_part, tables_item]:
                    item.setBackground(QColor("#ffebee"))  # Light red background
                
                # Add to low stock list instead of showing warning immediately
                low_stock_items.append((part_name, tables_possible))
            
            elif tables_possible < 10:
                color = QColor("#f57c00")  # Orange
            else:
                color = QColor("#2e7d32")  # Green
            
            count_item.setForeground(color)
            tables_item.setForeground(color)
            
            # Set items in table
            self.parts_table.setItem(row, 0, name_item)
            self.parts_table.setItem(row, 1, count_item)
            self.parts_table.setItem(row, 2, rails_per_part)
            self.parts_table.setItem(row, 3, tables_item)
            
            # Update totals
            total_parts += actual_count
            if tables_possible < 5:
                low_stock_count += 1

        self.parts_table.resizeColumnsToContents()
        self.total_parts_label.setText(str(total_parts))
        self.low_stock_label.setText(str(low_stock_count))
        self.last_update_label.setText(datetime.now().strftime('%d %b %Y, %H:%M:%S'))

    def update_production_table(self, daily_data_list):
        """Updates the production table with daily data, excluding weekends."""
        if not daily_data_list:
            self.production_table.setRowCount(0)
            return
        
        # Filter out weekends (Saturday and Sunday)
        weekday_data = [
            day for day in daily_data_list 
            if datetime.strptime(day["date"], "%Y-%m-%d").weekday() < 5
        ]
        
        self.production_table.setRowCount(len(weekday_data))
        
        # Larger font for table
        table_font = QFont()
        table_font.setPointSize(9)
        
        for row, day_data in enumerate(weekday_data):
            # Format date
            try:
                date_obj = datetime.strptime(day_data["date"], "%Y-%m-%d").date()
                friendly_date = date_obj.strftime("%a, %d %b %Y")
            except ValueError:
                friendly_date = day_data["date"]
            
            # Create items
            date_item = QTableWidgetItem(friendly_date)
            bodies_item = QTableWidgetItem(str(day_data.get("bodies", 0)))
            pods_item = QTableWidgetItem(str(day_data.get("pods", 0)))
            rails_item = QTableWidgetItem(str(day_data.get("top_rails", 0)))
            
            # Center align numbers and set font
            for item in [bodies_item, pods_item, rails_item]:
                item.setTextAlignment(Qt.AlignCenter)
                item.setFont(table_font)
            
            # Highlight today's row
            if day_data["date"] == datetime.now().date().strftime("%Y-%m-%d"):
                for item in [date_item, bodies_item, pods_item, rails_item]:
                    item.setBackground(QColor("#e6f7ff"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
            
            # Handle error states
            if day_data.get("error_info"):
                tooltip = day_data["error_info"]
                for item in [date_item, bodies_item, pods_item, rails_item]:
                    item.setForeground(QColor("#999999"))
                    item.setToolTip(tooltip)
            
            # Set items in table
            self.production_table.setItem(row, 0, date_item)
            self.production_table.setItem(row, 1, bodies_item)
            self.production_table.setItem(row, 2, pods_item)
            self.production_table.setItem(row, 3, rails_item)
            
        self.production_table.resizeColumnsToContents()

    def update_summary_counts(self, daily_data_list):
        """Updates the summary counts for the selected month only."""
        # Filter out weekends (Saturday and Sunday)
        weekday_data = [day for day in daily_data_list if datetime.strptime(day["date"], "%Y-%m-%d").weekday() < 5]
        
        total_bodies = sum(day.get("bodies", 0) for day in weekday_data)
        total_pods = sum(day.get("pods", 0) for day in weekday_data)
        total_rails = sum(day.get("top_rails", 0) for day in weekday_data)
        self.bodies_count_label.setText(str(total_bodies))
        self.pods_count_label.setText(str(total_pods))
        self.rails_count_label.setText(str(total_rails))

    def update_assembly_deficit_display(self):
        if not self.inventory_data:
            for config_name_iter in self.assembly_deficit_widgets: # Use a different variable name
                widgets = self.assembly_deficit_widgets[config_name_iter]
                # Use the base text color determined during setup for "N/A"
                base_text_color_style = widgets["body_stock_value_label"].styleSheet() # Get base style
                
                widgets["body_stock_value_label"].setText("N/A")
                widgets["body_stock_value_label"].setStyleSheet(base_text_color_style.replace("font-weight: bold;", "")) # Remove bold if present
                
                widgets["rail_stock_value_label"].setText("N/A")
                widgets["rail_stock_value_label"].setStyleSheet(base_text_color_style.replace("font-weight: bold;", ""))

                widgets["status_label"].setText("Inventory data unavailable.")
                widgets["status_label"].setStyleSheet(base_text_color_style.split(';')[0] + ";") # Just color part
            return

        finished_stock = self.inventory_data.get("finished_components_stock", {})

        for config_item in self.table_configurations: # Use a different variable name
            config_name = config_item["name"]
            widgets = self.assembly_deficit_widgets.get(config_name, {})
            if not widgets: continue

            body_stock = finished_stock.get(config_item["body_key"], 0)
            rail_stock = finished_stock.get(config_item["rail_key"], 0)

            body_stock_label = widgets.get("body_stock_value_label")
            rail_stock_label = widgets.get("rail_stock_value_label")
            status_label = widgets.get("status_label")

            # Get the base text color from one of the labels (set during setup)
            # Assuming body_stock_label exists and has its base style set
            base_text_color = "black" # Default
            if body_stock_label:
                # A bit hacky to parse from stylesheet, but QPalette isn't easily stored per widget for this
                style_str = body_stock_label.styleSheet() 
                match = re.search(r"color:\s*([^;]+);", style_str)
                if match:
                    base_text_color = match.group(1)
            
            base_label_style = f"color: {base_text_color}; font-size: 9pt;"
            shortage_label_style = f"color: #c62828; font-weight: bold; font-size: 9pt;" # Red and bold for shortage

            if body_stock_label: 
                body_stock_label.setText(str(body_stock))
                body_stock_label.setStyleSheet(base_label_style) # Default style
            if rail_stock_label: 
                rail_stock_label.setText(str(rail_stock))
                rail_stock_label.setStyleSheet(base_label_style) # Default style
            
            status_text = ""; 
            status_style = base_label_style # Default status style

            if body_stock == 0 and rail_stock == 0:
                status_text = "No components in stock."
                if body_stock_label: body_stock_label.setStyleSheet(shortage_label_style)
                if rail_stock_label: rail_stock_label.setStyleSheet(shortage_label_style)
            elif body_stock == rail_stock:
                status_text = f"Can assemble {body_stock} tables."
                status_style = f"color: #2e7d32; font-weight: bold; font-size: 9pt;" # Green and bold
            elif body_stock > rail_stock:
                needed = body_stock - rail_stock
                status_text = f"{needed} more Top Rails needed."
                status_style = shortage_label_style
                if rail_stock_label: rail_stock_label.setStyleSheet(shortage_label_style)
            else: 
                needed = rail_stock - body_stock
                status_text = f"{needed} more Bodies needed."
                status_style = shortage_label_style
                if body_stock_label: body_stock_label.setStyleSheet(shortage_label_style)
            
            if status_label:
                status_label.setText(status_text)
                status_label.setStyleSheet(status_style)

    def update_top_rail_dashboard(self):
        """Updates all pages of the Top Rail Dashboard."""
        if not self.inventory_data:
            # Clear grid if present
            if hasattr(self, 'tr_parts_grid_layout'):
                while self.tr_parts_grid_layout.count():
                    child = self.tr_parts_grid_layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
            return

        # --- Page 1: Performance - Update with real production data ---
        if hasattr(self, 'tr_dash_current_time_label'):
            try:
                # Get production stats
                production_stats_response = requests.get(
                    f"{self.api_client.base_url}/api/top_rail/production_stats",
                    headers=self.api_client.headers
                )
                if production_stats_response.status_code == 200:
                    stats = production_stats_response.json()
                    self.tr_dash_daily_label.setText(str(stats['daily']))
                    self.tr_dash_monthly_label.setText(str(stats['monthly']))
                    self.tr_dash_yearly_label.setText(str(stats['yearly']))
                else:
                    self.tr_dash_daily_label.setText("ERR")
                    self.tr_dash_monthly_label.setText("ERR")
                    self.tr_dash_yearly_label.setText("ERR")

                # Get next serial number
                next_serial_response = requests.get(
                    f"{self.api_client.base_url}/api/top_rail/next_serial",
                    headers=self.api_client.headers
                )
                if next_serial_response.status_code == 200:
                    next_serial = next_serial_response.json().get("next_serial", "N/A")
                    # Clean up the serial number if it has a suffix
                    if next_serial != "N/A":
                        # Extract base number using regex
                        # This will match numbers at the start of the string
                        # and ignore any suffixes like -O, -GO, etc.
                        match = re.match(r'^(\d+)', next_serial)
                        if match:
                            base_number = int(match.group(1))
                            next_number = str(base_number + 1)
                            # If there was a suffix in the original, preserve it
                            suffix_match = re.search(r'([-\s]+[A-Za-z]+)$', next_serial)
                            if suffix_match:
                                next_serial = next_number + suffix_match.group(1)
                            else:
                                next_serial = next_number
                    self.tr_dash_next_serial_label.setText(next_serial)
                else:
                    self.tr_dash_next_serial_label.setText("ERR")

                # Fetch current time for the ongoing top rail
                user_id = "user_123"  # Replace with actual user ID
                current_time_response = requests.get(
                    f"{self.api_client.base_url}/api/top_rail/current_time",
                    params={"user_id": user_id},
                    headers=self.api_client.headers
                )
                if current_time_response.status_code == 200:
                    current_time = current_time_response.json().get("current_time")
                    self.tr_dash_current_time_label.setText(
                        f"{current_time:.2f} seconds" if current_time else "N/A"
                    )

                # Fetch average time for top rails
                avg_time_response = requests.get(
                    f"{self.api_client.base_url}/api/top_rail/average_time",
                    headers=self.api_client.headers
                )
                if avg_time_response.status_code == 200:
                    avg_time = avg_time_response.json().get("average_time")
                    self.tr_dash_avg_time_label.setText(
                        f"{avg_time:.2f} seconds" if avg_time else "N/A"
                    )
            except Exception as e:
                print(f"Error fetching performance data: {e}")
                self.tr_dash_next_serial_label.setText("ERR")
                self.tr_dash_current_time_label.setText("ERR")
                self.tr_dash_avg_time_label.setText("ERR")

        # --- Page 2: Parts Inventory - Update grid of bubbles ---
        if hasattr(self, 'tr_parts_grid_layout'):
            # Clear existing grid
            while self.tr_parts_grid_layout.count():
                child = self.tr_parts_grid_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # Define top rail parts dictionary
            top_rail_parts = {
                "Top rail trim long length": 2,
                "Top rail trim short length": 4,
                "Chrome corner": 4,
                "Center pockets": 2,
                "Corner pockets": 4,
                "Catch Plate": 12,
                "M5 x 20 Socket Cap Screw": 16,
                "M5 x 18 x 1.25 Penny Mudguard Washer": 16,
                "LAMELLO CLAMEX P-14 CONNECTOR": 18,
                "4.8x32mm Self Tapping Screw": 24
            }
            
            hardware_parts_stock = self.inventory_data.get("hardware_parts_current", {})
            table_parts_stock = self.inventory_data.get("table_parts_current", {})

            # Combine stock data and calculate rails possible
            parts_data = []
            min_rails_possible = float('inf')
            for part_name, qty_per_rail in top_rail_parts.items():
                stock_count = hardware_parts_stock.get(part_name, table_parts_stock.get(part_name, 0))
                rails_possible = stock_count // qty_per_rail if qty_per_rail > 0 else 0
                parts_data.append({
                    "name": part_name,
                    "stock": stock_count,
                    "per_rail": qty_per_rail,
                    "rails_possible": rails_possible
                })
                min_rails_possible = min(min_rails_possible, rails_possible)

            # Sort by rails_possible (bottleneck first)
            parts_data.sort(key=lambda x: x['rails_possible'])

            # Populate grid
            num_columns = 6
            for i, data in enumerate(parts_data):
                bubble = QGroupBox()
                bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                bubble_layout = QVBoxLayout(bubble)
                bubble_layout.setContentsMargins(10, 20, 10, 10)
                bubble_layout.setSpacing(5)
                bubble_layout.setAlignment(Qt.AlignCenter)

                # Container for text with a solid background
                text_widget = QWidget()
                text_layout = QVBoxLayout(text_widget)
                text_layout.setContentsMargins(8, 8, 8, 8)
                text_layout.setSpacing(2)
                text_widget.setStyleSheet("background-color: rgba(255,255,255,0.92); border-radius: 12px;")

                # Large in-stock number
                stock_label = QLabel(f"{data['stock']}")
                stock_label.setAlignment(Qt.AlignCenter)
                stock_label.setStyleSheet("font-size: 32pt; font-weight: bold; color: #222; padding: 0; margin: 0;")

                # Part name
                name_label = QLabel(data['name'])
                name_label.setAlignment(Qt.AlignCenter)
                name_label.setStyleSheet("font-size: 10pt; font-weight: 600; color: #444; padding: 0; margin: 0;")

                # Can Build
                can_build_label = QLabel(f"Can Build: <b>{data['rails_possible']}</b>")
                can_build_label.setAlignment(Qt.AlignCenter)
                can_build_label.setStyleSheet("font-size: 11pt; color: #1976d2; padding: 0; margin: 0;")

                text_layout.addWidget(stock_label)
                text_layout.addWidget(name_label)
                text_layout.addWidget(can_build_label)
                text_layout.addStretch(1)
                bubble_layout.addWidget(text_widget)

                # Color coding
                bg_color = "#e8f5e9" # Green
                border_color = "#388e3c"
                if data['rails_possible'] < 5:
                    bg_color = "#ffebee" # Red
                    border_color = "#c62828"
                elif data['rails_possible'] < 10:
                    bg_color = "#fff3e0" # Orange
                    border_color = "#f57c00"

                bubble.setStyleSheet(f"""
                    QGroupBox {{
                        background-color: {bg_color};
                        border: 2px solid {border_color};
                        border-radius: 18px;
                        margin-top: 10px;
                        font-weight: bold;
                    }}
                    QGroupBox::title {{
                        subcontrol-origin: margin;
                        subcontrol-position: top center;
                        padding: 0;
                        background-color: transparent;
                    }}
                """)

                row = i // num_columns
                col = i % num_columns
                self.tr_parts_grid_layout.addWidget(bubble, row, col)

            # Handle low stock by showing warning section
            if min_rails_possible < 5:
                if hasattr(self, 'tr_warning_text'):
                    warning_text = f"Critical Top Rail Parts Low:\n\nCan only build {min_rails_possible} more top rails (bottleneck part)."
                    self.tr_warning_text.setText(warning_text)
                    self.tr_warning_section.show()
            else:
                if hasattr(self, 'tr_warning_section'):
                    self.tr_warning_section.hide()

        # --- Page 3: Top Rail Deficits (vs Bodies) ---
        if hasattr(self, 'top_rail_dashboard_widgets') and "deficits_7ft" in self.top_rail_dashboard_widgets:
            finished_stock = self.inventory_data.get("finished_components_stock", {})
            
            # Ensure layouts are clear before repopulating (if dynamic creation per update)
            # For this version, assuming widgets are created once in setup_top_rail_dashboard_tab
            # and then updated. If they are recreated, clearing logic would go here.

            for config in self.table_configurations:
                size_layout_key = f"deficits_{config['size']}"
                color_key = config['color_display']

                # Ensure widget dictionary structure exists
                if color_key not in self.top_rail_dashboard_widgets[size_layout_key]:
                    color_group_deficit = QGroupBox(config['color_display'])
                    color_group_deficit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                    form_layout_deficit = QFormLayout(color_group_deficit)
                    form_layout_deficit.setContentsMargins(15, 25, 15, 15)  # Add more padding
                    

                    hex_color_code = self.TABLE_FINISH_COLORS.get(color_key, self.TABLE_FINISH_COLORS["Default"])
                    q_color = QColor(hex_color_code)
                    
                    color_group_deficit.setAutoFillBackground(True)
                    
                    # Check if image path exists and is a file
                    if isinstance(hex_color_code, str) and hex_color_code.startswith('#'):
                        # It's a color hex code
                        bg_style = f"background-color: {hex_color_code};"
                    else:
                        # It's supposed to be an image path
                        if os.path.isfile(hex_color_code):
                            image_path = hex_color_code.replace('\\', '/')
                            bg_style = f"background-image: url('{image_path}');"
                            bg_style += """
                                background-repeat: no-repeat;
                                background-position: center;
                                background-origin: content;
                            """
                        else:
                            # Fallback colors for each finish type
                            colors = {
                                "Black": "#2d3436",
                                "Rustic Oak": "#cc8e35",
                                "Grey Oak": "#636e72",
                                "Stone": "#b2bec3",
                                "Default": "#E0E0E0"
                            }
                            bg_color = colors.get(color_key, colors["Default"])
                            bg_style = f"background-color: {bg_color};"
                    
                    color_group_deficit.setStyleSheet(f"""
                        QGroupBox {{
                            border: 1px solid #d0d0d0;
                            border-radius: 8px;
                            margin-top: 20px;
                            padding: 15px;
                            {bg_style}
                        }}
                        QGroupBox::title {{
                            color: black;
                            subcontrol-origin: margin;
                            left: 7px;
                            padding: 0 5px 0 5px;
                            background-color: rgba(255, 255, 255, 0.8);
                            font-weight: bold;
                        }}
                    """)  # Added closing parenthesis here
                    
                    # Always use white text for dark backgrounds and black for light ones
                    text_color = 'white' if q_color.lightnessF() < 0.5 else 'black'
                    label_style = f"color: {text_color}; font-size: 10pt; margin: 2px;"  # Increased font size and margin
                    
                    # --- Create and style labels ---
                    body_stock_val_label = QLabel("N/A")
                    body_stock_val_label.setStyleSheet(label_style)
                    body_stock_val_label.setObjectName("StockValue")
                    
                    rail_stock_val_label = QLabel("N/A")
                    rail_stock_val_label.setStyleSheet(label_style)
                    rail_stock_val_label.setObjectName("StockValue")
                    
                    status_val_label = QLabel("N/A")
                    status_val_label.setStyleSheet(label_style)
                    status_val_label.setWordWrap(True)

                    body_label = QLabel("Body Stock:")
                    body_label.setStyleSheet(label_style)
                    rail_label = QLabel("Top Rail Stock:")
                    rail_label.setStyleSheet(label_style)
                    status_label = QLabel("Status:")
                    status_label.setStyleSheet(label_style)

                    form_layout_deficit.addRow(body_label, body_stock_val_label)
                    form_layout_deficit.addRow(rail_label, rail_stock_val_label)
                    form_layout_deficit.addRow(status_label, status_val_label)

                    self.top_rail_dashboard_widgets[size_layout_key][color_key] = {
                        "body_stock": body_stock_val_label,
                        "rail_stock": rail_stock_val_label,
                        "status": status_val_label,
                        "group_box": color_group_deficit,
                        "text_color": text_color
                    }
                    if config['size'] == "7ft":
                        self.tr_dash_deficits_layout_7ft.addWidget(color_group_deficit)
                    else:
                        self.tr_dash_deficits_layout_6ft.addWidget(color_group_deficit)
                
                widgets = self.top_rail_dashboard_widgets[size_layout_key][color_key]
                body_stock = finished_stock.get(config["body_key"], 0)
                rail_stock = finished_stock.get(config["rail_key"], 0)

                widgets["body_stock"].setText(str(body_stock))
                widgets["rail_stock"].setText(str(rail_stock))
                
                # Base style for these labels (can be simple, specific styles below will override)
                base_deficit_value_style = "font-size: 9pt;"
                widgets["body_stock"].setStyleSheet(base_deficit_value_style) 
                widgets["rail_stock"].setStyleSheet(base_deficit_value_style) 

                status_text = ""
                status_style = "font-size: 9pt; color: #555;" # Default neutral
                
# Add styling for text boxes with light background
                value_box_style = """
                    background-color: rgba(255, 255, 255, 0.9);
                    border-radius: 4px;
                    padding: 2px 4px;
                    margin: 2px;
                """
                
                # Apply the box style to all labels first
                widgets["body_stock"].setStyleSheet(f"{value_box_style} color: black;")
                widgets["rail_stock"].setStyleSheet(f"{value_box_style} color: black;")
                widgets["status"].setStyleSheet(f"{value_box_style} color: black; margin-top: 8px;")
                
                # Then handle the specific status cases with color overrides
                if body_stock == 0 and rail_stock == 0:
                    status_text = "No bodies or rails."
                    widgets["body_stock"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold;")
                    widgets["status"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold; margin-top: 8px;")
                elif body_stock == rail_stock:
                    status_text = f"Balanced. Can make {body_stock} sets."
                    widgets["body_stock"].setStyleSheet(f"{value_box_style} color: #1a237e; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet(f"{value_box_style} color: #1a237e; font-weight: bold;")
                    widgets["status"].setStyleSheet(f"{value_box_style} color: #1a237e; font-weight: bold; margin-top: 8px;")
                elif body_stock > rail_stock:
                    needed = body_stock - rail_stock
                    status_text = f"{needed} more Top Rails needed."
                    widgets["body_stock"].setStyleSheet(f"{value_box_style} color: #1a237e; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold;")
                    widgets["status"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold; margin-top: 8px;")
                else:  # rail_stock > body_stock
                    needed = rail_stock - body_stock
                    status_text = f"{needed} more Bodies needed."
                    widgets["body_stock"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet(f"{value_box_style} color: #1a237e; font-weight: bold;")
                    widgets["status"].setStyleSheet(f"{value_box_style} color: #b71c1c; font-weight: bold; margin-top: 8px;")
                
                widgets["status"].setText(status_text)


    def update_body_build_dashboard(self):
        """Updates the Body Build Dashboard with a grid of part 'bubbles'."""
        # Clear existing grid before repopulating
        if hasattr(self, 'body_parts_grid_layout'):
            while self.body_parts_grid_layout.count():
                child = self.body_parts_grid_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        
        if not self.inventory_data:
            if hasattr(self, 'body_low_stock_warning_section'): self.body_low_stock_warning_section.hide()
            return

        hardware_stock = self.inventory_data.get("hardware_parts_current", {})
        table_parts_stock = self.inventory_data.get("table_parts_current", {})
        printed_parts_stock = self.inventory_data.get("printed_parts_current", {})
        
        all_parts_stock = {**hardware_stock, **table_parts_stock, **printed_parts_stock}

        all_required_parts = set(self.BODY_PARTS_7FT.keys()) | set(self.BODY_PARTS_6FT.keys())

        min_7ft_possible = float('inf')
        min_6ft_possible = float('inf')
        
        # Calculate all data first
        parts_data = []
        for part_name in sorted(list(all_required_parts)):
            stock_count = all_parts_stock.get(part_name, 0)
            req_7ft = self.BODY_PARTS_7FT.get(part_name, 0)
            req_6ft = self.BODY_PARTS_6FT.get(part_name, 0)
            possible_7ft = stock_count // req_7ft if req_7ft > 0 else float('inf')
            possible_6ft = stock_count // req_6ft if req_6ft > 0 else float('inf')
            
            parts_data.append({
                "name": part_name, "stock": stock_count,
                "possible_7ft": possible_7ft, "possible_6ft": possible_6ft,
                "req_7ft": req_7ft, "req_6ft": req_6ft
            })
            
            if req_7ft > 0: min_7ft_possible = min(min_7ft_possible, possible_7ft)
            if req_6ft > 0: min_6ft_possible = min(min_6ft_possible, possible_6ft)

        # Sort by bottleneck
        parts_data.sort(key=lambda x: min(x['possible_7ft'], x['possible_6ft']))

        # Populate grid
        num_columns = 8  # Increased from 6
        for i, data in enumerate(parts_data):
            bubble = QGroupBox()
            bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            bubble_layout = QVBoxLayout(bubble)
            bubble_layout.setContentsMargins(5, 10, 5, 5)  # Reduced from (10, 20, 10, 10)
            bubble_layout.setSpacing(2)  # Reduced from 5
            bubble_layout.setAlignment(Qt.AlignCenter)

            text_widget = QWidget()
            text_layout = QVBoxLayout(text_widget)
            text_layout.setContentsMargins(4, 4, 4, 4)  # Reduced from (8, 8, 8, 8)
            text_layout.setSpacing(1)  # Reduced from 2
            text_widget.setStyleSheet("background-color: rgba(255,255,255,0.92); border-radius: 8px;")

            # Large in-stock number
            stock_label = QLabel(f"{data['stock']}")
            stock_label.setAlignment(Qt.AlignCenter)
            stock_label.setStyleSheet("font-size: 28pt; font-weight: bold; color: #222; padding: 0; margin: 0;")  # Reduced from 32pt

            # Part name
            name_label = QLabel(data['name'])
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setStyleSheet("font-size: 9pt; font-weight: 600; color: #444; padding: 0; margin: 0;")  # Reduced from 10pt

            # Can Build label
            if data['req_6ft'] > 0 and data['req_7ft'] == 0:
                can_build_val = str(data['possible_6ft']) if data['possible_6ft'] != float('inf') else "N/A"
                can_build_label = QLabel(f"Can Build: <b>{can_build_val}</b> (6ft)")
            else:
                can_build_val = str(data['possible_7ft']) if data['possible_7ft'] != float('inf') else "N/A"
                can_build_label = QLabel(f"Can Build: <b>{can_build_val}</b> (7ft)")
            can_build_label.setAlignment(Qt.AlignCenter)
            can_build_label.setStyleSheet("font-size: 10pt; color: #1976d2; padding: 0; margin: 0;")  # Reduced from 11pt

            text_layout.addWidget(stock_label)
            text_layout.addWidget(name_label)
            text_layout.addWidget(can_build_label)
            bubble_layout.addWidget(text_widget)

             # Color coding
            min_possible = min(data['possible_7ft'], data['possible_6ft'])
            bg_color = "#e8f5e9" # Green
            border_color = "#388e3c"
            if min_possible < 5:
                bg_color = "#ffebee" # Red
                border_color = "#c62828"
            elif min_possible < 10:
                bg_color = "#fff3e0" # Orange
                border_color = "#f57c00"


            bubble.setStyleSheet(f"""
                QGroupBox {{
                    background-color: {bg_color};
                    border: 2px solid {border_color};
                    border-radius: 12px;  /* Reduced from 18px */
                    margin-top: 5px;  /* Reduced from 10px */
                    font-weight: bold;
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin;
                    subcontrol-position: top center;
                    padding: 0;
                    background-color: transparent;
                }}
            """)
            
            row = i // num_columns
            col = i % num_columns
            self.body_parts_grid_layout.addWidget(bubble, row, col)

        # Update warning text
        if min_7ft_possible < 5 or min_6ft_possible < 1:
            warning_text = "Overall build capacity is low:\n\n"
            if min_7ft_possible < 5:
                warning_text += f"• Can only build {min_7ft_possible} more 7ft bodies.\n"
            if min_6ft_possible < 1:
                warning_text += f"• Can only build {min_6ft_possible} more 6ft bodies.\n"
            self.body_low_stock_warning_text.setText(warning_text)
            self.body_low_stock_warning_section.show()
        else:
            self.body_low_stock_warning_section.hide()

    def check_api_connection(self):
        self.connection_label.setText("API: Checking...")
        self.connection_label.setProperty("status", "checking"); self.style().polish(self.connection_label)
        if self.api_client.test_connection():
            self.connection_label.setText("API: Connected")
            self.connection_label.setProperty("status", "connected"); self.refresh_all_data()
        else:
            self.connection_label.setText("API: Disconnected")
            self.connection_label.setProperty("status", "disconnected")
            self.update_production_table([]); self.update_summary_counts([])
            self.update_parts_inventory_table(None); self.update_assembly_deficit_display() 
            self.update_top_rail_dashboard() # Clear dashboard on disconnect
            self.update_body_build_dashboard()
            QMessageBox.warning(self, "Connection Error", f"Unable to connect to the API at {self.api_client.base_url}.")
        self.style().polish(self.connection_label)

    def refresh_production_data(self):
        """Fetches and updates production data for the entire year."""
        self.statusBar().showMessage("Refreshing production data...")
        self.refresh_button.setEnabled(False)
        selected_year = int(self.prod_year_combo.currentText())
        yearly_data = []

        # Fetch data for all months in the selected year
        for month in range(1, 13):
            monthly_data = self.api_client.get_production_for_month(selected_year, month)
            if monthly_data:
                yearly_data.extend(monthly_data)

        # Update the production table and summary counts with the yearly data
        self.update_production_table(yearly_data)
        self.update_summary_counts(yearly_data)

        self.statusBar().showMessage(f"Production data refreshed for {selected_year} at {datetime.now().strftime('%H:%M:%S')}")
        self.refresh_button.setEnabled(True)

    def refresh_all_data(self):
        """Refreshes all data from the API without resetting the UI."""
        self.statusBar().showMessage("Refreshing all data from API...")
        self.refresh_button.setEnabled(False)

        try:
            # Fetch production data for the entire year
            selected_prod_year = int(self.prod_year_combo.currentText())
            yearly_data = []
            
            # Fetch data for all months in the selected year
            for month in range(1, 13):
                monthly_data = self.api_client.get_production_for_month(selected_prod_year, month)
                if monthly_data:
                    yearly_data.extend(monthly_data)

            # Update production table and summary with yearly data
            self.update_production_table(yearly_data)
            self.update_summary_counts(yearly_data)

            # Fetch inventory data
            self.parts_table.setUpdatesEnabled(False)
            self.inventory_data = self.api_client.get_inventory_summary()
            self.update_parts_inventory_table(self.inventory_data)
            self.parts_table.setUpdatesEnabled(True)

            # Update other components
            self.update_assembly_deficit_display()
            self.update_top_rail_dashboard()
            self.update_body_build_dashboard()

            self.statusBar().showMessage(f"All data refreshed at {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            self.statusBar().showMessage("Error refreshing data.")
            QMessageBox.critical(self, "Error", f"An error occurred while refreshing data: {e}")
        finally:
            self.refresh_button.setEnabled(True)

    def save_settings(self):
        self.config["API_URL"] = self.api_url_input.text().strip()
        self.config["API_TOKEN"] = self.api_token_input.text().strip()
        
        # Save default tab selection
        self.config["DEFAULT_TAB"] = self.default_tab_combo.currentIndex()
        
        if self.use_standard_port.isChecked():
            self.config["API_PORT"] = None
        else:
            try:
                port_val = int(self.api_port_input.text().strip())
                if not (0 < port_val < 65536): raise ValueError("Port out of range")
                self.config["API_PORT"] = port_val
            except ValueError: QMessageBox.warning(self, "Invalid Port", "Port must be between 1-65535."); return
        
        # Save timer setting
        try:
            scroll_time = int(self.scroll_timer_input.text().strip())
            if not (1 <= scroll_time <= 60):
                raise ValueError("Timer must be between 1-60 seconds")
            self.config["SCROLL_TIMER"] = scroll_time
            # Update the active timer
            self.dashboard_scroll_timer.setInterval(scroll_time * 1000)
        except ValueError:
            QMessageBox.warning(self, "Invalid Timer", "Scroll timer must be between 1-60 seconds.")
            return
            
        config_file = save_config(self.config)
        
        # Re-initialize APIClient with new settings
        self.api_client = APIClient(
            self.config["API_URL"], 
            self.config["API_TOKEN"], 
            self.config["API_PORT"]
        )
        new_display_url = self.api_client.base_url # Get potentially modified URL (with port)
        self.server_info_label.setText(f"Server: {new_display_url}")
        if hasattr(self, 'about_text_label'):
            self.about_text_label.setText(
                f"Pool Table Factory Tracker v1.6\n\nDisplays production and inventory data.\nConnects to: {new_display_url}")
        
        self.check_api_connection() # Test with new settings and refresh data if connected
        QMessageBox.information(self, "Settings Saved", f"Settings saved to {config_file}")

    def setup_settings_tab(self, settings_tab):
        settings_layout = QVBoxLayout(settings_tab)
        settings_layout.setSpacing(20)

        # API Configuration group
        api_group = QGroupBox("API Configuration")
        api_layout = QFormLayout()
        
        # API URL input
        self.api_url_input = QLineEdit(str(self.config.get("API_URL", "")))
        api_layout.addRow("API URL:", self.api_url_input)

        # Port configuration
        port_widget = QWidget()
        port_layout = QHBoxLayout(port_widget)
        port_layout.setContentsMargins(0, 0, 0, 0)
        
        self.use_standard_port = QCheckBox("Use standard HTTP/HTTPS port")
        self.use_standard_port.setChecked(self.config.get("API_PORT") is None)
        port_layout.addWidget(self.use_standard_port)
        
        self.api_port_input = QLineEdit(str(self.config.get("API_PORT", "5000")))
        self.api_port_input.setEnabled(not self.use_standard_port.isChecked())
        self.api_port_input.setFixedWidth(80)
        port_layout.addWidget(QLabel("Custom Port:"))
        port_layout.addWidget(self.api_port_input)
        port_layout.addStretch()
        api_layout.addRow(port_widget)
        
        # API Token input
        self.api_token_input = QLineEdit(str(self.config.get("API_TOKEN", "")))
        self.api_token_input.setEchoMode(QLineEdit.Password)
        api_layout.addRow("API Token:", self.api_token_input)
        
        # Connect port checkbox to port input enable/disable
        self.use_standard_port.toggled.connect(lambda checked: self.api_port_input.setEnabled(not checked))
        
        # Buttons
        buttons_layout = QHBoxLayout()
        self.save_settings_button = QPushButton("Save Settings")
        self.save_settings_button.clicked.connect(self.save_settings)
        buttons_layout.addWidget(self.save_settings_button)
        
        self.test_connection_button = QPushButton("Test API Connection")
        self.test_connection_button.setStyleSheet("background-color: #f39c12; color: white;")
        self.test_connection_button.clicked.connect(self.check_api_connection)
        buttons_layout.addWidget(self.test_connection_button)
        buttons_layout.addStretch()
        api_layout.addRow(buttons_layout)
        api_group.setLayout(api_layout)
        settings_layout.addWidget(api_group)

        # Timer Settings Group
        timer_group = QGroupBox("Dashboard Settings")
        timer_layout = QFormLayout()
        
        # Add default tab selector
        self.default_tab_combo = QComboBox()
        self.default_tab_combo.addItems([
            "Monthly Production",
            "Assembly Capacity",
            "Top Rail Dashboard",
            "Body Build Dashboard",
            "Inventory Parts"
        ])
        current_tab = self.config.get("DEFAULT_TAB", 0)
        self.default_tab_combo.setCurrentIndex(current_tab)
        timer_layout.addRow("Default startup tab:", self.default_tab_combo)
        
        # Add existing scroll timer setting
        self.scroll_timer_input = QLineEdit(str(self.config.get("SCROLL_TIMER", 10)))
        self.scroll_timer_input.setFixedWidth(80)
        self.scroll_timer_input.setValidator(QIntValidator(1, 60))
        timer_layout.addRow("Screen scroll interval (seconds):", self.scroll_timer_input)
        timer_group.setLayout(timer_layout)
        settings_layout.addWidget(timer_group)
        
        settings_layout.addStretch()
        
        # About section
        about_group = QGroupBox("About")
        about_layout = QVBoxLayout()
        self.about_text_label = QLabel(

            f"Pool Table Factory Tracker v1.6\n\n"
            f"Displays production and inventory data.\n"
            f"Connects to: {self.api_client.base_url}"
        )
        self.about_text_label.setAlignment(Qt.AlignCenter)
        self.about_text_label.setWordWrap(True)
        about_layout.addWidget(self.about_text_label)
        about_group.setLayout(about_layout)
        settings_layout.addWidget(about_group)

    def resizeEvent(self, event):
        """Handle window resize events to maintain proper scaling"""
        super().resizeEvent(event)
        # Adjust font sizes based on window size
        base_size = min(self.width() / 100, self.height() / 50)
        font = self.font()
        font.setPointSize(int(base_size))
        self.setFont(font)
        
        # Update tab contents scaling
        if hasattr(self, 'tabs'):  # Add safety check
            for i in range(self.tabs.count()):
                scroll = self.tabs.widget(i)
                if isinstance(scroll, QScrollArea):
                    content = scroll.widget()
                    if content:
                        content.updateGeometry()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()