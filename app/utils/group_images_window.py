"""
Groups similar images into folders based on base filenames.

Usage:
- Launches as a separate window in the GUI
- User selects a folder
- Script detects image files with common base names (e.g., "image1", "image2")
- Groups images into folders named after the base name
- Single and multiple files are both grouped
"""

import os
import shutil
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

SUPPORTED_EXTS = [".jpg", ".jpeg", ".png", ".webp"]


class GroupImagesWindow(tk.Toplevel):
    """
    A popup window that allows users to group similar image files into folders.

    Grouping is based on filenames that differ only by trailing digits.
    """

    def __init__(self, parent, log=None):
        super().__init__(parent)
        self.title("Group Images by Name")
        self.geometry("600x160")
        self.resizable(False, False)
        self.log = log  # Optional log function

        self.folder_path = tk.StringVar()
        self.build_interface()

    def build_interface(self):
        """Create and layout all widgets inside the window."""
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Select Folder Containing Images:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.folder_path, width=50).grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(0, 8)
        )
        ttk.Button(frame, text="Browse", command=self.browse_folder).grid(row=1, column=2, padx=(8, 0))

        ttk.Button(frame, text="Group Images", command=self.group_images).grid(
            row=2, column=0, pady=(10, 0), sticky="w"
        )

    def browse_folder(self):
        """Open a file dialog to select the base image folder."""
        path = filedialog.askdirectory()
        if path:
            self.folder_path.set(path)

    def group_images(self):
        """
        Group image files with similar base names into their own folders.
        Files must have a trailing number to be grouped (e.g., "image1", "image2").
        Now includes single files as well.
        """
        folder_path = self.folder_path.get().strip()

        if not os.path.isdir(folder_path):
            messagebox.showerror("Error", "Please select a valid folder.")
            return

        image_groups = {}

        # --- Detect all supported image files and group by base name ---
        for filename in os.listdir(folder_path):
            if not any(filename.lower().endswith(ext) for ext in SUPPORTED_EXTS):
                continue

            filename_noext = os.path.splitext(filename)[0]
            base_name = re.sub(r'\d+$', '', filename_noext)

            if base_name in image_groups:
                image_groups[base_name].append(filename)
            else:
                image_groups[base_name] = [filename]

        moved_count = 0

        # --- Move grouped images into their own folders (even single ones) ---
        for group_name, files in image_groups.items():
            group_folder = os.path.join(folder_path, group_name)

            if not os.path.exists(group_folder):
                os.makedirs(group_folder, exist_ok=True)
                if self.log:
                    self.log(f"Created folder: {group_folder}", level="INFO")

            for file in files:
                src = os.path.join(folder_path, file)
                dst = os.path.join(group_folder, file)

                try:
                    shutil.move(src, dst)
                    if self.log:
                        self.log(f"{file} → {group_name}/", level="INFO")
                    moved_count += 1
                except Exception as e:
                    if self.log:
                        self.log(f"⚠️ Failed to move {file}: {e}", level="WARN")

        # --- Final summary ---
        if self.log:
            self.log(f"✅ Grouped {moved_count} images into folders.", level="INFO")

        messagebox.showinfo("Complete", f"Grouped {moved_count} images.")