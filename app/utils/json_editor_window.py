import os
import platform
import json
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox, simpledialog
from itertools import product
from json_utils import load_json, save_json

class JsonEditorWindow(tk.Toplevel):
    def __init__(self, parent, log=None):
        super().__init__(parent)
        self.title("JSON Editor")
        self.geometry("1000x800")
        self.resizable(True, True)
        self.log = log

        self.current_json_path = tk.StringVar()
        self.fields = {}

        self.build_editor()

    def build_editor(self):
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        def bind_mousewheel_to(widget, target_canvas):
            def _on_mousewheel(event):
                delta = int(-1 * (event.delta / 120 if platform.system() != 'Darwin' else event.delta))
                target_canvas.yview_scroll(delta, "units")
                return "break"
            widget.bind("<Enter>", lambda e: target_canvas.bind_all("<MouseWheel>", _on_mousewheel))
            widget.bind("<Leave>", lambda e: target_canvas.unbind_all("<MouseWheel>"))

        def load_file():
            path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
            if not path:
                return
            try:
                data = load_json(path)
                self.current_json_path.set(path)
                populate_fields(data)
                loaded_path_label.config(text=f"Loaded: {path}")
                if self.log: self.log(f"✅ Loaded: {os.path.basename(path)}", level="INFO")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load JSON:\n{e}")
                if self.log: self.log(str(e), level="ERROR")

        def save_file():
            if not self.current_json_path.get():
                messagebox.showerror("Error", "No JSON file loaded.")
                return
            try:
                data = prune_empty_fields(collect_fields())
                save_json(self.current_json_path.get(), data)
                if self.log: self.log(f"✅ Saved: {os.path.basename(self.current_json_path.get())}", level="INFO")
                messagebox.showinfo("Saved", "JSON saved successfully.")
                clear_form()
                loaded_path_label.config(text="No file loaded")
                self.current_json_path.set("")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save JSON:\n{e}")
                if self.log: self.log(str(e), level="ERROR")

        def clear_form():
            collection_type_var.set("")
            for key in self.fields:
                self.fields[key].delete(0, tk.END)
            short_desc.delete("1.0", tk.END)
            description.delete("1.0", tk.END)
            attr_text.delete("1.0", tk.END)
            image_attr_text.delete("1.0", tk.END)
            modifier_box.delete("1.0", tk.END)
            var_count_label.config(text="Variations: 0")

        def clear_form_and_reset():
            self.current_json_path.set("")
            loaded_path_label.config(text="No file loaded")
            clear_form()
            if self.log: self.log("Cleared form and reset file path", level="INFO")

        def add_entry(label, key):
            ttk.Label(form_frame, text=label).pack(anchor="w", pady=(6, 0))
            entry = ttk.Entry(form_frame)
            entry.pack(fill="x")
            self.fields[key] = entry

        def add_dimensions():
            ttk.Label(form_frame, text="Dimensions (mm)").pack(anchor="w", pady=(6, 0))
            dim_frame = ttk.Frame(form_frame)
            dim_frame.pack(fill="x")
            for dim in ["length", "width", "height"]:
                ttk.Label(dim_frame, text=dim.title()).pack(side="left", padx=(0, 4))
                entry = ttk.Entry(dim_frame, width=10)
                entry.pack(side="left", padx=(0, 10))
                self.fields[dim] = entry

        def wrap_selection(widget, before, after):
            try:
                start = widget.index("sel.first")
                end = widget.index("sel.last")
                text = widget.get(start, end)
                widget.delete(start, end)
                widget.insert(start, f"{before}{text}{after}")
            except tk.TclError:
                pass

        def insert_accordion():
            title = simpledialog.askstring("Accordion Title", "Enter the title for the accordion:")
            if not title:
                return
            text = f"[cg_accordion title='{title}']Your content here[/cg_accordion]"
            short_desc.insert("insert", text)

        def update_variation_count():
            try:
                values = json.loads(attr_text.get("1.0", "end").strip()).values()
                count = len(list(product(*values))) if values else 0
                var_count_label.config(text=f"Variations: {count}")
            except Exception:
                var_count_label.config(text="Invalid attributes JSON")

        def insert_sample_attributes():
            sample = { "Size": ["A4", "A3"] }
            attr_text.delete("1.0", tk.END)
            attr_text.insert("1.0", json.dumps(sample, indent=2))

        def insert_sample_image_attributes():
            sample = ["Style", "Size"]
            image_attr_text.delete("1.0", tk.END)
            image_attr_text.insert("1.0", json.dumps(sample, indent=2))

        def insert_sample_modifiers():
            sample = {
                "Size=A3": { "price": "12.99", "weight": "200" },
                "Size=A3|Style=Hero C": { "price": "12.99", "weight": "300" }
            }
            modifier_box.delete("1.0", tk.END)
            modifier_box.insert("1.0", json.dumps(sample, indent=2))

        def prune_empty_fields(data):
            if isinstance(data, dict):
                return {k: prune_empty_fields(v) for k, v in data.items()
                        if v not in ("", [], {}) and prune_empty_fields(v) != {}}
            elif isinstance(data, list):
                return [prune_empty_fields(i) for i in data if prune_empty_fields(i) not in ("", [], {}, None)]
            return data
        
        def create_new_json():
            folder = filedialog.askdirectory(title="Select Folder to Create JSON")
            if not folder:
                return

            json_path = os.path.join(folder, "product_info.json")
            if os.path.exists(json_path):
                messagebox.showwarning(
                    "File Already Exists",
                    "A product_info.json already exists in this folder.\n\nPlease load and update it instead."
                )
                return

            try:
                save_json(json_path, {})
                self.current_json_path.set(json_path)
                if self.log:
                    self.log(f"🆕 Created new: {os.path.basename(json_path)}", level="INFO")
                loaded_path_label.config(text=f"Created: {json_path}")
                messagebox.showinfo("New JSON Created", "Blank product_info.json created and loaded.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to create JSON:\n{e}")
                if self.log:
                    self.log(str(e), level="ERROR")

        def populate_fields(data):
            collection_type_var.set(data.get("collection_type", ""))
            for key in ["title", "sku_prefix", "price", "sale_price", "sale_start_date", "sale_end_date", "weight", "shipping_class"]:
                self.fields[key].delete(0, tk.END)
                self.fields[key].insert(0, str(data.get(key, "")))
            for key in ["categories", "tags"]:
                self.fields[key].delete(0, tk.END)
                self.fields[key].insert(0, ",".join(data.get(key, [])))
            dims = data.get("dimensions", {})
            for dim in ["length", "width", "height"]:
                self.fields[dim].delete(0, tk.END)
                self.fields[dim].insert(0, str(dims.get(dim, "")))
            self.fields["grouped_ids"].delete(0, tk.END)
            self.fields["grouped_ids"].insert(0, ",".join(data.get("grouped_ids", [])))
            for key in ["upsell_ids", "cross_sell_ids"]:
                self.fields[key].delete(0, tk.END)
                self.fields[key].insert(0, ",".join(data.get(key, [])))
            short_desc.delete("1.0", tk.END)
            short_desc.insert("1.0", data.get("short_description", ""))
            description.delete("1.0", tk.END)
            description.insert("1.0", data.get("description", ""))
            attr_text.delete("1.0", tk.END)
            attr_text.insert("1.0", json.dumps(data.get("attributes", {}), indent=2))
            update_variation_count()
            image_attr_text.delete("1.0", tk.END)
            image_attr_text.insert("1.0", json.dumps(data.get("image_attributes", {}), indent=2))
            modifier_box.delete("1.0", tk.END)
            modifier_box.insert("1.0", json.dumps(data.get("variation_modifiers", {}), indent=2))
            self.fields["seo_title"].delete(0, tk.END)
            self.fields["seo_title"].insert(0, str(data.get("meta_title", "")))
            self.fields["seo_description"].delete(0, tk.END)
            self.fields["seo_description"].insert(0, str(data.get("meta_description", "")))

        def collect_fields():
            try:
                modifiers = json.loads(modifier_box.get("1.0", "end").strip() or "{}")
            except json.JSONDecodeError as e:
                if self.log: self.log(f"⚠️ Failed to parse variation_modifiers: {str(e)}", level="WARN")
                modifiers = {}

            return {
                "collection_type": collection_type_var.get(),
                "title": self.fields["title"].get(),
                "sku_prefix": self.fields["sku_prefix"].get(),
                "price": self.fields["price"].get(),
                "sale_price": self.fields["sale_price"].get(),
                "sale_start_date": self.fields["sale_start_date"].get(),
                "sale_end_date": self.fields["sale_end_date"].get(),
                "weight": self.fields["weight"].get(),
                "dimensions": {
                    "length": self.fields["length"].get(),
                    "width": self.fields["width"].get(),
                    "height": self.fields["height"].get()
                },
                "categories": [i.strip() for i in self.fields["categories"].get().split(",") if i.strip()],
                "tags": [i.strip() for i in self.fields["tags"].get().split(",") if i.strip()],
                "shipping_class": self.fields["shipping_class"].get(),
                "grouped_ids": [i.strip() for i in self.fields["grouped_ids"].get().split(",") if i.strip()],
                "upsell_ids": [i.strip() for i in self.fields["upsell_ids"].get().split(",") if i.strip()],
                "cross_sell_ids": [i.strip() for i in self.fields["cross_sell_ids"].get().split(",") if i.strip()],
                "short_description": short_desc.get("1.0", "end").strip(),
                "description": description.get("1.0", "end").strip(),
                "attributes": json.loads(attr_text.get("1.0", "end").strip() or "{}"),
                "image_attributes": json.loads(image_attr_text.get("1.0", "end").strip() or "{}"),
                "variation_modifiers": modifiers,
                "meta_title": self.fields["seo_title"].get(),
                "meta_description": self.fields["seo_description"].get(),
            }

        # === Layout ===
        control_frame = ttk.Frame(frame)
        control_frame.pack(fill="x", padx=10, pady=10)
        ttk.Button(control_frame, text="Create New", command=lambda: create_new_json()).pack(side="left", padx=(0, 10))
        ttk.Button(control_frame, text="Load JSON File", command=load_file).pack(side="left", padx=(0, 10))
        ttk.Button(control_frame, text="Save Changes", command=save_file).pack(side="left", padx=(0, 10))
        ttk.Button(control_frame, text="Clear Form", command=clear_form_and_reset).pack(side="left")

        # === Scrollable Area ===
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        bind_mousewheel_to(canvas, canvas)

        # === Form Frame ===
        scrollable_frame.config(padding=20)
        form_frame = scrollable_frame

        # Top JSON status
        loaded_path_label = ttk.Label(form_frame, text="No file loaded", foreground="gray", font=("Segoe UI", 10, "italic"))
        loaded_path_label.pack(anchor="w", pady=(0, 4))

        collection_type_var = tk.StringVar()
        ttk.Label(form_frame, text="Collection Type").pack(anchor="w", pady=(6, 0))
        ttk.Combobox(form_frame, textvariable=collection_type_var, values=["", "Simple", "Variable Collection", "Single Variable"], state="readonly").pack(fill="x")

        # Title block
        ttk.Label(form_frame, text="Title").pack(anchor="w", pady=(6, 0))
        ttk.Label(form_frame, text="Fallback logic: • Product JSON + Shared JSON → 'Product - Shared' • Shared JSON only → 'Folder - Shared' • If none → 'Folder Name'", font=("Segoe UI", 10), foreground="gray").pack(anchor="w", padx=10, pady=(0, 6))
        title_entry = ttk.Entry(form_frame)
        title_entry.pack(fill="x")
        self.fields["title"] = title_entry

        # Add all entry fields
        for label, key in [("SKU Prefix", "sku_prefix"), ("Price", "price"), ("Sale Price", "sale_price"),
                           ("Sale Start Date (YYYY-MM-DD)", "sale_start_date"), ("Sale End Date (YYYY-MM-DD)", "sale_end_date"),
                           ("Weight (g)", "weight")]:
            add_entry(label, key)

        add_dimensions()
        add_entry("Categories (comma-separated)", "categories")
        add_entry("Tags (comma-separated)", "tags")
        add_entry("Shipping Class", "shipping_class")
        add_entry("Grouped Product IDs (comma-separated)", "grouped_ids")
        add_entry("Upsell IDs (comma-separated)", "upsell_ids")
        add_entry("Cross-sell IDs (comma-separated)", "cross_sell_ids")

        ttk.Label(form_frame, text="Short Description").pack(anchor="w", pady=(10, 0))
        short_desc = scrolledtext.ScrolledText(form_frame, height=5)
        short_desc.pack(fill="x")

        ttk.Label(form_frame, text="Description").pack(anchor="w", pady=(10, 0))
        description = scrolledtext.ScrolledText(form_frame, height=8)
        description.pack(fill="x")

        toolbar = ttk.Frame(form_frame)
        toolbar.pack(anchor="w", pady=5)
        ttk.Button(toolbar, text="Bold", command=lambda: wrap_selection(description, "<strong>", "</strong>")).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Italic", command=lambda: wrap_selection(description, "<em>", "</em>")).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Bullet List", command=lambda: wrap_selection(description, "<ul>\n<li>", "</li>\n</ul>")).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Bold (Short)", command=lambda: wrap_selection(short_desc, "<strong>", "</strong>")).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Italic (Short)", command=lambda: wrap_selection(short_desc, "<em>", "</em>")).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Insert Accordion", command=insert_accordion).pack(side="left", padx=2)

        ttk.Label(form_frame, text="Attributes (JSON format)").pack(anchor="w", pady=(10, 0))
        ttk.Label(form_frame, text="If using Image Attributes, make sure attribute titles match folder names exactly.", font=("Segoe UI", 10), foreground="gray").pack(anchor="w", padx=10, pady=(0, 6))
        attr_text = scrolledtext.ScrolledText(form_frame, height=6)
        attr_text.pack(fill="x")
        ttk.Button(form_frame, text="Insert Sample Attributes", command=insert_sample_attributes).pack(anchor="w", pady=(0, 8))

        var_count_label = ttk.Label(form_frame, text="Variations: 0", font=("Segoe UI", 9, "italic"))
        var_count_label.pack(anchor="w", pady=(4, 0))
        ttk.Button(form_frame, text="Preview Variation Count", command=update_variation_count).pack(anchor="w", pady=(0, 10))

        ttk.Label(form_frame, text="Image Attributes (JSON format)").pack(anchor="w", pady=(10, 0))
        ttk.Label(form_frame, text="Use attribute titles. Must match folder names exactly (case-sensitive).", font=("Segoe UI", 10), foreground="gray").pack(anchor="w", padx=10, pady=(0, 6))
        image_attr_text = scrolledtext.ScrolledText(form_frame, height=6)
        image_attr_text.pack(fill="x")
        ttk.Button(form_frame, text="Insert Sample Image Attributes", command=insert_sample_image_attributes).pack(anchor="w", pady=(0, 8))

        ttk.Label(form_frame, text="Variation Modifiers (JSON format)").pack(anchor="w", pady=(10, 0))
        modifier_box = scrolledtext.ScrolledText(form_frame, height=6)
        modifier_box.pack(fill="x")
        ttk.Button(form_frame, text="Insert Sample Modifiers", command=insert_sample_modifiers).pack(anchor="w", pady=(0, 10))

        ttk.Label(form_frame, text="SEO Meta Title").pack(anchor="w", pady=(10, 0))
        seo_title_entry = ttk.Entry(form_frame)
        seo_title_entry.pack(fill="x")
        self.fields["seo_title"] = seo_title_entry

        ttk.Label(form_frame, text="SEO Meta Description").pack(anchor="w", pady=(10, 0))
        seo_description_entry = ttk.Entry(form_frame)
        seo_description_entry.pack(fill="x")
        self.fields["seo_description"] = seo_description_entry