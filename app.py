"""
app.py - the web user interface (Streamlit).

WHY THIS FILE EXISTS
    It lets anyone use the assistant in a browser: type a question, see the
    SQL the AI wrote, the result table, an automatic chart, and a clear
    explanation when a question is blocked or cannot be answered.

HOW STREAMLIT WORKS (important for reading this file)
    Streamlit re-runs this WHOLE script from top to bottom every time the user
    clicks or types something. So:
      - anything that must survive a click (the last result, the history) is
        kept in `st.session_state`, a dictionary that lives for the browser session;
      - slow things that never change (the LLM client, the schema) are created
        once with @st.cache_resource;
      - we only call the LLM when a question is actually submitted, and we draw
        the page from session_state otherwise (so a click on "Download CSV"
        does not cost a new LLM call).

HOW IT FITS IN THE PIPELINE
    [app.py] -> pipeline.ask(question) -> PipelineResult -> charts -> screen
    The UI contains no security logic: it only displays what the (tested)
    pipeline returns.

RUN
    streamlit run app.py
"""

import streamlit as st

# set_page_config must be the first Streamlit command.
st.set_page_config(page_title="NL to SQL Assistant", page_icon="🔎", layout="wide")

from src.charts import build_figure, choose_chart, format_number, pretty_label  # noqa: E402
from src.executor import execute_query  # noqa: E402
from src.llm_client import LLMError, get_llm_client  # noqa: E402
from src.pipeline import (  # noqa: E402
    STATUS_BLOCKED,
    STATUS_CANNOT_ANSWER,
    STATUS_SUCCESS,
    PipelineResult,
    ask,
)
from src.schema import get_schema  # noqa: E402
from src.sql_validator import validate_sql  # noqa: E402

# Example questions shown as buttons. The last two show the "safety" behaviour.
EXAMPLE_QUESTIONS = [
    "Show me last month's top 5 customers by spending",
    "What is the revenue by month this year?",
    "How many orders are there in each status?",
    "Revenue by product category this year",
    "What is the average delivery time per city?",
    "Delete all customers",
]

STATUS_ICONS = {
    STATUS_SUCCESS: "✅",
    STATUS_BLOCKED: "🛡️",
    STATUS_CANNOT_ANSWER: "🤷",
}
MAX_HISTORY = 10


# ---------------------------------------------------------------------------
# Cached resources (created once per server, shared by all reruns)
# ---------------------------------------------------------------------------
@st.cache_resource
def load_llm():
    """Create the LLM client once (it holds the API connection)."""
    return get_llm_client()


@st.cache_resource
def load_schema():
    """Read the database schema + allowlist once."""
    return get_schema()


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def init_session_state() -> None:
    """Create the session_state entries the first time the page loads."""
    st.session_state.setdefault("history", [])           # list of {question, status, rows}
    st.session_state.setdefault("last_result", None)     # the PipelineResult on screen
    st.session_state.setdefault("pending_question", None)  # set by example/history buttons
    st.session_state.setdefault("question_input", "")


def queue_question(question: str) -> None:
    """
    Button callback: put a question in the text box and mark it to be asked.

    Callbacks run BEFORE the script re-runs, which is the only moment we are
    allowed to change a widget's value through session_state.
    """
    st.session_state.question_input = question
    st.session_state.pending_question = question


def clear_history() -> None:
    """Button callback: forget the history and the result on screen."""
    st.session_state.history = []
    st.session_state.last_result = None


# ---------------------------------------------------------------------------
# Drawing the result
# ---------------------------------------------------------------------------
def render_sql_panel(result: PipelineResult) -> None:
    """The expandable 'Show generated SQL' panel."""
    with st.expander("Show generated SQL"):
        if result.executed_sql:
            st.caption("SQL that was run (rebuilt by the safety validator, with LIMIT enforced):")
            st.code(result.executed_sql, language="sql")
        if result.generated_sql:
            st.caption("SQL as written by the AI:")
            st.code(result.generated_sql, language="sql")
        if result.repaired:
            st.caption("The AI's first query failed, so the error was sent back to it once and it fixed the query.")


def render_success(result: PipelineResult) -> None:
    """Show a successful answer: chart (or big number), table, download, SQL."""
    df = result.dataframe
    spec = choose_chart(df)

    if spec.kind == "metric":
        value = df[spec.y].iloc[0]
        other_columns = [c for c in df.columns if c != spec.y]
        context = " · ".join(f"{pretty_label(c)}: {df[c].iloc[0]}" for c in other_columns)
        st.metric(label=pretty_label(spec.y), value=format_number(value), help=context or None, border=True)
    else:
        figure = build_figure(df, spec)
        if figure is not None:
            st.plotly_chart(figure, key="result_chart")
            st.caption(f"Chart chosen automatically: {spec.reason}")

    if result.truncated:
        st.warning(f"Only the first {result.row_count} rows are shown (row limit).")
    st.dataframe(df, hide_index=True)

    st.download_button(
        "⬇️ Download CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="query_result.csv",
        mime="text/csv",
        on_click="ignore",  # no page re-run needed for a download
    )
    render_sql_panel(result)


def render_result(result: PipelineResult) -> None:
    """Show any PipelineResult, with a clear message for every status."""
    st.subheader(result.question)

    if result.status == STATUS_SUCCESS:
        render_success(result)
    elif result.status == STATUS_BLOCKED:
        st.error(f"🛡️ **Blocked for safety.** {result.message}")
        st.caption(f"Failed check: `{result.validation.failed_check}`. The query was NOT run.")
        render_sql_panel(result)
    elif result.status == STATUS_CANNOT_ANSWER:
        st.info(f"🤷 **I can't answer that from this database.** {result.message}")
        st.caption("Try rephrasing, or pick one of the example questions above.")
    else:
        st.warning(f"⚠️ {result.message}")
        if result.generated_sql:
            render_sql_panel(result)

    t = result.timings
    details = (
        f"⏱️ {t['total']:.1f} s total · AI {t['llm']:.1f} s · safety checks {t['validation'] * 1000:.0f} ms"
        f" · database {t['execution'] * 1000:.0f} ms"
    )
    if result.status == STATUS_SUCCESS:
        details = f"{result.row_count} row(s) · " + details
    if result.repaired:
        details += " · self-repaired"
    st.caption(details)


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------
def render_sidebar(llm) -> None:
    """Sidebar: model info and the clickable question history."""
    with st.sidebar:
        st.header("History")
        history = st.session_state.history
        if not history:
            st.caption("Your questions will appear here.")
        for index, item in enumerate(reversed(history)):
            icon = STATUS_ICONS.get(item["status"], "⚠️")
            st.button(
                f"{icon} {item['question']}",
                key=f"history_{index}",
                on_click=queue_question,
                args=(item["question"],),
                width="stretch",
                help="Ask this again",
            )
        if history:
            st.button("Clear history", on_click=clear_history, type="tertiary")

        st.divider()
        st.caption(f"Model: **{llm.provider_name} / {llm.model}**")
        st.caption("Data: a synthetic Indian online shop (amounts in INR).")


def render_how_it_works() -> None:
    """Short explanation for demo viewers."""
    with st.expander("ℹ️ How it works & safety"):
        st.markdown(
            """
**Pipeline:** your question + the allowed database schema → **AI (LLM)** writes one SQL query →
**safety validator** checks it → **read-only database** runs it → table + automatic chart.

**Six layers of safety** (the AI's SQL is treated as untrusted input):
1. **Prompt rules**: the AI is told to write one read-only SELECT using only allowed tables.
2. **Syntax-tree validation** (sqlglot): exactly one statement, SELECT only, safe functions only, no recursion.
3. **Allowlist**: only 4 tables and specific columns exist for the AI. Sensitive data (`users_private`, emails) is invisible and blocked.
4. **Read-only database + SQLite authorizer**: even a validator bug cannot change data.
5. **Limits**: at most 500 rows and 5 seconds per query.
6. **Transparency**: the SQL is always shown, so you can check it.

Try the **Safety lab** below to see attacks being blocked.
"""
        )


def render_safety_lab() -> None:
    """
    Let demo viewers type SQL directly (no AI) and see the validator's verdict.
    The SQL goes through exactly the same validator and read-only executor.
    """
    with st.expander("🧪 Safety lab: test the validator with your own SQL (no AI involved)"):
        st.caption("Try an attack such as `DROP TABLE orders`, `SELECT 1; DELETE FROM orders`, "
                   "or `SELECT * FROM sqlite_master`. Safe queries are run read-only.")
        sql = st.text_area(
            "SQL to check",
            value="SELECT name FROM customers UNION SELECT password_hash FROM users_private",
            key="lab_sql",
            height=90,
        )
        if st.button("Check & run", key="lab_run"):
            verdict = validate_sql(sql, schema=load_schema())
            if not verdict.is_safe:
                st.error(f"🛡️ **Blocked** (`{verdict.failed_check}`): {verdict.reason}")
                return
            st.success("✅ Passed all safety checks. Running it read-only:")
            st.code(verdict.clean_sql, language="sql")
            execution = execute_query(verdict.clean_sql, schema=load_schema())
            if execution.error:
                st.warning(execution.error)
            else:
                st.dataframe(execution.dataframe, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Build the page."""
    init_session_state()

    # Fail with a helpful message if the setup is incomplete.
    try:
        llm = load_llm()
        schema = load_schema()
    except LLMError as error:
        st.error(f"LLM setup problem: {error}")
        st.stop()
    except FileNotFoundError as error:
        st.error(f"{error}")
        st.stop()

    st.title("🔎 Natural-Language SQL Assistant")
    st.caption("Ask a question about the shop's data in plain English. "
               "The AI writes SQL, safety checks verify it, and the result is shown as a table and chart.")

    # Example question buttons, in two rows of three.
    st.write("**Try an example:**")
    columns = st.columns(3)
    for index, example in enumerate(EXAMPLE_QUESTIONS):
        columns[index % 3].button(
            example, key=f"example_{index}", on_click=queue_question, args=(example,), width="stretch"
        )

    # The question form. A form only re-runs the script when "Ask" is clicked,
    # not on every key press.
    with st.form("ask_form"):
        st.text_input(
            "Your question",
            key="question_input",
            placeholder="e.g. Which city had the most orders last month?",
        )
        submitted = st.form_submit_button("Ask", type="primary", key="ask")

    question = st.session_state.pending_question or (st.session_state.question_input if submitted else None)
    st.session_state.pending_question = None

    if question and question.strip():
        with st.spinner("Thinking… (writing SQL, checking it, running it)"):
            result = ask(question.strip(), llm=llm, schema=schema)
        st.session_state.last_result = result
        st.session_state.history.append(
            {"question": result.question, "status": result.status, "rows": result.row_count}
        )
        st.session_state.history = st.session_state.history[-MAX_HISTORY:]
    elif submitted:
        st.warning("Please type a question first.")

    # Drawn AFTER the question is processed, so the new question already
    # appears in the history (the sidebar can be filled at any point).
    render_sidebar(llm)

    if st.session_state.last_result is not None:
        st.divider()
        render_result(st.session_state.last_result)

    st.divider()
    render_how_it_works()
    render_safety_lab()


main()
