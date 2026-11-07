# DBeaver Guide (for complete beginners)

DBeaver is a free desktop app for working with databases. You can browse tables, look at data, run SQL,
draw diagrams and export results. In this project, it's your **independent second opinion**: whenever the AI
writes a SQL query, you can paste it into DBeaver, run it yourself, and check that the answer is right.

> Throughout this guide, the database file is:
> `D:\Projects\Natural Language SQL Assistant\data\shop.db`
> If it doesn't exist yet, create it first (from the project root, with the venv activated):
> `python data/seed_database.py`

---

## 1. Check the installation

DBeaver Community is already installed on this laptop (`C:\Users\<you>\AppData\Local\DBeaver\`).
Open it from the Start menu: type **DBeaver** and press Enter.

- If it asks to **create a sample database**, click **No**.
- If it asks about **usage statistics** or **tips**, close those windows.

(To install it on another computer: download **DBeaver Community** from https://dbeaver.io/download/,
or run `winget install dbeaver.dbeaver` in PowerShell.)

The window has three main areas:

```
┌────────────────────┬───────────────────────────────────────────┐
│ Database Navigator │  Editor area (tables, SQL scripts)        │
│ (tree of your      │                                           │
│  connections)      ├───────────────────────────────────────────┤
│                    │  Results panel (query output)             │
└────────────────────┴───────────────────────────────────────────┘
```

---

## 2. Connect to `shop.db`

1. Click the **New Database Connection** button. It's the plug icon with a `+` in the top-left of the toolbar.
   You can also use the menu **Database → New Database Connection**.
2. In the list of databases, type `sqlite` in the search box, select **SQLite**, and click **Next**.
3. Next to **Path**, click **Browse…**, go to
   `D:\Projects\Natural Language SQL Assistant\data\`, select **shop.db**, and click **Open**.
4. Click **Test Connection…** in the bottom-left.
   - The first time, DBeaver says the **driver files are missing** and offers to download them.
     A *driver* is the small piece of software that lets DBeaver talk to a particular type of database.
     Click **Download** and wait a few seconds.
   - You should then see **"Connected"**. Click **OK**.
5. Recommended: make the connection **read-only**, so you can't change the data by accident.
   Click **Connection details (name, type, …)** (or go back to the *General* section), tick
   **Read-only connection**, and give it the name `shop.db (read-only)`.
6. Click **Finish**. The connection appears in the **Database Navigator** on the left.

---

## 3. Browse the tables

1. In the Database Navigator, click the arrow next to your connection, then **Tables**.
2. You should see five tables: `customers`, `order_items`, `orders`, `products`, `users_private`.
3. Expand a table and then **Columns** to see its column names and types.

> `users_private` contains sensitive data (password hashes, salaries). **DBeaver** can see it because you are
> the database owner. The **AI assistant** will be blocked from it by an allowlist (Phase 5). This table exists
> to prove that.

---

## 4. View data

- **Double-click** a table (for example `orders`) and open the **Data** tab at the top of the editor.
- You can sort by clicking a column header, and filter by typing a condition in the filter box above the grid,
  for example `status = 'cancelled'`.
- The **Properties** tab shows the table's structure, and the **DDL** tab shows the `CREATE TABLE`
  statement that built it.

---

## 5. Run your first SELECT

1. Select your connection in the navigator, then open the menu **SQL Editor → New SQL script**
   (shortcut **Ctrl + ]**). An empty editor tab opens, connected to `shop.db`.
2. Type:

   ```sql
   SELECT * FROM customers LIMIT 10;
   ```

3. Put the cursor on that line and press **Ctrl + Enter**. This runs the *statement under the cursor*.
   The results appear in the panel below.
4. Useful shortcuts:
   | Shortcut | What it does |
   |---|---|
   | **Ctrl + Enter** | Run the statement under the cursor |
   | **Alt + X** | Run the whole script (every statement, each with its own result tab) |
   | **Ctrl + Space** | Autocomplete table and column names |
   | **Ctrl + Shift + F** | Format (pretty-print) the SQL |

---

## 6. View the ER diagram

An **ER (Entity-Relationship) diagram** draws the tables as boxes, with lines for the relationships between them
(foreign keys).

- In the navigator, right-click **Tables** (or the connection) and choose **View Diagram**.
  You can also double-click a table and open its **ER Diagram** tab.
- You should see `orders → customers`, `order_items → orders` and `order_items → products`.
  `users_private` stands alone with no relationships.

Reading it: one customer has **many** orders, one order has **many** order items, and each order item points to
**one** product. That is why "revenue per product" needs a join through `order_items`.

---

## 7. Export a result to CSV

1. Run a query (for example exercise 2 below).
2. Right-click inside the results grid and choose **Export data…**.
3. Choose **CSV**, then click **Next** until you reach **Output**. Pick a folder (for example your Desktop) and a file name.
4. Click **Proceed**. Open the file in Excel to check it.

---

## 8. Using DBeaver to double-check the AI's SQL

From Phase 7, the app shows the SQL it generated in a **"Show generated SQL"** panel. To verify an answer:

1. Copy the SQL from the app and paste it into a DBeaver SQL editor.
2. Press **Ctrl + Enter** and compare the result with the app's table. It should be identical.
3. **Write your own version** of the query for the same question and compare the results.
   If they differ, find out which is right. Common reasons:
   - Did one of you include **cancelled** orders in revenue?
   - What does **"last month"** mean? The whole previous calendar month, or the last 30 days?
   - **Ties**: in a "top 5", two customers may have the same total.
   - **NULLs**: `city = 'Chennai'` silently skips customers whose city is NULL.
4. Tip: to compare two queries exactly, run `(query A) EXCEPT (query B)`. If it returns no rows, then every row of A
   is also in B. Run it the other way round as well.

Also: if you re-run `python data/seed_database.py` while DBeaver is connected, Windows will refuse to replace
the file. In DBeaver, **right-click the connection → Disconnect** first. After re-seeding, click **Refresh** (F5).

---

## 9. Practice exercises (10)

Try each one yourself first, then expand the answer. Exact numbers depend on the day you ran the seed script,
because dates are relative to "today". The *shape* of the result is given so you can check yourself.

SQLite date tips: dates are stored as text `'YYYY-MM-DD'`. Useful functions:
`date('now')` (today), `date('now', 'start of month')`, `date('now', 'start of month', '-1 month')`,
`date('now', 'start of year')`, and `strftime('%Y-%m', order_date)` (gives the year and month, for example `2026-10`).

### Exercise 1: Simple filter
List the customers who live in **Chennai** (id, name, email, signup date), sorted by name.

<details><summary>Answer</summary>

```sql
SELECT id, name, email, signup_date
FROM customers
WHERE city = 'Chennai'
ORDER BY name;
```
About 30-35 rows.
</details>

### Exercise 2: GROUP BY
How many orders are there per **status**?

<details><summary>Answer</summary>

```sql
SELECT status, COUNT(*) AS order_count
FROM orders
GROUP BY status
ORDER BY order_count DESC;
```
3 rows: `completed` (the vast majority), `cancelled` (~10%), `pending` (only recent orders).
</details>

### Exercise 3: Top-N
What are the **5 most expensive products**?

<details><summary>Answer</summary>

```sql
SELECT name, category, price
FROM products
ORDER BY price DESC
LIMIT 5;
```
Top row: Noise Cancelling Headphones, 12999.
</details>

### Exercise 4: LEFT JOIN to find missing data
Which customers have **never placed an order**?

<details><summary>Answer</summary>

```sql
SELECT c.id, c.name, c.city, c.signup_date
FROM customers AS c
LEFT JOIN orders AS o ON o.customer_id = c.id
WHERE o.id IS NULL
ORDER BY c.signup_date;
```
A `LEFT JOIN` keeps every customer, even those without a matching order. For those customers, the order columns
are `NULL`, and that is what `WHERE o.id IS NULL` picks out. About 20-25 rows.
</details>

### Exercise 5: NULL handling
Count the customers per city. Customers with **no city** should appear as `Unknown`.

<details><summary>Answer</summary>

```sql
SELECT COALESCE(city, 'Unknown') AS city, COUNT(*) AS customer_count
FROM customers
GROUP BY COALESCE(city, 'Unknown')
ORDER BY customer_count DESC;
```
`COALESCE(a, b)` returns the first value that is not NULL. Without it, the missing cities would show as an empty
`NULL` group.
</details>

### Exercise 6: Date filter + monthly grouping
What is the **revenue per month this year**? Count only completed orders.

<details><summary>Answer</summary>

```sql
SELECT strftime('%Y-%m', order_date) AS month,
       ROUND(SUM(total), 2)          AS revenue
FROM orders
WHERE status = 'completed'
  AND order_date >= date('now', 'start of year')
GROUP BY month
ORDER BY month;
```
One row per month from January up to the current month. October is the peak (festive season).
</details>

### Exercise 7: JOIN + date range + top-N
Who were the **top 5 customers by spending last month** (completed orders only)?

<details><summary>Answer</summary>

```sql
SELECT c.name, c.city,
       COUNT(o.id)          AS orders,
       ROUND(SUM(o.total), 2) AS total_spent
FROM orders AS o
JOIN customers AS c ON c.id = o.customer_id
WHERE o.status = 'completed'
  AND o.order_date >= date('now', 'start of month', '-1 month')
  AND o.order_date <  date('now', 'start of month')
GROUP BY c.id, c.name, c.city
ORDER BY total_spent DESC
LIMIT 5;
```
"Last month" here means the full previous calendar month: from the 1st of last month up to, but not including,
the 1st of this month. This is exactly the kind of interpretation question the AI must get right.
</details>

### Exercise 8: Three-table join + HAVING
What is the **revenue per product category**, showing only categories with more than ₹10,00,000 (10 lakh)?

<details><summary>Answer</summary>

```sql
SELECT p.category,
       SUM(oi.quantity)                          AS units_sold,
       ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
FROM order_items AS oi
JOIN orders   AS o ON o.id = oi.order_id
JOIN products AS p ON p.id = oi.product_id
WHERE o.status = 'completed'
GROUP BY p.category
HAVING SUM(oi.quantity * oi.unit_price) > 1000000
ORDER BY revenue DESC;
```
`WHERE` filters rows **before** grouping. `HAVING` filters groups **after** grouping. We use `unit_price`
(the price actually paid, which may include a discount), not `products.price` (today's list price).
</details>

### Exercise 9: Window function (RANK)
**Rank the products within each category** by the number of units sold.

<details><summary>Answer</summary>

```sql
SELECT category, name, units_sold,
       RANK() OVER (PARTITION BY category ORDER BY units_sold DESC) AS rank_in_category
FROM (
    SELECT p.category, p.name, SUM(oi.quantity) AS units_sold
    FROM order_items AS oi
    JOIN orders   AS o ON o.id = oi.order_id
    JOIN products AS p ON p.id = oi.product_id
    WHERE o.status = 'completed'
    GROUP BY p.id, p.category, p.name
)
ORDER BY category, rank_in_category;
```
`PARTITION BY category` restarts the ranking for each category. Unlike `GROUP BY`, a window function keeps every
row. 40 rows, one per product.
</details>

### Exercise 10: Window function (running total)
Show the **monthly revenue this year with a running total**.

<details><summary>Answer</summary>

```sql
SELECT month, revenue,
       ROUND(SUM(revenue) OVER (ORDER BY month), 2) AS running_total
FROM (
    SELECT strftime('%Y-%m', order_date) AS month,
           ROUND(SUM(total), 2)          AS revenue
    FROM orders
    WHERE status = 'completed'
      AND order_date >= date('now', 'start of year')
    GROUP BY month
)
ORDER BY month;
```
`SUM(...) OVER (ORDER BY month)` adds up every month so far. The last row's running total equals this year's
total revenue.
</details>

---

## 10. Sanity checks to run after seeding

These should match what `python data/seed_database.py` printed:

```sql
SELECT 'customers'     AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL SELECT 'products',      COUNT(*) FROM products
UNION ALL SELECT 'orders',        COUNT(*) FROM orders
UNION ALL SELECT 'order_items',   COUNT(*) FROM order_items
UNION ALL SELECT 'users_private', COUNT(*) FROM users_private;
```

Expected: customers 200, products 40, orders 3200, order_items 5619, users_private 10.
