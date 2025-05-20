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
    QSizePolicy, QSpacerItem, QGridLayout, QStackedWidget # Added QStackedWidget
)
from PyQt5.QtGui import QFont, QColor, QPalette, QBrush, QIcon, QIntValidator, QPixmap  # Added QPixmap
from PyQt5.QtCore import Qt, QTimer

# --- Modern UI Styling ---
STYLESHEET = """
QMainWindow {
    background-color: #f0f2f5; 
}
QGroupBox {
    font-size: 11pt;
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
    font-size: 10pt;
    color: #444;
}
QPushButton {
    font-size: 10pt;
    background-color: #3498db; 
    color: white;
    border: none;
    padding: 8px 16px;
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
    font-size: 10pt;
    padding: 6px;
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
    font-size: 9pt;
    border: 1px solid #e0e0e0;
    border-radius: 5px;
    gridline-color: #e0e0e0;
    background-color: #ffffff;
    alternate-background-color: #f9f9f9;
}
QHeaderView::section {
    background-color: #e9edf0; 
    padding: 6px;
    border: none;
    border-bottom: 1px solid #d0d0d0;
    font-size: 10pt;
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
    padding: 10px 20px;
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
QLabel.StatusNeeded { font-size: 10pt; font-weight: bold; color: #c62828; } /* Red for "needed" status */
QLabel.StatusCanAssemble { font-size: 10pt; font-weight: bold; color: #2e7d32; } /* Green for "can assemble" */
QLabel.StatusNeutral { font-size: 10pt; color: #555; } /* Neutral for no components */
QLabel.StockValue { font-size: 10pt; }
QLabel.StockValueShortage { font-size: 10pt; color: #c62828; font-weight: bold; } /* Red for stock value that is a bottleneck */

/* Top Rail Dashboard Styling */
QWidget#DashboardPage {
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 5px;
}
QLabel.DashboardHeader {
    font-size: 28pt;  /* Increased from 16pt */
    font-weight: bold;
    color: #3498db;
    padding-bottom: 15px;
    border-bottom: 3px solid #3498db;
    margin-bottom: 20px;
}
QLabel.DashboardMetricLabel {
    font-size: 18pt;  /* Increased from 12pt */
    color: #2c3e50;
}
QLabel.DashboardMetricValue {
    font-size: 32pt;  /* Increased from 18pt */
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
    "SCROLL_TIMER": 10  # Default 10 seconds
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
        "Black": os.path.join("images", "Black Oak.jpg"),
        "Rustic Oak": os.path.join("images", "Rustic Oak.jpg"),
        "Grey Oak": os.path.join("images", "Grey Oak.jpg"),
        "Stone": os.path.join("images", "Stone.jpg"),
        "Default": "#E0E0E0"
    }

    def __init__(self, config=None):
        super().__init__()
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

        self.setup_ui()
        self.setStyleSheet(STYLESHEET) 
        self.check_api_connection() 
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_all_data)
        self.refresh_timer.start(300000) 

        # Timer for Top Rail Dashboard page scrolling
        self.dashboard_scroll_timer = QTimer(self)
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
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0,0,0,0)
        title_label = QLabel("Pool Table Factory Tracker")
        title_font = QFont("Arial", 18, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #2c3e50;")
        header_layout.addWidget(title_label)
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.connection_label = QLabel("API: Checking...")
        self.connection_label.setObjectName("ApiStatusLabel")
        self.connection_label.setProperty("status", "checking")
        header_layout.addWidget(self.connection_label)
        main_layout.addWidget(header_frame)
        
        self.server_info_label = QLabel(f"Server: {self.api_client.base_url}")
        self.server_info_label.setStyleSheet("font-size: 8pt; color: #7f8c8d;")
        main_layout.addWidget(self.server_info_label, alignment=Qt.AlignRight)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        self.prod_tab = QWidget()
        self.tabs.addTab(self.prod_tab, "Monthly Production")
        self.setup_production_tab()

        self.assembly_deficit_tab = QWidget() 
        self.tabs.addTab(self.assembly_deficit_tab, "Assembly Capacity")
        self.setup_assembly_deficit_tab()

        self.top_rail_dashboard_tab = QWidget() # New Tab
        self.tabs.addTab(self.top_rail_dashboard_tab, "Top Rail Dashboard")
        self.setup_top_rail_dashboard_tab()
        
        self.parts_tab = QWidget()
        self.tabs.addTab(self.parts_tab, "Inventory Parts")
        self.setup_parts_inventory_tab()
        
        settings_tab = QWidget()
        self.tabs.addTab(settings_tab, "Settings")
        self.setup_settings_tab(settings_tab)
        
        self.statusBar().showMessage("Ready")
        
        refresh_button_layout = QHBoxLayout()
        refresh_button_layout.addStretch()
        self.refresh_button = QPushButton("Refresh All Data") 
        self.refresh_button.setIcon(QIcon.fromTheme("view-refresh")) 
        self.refresh_button.clicked.connect(self.refresh_all_data)
        refresh_button_layout.addWidget(self.refresh_button)
        main_layout.addLayout(refresh_button_layout)

    def setup_production_tab(self):
        prod_layout = QVBoxLayout(self.prod_tab)
        prod_layout.setSpacing(15)
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
        summary_layout = QHBoxLayout(); summary_layout.setSpacing(15)
        self.bodies_count_label = QLabel("0"); self.bodies_count_label.setObjectName("BodiesCountLabel")
        self.bodies_count_label.setAlignment(Qt.AlignCenter); self.bodies_count_label.setStyleSheet("font-size: 28pt; font-weight: bold;")
        bodies_group = self.create_summary_group("Bodies (Selected Month)", self.bodies_count_label, "BodiesGroup")
        summary_layout.addWidget(bodies_group)
        self.pods_count_label = QLabel("0"); self.pods_count_label.setObjectName("PodsCountLabel")
        self.pods_count_label.setAlignment(Qt.AlignCenter); self.pods_count_label.setStyleSheet("font-size: 28pt; font-weight: bold;")
        pods_group = self.create_summary_group("Pods (Selected Month)", self.pods_count_label, "PodsGroup")
        summary_layout.addWidget(pods_group)
        self.rails_count_label = QLabel("0"); self.rails_count_label.setObjectName("RailsCountLabel")
        self.rails_count_label.setAlignment(Qt.AlignCenter); self.rails_count_label.setStyleSheet("font-size: 28pt; font-weight: bold;")
        rails_group = self.create_summary_group("Top Rails (Selected Month)", self.rails_count_label, "RailsGroup")
        summary_layout.addWidget(rails_group); prod_layout.addLayout(summary_layout)
        self.production_table = QTableWidget(); self.production_table.setColumnCount(4)
        self.production_table.setHorizontalHeaderLabels(["Date", "Bodies", "Pods", "Top Rails"])
        self.production_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.production_table.setAlternatingRowColors(True); prod_layout.addWidget(self.production_table)
        note_label = QLabel("Data for the selected month is fetched from the server. Days with no production or API errors will show 0.")
        note_label.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;"); note_label.setAlignment(Qt.AlignCenter)
        note_label.setWordWrap(True); prod_layout.addWidget(note_label)

    def setup_assembly_deficit_tab(self):
        main_tab_layout = QVBoxLayout(self.assembly_deficit_tab)
        main_tab_layout.setSpacing(10)
        info_label = QLabel(
            "This tab shows current stock for Bodies and Top Rails for each table type, "
            "and indicates what is needed to assemble more tables based on the component with the highest stock."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-size: 9pt; color: #555; margin-bottom: 10px; padding: 5px; background-color: #e9edf0; border-radius: 5px;")
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
            label_stylesheet = f"color: {text_color_hex}; font-size: 10pt;" # Base style for labels in this box

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
        
        # Increase spacing between elements
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        self.dashboard_stacked_widget = QStackedWidget()
        main_layout.addWidget(self.dashboard_stacked_widget)

        # Page 1: Performance
        page1 = QWidget(); page1.setObjectName("DashboardPage")
        layout1 = QVBoxLayout(page1); layout1.setAlignment(Qt.AlignTop)  # Changed to AlignTop
        header1 = QLabel("Top Rail Performance"); header1.setObjectName("DashboardHeader"); header1.setAlignment(Qt.AlignCenter)
        layout1.addWidget(header1)
        
        # Current Performance
        current_perf_group = QGroupBox("Current Performance")
        current_perf_layout = QFormLayout()
        self.tr_dash_current_time_label = QLabel("N/A"); self.tr_dash_current_time_label.setObjectName("DashboardMetricValue")
        current_perf_layout.addRow(QLabel("Time on Current Rail:", objectName="DashboardMetricLabel"), self.tr_dash_current_time_label)
        self.tr_dash_avg_time_label = QLabel("N/A"); self.tr_dash_avg_time_label.setObjectName("DashboardMetricValue")
        current_perf_layout.addRow(QLabel("Average Rail Time:", objectName="DashboardMetricLabel"), self.tr_dash_avg_time_label)
        self.tr_dash_predicted_label = QLabel("N/A"); self.tr_dash_predicted_label.setObjectName("DashboardMetricValue")
        current_perf_layout.addRow(QLabel("Predicted Today:", objectName="DashboardMetricLabel"), self.tr_dash_predicted_label)
        current_perf_group.setLayout(current_perf_layout)
        layout1.addWidget(current_perf_group)
        
        # Production Statistics
        prod_stats_group = QGroupBox("Production Statistics")
        prod_stats_layout = QFormLayout()
        self.tr_dash_daily_label = QLabel("0"); self.tr_dash_daily_label.setObjectName("DashboardMetricValue")
        prod_stats_layout.addRow(QLabel("Today's Production:", objectName="DashboardMetricLabel"), self.tr_dash_daily_label)
        self.tr_dash_monthly_label = QLabel("0"); self.tr_dash_monthly_label.setObjectName("DashboardMetricValue")
        prod_stats_layout.addRow(QLabel("This Month:", objectName="DashboardMetricLabel"), self.tr_dash_monthly_label)
        self.tr_dash_yearly_label = QLabel("0"); self.tr_dash_yearly_label.setObjectName("DashboardMetricValue")
        prod_stats_layout.addRow(QLabel("This Year:", objectName="DashboardMetricLabel"), self.tr_dash_yearly_label)
        prod_stats_group.setLayout(prod_stats_layout)
        layout1.addWidget(prod_stats_group)
        
        layout1.addStretch()
        self.dashboard_stacked_widget.addWidget(page1)

        # Page 2: Parts Inventory - Updated layout
        page2 = QWidget(); page2.setObjectName("DashboardPage")
        layout2 = QVBoxLayout(page2)
        header2 = QLabel("Top Rail - Parts Inventory"); header2.setObjectName("DashboardHeader")
        layout2.addWidget(header2)

        # Create a table instead of form layout
        self.tr_parts_table = QTableWidget()
        self.tr_parts_table.setColumnCount(4)
        self.tr_parts_table.setHorizontalHeaderLabels(["Part Name", "In Stock", "Per Rail", "Rails Possible"])
        self.tr_parts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tr_parts_table.setAlternatingRowColors(True)
        layout2.addWidget(self.tr_parts_table)
        
        layout2.addStretch()
        self.dashboard_stacked_widget.addWidget(page2)

        # Page 3: Deficits (Top Rails vs Bodies)
        page3 = QWidget(); page3.setObjectName("DashboardPage")
        layout3 = QVBoxLayout(page3); layout3.setAlignment(Qt.AlignTop)
        header3 = QLabel("Top Rail - Assembly Needs"); header3.setObjectName("DashboardHeader"); header3.setAlignment(Qt.AlignCenter)
        layout3.addWidget(header3)
        self.tr_dash_deficits_layout_7ft = QVBoxLayout() # For 7ft tables
        self.tr_dash_deficits_layout_6ft = QVBoxLayout() # For 6ft tables
        
        deficit_content_layout = QHBoxLayout()
        group_7ft_deficit = QGroupBox("7ft Tables"); group_7ft_deficit.setLayout(self.tr_dash_deficits_layout_7ft)
        group_6ft_deficit = QGroupBox("6ft Tables"); group_6ft_deficit.setLayout(self.tr_dash_deficits_layout_6ft)
        deficit_content_layout.addWidget(group_7ft_deficit)
        deficit_content_layout.addWidget(group_6ft_deficit)
        layout3.addLayout(deficit_content_layout)
        layout3.addStretch()
        self.dashboard_stacked_widget.addWidget(page3)

        # Initialize dashboard widgets dictionary (for parts and deficits)
        self.top_rail_dashboard_widgets["parts"] = {}
        self.top_rail_dashboard_widgets["deficits_7ft"] = {}
        self.top_rail_dashboard_widgets["deficits_6ft"] = {}


    def scroll_dashboard_page(self):
        """Cycles through the pages of the Top Rail Dashboard."""
        if hasattr(self, 'dashboard_stacked_widget') and self.dashboard_stacked_widget.count() > 0:
            current_index = self.dashboard_stacked_widget.currentIndex()
            next_index = (current_index + 1) % self.dashboard_stacked_widget.count()
            self.dashboard_stacked_widget.setCurrentIndex(next_index)


    def create_summary_group(self, title, count_label, object_name):
        group_box = QGroupBox(title)
        group_box.setObjectName(object_name)
        layout = QVBoxLayout()
        layout.addWidget(count_label)
        group_box.setLayout(layout)
        return group_box

    def setup_parts_inventory_tab(self):
        parts_layout = QVBoxLayout(self.parts_tab); parts_layout.setSpacing(15)
        parts_group = QGroupBox("3D Printed Parts Stock"); parts_group_layout = QVBoxLayout(parts_group)
        self.parts_table = QTableWidget(); self.parts_table.setColumnCount(4)
        self.parts_table.setHorizontalHeaderLabels(["Part Name", "Current Stock", "Status", "Stock Level"])
        self.parts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.parts_table.verticalHeader().setVisible(False); self.parts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.parts_table.setAlternatingRowColors(True); parts_group_layout.addWidget(self.parts_table)
        parts_layout.addWidget(parts_group)
        info_group = QGroupBox("Inventory Summary"); info_layout = QFormLayout(info_group)
        self.total_parts_label = QLabel("0"); self.low_stock_label = QLabel("0")
        self.low_stock_label.setStyleSheet("font-weight: bold; color: #c0392b;"); self.last_update_label = QLabel("Never")
        info_layout.addRow(QLabel("Total Parts in Stock:"), self.total_parts_label)
        info_layout.addRow(QLabel("Parts Low on Stock:"), self.low_stock_label)
        info_layout.addRow(QLabel("Last Updated:"), self.last_update_label); parts_layout.addWidget(info_group)

    def setup_settings_tab(self, settings_tab):
        settings_layout = QVBoxLayout(settings_tab); settings_layout.setSpacing(20)
        api_group = QGroupBox("API Configuration"); api_layout = QFormLayout(api_group) 
        api_layout.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.api_url_input = QLineEdit(str(self.config.get("API_URL", ""))); api_layout.addRow("API URL:", self.api_url_input)
        port_widget = QWidget(); port_hbox_layout = QHBoxLayout(port_widget); port_hbox_layout.setContentsMargins(0,0,0,0)
        self.use_standard_port = QCheckBox("Use standard HTTP/HTTPS port")
        self.use_standard_port.setChecked(self.config.get("API_PORT") is None); port_hbox_layout.addWidget(self.use_standard_port)
        self.api_port_input = QLineEdit(str(self.config.get("API_PORT", "5000")))
        self.api_port_input.setEnabled(not self.use_standard_port.isChecked()); self.api_port_input.setFixedWidth(80)
        port_hbox_layout.addWidget(QLabel("Custom Port:")); port_hbox_layout.addWidget(self.api_port_input)
        port_hbox_layout.addStretch(); api_layout.addRow(port_widget)
        self.api_token_input = QLineEdit(str(self.config.get("API_TOKEN", ""))); self.api_token_input.setEchoMode(QLineEdit.Password)
        api_layout.addRow("API Token:", self.api_token_input)
        self.use_standard_port.toggled.connect(lambda checked: self.api_port_input.setEnabled(not checked))
        buttons_layout = QHBoxLayout(); self.save_settings_button = QPushButton("Save Settings")
        self.save_settings_button.clicked.connect(self.save_settings); buttons_layout.addWidget(self.save_settings_button)
        self.test_connection_button = QPushButton("Test API Connection") 
        self.test_connection_button.setStyleSheet("background-color: #f39c12; color:white;")
        self.test_connection_button.clicked.connect(self.check_api_connection); buttons_layout.addWidget(self.test_connection_button)
        buttons_layout.addStretch(); api_layout.addRow(buttons_layout); settings_layout.addWidget(api_group)
        
        # Add Timer Settings Group
        timer_group = QGroupBox("Dashboard Settings")
        timer_layout = QFormLayout()
        
        self.scroll_timer_input = QLineEdit(str(self.config.get("SCROLL_TIMER", 10)))
        self.scroll_timer_input.setFixedWidth(80)
        # Only allow integers - use QIntValidator directly since we imported it
        self.scroll_timer_input.setValidator(QIntValidator(1, 60))
        
        timer_layout.addRow("Screen scroll interval (seconds):", self.scroll_timer_input)
        timer_group.setLayout(timer_layout)
        settings_layout.addWidget(timer_group)
        
        settings_layout.addStretch()
        
        about_group = QGroupBox("About"); about_layout = QVBoxLayout(about_group)
        self.about_text_label = QLabel(f"Pool Table Factory Tracker v1.6\n\nDisplays production and inventory data.\nConnects to: {self.api_client.base_url}") # Version bump
        self.about_text_label.setAlignment(Qt.AlignCenter); self.about_text_label.setWordWrap(True)
        about_layout.addWidget(self.about_text_label); settings_layout.addWidget(about_group)

    def update_production_table(self, daily_data_list):
        self.production_table.setRowCount(len(daily_data_list))
        for row, day_data_item in enumerate(daily_data_list):
            try: date_obj = datetime.strptime(day_data_item["date"], "%Y-%m-%d").date(); friendly_date = date_obj.strftime("%a, %d %b %Y") 
            except ValueError: friendly_date = day_data_item["date"] 
            date_item = QTableWidgetItem(friendly_date)
            bodies_item = QTableWidgetItem(str(day_data_item.get("bodies",0))); pods_item = QTableWidgetItem(str(day_data_item.get("pods",0)))
            rails_item = QTableWidgetItem(str(day_data_item.get("top_rails",0)))
            for item in [bodies_item, pods_item, rails_item]: item.setTextAlignment(Qt.AlignCenter)
            if day_data_item["date"] == datetime.now().date().strftime("%Y-%m-%d"):
                for item_widget in [date_item, bodies_item, pods_item, rails_item]:
                    font = item_widget.font(); font.setBold(True); item_widget.setFont(font); item_widget.setBackground(QColor("#e6f7ff")) 
            if day_data_item.get("error_info"):
                tooltip_text = day_data_item['error_info']
                for item_widget in [date_item, bodies_item, pods_item, rails_item]:
                    item_widget.setForeground(QColor("#999999")); item_widget.setToolTip(tooltip_text)
            else: 
                for item_widget in [date_item, bodies_item, pods_item, rails_item]:
                    item_widget.setForeground(QColor("#333333")); item_widget.setToolTip("")
            self.production_table.setItem(row, 0, date_item); self.production_table.setItem(row, 1, bodies_item)
            self.production_table.setItem(row, 2, pods_item); self.production_table.setItem(row, 3, rails_item)
        self.production_table.resizeColumnsToContents()

    def update_parts_inventory_table(self, inventory_data):
        if not inventory_data or 'printed_parts_current' not in inventory_data:
            self.parts_table.setRowCount(0); self.total_parts_label.setText("N/A")
            self.low_stock_label.setText("N/A")
            if inventory_data is None: self.last_update_label.setText(f"Update Failed: {datetime.now().strftime('%H:%M:%S')}")
            return
        printed_parts = inventory_data.get('printed_parts_current', {}); sorted_parts = sorted(printed_parts.items())
        self.parts_table.setRowCount(len(sorted_parts)); total_parts = 0; low_stock_count = 0
        for row, (part_name, count) in enumerate(sorted_parts):
            name_item = QTableWidgetItem(part_name); actual_count = count if count is not None else 0
            count_item = QTableWidgetItem(str(actual_count)); count_item.setTextAlignment(Qt.AlignCenter)
            status = "Low" if actual_count < 5 else "OK" if actual_count < 20 else "Good"
            status_item = QTableWidgetItem(status); status_item.setTextAlignment(Qt.AlignCenter)
            if status == "Low": status_item.setForeground(QColor("#d32f2f")); low_stock_count +=1
            elif status == "OK": status_item.setForeground(QColor("#f57c00"))
            else: status_item.setForeground(QColor("#388e3c"))
            self.parts_table.setItem(row, 0, name_item); self.parts_table.setItem(row, 1, count_item); self.parts_table.setItem(row, 2, status_item)
            progress_widget = QWidget(); progress_layout = QHBoxLayout(progress_widget); progress_layout.setContentsMargins(5,2,5,2)
            progress_bar = QProgressBar(); progress_bar.setMinimum(0); progress_bar.setMaximum(100)
            percentage = min(100, int((actual_count / 30.0) * 100)) if actual_count else 0; progress_bar.setValue(percentage)
            if percentage < 20: progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #ef5350; border-radius: 4px;}")
            elif percentage < 60: progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #ffa726; border-radius: 4px;}")
            else: progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #66bb6a; border-radius: 4px;}")
            progress_layout.addWidget(progress_bar); self.parts_table.setCellWidget(row, 3, progress_widget); total_parts += actual_count
        self.total_parts_label.setText(str(total_parts)); self.low_stock_label.setText(str(low_stock_count))
        self.last_update_label.setText(datetime.now().strftime('%d %b %Y, %H:%M:%S')); self.parts_table.resizeColumnsToContents()

    def update_summary_counts(self, daily_data_list):
        total_bodies = sum(day.get("bodies", 0) for day in daily_data_list) 
        total_pods = sum(day.get("pods", 0) for day in daily_data_list)
        total_rails = sum(day.get("top_rails", 0) for day in daily_data_list)
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
            
            base_label_style = f"color: {base_text_color}; font-size: 10pt;"
            shortage_label_style = f"color: #c62828; font-weight: bold; font-size: 10pt;" # Red and bold for shortage

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
                status_style = f"color: #2e7d32; font-weight: bold; font-size: 10pt;" # Green and bold
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
            # ...existing code for handling no data...
            return

        # --- Page 1: Performance - Update with real production data ---
        if hasattr(self, 'tr_dash_current_time_label'):
            today = datetime.now().date()
            
            # Get today's production count from today's date in monthly data
            current_month = today.month
            current_year = today.year
            daily_data = self.api_client.get_production_for_month(current_year, current_month)
            
            # Find today's entry and get top_rails count
            today_str = today.strftime("%Y-%m-%d")
            today_production = next(
                (day.get("top_rails", 0) for day in daily_data 
                 if day.get("date") == today_str), 
                0
            )
            self.tr_dash_daily_label.setText(str(today_production))
            
            # Get monthly production from production summary
            monthly_data = self.api_client.get_production_summary(current_year, current_month)
            if monthly_data:
                monthly_count = monthly_data.get("current_production", {}).get("total", {}).get("top_rails", 0)
                self.tr_dash_monthly_label.setText(str(monthly_count))
                
                # Calculate yearly production from all months
                yearly_count = sum(
                    self.api_client.get_production_summary(current_year, m)
                    .get("current_production", {}).get("total", {}).get("top_rails", 0)
                    for m in range(1, 13)
                )
                self.tr_dash_yearly_label.setText(str(yearly_count))

        # --- Page 2: Parts Inventory - Update table with real data ---
        if hasattr(self, 'tr_parts_table'):
            top_rail_parts = {
                "Top rail trim long length": 2,
                "Top rail trim short length": 4,
                "Chrome corner": 4,
                "Center pockets": 2,
                "Corner pockets": 4,
                "M5 x 18 x 1.25 Penny Mudguard Washer": 16,
                "M5 x 20 Socket Cap Screw": 16,
                "Catch Plate": 12,
                "4.8x32mm Self Tapping Screw": 24
            }
            
            hardware_parts_stock = self.inventory_data.get("hardware_parts_current", {})
            table_parts_stock = self.inventory_data.get("table_parts_current", {})
            
            # Clear and set up table
            self.tr_parts_table.setRowCount(len(top_rail_parts))
            row = 0
            
            for part_name, qty_per_rail in top_rail_parts.items():
                # Get stock count from either hardware or table parts
                stock_count = hardware_parts_stock.get(part_name, table_parts_stock.get(part_name, 0))
                
                # Calculate how many rails can be made with this part
                rails_possible = stock_count // qty_per_rail if qty_per_rail > 0 else 0
                
                # Create table items
                name_item = QTableWidgetItem(part_name)
                stock_item = QTableWidgetItem(str(stock_count))
                per_rail_item = QTableWidgetItem(str(qty_per_rail))
                rails_item = QTableWidgetItem(str(rails_possible))
                
                # Center align numbers
                stock_item.setTextAlignment(Qt.AlignCenter)
                per_rail_item.setTextAlignment(Qt.AlignCenter)
                rails_item.setTextAlignment(Qt.AlignCenter)
                
                # Color coding based on rails possible
                if rails_possible < 5:
                    color = QColor("#c62828")  # Red
                elif rails_possible < 10:
                    color = QColor("#f57c00")  # Orange
                else:
                    color = QColor("#2e7d32")  # Green
                
                stock_item.setForeground(color)
                rails_item.setForeground(color)
                
                # Set items in table
                self.tr_parts_table.setItem(row, 0, name_item)
                self.tr_parts_table.setItem(row, 1, stock_item)
                self.tr_parts_table.setItem(row, 2, per_rail_item)
                self.tr_parts_table.setItem(row, 3, rails_item)
                
                row += 1
            
            # Adjust column widths
            self.tr_parts_table.resizeColumnsToContents()

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
                    form_layout_deficit = QFormLayout(color_group_deficit)
                    form_layout_deficit.setContentsMargins(15, 25, 15, 15)  # Add more padding
                    
                    hex_color_code = self.TABLE_FINISH_COLORS.get(color_key, self.TABLE_FINISH_COLORS["Default"])
                    q_color = QColor(hex_color_code)
                    
                    color_group_deficit.setAutoFillBackground(True)
                    color_group_deficit.setStyleSheet(f"""
                        QGroupBox {{
                            border: 1px solid #d0d0d0;
                            border-radius: 8px;
                            margin-top: 20px;
                            padding: 15px;
                            background-image: url({hex_color_code.replace(os.sep, '/')});
                            background-repeat: no-repeat;
                            background-size: cover;
                            background-position: center;
                            background-origin: content;
                            background-color: transparent;
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
                    label_style = f"color: {text_color}; font-size: 11pt; margin: 2px;"  # Increased font size and margin
                    
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
                base_deficit_value_style = "font-size: 10pt;"
                widgets["body_stock"].setStyleSheet(base_deficit_value_style) 
                widgets["rail_stock"].setStyleSheet(base_deficit_value_style) 

                status_text = ""
                status_style = "font-size: 10pt; color: #555;" # Default neutral
                
                if body_stock == 0 and rail_stock == 0:
                    status_text = "No bodies or rails."
                    widgets["body_stock"].setStyleSheet("font-size: 10pt; color: #c62828; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet("font-size: 10pt; color: #c62828; font-weight: bold;")
                elif body_stock == rail_stock:
                    status_text = f"Balanced. Can make {body_stock} sets."
                    status_style = "font-size: 10pt; font-weight: bold; color: #2e7d32;"
                    # Make both values green when balanced
                    widgets["body_stock"].setStyleSheet("font-size: 10pt; color: #2e7d32; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet("font-size: 10pt; color: #2e7d32; font-weight: bold;")
                elif body_stock > rail_stock:
                    needed = body_stock - rail_stock
                    status_text = f"{needed} more Top Rails needed."
                    status_style = "font-size: 10pt; font-weight: bold; color: #c62828;"
                    widgets["body_stock"].setStyleSheet("font-size: 10pt; color: #2e7d32; font-weight: bold;") # Green for higher stock
                    widgets["rail_stock"].setStyleSheet("font-size: 10pt; color: #c62828; font-weight: bold;")
                else: # rail_stock > body_stock
                    needed = rail_stock - body_stock
                    status_text = f"{needed} more Bodies needed."
                    status_style = "font-size: 10pt; font-weight: bold; color: #c62828;"
                    widgets["body_stock"].setStyleSheet("font-size: 10pt; color: #c62828; font-weight: bold;")
                    widgets["rail_stock"].setStyleSheet("font-size: 10pt; color: #2e7d32; font-weight: bold;") # Green for higher stock
                
                widgets["status"].setText(status_text)
                widgets["status"].setStyleSheet(status_style)


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
            QMessageBox.warning(self, "Connection Error", f"Unable to connect to the API at {self.api_client.base_url}.")
        self.style().polish(self.connection_label)

    def refresh_production_data(self):
        self.statusBar().showMessage("Refreshing production data...")
        self.refresh_button.setEnabled(False)
        selected_year = int(self.prod_year_combo.currentText())
        selected_month = self.prod_month_combo.currentData()
        daily_data = self.api_client.get_production_for_month(selected_year, selected_month)
        self.update_production_table(daily_data)
        self.update_summary_counts(daily_data)
        self.statusBar().showMessage(f"Production data refreshed for {self.prod_month_combo.currentText()} {selected_year} at {datetime.now().strftime('%H:%M:%S')}")
        self.refresh_button.setEnabled(True)

    def refresh_all_data(self):
        self.statusBar().showMessage("Refreshing all data from API...")
        self.refresh_button.setEnabled(False) 

        selected_prod_year = int(self.prod_year_combo.currentText())
        selected_prod_month = self.prod_month_combo.currentData() 
        prod_daily_data = self.api_client.get_production_for_month(selected_prod_year, selected_prod_month)
        self.update_production_table(prod_daily_data)
        self.update_summary_counts(prod_daily_data)

        self.inventory_data = self.api_client.get_inventory_summary()
        self.update_parts_inventory_table(self.inventory_data)
        self.update_assembly_deficit_display() 
        self.update_top_rail_dashboard() # Refresh the new dashboard

        self.statusBar().showMessage(f"All data refreshed at {datetime.now().strftime('%H:%M:%S')}")
        self.refresh_button.setEnabled(True)

    def save_settings(self):
        self.config["API_URL"] = self.api_url_input.text().strip()
        self.config["API_TOKEN"] = self.api_token_input.text().strip()
        if self.use_standard_port.isChecked(): self.config["API_PORT"] = None
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

def main():
    app = QApplication(sys.argv)
    # Removed global QtGui as it's not best practice and QIcon/QColor/QPalette are imported directly
    # from PyQt5 import QtGui 

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
