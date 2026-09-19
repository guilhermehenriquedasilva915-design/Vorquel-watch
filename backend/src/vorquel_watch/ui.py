from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any


DEFAULT_VAULT = Path(r"C:\VORQUEL\VORQUEL BRAIN")
MEDIA_TYPES = [
    ("Vídeo e áudio", "*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac"),
    ("Todos os arquivos", "*.*"),
]


class VorquelWatchUI:
    """Small local desktop shell around the existing Vorquel Watch CLI.

    The UI deliberately does not bypass the established service/review
    boundaries. Every operation delegates to an existing CLI command, so
    knowledge approval remains explicit and the Obsidian export remains
    one-way into _generated.
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Vorquel Watch")
        self.root.geometry("1080x760")
        self.root.minsize(900, 650)

        self.selected_file: Path | None = None
        self.pending_by_id: dict[str, dict[str, Any]] = {}

        self.file_var = tk.StringVar(value="Nenhum arquivo selecionado")
        self.status_var = tk.StringVar(value="Pronto")
        self.vault_var = tk.StringVar(value=str(DEFAULT_VAULT))
        self.search_var = tk.StringVar()
        self._busy = False

        self._build()
        self.refresh_candidates()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=14)
        shell.pack(fill="both", expand=True)

        title = ttk.Label(
            shell,
            text="VORQUEL WATCH",
            font=("Segoe UI", 18, "bold"),
        )
        title.pack(anchor="w")

        subtitle = ttk.Label(
            shell,
            text="Análise local de mídia, revisão humana do Brain e exportação para Obsidian",
        )
        subtitle.pack(anchor="w", pady=(0, 12))

        analysis = ttk.LabelFrame(shell, text="1. Análise", padding=10)
        analysis.pack(fill="x", pady=(0, 10))

        file_row = ttk.Frame(analysis)
        file_row.pack(fill="x")
        ttk.Button(
            file_row,
            text="Selecionar vídeo ou áudio",
            command=self.select_file,
        ).pack(side="left")
        ttk.Button(
            file_row,
            text="Analisar",
            command=self.analyze_selected,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            analysis,
            textvariable=self.file_var,
            wraplength=880,
        ).pack(anchor="w", pady=(8, 0))

        self.progress = ttk.Progressbar(analysis, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 4))
        ttk.Label(analysis, textvariable=self.status_var).pack(anchor="w")

        brain = ttk.LabelFrame(shell, text="2. Brain — candidates pendentes", padding=10)
        brain.pack(fill="both", expand=True, pady=(0, 10))

        actions = ttk.Frame(brain)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="Atualizar", command=self.refresh_candidates).pack(
            side="left"
        )
        ttk.Button(actions, text="Aprovar selecionado", command=self.approve_selected).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Rejeitar selecionado", command=self.reject_selected).pack(
            side="left", padx=(8, 0)
        )

        columns = ("type", "domain", "title", "source")
        self.candidates = ttk.Treeview(
            brain,
            columns=columns,
            show="headings",
            height=9,
            selectmode="browse",
        )
        self.candidates.heading("type", text="Tipo")
        self.candidates.heading("domain", text="Domínio")
        self.candidates.heading("title", text="Título")
        self.candidates.heading("source", text="Source")
        self.candidates.column("type", width=110, stretch=False)
        self.candidates.column("domain", width=130, stretch=False)
        self.candidates.column("title", width=420)
        self.candidates.column("source", width=280)
        self.candidates.pack(fill="both", expand=True)

        search = ttk.Frame(brain)
        search.pack(fill="x", pady=(8, 0))
        ttk.Label(search, text="Buscar conhecimento aprovado:").pack(side="left")
        ttk.Entry(search, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(search, text="Buscar", command=self.search_knowledge).pack(side="left")

        obsidian = ttk.LabelFrame(shell, text="3. Obsidian", padding=10)
        obsidian.pack(fill="x")

        vault_row = ttk.Frame(obsidian)
        vault_row.pack(fill="x")
        ttk.Entry(vault_row, textvariable=self.vault_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(vault_row, text="Escolher Vault", command=self.choose_vault).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(vault_row, text="Exportar", command=self.export_obsidian).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(vault_row, text="Abrir Vault", command=self.open_vault).pack(
            side="left", padx=(8, 0)
        )

        output_frame = ttk.LabelFrame(shell, text="Resultado / log", padding=8)
        output_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.output = tk.Text(output_frame, height=10, wrap="word")
        self.output.pack(fill="both", expand=True)
        self.output.configure(state="disabled")

    def select_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Selecionar vídeo ou áudio",
            filetypes=MEDIA_TYPES,
        )
        if not chosen:
            return
        self.selected_file = Path(chosen)
        self.file_var.set(str(self.selected_file))
        self.status_var.set("Arquivo selecionado")

    def choose_vault(self) -> None:
        chosen = filedialog.askdirectory(
            title="Selecionar Vault do Obsidian",
            initialdir=self.vault_var.get() or str(DEFAULT_VAULT),
        )
        if chosen:
            self.vault_var.set(chosen)

    def analyze_selected(self) -> None:
        if self.selected_file is None:
            messagebox.showwarning(
                "Vorquel Watch",
                "Selecione um vídeo ou áudio primeiro.",
            )
            return
        self._run_async(
            [
                "analyze",
                str(self.selected_file),
                "--language",
                "pt",
            ],
            status="Analisando mídia local...",
            on_success=self._after_analysis,
        )

    def refresh_candidates(self) -> None:
        self._run_async(
            ["brain", "list", "--status", "PENDING"],
            status="Carregando candidates...",
            on_success=self._populate_candidates,
            quiet=True,
        )

    def approve_selected(self) -> None:
        candidate_id = self._selected_candidate_id()
        if not candidate_id:
            return
        if not messagebox.askyesno(
            "Confirmar aprovação",
            "Aprovar este candidate como conhecimento reutilizável?\n\n"
            "A aprovação não concede autoridade de instrução.",
        ):
            return
        self._run_async(
            [
                "brain",
                "approve",
                candidate_id,
                "--note",
                "Aprovado explicitamente pelo usuário via interface local.",
            ],
            status="Aprovando candidate...",
            on_success=lambda data: self._after_review(data, "Candidate aprovado."),
        )

    def reject_selected(self) -> None:
        candidate_id = self._selected_candidate_id()
        if not candidate_id:
            return
        if not messagebox.askyesno(
            "Confirmar rejeição",
            "Rejeitar este candidate?",
        ):
            return
        self._run_async(
            [
                "brain",
                "reject",
                candidate_id,
                "--note",
                "Rejeitado explicitamente pelo usuário via interface local.",
            ],
            status="Rejeitando candidate...",
            on_success=lambda data: self._after_review(data, "Candidate rejeitado."),
        )

    def search_knowledge(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            messagebox.showwarning("Vorquel Watch", "Digite algo para buscar.")
            return
        self._run_async(
            ["brain", "search", query],
            status="Buscando no Brain...",
            on_success=lambda data: self._show_json(data, "Busca concluída."),
        )

    def export_obsidian(self) -> None:
        vault = Path(self.vault_var.get().strip())
        if not vault.is_dir():
            messagebox.showerror(
                "Vorquel Watch",
                "O caminho do Vault não existe.",
            )
            return
        self._run_async(
            ["brain", "export-obsidian", "--vault", str(vault)],
            status="Exportando para Obsidian...",
            on_success=lambda data: self._show_json(data, "Exportação concluída."),
        )

    def open_vault(self) -> None:
        vault = Path(self.vault_var.get().strip())
        if not vault.is_dir():
            messagebox.showerror("Vorquel Watch", "O caminho do Vault não existe.")
            return
        if os.name != "nt":
            messagebox.showerror(
                "Vorquel Watch",
                "Abrir pasta automaticamente está habilitado somente no Windows.",
            )
            return
        os.startfile(str(vault))  # type: ignore[attr-defined]

    def _selected_candidate_id(self) -> str | None:
        selection = self.candidates.selection()
        if not selection:
            messagebox.showwarning(
                "Vorquel Watch",
                "Selecione um candidate primeiro.",
            )
            return None
        return selection[0]

    def _after_analysis(self, data: dict[str, Any]) -> None:
        self._show_json(data, "Análise concluída.")
        self.refresh_candidates()

    def _after_review(self, data: dict[str, Any], message: str) -> None:
        self._show_json(data, message)
        self.refresh_candidates()

    def _populate_candidates(self, data: dict[str, Any]) -> None:
        for item in self.candidates.get_children():
            self.candidates.delete(item)
        self.pending_by_id.clear()

        for candidate in data.get("items") or []:
            candidate_id = str(candidate.get("candidate_id") or "")
            if not candidate_id:
                continue
            self.pending_by_id[candidate_id] = candidate
            self.candidates.insert(
                "",
                "end",
                iid=candidate_id,
                values=(
                    candidate.get("knowledge_type") or "",
                    candidate.get("domain") or "",
                    candidate.get("title") or "",
                    candidate.get("source_id") or "",
                ),
            )
        self.status_var.set(
            f"{len(self.pending_by_id)} candidate(s) pendente(s)"
        )

    def _show_json(self, data: dict[str, Any], status: str) -> None:
        self.status_var.set(status)
        rendered = json.dumps(data, ensure_ascii=False, indent=2)
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", rendered)
        self.output.configure(state="disabled")

    def _run_async(
        self,
        args: list[str],
        *,
        status: str,
        on_success,
        quiet: bool = False,
    ) -> None:
        if self._busy and not quiet:
            messagebox.showinfo(
                "Vorquel Watch",
                "Aguarde a operação atual terminar.",
            )
            return

        if not quiet:
            self._busy = True
            self.progress.start(10)
        self.status_var.set(status)

        def worker() -> None:
            command = [sys.executable, "-m", "vorquel_watch.cli", *args]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
            except Exception as exc:
                self.root.after(
                    0,
                    lambda: self._finish_error(
                        f"Falha ao iniciar operação: {type(exc).__name__}",
                        quiet=quiet,
                    ),
                )
                return

            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            if completed.returncode != 0:
                safe_message = stderr or stdout or "A operação falhou sem detalhes."
                self.root.after(
                    0,
                    lambda: self._finish_error(safe_message, quiet=quiet),
                )
                return

            try:
                payload = json.loads(stdout) if stdout else {}
            except json.JSONDecodeError:
                payload = {"output": stdout}

            self.root.after(
                0,
                lambda: self._finish_success(payload, on_success, quiet=quiet),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_success(self, payload: dict[str, Any], callback, *, quiet: bool) -> None:
        if not quiet:
            self._busy = False
            self.progress.stop()
        callback(payload)

    def _finish_error(self, message: str, *, quiet: bool) -> None:
        if not quiet:
            self._busy = False
            self.progress.stop()
        self.status_var.set("Erro")
        if not quiet:
            self.output.configure(state="normal")
            self.output.delete("1.0", "end")
            self.output.insert("1.0", message)
            self.output.configure(state="disabled")
            messagebox.showerror("Vorquel Watch", message)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    VorquelWatchUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
