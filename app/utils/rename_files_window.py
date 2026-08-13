import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

SUPPORTED_EXTS = [".jpg", ".jpeg", ".png", ".webp"]

class RenameFilesWindow(tk.Toplevel):
    def __init__(self, parent, log=None):
        super().__init__(parent)
        self.title("Batch Rename Files")
        self.geometry("600x200")
        self.resizable(False, False)
        self.log = log

        self.folder_path = tk.StringVar()
        self.prefix = tk.StringVar()

        self.build_interface()

    def build_interface(self):
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Select Base Folder:").grid(row=0, column=0, sticky="w")
        path_entry = ttk.Entry(frame, textvariable=self.folder_path, width=50)
        path_entry.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 8))
        ttk.Button(frame, text="Browse", command=self.browse_folder).grid(row=1, column=2, padx=(8, 0))

        ttk.Label(frame, text="Prefix for renamed files:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.prefix, width=30).grid(row=3, column=0, columnspan=2, sticky="we", pady=(0, 8))

        ttk.Button(frame, text="Start Renaming", command=self.rename_files).grid(row=4, column=0, pady=(10, 0), sticky="w")

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_path.set(path)

    def rename_files(self):
        root_folder = self.folder_path.get().strip()
        prefix = self.prefix.get().strip().replace(" ", "_")

        if not os.path.isdir(root_folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return
        if not prefix:
            messagebox.showerror("Error", "Please enter a prefix.")
            return

        renamed_count = 0

        for dirpath, _, filenames in os.walk(root_folder):
            if dirpath == root_folder:
                continue  # Skip root-level files

            rel_path = os.path.relpath(dirpath, root_folder)
            parts = rel_path.split(os.sep)

            # Determine the subfolder name part
            if len(parts) >= 2:
                subfolder_part = f"{parts[-2]}_{parts[-1]}"
            else:
                subfolder_part = parts[-1]

            # Special case: if last folder is literally named "Parent", use root folder name instead
            if parts[-1].lower() == "parent":
                subfolder_part = os.path.basename(root_folder)

            subfolder_part = subfolder_part.replace(" ", "_")

            count = 1
            for filename in sorted(filenames):
                if filename.startswith("._"):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext not in SUPPORTED_EXTS:
                    continue

                new_name = f"{prefix}_{subfolder_part}_{count:02d}{ext}"
                src = os.path.join(dirpath, filename)
                dst = os.path.join(dirpath, new_name.lower())

                try:
                    os.rename(src, dst)
                    if self.log:
                        self.log(f"{filename} → {new_name.lower()}", level="INFO")
                    count += 1
                    renamed_count += 1
                except Exception as e:
                    if self.log:
                        self.log(f"⚠️ Failed to rename {filename}: {e}", level="WARN")

        messagebox.showinfo("Complete", f"Renamed {renamed_count} files.")