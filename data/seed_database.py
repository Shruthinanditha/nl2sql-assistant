"""
seed_database.py - creates the sample SQLite database `data/shop.db`.

WHY THIS FILE EXISTS
    The assistant needs a realistic database to answer questions about.
    Instead of committing a binary .db file to git, we commit THIS script.
    Anyone can rebuild the exact same database by running it.

WHAT IT CREATES (an imaginary Indian online shop; money is in INR)
    customers      ~200 people; some never ordered, some have missing city/email
    products        40 products in 6 categories
    orders       3,200 orders over the last ~18 months (relative to TODAY),
                       with growth over time and a festive-season peak (Oct-Nov)
    order_items    1-4 lines per order; orders.total = sum of its lines
    users_private  a SENSITIVE table (password hashes, salaries). It exists
                   so we can prove the assistant can NEVER read it (allowlist).

REPRODUCIBILITY
    A fixed random seed (42) means the same customers, products and order
    *pattern* every run. Dates are calculated relative to the day you run the
    script, so "last month" always has data. Re-run it any time to refresh.

HOW IT FITS IN THE PIPELINE
    This is the data source. Later, schema.py reads its structure for the LLM,
    and executor.py runs the validated SQL against it (read-only).

HOW TO RUN (from the project root, venv activated)
    python data/seed_database.py
"""

import hashlib
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings for the generated data
# ---------------------------------------------------------------------------
RANDOM_SEED = 42                # fixed seed -> same data every run
NUM_CUSTOMERS = 200
NUM_CUSTOMERS_WITHOUT_ORDERS = 15
NUM_ORDERS = 3200
ORDER_HISTORY_DAYS = 540        # ~18 months of orders, ending today
SIGNUP_HISTORY_DAYS = 730       # customers signed up over the last ~2 years

DB_PATH = Path(__file__).resolve().parent / "shop.db"

# ---------------------------------------------------------------------------
# Reference data (all fictional)
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Bhavya", "Deepak", "Divya",
    "Gautam", "Harini", "Ishaan", "Janani", "Karthik", "Kavya", "Lakshmi",
    "Manoj", "Meera", "Naveen", "Nisha", "Pooja", "Pranav", "Priya", "Rahul",
    "Ramya", "Rohan", "Sanjay", "Sneha", "Suresh", "Swathi", "Tarun", "Uma",
    "Varun", "Vidya", "Vikram", "Yamini", "Zoya", "Farhan", "Joseph", "Mary",
    "Gurpreet",
]
LAST_NAMES = [
    "Sharma", "Iyer", "Reddy", "Nair", "Patel", "Gupta", "Krishnan", "Menon",
    "Rao", "Singh", "Das", "Pillai", "Bose", "Joshi", "Kumar", "Mehta",
    "Subramanian", "Verma", "Khan", "Fernandes", "Chatterjee", "Naidu",
    "Venkatesh", "Agarwal", "Kapoor",
]

# (city, country, weight). Weight = how common the city is among customers.
CITIES = [
    ("Chennai", "India", 18), ("Bengaluru", "India", 18), ("Mumbai", "India", 14),
    ("Delhi", "India", 12), ("Hyderabad", "India", 12), ("Pune", "India", 8),
    ("Kolkata", "India", 6), ("Coimbatore", "India", 5), ("Kochi", "India", 4),
    ("Singapore", "Singapore", 2), ("Dubai", "UAE", 2), ("London", "UK", 1),
]

# (product name, category, price in INR)
PRODUCTS = [
    # Electronics
    ("Wireless Earbuds", "Electronics", 2499.00),
    ("Bluetooth Speaker", "Electronics", 3299.00),
    ("Smart Watch", "Electronics", 6999.00),
    ("Power Bank 20000mAh", "Electronics", 1799.00),
    ("USB-C Charger 65W", "Electronics", 1499.00),
    ("Mechanical Keyboard", "Electronics", 4599.00),
    ("Wireless Mouse", "Electronics", 899.00),
    ("Noise Cancelling Headphones", "Electronics", 12999.00),
    # Books
    ("Learning SQL", "Books", 650.00),
    ("Python Crash Course", "Books", 899.00),
    ("Clean Code", "Books", 749.00),
    ("Atomic Habits", "Books", 499.00),
    ("The Psychology of Money", "Books", 399.00),
    ("Ponniyin Selvan Box Set", "Books", 1599.00),
    ("Data Science from Scratch", "Books", 950.00),
    # Home & Kitchen
    ("Pressure Cooker 5L", "Home & Kitchen", 2199.00),
    ("Non-Stick Tawa", "Home & Kitchen", 899.00),
    ("Mixer Grinder", "Home & Kitchen", 3899.00),
    ("Steel Water Bottle", "Home & Kitchen", 449.00),
    ("Filter Coffee Maker", "Home & Kitchen", 1299.00),
    ("Air Fryer", "Home & Kitchen", 6499.00),
    ("Cotton Bedsheet Set", "Home & Kitchen", 1499.00),
    # Clothing
    ("Cotton Kurta", "Clothing", 1199.00),
    ("Silk Saree", "Clothing", 5499.00),
    ("Denim Jeans", "Clothing", 1899.00),
    ("Running T-Shirt", "Clothing", 699.00),
    ("Winter Jacket", "Clothing", 3499.00),
    ("Formal Shirt", "Clothing", 1399.00),
    ("Ethnic Dupatta", "Clothing", 799.00),
    # Sports
    ("Yoga Mat", "Sports", 999.00),
    ("Cricket Bat (English Willow)", "Sports", 7999.00),
    ("Badminton Racket", "Sports", 1899.00),
    ("Running Shoes", "Sports", 4299.00),
    ("Dumbbell Set 10kg", "Sports", 2599.00),
    ("Football", "Sports", 899.00),
    # Beauty
    ("Herbal Face Wash", "Beauty", 249.00),
    ("Sunscreen SPF 50", "Beauty", 549.00),
    ("Hair Oil (Coconut)", "Beauty", 199.00),
    ("Perfume Gift Set", "Beauty", 2999.00),
    ("Kumkumadi Face Serum", "Beauty", 1199.00),
]

# Festive season (Diwali etc.) boosts sales in October and November.
MONTH_SEASONALITY = {1: 0.9, 2: 0.85, 3: 0.95, 4: 1.0, 5: 1.0, 6: 0.95,
                     7: 1.0, 8: 1.05, 9: 1.1, 10: 1.5, 11: 1.45, 12: 1.2}


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE customers (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    email        TEXT,               -- may be NULL (customer did not share it)
    city         TEXT,               -- may be NULL (unknown)
    country      TEXT    NOT NULL,
    signup_date  TEXT    NOT NULL    -- ISO date 'YYYY-MM-DD'
);

CREATE TABLE products (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    category  TEXT NOT NULL,
    price     REAL NOT NULL           -- current list price in INR
);

CREATE TABLE orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    order_date   TEXT    NOT NULL,   -- ISO date 'YYYY-MM-DD'
    status       TEXT    NOT NULL CHECK (status IN ('completed', 'cancelled', 'pending')),
    total        REAL    NOT NULL    -- INR, = SUM(quantity * unit_price) of its items
);

CREATE TABLE order_items (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  REAL    NOT NULL     -- price actually paid (may include a discount)
);

-- SENSITIVE: the assistant must NEVER be able to query this table.
CREATE TABLE users_private (
    id             INTEGER PRIMARY KEY,
    email          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    salary         REAL NOT NULL
);

-- Indexes make the common joins and date filters fast.
CREATE INDEX idx_orders_customer   ON orders(customer_id);
CREATE INDEX idx_orders_date       ON orders(order_date);
CREATE INDEX idx_items_order       ON order_items(order_id);
CREATE INDEX idx_items_product     ON order_items(product_id);
"""


# ---------------------------------------------------------------------------
# Data generation functions
# ---------------------------------------------------------------------------
def make_customers(rng: random.Random, today: date) -> list[tuple]:
    """
    Generate customer rows.

    Inputs:  rng   - the seeded random generator (so results are reproducible)
             today - the reference date; signup dates are in the past 2 years
    Output:  list of tuples (id, name, email, city, country, signup_date)
    """
    city_names = [c for c in CITIES]
    city_weights = [c[2] for c in CITIES]

    customers = []
    used_names: set[str] = set()
    for customer_id in range(1, NUM_CUSTOMERS + 1):
        # Pick a name that is not used yet, so names are unique (easier to read results).
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break

        # example.com is reserved for examples, so these emails can never be real.
        email = name.lower().replace(" ", ".") + "@example.com"
        city, country, _weight = rng.choices(city_names, weights=city_weights)[0]
        signup_date = today - timedelta(days=rng.randint(10, SIGNUP_HISTORY_DAYS))

        # Messy reality: a few customers have missing data (NULL in the database).
        if rng.random() < 0.03:
            city = None
        if rng.random() < 0.02:
            email = None

        customers.append((customer_id, name, email, city, country, signup_date.isoformat()))
    return customers


def make_products() -> list[tuple]:
    """Return product rows (id, name, category, price). Ids start at 1."""
    return [(i, name, category, price) for i, (name, category, price) in enumerate(PRODUCTS, start=1)]


def day_weight(day: date, first_day: date) -> float:
    """
    How likely an order is to happen on a given day.

    The weight grows over time (the shop is growing, up to +60% by today),
    and it is multiplied by a monthly seasonality factor (festive peak).
    """
    progress = (day - first_day).days / ORDER_HISTORY_DAYS   # 0.0 at the start, 1.0 today
    growth = 1.0 + 0.6 * progress
    return growth * MONTH_SEASONALITY[day.month]


def pick_status(rng: random.Random, order_day: date, today: date) -> str:
    """
    Choose an order status that is realistic for the order's age.

    Recent orders (last 10 days) may still be 'pending'. Older orders are
    almost always either completed or cancelled.
    """
    if (today - order_day).days <= 10:
        return rng.choices(["pending", "completed", "cancelled"], weights=[45, 45, 10])[0]
    return rng.choices(["completed", "cancelled"], weights=[90, 10])[0]


def make_orders_and_items(
    rng: random.Random,
    today: date,
    customers: list[tuple],
    products: list[tuple],
) -> tuple[list[tuple], list[tuple]]:
    """
    Generate orders and their order items together.

    They are generated together because each order's `total` must equal the
    sum of its items (quantity * unit_price). That keeps the data consistent.

    Output: (orders, order_items) as lists of tuples.
    """
    first_day = today - timedelta(days=ORDER_HISTORY_DAYS)
    all_days = [first_day + timedelta(days=i) for i in range(ORDER_HISTORY_DAYS + 1)]

    # Messy reality: some customers never place an order.
    customer_ids = [c[0] for c in customers]
    no_order_ids = set(rng.sample(customer_ids, NUM_CUSTOMERS_WITHOUT_ORDERS))
    buying_customers = [c for c in customers if c[0] not in no_order_ids]

    # Some customers buy much more than others (loyal customers).
    # paretovariate gives a few large values and many small ones, like real shops.
    customer_weights = [rng.paretovariate(1.5) for _ in buying_customers]

    # Some products are more popular. Cheaper products sell more often.
    product_weights = [rng.uniform(0.5, 2.0) * (3000 / (p[3] + 500)) for p in products]

    # Step 1: decide (date, customer) for every order.
    # We pick the DATE first, so the number of orders per month follows our
    # growth + seasonality curve. (Picking the customer first would push most
    # orders into recent months, because recent days are open to every customer.)
    all_day_weights = [day_weight(d, first_day) for d in all_days]
    order_plans = []
    for _ in range(NUM_ORDERS):
        order_day = rng.choices(all_days, weights=all_day_weights)[0]
        # A customer can only order on or after their signup date.
        eligible = [
            (customer, weight)
            for customer, weight in zip(buying_customers, customer_weights)
            if date.fromisoformat(customer[5]) <= order_day
        ]
        eligible_customers = [pair[0] for pair in eligible]
        eligible_weights = [pair[1] for pair in eligible]
        customer = rng.choices(eligible_customers, weights=eligible_weights)[0]
        order_plans.append((order_day, customer[0]))

    # Sort by date so order ids increase over time (as in a real system).
    order_plans.sort()

    # Step 2: create the items and compute each order's total.
    orders, items = [], []
    item_id = 1
    for order_id, (order_day, customer_id) in enumerate(order_plans, start=1):
        num_lines = rng.choices([1, 2, 3, 4], weights=[50, 30, 15, 5])[0]
        # Pick distinct products for the lines of this order.
        chosen_products: list[tuple] = []
        while len(chosen_products) < num_lines:
            product = rng.choices(products, weights=product_weights)[0]
            if product not in chosen_products:
                chosen_products.append(product)

        order_total = 0.0
        for product in chosen_products:
            quantity = rng.choices([1, 2, 3], weights=[75, 20, 5])[0]
            unit_price = product[3]
            # Sometimes a discount was applied, so unit_price differs from the list price.
            if rng.random() < 0.15:
                unit_price = round(unit_price * 0.9, 2)
            items.append((item_id, order_id, product[0], quantity, unit_price))
            order_total += quantity * unit_price
            item_id += 1

        status = pick_status(rng, order_day, today)
        orders.append((order_id, customer_id, order_day.isoformat(), status, round(order_total, 2)))

    return orders, items


def make_users_private(rng: random.Random) -> list[tuple]:
    """
    Generate the SENSITIVE table rows (fake staff accounts).

    The password hashes are SHA-256 of fake passwords. Real systems would use a slow
    hash like bcrypt; here we only need something that *looks* sensitive.
    """
    rows = []
    for user_id in range(1, 11):
        email = f"staff{user_id}@example.com"
        password_hash = hashlib.sha256(f"fake-password-{user_id}".encode()).hexdigest()
        salary = float(rng.randrange(600_000, 3_000_000, 10_000))  # INR per year
        rows.append((user_id, email, password_hash, salary))
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def create_database(db_path: Path = DB_PATH, today: date | None = None) -> dict[str, int]:
    """
    Delete any old database and build a fresh one.

    Inputs:  db_path - where to write the SQLite file
             today   - reference date (defaults to the real today); tests may pass a fixed date
    Output:  dict of {table_name: row_count}, so the caller can show/verify counts
    """
    today = today or date.today()
    rng = random.Random(RANDOM_SEED)

    # Start fresh. On Windows, the file cannot be deleted while another program
    # (for example DBeaver) has it open, so we explain that instead of crashing.
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            raise SystemExit(
                f"Cannot replace {db_path.name}: it is open in another program "
                "(probably DBeaver). Disconnect it there (right-click the connection -> "
                "Disconnect) and run this script again."
            )

    customers = make_customers(rng, today)
    products = make_products()
    orders, items = make_orders_and_items(rng, today, customers, products)
    users_private = make_users_private(rng)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")  # make SQLite enforce REFERENCES
        connection.executescript(SCHEMA_SQL)
        # executemany with ? placeholders: values are passed separately from the SQL,
        # which is the safe way to insert data (no string-building of SQL).
        connection.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers)
        connection.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", products)
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)
        connection.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", items)
        connection.executemany("INSERT INTO users_private VALUES (?, ?, ?, ?)", users_private)
        connection.commit()

        counts = {}
        for table in ["customers", "products", "orders", "order_items", "users_private"]:
            # Table names come from our own fixed list above, never from user input.
            counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        connection.close()


def main() -> None:
    """Build the database and print a summary you can compare with DBeaver."""
    counts = create_database()
    print(f"Created {DB_PATH}")
    print(f"Reference date (today): {date.today().isoformat()}\n")
    print(f"{'table':<15}{'rows':>8}")
    print("-" * 23)
    for table, count in counts.items():
        print(f"{table:<15}{count:>8}")


if __name__ == "__main__":
    main()
