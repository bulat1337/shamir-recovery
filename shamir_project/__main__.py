import shutil
import tkinter as tk
from pathlib import Path
from tempfile import mkdtemp
from tkinter import filedialog, messagebox, ttk

from .core import (
    MAX_FILE, SHARE_COUNT, STATUS, Container, FormatError,
    encrypt, load_inputs, prepare, read_file, recover, write_file,
)
from .demo import DEMO_TEXT, scenarios

BG = "#f4f6f8"
PANEL = "#ffffff"
INK = "#182532"
MUTED = "#425366"
ACCENT = "#1e594e"
BORDER = "#d4dce4"
FONT = "Inter"
FILTER_SHARES = [("Файлы долей", "*.json"), ("Все файлы", "*")]
FILTER_CONTAINER = [("Контейнер Шамира", "*.sssf"), ("Все файлы", "*")]


def title(parent, text):
    return ttk.Label(parent, text=text, style="Title.TLabel")


def choose_save(data, suggested, protected=()):
    if data is None:
        return
    target = filedialog.asksaveasfilename(initialfile=suggested)
    if not target:
        return
    path = Path(target)
    try:
        if any(path.resolve() == source.resolve() or (path.exists() and source.exists() and path.samefile(source))
               for source in protected):
            raise FormatError("выберите другое имя, чтобы сохранить входные файлы")
        write_file(path, data)
    except (OSError, FormatError) as exc:
        messagebox.showerror("Сохранение файла", str(exc))
        return
    messagebox.showinfo("Файл сохранён", path.name)


class Comparison(ttk.Frame):
    def __init__(self, parent, suggested, protected=lambda: ()):
        super().__init__(parent)
        self.plaintext = None
        self.suggested = suggested
        self.protected = protected
        self.columnconfigure((0, 1), weight=1, uniform="results")
        self.rowconfigure(2, weight=1, minsize=104)

        self.statuses = []
        for column, name in enumerate(("Первые три файла", "Перебор троек")):
            card = ttk.Frame(self, padding=(14, 10), style="Card.TFrame")
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
            ttk.Label(card, text=name, style="CardTitle.TLabel").pack(anchor="w")
            value = ttk.Label(card, text="Ожидает проверки", style="Card.TLabel", wraplength=355)
            value.pack(anchor="w", pady=(4, 0))
            self.statuses.append(value)

        ttk.Label(self, text="Входные файлы и диагностика", style="Section.TLabel").grid(
            row=1, column=0, sticky="w", pady=(14, 6)
        )
        area = ttk.Frame(self)
        area.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.log = tk.Text(
            area, height=6, wrap="word", state="disabled", relief="flat", bg=PANEL, fg=INK,
            font=(FONT, 11), padx=10, pady=8, highlightbackground=BORDER, highlightthickness=1,
        )
        scroll = ttk.Scrollbar(area, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.save = ttk.Button(
            self, text="Сохранить восстановленный файл…", style="Primary.TButton",
            command=lambda: choose_save(self.plaintext, self.suggested, self.protected()),
        )
        self.save.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.save.state(["disabled"])

    def write_log(self, lines):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(lines))
        self.log.configure(state="disabled")

    def reset(self, lines=()):
        self.plaintext = None
        self.save.state(["disabled"])
        for value in self.statuses:
            value.configure(text="Ожидает проверки", style="Card.TLabel")
        self.write_log(lines)

    def reject_container(self, error):
        self.reset([f"Ошибка контейнера: {error}"])
        for value in self.statuses:
            value.configure(text="Контейнер не прочитан", style="Failed.TLabel")

    def compare(self, inputs, original=None):
        lines = []
        for entry in inputs.entries:
            index = f"x={entry.share.index}" if entry.share is not None else "ошибка формата"
            notes = f" · {', '.join(entry.notes)}" if entry.notes else ""
            lines.append(f"№{entry.position}  {entry.label}  ·  {index}{notes}")
        indexes = len({entry.share.index for entry in inputs.candidates})
        lines.append(f"\nКандидатов после удаления повторов: {len(inputs.candidates)}. Разных индексов: {indexes}.")
        for number, label in enumerate(self.statuses):
            result = recover(inputs, search=bool(number))
            detail = f"{STATUS[result.status]}\nПроверено троек: {len(result.attempts)}"
            if result.valid:
                detail += " · файлы " + ", ".join(f"№{pos}" for pos in result.selected)
            label.configure(text=detail, style="Good.TLabel" if result.valid else "Failed.TLabel")
            if number:
                self.plaintext = result.plaintext
                self.save.state(["!disabled"] if result.valid else ["disabled"])
                if result.valid and original is not None:
                    if result.plaintext == original:
                        lines.append(f"Результат совпал с исходником побайтно ({len(original)} байт).")
                    else:
                        lines.append("Результат отличается от исходника.")
        self.write_log(lines)


class EncryptPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(24, 20))
        self.source = None
        self.output = None
        title(self, "Зашифровать файл").pack(anchor="w")
        ttk.Label(self, text="На выходе получится контейнер и пять отдельных файлов с долями ключа.").pack(
            anchor="w", pady=(5, 16)
        )
        for attr, label, button, choose in (
            ("source", "Исходный файл до 32 МиБ", "Выбрать файл…", filedialog.askopenfilename),
            ("output", "Куда сохранить набор", "Выбрать папку…", filedialog.askdirectory),
        ):
            row = ttk.Frame(self, padding=(14, 12), style="Card.TFrame")
            row.pack(fill="x", pady=(0, 9))
            caption = ttk.Label(row, text="Файл или папка не выбраны", style="Card.TLabel", wraplength=578)
            ttk.Label(row, text=label, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            caption.grid(row=1, column=0, sticky="w", pady=(4, 0))
            ttk.Button(row, text=button, command=lambda a=attr, c=caption, f=choose: self.pick(a, c, f)).grid(
                row=0, column=1, rowspan=2, padx=(14, 0), sticky="e"
            )
            row.columnconfigure(0, weight=1)
        self.action = ttk.Button(self, text="Зашифровать и разделить ключ", style="Primary.TButton", command=self.run)
        self.action.pack(anchor="w", pady=(8, 12))
        self.action.state(["disabled"])
        self.message = ttk.Label(self, text="", wraplength=800)
        self.message.pack(anchor="w")

    def pick(self, attr, caption, dialog):
        name = dialog()
        if name:
            setattr(self, attr, Path(name))
            caption.configure(text=name)
            self.message.configure(text="")
            if self.source and self.output:
                self.action.state(["!disabled"])

    def run(self):
        destination = None
        try:
            data = read_file(self.source, MAX_FILE)
            container, shares = encrypt(data)
            destination = Path(mkdtemp(prefix="shamir-", dir=self.output))
            write_file(destination / "file.sssf", container)
            for share in shares:
                write_file(destination / f"share-{share.index}.json", share.encode())
        except (OSError, FormatError) as exc:
            if destination is not None:
                shutil.rmtree(destination, ignore_errors=True)
            messagebox.showerror("Шифрование файла", str(exc))
            return
        self.message.configure(
            text=f"Набор сохранён в {destination}\nКонтейнер file.sssf и пять файлов share-1.json … share-5.json.\n"
                 "Доли можно разнести по отдельным хранилищам."
        )


class RecoveryPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(24, 20))
        self.container = None
        self.paths = []
        title(self, "Восстановить файл").pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(6, 9))
        ttk.Button(row, text="Выбрать контейнер…", command=self.pick_container).pack(side="left")
        self.filename = ttk.Label(row, text="Контейнер не выбран")
        self.filename.pack(side="left", padx=10)
        ttk.Label(self, text="До пяти файлов долей. Порядок в списке задаёт первые три файла для сравнения.").pack(
            anchor="w", pady=(0, 6)
        )
        files = ttk.Frame(self)
        files.pack(fill="x")
        self.listbox = tk.Listbox(
            files, height=5, bg=PANEL, fg=INK, font=(FONT, 11), relief="flat",
            highlightbackground=BORDER, highlightthickness=1, exportselection=False,
        )
        self.listbox.pack(side="left", fill="x", expand=True)
        controls = ttk.Frame(files)
        controls.pack(side="right", padx=(8, 0), fill="y")
        for label, action in (
            ("Добавить…", self.add), ("Убрать", self.remove),
            ("Выше", lambda: self.move(-1)), ("Ниже", lambda: self.move(1)),
        ):
            ttk.Button(controls, text=label, command=action).pack(fill="x")
        self.action = ttk.Button(self, text="Сравнить способы", style="Primary.TButton", command=self.run)
        self.action.pack(anchor="w", pady=9)
        self.action.state(["disabled"])
        self.results = Comparison(self, "восстановленный-файл.bin", lambda: (self.container, *self.paths))
        self.results.pack(fill="both", expand=True)

    def pick_container(self):
        name = filedialog.askopenfilename(filetypes=FILTER_CONTAINER)
        if name:
            self.container = Path(name)
            self.filename.configure(text=self.container.name)
            self.refresh()

    def add(self):
        names = filedialog.askopenfilenames(filetypes=FILTER_SHARES)
        if not names:
            return
        if len(names) + len(self.paths) > SHARE_COUNT:
            messagebox.showinfo("Файлы долей", "В списке может быть не больше пяти файлов.")
            return
        self.paths.extend(map(Path, names))
        self.refresh()

    def remove(self):
        if selection := self.listbox.curselection():
            self.paths.pop(selection[0])
            self.refresh()

    def move(self, delta):
        if selection := self.listbox.curselection():
            old = selection[0]
            new = old + delta
            if 0 <= new < len(self.paths):
                self.paths[old], self.paths[new] = self.paths[new], self.paths[old]
                self.refresh()
                self.listbox.selection_set(new)

    def refresh(self):
        self.listbox.delete(0, "end")
        for index, path in enumerate(self.paths, start=1):
            self.listbox.insert("end", f"  {index}.  {path.name}   ({path.parent})")
        self.action.state(["!disabled"] if self.container and self.paths else ["disabled"])
        self.results.reset()

    def run(self):
        try:
            inputs = load_inputs(self.container, self.paths)
        except (OSError, FormatError) as exc:
            self.results.reject_container(exc)
            return
        self.results.compare(inputs)


class DemoPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(24, 20))
        self.cases = {case.title: case for case in scenarios(DEMO_TEXT)}
        title(self, "Проверить на примере").pack(anchor="w")
        ttk.Label(self, text="Для примеров используется один исходный текст и специально созданные доли.").pack(
            anchor="w", pady=(5, 12)
        )
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.selection = ttk.Combobox(row, values=list(self.cases), state="readonly", width=36)
        self.selection.set("Две изменённые доли")
        self.selection.pack(side="left", padx=(0, 8))
        self.selection.bind("<<ComboboxSelected>>", lambda _event: self.preview())
        ttk.Button(row, text="Сравнить способы", style="Primary.TButton", command=self.run).pack(side="left")
        ttk.Button(
            row, text="Сохранить исходный текст…",
            command=lambda: choose_save(DEMO_TEXT, "исходник.txt"),
        ).pack(side="left", padx=8)
        self.results = Comparison(self, "восстановленный-текст.txt")
        self.results.pack(fill="both", expand=True, pady=(15, 0))
        self.preview()

    def preview(self):
        case = self.cases[self.selection.get()]
        self.results.reset([f"№{i}  {name}" for i, (name, _) in enumerate(case.files, start=1)])

    def run(self):
        case = self.cases[self.selection.get()]
        try:
            inputs = prepare(Container.decode(case.container), list(case.files))
        except FormatError as exc:
            self.results.reject_container(exc)
            return
        self.results.compare(inputs, original=DEMO_TEXT)


def create_app():
    root = tk.Tk()
    root.title("Восстановление секрета")
    root.geometry("980x770")
    root.minsize(850, 720)
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, 12), background=BG, foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=MUTED)
    style.configure("Title.TLabel", font=(FONT, 22, "bold"), foreground=INK)
    style.configure("Section.TLabel", font=(FONT, 12, "bold"), foreground=INK)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("Card.TLabel", background=PANEL, foreground=MUTED)
    style.configure("CardTitle.TLabel", background=PANEL, foreground=INK, font=(FONT, 13, "bold"))
    style.configure("Good.TLabel", background=PANEL, foreground="#1b624c")
    style.configure("Failed.TLabel", background=PANEL, foreground="#933c31")
    style.configure("TButton", background=PANEL, padding=(8, 4))
    style.configure("Primary.TButton", background=ACCENT, foreground="white", padding=(12, 7))
    style.map("Primary.TButton", background=[("disabled", "#e3e8ec"), ("active", "#184c43")],
              foreground=[("disabled", "#586675"), ("!disabled", "white")])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(22, 9, 0, 0))
    style.configure("TNotebook.Tab", background="#e2e8ee", padding=(13, 7))
    style.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])

    header = tk.Frame(root, bg="#182d38", padx=24, pady=14)
    header.pack(fill="x")
    tk.Label(header, text="Восстановление секрета", font=(FONT, 20, "bold"), fg="white", bg="#182d38").pack(anchor="w")
    tk.Label(header, text="Схема Шамира 3 из 5  ·  AES-128-GCM", font=(FONT, 11), fg="#c4dbe2", bg="#182d38").pack(anchor="w")

    tabs = ttk.Notebook(root)
    tabs.pack(fill="both", expand=True)
    tabs.add(EncryptPage(tabs), text="Шифрование")
    tabs.add(RecoveryPage(tabs), text="Восстановление")
    demo = DemoPage(tabs)
    tabs.add(demo, text="Демонстрация")
    tabs.select(demo)
    return root


if __name__ == "__main__":
    create_app().mainloop()
