from picard.plugin3.api import OptionsPage
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                             QTextEdit, QLineEdit, QCheckBox, QGroupBox, QComboBox,
                             QScrollArea, QWidget, QSizePolicy, QPushButton, QListWidget,
                             QListWidgetItem, QStackedWidget, QFrame, QSplitter, QPlainTextEdit,
                             QFileDialog, QMessageBox, QDialog, QDialogButtonBox)
from PyQt6.QtCore import Qt, QUrl, QEventLoop, QTimer
from PyQt6.QtGui import QDesktopServices, QFont, QTextCursor
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
import json
import datetime

from . import utils, collector, sources


class ValidationResultsDialog(QDialog):
    """Custom dialog for displaying validation results with scrolling support."""
    
    def __init__(self, parent, title, errors=None, warnings=None, success_message=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(500, 300)
        self.resize(700, 500)
        
        layout = QVBoxLayout(self)
        
        # Content area with scrolling
        if errors or warnings:
            # Create scrollable text area
            self.text_area = QPlainTextEdit(self)
            self.text_area.setReadOnly(True)
            self.text_area.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            # Use monospace font for better readability
            font = QFont("Consolas", 9)
            if font.exactMatch():
                self.text_area.setFont(font)
            else:
                self.text_area.setFont(QFont("Courier New", 9))
            
            # Build content with logical grouping
            content_lines = []
            if errors:
                content_lines.append(self.tr("Errors:"))
                content_lines.append("=" * 60)
                for error in errors:
                    content_lines.append(error)
                content_lines.append("")
            
            if warnings:
                content_lines.append(self.tr("Warnings:"))
                content_lines.append("=" * 60)
                for warning in warnings:
                    content_lines.append(warning)
            
            self.text_area.setPlainText("\n".join(content_lines))
            
            # Store full content for copy/save
            self.full_content = "\n".join(content_lines)
            
            layout.addWidget(self.text_area)
            
            # Button area
            button_layout = QHBoxLayout()
            
            # Copy to clipboard button
            btn_copy = QPushButton(self.tr("Copy to Clipboard"), self)
            btn_copy.clicked.connect(self._copy_to_clipboard)
            button_layout.addWidget(btn_copy)
            
            # Save to file button
            btn_save = QPushButton(self.tr("Save to File..."), self)
            btn_save.clicked.connect(self._save_to_file)
            button_layout.addWidget(btn_save)
            
            button_layout.addStretch()
            
            # Close button
            btn_close = QPushButton(self.tr("Close"), self)
            btn_close.setDefault(True)
            btn_close.clicked.connect(self.accept)
            button_layout.addWidget(btn_close)
            
            layout.addLayout(button_layout)
        else:
            # Success message - simple display
            label = QLabel(success_message, self)
            label.setWordWrap(True)
            layout.addWidget(label)
            
            # Store for copy/save
            self.full_content = success_message
            
            # Button area for success case
            button_layout = QHBoxLayout()
            button_layout.addStretch()
            
            # Copy to clipboard button
            btn_copy = QPushButton(self.tr("Copy"), self)
            btn_copy.clicked.connect(self._copy_to_clipboard)
            button_layout.addWidget(btn_copy)
            
            # Close button
            btn_close = QPushButton(self.tr("Close"), self)
            btn_close.setDefault(True)
            btn_close.clicked.connect(self.accept)
            button_layout.addWidget(btn_close)
            
            layout.addLayout(button_layout)
    
    def _copy_to_clipboard(self):
        """Copy full content to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self.full_content)
        # Show brief feedback
        btn = self.sender()
        original_text = btn.text()
        btn.setText(self.tr("Copied!"))
        QTimer.singleShot(2000, lambda: btn.setText(original_text))
    
    def _save_to_file(self):
        """Save full content to a text file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Validation Results"),
            "",
            self.tr("Text Files (*.txt);;All Files (*)")
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.full_content)
                QMessageBox.information(self, self.tr("Save Successful"), 
                                       self.tr("Results saved to:\n{}").format(file_path))
            except Exception as e:
                QMessageBox.warning(self, self.tr("Save Failed"), 
                                  self.tr("Failed to save file:\n{}").format(str(e)))


class UMAOptionsPage(OptionsPage):
    NAME = "dj_genre_selector_options"
    TITLE = "DJ Genre Selector"
    PARENT = "plugins"

    # Config keys for preview state
    CONFIG_KEY_LAST_MBID = "uma_preview_last_mbid"
    CONFIG_KEY_LAST_RESULT = "uma_preview_last_result"
    CONFIG_KEY_LAST_PREVIEW = "uma_last_preview"

    def __init__(self, api=None, parent=None):
        self.api = api
        super().__init__(parent)

    def _get_config(self, key, default=None):
        if self.api is None:
            return default
        try:
            return self.api.global_config.setting[key]
        except (KeyError, TypeError, AttributeError):
            return default
        
        # Initialize persistent preview state
        self._preview_state = {
            "mbid": "",
            "mb_release": None,
            "sources": {},
            "clusters": [],
            "final_genre": "",
            "final_style": [],
            "final_comment": "",
            "timestamp": None,
        }
        
        # Initialize display cache attribute
        self._last_preview = None
        
        # Debug log when options page is created
        utils.log_info("DJ Genre Selector options page created")
        
        # Set minimum size
        self.setMinimumSize(900, 650)
        
        # Main layout: vertical (status bar + splitter)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # ============================================
        # Top Bar: Pipeline Status Indicator
        # ============================================
        status_bar = self._create_status_bar()
        main_layout.addWidget(status_bar)
        
        # ============================================
        # Splitter: Sidebar + Content Area
        # ============================================
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left Sidebar: Navigation
        sidebar = self._create_sidebar()
        sidebar.setMaximumWidth(200)
        sidebar.setMinimumWidth(180)
        splitter.addWidget(sidebar)
        
        # Right Content Area: Stacked Widget for Sections
        self.content_stack = QStackedWidget()
        self.content_stack.setMinimumWidth(700)
        
        # Create section widgets
        self.sections = {}
        self.sections['sources'] = self._create_sources_section()
        self.sections['merge'] = self._create_merge_section()
        self.sections['mapping'] = self._create_mapping_section()
        self.sections['filters'] = self._create_filters_section()
        self.sections['preview'] = self._create_preview_section()
        
        # Add sections to stack
        for section_widget in self.sections.values():
            self.content_stack.addWidget(section_widget)
        
        splitter.addWidget(self.content_stack)
        splitter.setStretchFactor(0, 0)  # Sidebar doesn't stretch
        splitter.setStretchFactor(1, 1)  # Content area stretches
        
        main_layout.addWidget(splitter)
        self.setLayout(main_layout)
        
        # Connect sidebar navigation
        self._connect_navigation()
        
        # Initialize status bar
        self._update_status_bar()
    
    def _create_status_bar(self):
        """Create the always-visible pipeline status indicator bar."""
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(8, 4, 8, 4)
        status_layout.setSpacing(12)
        
        # Status segments (clickable labels)
        self.status_sources = QLabel("Sources: ...")
        self.status_sources.setStyleSheet("QLabel { padding: 2px 6px; border: 1px solid #ccc; border-radius: 3px; }")
        self.status_sources.mousePressEvent = lambda e: self._navigate_to_section('sources')
        self.status_sources.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.status_mapping = QLabel("Mapping: ...")
        self.status_mapping.setStyleSheet("QLabel { padding: 2px 6px; border: 1px solid #ccc; border-radius: 3px; }")
        self.status_mapping.mousePressEvent = lambda e: self._navigate_to_section('mapping')
        self.status_mapping.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.status_clusters = QLabel("Clusters: ...")
        self.status_clusters.setStyleSheet("QLabel { padding: 2px 6px; border: 1px solid #ccc; border-radius: 3px; }")
        self.status_clusters.mousePressEvent = lambda e: self._navigate_to_section('mapping')
        self.status_clusters.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.status_filters = QLabel("Filters: ...")
        self.status_filters.setStyleSheet("QLabel { padding: 2px 6px; border: 1px solid #ccc; border-radius: 3px; }")
        self.status_filters.mousePressEvent = lambda e: self._navigate_to_section('filters')
        self.status_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.status_output = QLabel("Output: Genre + Style + Comment")
        self.status_output.setStyleSheet("QLabel { padding: 2px 6px; border: 1px solid #ccc; border-radius: 3px; }")
        
        status_layout.addWidget(self.status_sources)
        status_layout.addWidget(QLabel("→"))
        status_layout.addWidget(self.status_mapping)
        status_layout.addWidget(QLabel("→"))
        status_layout.addWidget(self.status_clusters)
        status_layout.addWidget(QLabel("→"))
        status_layout.addWidget(self.status_filters)
        status_layout.addWidget(QLabel("→"))
        status_layout.addWidget(self.status_output)
        status_layout.addStretch()
        
        status_frame.setLayout(status_layout)
        return status_frame
    
    def _create_sidebar(self):
        """Create left sidebar navigation."""
        sidebar = QListWidget()
        sidebar.setMaximumWidth(200)
        sidebar.setMinimumWidth(180)
        
        # Navigation items
        items = [
            ("Sources", "sources"),
            ("Merge & Priority", "merge"),
            ("Tag Mapping", "mapping"),
            ("Generic Filters", "filters"),
            ("Preview & Debug", "preview"),
        ]
        
        for label, section_id in items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, section_id)
            sidebar.addItem(item)
        
        sidebar.setCurrentRow(0)  # Select first item
        self.sidebar = sidebar
        return sidebar
    
    def _connect_navigation(self):
        """Connect sidebar navigation to content stack."""
        def on_selection_changed():
            current = self.sidebar.currentItem()
            if current:
                section_id = current.data(Qt.ItemDataRole.UserRole)
                section_index = list(self.sections.keys()).index(section_id)
                self.content_stack.setCurrentIndex(section_index)
                self._update_status_bar()
        
        self.sidebar.currentItemChanged.connect(lambda: on_selection_changed())
    
    def _navigate_to_section(self, section_id):
        """Navigate to a specific section (called from status bar)."""
        section_index = list(self.sections.keys()).index(section_id)
        self.content_stack.setCurrentIndex(section_index)
        self.sidebar.setCurrentRow(section_index)
    
    def _update_status_bar(self):
        """Update status bar with current configuration state."""
        # Sources status
        enabled_sources = []
        if self._get_config("uma_enable_bandcamp", True):
            enabled_sources.append("Bandcamp")
        if self._get_config("uma_enable_discogs", True):
            discogs_token = self._get_config("uma_discogs_token", "")
            if discogs_token:
                enabled_sources.append("Discogs")
            else:
                enabled_sources.append("Discogs (no token)")
        self.status_sources.setText(f"Sources: {len(enabled_sources)} enabled")

        # Mapping status
        mapping_text = self._get_config("uma_tag_mapping", "")
        rule_count = len([l for l in mapping_text.splitlines() if '=' in l.strip()]) if mapping_text else 0
        self.status_mapping.setText(f"Mapping: {rule_count} rules")

        # Clusters status
        self.status_clusters.setText("Clusters: 12 available")

        # Filters status
        generic_text = self._get_config("uma_generic_genres", "")
        filter_count = len([l for l in generic_text.splitlines() if l.strip()]) if generic_text else 0
        self.status_filters.setText(f"Filters: {filter_count} patterns")
    
    def _create_sources_section(self):
        """Create Sources section widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        # Two-column layout for sources
        sources_layout = QHBoxLayout()
        
        # Column 1: Bandcamp
        bandcamp_group = QGroupBox("Bandcamp Source")
        bandcamp_layout = QVBoxLayout()
        bandcamp_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        self.check_bandcamp = QCheckBox("Enable Bandcamp")
        self.check_bandcamp.setToolTip("Fetch tags and metadata from Bandcamp when a Bandcamp URL is found in MusicBrainz relations")
        bandcamp_layout.addWidget(self.check_bandcamp)
        
        self.check_bandcamp_fallback = QCheckBox("Enable fallback search (artist + album)")
        self.check_bandcamp_fallback.setToolTip("If enabled, search Bandcamp by artist and album title when no Bandcamp URL is found in MusicBrainz")
        bandcamp_layout.addWidget(self.check_bandcamp_fallback)
        
        bandcamp_group.setLayout(bandcamp_layout)
        sources_layout.addWidget(bandcamp_group)
        
        # Column 2: Discogs
        discogs_group = QGroupBox("Discogs Source")
        discogs_layout = QVBoxLayout()
        discogs_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        self.check_discogs = QCheckBox("Enable Discogs")
        self.check_discogs.setToolTip("Fetch genres and styles from Discogs when a Discogs URL is found in MusicBrainz relations")
        discogs_layout.addWidget(self.check_discogs)
        
        discogs_layout.addWidget(QLabel("Personal Access Token:"))
        
        token_layout = QHBoxLayout()
        self.input_discogs_token = QLineEdit()
        self.input_discogs_token.setEchoMode(QLineEdit.Password)
        self.input_discogs_token.setPlaceholderText("Enter your Discogs token")
        self.input_discogs_token.setToolTip("Required for Discogs API access. Get token at discogs.com/settings/developers")
        token_layout.addWidget(self.input_discogs_token)
        
        self.btn_get_token = QPushButton("Get token…")
        self.btn_get_token.clicked.connect(self.open_discogs_token_page)
        token_layout.addWidget(self.btn_get_token)
        discogs_layout.addLayout(token_layout)
        
        # Token status indicator
        self.token_status = QLabel("⚠ Token missing")
        self.token_status.setStyleSheet("QLabel { color: #d4a017; }")
        discogs_layout.addWidget(self.token_status)
        
        discogs_group.setLayout(discogs_layout)
        sources_layout.addWidget(discogs_group)
        
        layout.addLayout(sources_layout)
        
        # General settings (full width)
        general_group = QGroupBox("General")
        general_layout = QVBoxLayout()
        self.check_debug = QCheckBox("Enable Debug Logging")
        self.check_debug.setToolTip("Logs detailed processing steps to Picard's debug log")
        general_layout.addWidget(self.check_debug)
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        
        # Connect token field to update status
        self.input_discogs_token.textChanged.connect(self._update_token_status)
        
        return widget
    
    def _update_token_status(self):
        """Update Discogs token status indicator."""
        token = self.input_discogs_token.text().strip()
        if token:
            self.token_status.setText("✓ Token set")
            self.token_status.setStyleSheet("QLabel { color: #28a745; }")
        else:
            self.token_status.setText("⚠ Token missing")
            self.token_status.setStyleSheet("QLabel { color: #d4a017; }")
    
    def _create_merge_section(self):
        """Create Merge & Priority section widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        # Two-column layout
        merge_layout = QHBoxLayout()
        
        # Column 1: Genre
        genre_group = QGroupBox("Genre Field")
        genre_layout = QVBoxLayout()
        genre_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        genre_layout.addWidget(QLabel("Source Priority (highest to lowest):"))
        # Improved priority UI: two dropdowns for now (simpler than drag-and-drop)
        priority_genre_layout = QHBoxLayout()
        priority_genre_layout.addWidget(QLabel("1st:"))
        self.combo_prio_genre_1 = QComboBox()
        self.combo_prio_genre_1.addItems(["bandcamp", "discogs"])
        priority_genre_layout.addWidget(self.combo_prio_genre_1)
        priority_genre_layout.addWidget(QLabel("2nd:"))
        self.combo_prio_genre_2 = QComboBox()
        self.combo_prio_genre_2.addItems(["bandcamp", "discogs"])
        priority_genre_layout.addWidget(self.combo_prio_genre_2)
        genre_layout.addLayout(priority_genre_layout)
        
        # Connect to prevent duplicate selection
        def update_genre_2():
            selected_1 = self.combo_prio_genre_1.currentText()
            current_2 = self.combo_prio_genre_2.currentText()
            if selected_1 == current_2:
                # Swap to the other option
                other = "discogs" if selected_1 == "bandcamp" else "bandcamp"
                self.combo_prio_genre_2.setCurrentText(other)
        
        def update_genre_1():
            selected_2 = self.combo_prio_genre_2.currentText()
            current_1 = self.combo_prio_genre_1.currentText()
            if selected_2 == current_1:
                other = "discogs" if selected_2 == "bandcamp" else "bandcamp"
                self.combo_prio_genre_1.setCurrentText(other)
        
        self.combo_prio_genre_1.currentTextChanged.connect(update_genre_2)
        self.combo_prio_genre_2.currentTextChanged.connect(update_genre_1)
        
        genre_layout.addWidget(QLabel("Order determines which source's genres are preferred when multiple sources provide genres"))
        
        genre_layout.addWidget(QLabel(""))
        genre_layout.addWidget(QLabel("Merge Mode (when genre already exists in track):"))
        self.combo_mode_genre = QComboBox()
        # Note: Using "overwrite" for backward compatibility with existing config
        # The spec suggests "replace" but existing code uses "overwrite"
        self.combo_mode_genre.addItems(["append", "overwrite", "keep"])
        self.combo_mode_genre.setToolTip(
            "Append: Add UMA genres to existing track genres\n"
            "Overwrite: Replace existing genres with UMA genres\n"
            "Keep: Only add if track has no genre"
        )
        genre_layout.addWidget(self.combo_mode_genre)
        genre_layout.addWidget(QLabel("Controls how UMA genres interact with existing Picard metadata. 'Append' is recommended."))
        
        genre_group.setLayout(genre_layout)
        merge_layout.addWidget(genre_group)
        
        # Column 2: Style
        style_group = QGroupBox("Style Field")
        style_layout = QVBoxLayout()
        style_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        style_layout.addWidget(QLabel("Source Priority (highest to lowest):"))
        priority_style_layout = QHBoxLayout()
        priority_style_layout.addWidget(QLabel("1st:"))
        self.combo_prio_style_1 = QComboBox()
        self.combo_prio_style_1.addItems(["bandcamp", "discogs"])
        priority_style_layout.addWidget(self.combo_prio_style_1)
        priority_style_layout.addWidget(QLabel("2nd:"))
        self.combo_prio_style_2 = QComboBox()
        self.combo_prio_style_2.addItems(["bandcamp", "discogs"])
        priority_style_layout.addWidget(self.combo_prio_style_2)
        style_layout.addLayout(priority_style_layout)
        
        # Connect to prevent duplicate selection
        def update_style_2():
            selected_1 = self.combo_prio_style_1.currentText()
            current_2 = self.combo_prio_style_2.currentText()
            if selected_1 == current_2:
                other = "discogs" if selected_1 == "bandcamp" else "bandcamp"
                self.combo_prio_style_2.setCurrentText(other)
        
        def update_style_1():
            selected_2 = self.combo_prio_style_2.currentText()
            current_1 = self.combo_prio_style_1.currentText()
            if selected_2 == current_1:
                other = "discogs" if selected_2 == "bandcamp" else "bandcamp"
                self.combo_prio_style_1.setCurrentText(other)
        
        self.combo_prio_style_1.currentTextChanged.connect(update_style_2)
        self.combo_prio_style_2.currentTextChanged.connect(update_style_1)
        
        style_layout.addWidget(QLabel("Style tags are always appended from enabled sources in priority order"))
        
        style_layout.addWidget(QLabel(""))
        style_layout.addWidget(QLabel("Merge Mode (when style already exists in track):"))
        self.combo_mode_style = QComboBox()
        # Note: Using "overwrite" for backward compatibility with existing config
        self.combo_mode_style.addItems(["append", "overwrite", "keep"])
        self.combo_mode_style.setToolTip(
            "Append: Add UMA styles to existing track styles\n"
            "Overwrite: Replace existing styles with UMA styles\n"
            "Keep: Only add if track has no style"
        )
        style_layout.addWidget(self.combo_mode_style)
        style_layout.addWidget(QLabel("Controls how UMA styles interact with existing Picard metadata."))
        
        style_group.setLayout(style_layout)
        merge_layout.addWidget(style_group)
        
        layout.addLayout(merge_layout)
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_mapping_section(self):
        """Create Tag Mapping section widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_validate_mapping = QPushButton(self.tr("Validate"))
        self.btn_validate_mapping.setToolTip(self.tr("Check mapping table for syntax errors"))
        self.btn_validate_mapping.clicked.connect(self._validate_mapping)
        toolbar.addWidget(self.btn_validate_mapping)
        self.btn_import_mapping = QPushButton(self.tr("Import from file…"))
        self.btn_import_mapping.setToolTip(self.tr("Load mapping rules from a text file"))
        self.btn_import_mapping.clicked.connect(self._import_mapping_from_file)
        toolbar.addWidget(self.btn_import_mapping)
        
        self.btn_export_mapping = QPushButton(self.tr("Export to file…"))
        self.btn_export_mapping.setToolTip(self.tr("Save mapping rules to a text file"))
        self.btn_export_mapping.clicked.connect(self._export_mapping_to_file)
        toolbar.addWidget(self.btn_export_mapping)
        toolbar.addStretch()
        self.label_mapping_status = QLabel("0 rules loaded")
        toolbar.addWidget(self.label_mapping_status)
        layout.addLayout(toolbar)
        
        # Main editor area (full width after removing Available Clusters sidebar)
        editor_widget = QWidget()
        editor_vlayout = QVBoxLayout()
        editor_vlayout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        editor_vlayout.addWidget(QLabel("Mapping rules (one per line, format: <pattern> = <ClusterName>):"))
        self.input_tag_mapping = QPlainTextEdit()
        self.input_tag_mapping.setPlaceholderText("Example:\npsytrance = Prog-Psy\nchillout = Downtempo\n*rock* = Rock")
        monospace_font = QFont("Courier", 10)
        self.input_tag_mapping.setFont(monospace_font)
        self.input_tag_mapping.setToolTip("Each line maps a tag pattern to one of 12 genre clusters. Patterns support wildcards (*, ?).")
        editor_vlayout.addWidget(self.input_tag_mapping)
        
        editor_widget.setLayout(editor_vlayout)
        layout.addWidget(editor_widget)
        
        # Mapping options
        options_group = QGroupBox("Mapping Options")
        options_layout = QVBoxLayout()
        
        self.check_mapping_use_regex = QCheckBox("Use regex patterns (instead of wildcards)")
        self.check_mapping_use_regex.setToolTip("When enabled, mapping patterns are treated as full regular expressions. When disabled, wildcards (* and ?) are supported.")
        options_layout.addWidget(self.check_mapping_use_regex)
        
        self.check_mapping_first_match = QCheckBox("First match only")
        self.check_mapping_first_match.setToolTip("When enabled, only the first matching pattern is applied.")
        options_layout.addWidget(self.check_mapping_first_match)
        
        options_layout.addWidget(QLabel("Mapping Mode:"))
        self.combo_mapping_mode = QComboBox()
        self.combo_mapping_mode.addItems(["first_match", "single_winner", "multi_winner", "override"])
        self.combo_mapping_mode.setToolTip("first_match: Return first match found\nsingle_winner: Return first match (same as first_match)\nmulti_winner: Return all matches\noverride: Override existing genres")
        options_layout.addWidget(self.combo_mapping_mode)
        
        self.check_filter_tags = QCheckBox("Filter tags using mapping keys (whitelist)")
        self.check_filter_tags.setToolTip("When enabled, only tags that match a mapping pattern (left side) are included in the comment field. Mapping still runs on all tags.")
        options_layout.addWidget(self.check_filter_tags)
        
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        # Cluster priority (collapsible/optional)
        priority_group = QGroupBox("Cluster Priority (for final selection)")
        priority_layout = QVBoxLayout()
        priority_layout.addWidget(QLabel("When multiple clusters match, this order determines the winner. Leave empty to use default priority."))
        self.input_cluster_priority = QPlainTextEdit()
        self.input_cluster_priority.setPlaceholderText("Psychill\nPsybient\nPsyDub\nProg-Psy\n...")
        self.input_cluster_priority.setMaximumHeight(150)
        priority_layout.addWidget(self.input_cluster_priority)
        priority_group.setLayout(priority_layout)
        layout.addWidget(priority_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        
        # Connect mapping text change to update status
        self.input_tag_mapping.textChanged.connect(self._update_mapping_status)
        
        return widget
    
    def _get_valid_clusters(self):
        """Extract cluster names from mapping configuration.
        
        Clusters are extracted from:
        - Mapping rules (right side of '=' in rules)
        - Cluster priority list (bare lines without '=')
        """
        from .mapping_engine import TagMappingEngine
        cluster_names = set()
        
        mapping_text = self.input_tag_mapping.toPlainText()
        if mapping_text:
            temp_engine = TagMappingEngine()
            config_data = temp_engine._parse_mapping_file(mapping_text)
            # Add clusters from rules
            for cluster in config_data.rules.values():
                if cluster:
                    cluster_names.add(cluster.strip())
            # Add clusters from priority list
            for cluster in config_data.cluster_priority:
                if cluster:
                    cluster_names.add(cluster.strip())
        
        return cluster_names
    
    def _validate_mapping(self):
        """Validate mapping table syntax.
        
        Supports two syntaxes:
        1. Mapping rules: <pattern> = <ClusterName>
        2. Cluster priority list: <ClusterName>
        """
        mapping_text = self.input_tag_mapping.toPlainText()
        errors = []
        warnings = []
        
        # Track patterns to detect duplicates
        pattern_lines = {}  # pattern -> list of line numbers
        valid_rules = 0
        priority_entries = 0
        
        for line_num, raw_line in enumerate(mapping_text.splitlines(), 1):
            stripped = raw_line.strip()
            
            # 1) Skip empty lines
            if not stripped:
                continue
            
            # 2) Skip comment/header lines
            # Lines starting with "#" → comment
            if stripped.startswith('#'):
                continue
            # Lines starting with "=" → section documentation
            # Only skip if the line starts with '=' at the very beginning (no leading whitespace)
            # This distinguishes "= SECTION" (documentation) from " = cluster" (malformed rule)
            if raw_line.startswith('='):
                # Skip if it's clearly documentation:
                # - Lines with only '=' characters (like "=====")
                # - Lines starting with "= " followed by text (like "= SECTION TITLE")
                if all(c == '=' for c in stripped) or (len(stripped) > 1 and stripped[1] == ' '):
                    continue
            # Lines starting with "//" → comment (legacy support)
            if stripped.startswith('//'):
                continue
            
            # 3) Skip separator lines (lines with only separator characters)
            if all(c in '-_=* ' for c in stripped):
                continue
            
            # 4) Check if line contains '='
            if '=' in stripped:
                # This is a mapping rule line
                parts = stripped.split('=', 1)
                if len(parts) != 2:
                    errors.append(f"Line {line_num}: invalid format (expected exactly one '=')")
                    continue
                
                pattern = parts[0].strip()
                cluster = parts[1].strip()
                
                # Validate pattern is non-empty
                if not pattern:
                    errors.append(f"Line {line_num}: empty pattern")
                
                # Validate cluster is non-empty
                if not cluster:
                    errors.append(f"Line {line_num}: empty cluster name")
                
                # Track patterns for duplicate detection
                if pattern:
                    pattern_norm = pattern.lower()  # Case-insensitive duplicate detection
                    if pattern_norm not in pattern_lines:
                        pattern_lines[pattern_norm] = []
                    pattern_lines[pattern_norm].append(line_num)
                
                # Count valid rules (any non-empty cluster is valid)
                if pattern and cluster:
                    valid_rules += 1
            else:
                # This is a cluster priority entry - accept any non-empty name
                if stripped:
                    priority_entries += 1
                else:
                    # Empty line - already skipped above
                    pass
        
        # Check for duplicate patterns
        for pattern_norm, line_nums in pattern_lines.items():
            if len(line_nums) > 1:
                warnings.append(f"Duplicate pattern found on lines {', '.join(map(str, line_nums))}")
        
        # Count unique clusters used (from both rules and priority)
        clusters_used = set()
        for raw_line in mapping_text.splitlines():
            line = raw_line.strip()
            # Skip empty lines
            if not line:
                continue
            # Skip comment/header lines
            if line.startswith('#') or line.startswith('//'):
                continue
            # Skip lines starting with "=" at the very beginning (section documentation)
            if raw_line.startswith('='):
                # Skip if it's clearly documentation
                if all(c == '=' for c in line) or (len(line) > 1 and line[1] == ' '):
                    continue
            # Skip separator lines (lines with only separator characters)
            if all(c in '-_=* ' for c in line):
                continue
            if '=' in line:
                parts = line.split('=', 1)
                if len(parts) == 2:
                    cluster = parts[1].strip()
                    if cluster:
                        clusters_used.add(cluster)
            else:
                # Priority entry - any non-empty line is a valid cluster name
                if line:
                    clusters_used.add(line)
        
        # Show results in custom scrollable dialog
        if errors or warnings:
            dialog = ValidationResultsDialog(
                self,
                self.tr("Validation Failed"),
                errors=errors,
                warnings=warnings
            )
            dialog.exec()
            utils.log_warning(f"Mapping validation found {len(errors)} errors, {len(warnings)} warnings")
        else:
            report = self.tr("Validation OK: {rules} rules, {priority} priority entries, {clusters} clusters used").format(
                rules=valid_rules,
                priority=priority_entries,
                clusters=len(clusters_used)
            )
            dialog = ValidationResultsDialog(
                self,
                self.tr("Validation Successful"),
                success_message=report
            )
            dialog.exec()
            utils.log_info(f"Mapping table validation passed: {valid_rules} rules, {priority_entries} priority entries, {len(clusters_used)} clusters")
    
    def _import_mapping_from_file(self):
        """Import mapping rules from a text file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Import Mapping Rules"),
            "",
            self.tr("Text files (*.txt);;All files (*)")
        )
        
        if not file_path:
            # User cancelled
            return
        
        try:
            # Read file as UTF-8
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            # Put content into editor
            self.input_tag_mapping.setPlainText(file_content)
            
            # Store in config
            self.api.global_config.setting["uma_tag_mapping"] = file_content
            
            # Update status
            self._update_mapping_status()
            
            utils.log_info(f"UMA: Imported mapping rules from {file_path}")
            
            # Optionally validate and show errors (but keep the imported content)
            # Quick syntax validation (clusters are not restricted to a fixed list)
            validation_errors = []
            for line_num, line in enumerate(file_content.splitlines(), 1):
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('//') or line.startswith('='):
                    continue
                if '=' not in line:
                    continue
                parts = line.split('=', 1)
                if len(parts) == 2:
                    pattern = parts[0].strip()
                    cluster = parts[1].strip()
                    if not pattern:
                        validation_errors.append(f"Line {line_num}: empty pattern")
                    if not cluster:
                        validation_errors.append(f"Line {line_num}: empty cluster")
            
            if validation_errors:
                error_msg = self.tr("Imported successfully, but validation found errors:\n\n") + "\n".join(validation_errors[:10])  # Limit to first 10 errors
                if len(validation_errors) > 10:
                    error_msg += f"\n\n... and {len(validation_errors) - 10} more errors"
                QMessageBox.warning(self, self.tr("Import with Warnings"), error_msg)
            else:
                QMessageBox.information(self, self.tr("Import Successful"), 
                                       self.tr("Mapping rules imported successfully from:\n{path}").format(path=file_path))
        
        except IOError as e:
            error_msg = self.tr("Failed to read file:\n{error}").format(error=str(e))
            QMessageBox.warning(self, self.tr("Import Failed"), error_msg)
            utils.log_error(f"UMA: Failed to import mapping rules from {file_path}: {e}")
        except Exception as e:
            error_msg = self.tr("Unexpected error during import:\n{error}").format(error=str(e))
            QMessageBox.warning(self, self.tr("Import Failed"), error_msg)
            utils.log_error(f"UMA: Unexpected error importing mapping rules: {e}", exc_info=True)
    
    def _export_mapping_to_file(self):
        """Export mapping rules to a text file."""
        mapping_text = self.input_tag_mapping.toPlainText()
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export Mapping Rules"),
            "crate_genre_mapping.txt",
            self.tr("Text files (*.txt);;All files (*)")
        )
        
        if not file_path:
            # User cancelled
            return
        
        try:
            # Write file as UTF-8
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(mapping_text)
            
            success_msg = self.tr("Mapping rules saved to:\n{path}").format(path=file_path)
            QMessageBox.information(self, self.tr("Export Successful"), success_msg)
            utils.log_info(f"UMA: Exported mapping rules to {file_path}")
        
        except IOError as e:
            error_msg = self.tr("Failed to write file:\n{error}").format(error=str(e))
            QMessageBox.warning(self, self.tr("Export Failed"), error_msg)
            utils.log_error(f"UMA: Failed to export mapping rules to {file_path}: {e}")
        except Exception as e:
            error_msg = self.tr("Unexpected error during export:\n{error}").format(error=str(e))
            QMessageBox.warning(self, self.tr("Export Failed"), error_msg)
            utils.log_error(f"UMA: Unexpected error exporting mapping rules: {e}", exc_info=True)
    
    def _update_mapping_status(self):
        """Update mapping status label."""
        mapping_text = self.input_tag_mapping.toPlainText()
        rule_count = len([l for l in mapping_text.splitlines() if '=' in l.strip() and not l.strip().startswith('#')])
        self.label_mapping_status.setText(f"{rule_count} rules loaded")
    
    def _create_filters_section(self):
        """Create Generic Filters section widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        # Info banner
        info_banner = QLabel("⚠ These filters are applied at the END of the pipeline. Tags matching these patterns will be removed from final output, even if they participated in cluster mapping.")
        info_banner.setWordWrap(True)
        # Use neutral colors with high contrast for both light and dark themes (WCAG AA compliant: ≥ 4.5:1)
        # Background: medium gray (#d0d0d0), Text: dark (#1a1a1a), Border: subtle gray
        info_banner.setStyleSheet("QLabel { background-color: #d0d0d0; color: #1a1a1a; padding: 8px; border: 1px solid #999999; border-radius: 4px; }")
        layout.addWidget(info_banner)
        
        # Single text editor for all generic filters
        layout.addWidget(QLabel("Patterns (one per line, supports wildcards *):"))
        
        self.input_generic_genres = QPlainTextEdit()
        # Use monospace font for better readability
        font = QFont("Courier", 9)
        self.input_generic_genres.setFont(font)
        self.input_generic_genres.setPlaceholderText(
            "Example patterns:\n"
            "*single*\n*album*\n*remastered*\n*original mix*\n*vinyl*\n*flac*\n"
            "*london*\n*berlin*\n*germany*\n"
            "Electronica\nElectronic\nelectronica\nelectronic"
        )
        self.input_generic_genres.setToolTip("Patterns support wildcards (* matches any text). Matching is case-insensitive. These filters run AFTER cluster selection and hashtag generation.")
        # Expand editor to use available height
        self.input_generic_genres.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.input_generic_genres)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_preview_section(self):
        """Create Preview & Debug section widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        # MBID input field
        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Album MBID:"))
        self.input_preview_mbid = QLineEdit()
        self.input_preview_mbid.setPlaceholderText("Paste MusicBrainz release MBID here…")
        self.input_preview_mbid.setToolTip("Enter a 36-character UUID MBID (with hyphens) to preview UMA processing for that release")
        selector_layout.addWidget(self.input_preview_mbid)
        self.btn_refresh_preview = QPushButton("Refresh preview")
        self.btn_refresh_preview.setToolTip("Process the entered MBID through UMA pipeline and display results")
        selector_layout.addWidget(self.btn_refresh_preview)
        selector_layout.addStretch()
        layout.addLayout(selector_layout)
        
        # Error message label (hidden by default)
        # Uses neutral colors for dark theme compatibility
        self.preview_error_label = QLabel()
        self.preview_error_label.setStyleSheet("QLabel { color: #dc3545; background-color: #f8d7da; padding: 8px; border: 1px solid #f5c6cb; border-radius: 4px; }")
        self.preview_error_label.setWordWrap(True)
        self.preview_error_label.hide()
        layout.addWidget(self.preview_error_label)
        
        # Two-column layout
        preview_layout = QHBoxLayout()
        
        # Left: Processing steps
        steps_widget = QWidget()
        steps_layout = QVBoxLayout()
        steps_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        steps_layout.addWidget(QLabel("<b>Processing Steps</b>"))
        
        # Step 1: Raw Sources
        step1 = QGroupBox("Step 1: Fetch from Sources")
        step1_layout = QVBoxLayout()
        step1_status = QLabel("No data loaded")
        step1_status.setStyleSheet("QLabel { color: #6c757d; font-style: italic; }")
        step1_layout.addWidget(step1_status)
        self.preview_raw_sources = QPlainTextEdit()
        self.preview_raw_sources.setReadOnly(True)
        self.preview_raw_sources.setMaximumHeight(100)
        self.preview_raw_sources.setPlaceholderText("Enter an MBID and click 'Refresh preview' to see raw source data")
        step1_layout.addWidget(self.preview_raw_sources)
        step1.setLayout(step1_layout)
        steps_layout.addWidget(step1)
        
        # Step 2: After Mapping
        step2 = QGroupBox("Step 2: After Mapping")
        step2_layout = QVBoxLayout()
        self.preview_mapped = QPlainTextEdit()
        self.preview_mapped.setReadOnly(True)
        self.preview_mapped.setMaximumHeight(80)
        self.preview_mapped.setPlaceholderText("Mapped clusters will appear here after processing")
        step2_layout.addWidget(self.preview_mapped)
        step2.setLayout(step2_layout)
        steps_layout.addWidget(step2)
        
        # Step 3: Cluster Selection
        step3 = QGroupBox("Step 3: Cluster Selection")
        step3_layout = QVBoxLayout()
        self.preview_selected = QPlainTextEdit()
        self.preview_selected.setReadOnly(True)
        self.preview_selected.setMaximumHeight(60)
        self.preview_selected.setPlaceholderText("Selected cluster and reason will appear here")
        step3_layout.addWidget(self.preview_selected)
        step3.setLayout(step3_layout)
        steps_layout.addWidget(step3)
        
        # Step 4: Generic Filter
        step4 = QGroupBox("Step 4: Generic Filter Applied")
        step4_layout = QVBoxLayout()
        self.preview_filtered = QPlainTextEdit()
        self.preview_filtered.setReadOnly(True)
        self.preview_filtered.setMaximumHeight(100)
        self.preview_filtered.setPlaceholderText("Before/after filter comparison will appear here")
        step4_layout.addWidget(self.preview_filtered)
        step4.setLayout(step4_layout)
        steps_layout.addWidget(step4)
        
        # Step 5: Final Output
        step5 = QGroupBox("Step 5: Final Output")
        step5_layout = QVBoxLayout()
        self.preview_output = QPlainTextEdit()
        self.preview_output.setReadOnly(True)
        self.preview_output.setMaximumHeight(80)
        self.preview_output.setPlaceholderText("Final genre, style, and comment will appear here")
        step5_layout.addWidget(self.preview_output)
        step5.setLayout(step5_layout)
        steps_layout.addWidget(step5)
        
        # Store step1_status for dynamic updates
        self.preview_step1_status = step1_status
        
        steps_widget.setLayout(steps_layout)
        preview_layout.addWidget(steps_widget, stretch=1)
        
        layout.addLayout(preview_layout)
        layout.addStretch()
        widget.setLayout(layout)
        
        # Connect refresh button to preview pipeline
        self.btn_refresh_preview.clicked.connect(self._refresh_preview)
        
        # Restore preview state if it exists (when returning to this tab)
        # Use a timer to ensure widget is fully initialized
        QTimer.singleShot(100, self._restore_preview_if_needed)
        
        return widget
    
    def _restore_preview_if_needed(self):
        """Restore preview displays from persistent state if available."""
        if self._preview_state["mbid"] and self._preview_state["mb_release"]:
            # Restore MBID in field
            if self.input_preview_mbid.text() != self._preview_state["mbid"]:
                self.input_preview_mbid.setText(self._preview_state["mbid"])
            
            # Restore displays if we have processed data
            if self._preview_state["sources"] or self._preview_state["final_genre"]:
                self._restore_preview_displays()
    
    def _restore_preview_displays(self):
        """Restore preview displays from persistent state."""
        if not self._preview_state["mbid"]:
            return
        
        # Safety check: ensure all preview widgets exist
        required_widgets = ['preview_raw_sources', 'preview_mapped', 'preview_selected', 
                           'preview_filtered', 'preview_output', 'preview_step1_status']
        if not all(hasattr(self, widget) for widget in required_widgets):
            utils.log_debug("UMA: Preview: Not all widgets initialized, skipping display restore")
            return
        
        try:
            # Restore Step 1: Raw Sources (don't require mb_release for restore from config)
            if self._preview_state["sources"]:
                self._update_preview_step1_from_state()
            
            # Restore Steps 2-5 if we have processed data
            if self._preview_state["clusters"]:
                self._update_preview_step2_from_state()
            if self._preview_state["final_genre"]:
                self._update_preview_step3_from_state()
            if self._preview_state["final_genre"] or self._preview_state["final_style"]:
                self._update_preview_step4_from_state()
            if self._preview_state["final_genre"] or self._preview_state["final_style"] or self._preview_state["final_comment"]:
                self._update_preview_step5_from_state()
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to restore displays: {e}", exc_info=True)
    
    def _update_preview_step1_from_state(self):
        """Update Step 1 from persistent state."""
        lines = []
        if self._preview_state["sources"]:
            lines.append(f"{len(self._preview_state['sources'])} source(s) enabled")
            lines.append("")
            for source_name, data in sorted(self._preview_state["sources"].items()):
                lines.append(f"{source_name.capitalize()}:")
                if data.get("genres"):
                    lines.append(f"  Genres: {', '.join(data['genres'])}")
                if data.get("styles"):
                    lines.append(f"  Styles: {', '.join(data['styles'])}")
                if data.get("tags"):
                    tags = data["tags"][:20]
                    lines.append(f"  Tags: {', '.join(tags)}")
                    if len(data["tags"]) > 20:
                        lines.append(f"  ... and {len(data['tags']) - 20} more tags")
                if not data.get("genres") and not data.get("styles") and not data.get("tags"):
                    lines.append("  (no data)")
                lines.append("")
        if not lines:
            lines.append("No source data available")
        self.preview_raw_sources.setPlainText("\n".join(lines))
        if self._preview_state["sources"]:
            self.preview_step1_status.setText(f"✓ {len(self._preview_state['sources'])} source(s) completed")
            self.preview_step1_status.setStyleSheet("QLabel { color: #28a745; }")
    
    def _update_preview_step2_from_state(self):
        """Update Step 2 from persistent state."""
        lines = []
        if self._preview_state["clusters"]:
            for cluster in self._preview_state["clusters"]:
                lines.append(f"- {cluster}")
        if not lines:
            lines.append("No mapping results")
        self.preview_mapped.setPlainText("\n".join(lines))
    
    def _update_preview_step3_from_state(self):
        """Update Step 3 from persistent state."""
        lines = []
        if self._preview_state["final_genre"]:
            lines.append(f"Selected Genre: {self._preview_state['final_genre']}")
        if not lines:
            lines.append("No cluster selected")
        self.preview_selected.setPlainText("\n".join(lines))
    
    def _update_preview_step4_from_state(self):
        """Update Step 4 from persistent state."""
        lines = []
        if self._preview_state["final_genre"] or self._preview_state["final_style"]:
            lines.append("After Generic Filter:")
            if self._preview_state["final_genre"]:
                lines.append(f"  Genre: {self._preview_state['final_genre']}")
            if self._preview_state["final_style"]:
                lines.append(f"  Styles: {', '.join(self._preview_state['final_style'])}")
        if not lines:
            lines.append("No filter applied")
        self.preview_filtered.setPlainText("\n".join(lines))
    
    def _update_preview_step5_from_state(self):
        """Update Step 5 from persistent state."""
        lines = []
        if self._preview_state["final_genre"]:
            lines.append(f"Genre: {self._preview_state['final_genre']}")
        if self._preview_state["final_style"]:
            lines.append(f"Style: {', '.join(self._preview_state['final_style'])}")
        if self._preview_state["final_comment"]:
            lines.append(f"Comment: {self._preview_state['final_comment']}")
        if not lines:
            lines.append("No output")
        self.preview_output.setPlainText("\n".join(lines))
    
    def _refresh_preview(self):
        """
        Refresh preview with current settings using the MBID from the input field.
        The preview now works on an arbitrary release MBID provided by the user.
        """
        # Hide any previous error message
        self.preview_error_label.hide()
        
        # Get MBID from input field
        mbid = self.input_preview_mbid.text().strip()
        
        # Validate MBID format (UUID v4 format: 8-4-4-4-12 hex digits with hyphens)
        if not mbid:
            self.preview_error_label.setText("Error: MBID field is empty. Please enter a MusicBrainz release MBID.")
            self.preview_error_label.show()
            utils.log_warning("UMA: Preview: Empty MBID provided")
            # Show message in Step 1
            self._clear_preview_displays()
            self.preview_step1_status.setText("Enter a release MBID and click 'Refresh preview'.")
            self.preview_step1_status.setStyleSheet("QLabel { color: #6c757d; font-style: italic; }")
            self.preview_raw_sources.setPlainText("Enter a release MBID and click 'Refresh preview'.")
            return
        
        # Validate UUID format (basic check: 36 chars, hyphens at positions 8, 13, 18, 23)
        import re
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
        if not uuid_pattern.match(mbid):
            error_msg = f"Invalid MBID format. Expected UUID format (e.g., 12345678-1234-1234-1234-123456789abc)"
            self.preview_error_label.setText(f"Error: {error_msg}")
            self.preview_error_label.show()
            utils.log_warning(f"UMA: Preview: Invalid MBID format: {mbid}")
            # Show error in Step 1, but don't clear other steps if we have existing preview
            if self._preview_state["mbid"] != mbid:
                # Only clear if this is a new MBID attempt
                self._clear_preview_displays()
            self.preview_step1_status.setText(f"Error: {error_msg}")
            self.preview_step1_status.setStyleSheet("QLabel { color: #dc3545; font-style: italic; }")
            self.preview_raw_sources.setPlainText(f"Invalid MBID format.\n\nExpected: 36-character UUID with hyphens\nGot: {mbid[:50]}")
            # Keep MBID in field - don't clear it
            return
        
        # MBID is valid, proceed with preview processing
        # Only clear if this is a different MBID than what we currently have
        if self._preview_state["mbid"] != mbid:
            utils.log_debug(f"UMA: Preview: Processing new MBID {mbid} (previous: {self._preview_state['mbid']})")
            # Clear preview displays for new MBID
            self._clear_preview_displays()
            # Update state MBID and reset state
            self._preview_state["mbid"] = mbid
            self._preview_state["mb_release"] = None
            self._preview_state["sources"] = {}
            self._preview_state["clusters"] = []
            self._preview_state["final_genre"] = ""
            self._preview_state["final_style"] = []
            self._preview_state["final_comment"] = ""
            self._preview_state["timestamp"] = None
        else:
            utils.log_debug(f"UMA: Preview: Refreshing existing MBID {mbid}")
            # Same MBID - just clear transient state, keep displays
            self._clear_transient_state()
        
        # Show loading status
        self.preview_step1_status.setText("Loading MusicBrainz release data...")
        self.preview_step1_status.setStyleSheet("QLabel { color: #856404; font-style: italic; }")
        
        # Fetch MusicBrainz release data and run pipeline
        self._run_preview_pipeline(mbid)
    
    def _clear_preview_displays(self):
        """Clear all preview display widgets (used when starting new preview)."""
        self.preview_raw_sources.setPlainText("")
        self.preview_mapped.setPlainText("")
        self.preview_selected.setPlainText("")
        self.preview_filtered.setPlainText("")
        self.preview_output.setPlainText("")
        self.preview_step1_status.setText("No data loaded")
        self.preview_step1_status.setStyleSheet("QLabel { color: #6c757d; font-style: italic; }")
    
    def _clear_transient_state(self):
        """Clear only transient state (pending sources, timers) but keep preview displays."""
        # Stop any pending timers
        if hasattr(self, '_preview_check_timer'):
            self._preview_check_timer.stop()
        if hasattr(self, '_preview_timeout_timer'):
            self._preview_timeout_timer.stop()
        # Clear source tracking (will be repopulated)
        if hasattr(self, '_preview_source_results'):
            self._preview_source_results = {}
        if hasattr(self, '_preview_source_errors'):
            self._preview_source_errors = {}
    
    def _run_preview_pipeline(self, mbid: str):
        """
        Run the full UMA pipeline for preview: fetch MB data → collect from sources → map → merge → filter → display.
        This method handles async operations and updates the preview displays with results.
        """
        utils.log_debug(f"UMA: Preview: Starting preview pipeline for MBID {mbid}")
        # Step 1: Fetch MusicBrainz release data
        self._fetch_mb_release(mbid)
    
    def _fetch_mb_release(self, mbid: str):
        """
        Fetch MusicBrainz release data by MBID using QNetworkAccessManager.
        Uses proper Qt signal/slot mechanism to ensure callbacks work.
        """
        mb_url = f"https://musicbrainz.org/ws/2/release/{mbid}?inc=url-rels+release-groups+artist-credits&fmt=json"
        
        utils.log_debug(f"UMA: Preview: Fetching MB release from {mb_url}")
        
        # Create network manager (reuse if exists, create if not)
        # Keep it as instance variable to prevent garbage collection
        if not hasattr(self, '_preview_network_manager'):
            self._preview_network_manager = QNetworkAccessManager()
            # Keep network manager alive by storing reference
            self._preview_network_manager.setParent(self)
        
        # Create network request with proper headers
        # MusicBrainz API requires specific User-Agent format and may rate-limit
        request = QNetworkRequest(QUrl(mb_url))
        # Use MusicBrainz-compatible User-Agent (Picard format)
        # MusicBrainz prefers: ApplicationName/Version (contact-url-or-email)
        request.setRawHeader(b'User-Agent', b'Picard/2.9.0 (https://picard.musicbrainz.org)')
        request.setRawHeader(b'Accept', b'application/json')
        # Don't set Connection header - let Qt handle it
        # Set redirect policy
        # Set timeout attributes (Qt6 follows redirects automatically; no FollowRedirectsAttribute)
        request.setAttribute(QNetworkRequest.Attribute.HttpPipeliningAllowedAttribute, False)
        
        # Store MBID for callback
        self._preview_current_mbid = mbid
        
        # Initialize reply storage if needed
        if not hasattr(self, '_preview_replies'):
            self._preview_replies = {}
        if not hasattr(self, '_preview_timers'):
            self._preview_timers = {}
        
        # Make request and connect finished signal properly
        try:
            reply = self._preview_network_manager.get(request)
            if not reply:
                utils.log_warning("UMA: Preview: Failed to create network request")
                self._show_preview_error("Failed to create network request")
                return
            
            # Store reply reference to prevent garbage collection
            # Keep reply as child of network manager to ensure proper lifecycle
            reply.setParent(self._preview_network_manager)
            self._preview_replies[mbid] = reply
            
            # Connect finished signal using lambda with captured variables
            # This ensures the callback has access to both reply and mbid
            def handle_finished():
                try:
                    if hasattr(self, '_preview_replies') and mbid in self._preview_replies:
                        stored_reply = self._preview_replies[mbid]
                        if stored_reply and stored_reply.isFinished():
                            self._on_mb_reply_finished(stored_reply, mbid)
                except Exception as e:
                    utils.log_warning(f"UMA: Preview: Error in finished handler: {e}", exc_info=True)
            
            reply.finished.connect(handle_finished)
            
            # Also set up a timer-based fallback to check reply status (in case signal doesn't fire)
            check_timer = QTimer()
            check_timer.setSingleShot(False)
            check_timer.timeout.connect(lambda: self._check_reply_status(reply, mbid, check_timer))
            check_timer.start(500)  # Check every 500ms
            self._preview_timers[mbid] = check_timer
            
            # Set timeout (30 seconds max)
            QTimer.singleShot(30000, lambda: self._on_mb_request_timeout(mbid))
            
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Exception creating network request: {e}", exc_info=True)
            self._show_preview_error(f"Failed to create network request: {str(e)}")
    
    def _on_mb_request_timeout(self, mbid: str):
        """Handle timeout for MB request."""
        if hasattr(self, '_preview_replies') and mbid in self._preview_replies:
            reply = self._preview_replies[mbid]
            if reply and not reply.isFinished():
                utils.log_warning(f"UMA: Preview: Request timeout for MBID {mbid}")
                reply.abort()
                self._show_preview_error("Request timeout (30 seconds)")
                # Clean up
                if mbid in self._preview_replies:
                    del self._preview_replies[mbid]
                if hasattr(self, '_preview_timers') and mbid in self._preview_timers:
                    self._preview_timers[mbid].stop()
                    del self._preview_timers[mbid]
    
    def _check_reply_status(self, reply: QNetworkReply, mbid: str, timer: QTimer):
        """Fallback: Check if reply is finished and process if so."""
        try:
            if reply and reply.isFinished():
                timer.stop()
                if hasattr(self, '_preview_replies') and mbid in self._preview_replies:
                    # Only process if not already processed
                    if mbid in self._preview_replies:
                        self._on_mb_reply_finished(reply, mbid)
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Error in reply status check: {e}", exc_info=True)
            timer.stop()
    
    def _on_mb_reply_finished(self, reply: QNetworkReply, mbid: str):
        """Handle MusicBrainz API response."""
        # Safety check: ensure options page is still valid
        try:
            if not hasattr(self, 'preview_raw_sources'):
                utils.log_warning("UMA: Preview: Options page no longer valid, ignoring response")
                return
        except:
            utils.log_warning("UMA: Preview: Options page destroyed, ignoring response")
            return
        
        # Prevent duplicate processing
        if not hasattr(self, '_preview_replies') or mbid not in self._preview_replies:
            return
        
        # Mark as processed to prevent duplicate calls
        processed_key = f'_preview_processed_{mbid}'
        if hasattr(self, processed_key):
            return
        setattr(self, processed_key, True)
        
        try:
            # Check if reply is still valid
            if not reply or reply.isFinished() is False:
                utils.log_warning(f"UMA: Preview: Reply not finished or invalid for {mbid}")
                return
            
            error = reply.error()
            if error != QNetworkReply.NetworkError.NoError:
                error_msg = reply.errorString()
                http_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                utils.log_warning(f"UMA: Preview: Error fetching MB release {mbid}: {error_msg} (HTTP: {http_code}, QT error code: {error})")
                
                # Handle specific error codes (QNetworkReply error codes are integers)
                # Common codes: 1=ConnectionRefused, 2=RemoteHostClosed, 3=HostNotFound, 4=Timeout, 5=OperationCanceled
                # Error code 2 (RemoteHostClosedError) often means the server closed the connection
                # This can happen with MusicBrainz if rate limiting is triggered or the request is malformed
                if error == 2:  # RemoteHostClosedError - "Connection closed"
                    # MusicBrainz may close connections due to rate limiting or invalid requests
                    # Provide helpful error message and suggest retry
                    error_msg = f"Connection closed by MusicBrainz server. This may be due to rate limiting. Please wait a few seconds and try again. If the problem persists, check your network connection."
                elif error == 1:  # ConnectionRefusedError
                    error_msg = "Connection refused by server"
                elif error == 3:  # HostNotFoundError
                    error_msg = "Host not found"
                elif error == 4:  # TimeoutError
                    error_msg = "Request timeout"
                elif error == 5:  # OperationCanceledError
                    error_msg = "Request was cancelled"
                else:
                    # Use the original error string with error code
                    error_msg = f"{error_msg} (Error code: {error})"
                
                self._show_preview_error(f"Failed to fetch MusicBrainz data: {error_msg}")
                return
            
            # Read response data
            data = reply.readAll()
            if not data or data.isEmpty():
                utils.log_warning(f"UMA: Preview: Empty response for MB release {mbid}")
                self._show_preview_error("Empty response from MusicBrainz")
                return
            
            # Parse JSON
            try:
                data_bytes = bytes(data)
                if not data_bytes:
                    utils.log_warning(f"UMA: Preview: Empty data bytes for MB release {mbid}")
                    self._show_preview_error("Empty response data")
                    return
                
                release_data = json.loads(data_bytes.decode('utf-8'))
                utils.log_debug(f"UMA: Preview: MB release loaded successfully for {mbid}")
                
                # Process on main thread (we're already on main thread from Qt signal)
                self._on_mb_release_loaded(mbid, release_data)
            except json.JSONDecodeError as e:
                utils.log_warning(f"UMA: Preview: Failed to parse MB release JSON: {e}")
                self._show_preview_error(f"Invalid JSON response: {str(e)}")
            except UnicodeDecodeError as e:
                utils.log_warning(f"UMA: Preview: Failed to decode response: {e}")
                self._show_preview_error(f"Failed to decode response: {str(e)}")
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Unexpected error processing MB release: {e}", exc_info=True)
            self._show_preview_error(f"Unexpected error: {str(e)}")
        finally:
            # Clean up reply and timer after processing
            try:
                if hasattr(self, '_preview_replies') and mbid in self._preview_replies:
                    stored_reply = self._preview_replies[mbid]
                    del self._preview_replies[mbid]
                    if stored_reply:
                        stored_reply.deleteLater()
                
                if hasattr(self, '_preview_timers') and mbid in self._preview_timers:
                    self._preview_timers[mbid].stop()
                    del self._preview_timers[mbid]
                
                # Clear processed flag
                if hasattr(self, processed_key):
                    delattr(self, processed_key)
            except Exception as e:
                utils.log_warning(f"UMA: Preview: Error during cleanup: {e}", exc_info=True)
    
    def _show_preview_error(self, error_msg: str):
        """Show error message in preview UI (called on main thread)."""
        self.preview_step1_status.setText(f"Error: {error_msg}")
        self.preview_step1_status.setStyleSheet("QLabel { color: #dc3545; font-style: italic; }")
        self.preview_raw_sources.setPlainText(f"Error fetching MusicBrainz release:\n{error_msg}")
        # Don't clear other steps - they may contain previous preview data
        # Only clear if this is a new MBID (handled in _refresh_preview)
    
    def _on_mb_release_loaded(self, mbid: str, release_data: dict):
        """
        Called when MusicBrainz release data is loaded.
        Creates a mock album object and runs the UMA pipeline.
        """
        # Store MB release in persistent state
        self._preview_state["mb_release"] = release_data
        self._preview_state["timestamp"] = datetime.datetime.now() if hasattr(datetime, 'datetime') else None
        
        utils.log_debug(f"UMA: Preview: MB release loaded for {mbid}")
        utils.log_debug(f"UMA: Preview: Running mapping & cluster selection")
        
        # Extract basic info
        release = release_data
        title = release.get('title', 'Unknown Album')
        artist_credit = release.get('artist-credit', [])
        artist_name = ', '.join([ac.get('name', '') for ac in artist_credit if 'name' in ac]) if artist_credit else 'Unknown Artist'
        
        # Create mock album object
        mock_album = self._create_mock_album(mbid, title, artist_name, release)
        
        # Initialize collector
        col = collector.AlbumCollector(album_id=f"preview_{mbid}", album_title=title)
        col._album_object = mock_album
        
        # Determine enabled sources
        enabled_sources = set()
        if self._get_config("uma_enable_bandcamp", True):
            # Check if Bandcamp URL exists or fallback is enabled
            release_group = release.get("release-group") if isinstance(release, dict) else None
            bc_url, bc_source = sources.resolve_bandcamp_url(mock_album, release, release_group)
            fallback_enabled = self._get_config("uma_bandcamp_fallback_search", False)
            if bc_url or fallback_enabled:
                enabled_sources.add("bandcamp")

        if self._get_config("uma_enable_discogs", True):
            enabled_sources.add("discogs")
        
        # Initialize sources
        col.initialize_sources(enabled_sources)
        
        if not enabled_sources:
            self.preview_step1_status.setText("No sources enabled")
            self.preview_step1_status.setStyleSheet("QLabel { color: #6c757d; font-style: italic; }")
            self.preview_raw_sources.setPlainText("No sources are enabled in UMA settings.")
            return
        
        # Update Step 1 status
        self.preview_step1_status.setText(f"✓ {len(enabled_sources)} source(s) enabled")
        self.preview_step1_status.setStyleSheet("QLabel { color: #28a745; }")
        
        # Store collector for callbacks
        self._preview_collector = col
        self._preview_release = release
        self._preview_mbid = mbid
        
        # Initialize source tracking
        self._preview_source_results = {}
        self._preview_source_errors = {}
        
        utils.log_debug(f"UMA: Preview: Starting source fetches for {len(enabled_sources)} source(s)")
        
        # Fetch Bandcamp
        if "bandcamp" in enabled_sources:
            utils.log_debug(f"UMA: Preview: Fetching from Bandcamp")
            bc_source = sources.BandcampSource()
            bc_source.fetch(mock_album, lambda name, data, error: self._on_preview_source_done("bandcamp", data, error))
        
        # Fetch Discogs
        if "discogs" in enabled_sources:
            utils.log_debug(f"UMA: Preview: Fetching from Discogs")
            discogs_source = sources.DiscogsSource()
            discogs_source.fetch(mock_album, lambda name, data, error: self._on_preview_source_done("discogs", data, error))
        
        # Wait for sources to complete (with timeout)
        # Use QTimer to check completion status periodically
        self._preview_check_timer = QTimer()
        self._preview_check_timer.timeout.connect(lambda: self._check_preview_sources_complete())
        self._preview_check_timer.start(100)  # Check every 100ms
        
        # Set timeout (15 seconds - increased to allow Discogs API time to respond)
        self._preview_timeout_timer = QTimer()
        self._preview_timeout_timer.setSingleShot(True)
        self._preview_timeout_timer.timeout.connect(self._on_preview_timeout)
        self._preview_timeout_timer.start(15000)  # 15 seconds
    
    def _create_mock_album(self, mbid: str, title: str, artist: str, release: dict):
        """Create a mock album object for preview processing."""
        class MockAlbum:
            def __init__(self, mbid, title, artist, release):
                self.id = mbid
                self._requests = 0
                self._uma_release = release
                self._uma_release_group = release.get("release-group") if isinstance(release, dict) else None
                self._uma_band_artist = artist
                self._uma_band_title = title
                self._uma_replies = []
                
                # Mock metadata
                class MockMetadata:
                    def __init__(self, title, artist):
                        self._data = {
                            "album": title,
                            "albumartist": artist,
                            "artist": artist,
                            "title": title,
                        }
                    def get(self, key, default=None):
                        return self._data.get(key, default)
                
                self.metadata = MockMetadata(title, artist)
                
                # Mock tagger (needed for webservice)
                class MockTagger:
                    def __init__(self, parent_album):
                        self._network_manager = QNetworkAccessManager()
                        self._parent_album = parent_album
                        
                        class MockWebservice:
                            def __init__(self, tagger_ref):
                                self._tagger = tagger_ref
                                self._network = tagger_ref._network_manager
                            
                            def get_url(self, url=None, handler=None, parse_response_type=None, priority=None):
                                """
                                Mock webservice.get_url that actually makes HTTP requests.
                                Used by DiscogsSource in preview mode.
                                """
                                if not url or not handler:
                                    utils.log_warning("UMA: Preview: MockWebservice.get_url called without url or handler")
                                    return
                                
                                utils.log_debug(f"UMA: Preview: MockWebservice.get_url called for {url}")
                                
                                # Create network request with proper headers
                                request = QNetworkRequest(QUrl(url))
                                # Use appropriate User-Agent based on URL
                                if 'discogs.com' in url.lower():
                                    # Discogs API
                                    request.setRawHeader(b'User-Agent', b'Picard/2.9.0 (https://picard.musicbrainz.org)')
                                else:
                                    # Default
                                    request.setRawHeader(b'User-Agent', b'Picard/2.9.0 (https://picard.musicbrainz.org)')
                                request.setRawHeader(b'Accept', b'application/json')
                                # Qt6 follows redirects automatically; no FollowRedirectsAttribute needed
                                
                                # Make request
                                reply = self._network.get(request)
                                if not reply:
                                    utils.log_warning("UMA: Preview: MockWebservice failed to create network request")
                                    if handler:
                                        handler(None, None, "network_request_failed")
                                    return
                                
                                # Store reply to prevent garbage collection
                                if not hasattr(self._tagger._parent_album, '_preview_discogs_replies'):
                                    self._tagger._parent_album._preview_discogs_replies = []
                                self._tagger._parent_album._preview_discogs_replies.append(reply)
                                
                                # Connect finished signal
                                def on_finished():
                                    try:
                                        if reply.error() != QNetworkReply.NetworkError.NoError:
                                            error_msg = reply.errorString()
                                            utils.log_warning(f"UMA: Preview: MockWebservice error: {error_msg}")
                                            if handler:
                                                handler(None, reply, error_msg)
                                            return
                                        
                                        # Read and parse response
                                        data = reply.readAll()
                                        if parse_response_type == "json":
                                            try:
                                                response_data = json.loads(bytes(data).decode('utf-8'))
                                                if handler:
                                                    handler(response_data, reply, None)
                                            except json.JSONDecodeError as e:
                                                utils.log_warning(f"UMA: Preview: MockWebservice JSON decode error: {e}")
                                                if handler:
                                                    handler(None, reply, f"json_decode_error: {e}")
                                            except Exception as e:
                                                utils.log_warning(f"UMA: Preview: MockWebservice error: {e}")
                                                if handler:
                                                    handler(None, reply, str(e))
                                        else:
                                            # For non-JSON, pass raw data
                                            if handler:
                                                handler(data, reply, None)
                                    except Exception as e:
                                        utils.log_warning(f"UMA: Preview: MockWebservice handler error: {e}", exc_info=True)
                                        if handler:
                                            handler(None, reply, str(e))
                                    finally:
                                        # Clean up
                                        if hasattr(self._tagger._parent_album, '_preview_discogs_replies'):
                                            if reply in self._tagger._parent_album._preview_discogs_replies:
                                                self._tagger._parent_album._preview_discogs_replies.remove(reply)
                                        reply.deleteLater()
                                
                                reply.finished.connect(on_finished)
                        
                        self.webservice = MockWebservice(self)
                
                self.tagger = MockTagger(self)
        
        return MockAlbum(mbid, title, artist, release)
    
    def _on_preview_source_done(self, source_name: str, data, error: str):
        """Called when a source fetch completes."""
        if error:
            self._preview_source_errors[source_name] = error
            utils.log_debug(f"UMA: Preview: Source {source_name} failed: {error}")
            # Mark as done in collector
            if hasattr(self, '_preview_collector'):
                self._preview_collector._mark_source_done(source_name, False)
        else:
            self._preview_source_results[source_name] = data
            tag_count = len(data.tags) if data else 0
            genre_count = len(data.genres) if data else 0
            style_count = len(data.styles) if data else 0
            utils.log_debug(f"UMA: Preview: Source {source_name} completed: tags={tag_count}, genres={genre_count}, styles={style_count}")
            # Mark as done in collector and add data
            if hasattr(self, '_preview_collector'):
                if data:
                    self._preview_collector.add_source_data(source_name, data)
                self._preview_collector._mark_source_done(source_name, True)
        
        # Check if all sources are done after each completion
        # This ensures we process results as soon as possible
        if hasattr(self, '_preview_collector'):
            self._check_preview_sources_complete()
    
    def _check_preview_sources_complete(self):
        """Check if all preview sources have completed."""
        if not hasattr(self, '_preview_collector'):
            return
        
        col = self._preview_collector
        expected_sources = col.pending_sources | col.completed_sources | col.failed_sources
        
        # Check if all sources have reported (either in results or errors)
        all_done = all(
            source in self._preview_source_results or source in self._preview_source_errors
            for source in expected_sources
        )
        
        if all_done:
            # Stop both timers
            if hasattr(self, '_preview_check_timer'):
                self._preview_check_timer.stop()
            if hasattr(self, '_preview_timeout_timer'):
                self._preview_timeout_timer.stop()
            utils.log_debug(f"UMA: Preview: All sources completed, processing results")
            self._process_preview_results()
    
    def _on_preview_timeout(self):
        """Handle preview timeout - process whatever sources have completed."""
        utils.log_warning("UMA: Preview: Timeout waiting for sources - processing partial results")
        
        # Stop the check timer
        if hasattr(self, '_preview_check_timer'):
            self._preview_check_timer.stop()
        
        # Mark any pending sources as failed
        if hasattr(self, '_preview_collector'):
            col = self._preview_collector
            pending = col.pending_sources.copy()
            for source_name in pending:
                if source_name not in self._preview_source_results and source_name not in self._preview_source_errors:
                    self._preview_source_errors[source_name] = "timeout"
                    col._mark_source_done(source_name, False)
                    utils.log_debug(f"UMA: Preview: Marked {source_name} as failed due to timeout")
        
        # Process whatever results we have (even if incomplete)
        if hasattr(self, '_preview_collector'):
            self._process_preview_results()
            self.preview_step1_status.setText("⚠ Timeout: Some sources may be incomplete")
            self.preview_step1_status.setStyleSheet("QLabel { color: #d4a017; font-style: italic; }")
        else:
            self.preview_step1_status.setText("Timeout waiting for sources")
            self.preview_step1_status.setStyleSheet("QLabel { color: #dc3545; font-style: italic; }")
    
    def _process_preview_results(self):
        """
        Process preview results: add source data to collector, run merge pipeline, update displays.
        Note: Source data should already be added in _on_preview_source_done, but we double-check here.
        """
        utils.log_debug(f"UMA: Preview: Processing preview results")
        
        if not hasattr(self, '_preview_collector'):
            utils.log_warning("UMA: Preview: No collector available for processing")
            return
        
        col = self._preview_collector
        
        # Ensure all source data is added (in case _on_preview_source_done didn't do it)
        for source_name, data in self._preview_source_results.items():
            if data and source_name not in col.sources:
                col.add_source_data(source_name, data)
                utils.log_debug(f"UMA: Preview: Added data from {source_name} to collector")
        
        # Ensure failed sources are marked
        for source_name in self._preview_source_errors:
            if source_name in col.pending_sources:
                col._mark_source_done(source_name, False)
                utils.log_debug(f"UMA: Preview: Marked {source_name} as failed")
        
        # Store source results in persistent state
        self._preview_state["sources"] = {}
        for source_name, data in self._preview_source_results.items():
            if data:
                self._preview_state["sources"][source_name] = {
                    "tags": data.tags if hasattr(data, 'tags') else [],
                    "genres": data.genres if hasattr(data, 'genres') else [],
                    "styles": data.styles if hasattr(data, 'styles') else [],
                }
        
        # Update Step 1: Raw Sources
        self._update_preview_step1()
        
        # Run merge pipeline
        utils.log_debug(f"UMA: Preview: Running merge pipeline")
        col.merge()
        
        # Store final results in persistent state
        self._preview_state["clusters"] = col.clusters if hasattr(col, 'clusters') else []
        self._preview_state["final_genre"] = col.merged_genres[0] if col.merged_genres else ""
        self._preview_state["final_style"] = col.merged_styles if hasattr(col, 'merged_styles') else []
        self._preview_state["final_comment"] = col.merged_comment if hasattr(col, 'merged_comment') else ""
        self._preview_state["timestamp"] = datetime.datetime.now()
        
        # Log merge results
        utils.log_debug(f"UMA: Preview: Merge complete - genres={col.merged_genres}, styles={col.merged_styles}, comment={col.merged_comment[:50] if col.merged_comment else 'none'}")
        
        # Update remaining steps
        utils.log_debug(f"UMA: Preview: Updating UI displays")
        self._update_preview_step2(col)
        self._update_preview_step3(col)
        self._update_preview_step4(col)
        self._update_preview_step5(col)
        
        utils.log_debug(f"UMA: Preview: UI updated successfully")
        
        # Persist preview state to config
        self._save_preview_state()
        
        # Save display text cache for restoration
        self._save_preview_display_cache()
    
    def _save_preview_state(self):
        """Save current preview state to Picard config."""
        if not self._preview_state["mbid"]:
            return
        
        try:
            setting = self.api.global_config.setting
            # Save MBID
            setting[self.CONFIG_KEY_LAST_MBID] = self._preview_state["mbid"]

            # Build result dict (serializable)
            # Ensure all values are JSON-serializable
            result_dict = {
                "mbid": str(self._preview_state["mbid"]),
                "sources": {},
                "clusters": list(self._preview_state["clusters"]) if isinstance(self._preview_state["clusters"], (list, tuple)) else [],
                "final_genre": str(self._preview_state["final_genre"]) if self._preview_state["final_genre"] else "",
                "final_style": list(self._preview_state["final_style"]) if isinstance(self._preview_state["final_style"], (list, tuple)) else [],
                "final_comment": str(self._preview_state["final_comment"]) if self._preview_state["final_comment"] else "",
                "timestamp": None,
            }
            
            # Convert timestamp safely
            if self._preview_state["timestamp"]:
                try:
                    if hasattr(self._preview_state["timestamp"], 'isoformat'):
                        result_dict["timestamp"] = self._preview_state["timestamp"].isoformat()
                    else:
                        result_dict["timestamp"] = str(self._preview_state["timestamp"])
                except:
                    result_dict["timestamp"] = None
            
            # Convert sources dict safely (ensure all values are lists/strings)
            for source_name, source_data in self._preview_state["sources"].items():
                if isinstance(source_data, dict):
                    result_dict["sources"][str(source_name)] = {
                        "tags": list(source_data.get("tags", [])) if isinstance(source_data.get("tags"), (list, tuple)) else [],
                        "genres": list(source_data.get("genres", [])) if isinstance(source_data.get("genres"), (list, tuple)) else [],
                        "styles": list(source_data.get("styles", [])) if isinstance(source_data.get("styles"), (list, tuple)) else [],
                    }
            
            # Serialize to JSON string
            result_json = json.dumps(result_dict)
            setting[self.CONFIG_KEY_LAST_RESULT] = result_json
            
            utils.log_debug(f"UMA: Preview: Saved state for MBID {self._preview_state['mbid']}")
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to save state: {e}", exc_info=True)
    
    def _save_preview_display_cache(self):
        """
        Save the current display text from all preview step widgets to config.
        This allows restoring the preview display when the options dialog is reopened.
        """
        if not self._preview_state["mbid"]:
            return
        
        # Safety check: ensure all preview widgets exist
        required_widgets = ['preview_raw_sources', 'preview_mapped', 'preview_selected', 
                           'preview_filtered', 'preview_output']
        if not all(hasattr(self, widget) for widget in required_widgets):
            utils.log_debug("UMA: Preview: Widgets not available for display cache save")
            return
        
        try:
            # Extract text from all step widgets
            cache_data = {
                "mbid": self._preview_state["mbid"],
                "step1_text": self.preview_raw_sources.toPlainText(),
                "step2_text": self.preview_mapped.toPlainText(),
                "step3_text": self.preview_selected.toPlainText(),
                "step4_text": self.preview_filtered.toPlainText(),
                "step5_text": self.preview_output.toPlainText(),
            }
            
            # Serialize to JSON
            cache_json = json.dumps(cache_data)
            self.api.global_config.setting[self.CONFIG_KEY_LAST_PREVIEW] = cache_json

            # Also cache in instance attribute
            self._last_preview = cache_data
            
            utils.log_debug(f"UMA: Preview: Saved display cache for MBID {self._preview_state['mbid']}")
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to save display cache: {e}", exc_info=True)
    
    def _load_preview_display_cache(self):
        """
        Load the cached preview display text from config and restore it to the widgets.
        Returns True if cache was loaded and restored, False otherwise.
        """
        # Safety check: ensure all preview widgets exist
        required_widgets = ['preview_raw_sources', 'preview_mapped', 'preview_selected', 
                           'preview_filtered', 'preview_output', 'input_preview_mbid']
        if not all(hasattr(self, widget) for widget in required_widgets):
            utils.log_debug("UMA: Preview: Widgets not available for display cache restore")
            return False
        
        try:
            cache_json = self._get_config(self.CONFIG_KEY_LAST_PREVIEW, "")
            if not cache_json:
                return False
            
            # Deserialize JSON
            cache_data = json.loads(cache_json)
            
            # Validate structure
            if not isinstance(cache_data, dict):
                utils.log_warning("UMA: Preview: Invalid cache data format")
                return False
            
            required_keys = ["mbid", "step1_text", "step2_text", "step3_text", "step4_text", "step5_text"]
            if not all(key in cache_data for key in required_keys):
                utils.log_warning("UMA: Preview: Cache data missing required keys")
                return False
            
            # Cache in instance attribute
            self._last_preview = cache_data
            
            # Restore display text
            self._restore_preview_from_cache(cache_data)
            
            utils.log_debug(f"UMA: Preview: Restored display cache for MBID {cache_data.get('mbid', 'unknown')}")
            return True
        except json.JSONDecodeError as e:
            utils.log_warning(f"UMA: Preview: Failed to parse display cache JSON: {e}")
            return False
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to load display cache: {e}", exc_info=True)
            return False
    
    def _restore_preview_from_cache(self, cache_data: dict):
        """
        Restore preview display text from cached data to the widgets.
        This is a pure UI restoration - no network calls are made.
        """
        try:
            # Restore MBID
            mbid = cache_data.get("mbid", "")
            if mbid:
                self.input_preview_mbid.setText(mbid)
            
            # Restore step texts
            self.preview_raw_sources.setPlainText(cache_data.get("step1_text", ""))
            self.preview_mapped.setPlainText(cache_data.get("step2_text", ""))
            self.preview_selected.setPlainText(cache_data.get("step3_text", ""))
            self.preview_filtered.setPlainText(cache_data.get("step4_text", ""))
            self.preview_output.setPlainText(cache_data.get("step5_text", ""))
            
            utils.log_debug(f"UMA: Preview: Restored display text for MBID {mbid}")
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to restore display from cache: {e}", exc_info=True)
    
    def _load_preview_state(self):
        """Load preview state from Picard config. Returns (mbid, result_dict) or (None, None)."""
        try:
            mbid = self._get_config(self.CONFIG_KEY_LAST_MBID, "")
            if not mbid:
                return None, None

            result_json = self._get_config(self.CONFIG_KEY_LAST_RESULT, "")
            if not result_json:
                return mbid, None
            
            # Deserialize JSON
            result_dict = json.loads(result_json)
            
            utils.log_debug(f"UMA: Preview: Loaded state for MBID {mbid}")
            return mbid, result_dict
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to load state: {e}", exc_info=True)
            return None, None
    
    def _apply_preview_result(self, result_dict: dict):
        """
        Apply preview result to UI without triggering network requests.
        Reusable method for both restore and callback paths.
        """
        if not result_dict:
            return
        
        # Safety check: ensure preview widgets exist
        if not hasattr(self, 'input_preview_mbid') or not hasattr(self, 'preview_raw_sources'):
            utils.log_debug("UMA: Preview: Widgets not yet initialized, skipping restore")
            return
        
        try:
            # Update in-memory state
            self._preview_state["mbid"] = result_dict.get("mbid", "")
            self._preview_state["sources"] = result_dict.get("sources", {})
            self._preview_state["clusters"] = result_dict.get("clusters", [])
            self._preview_state["final_genre"] = result_dict.get("final_genre", "")
            self._preview_state["final_style"] = result_dict.get("final_style", [])
            self._preview_state["final_comment"] = result_dict.get("final_comment", "")
            
            # Parse timestamp if present
            timestamp_str = result_dict.get("timestamp")
            if timestamp_str:
                try:
                    self._preview_state["timestamp"] = datetime.datetime.fromisoformat(timestamp_str)
                except:
                    self._preview_state["timestamp"] = None
            
            # Restore MBID in field
            if self._preview_state["mbid"]:
                self.input_preview_mbid.setText(self._preview_state["mbid"])
            
            # Restore UI displays
            self._restore_preview_displays()
            
            utils.log_debug(f"UMA: Preview: Applied result for MBID {self._preview_state['mbid']}")
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to apply result: {e}", exc_info=True)
    
    def _clear_saved_preview_state(self):
        """Clear saved preview state from config (when user changes MBID or clears it)."""
        try:
            setting = self.api.global_config.setting
            setting[self.CONFIG_KEY_LAST_MBID] = ""
            setting[self.CONFIG_KEY_LAST_RESULT] = ""
            utils.log_debug("UMA: Preview: Cleared saved state")
        except Exception as e:
            utils.log_warning(f"UMA: Preview: Failed to clear saved state: {e}", exc_info=True)
    
    def _update_preview_step1(self):
        """Update Step 1: Raw Sources display."""
        lines = []
        
        # Count enabled sources
        enabled_count = len(self._preview_source_results) + len(self._preview_source_errors)
        if enabled_count > 0:
            lines.append(f"{enabled_count} source(s) enabled")
            lines.append("")
        
        for source_name in sorted(self._preview_source_results.keys()):
            data = self._preview_source_results[source_name]
            lines.append(f"{source_name.capitalize()}:")
            if data.genres:
                lines.append(f"  Genres: {', '.join(data.genres)}")
            if data.styles:
                lines.append(f"  Styles: {', '.join(data.styles)}")
            if data.tags:
                lines.append(f"  Tags: {', '.join(data.tags[:20])}")  # Limit to first 20 tags
                if len(data.tags) > 20:
                    lines.append(f"  ... and {len(data.tags) - 20} more tags")
            if not data.genres and not data.styles and not data.tags:
                lines.append("  (no data)")
            lines.append("")
        
        for source_name, error in self._preview_source_errors.items():
            lines.append(f"{source_name.capitalize()}:")
            lines.append(f"  Error: {error}")
            lines.append("")
        
        if not lines:
            lines.append("No source data available")
        
        self.preview_raw_sources.setPlainText("\n".join(lines))
        
        # Update status
        if self._preview_source_results:
            self.preview_step1_status.setText(f"✓ {len(self._preview_source_results)} source(s) completed")
            self.preview_step1_status.setStyleSheet("QLabel { color: #28a745; }")
        elif self._preview_source_errors:
            self.preview_step1_status.setText(f"⚠ {len(self._preview_source_errors)} source(s) failed")
            self.preview_step1_status.setStyleSheet("QLabel { color: #dc3545; }")
    
    def _update_preview_step2(self, col: collector.AlbumCollector):
        """Update Step 2: After Mapping display."""
        # Re-run the mapping step to get candidate clusters
        # (merge() already did this, but we need to capture the mapping for display)
        col.mapping_engine.refresh()
        
        # Collect all normalized tags
        all_normalized_tags = []
        seen_tag_norms = set()
        
        for source_name in col.sources.keys():
            norm_block = col.normalize_source(source_name)
            if norm_block:
                for genre in norm_block.genres:
                    norm = utils.normalize_tag(genre)
                    if norm and norm not in seen_tag_norms:
                        seen_tag_norms.add(norm)
                        all_normalized_tags.append(genre)
                for style in norm_block.styles:
                    norm = utils.normalize_tag(style)
                    if norm and norm not in seen_tag_norms:
                        seen_tag_norms.add(norm)
                        all_normalized_tags.append(style)
                for tag in norm_block.tags:
                    norm = utils.normalize_tag(tag)
                    if norm and norm not in seen_tag_norms:
                        seen_tag_norms.add(norm)
                        all_normalized_tags.append(tag)
        
        # Compute candidate clusters
        candidate_cluster_map = col.mapping_engine.compute_candidate_clusters(all_normalized_tags)
        
        if candidate_cluster_map:
            utils.log_debug(f"UMA: Preview: Mapping produced {len(candidate_cluster_map)} clusters")
        
        # Build display
        lines = []
        if candidate_cluster_map:
            lines.append("Mapped Clusters:")
            for cluster, source_tags in sorted(candidate_cluster_map.items()):
                tag_sample = source_tags[:5]
                lines.append(f"  {cluster} ← {', '.join(tag_sample)}")
                if len(source_tags) > 5:
                    lines.append(f"    ... and {len(source_tags) - 5} more tags")
        else:
            lines.append("No clusters mapped from tags")
        
        self.preview_mapped.setPlainText("\n".join(lines) if lines else "No mapping results")
    
    def _update_preview_step3(self, col: collector.AlbumCollector):
        """Update Step 3: Cluster Selection display."""
        selected = col.merged_genres[0] if col.merged_genres else None
        if selected:
            utils.log_debug(f"UMA: Preview: Selected cluster: {selected}")
            lines = [f"Selected: {selected}", "Reason: Highest priority among candidates"]
        else:
            utils.log_debug(f"UMA: Preview: No cluster selected")
            lines = ["No cluster selected", "Reason: No clusters matched"]
        
        self.preview_selected.setPlainText("\n".join(lines))
    
    def _update_preview_step4(self, col: collector.AlbumCollector):
        """Update Step 4: Generic Filter display."""
        # Get generic terms
        generic_terms = col._get_generic_terms()
        
        # Collect tags before filter (from merged_tags, which is after whitelist but before generic)
        # We need to reconstruct what was there before generic filter
        # Actually, merged_tags is already after generic filter, so we need to track this differently
        # For now, show what we have
        lines = []
        
        # Show styles before/after
        style_priority = col._get_priority_list("uma_priority_style", ["discogs", "bandcamp"])
        normalized_sources = {}
        for source_name in col.sources.keys():
            norm_block = col.normalize_source(source_name)
            if norm_block:
                normalized_sources[source_name] = norm_block
        
        styles_before = col._merge_field("styles", style_priority, normalized_sources)
        styles_after = col.merged_styles
        
        if styles_before != styles_after:
            dropped_styles = [s for s in styles_before if s not in styles_after]
            utils.log_debug(f"UMA: Preview: Filters removed {len(dropped_styles)} style tags")
            lines.append(f"Before: {', '.join(styles_before) if styles_before else '(none)'}")
            lines.append(f"After: {', '.join(styles_after) if styles_after else '(none)'}")
            if dropped_styles:
                lines.append(f"Dropped: {', '.join(dropped_styles)} (Generic filter)")
        else:
            lines.append(f"Before: {', '.join(styles_before) if styles_before else '(none)'}")
            lines.append(f"After: {', '.join(styles_after) if styles_after else '(none)'}")
            lines.append("(No generic styles filtered)")
        
        # Show comment tags (merged_tags is after generic filter)
        if col.merged_tags:
            lines.append("")
            lines.append(f"Comment Tags (after generic filter): {', '.join(col.merged_tags[:15])}")
            if len(col.merged_tags) > 15:
                lines.append(f"... and {len(col.merged_tags) - 15} more")
        
        self.preview_filtered.setPlainText("\n".join(lines) if lines else "No filter applied")
    
    def _update_preview_step5(self, col: collector.AlbumCollector):
        """Update Step 5: Final Output display."""
        lines = []
        if col.merged_genres:
            lines.append(f"Genre: {col.merged_genres[0]}")
        else:
            lines.append("Genre: (none)")
        
        if col.merged_styles:
            lines.append(f"Style: {', '.join(col.merged_styles)}")
        else:
            lines.append("Style: (none)")
        
        if col.merged_comment:
            lines.append(f"Comment: {col.merged_comment}")
        else:
            lines.append("Comment: (none)")
        
        self.preview_output.setPlainText("\n".join(lines))

    def load(self):
        """Load settings from Picard config into UI widgets."""
        # General
        self.check_debug.setChecked(self._get_config("uma_debug", False))

        # Load and restore preview state (if available)
        # This happens after widgets are created, so we can safely update UI
        # Use a longer delay to ensure all widgets are fully initialized
        mbid, result_dict = self._load_preview_state()
        if mbid and result_dict:
            # Restore preview state without triggering network requests
            # Use a timer to ensure all widgets are fully initialized
            # Increased delay to 500ms to ensure widgets are ready
            QTimer.singleShot(500, lambda: self._apply_preview_result(result_dict))

        # Also try to restore display text cache (simpler, no async needed)
        # This restores the actual display text that was shown in the previous session
        QTimer.singleShot(600, lambda: self._load_preview_display_cache())

        # Sources
        self.check_bandcamp.setChecked(self._get_config("uma_enable_bandcamp", True))
        self.check_discogs.setChecked(self._get_config("uma_enable_discogs", True))
        self.input_discogs_token.setText(self._get_config("uma_discogs_token", ""))
        self.check_bandcamp_fallback.setChecked(self._get_config("uma_bandcamp_fallback_search", False))
        self._update_token_status()

        # Merge & Priority - Genre
        genre_priority = self._get_config("uma_priority_genre", "bandcamp,discogs")
        genre_sources = [s.strip() for s in genre_priority.split(',') if s.strip()]
        if len(genre_sources) >= 1:
            self.combo_prio_genre_1.setCurrentText(genre_sources[0])
        if len(genre_sources) >= 2:
            self.combo_prio_genre_2.setCurrentText(genre_sources[1])
        self.combo_mode_genre.setCurrentText(self._get_config("uma_mode_genre", "append"))

        # Merge & Priority - Style
        style_priority = self._get_config("uma_priority_style", "discogs,bandcamp")
        style_sources = [s.strip() for s in style_priority.split(',') if s.strip()]
        if len(style_sources) >= 1:
            self.combo_prio_style_1.setCurrentText(style_sources[0])
        if len(style_sources) >= 2:
            self.combo_prio_style_2.setCurrentText(style_sources[1])
        self.combo_mode_style.setCurrentText(self._get_config("uma_mode_style", "append"))

        # Tag Mapping
        self.input_tag_mapping.setPlainText(self._get_config("uma_tag_mapping", ""))
        self.check_mapping_use_regex.setChecked(self._get_config("uma_mapping_use_regex", False))
        self.check_mapping_first_match.setChecked(self._get_config("uma_mapping_first_match_only", False))
        self.combo_mapping_mode.setCurrentText(self._get_config("uma_mapping_mode", "first_match"))
        self.check_filter_tags.setChecked(self._get_config("uma_filter_tags_with_mapping", False))

        # Extract cluster priority from mapping text (look for section header)
        mapping_text = self._get_config("uma_tag_mapping", "")
        cluster_priority_lines = []
        in_priority_section = False
        for line in mapping_text.splitlines():
            stripped = line.strip()
            # Check for priority section header
            if stripped.startswith('=') and 'PRIORITY' in stripped.upper():
                in_priority_section = True
                continue
            # Stop at next section header
            if in_priority_section and stripped.startswith('=') and stripped.count('=') >= 3:
                break
            # Collect priority lines
            if in_priority_section and stripped and '=' not in stripped:
                if not stripped.startswith('#') and not stripped.startswith('//'):
                    cluster_priority_lines.append(stripped)
        
        # Fallback: look for bare lines at end of file (no section header)
        if not cluster_priority_lines:
            lines = mapping_text.splitlines()
            # Look backwards for bare lines
            for line in reversed(lines):
                stripped = line.strip()
                if stripped and '=' not in stripped:
                    if not stripped.startswith('#') and not stripped.startswith('//'):
                        if not (stripped.startswith('=') and stripped.count('=') >= 3):
                            cluster_priority_lines.insert(0, stripped)
                elif stripped:  # Hit a line with content that's not a bare cluster name
                    break
        
        if cluster_priority_lines:
            self.input_cluster_priority.setPlainText('\n'.join(cluster_priority_lines))
        else:
            self.input_cluster_priority.setPlainText("")
        
        # Generic Filters
        self.input_generic_genres.setPlainText(self._get_config("uma_generic_genres",
            "Electronic\nElectronica\nElectronic Music\nRock\nPop"))
        
        # Update status bar
        self._update_status_bar()
        self._update_mapping_status()

    def save(self):
        """Save settings from UI widgets to Picard config."""
        setting = self.api.global_config.setting
        # General
        setting["uma_debug"] = self.check_debug.isChecked()

        # Sources
        setting["uma_enable_bandcamp"] = self.check_bandcamp.isChecked()
        setting["uma_enable_discogs"] = self.check_discogs.isChecked()
        setting["uma_discogs_token"] = self.input_discogs_token.text().strip()
        setting["uma_bandcamp_fallback_search"] = self.check_bandcamp_fallback.isChecked()

        # Merge & Priority - Genre (convert dropdowns to comma-separated string)
        genre_priority = f"{self.combo_prio_genre_1.currentText()},{self.combo_prio_genre_2.currentText()}"
        setting["uma_priority_genre"] = genre_priority
        setting["uma_mode_genre"] = self.combo_mode_genre.currentText()

        # Merge & Priority - Style
        style_priority = f"{self.combo_prio_style_1.currentText()},{self.combo_prio_style_2.currentText()}"
        setting["uma_priority_style"] = style_priority
        setting["uma_mode_style"] = self.combo_mode_style.currentText()

        # Tag Mapping (combine mapping rules + cluster priority)
        mapping_text = self.input_tag_mapping.toPlainText().strip()
        cluster_priority_text = self.input_cluster_priority.toPlainText().strip()

        # Append cluster priority to mapping text if present
        if cluster_priority_text:
            if mapping_text and not mapping_text.endswith('\n'):
                mapping_text += '\n'
            mapping_text += '\n===========================================\n'
            mapping_text += '= FINAL CLUSTER PRIORITY\n'
            mapping_text += '===========================================\n\n'
            mapping_text += cluster_priority_text

        setting["uma_tag_mapping"] = mapping_text

        # Mapping options
        setting["uma_mapping_use_regex"] = self.check_mapping_use_regex.isChecked()
        setting["uma_mapping_first_match_only"] = self.check_mapping_first_match.isChecked()
        setting["uma_mapping_mode"] = self.combo_mapping_mode.currentText()
        setting["uma_filter_tags_with_mapping"] = self.check_filter_tags.isChecked()

        # Generic Filters
        setting["uma_generic_genres"] = self.input_generic_genres.toPlainText().strip()
        
        # Update status bar after save
        self._update_status_bar()
    
    def open_discogs_token_page(self):
        """Open Discogs developer settings page in the default browser."""
        try:
            url = QUrl("https://www.discogs.com/settings/developers")
            if QDesktopServices.openUrl(url):
                utils.log_info("Opened Discogs token page in browser")
            else:
                utils.log_warning("Failed to open Discogs token page in browser")
        except Exception as e:
            utils.log_warning(f"Error opening Discogs token page: {e}")


def _get_config(key, default=None):
    return utils._get_config(key, default)


def album_processor(api, album, metadata, release):
    """
    Triggered when album metadata is loaded from MusicBrainz.
    This is the start of our pipeline.
    Implements idempotency check to prevent repeated processing.
    """
    if not _get_config("uma_enabled", True):
        return

    # Get album title once
    album_title = metadata.get('album', 'unknown')

    # Idempotency check: if already processed, skip
    if hasattr(album, "_uma_collector") and album._uma_collector.finalized:
        utils.log_debug(f"Album '{album_title}' already processed, skipping")
        return

    # Initialize Collector
    if not hasattr(album, "_uma_collector"):
        album._uma_collector = collector.AlbumCollector(album_id=str(id(album)), album_title=album_title)
        album._uma_collector._album_object = album
        utils.log_info(f"Initialized UMA Collector for album '{album_title}'")

    col = album._uma_collector
    col.set_album_title(album_title)  # Ensure title is set

    # Determine which sources will be fetched
    enabled_sources = set()

    # Bandcamp
    if _get_config("uma_enable_bandcamp", True):
        utils.log_info(f"UMA: bandcamp: start collecting for album '{album_title}'")

        # Cache MB artist/album on the album object so BandcampSource can use
        # them for fallback search even before album.metadata is fully populated.
        if not hasattr(album, "_uma_band_artist"):
            album_artist = metadata.get("albumartist") or metadata.get("artist")
            album._uma_band_artist = album_artist
        if not hasattr(album, "_uma_band_title"):
            album._uma_band_title = album_title

        # Use multi-step URL resolution (relations, annotation, cover art)
        release_group = release.get("release-group") if release and isinstance(release, dict) else None
        bc_url, bc_source = sources.resolve_bandcamp_url(album, release, release_group)

        fallback_enabled = _get_config("uma_bandcamp_fallback_search", False)
        if bc_url or fallback_enabled:
            enabled_sources.add("bandcamp")
            if not bc_url:
                utils.log_debug(f"UMA: bandcamp: no Bandcamp URL found via multi-step discovery, will use fallback search")
            else:
                utils.log_debug(f"UMA: bandcamp: URL found via {bc_source}")
        else:
            utils.log_info(f"UMA: bandcamp: no Bandcamp URL found and fallback disabled - skipping")
    else:
        utils.log_debug(f"UMA: bandcamp: disabled in settings - skipping")

    # Discogs
    if _get_config("uma_enable_discogs", True):
        enabled_sources.add("discogs")

    # Initialize pending sources
    col.initialize_sources(enabled_sources)

    # If no sources are enabled, finalize immediately
    if not enabled_sources:
        utils.log_debug(f"No sources enabled for album '{album_title}', finalizing immediately")
        col._maybe_finalize_album(album)
        return

    # Bandcamp
    if "bandcamp" in enabled_sources:
        release_group = release.get("release-group") if release and isinstance(release, dict) else None
        bc_url, bc_source = sources.resolve_bandcamp_url(album, release, release_group)
        task_id = f'dj_genre_bandcamp_{id(album)}'
        api.add_album_task(album, task_id, 'Fetching Bandcamp genres')

        if bc_url:
            utils.log_info(f"UMA: bandcamp: fetching metadata from URL: {bc_url} (found in {bc_source})")
            src = sources.get_source("bandcamp")
            if src:
                src.fetch_from_url(album, bc_url, _make_callback(api, album, col, "bandcamp", task_id, album_title))
            else:
                utils.log_error(f"UMA: bandcamp: failed to get Bandcamp source instance")
                col._mark_source_done("bandcamp", success=False)
                api.complete_album_task(album, task_id)
        else:
            # Fallback search (we know it's enabled because bandcamp is in enabled_sources)
            utils.log_info(f"UMA: bandcamp: attempting fallback search for album '{album_title}'")
            src = sources.get_source("bandcamp")
            if src:
                # Store release/release_group on album for BandcampSource to access
                if not hasattr(album, '_uma_release'):
                    album._uma_release = release
                if not hasattr(album, '_uma_release_group'):
                    album._uma_release_group = release_group
                src.fetch(album, _make_callback(api, album, col, "bandcamp", task_id, album_title))
            else:
                utils.log_error(f"UMA: bandcamp: failed to get Bandcamp source instance")
                col._mark_source_done("bandcamp", success=False)
                api.complete_album_task(album, task_id)

    # Discogs
    if "discogs" in enabled_sources:
        if not hasattr(album, '_uma_release'):
            album._uma_release = release
        if not hasattr(album, '_uma_release_group'):
            release_group = release.get("release-group") if release and isinstance(release, dict) else None
            album._uma_release_group = release_group
        task_id = f'dj_genre_discogs_{id(album)}'
        api.add_album_task(album, task_id, 'Fetching Discogs genres')
        src = sources.get_source("discogs")
        src.fetch(album, _make_callback(api, album, col, "discogs", task_id, album_title))


def _make_callback(api, album, col, source_name, task_id, album_title=None):
    """Create a callback that handles result and completes the album task."""
    def cb(name, block, error):
        try:
            # Update album title in collector if provided
            if album_title and album_title != 'unknown':
                col.set_album_title(album_title)

            success = False
            if error:
                utils.log_warning(f"{name} failed for album '{col.album_title}': {error}")
            elif block:
                col.add_source_data(name, block, album_title)
                success = True
            else:
                # No error but no block - treat as failure
                utils.log_debug(f"{name} returned no data for album '{col.album_title}'")

            # Mark source as done (success or failure)
            col._mark_source_done(source_name, success=success)

            # Check if all sources are resolved and finalize if so
            col._maybe_finalize_album(album)

        finally:
            api.complete_album_task(album, task_id)

    return cb

def _find_bandcamp_url(release):
    """
    Extract Bandcamp URL from MusicBrainz release relations.
    Looks for URLs where resource domain ends with *.bandcamp.com/album/*
    Based on bandcamp_tag_fetcher implementation.
    """
    if not release:
        utils.log_debug("UMA: bandcamp: release is None")
        return None
    
    if not isinstance(release, dict):
        utils.log_debug(f"UMA: bandcamp: release is not a dict (type: {type(release)})")
        return None
    
    relations = release.get("relations", [])
    if not isinstance(relations, list):
        utils.log_debug(f"UMA: bandcamp: relations is not a list (type: {type(relations)}, value: {relations})")
        return None
    
    utils.log_debug(f"UMA: bandcamp: checking {len(relations)} relations for Bandcamp URL")
    
    for idx, relation in enumerate(relations):
        if not isinstance(relation, dict):
            utils.log_debug(f"UMA: bandcamp: relation[{idx}] is not a dict, skipping")
            continue
        
        url_obj = relation.get("url", {})
        if not isinstance(url_obj, dict):
            # Try direct 'resource' key as fallback
            resource = relation.get("resource", "")
            if resource and ".bandcamp.com/album/" in resource.lower():
                utils.log_info(f"UMA: bandcamp: found Bandcamp URL in relation[{idx}]: {resource}")
                return resource
            continue
        
        resource = url_obj.get("resource", "")
        if not resource:
            continue
        
        # Check if it's a Bandcamp album URL (matches *.bandcamp.com/album/*)
        resource_lower = resource.lower()
        if ".bandcamp.com/album/" in resource_lower:
            utils.log_info(f"UMA: bandcamp: found Bandcamp URL in relations[{idx}]: {resource}")
            return resource
        else:
            utils.log_debug(f"UMA: bandcamp: relation[{idx}] resource '{resource}' is not a Bandcamp album URL")
    
    utils.log_info("UMA: bandcamp: no Bandcamp URL found in relations")
    return None

def track_processor(api, track, metadata):
    """
    Called for each track when it's processed.
    This ensures that tracks created after UMA's album processor runs
    still get the merged metadata applied.
    """
    if not _get_config("uma_enabled", True):
        return

    # Access parent album via track object
    album = track.album if hasattr(track, 'album') else None
    if album is None:
        return

    # Check if album has a collector with finalized metadata
    if not hasattr(album, "_uma_collector"):
        return

    col = album._uma_collector

    # Only apply if collector is finalized (all sources resolved)
    if not col.finalized:
        utils.log_debug(f"Track processor: Collector not finalized yet for album '{col.album_title}', skipping track")
        return

    # Check if we have metadata to apply
    has_genres = bool(col.merged_genres)
    has_styles = bool(col.merged_styles)
    has_comment = bool(col.merged_comment)

    if not has_genres and not has_styles and not has_comment:
        return

    # Apply metadata to this track
    track_title = metadata.get('title', 'unknown track')
    track_updated = False

    try:
        # Apply Genre
        if has_genres:
            col._apply_field(metadata, "genre", col.merged_genres, "uma_mode_genre")
            track_updated = True

        # Apply Style
        if has_styles:
            col._apply_field(metadata, "style", col.merged_styles, "uma_mode_style")
            track_updated = True

        # Apply Comment
        if has_comment:
            col._apply_comment(metadata, col.merged_comment, "uma_mode_comment")
            track_updated = True

        if track_updated:
            utils.log_debug(f"Track processor: Applied UMA metadata to track '{track_title}' from album '{col.album_title}'")
    except Exception as e:
        utils.log_warning(f"Track processor: Error applying metadata to track '{track_title}': {e}")


def enable(api):
    """Called when plugin is enabled."""
    utils.set_api(api)
    utils.migrate_legacy_config()
    api.register_album_metadata_processor(album_processor)
    api.register_track_metadata_processor(track_processor)
    api.register_options_page(UMAOptionsPage)


