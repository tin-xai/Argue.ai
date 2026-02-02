#!/usr/bin/env python3
"""
AI Brainstorm Panel - LLM ↔ LLM Auto-Conversation
G2G-style peer-to-peer communication between two AI chatbots
Supports: ChatGPT, DeepSeek, Gemini, Claude
"""

import sys
import os
import json
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QSplitter, QTextEdit, QComboBox,
    QFileDialog, QMessageBox
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QPalette


# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_FILE = os.path.join(SCRIPT_DIR, "examples.json")


# Persistent storage directory
STORAGE_DIR = os.path.expanduser("~/.brainstorm_panel")

# Available chatbot configurations
AVAILABLE_CHATBOTS = {
    "ChatGPT": {
        "name": "ChatGPT",
        "url": "https://chat.openai.com",
        "color": "#10a37f",
        "icon": "◉",
        "input_selector": "#prompt-textarea",
        "send_selector": "button[data-testid='send-button']",
        "response_selector": "[data-message-author-role='assistant']",
    },
    "DeepSeek": {
        "name": "DeepSeek", 
        "url": "https://chat.deepseek.com",
        "color": "#3b82f6",
        "icon": "◆",
        "input_selector": "textarea, #chat-input, [contenteditable='true']",
        "send_selector": "button[type='submit'], button:has(svg), div[role='button']",
        "response_selector": ".ds-markdown, .message-content, [class*='answer'], [class*='response']",
    },
    "Gemini": {
        "name": "Gemini",
        "url": "https://gemini.google.com/app",
        "color": "#8e44ad",
        "icon": "✦",
        "input_selector": "rich-textarea .ql-editor, .text-input-field textarea, [contenteditable='true']",
        "send_selector": "button[aria-label='Send message'], button.send-button, button[mat-icon-button]",
        "response_selector": ".model-response-text, .response-content, message-content[class*='model']",
    },
    "Claude": {
        "name": "Claude",
        "url": "https://claude.ai/new",
        "color": "#d97706",
        "icon": "◈",
        "input_selector": "[contenteditable='true'].ProseMirror, div[contenteditable='true'], fieldset textarea",
        "send_selector": "button[aria-label='Send Message'], button[type='submit']:not(:disabled)",
        "response_selector": "[data-is-streaming], .font-claude-message, [class*='claude-message']",
    },
}

# Default chatbot selection (will be updated by UI)
CHATBOTS = [
    AVAILABLE_CHATBOTS["ChatGPT"],
    AVAILABLE_CHATBOTS["DeepSeek"],
]

# Template for forwarding messages (G2G style)
FORWARD_TEMPLATE = """The response from the opposite party:

{message}"""


class ChatBridge(QObject):
    """Handles G2G-style communication between the two chatbot panels"""
    message_received = pyqtSignal(int, str)
    status_update = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.is_running = False
        self.panels = []
        self.last_response_count = [0, 0]
        self.last_response_text = ["", ""]
        self.check_timer = QTimer()
        self.check_timer.timeout.connect(self.check_for_responses)
        self.waiting_for_panel = -1  # Which panel we're waiting for (-1 = none)
        self.right_panel_initial_prompt = ""  # Store right panel prompt for later
        self.last_text_check = ""  # For stability check
        self.stable_count = 0  # Count how many times text is stable
        self.get_chatbots = None  # Function to get current chatbot configs
        self.expecting_new_response = False  # True after we send a message
        self.sent_message_time = 0  # Timestamp when we sent
        
    def set_panels(self, panels):
        self.panels = panels
    
    def set_chatbot_getter(self, getter):
        """Set the function to get current chatbot configurations"""
        self.get_chatbots = getter
    
    def get_current_chatbots(self):
        """Get the current chatbot configurations"""
        if self.get_chatbots:
            return self.get_chatbots()
        return CHATBOTS
        
    def start(self, initial_prompts):
        """Start the conversation - send to left panel first, wait for response"""
        self.is_running = True
        self.last_response_count = [0, 0]
        self.last_response_text = ["", ""]
        self.stable_count = 0
        self.last_text_check = ""
        
        chatbots = self.get_current_chatbots()
        left_name = chatbots[0]['name']
        
        # Store right panel prompt for later (will be sent after left panel responds)
        prompt1, prompt2 = initial_prompts
        self.right_panel_initial_prompt = prompt2
        
        # Only send to left panel first
        if prompt1.strip():
            print(f"[Start] Sending initial prompt to {left_name} only...")
            self.send_message(0, prompt1)
            
        # Wait for left panel to respond
        self.waiting_for_panel = 0
        self.expecting_new_response = True
        self.status_update.emit(f"⏳ Waiting for {left_name} to respond...")
        
        # Start checking for responses after a delay (check every 2.5 seconds)
        QTimer.singleShot(5000, lambda: self.check_timer.start(2500))
        
    def stop(self):
        """Stop the conversation loop"""
        self.is_running = False
        self.check_timer.stop()
        self.waiting_for_panel = -1
        self.expecting_new_response = False
        self.status_update.emit("Stopped")
        
    def check_for_responses(self):
        """Check the panel we're waiting for"""
        if not self.is_running or self.waiting_for_panel < 0:
            return
            
        self.check_panel_response(self.waiting_for_panel)
            
    def check_panel_response(self, index):
        """Check if a panel has a new complete response"""
        if index >= len(self.panels):
            return
            
        panel = self.panels[index]
        chatbots = self.get_current_chatbots()
        config = chatbots[index]
        name = config['name']
        
        # JavaScript to get the last response and check if streaming
        js_code = f"""
        (function() {{
            // Try multiple response selectors
            const selectors = "{config['response_selector']}".split(', ');
            let responses = [];
            for (const sel of selectors) {{
                try {{
                    const found = document.querySelectorAll(sel);
                    if (found.length > 0) {{
                        responses = found;
                        console.log('Found responses with selector:', sel, found.length);
                        break;
                    }}
                }} catch(e) {{}}
            }}
            
            if (responses.length === 0) {{
                console.log('No responses found');
                return JSON.stringify({{count: 0, text: '', streaming: false, hasCompletionIndicators: false, debug: 'no responses'}});
            }}
            
            const lastResponse = responses[responses.length - 1];
            const text = lastResponse.innerText || lastResponse.textContent || '';
            
            // Check if still streaming (multiple indicators)
            const streamingSelectors = [
                '.result-streaming',
                '[class*="streaming"]',
                '[class*="typing"]', 
                '[class*="loading"]',
                '[class*="cursor"]',
                '.animate-pulse',
                '[data-state="streaming"]',
                '[data-is-streaming="true"]'
            ];
            let isStreaming = false;
            for (const sel of streamingSelectors) {{
                if (document.querySelector(sel)) {{
                    isStreaming = true;
                    console.log('Streaming detected via:', sel);
                    break;
                }}
            }}
            
            // Check for Stop button (indicates still generating)
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {{
                const btnText = (btn.innerText || '').toLowerCase();
                const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
                if (btnText.includes('stop') || ariaLabel.includes('stop')) {{
                    isStreaming = true;
                    console.log('Streaming detected: Stop button visible');
                    break;
                }}
            }}
            
            // Check for completion indicators (buttons that appear when response is done)
            let hasCompletionIndicators = false;
            const lastResponseContainer = lastResponse.closest('[data-message-id]') || lastResponse.parentElement?.parentElement;
            if (lastResponseContainer) {{
                // ChatGPT: Look for copy button, thumbs up/down, regenerate button
                const completionSelectors = [
                    'button[aria-label*="Copy"]',
                    'button[aria-label*="copy"]',
                    'button[data-testid="copy-turn-action-button"]',
                    'button[aria-label*="Good response"]',
                    'button[aria-label*="Bad response"]',
                    'button[aria-label*="Read aloud"]'
                ];
                for (const sel of completionSelectors) {{
                    if (lastResponseContainer.querySelector(sel) || document.querySelector(sel)) {{
                        hasCompletionIndicators = true;
                        console.log('Completion indicator found:', sel);
                        break;
                    }}
                }}
            }}
            
            // Also check globally for these indicators near the last message
            if (!hasCompletionIndicators) {{
                // If we find action buttons at the bottom of chat, response is likely complete
                const actionButtons = document.querySelectorAll('[data-testid="copy-turn-action-button"], button[aria-label*="Copy"]');
                if (actionButtons.length > 0) {{
                    hasCompletionIndicators = true;
                }}
            }}
            
            console.log('Response check:', {{count: responses.length, textLen: text.length, streaming: isStreaming, hasCompletionIndicators: hasCompletionIndicators}});
            
            return JSON.stringify({{
                count: responses.length,
                text: text.trim().substring(0, 8000),
                streaming: isStreaming,
                hasCompletionIndicators: hasCompletionIndicators
            }});
        }})();
        """
        
        panel.browser.page().runJavaScript(js_code, lambda result: self.handle_response_check(index, result))
        
    def handle_response_check(self, panel_index, result):
        """Handle the result of checking for a response"""
        if not self.is_running or not result:
            return
            
        try:
            import json
            data = json.loads(result)
            
            count = data.get('count', 0)
            text = data.get('text', '')
            is_streaming = data.get('streaming', False)
            has_completion_indicators = data.get('hasCompletionIndicators', False)
            chatbots = self.get_current_chatbots()
            name = chatbots[panel_index]['name']
            
            # Simple text stability approach - ignore unreliable streaming detection
            if count > 0 and text:
                # When expecting a new response, wait for text to change from recorded
                if self.expecting_new_response:
                    text_changed = text != self.last_response_text[panel_index]
                    if text_changed:
                        print(f"[{name}] New response detected! Starting stability tracking...")
                        self.expecting_new_response = False
                        self.stable_count = 0
                        self.last_text_check = text
                    else:
                        print(f"[{name}] Waiting for new response... (current len: {len(text)})")
                        self.status_update.emit(f"⏳ Waiting for {name} to start responding...")
                        return
                
                # Track text stability (ignore streaming detection - too unreliable)
                if text == self.last_text_check:
                    self.stable_count += 1
                else:
                    self.stable_count = 0
                    self.last_text_check = text
                
                # Wait for 5 stable checks (~12.5 seconds) to be sure response is complete
                stability_threshold = 5
                is_complete = self.stable_count >= stability_threshold
                    
                print(f"[{name}] Responses: {count}, Stable: {self.stable_count}/{stability_threshold}, Len: {len(text)}")
                
                # Update status
                if self.stable_count > 0 and not is_complete:
                    self.status_update.emit(f"⏳ {name} responding... verifying ({self.stable_count}/{stability_threshold})")
                else:
                    self.status_update.emit(f"⏳ {name} is generating... ({len(text)} chars)")
                
                # Forward when stable
                if is_complete:
                    
                    print(f"[{name}] ✓ RESPONSE COMPLETE! Forwarding...")
                    
                    self.last_response_count[panel_index] = count
                    self.last_response_text[panel_index] = text
                    self.stable_count = 0
                    self.last_text_check = ""
                    
                    # Notify UI
                    self.message_received.emit(panel_index, text[:80] + "..." if len(text) > 80 else text)
                    
                    # Forward to the other panel
                    other_index = 1 - panel_index
                    other_name = chatbots[other_index]['name']
                    
                    # For the first left panel response, send right panel's initial prompt + left panel's response
                    if panel_index == 0 and self.right_panel_initial_prompt:
                        message_to_send = self.right_panel_initial_prompt + "\n\nThe first round from the opposite party:\n\n" + text
                        self.right_panel_initial_prompt = ""  # Clear it
                    else:
                        message_to_send = FORWARD_TEMPLATE.format(message=text)
                    
                    print(f"[{name}] Sending to {other_name}...")
                    self.send_message(other_index, message_to_send)
                    
                    # Now wait for the other panel - reset counters for fresh detection
                    self.waiting_for_panel = other_index
                    self.stable_count = 0
                    self.last_text_check = ""
                    self.expecting_new_response = True
                    self.status_update.emit(f"⏳ Waiting for {other_name} to respond...")
            else:
                print(f"[{name}] No responses yet...")
                    
        except Exception as e:
            print(f"Error parsing response: {e}")
            self.status_update.emit(f"Error: {str(e)[:30]}")
            
    def send_message(self, panel_index, message):
        """Send a message to a specific panel"""
        if panel_index >= len(self.panels):
            return
            
        panel = self.panels[panel_index]
        chatbots = self.get_current_chatbots()
        config = chatbots[panel_index]
        name = config['name']
        
        self.status_update.emit(f"📤 Sending to {name}...")
        
        # Escape for JavaScript
        escaped = message.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        escaped = escaped.replace('\n', '\\n').replace('\r', '').replace("'", "\\'")
        
        js_code = f"""
        (function() {{
            // Try multiple input selectors
            const inputSelectors = "{config['input_selector']}".split(', ');
            let input = null;
            for (const sel of inputSelectors) {{
                input = document.querySelector(sel);
                if (input) break;
            }}
            
            if (!input) {{
                console.log('No input found with selectors:', inputSelectors);
                return 'no input';
            }}
            
            console.log('Found input:', input);
            
            const text = `{escaped}`;
            
            // Focus and clear
            input.focus();
            input.select && input.select();
            
            // Method 1: Use execCommand insertText (works with React)
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, text);
            
            // Method 2: If that didn't work, try DataTransfer (paste simulation)
            if (!input.value && !input.innerText) {{
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                const pasteEvent = new ClipboardEvent('paste', {{
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true
                }});
                input.dispatchEvent(pasteEvent);
            }}
            
            // Method 3: Direct value set with React fiber hack
            if (!input.value && !input.innerText) {{
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
                    'value'
                )?.set;
                if (nativeSetter) {{
                    nativeSetter.call(input, text);
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }}
            
            // Trigger events to update React state
            input.dispatchEvent(new Event('input', {{bubbles: true, cancelable: true}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
            
            // Wait then send
            setTimeout(() => {{
                const panelName = "{name}";
                
                // For DeepSeek, use Enter key directly (more reliable)
                if (panelName === "DeepSeek") {{
                    console.log('DeepSeek: Using Enter key to send');
                    // Simulate Ctrl+Enter or just Enter
                    input.dispatchEvent(new KeyboardEvent('keydown', {{
                        key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                        bubbles: true, cancelable: true
                    }}));
                    return;
                }}
                
                // For ChatGPT, try the specific send button
                const sendSelectors = "{config['send_selector']}".split(', ');
                let sendBtn = null;
                for (const sel of sendSelectors) {{
                    try {{
                        sendBtn = document.querySelector(sel);
                        if (sendBtn && !sendBtn.disabled) break;
                    }} catch(e) {{}}
                }}
                
                if (sendBtn && !sendBtn.disabled) {{
                    console.log('Clicking send:', sendBtn);
                    sendBtn.click();
                }} else {{
                    // Fallback: Enter key
                    console.log('Fallback: Enter key');
                    input.dispatchEvent(new KeyboardEvent('keydown', {{
                        key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true
                    }}));
                }}
            }}, 500);
            
            return 'sent';
        }})();
        """
        
        panel.browser.page().runJavaScript(js_code, lambda r: print(f"Send to {name}: {r}"))


class BrowserPanel(QFrame):
    """A panel containing a browser view"""
    
    def __init__(self, config, profile, parent=None):
        super().__init__(parent)
        self.config = config
        self.profile = profile
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Minimal header
        header = QWidget()
        header.setFixedHeight(28)
        header.setStyleSheet("background: #1a1a24;")
        
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 10, 0)
        
        self.title = QLabel(f"{self.config['icon']} {self.config['name']}")
        self.title.setStyleSheet(f"color: {self.config['color']}; font-size: 12px; font-weight: bold;")
        header_layout.addWidget(self.title)
        header_layout.addStretch()
        
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(24, 24)
        refresh_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #888; border: none; font-size: 14px; }
            QPushButton:hover { color: white; }
        """)
        refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(refresh_btn)
        
        layout.addWidget(header)
        
        # Browser
        self.browser = QWebEngineView()
        page = QWebEnginePage(self.profile, self.browser)
        self.browser.setPage(page)
        self.browser.setUrl(QUrl(self.config['url']))
        layout.addWidget(self.browser, stretch=1)
        
    def refresh(self):
        self.browser.reload()
    
    def set_chatbot(self, config):
        """Change the chatbot for this panel"""
        self.config = config
        self.title.setText(f"{config['icon']} {config['name']}")
        self.title.setStyleSheet(f"color: {config['color']}; font-size: 12px; font-weight: bold;")
        self.browser.setUrl(QUrl(config['url']))


class ControlPanel(QFrame):
    """Control panel with initial prompts for both chatbots"""
    
    start_clicked = pyqtSignal(tuple)
    stop_clicked = pyqtSignal()
    llm_changed = pyqtSignal(int, str)  # panel_index, llm_name
    save_pdf_clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_llms = ["ChatGPT", "DeepSeek"]  # Default selection
        self.setup_ui()
        
    def setup_ui(self):
        self.setStyleSheet("background: #1a1a24; border-top: 1px solid #2a2a3a;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        
        # Row 0: LLM Selection dropdowns
        llm_selection_layout = QHBoxLayout()
        
        # Left LLM selector
        left_llm_frame = QFrame()
        left_llm_layout = QHBoxLayout(left_llm_frame)
        left_llm_layout.setContentsMargins(0, 0, 0, 0)
        left_llm_layout.setSpacing(8)
        
        left_llm_label = QLabel("Left Panel:")
        left_llm_label.setStyleSheet("color: #888; font-size: 11px;")
        left_llm_layout.addWidget(left_llm_label)
        
        self.left_llm_dropdown = QComboBox()
        self.left_llm_dropdown.setFixedWidth(120)
        for name in AVAILABLE_CHATBOTS.keys():
            self.left_llm_dropdown.addItem(f"{AVAILABLE_CHATBOTS[name]['icon']} {name}", name)
        self.left_llm_dropdown.setCurrentText(f"{AVAILABLE_CHATBOTS['ChatGPT']['icon']} ChatGPT")
        self.left_llm_dropdown.setStyleSheet("""
            QComboBox { background: #2a2a3a; color: white; border: 1px solid #3a3a4a; border-radius: 4px; padding: 4px 8px; font-size: 11px; }
            QComboBox:hover { border-color: #6366f1; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #2a2a3a; color: white; selection-background-color: #6366f1; }
        """)
        self.left_llm_dropdown.currentIndexChanged.connect(lambda: self.on_llm_changed(0))
        left_llm_layout.addWidget(self.left_llm_dropdown)
        left_llm_layout.addStretch()
        llm_selection_layout.addWidget(left_llm_frame)
        
        # Right LLM selector
        right_llm_frame = QFrame()
        right_llm_layout = QHBoxLayout(right_llm_frame)
        right_llm_layout.setContentsMargins(0, 0, 0, 0)
        right_llm_layout.setSpacing(8)
        
        right_llm_label = QLabel("Right Panel:")
        right_llm_label.setStyleSheet("color: #888; font-size: 11px;")
        right_llm_layout.addWidget(right_llm_label)
        
        self.right_llm_dropdown = QComboBox()
        self.right_llm_dropdown.setFixedWidth(120)
        for name in AVAILABLE_CHATBOTS.keys():
            self.right_llm_dropdown.addItem(f"{AVAILABLE_CHATBOTS[name]['icon']} {name}", name)
        self.right_llm_dropdown.setCurrentIndex(1)  # Default to DeepSeek
        self.right_llm_dropdown.setStyleSheet("""
            QComboBox { background: #2a2a3a; color: white; border: 1px solid #3a3a4a; border-radius: 4px; padding: 4px 8px; font-size: 11px; }
            QComboBox:hover { border-color: #6366f1; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #2a2a3a; color: white; selection-background-color: #6366f1; }
        """)
        self.right_llm_dropdown.currentIndexChanged.connect(lambda: self.on_llm_changed(1))
        right_llm_layout.addWidget(self.right_llm_dropdown)
        right_llm_layout.addStretch()
        llm_selection_layout.addWidget(right_llm_frame)
        
        layout.addLayout(llm_selection_layout)
        
        # Row 1: Prompt inputs
        prompts_layout = QHBoxLayout()
        
        # Left panel prompt
        self.left_prompt_frame = QFrame()
        left_prompt_layout = QVBoxLayout(self.left_prompt_frame)
        left_prompt_layout.setContentsMargins(0, 0, 0, 0)
        left_prompt_layout.setSpacing(4)
        
        self.left_prompt_label = QLabel("◉ ChatGPT Initial Prompt:")
        self.left_prompt_label.setStyleSheet("color: #10a37f; font-size: 11px; font-weight: bold;")
        left_prompt_layout.addWidget(self.left_prompt_label)
        
        self.chatgpt_prompt = QTextEdit()
        self.chatgpt_prompt.setMaximumHeight(60)
        self.chatgpt_prompt.setPlaceholderText("Represent me, Alice, and create a dramatic improv scene with my friend, Bob...")
        self.chatgpt_prompt.setStyleSheet("""
            QTextEdit { background: #0f0f14; color: white; border: 1px solid #2a2a3a; border-radius: 4px; padding: 4px; font-size: 12px; }
        """)
        left_prompt_layout.addWidget(self.chatgpt_prompt)
        prompts_layout.addWidget(self.left_prompt_frame)
        
        # Right panel prompt
        self.right_prompt_frame = QFrame()
        right_prompt_layout = QVBoxLayout(self.right_prompt_frame)
        right_prompt_layout.setContentsMargins(0, 0, 0, 0)
        right_prompt_layout.setSpacing(4)
        
        self.right_prompt_label = QLabel("◆ DeepSeek Initial Prompt:")
        self.right_prompt_label.setStyleSheet("color: #3b82f6; font-size: 11px; font-weight: bold;")
        right_prompt_layout.addWidget(self.right_prompt_label)
        
        self.deepseek_prompt = QTextEdit()
        self.deepseek_prompt.setMaximumHeight(60)
        self.deepseek_prompt.setPlaceholderText("Represent me, Bob, and create a dramatic improv scene with my friend, Alice...")
        self.deepseek_prompt.setStyleSheet("""
            QTextEdit { background: #0f0f14; color: white; border: 1px solid #2a2a3a; border-radius: 4px; padding: 4px; font-size: 12px; }
        """)
        right_prompt_layout.addWidget(self.deepseek_prompt)
        prompts_layout.addWidget(self.right_prompt_frame)
        
        layout.addLayout(prompts_layout)
        
        # Row 2: Buttons and status
        controls_layout = QHBoxLayout()
        
        # Example dropdown
        example_label = QLabel("📝 Examples:")
        example_label.setStyleSheet("color: #888; font-size: 12px;")
        controls_layout.addWidget(example_label)
        
        self.example_dropdown = QComboBox()
        self.example_dropdown.setFixedWidth(250)
        self.example_dropdown.setStyleSheet("""
            QComboBox { 
                background: #2a2a3a; color: white; border: 1px solid #3a3a4a; 
                border-radius: 4px; padding: 6px; font-size: 12px; 
            }
            QComboBox:hover { border-color: #6366f1; }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow { image: none; border: none; }
            QComboBox QAbstractItemView { 
                background: #2a2a3a; color: white; 
                selection-background-color: #6366f1; 
            }
        """)
        self.load_examples_from_file()
        self.example_dropdown.currentIndexChanged.connect(self.on_example_selected)
        controls_layout.addWidget(self.example_dropdown)
        
        controls_layout.addStretch()
        
        # Status
        self.status_label = QLabel("Ready - Enter prompts and click Start")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        controls_layout.addWidget(self.status_label)
        
        controls_layout.addStretch()
        
        # Start button
        self.start_btn = QPushButton("▶ Start Conversation")
        self.start_btn.setFixedSize(140, 32)
        self.start_btn.setStyleSheet("""
            QPushButton { background: #10a37f; color: white; border: none; border-radius: 4px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background: #0d8a6b; }
        """)
        self.start_btn.clicked.connect(self.on_start)
        controls_layout.addWidget(self.start_btn)
        
        # Stop button
        self.stop_btn = QPushButton("⬛ Stop")
        self.stop_btn.setFixedSize(80, 32)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton { background: #e53935; color: white; border: none; border-radius: 4px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background: #c62828; }
            QPushButton:disabled { background: #444; }
        """)
        self.stop_btn.clicked.connect(self.on_stop)
        controls_layout.addWidget(self.stop_btn)
        
        # Save PDF button
        self.save_pdf_btn = QPushButton("📄 Save PDF")
        self.save_pdf_btn.setFixedSize(100, 32)
        self.save_pdf_btn.setStyleSheet("""
            QPushButton { background: #6366f1; color: white; border: none; border-radius: 4px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background: #4f46e5; }
        """)
        self.save_pdf_btn.clicked.connect(self.save_pdf_clicked.emit)
        controls_layout.addWidget(self.save_pdf_btn)
        
        layout.addLayout(controls_layout)
        
    def load_examples_from_file(self):
        """Load examples from the JSON file"""
        self.examples = []
        self.example_dropdown.addItem("Select an example...", None)
        
        try:
            if os.path.exists(EXAMPLES_FILE):
                with open(EXAMPLES_FILE, 'r') as f:
                    data = json.load(f)
                    self.examples = data.get('examples', [])
                    
                for i, ex in enumerate(self.examples):
                    name = ex.get('name', f"Example {i+1}")
                    self.example_dropdown.addItem(name, i)
            else:
                self.example_dropdown.addItem("No examples found", None)
                print(f"Examples file not found at: {EXAMPLES_FILE}")
        except Exception as e:
            print(f"Error loading examples: {e}")
            self.example_dropdown.addItem("Error loading examples", None)
            
    def on_example_selected(self, index):
        """Handle dropdown selection"""
        if index <= 0:  # The "Select an example..." item
            return
            
        # Adjust for the placeholder item
        example_idx = index - 1
        if example_idx < len(self.examples):
            ex = self.examples[example_idx]
            self.chatgpt_prompt.setText(ex.get('chatgpt', ''))
            self.deepseek_prompt.setText(ex.get('deepseek', ''))
            self.status_label.setText(f"Loaded: {ex.get('name')}")
            
    def load_example(self):
        # Legacy method kept for compatibility if needed
        pass
    
    def on_llm_changed(self, panel_index):
        """Handle LLM selection change"""
        if panel_index == 0:
            llm_name = self.left_llm_dropdown.currentData()
            config = AVAILABLE_CHATBOTS[llm_name]
            self.left_prompt_label.setText(f"{config['icon']} {llm_name} Initial Prompt:")
            self.left_prompt_label.setStyleSheet(f"color: {config['color']}; font-size: 11px; font-weight: bold;")
        else:
            llm_name = self.right_llm_dropdown.currentData()
            config = AVAILABLE_CHATBOTS[llm_name]
            self.right_prompt_label.setText(f"{config['icon']} {llm_name} Initial Prompt:")
            self.right_prompt_label.setStyleSheet(f"color: {config['color']}; font-size: 11px; font-weight: bold;")
        
        self.selected_llms[panel_index] = llm_name
        self.llm_changed.emit(panel_index, llm_name)
        
    def get_selected_chatbots(self):
        """Return the current chatbot configurations"""
        return [
            AVAILABLE_CHATBOTS[self.selected_llms[0]],
            AVAILABLE_CHATBOTS[self.selected_llms[1]],
        ]
        
    def on_start(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("� Starting...")
        self.status_label.setStyleSheet("color: #10a37f; font-size: 12px;")
        
        prompts = (
            self.chatgpt_prompt.toPlainText(),
            self.deepseek_prompt.toPlainText()
        )
        self.start_clicked.emit(prompts)
        
    def on_stop(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Stopped")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        self.stop_clicked.emit()
        
    def update_status(self, message):
        self.status_label.setText(message)


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Brainstorm - LLM ↔ LLM")
        self.setMinimumSize(1300, 900)
        
        self.setup_persistent_profile()
        self.setup_ui()
        self.setup_bridge()
        
    def setup_persistent_profile(self):
        os.makedirs(STORAGE_DIR, exist_ok=True)
        self.profile = QWebEngineProfile("brainstorm_profile", self)
        self.profile.setPersistentStoragePath(STORAGE_DIR)
        self.profile.setCachePath(os.path.join(STORAGE_DIR, "cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Browser panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter { background: #0f0f14; }
            QSplitter::handle { background: #2a2a3a; width: 2px; }
        """)
        
        self.panels = []
        for config in CHATBOTS:
            panel = BrowserPanel(config, self.profile)
            self.panels.append(panel)
            splitter.addWidget(panel)
        
        splitter.setSizes([650, 650])
        main_layout.addWidget(splitter, stretch=1)
        
        # Control panel
        self.control_panel = ControlPanel()
        main_layout.addWidget(self.control_panel)
        
        self.statusBar().hide()
        self.setStyleSheet("QMainWindow { background: #0f0f14; }")
        
    def setup_bridge(self):
        self.bridge = ChatBridge()
        self.bridge.set_panels(self.panels)
        self.bridge.set_chatbot_getter(self.control_panel.get_selected_chatbots)
        self.bridge.status_update.connect(self.control_panel.update_status)
        self.bridge.message_received.connect(
            lambda idx, msg: self.control_panel.update_status(f"✓ {self.control_panel.get_selected_chatbots()[idx]['name']}: {msg[:50]}...")
        )
        
        self.control_panel.start_clicked.connect(self.bridge.start)
        self.control_panel.stop_clicked.connect(self.bridge.stop)
        self.control_panel.llm_changed.connect(self.on_llm_changed)
        self.control_panel.save_pdf_clicked.connect(self.save_conversations_to_pdf)
    
    def on_llm_changed(self, panel_index, llm_name):
        """Handle LLM selection change"""
        config = AVAILABLE_CHATBOTS[llm_name]
        self.panels[panel_index].set_chatbot(config)
    
    def save_conversations_to_pdf(self):
        """Save both conversations - extract text and save as HTML+PDF"""
        # Ask user for save directory
        save_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Directory to Save Conversations",
            os.path.expanduser("~"),
            QFileDialog.Option.ShowDirsOnly
        )
        
        if not save_dir:
            return
        
        self.save_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.save_dir = save_dir
        self.conversations_extracted = []
        self.chatbots_for_save = self.control_panel.get_selected_chatbots()
        
        self.control_panel.update_status("📄 Extracting conversations...")
        
        # Extract text from each panel
        for i, panel in enumerate(self.panels):
            self.extract_conversation_text(i, panel)
    
    def extract_conversation_text(self, panel_index, panel):
        """Extract conversation text using JavaScript"""
        config = self.chatbots_for_save[panel_index]
        
        # JavaScript to extract all messages (generic approach that works across sites)
        js_code = """
        (function() {
            let messages = [];
            
            // Try various selectors for different chat interfaces
            const messageSelectors = [
                // ChatGPT
                '[data-message-author-role]',
                // Claude
                '.font-claude-message, .font-user-message, [class*="message"]',
                // Gemini
                '.model-response-text, .user-query-text, message-content',
                // DeepSeek
                '.ds-markdown, .user-message, [class*="message"]',
                // Generic
                '[class*="chat"] [class*="message"]',
                '[role="article"]',
                '.message, .chat-message'
            ];
            
            let foundElements = [];
            for (const sel of messageSelectors) {
                try {
                    const elements = document.querySelectorAll(sel);
                    if (elements.length > 0) {
                        foundElements = Array.from(elements);
                        break;
                    }
                } catch(e) {}
            }
            
            // If no structured messages found, try to get main content
            if (foundElements.length === 0) {
                const mainContent = document.querySelector('main, [role="main"], .chat-container, #chat');
                if (mainContent) {
                    return JSON.stringify({
                        messages: [{role: 'content', text: mainContent.innerText}],
                        raw: mainContent.innerText.substring(0, 50000)
                    });
                }
            }
            
            // Extract text from found elements
            foundElements.forEach((el, idx) => {
                const text = (el.innerText || el.textContent || '').trim();
                if (text.length > 10) {
                    // Try to determine if user or assistant
                    let role = 'message';
                    const roleAttr = el.getAttribute('data-message-author-role');
                    if (roleAttr) {
                        role = roleAttr;
                    } else if (el.className.includes('user') || el.className.includes('human')) {
                        role = 'user';
                    } else if (el.className.includes('assistant') || el.className.includes('model') || el.className.includes('claude')) {
                        role = 'assistant';
                    }
                    messages.push({role: role, text: text.substring(0, 10000)});
                }
            });
            
            return JSON.stringify({messages: messages, count: messages.length});
        })();
        """
        
        panel.browser.page().runJavaScript(
            js_code,
            lambda result, idx=panel_index: self.on_conversation_extracted(idx, result)
        )
    
    def on_conversation_extracted(self, panel_index, result):
        """Handle extracted conversation text"""
        config = self.chatbots_for_save[panel_index]
        llm_name = config['name']
        
        try:
            data = json.loads(result) if result else {'messages': [], 'raw': ''}
            messages = data.get('messages', [])
            raw_text = data.get('raw', '')
            
            # Store the extracted data
            self.conversations_extracted.append({
                'index': panel_index,
                'name': llm_name,
                'color': config['color'],
                'messages': messages,
                'raw': raw_text
            })
            
            print(f"✓ Extracted {len(messages)} messages from {llm_name}")
            
        except Exception as e:
            print(f"✗ Error extracting from {llm_name}: {e}")
            self.conversations_extracted.append({
                'index': panel_index,
                'name': llm_name,
                'color': config['color'],
                'messages': [],
                'raw': ''
            })
        
        # When all panels are extracted, save the file
        if len(self.conversations_extracted) >= len(self.panels):
            self.save_combined_conversation()
    
    def save_combined_conversation(self):
        """Save all conversations to a combined HTML file"""
        # Sort by panel index
        self.conversations_extracted.sort(key=lambda x: x['index'])
        
        # Generate HTML
        html_content = self.generate_conversation_html()
        
        # Save HTML file
        html_filename = os.path.join(self.save_dir, f"conversation_{self.save_timestamp}.html")
        
        try:
            with open(html_filename, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"✓ Saved conversation to: {html_filename}")
            
            QMessageBox.information(
                self,
                "Conversation Saved",
                f"Conversation saved to:\n{html_filename}\n\nOpen in browser and print to PDF if needed."
            )
            self.control_panel.update_status("✓ Conversation saved as HTML")
            
        except Exception as e:
            print(f"✗ Error saving: {e}")
            QMessageBox.warning(self, "Error", f"Failed to save: {e}")
    
    def generate_conversation_html(self):
        """Generate a nicely formatted HTML document"""
        chatbots = self.chatbots_for_save
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>AI Brainstorm Conversation - {self.save_timestamp}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #1a1a2e;
            color: #eee;
        }}
        h1 {{
            text-align: center;
            color: #fff;
            border-bottom: 2px solid #333;
            padding-bottom: 20px;
        }}
        .panels {{
            display: flex;
            gap: 20px;
        }}
        .panel {{
            flex: 1;
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
        }}
        .panel-header {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid;
        }}
        .message {{
            margin: 10px 0;
            padding: 12px;
            border-radius: 8px;
            background: #1a1a2e;
        }}
        .message.user {{
            background: #2d4a7c;
            border-left: 3px solid #5b8dee;
        }}
        .message.assistant {{
            background: #1e3a3a;
            border-left: 3px solid #4ecdc4;
        }}
        .role {{
            font-size: 11px;
            text-transform: uppercase;
            color: #888;
            margin-bottom: 5px;
        }}
        .text {{
            white-space: pre-wrap;
            line-height: 1.5;
        }}
        .timestamp {{
            text-align: center;
            color: #666;
            margin-top: 30px;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <h1>🧠 AI Brainstorm Conversation</h1>
    <div class="panels">
"""
        
        for conv in self.conversations_extracted:
            html += f"""
        <div class="panel">
            <div class="panel-header" style="border-color: {conv['color']}; color: {conv['color']};">
                {conv['name']}
            </div>
"""
            
            if conv['messages']:
                for msg in conv['messages']:
                    role_class = msg['role'] if msg['role'] in ['user', 'assistant'] else 'message'
                    role_display = msg['role'].upper()
                    text = msg['text'].replace('<', '&lt;').replace('>', '&gt;')
                    html += f"""
            <div class="message {role_class}">
                <div class="role">{role_display}</div>
                <div class="text">{text}</div>
            </div>
"""
            elif conv['raw']:
                text = conv['raw'].replace('<', '&lt;').replace('>', '&gt;')
                html += f"""
            <div class="message">
                <div class="text">{text}</div>
            </div>
"""
            else:
                html += """
            <div class="message">
                <div class="text">(No conversation content extracted)</div>
            </div>
"""
            
            html += """
        </div>
"""
        
        html += f"""
    </div>
    <div class="timestamp">Saved: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
</body>
</html>
"""
        return html


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(15, 15, 20))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(22, 22, 29))
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
