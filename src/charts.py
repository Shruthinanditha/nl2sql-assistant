"""
charts.py - chooses and draws a chart for a query result, automatically.

WHY THIS FILE EXISTS
    Users ask questions, not "draw me a bar chart". So we look at the SHAPE
    of the result and pick the clearest view:

      one row, one number            -> "metric"  (a big number; a chart of one value is silly)
      a date/month column + a number -> "line"    (trend over time)
      a text column + a number       -> "bar"     (compare categories; <= 30 rows)
      anything else                  -> "table"   (the table is always shown anyway)

    Design rules (from our data-visualisation guidelines):
      - ONE measure per chart, ONE y-axis (never two scales on one chart).
      - One series = one colour (blue #2a78d6, contrast-checked for light and dark
        mode); no legend needed because the axis title names the measure.
      - Thin marks: 2px lines, rounded bar ends, faint gridlines.
      - Hover tooltips on every mark (Plotly's default, with clean formatting).

HOW IT FITS IN THE PIPELINE
    pipeline.ask() -> DataFrame -> [charts.choose_chart + build_figure] -> app.py
"""

import re
from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go

SERIES_COLOR = "#2a78d6"   # validated: passes contrast on light and dark surfaces
GRID_COLOR = "rgba(128, 128, 128, 0.18)"  # faint: works on light and dark themes
MAX_BAR_CATEGORIES = 30    # more bars than this are unreadable -> table only
MAX_MARKER_POINTS = 31     # show dots on a line only when there are few points

# Text values that look like dates: '2026-10' (month) or '2026-10-15' (day).
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


@dataclass
class ChartSpec:
    """
    The chart decision.

      kind   - "metric", "line", "bar" or "table"
      x      - column for the x-axis (category or date), if any
      y      - the numeric column to plot, if any
      reason - why this chart was chosen (shown as a caption; nice for demos)
    """

    kind: str
    x: str | None = None
    y: str | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Looking at the data
# ---------------------------------------------------------------------------
def _is_id_column(name: str) -> bool:
    """Ids are numbers but not measures: 'id', 'customer_id', 'order_id' are never plotted."""
    lowered = name.lower()
    return lowered == "id" or lowered.endswith("_id")


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    """Columns that hold measurable numbers (excluding id columns)."""
    return [
        column for column in df.columns
        if pd.api.types.is_numeric_dtype(df[column])
        and not pd.api.types.is_bool_dtype(df[column])
        and not _is_id_column(str(column))
    ]


def _date_columns(df: pd.DataFrame) -> list[str]:
    """Text columns where every non-empty value looks like 'YYYY-MM' or 'YYYY-MM-DD'."""
    date_columns = []
    for column in df.columns:
        if pd.api.types.is_numeric_dtype(df[column]):
            continue
        values = df[column].dropna().astype(str)
        if len(values) > 0 and values.map(lambda v: bool(DATE_PATTERN.match(v))).all():
            date_columns.append(column)
    return date_columns


def _text_columns(df: pd.DataFrame, exclude: list[str]) -> list[str]:
    """Non-numeric columns (categories such as city or product name)."""
    return [
        column for column in df.columns
        if column not in exclude and not pd.api.types.is_numeric_dtype(df[column])
    ]


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------
def choose_chart(df: pd.DataFrame | None) -> ChartSpec:
    """
    Decide how to show a result. See the module docstring for the rules.

    Input:  the result DataFrame (may be None or empty)
    Output: a ChartSpec
    """
    if df is None or df.empty:
        return ChartSpec("table", reason="No rows to chart.")

    numbers = _numeric_columns(df)
    if not numbers:
        return ChartSpec("table", reason="No numeric column to chart.")
    measure = numbers[0]  # one measure per chart (one y-axis)

    if len(df) == 1 and len(numbers) == 1:
        return ChartSpec("metric", y=measure, reason="A single value is shown as a number.")

    dates = _date_columns(df)
    if dates and len(df) >= 2:
        return ChartSpec("line", x=dates[0], y=measure, reason=f"'{dates[0]}' looks like dates, so a line shows the trend.")

    categories = _text_columns(df, exclude=dates)
    if categories and 2 <= len(df) <= MAX_BAR_CATEGORIES:
        return ChartSpec("bar", x=categories[0], y=measure, reason=f"Compares '{measure}' across '{categories[0]}'.")
    if categories and len(df) > MAX_BAR_CATEGORIES:
        return ChartSpec("table", reason=f"More than {MAX_BAR_CATEGORIES} rows: a table is easier to read than a chart.")

    return ChartSpec("table", reason="This result is best read as a table.")


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def pretty_label(column: str) -> str:
    """'total_revenue' -> 'Total revenue' (for axis titles and tooltips)."""
    text = str(column).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def format_number(value: float) -> str:
    """Thousands separators, and 2 decimals only when the value has decimals."""
    if float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _base_layout(figure: go.Figure, x_title: str, y_title: str) -> None:
    """Shared, quiet styling: no chart title (the page has one), faint grid, small margins."""
    figure.update_layout(
        margin={"l": 8, "r": 8, "t": 16, "b": 8},
        showlegend=False,       # one series: the axis title names it
        hoverlabel={"font_size": 13},
        bargap=0.35,            # thin bars with clear gaps between them
        height=400,
    )
    figure.update_xaxes(title_text=x_title, showgrid=False, zeroline=False)
    figure.update_yaxes(title_text=y_title, gridcolor=GRID_COLOR, zeroline=False)


def build_figure(df: pd.DataFrame, spec: ChartSpec) -> go.Figure | None:
    """
    Draw the chart described by `spec`. Returns None for "table" and "metric"
    (the UI shows those without Plotly).
    """
    if spec.kind == "line":
        return _line_chart(df, spec)
    if spec.kind == "bar":
        return _bar_chart(df, spec)
    return None


def _line_chart(df: pd.DataFrame, spec: ChartSpec) -> go.Figure:
    """Trend over time: a 2px line, with dots when there are few points."""
    data = df[[spec.x, spec.y]].dropna().copy()
    is_monthly = data[spec.x].astype(str).str.len().eq(7).all()  # 'YYYY-MM'
    # Real dates give a proper time axis (correct spacing, nice tick labels).
    data["_x"] = pd.to_datetime(data[spec.x].astype(str), format="%Y-%m" if is_monthly else "%Y-%m-%d")
    data = data.sort_values("_x")

    show_markers = len(data) <= MAX_MARKER_POINTS
    figure = go.Figure(
        go.Scatter(
            x=data["_x"],
            y=data[spec.y],
            mode="lines+markers" if show_markers else "lines",
            line={"color": SERIES_COLOR, "width": 2},
            marker={"size": 8, "color": SERIES_COLOR},
            customdata=data[spec.x].astype(str),
            hovertemplate=f"<b>%{{customdata}}</b><br>{pretty_label(spec.y)}: %{{y:,.2f}}<extra></extra>",
        )
    )
    _base_layout(figure, pretty_label(spec.x), pretty_label(spec.y))
    if is_monthly:
        figure.update_xaxes(tickformat="%b %Y", dtick="M1" if len(data) <= 18 else "M3")
    figure.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikedash="dot")
    return figure


def _bar_chart(df: pd.DataFrame, spec: ChartSpec) -> go.Figure:
    """
    Compare categories. Horizontal bars when labels are long or there are many
    bars (long names stay readable); the row order from the SQL is kept,
    because the query usually sorts deliberately (e.g. "top 5").
    """
    data = df[[spec.x, spec.y]].copy()
    data[spec.x] = data[spec.x].fillna("(unknown)").astype(str)
    longest_label = data[spec.x].str.len().max()
    horizontal = longest_label > 12 or len(data) > 8

    hover = f"<b>%{{{'y' if horizontal else 'x'}}}</b><br>{pretty_label(spec.y)}: %{{{'x' if horizontal else 'y'}:,.2f}}<extra></extra>"
    bar = go.Bar(
        x=data[spec.y] if horizontal else data[spec.x],
        y=data[spec.x] if horizontal else data[spec.y],
        orientation="h" if horizontal else "v",
        marker={"color": SERIES_COLOR, "cornerradius": 4},
        hovertemplate=hover,
    )
    figure = go.Figure(bar)
    if horizontal:
        _base_layout(figure, pretty_label(spec.y), "")
        # Plotly draws horizontal bars bottom-up; reverse so row 1 is on top.
        figure.update_yaxes(autorange="reversed", showgrid=False)
        figure.update_xaxes(showgrid=True, gridcolor=GRID_COLOR)
        figure.update_layout(height=max(260, 34 * len(data) + 80))
    else:
        _base_layout(figure, pretty_label(spec.x), pretty_label(spec.y))
    return figure
