"""
check_setup.py - verifies that the project environment is installed correctly.

WHY THIS FILE EXISTS
    After installing packages, the quickest way to prove "everything is set up"
    is to import every package and print its version. If something is missing,
    this script tells you exactly which one and how to fix it.

HOW TO RUN (from the project root, with the venv activated)
    python scripts/check_setup.py
"""

import sys
from importlib import import_module, metadata
from pathlib import Path

# Make the project root importable so `import src.config` works when this
# script is run from the scripts/ folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# (name used in `import`, name of the package on PyPI)
# They differ sometimes: you `pip install python-dotenv` but `import dotenv`.
PACKAGES = [
    ("streamlit", "streamlit"),
    ("pandas", "pandas"),
    ("plotly", "plotly"),
    ("sqlglot", "sqlglot"),
    ("dotenv", "python-dotenv"),
    ("yaml", "pyyaml"),
    ("pytest", "pytest"),
    ("google.genai", "google-genai"),
]


def check_packages() -> bool:
    """
    Try to import each package and print its installed version.

    Output: True if every package imported, False if any failed.
    """
    all_ok = True
    for import_name, pypi_name in PACKAGES:
        try:
            import_module(import_name)
            version = metadata.version(pypi_name)
            print(f"  OK       {pypi_name:<15} {version}")
        except Exception as error:  # broad on purpose: report, don't crash
            all_ok = False
            print(f"  MISSING  {pypi_name:<15} ({error.__class__.__name__}: {error})")
    return all_ok


def main() -> None:
    """Run all checks and print a final verdict."""
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")

    # sys.prefix differs from sys.base_prefix only inside a virtual environment.
    in_venv = sys.prefix != sys.base_prefix
    print(f"Inside virtual environment: {'yes' if in_venv else 'NO - activate .venv first!'}")

    print("\nPackages:")
    packages_ok = check_packages()

    print("\nSettings from .env (secrets are masked):")
    from src.config import describe_settings  # imported here: needs dotenv installed

    print(describe_settings())

    print()
    if packages_ok and in_venv:
        print("RESULT: setup looks good.")
    else:
        print("RESULT: problems found - see above. Try: pip install -r requirements.txt")


if __name__ == "__main__":
    main()
