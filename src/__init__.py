"""
src package
-----------
This (almost empty) file tells Python that the `src` folder is a *package*,
so other code can write `from src.config import settings`.

All of the assistant's logic lives in this package:
config -> llm_client -> schema/prompts -> sql_generator -> sql_validator
-> executor -> pipeline -> charts. The Streamlit UI (app.py) calls the pipeline.
"""
