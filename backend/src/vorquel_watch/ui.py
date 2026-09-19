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
        self.checked_candidate_ids: set[str] = set()
        self.knowledge_results_by_id: dict[str, dict[str, Any]] = {}

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
        ttk.Button(actions, text="Aprovar marcados", command=self.approve_selected).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Rejeitar marcados", command=self.reject_selected).pack(
            side="left", padx=(8, 0)
        )

        columns = ("check", "type", "domain", "title", "source")
        self.candidates = ttk.Treeview(
            brain,
            columns=columns,
            show="headings",
            height=9,
            selectmode="browse",
        )
        self.candidates.heading("check", text="✓")
        self.candidates.heading("type", text="Tipo")
        self.candidates.heading("domain", text="Domínio")
        self.candidates.heading("title", text="Título")
        self.candidates.heading("source", text="Source")
        self.candidates.column("check", width=46, stretch=False, anchor="center")
        self.candidates.column("type", width=110, stretch=False)
        self.candidates.column("domain", width=130, stretch=False)
        self.candidates.column("title", width=420)
        self.candidates.column("source", width=280)
        self.candidates.pack(fill="both", expand=True)
        self.candidates.bind("<Button-1>", self.toggle_candidate_check)
        self.candidates.bind("<Double-1>", self.show_candidate_details)

        search = ttk.Frame(brain)
        search.pack(fill="x", pady=(8, 0))
        ttk.Label(search, text="Buscar conhecimento aprovado:").pack(side="left")
        ttk.Entry(search, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(search, text="Buscar", command=self.search_knowledge).pack(side="left")
        ttk.Button(search, text="Sintetizar", command=self.synthesize_knowledge).pack(
            side="left", padx=(8, 0)
        )

        lifecycle = ttk.Frame(brain)
        lifecycle.pack(fill="x", pady=(8, 0))
        ttk.Label(lifecycle, text="Knowledge ID selecionado/colado:").pack(side="left")
        self.knowledge_id_var = tk.StringVar()
        ttk.Entry(lifecycle, textvariable=self.knowledge_id_var, width=44).pack(
            side="left", padx=8
        )
        ttk.Button(
            lifecycle,
            text="Retirar da memória ativa",
            command=self.withdraw_knowledge_ui,
        ).pack(side="left")
        ttk.Button(
            lifecycle,
            text="Substituir por...",
            command=self.supersede_knowledge_ui,
        ).pack(side="left", padx=(8, 0))

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
        ttk.Button(vault_row, text="Configurar 3D Graph", command=self.setup_3d_graph).pack(
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

    def toggle_candidate_check(self, event) -> None:
        row_id = self.candidates.identify_row(event.y)
        column = self.candidates.identify_column(event.x)
        if not row_id or column != "#1":
            return
        if row_id in self.checked_candidate_ids:
            self.checked_candidate_ids.remove(row_id)
            mark = "☐"
        else:
            self.checked_candidate_ids.add(row_id)
            mark = "☑"
        values = list(self.candidates.item(row_id, "values"))
        if values:
            values[0] = mark
            self.candidates.item(row_id, values=values)

    def _checked_candidate_ids(self) -> list[str]:
        items = [
            candidate_id
            for candidate_id in self.candidates.get_children()
            if candidate_id in self.checked_candidate_ids
        ]
        if not items:
            messagebox.showwarning(
                "Vorquel Watch",
                "Marque um ou mais candidates na coluna ✓.",
            )
        return items

    def approve_selected(self) -> None:
        candidate_ids = self._checked_candidate_ids()
        if not candidate_ids:
            return
        if not messagebox.askyesno(
            "Confirmar aprovação",
            f"Aprovar {len(candidate_ids)} candidate(s) como conhecimento reutilizável?\n\n"
            "A aprovação não concede autoridade de instrução.",
        ):
            return
        self._run_batch_review(candidate_ids, action="approve")

    def reject_selected(self) -> None:
        candidate_ids = self._checked_candidate_ids()
        if not candidate_ids:
            return
        if not messagebox.askyesno(
            "Confirmar rejeição",
            f"Rejeitar {len(candidate_ids)} candidate(s)?",
        ):
            return
        self._run_batch_review(candidate_ids, action="reject")

    def _run_batch_review(self, candidate_ids: list[str], *, action: str) -> None:
        if self._busy:
            messagebox.showinfo(
                "Vorquel Watch",
                "Aguarde a operação atual terminar.",
            )
            return

        self._busy = True
        self.progress.start(10)
        verb = "Aprovando" if action == "approve" else "Rejeitando"
        self.status_var.set(f"{verb} {len(candidate_ids)} candidate(s)...")

        def worker() -> None:
            results: list[dict[str, Any]] = []
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"

            for candidate_id in candidate_ids:
                note = (
                    "Aprovado explicitamente pelo usuário via interface local em seleção múltipla."
                    if action == "approve"
                    else "Rejeitado explicitamente pelo usuário via interface local em seleção múltipla."
                )
                command = [
                    sys.executable,
                    "-m",
                    "vorquel_watch.cli",
                    "brain",
                    action,
                    candidate_id,
                    "--note",
                    note,
                ]
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    env=child_env,
                    check=False,
                )
                stdout = completed.stdout.strip()
                stderr = completed.stderr.strip()
                if completed.returncode != 0:
                    self.root.after(
                        0,
                        lambda cid=candidate_id, msg=(stderr or stdout or "Falha sem detalhes."):
                            self._finish_error(
                                f"Falha em {cid}:\n{msg}",
                                quiet=False,
                            ),
                    )
                    return
                try:
                    payload = json.loads(stdout) if stdout else {}
                except json.JSONDecodeError:
                    payload = {"output": stdout}
                results.append({"candidate_id": candidate_id, "result": payload})

            self.root.after(
                0,
                lambda: self._finish_batch_review(results, action=action),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_batch_review(
        self,
        results: list[dict[str, Any]],
        *,
        action: str,
    ) -> None:
        self._busy = False
        self.progress.stop()
        label = "aprovado(s)" if action == "approve" else "rejeitado(s)"
        self._show_json(
            {"count": len(results), "items": results},
            f"{len(results)} candidate(s) {label}.",
        )
        self.refresh_candidates()

    def show_candidate_details(self, _event=None) -> None:
        selection = self.candidates.selection()
        if not selection:
            return
        candidate = self.pending_by_id.get(selection[0])
        if not candidate:
            return
        self._show_json({"candidate": candidate}, "Candidate selecionado.")

    def search_knowledge(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            messagebox.showwarning("Vorquel Watch", "Digite algo para buscar.")
            return
        self._run_async(
            ["brain", "search", query],
            status="Buscando no Brain...",
            on_success=self._after_knowledge_search,
        )

    def _after_knowledge_search(self, data: dict[str, Any]) -> None:
        items = data.get("items") or []
        self.knowledge_results_by_id = {
            str(item.get("knowledge_id") or ""): item
            for item in items
            if item.get("knowledge_id")
        }
        if len(items) == 1 and items[0].get("knowledge_id"):
            self.knowledge_id_var.set(str(items[0]["knowledge_id"]))
        self._show_json(data, f"Busca concluída: {len(items)} item(ns).")

    def synthesize_knowledge(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            messagebox.showwarning("Vorquel Watch", "Digite uma pergunta ou tema para sintetizar.")
            return
        self._run_async(
            ["brain", "synthesize", query],
            status="Sintetizando conhecimento ativo...",
            on_success=lambda data: self._show_json(data, "Síntese concluída."),
        )

    def withdraw_knowledge_ui(self) -> None:
        knowledge_id = self.knowledge_id_var.get().strip()
        if not knowledge_id:
            messagebox.showwarning(
                "Vorquel Watch",
                "Informe um knowledge_id (knw_...).",
            )
            return
        reason = self._ask_reason(
            "Retirar conhecimento",
            "Motivo da retirada da memória ativa:",
        )
        if reason is None:
            return
        if not messagebox.askyesno(
            "Confirmar retirada",
            f"Retirar {knowledge_id} da memória ativa?\n\n"
            "O histórico e a provenance serão preservados.",
        ):
            return
        self._run_async(
            ["brain", "withdraw", knowledge_id, "--reason", reason],
            status="Retirando conhecimento da memória ativa...",
            on_success=lambda data: self._show_json(
                data,
                "Conhecimento retirado da memória ativa.",
            ),
        )

    def supersede_knowledge_ui(self) -> None:
        knowledge_id = self.knowledge_id_var.get().strip()
        if not knowledge_id:
            messagebox.showwarning(
                "Vorquel Watch",
                "Informe o knowledge_id antigo (knw_...).",
            )
            return
        replacement = self._ask_text(
            "Substituir conhecimento",
            "Knowledge ID substituto (knw_...):",
        )
        if replacement is None:
            return
        reason = self._ask_reason(
            "Substituir conhecimento",
            "Motivo da substituição/correção:",
        )
        if reason is None:
            return
        if not messagebox.askyesno(
            "Confirmar substituição",
            f"Marcar {knowledge_id} como SUPERSEDED por\n{replacement}?\n\n"
            "O conhecimento antigo continuará no histórico.",
        ):
            return
        self._run_async(
            ["brain", "supersede", knowledge_id, replacement, "--reason", reason],
            status="Registrando supersession...",
            on_success=lambda data: self._show_json(
                data,
                "Conhecimento substituído; histórico preservado.",
            ),
        )

    def _ask_text(self, title: str, prompt: str) -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        value = tk.StringVar()
        result: dict[str, str | None] = {"value": None}

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=prompt).pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=value, width=64)
        entry.pack(fill="x", pady=(8, 12))
        entry.focus_set()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def accept() -> None:
            clean = value.get().strip()
            if not clean:
                messagebox.showwarning(title, "Digite um valor.", parent=dialog)
                return
            result["value"] = clean
            dialog.destroy()

        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Confirmar", command=accept).pack(
            side="right", padx=(0, 8)
        )
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()
        return result["value"]

    def _ask_reason(self, title: str, prompt: str) -> str | None:
        return self._ask_text(title, prompt)

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

    def setup_3d_graph(self) -> None:
        vault = Path(self.vault_var.get().strip())
        if not vault.is_dir():
            messagebox.showerror("Vorquel Watch", "O caminho do Vault não existe.")
            return
        if not messagebox.askyesno(
            "Configurar 3D Graph",
            "Aplicar o preset visual da Vorquel ao New 3D Graph?\n\n"
            "A configuração atual será preservada em backup antes da primeira alteração.",
        ):
            return
        self._run_async(
            ["brain", "setup-obsidian", "--vault", str(vault)],
            status="Configurando New 3D Graph...",
            on_success=lambda data: self._show_json(
                data,
                "3D Graph configurado. Reinicie o Obsidian para aplicar.",
            ),
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

        current_ids = {
            str(candidate.get("candidate_id") or "")
            for candidate in (data.get("items") or [])
            if candidate.get("candidate_id")
        }
        self.checked_candidate_ids.intersection_update(current_ids)

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
                    "☑" if candidate_id in self.checked_candidate_ids else "☐",
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
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    env=child_env,
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
