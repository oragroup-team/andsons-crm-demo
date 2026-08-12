"""Turns an uploaded Excel/CSV file into a compact text summary an LLM can
use as grounding context. Never fabricates - if the file can't be parsed,
raises a clear error instead of silently producing an empty/misleading
summary.

Deliberately a *summary*, not the raw file dumped into the prompt: a real
spreadsheet can be thousands of rows, which would blow the context window
and bury the actual signal. Column names/dtypes + basic numeric stats + a
sample of rows is enough for the model to reason about "what does this data
say" without us pretending to feed it the whole sheet.
"""
import io
from typing import Tuple

import pandas as pd

MAX_SUMMARY_CHARS = 4000
MAX_SAMPLE_ROWS = 10

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xls")


def _load_dataframe(filename: str, content: bytes) -> pd.DataFrame:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        return pd.read_excel(io.BytesIO(content))
    raise ValueError(
        f"Unsupported file type for {filename!r} - only CSV and Excel (.xlsx/.xls) are supported."
    )


def summarize_file(filename: str, content: bytes) -> str:
    """Parse a CSV/Excel file and return a text summary: shape, columns,
    numeric-column stats, and a small sample of rows. Raises ValueError/
    pandas exceptions on anything that can't actually be parsed - the caller
    surfaces that to the user rather than proceeding with a made-up summary."""
    df = _load_dataframe(filename, content)

    lines = [f"Uploaded file: {filename}", f"Shape: {df.shape[0]} rows x {df.shape[1]} columns", ""]

    lines.append("Columns:")
    for col in df.columns:
        lines.append(f"- {col} ({df[col].dtype})")
    lines.append("")

    numeric_cols = df.select_dtypes(include="number").columns
    if len(numeric_cols) > 0:
        lines.append("Numeric column summary (sum / mean / min / max):")
        for col in numeric_cols:
            series = df[col].dropna()
            if series.empty:
                continue
            lines.append(
                f"- {col}: sum={series.sum():,.2f} mean={series.mean():,.2f} "
                f"min={series.min():,.2f} max={series.max():,.2f}"
            )
        lines.append("")

    sample = df.head(MAX_SAMPLE_ROWS)
    lines.append(f"Sample rows (first {len(sample)} of {len(df)}):")
    lines.append(sample.to_string(index=False))

    summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n... (truncated, file is larger than fits here)"
    return summary


def summarize_files(files: list) -> Tuple[str, list]:
    """files: list of (filename, content_bytes). Returns (combined_summary_text, errors) -
    one bad file doesn't block the others, but every failure is reported, never silently dropped."""
    summaries = []
    errors = []
    for filename, content in files:
        try:
            summaries.append(summarize_file(filename, content))
        except Exception as exc:  # noqa: BLE001 - reported to caller, not swallowed
            errors.append(f"{filename}: {exc}")
    return "\n\n---\n\n".join(summaries), errors
