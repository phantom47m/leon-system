"""
Leon GTK4 UI - Main application window
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw, Pango
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("leon.ui")


class LeonWindow(Gtk.ApplicationWindow):
    """Main GTK4 window for Leon"""

    def __init__(self, application, leon_core):
        super().__init__(application=application, title="Leon")
        self.leon = leon_core
        self.set_default_size(520, 750)
        self._build_ui()
        # Start periodic task panel refresh
        GLib.timeout_add_seconds(5, self._refresh_tasks)

    def _build_ui(self):
        # Main vertical layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # ---- Header ----
        header = Gtk.HeaderBar()
        title_label = Gtk.Label()
        title_label.set_markup("<b>🤖 Leon</b>")
        header.set_title_widget(title_label)

        # Status indicator
        self.status_label = Gtk.Label(label="● Online")
        self.status_label.add_css_class("success")
        header.pack_end(self.status_label)

        main_box.append(header)

        # ---- Chat area (scrollable) ----
        chat_scroll = Gtk.ScrolledWindow()
        chat_scroll.set_vexpand(True)
        chat_scroll.set_hexpand(True)
        chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.chat_box.set_margin_start(12)
        self.chat_box.set_margin_end(12)
        self.chat_box.set_margin_top(12)
        self.chat_box.set_margin_bottom(12)
        chat_scroll.set_child(self.chat_box)

        main_box.append(chat_scroll)
        self._chat_scroll = chat_scroll

        # ---- Task panel ----
        task_frame = Gtk.Frame()
        task_frame.set_margin_start(12)
        task_frame.set_margin_end(12)
        task_frame.set_margin_bottom(4)

        task_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        task_header.set_margin_start(8)
        task_header.set_margin_top(6)
        task_title = Gtk.Label()
        task_title.set_markup("<b>🔧 Active Tasks</b>")
        task_title.set_xalign(0)
        task_header.append(task_title)

        self.task_count_label = Gtk.Label(label="(0)")
        task_header.append(self.task_count_label)

        task_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        task_inner.append(task_header)

        self.task_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.task_list_box.set_margin_start(8)
        self.task_list_box.set_margin_end(8)
        self.task_list_box.set_margin_bottom(8)
        task_inner.append(self.task_list_box)

        task_frame.set_child(task_inner)
        main_box.append(task_frame)

        # ---- Input area ----
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_box.set_margin_start(12)
        input_box.set_margin_end(12)
        input_box.set_margin_bottom(12)
        input_box.set_margin_top(8)

        self.input_entry = Gtk.Entry()
        self.input_entry.set_placeholder_text("Message Leon...")
        self.input_entry.set_hexpand(True)
        self.input_entry.connect("activate", self._on_send)

        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._on_send)

        input_box.append(self.input_entry)
        input_box.append(send_btn)
        main_box.append(input_box)

        # Welcome message
        self._add_message("Leon", "Hey! I'm Leon, your AI orchestrator. What are we building today?")

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def _on_send(self, _widget):
        text = self.input_entry.get_text().strip()
        if not text:
            return
        self.input_entry.set_text("")
        self._add_message("You", text)

        # Show typing indicator
        typing = Gtk.Label(label="Leon is thinking...")
        typing.set_xalign(0)
        typing.add_css_class("dim-label")
        self.chat_box.append(typing)

        # Process in background thread to avoid blocking UI
        import threading

        def run():
            loop = asyncio.new_event_loop()
            response = loop.run_until_complete(self.leon.process_user_input(text))
            loop.close()
            GLib.idle_add(self._show_response, response, typing)

        threading.Thread(target=run, daemon=True).start()

    def _show_response(self, response: str, typing_widget):
        self.chat_box.remove(typing_widget)
        self._add_message("Leon", response)
        self._refresh_tasks()

    def _add_message(self, sender: str, message: str):
        msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        msg_box.set_margin_bottom(4)

        sender_label = Gtk.Label()
        sender_label.set_markup(f"<b>{sender}</b>")
        sender_label.set_xalign(0)

        body = Gtk.Label(label=message)
        body.set_xalign(0)
        body.set_wrap(True)
        body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_selectable(True)
        body.set_max_width_chars(60)

        msg_box.append(sender_label)
        msg_box.append(body)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)

        self.chat_box.append(msg_box)
        self.chat_box.append(sep)

        # Scroll to bottom
        GLib.idle_add(self._scroll_bottom)

    def _scroll_bottom(self):
        adj = self._chat_scroll.get_vadjustment()
        adj.set_value(adj.get_upper())

    # ------------------------------------------------------------------
    # Task panel
    # ------------------------------------------------------------------

    def _refresh_tasks(self):
        # Clear
        while True:
            child = self.task_list_box.get_first_child()
            if child is None:
                break
            self.task_list_box.remove(child)

        status = self.leon.get_status()
        tasks = status["tasks"]
        active = tasks.get("active_tasks", [])
        self.task_count_label.set_text(f"({len(active)})")

        if not active:
            lbl = Gtk.Label(label="No active tasks")
            lbl.add_css_class("dim-label")
            lbl.set_xalign(0)
            self.task_list_box.append(lbl)
        else:
            for t in active:
                desc = t.get("description", "Unknown")[:55]
                proj = t.get("project", "")
                row = Gtk.Label()
                row.set_markup(f"• {desc}  <small>({proj})</small>")
                row.set_xalign(0)
                row.set_wrap(True)
                self.task_list_box.append(row)

        return True  # Keep the timeout alive
