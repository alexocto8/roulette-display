"""Professional session report, PDF — fpdf2 (pure Python, no Cairo/Pango/GTK, light enough for a
Pi 3). Charts are drawn with fpdf2's own rect/line primitives, the same "no heavy dependency for
simple shapes" choice already made for the on-screen suit-icon-turned-dots in app/ui/display.py —
matplotlib would drag in numpy + a large import for bars and lines this simple.

Layout follows sections 28-39 of the spec: cover/header, executive summary (KPI cards + color
distribution), operational performance, full number distribution table, hot/cold, streaks,
audit summary, and a discreet footer with integrity info on every page. Deliberately printable,
readable on a phone, and free of internal jargon or raw stack-trace-style output — this is a
document for a manager, not a developer.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.reports import branding
from app.statistics import engine as stats

# Paleta pensada pra impressão/tela clara (não é a paleta escura do painel — este documento é
# pra ler no papel, no computador ou no celular, então o fundo é branco).
_NAVY = (16, 24, 45)
_GRAY = (110, 118, 130)
_LIGHT_GRAY = (238, 240, 244)
_RED = (200, 30, 30)
_BLACK = (30, 30, 34)
_GREEN = (20, 140, 80)
_ORANGE = (200, 120, 20)
_WHITE = (255, 255, 255)

_PAGE_W_MM = 210
_MARGIN_MM = 15
_CONTENT_W_MM = _PAGE_W_MM - 2 * _MARGIN_MM


class _ReportPDF(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._footer_text = footer_text
        self.set_margins(_MARGIN_MM, _MARGIN_MM, _MARGIN_MM)
        self.set_auto_page_break(auto=True, margin=20)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", size=7)
        self.set_text_color(*_GRAY)
        self.cell(0, 5, self._footer_text, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.cell(0, 4, f"Página {self.page_no()}", align="C")


def _section_title(pdf: _ReportPDF, text: str) -> None:
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*_LIGHT_GRAY)
    pdf.set_line_width(0.4)
    y = pdf.get_y()
    pdf.line(_MARGIN_MM, y, _PAGE_W_MM - _MARGIN_MM, y)
    pdf.ln(3)


def _kpi_card(pdf: _ReportPDF, x: float, y: float, w: float, h: float, value: str, label: str) -> None:
    pdf.set_fill_color(*_LIGHT_GRAY)
    pdf.rect(x, y, w, h, style="F")
    pdf.set_xy(x, y + h * 0.18)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*_NAVY)
    pdf.cell(w, 10, value, align="C", new_x=XPos.LMARGIN, new_y=YPos.TOP)
    pdf.set_xy(x, y + h * 0.62)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_GRAY)
    pdf.cell(w, 5, label, align="C")


def _kpi_row(pdf: _ReportPDF, cards: list[tuple[str, str]]) -> None:
    gap = 4
    n = len(cards)
    w = (_CONTENT_W_MM - gap * (n - 1)) / n
    h = 22
    y = pdf.get_y()
    for i, (value, label) in enumerate(cards):
        _kpi_card(pdf, _MARGIN_MM + i * (w + gap), y, w, h, value, label)
    pdf.set_y(y + h + 6)


def _bar_chart(pdf: _ReportPDF, items: list[tuple[str, float, tuple]], max_value: float | None = None) -> None:
    """Barra horizontal simples: rótulo à esquerda, barra proporcional, valor à direita. `items`
    = [(label, value, rgb_color), ...]. Suficiente pra distribuição de cor/dúzia/coluna — não
    tenta ser uma biblioteca de gráficos genérica."""
    if not items:
        return
    max_value = max_value or max((v for _, v, _ in items), default=1) or 1
    row_h = 7
    label_w = 32
    value_w = 16
    bar_max_w = _CONTENT_W_MM - label_w - value_w
    for label, value, color in items:
        y = pdf.get_y()
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_NAVY)
        pdf.set_xy(_MARGIN_MM, y)
        pdf.cell(label_w, row_h, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
        bar_w = max(0.5, bar_max_w * (value / max_value)) if max_value else 0
        pdf.set_fill_color(*_LIGHT_GRAY)
        pdf.rect(_MARGIN_MM + label_w, y + 1, bar_max_w, row_h - 2, style="F")
        pdf.set_fill_color(*color)
        pdf.rect(_MARGIN_MM + label_w, y + 1, bar_w, row_h - 2, style="F")
        pdf.set_xy(_MARGIN_MM + label_w + bar_max_w, y)
        pdf.set_text_color(*_GRAY)
        pdf.cell(value_w, row_h, f"{value:.1f}%", align="R")
        pdf.set_y(y + row_h)


def generate_pdf_report(
    identity_row, session_row, spins, snapshot, audit_integrity_ok: bool, dest_path: str | Path,
) -> Path:
    """`identity_row`/`session_row` are sqlite3.Row from installation_identity/sessions.
    `snapshot` is an app.analytics.analytics_service.AnalyticsSnapshot already computed for this
    session. Never raises on a missing/invalid logo — falls back to a text-only header."""
    table_id_short = (session_row["table_id"] or "")[:8]
    footer_text = (
        f"ID da Sessao: {session_row['session_code']}  |  ID da Mesa: {table_id_short}...  |  "
        f"Integridade: {'VERIFICADA' if audit_integrity_ok else 'NAO VERIFICADA'}  |  "
        f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    pdf = _ReportPDF(footer_text)
    pdf.add_page()

    # -- capa / cabeçalho ---------------------------------------------------------------------
    logo_path = identity_row["venue_logo_path"]
    text_x = _MARGIN_MM
    if branding.is_valid_stored_logo(logo_path):
        try:
            from PIL import Image

            with Image.open(logo_path) as img:
                ratio = img.height / img.width
            logo_w = 28
            pdf.image(logo_path, x=_MARGIN_MM, y=_MARGIN_MM, w=logo_w, h=logo_w * ratio)
            text_x = _MARGIN_MM + logo_w + 6
        except Exception:
            pass  # nunca deixa um logo problemático quebrar o relatório

    pdf.set_xy(text_x, _MARGIN_MM)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_NAVY)
    report_title = identity_row["report_title"] or identity_row["venue_name"] or "Relatório Operacional"
    pdf.cell(_PAGE_W_MM - text_x - _MARGIN_MM, 8, report_title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(text_x)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_GRAY)
    subtitle = identity_row["report_subtitle"] or "Relatório Operacional da Mesa"
    pdf.cell(_PAGE_W_MM - text_x - _MARGIN_MM, 6, subtitle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(text_x)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*_NAVY)
    pdf.cell(
        _PAGE_W_MM - text_x - _MARGIN_MM, 6,
        f"{session_row['table_name']}  ({session_row['table_code'] or 's/codigo'})",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )

    pdf.set_y(_MARGIN_MM + 32)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_GRAY)
    started = (session_row["started_at"] or "")[:16].replace("T", " ")
    ended = (session_row["ended_at"] or "")[:16].replace("T", " ") if session_row["ended_at"] else "em andamento"
    info_line = f"Sessao: {session_row['session_code']}   |   Local: {session_row['table_location'] or '-'}"
    pdf.cell(0, 5, info_line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, f"Inicio: {started}   |   Encerramento: {ended}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # -- resumo executivo -----------------------------------------------------------------------
    _section_title(pdf, "Resumo Executivo")
    duration_min = snapshot.session_duration_seconds / 60
    duration_label = f"{int(duration_min // 60)}h{int(duration_min % 60):02d}"
    _kpi_row(pdf, [
        (str(snapshot.total_spins), "GIROS"),
        (f"{snapshot.spins_per_hour:.1f}", "GIROS/HORA"),
        (duration_label, "DURACAO"),
        (str(snapshot.undo_count), "CORRECOES"),
    ])

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, "Distribuicao principal", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    _bar_chart(pdf, [
        ("Vermelho", snapshot.color.percentage("red"), _RED),
        ("Preto", snapshot.color.percentage("black"), _BLACK),
        ("Zero", snapshot.color.percentage("green"), _GREEN),
    ], max_value=100)

    # -- performance operacional ------------------------------------------------------------------
    _section_title(pdf, "Performance Operacional")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_NAVY)
    lines = [
        f"Giros totais: {snapshot.total_spins}",
        f"Giros por hora: {snapshot.spins_per_hour:.1f}",
        f"Intervalo medio entre giros: {snapshot.interval_avg_seconds:.0f}s",
        f"Menor intervalo: {snapshot.interval_min_seconds:.0f}s   |   Maior intervalo: {snapshot.interval_max_seconds:.0f}s",
        f"Correcoes: {snapshot.undo_count}   |   Taxa de correcao: {snapshot.correction_rate:.2f}%",
    ]
    for line in lines:
        pdf.cell(0, 6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # -- distribuição completa -------------------------------------------------------------------
    _section_title(pdf, "Distribuicao dos Numeros (0-36)")
    since_last = stats.spins_since_last_occurrence([s.number for s in spins])
    _number_distribution_table(pdf, snapshot, since_last)

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, "Par/Impar, Faixa, Duzias e Colunas", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    _bar_chart(pdf, [
        ("Impar", snapshot.parity.percentage("odd"), _NAVY),
        ("Par", snapshot.parity.percentage("even"), _NAVY),
        ("1-18", snapshot.range_.percentage("low"), _ORANGE),
        ("19-36", snapshot.range_.percentage("high"), _ORANGE),
        ("1a duzia", snapshot.dozen.percentage("1"), _GRAY),
        ("2a duzia", snapshot.dozen.percentage("2"), _GRAY),
        ("3a duzia", snapshot.dozen.percentage("3"), _GRAY),
    ], max_value=100)

    # -- hot/cold ---------------------------------------------------------------------------------
    _section_title(pdf, "Hot / Cold")
    _hot_cold_table(pdf, "QUENTE (mais frequentes)", snapshot.hot, "ocorrencias")
    pdf.ln(2)
    _hot_cold_table(pdf, "FRIO (mais ausentes)", snapshot.cold, "giros sem sair")

    # -- streaks ------------------------------------------------------------------------------------
    _section_title(pdf, "Sequencias (Streaks)")
    streak_labels = {
        "red": "Maior sequencia vermelha", "black": "Maior sequencia preta",
        "even": "Maior sequencia par", "odd": "Maior sequencia impar",
        "low": "Maior sequencia 1-18", "high": "Maior sequencia 19-36",
        "dozen1": "Maior sequencia 1a duzia", "dozen2": "Maior sequencia 2a duzia",
        "dozen3": "Maior sequencia 3a duzia",
    }
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_NAVY)
    for key, label in streak_labels.items():
        largest = snapshot.streaks.get(key, {}).get("largest", 0)
        pdf.cell(0, 6, f"{label}: {largest}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if snapshot.chi_square.get("applicable"):
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*_GRAY)
        pdf.multi_cell(0, 5, snapshot.chi_square["verdict"])

    # -- auditoria resumida -------------------------------------------------------------------------
    _section_title(pdf, "Auditoria da Sessao")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, f"Giros registrados: {snapshot.total_spins}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f"Correcoes (undo): {snapshot.undo_count}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(
        0, 6,
        f"Integridade da trilha de auditoria: {'VALIDA' if audit_integrity_ok else 'INCONSISTENTE'}",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )

    # -- nota estatística (item 39) -----------------------------------------------------------------
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*_GRAY)
    pdf.multi_cell(
        0, 4.5,
        "As informacoes estatisticas deste relatorio representam resultados historicos "
        "registrados durante o periodo analisado. Frequencias passadas nao constituem previsao "
        "de resultados futuros.",
    )

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(dest))
    return dest


def _number_distribution_table(pdf: _ReportPDF, snapshot, since_last: dict) -> None:
    col_w = [16, 22, 20, 22, 22, 30]
    headers = ["Numero", "Ocorrencias", "%", "Esperado", "Desvio", "Ultima ocorrencia"]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    for w, h in zip(col_w, headers):
        pdf.cell(w, 6, h, border=0, align="C", fill=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "", 8)
    for n in range(37):
        row = snapshot.number_distribution[n]
        pdf.set_text_color(*_NAVY)
        fill = n % 2 == 0
        pdf.set_fill_color(*_LIGHT_GRAY if fill else _WHITE)
        pdf.cell(col_w[0], 5.5, str(n), border=0, align="C", fill=True)
        pdf.cell(col_w[1], 5.5, str(row["observed_count"]), border=0, align="C", fill=True)
        pdf.cell(col_w[2], 5.5, f"{row['observed_pct']:.2f}%", border=0, align="C", fill=True)
        pdf.cell(col_w[3], 5.5, f"{row['expected_pct']:.2f}%", border=0, align="C", fill=True)
        deviation = row["deviation_pct"]
        pdf.set_text_color(*(_RED if deviation > 0 else _GRAY))
        pdf.cell(col_w[4], 5.5, f"{deviation:+.2f}p.p.", border=0, align="C", fill=True)
        pdf.set_text_color(*_NAVY)
        last_seen = since_last.get(n)
        label = f"{last_seen} giros atras" if last_seen is not None else "-"
        pdf.cell(col_w[5], 5.5, label, border=0, align="C", fill=True)
        pdf.ln(5.5)


def _hot_cold_table(pdf: _ReportPDF, title: str, entries: list[tuple[int, int]], unit_label: str) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    if not entries:
        pdf.cell(0, 5, "(sem dados suficientes)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return
    for number, value in entries:
        pdf.cell(0, 5, f"{number:>2}   {value} {unit_label}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
