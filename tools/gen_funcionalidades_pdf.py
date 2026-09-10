"""Gera docs/FUNCIONALIDADES.pdf a partir de docs/FUNCIONALIDADES.md, no mesmo estilo visual
(paleta navy/cinza, Helvetica) já usado em app/reports/pdf_report.py -- não é parte do app em si,
só uma ferramenta de build de documentação, roda uma vez quando o .md muda."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

NAVY = (16, 24, 45)
GRAY = (90, 98, 112)
LIGHT_GRAY = (230, 233, 238)
GOLD = (170, 130, 40)
BLACK = (30, 30, 34)
WHITE = (255, 255, 255)

PAGE_W_MM = 210
MARGIN_MM = 18
CONTENT_W_MM = PAGE_W_MM - 2 * MARGIN_MM


class ManualPDF(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._footer_text = footer_text
        self.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
        self.set_auto_page_break(auto=True, margin=22)

    def footer(self):
        self.set_y(-15)
        self.set_draw_color(*LIGHT_GRAY)
        self.set_line_width(0.3)
        self.line(MARGIN_MM, self.get_y(), PAGE_W_MM - MARGIN_MM, self.get_y())
        self.set_font("Helvetica", size=7)
        self.set_text_color(*GRAY)
        self.set_y(-12)
        self.cell(0, 5, self._footer_text, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.cell(0, 4, f"Pagina {self.page_no()}", align="C")


_TOKEN_RE = re.compile(r"(\*\*.+?\*\*|`.+?`)")


def _write_inline(pdf: ManualPDF, text: str, size: int = 10, color=BLACK, line_h: float = 5.6) -> None:
    """Escreve uma linha/paragrafo com **negrito** e `codigo` inline, quebrando linha
    automaticamente (pdf.write cuida disso), sem markdown nativo do fpdf2 (mistura mal com o
    estilo `code` que precisamos aqui). `codigo` inline usa Helvetica em negrito/azul-marinho
    (NÃO troca pra Courier) de propósito -- alternar família de fonte no meio de uma sequência de
    `write()` na mesma linha aciona um bug real do fpdf2 (2.8.8) onde o texto seguinte, depois de
    um trecho Courier comprido, sai renderizado grande demais. As tabelas continuam usando
    Courier normalmente (cada célula é uma chamada única, não afetada)."""
    text = _sanitize(text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)  # link markdown -> só o texto visível
    parts = _TOKEN_RE.split(text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            pdf.set_font("Helvetica", "B", size)
            pdf.set_text_color(*color)
            pdf.write(line_h, part[2:-2])
        elif part.startswith("`") and part.endswith("`"):
            pdf.set_font("Helvetica", "B", size - 0.5)
            pdf.set_text_color(*NAVY)
            pdf.write(line_h, part[1:-1])
        else:
            pdf.set_font("Helvetica", "", size)
            pdf.set_text_color(*color)
            pdf.write(line_h, part)


_UNICODE_REPLACEMENTS = {
    "—": " - ",  # em dash
    "–": "-",  # en dash
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "…": "...",
    "•": "-",
    "●": "*",
    "→": "->",
}


def _sanitize(text: str) -> str:
    for src, dst in _UNICODE_REPLACEMENTS.items():
        text = text.replace(src, dst)
    # Rede de seguranca: qualquer outro caractere fora do latin-1 (fonte core do fpdf2) vira "?"
    # em vez de derrubar a geração inteira -- melhor um glifo estranho isolado que um traceback.
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_md(text: str) -> str:
    text = _sanitize(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return text


def _title(pdf: ManualPDF, text: str) -> None:
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 9, _strip_md(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.8)
    pdf.line(MARGIN_MM, pdf.get_y() + 1, MARGIN_MM + 40, pdf.get_y() + 1)
    pdf.ln(6)


def _h2(pdf: ManualPDF, text: str) -> None:
    if pdf.get_y() > 250:
        pdf.add_page()
    else:
        pdf.ln(4)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 8, _strip_md(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*LIGHT_GRAY)
    pdf.set_line_width(0.4)
    pdf.line(MARGIN_MM, pdf.get_y() + 1, PAGE_W_MM - MARGIN_MM, pdf.get_y() + 1)
    pdf.ln(4)


def _h3(pdf: ManualPDF, text: str) -> None:
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 6.5, _strip_md(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def _paragraph(pdf: ManualPDF, text: str) -> None:
    pdf.set_x(MARGIN_MM)
    _write_inline(pdf, text)
    pdf.ln(7)


def _bullet(pdf: ManualPDF, text: str, indent: float = 5) -> None:
    x0 = MARGIN_MM + indent
    pdf.set_x(x0)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GOLD)
    pdf.cell(4, 5.6, chr(0x95) if False else "-", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_x(x0 + 5)
    _write_inline(pdf, text)
    pdf.ln(6.2)


def _numbered(pdf: ManualPDF, n: int, text: str) -> None:
    x0 = MARGIN_MM
    pdf.set_x(x0)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GOLD)
    pdf.cell(6, 5.6, f"{n}.", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_x(x0 + 6)
    _write_inline(pdf, text)
    pdf.ln(6.2)


def _table(pdf: ManualPDF, header: list[str], rows: list[list[str]]) -> None:
    """API de alto nível do fpdf2 (`pdf.table`) em vez de multi_cell manual -- ela mesma cuida de
    quebra de página no meio de uma linha/tabela (o multi_cell manual perdia a posição e o
    conteúdo sumia quando uma linha caía perto do fim da página)."""
    from fpdf.fonts import FontFace

    pdf.set_font("Helvetica", "", 9.5)
    col0_w = 58
    col1_w = CONTENT_W_MM - col0_w
    # size_pt explícito nos três estilos -- sem isso, uma FontFace com o campo omitido herda
    # silenciosamente o tamanho/estilo (`pdf.font_size`/`pdf.font_style`) que estava setado em
    # QUALQUER elemento anterior (ex.: o título em 20pt bold), fazendo células de dados saírem
    # enormes e em negrito por acidente (bug real visto no primeiro render deste script).
    heading_style = FontFace(family="Helvetica", emphasis="B", size_pt=9.5, color=WHITE, fill_color=NAVY)
    code_style = FontFace(family="Courier", emphasis="B", size_pt=9, color=NAVY)
    text_style = FontFace(family="Helvetica", emphasis="", size_pt=9.5, color=BLACK)
    with pdf.table(
        col_widths=(col0_w, col1_w),
        text_align=("LEFT", "LEFT"),
        headings_style=heading_style,
        borders_layout="NONE",
        cell_fill_color=(245, 246, 248),
        cell_fill_mode="ROWS",
        line_height=6,
        padding=(2, 2),
    ) as table:
        table.row([_strip_md(h) for h in header])
        for row in rows:
            r = table.row()
            r.cell(_strip_md(row[0]), style=code_style)
            r.cell(_strip_md(row[1]), style=text_style)
    pdf.ln(3)


def parse_and_render(md_path: Path, out_path: Path) -> None:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    pdf = ManualPDF(footer_text="OCTO Tecnologia - Manual de Operacao do Painel de Roleta")
    pdf.add_page()

    i = 0
    n = len(lines)
    para_buf: list[str] = []

    def flush_para():
        nonlocal para_buf
        if para_buf:
            _paragraph(pdf, " ".join(para_buf))
            para_buf = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("# "):
            flush_para()
            _title(pdf, stripped[2:])
            i += 1
            continue
        if stripped.startswith("## "):
            flush_para()
            _h2(pdf, stripped[3:])
            i += 1
            continue
        if stripped.startswith("### "):
            flush_para()
            _h3(pdf, stripped[4:])
            i += 1
            continue
        if stripped.startswith("|"):
            flush_para()
            # markdown table: header row, separator row, data rows
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 1  # skip separator row
            i += 1
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            _table(pdf, header, rows)
            continue
        if re.match(r"^\d+\.\s", stripped):
            flush_para()
            m = re.match(r"^(\d+)\.\s+(.*)", stripped)
            _numbered(pdf, int(m.group(1)), m.group(2))
            i += 1
            continue
        if stripped.startswith("- "):
            flush_para()
            indent = 5 if not line.startswith("   ") else 11
            _bullet(pdf, stripped[2:], indent=indent)
            i += 1
            continue
        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            flush_para()
            pdf.ln(2)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*GRAY)
            pdf.multi_cell(0, 5.5, _strip_md(stripped.strip("*")), align="C")
            i += 1
            continue
        if stripped == "":
            flush_para()
            i += 1
            continue

        para_buf.append(stripped)
        i += 1

    flush_para()
    pdf.output(str(out_path))
    print(f"gerado: {out_path}")


if __name__ == "__main__":
    md_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    parse_and_render(md_path, out_path)
