"""Seed the andSons demo SQLite database with synthetic CRM data.

Run: python3 seed_data.py
Creates ./andsons.db (or $DATABASE_PATH) and
backend/synthetic_templates/golden_examples.json.

Flow taxonomy, products, and compliance facts are grounded in the real
andSons CRM knowledge base (see CRM_Email_Generation_Data/&SONS CRM
Knowledge) - see backend/flows.py for the flow definitions.
"""
import _vendor_path  # noqa: F401  - must be first, see _vendor_path.py

import json
import os
import random
import sqlite3
from datetime import datetime, timedelta

from faker import Faker

from flows import FLOWS

fake = Faker()
Faker.seed(42)
random.seed(42)

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "andsons.db"))
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "synthetic_templates")

FLOW_SLUGS = [f["slug"] for f in FLOWS]

# Real WhatsApp customer-service link + real registered address, per
# 02-Compliance-Claims.md rule 5 ("Required in every email"). Footers must
# NOT contain "Manage My Delivery Schedule" or "Cancel Anytime" links.
WHATSAPP_CS_LINK = "https://api.whatsapp.com/message/VX2SIFBLE7ECI1?autoload=1&app_absent=0"
SENDER_ADDRESS = "andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522"
FOOTER_TEXT = f"WhatsApp customer service: {WHATSAPP_CS_LINK}\n{SENDER_ADDRESS}\nUnsubscribe"

SUBJECTS = {
    "p1_plan_not_purchased": [
        "Your hair loss treatment plan is ready",
        "One step left to begin your treatment",
        "Your treatment plan, whenever you're ready",
    ],
    "p2_consult_no_show": [
        "Let's find a new time for your consultation",
        "Your doctor is ready when you are",
        "Missed your consultation? Easy to rebook",
    ],
    "p3_otc_cart_abandon": [
        "Your Redensyl serum is still in your cart",
        "Finish checking out your hair loss routine",
        "Your order is one step from complete",
    ],
    "the_valley": [
        "What's happening under the surface",
        "No results yet is completely normal",
        "Month 1 is about building the foundation",
    ],
    "consult_booking": [
        "You're one step from your personalised plan",
        "Your assessment is done, book your consultation",
        "A private, free consultation with a doctor",
    ],
    "replenishment_dunning": [
        "Your next delivery is coming up",
        "We couldn't process your last payment",
        "Your treatment is paused, easy to resume",
    ],
    "rx_not_suitable_otc": [
        "Your doctor's recommendation is ready",
        "A different path forward for your hair",
        "Redensyl: a strong first step",
    ],
    "aov_growth": [
        "Get more from your routine",
        "Build on what's already working",
        "The next step in your hair loss routine",
    ],
    "winback": [
        "We'd love to have you back",
        "Come back to your routine, on your terms",
        "See what's changed since you left",
    ],
    "results_milestone": [
        "You're four months in, here's what's next",
        "Your progress deserves a look back",
        "Keep the momentum going",
    ],
    "quiz_recovery": [
        "Your assessment is still open",
        "Pick up your hair loss assessment",
        "Two minutes left to finish your assessment",
    ],
}

# Real OTC catalogue + prices (01-Products.md). Rx medicines are never named
# in customer-facing copy, so the Rx line item uses a neutral internal label
# (no drug name) - it exists only so orders/analytics can represent Rx
# subscription revenue realistically. prescription_type mirrors the real
# BigQuery dotcom_plus_marketplace.Prescription_Type column (see
# "BigQuery Metadata.xlsx" -> Column Metadata sheet). "Free Online
# Consultation" is a $0 pseudo-product representing Order_Type='Consult
# Only' rows (a booked, doctor-led consult with no physical product shipped)
# - andSons consults are free per the brand voice doc.
PRODUCTS = [
    ("3% Redensyl Anti-Hair Loss Serum", 42.00, "Non-Prescription"),
    ("Intense Hair Growth Trio", 78.00, "Non-Prescription"),
    ("Intense Hair Growth Kit", 78.00, "Non-Prescription"),
    ("Thickening Shampoo", 24.00, "Non-Prescription"),
    ("Thickening Conditioner", 24.00, "Non-Prescription"),
    ("Biotin Gummies", 19.00, "Non-Prescription"),
    ("Dermaroller", 15.00, "Non-Prescription"),
    ("Doctor-Prescribed Hair Loss Plan (Rx)", 95.00, "Prescription"),
    ("Free Online Consultation", 0.00, "Non-Prescription"),
]

CONSULT_PRODUCT_NAME = "Free Online Consultation"

# Realistic order status mix, grounded in the real dotcom_plus_marketplace
# [Dotcom] status values (BigQuery Metadata.xlsx -> Column Metadata ->
# `status`). Consult-only orders always land on PAID_CONSULTATION_ONLY.
STATUS_WEIGHTS = [
    ("DELIVERED", 55),
    ("PACKED_DISPATCHED", 15),
    ("PAID_APPROVED", 15),
    ("PAID_PENDING_DOCTOR", 8),
    ("REFUND", 4),
    ("CANCELLED", 3),
]

# Grounded in the real Revenue_Type / Cleaned_Revenue_Type columns (same
# source), simplified to the subscription-length groupings relevant here.
REVENUE_TYPE_WEIGHTS = [
    ("New Customer One-off", 15),
    ("New Customer 1 Month Sub", 10),
    ("New Customer 3 Month Sub", 10),
    ("Repeat 1 Month Sub", 30),
    ("Repeat 3 Month Sub", 25),
    ("Repeat 6 Month Sub", 10),
]

# andSons ships free, discreet delivery on every order (00-Brand-Voice.md),
# so delivery_fee is always 0 - kept as its own column (rather than omitted)
# to match the real schema's Delivery_Fee field and make that explicit to
# the analytics agent rather than implicit.
DELIVERY_FEE = 0.0

MARKETING_CHANNELS = ["Facebook", "Google", "TikTok"]
MARKETING_CATEGORY = "HL"  # this demo's data is Hair Loss only

NUM_CUSTOMERS = 240
NUM_EMAILS = 520
OPEN_RATE = 0.40
CLICK_RATE = 0.15  # fraction of ALL sent emails that get clicked
ORDER_RATE_AFTER_CLICK = 0.50
BACKGROUND_ORDERS = 160
BACKGROUND_CONSULT_ONLY_RATE = 0.12  # share of background orders that are Consult Only

NOW = datetime(2026, 8, 3)
LOOKBACK_DAYS = 180


def create_schema(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS marketing_spend;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS email_events;
        DROP TABLE IF EXISTS emails_sent;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS campaigns;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            signup_date TEXT NOT NULL
        );

        CREATE TABLE campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flow_name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE emails_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            sent_at TEXT NOT NULL,
            subject TEXT NOT NULL
        );

        CREATE TABLE email_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER NOT NULL REFERENCES emails_sent(id),
            event_type TEXT NOT NULL CHECK (event_type IN ('opened', 'clicked')),
            event_at TEXT NOT NULL
        );

        -- prescription_type mirrors the real BigQuery
        -- dotcom_plus_marketplace.Prescription_Type column.
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            prescription_type TEXT NOT NULL CHECK (prescription_type IN ('Prescription', 'Non-Prescription'))
        );

        -- Financial/status/classification columns are grounded in the real
        -- BigQuery dotcom_plus_marketplace / updated_sales_data tables
        -- (see "BigQuery Metadata.xlsx"): status, Order_Type, Revenue_Type,
        -- Revenue, Applicable_Discount, Applicable_Cashback, Delivery_Fee,
        -- New_COGS, Final_Revenue.
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            order_at TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            attributed_email_id INTEGER REFERENCES emails_sent(id),
            status TEXT NOT NULL,
            order_type TEXT NOT NULL CHECK (order_type IN ('Products', 'Consult Only')),
            revenue_type TEXT NOT NULL,
            revenue REAL NOT NULL,
            discount_amount REAL NOT NULL DEFAULT 0,
            cashback_amount REAL NOT NULL DEFAULT 0,
            delivery_fee REAL NOT NULL DEFAULT 0,
            cogs REAL NOT NULL DEFAULT 0,
            final_revenue REAL NOT NULL
        );

        -- Grounded in the real BigQuery marketing_spend_data table
        -- (BigQuery Metadata.xlsx -> Marketing sheet), scoped to this demo's
        -- single brand/market/category (andSons, Singapore, Hair Loss).
        -- classification distinguishes whole-account spend on a channel
        -- ("Overall-Level") from spend specifically tagged to Hair Loss
        -- ("Category-Level") - do not sum both levels together, or spend
        -- gets double-counted (same caveat as the real table).
        CREATE TABLE marketing_spend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spend_date TEXT NOT NULL,
            channel TEXT NOT NULL,
            classification TEXT NOT NULL CHECK (classification IN ('Overall-Level', 'Category-Level')),
            category TEXT,
            spend REAL NOT NULL,
            clicks INTEGER NOT NULL,
            impressions INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def random_dt_within(days_back, after: datetime = None, min_offset_hours=0, max_offset_hours=None):
    if after is not None:
        max_h = max_offset_hours or 96
        offset = random.uniform(min_offset_hours, max_h)
        return after + timedelta(hours=offset)
    delta = random.uniform(0, days_back * 24)
    return NOW - timedelta(hours=delta)


def seed_customers(conn):
    cur = conn.cursor()
    rows = []
    for _ in range(NUM_CUSTOMERS):
        first_name = fake.first_name_male()
        email = fake.unique.email()
        signup_date = (NOW - timedelta(days=random.randint(10, 500))).strftime("%Y-%m-%d")
        rows.append((first_name, email, signup_date))
    cur.executemany(
        "INSERT INTO customers (first_name, email, signup_date) VALUES (?, ?, ?)", rows
    )
    conn.commit()
    return [r[0] for r in cur.execute("SELECT id FROM customers").fetchall()]


def seed_campaigns(conn):
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO campaigns (flow_name) VALUES (?)", [(f,) for f in FLOW_SLUGS]
    )
    conn.commit()
    return {
        row[1]: row[0] for row in cur.execute("SELECT id, flow_name FROM campaigns").fetchall()
    }


def seed_products(conn):
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO products (name, price, prescription_type) VALUES (?, ?, ?)", PRODUCTS
    )
    conn.commit()
    rows = cur.execute("SELECT id, name, price FROM products").fetchall()
    price_by_id = {row[0]: row[2] for row in rows}
    consult_product_id = next(row[0] for row in rows if row[1] == CONSULT_PRODUCT_NAME)
    sellable_product_ids = [row[0] for row in rows if row[1] != CONSULT_PRODUCT_NAME]
    return sellable_product_ids, price_by_id, consult_product_id


def seed_emails(conn, customer_ids, campaign_by_flow):
    cur = conn.cursor()
    email_rows = []
    for _ in range(NUM_EMAILS):
        flow = random.choice(FLOW_SLUGS)
        customer_id = random.choice(customer_ids)
        campaign_id = campaign_by_flow[flow]
        sent_at = random_dt_within(LOOKBACK_DAYS)
        subject = random.choice(SUBJECTS[flow])
        email_rows.append((customer_id, campaign_id, sent_at.strftime("%Y-%m-%d %H:%M:%S"), subject))
    cur.executemany(
        "INSERT INTO emails_sent (customer_id, campaign_id, sent_at, subject) VALUES (?, ?, ?, ?)",
        email_rows,
    )
    conn.commit()
    emails = cur.execute(
        "SELECT id, customer_id, sent_at FROM emails_sent"
    ).fetchall()
    return emails


def _weighted_choice(pairs):
    choices, weights = zip(*pairs)
    return random.choices(choices, weights=weights, k=1)[0]


def _build_product_order(customer_id, product_id, product_price, order_at, quantity, attributed_email_id):
    """Build one 'Products' order row with realistic status/revenue-type/
    financials, grounded in the real BigQuery dotcom_plus_marketplace
    columns (status, Revenue_Type, Revenue, Applicable_Discount,
    Applicable_Cashback, New_COGS, Final_Revenue)."""
    status = _weighted_choice(STATUS_WEIGHTS)
    revenue_type = _weighted_choice(REVENUE_TYPE_WEIGHTS)
    revenue = round(product_price * quantity, 2)

    if status == "CANCELLED":
        # Never captured - no financials booked.
        discount_amount = 0.0
        cashback_amount = 0.0
        cogs = 0.0
        final_revenue = 0.0
    else:
        discount_amount = round(revenue * random.uniform(0.05, 0.30), 2) if random.random() < 0.20 else 0.0
        cashback_amount = round(revenue * random.uniform(0.05, 0.15), 2) if random.random() < 0.10 else 0.0
        cogs = round(revenue * random.uniform(0.22, 0.32), 2)
        if status == "REFUND":
            # Was captured, then fully reversed.
            final_revenue = 0.0
        else:
            final_revenue = round(max(0.0, revenue - discount_amount - cashback_amount), 2)

    return (
        customer_id,
        product_id,
        order_at,
        quantity,
        attributed_email_id,
        status,
        "Products",
        revenue_type,
        revenue,
        discount_amount,
        cashback_amount,
        DELIVERY_FEE,
        cogs,
        final_revenue,
    )


def _build_consult_order(customer_id, consult_product_id, order_at):
    """A booked, doctor-led consultation with no physical product shipped -
    Order_Type='Consult Only', free (andSons consults are free)."""
    return (
        customer_id,
        consult_product_id,
        order_at,
        1,
        None,
        "PAID_CONSULTATION_ONLY",
        "Consult Only",
        "Consult Only",
        0.0,
        0.0,
        0.0,
        DELIVERY_FEE,
        0.0,
        0.0,
    )


def seed_events_and_orders(conn, emails, sellable_product_ids, price_by_id, consult_product_id):
    cur = conn.cursor()

    total = len(emails)
    num_opens = int(total * OPEN_RATE)
    num_clicks = int(total * CLICK_RATE)

    opened_emails = random.sample(emails, num_opens)
    # clicks are a subset of opened emails (you must open before you click)
    clicked_emails = random.sample(opened_emails, min(num_clicks, len(opened_emails)))
    clicked_ids = {e[0] for e in clicked_emails}

    event_rows = []
    for email_id, customer_id, sent_at in opened_emails:
        sent_dt = datetime.strptime(sent_at, "%Y-%m-%d %H:%M:%S")
        opened_at = random_dt_within(0, after=sent_dt, min_offset_hours=0.1, max_offset_hours=48)
        event_rows.append((email_id, "opened", opened_at.strftime("%Y-%m-%d %H:%M:%S")))
        if email_id in clicked_ids:
            clicked_at = random_dt_within(0, after=opened_at, min_offset_hours=0.05, max_offset_hours=6)
            event_rows.append((email_id, "clicked", clicked_at.strftime("%Y-%m-%d %H:%M:%S")))

    cur.executemany(
        "INSERT INTO email_events (email_id, event_type, event_at) VALUES (?, ?, ?)",
        event_rows,
    )
    conn.commit()

    # Orders attributed to ~50% of clicked emails, within a few days of the
    # click - these are always 'Products' orders (an email drove a purchase).
    click_at_by_email = {
        e_id: datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        for e_id, etype, t in event_rows
        if etype == "clicked"
    }
    clicked_email_list = list(click_at_by_email.items())
    random.shuffle(clicked_email_list)
    num_attributed_orders = int(len(clicked_email_list) * ORDER_RATE_AFTER_CLICK)

    order_rows = []
    email_customer = {e_id: cust_id for e_id, cust_id, _ in emails}

    for email_id, clicked_at in clicked_email_list[:num_attributed_orders]:
        customer_id = email_customer[email_id]
        product_id = random.choice(sellable_product_ids)
        order_at = random_dt_within(0, after=clicked_at, min_offset_hours=0.5, max_offset_hours=96)
        quantity = random.choice([1, 1, 1, 2])
        order_rows.append(
            _build_product_order(
                customer_id,
                product_id,
                price_by_id[product_id],
                order_at.strftime("%Y-%m-%d %H:%M:%S"),
                quantity,
                email_id,
            )
        )

    # Background/organic orders not attributed to any email - a portion are
    # Consult Only (organic consult bookings), the rest are Products.
    all_customer_ids = list(email_customer.values())
    num_consult_only = int(BACKGROUND_ORDERS * BACKGROUND_CONSULT_ONLY_RATE)
    for i in range(BACKGROUND_ORDERS):
        customer_id = random.choice(all_customer_ids)
        order_at = random_dt_within(LOOKBACK_DAYS).strftime("%Y-%m-%d %H:%M:%S")
        if i < num_consult_only:
            order_rows.append(_build_consult_order(customer_id, consult_product_id, order_at))
        else:
            product_id = random.choice(sellable_product_ids)
            quantity = random.choice([1, 1, 1, 2])
            order_rows.append(
                _build_product_order(
                    customer_id, product_id, price_by_id[product_id], order_at, quantity, None
                )
            )

    cur.executemany(
        "INSERT INTO orders (customer_id, product_id, order_at, quantity, attributed_email_id, "
        "status, order_type, revenue_type, revenue, discount_amount, cashback_amount, "
        "delivery_fee, cogs, final_revenue) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        order_rows,
    )
    conn.commit()

    return {
        "opens": len(opened_emails),
        "clicks": len(clicked_email_list),
        "attributed_orders": num_attributed_orders,
        "background_orders": BACKGROUND_ORDERS,
        "consult_only_orders": num_consult_only,
    }


def seed_marketing_spend(conn):
    """Weekly Google/Meta/TikTok ad spend, grounded in the real
    marketing_spend_data table (BigQuery Metadata.xlsx -> Marketing sheet),
    scoped to this demo's single brand/market/category. Each week gets a
    Category-Level (Hair Loss) row and a larger Overall-Level (whole
    andSons account on that channel) row per channel."""
    cur = conn.cursor()
    rows = []
    num_weeks = LOOKBACK_DAYS // 7
    start = NOW - timedelta(days=LOOKBACK_DAYS)
    for w in range(num_weeks):
        week_date = (start + timedelta(weeks=w)).strftime("%Y-%m-%d")
        for channel in MARKETING_CHANNELS:
            spend = round(random.uniform(800, 3500), 2)
            clicks = int(spend / random.uniform(0.8, 2.5))
            impressions = clicks * random.randint(15, 40)
            rows.append((week_date, channel, "Category-Level", MARKETING_CATEGORY, spend, clicks, impressions))

            overall_spend = round(spend * random.uniform(1.3, 2.2), 2)
            overall_clicks = int(overall_spend / random.uniform(0.8, 2.5))
            overall_impressions = overall_clicks * random.randint(15, 40)
            rows.append((week_date, channel, "Overall-Level", None, overall_spend, overall_clicks, overall_impressions))

    cur.executemany(
        "INSERT INTO marketing_spend (spend_date, channel, classification, category, spend, clicks, impressions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


# --- Golden examples for testing the Sweeper's judgment ---
# GOLDEN_P1 matches the real "P1 Email 1 - Approved Golden Template (mixed
# style)" doc exactly (Thalia-approved, structure items 1-9), with the real
# footer (WhatsApp CS link + registered address + unsubscribe) in place of
# the earlier placeholder footer.

GOLDEN_P1 = f"""Subject: Your hair loss treatment plan is ready
Preheader: One step left to begin your treatment.

Hi [name],

Your hair loss treatment plan is ready. Your doctor put it together
during your consultation, around what you discussed.

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment]

Doctor-led plan · Clinically studied · Discreet delivery

The andSons team

{FOOTER_TEXT}
""".strip()

FLAWED_TWO_HEROES = f"""Subject: Your hair loss treatment plan is ready
Preheader: One step left to begin your treatment.

[HERO IMAGE: "Hair growth with real support" — smiling man]

Hi [name],

Your hair loss treatment plan is ready. Your doctor put it together
during your consultation, around what you discussed.

[HERO IMAGE 2: "Confident Man" lifestyle banner]

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment]

Doctor-led plan · Clinically studied · Discreet delivery

The andSons team

{FOOTER_TEXT}
""".strip()

FLAWED_MISSING_FOOTER = """Subject: Your hair loss treatment plan is ready
Preheader: One step left to begin your treatment.

Hi [name],

Your hair loss treatment plan is ready. Your doctor put it together
during your consultation, around what you discussed.

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment]

Doctor-led plan · Clinically studied · Discreet delivery
""".strip()

FLAWED_PRICE_IN_P1 = f"""Subject: Your hair loss treatment plan is ready — just $49/month
Preheader: One step left to begin your treatment.

Hi [name],

Your hair loss treatment plan is ready. Your doctor put it together
during your consultation, around what you discussed.

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor. Your plan is just $49/month, billed after
your first shipment.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment — $49/month]

Doctor-led plan · Clinically studied · Discreet delivery

The andSons team

{FOOTER_TEXT}
""".strip()

FLAWED_BADGE_TRUST_ROW = f"""Subject: Your hair loss treatment plan is ready
Preheader: One step left to begin your treatment.

Hi [name],

Your hair loss treatment plan is ready. Your doctor put it together
during your consultation, around what you discussed.

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment]

[BADGE GRAPHIC: Doctor-Led] [BADGE GRAPHIC: Clinically Studied] [BADGE GRAPHIC: 4.8-Star Rated]

The andSons team

{FOOTER_TEXT}
""".strip()

FLAWED_RX_NAMED = f"""Subject: Your Finasteride and Minoxidil plan is ready
Preheader: One step left to begin your treatment.

Hi [name],

Your Finasteride 1mg and Minoxidil 5% plan is ready. Your doctor put it
together during your consultation, around what you discussed.

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment]

Doctor-led plan · Clinically studied · Discreet delivery

The andSons team

{FOOTER_TEXT}
""".strip()


def write_golden_examples():
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    examples = [
        {
            "id": "golden_p1_approved",
            "flow_name": "p1_plan_not_purchased",
            "approved": True,
            "reason": "Exact match to the Thalia-approved P1 golden template — one hero, light 3-step block, thin trust line, real footer, no price, no Rx names.",
            "email_text": GOLDEN_P1,
        },
        {
            "id": "flawed_two_heroes",
            "flow_name": "p1_plan_not_purchased",
            "approved": False,
            "reason": "Two hero images — golden template allows at most one hero.",
            "email_text": FLAWED_TWO_HEROES,
        },
        {
            "id": "flawed_missing_footer",
            "flow_name": "p1_plan_not_purchased",
            "approved": False,
            "reason": "Missing footer (no WhatsApp CS link/address/unsubscribe) — Sweeper must fail anything missing the required footer.",
            "email_text": FLAWED_MISSING_FOOTER,
        },
        {
            "id": "flawed_price_in_p1",
            "flow_name": "p1_plan_not_purchased",
            "approved": False,
            "reason": "Mentions a price ($49/month) in a P1 (Rx track) email — P1 must stay no-payment / no-price.",
            "email_text": FLAWED_PRICE_IN_P1,
        },
        {
            "id": "flawed_badge_trust_row",
            "flow_name": "p1_plan_not_purchased",
            "approved": False,
            "reason": "Trust row uses badge graphics instead of a thin centred text line with light ticks.",
            "email_text": FLAWED_BADGE_TRUST_ROW,
        },
        {
            "id": "flawed_rx_named",
            "flow_name": "p1_plan_not_purchased",
            "approved": False,
            "reason": "Names prescription medicines (Finasteride, Minoxidil) — never permitted in customer-facing copy on any flow.",
            "email_text": FLAWED_RX_NAMED,
        },
    ]
    path = os.path.join(TEMPLATES_DIR, "golden_examples.json")
    with open(path, "w") as f:
        json.dump(examples, f, indent=2)
    return path


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    create_schema(conn)

    customer_ids = seed_customers(conn)
    campaign_by_flow = seed_campaigns(conn)
    sellable_product_ids, price_by_id, consult_product_id = seed_products(conn)
    emails = seed_emails(conn, customer_ids, campaign_by_flow)
    stats = seed_events_and_orders(conn, emails, sellable_product_ids, price_by_id, consult_product_id)
    num_marketing_rows = seed_marketing_spend(conn)

    conn.close()

    golden_path = write_golden_examples()

    total_orders = stats["attributed_orders"] + stats["background_orders"]
    print(f"Seeded database at {DB_PATH}")
    print(f"  customers: {len(customer_ids)}")
    print(f"  campaigns (flows): {len(campaign_by_flow)}")
    print(f"  products: {len(sellable_product_ids) + 1}")
    print(f"  emails_sent: {len(emails)}")
    print(f"  opens: {stats['opens']} ({stats['opens'] / len(emails):.1%})")
    print(f"  clicks: {stats['clicks']} ({stats['clicks'] / len(emails):.1%})")
    print(f"  orders attributed to a clicked email: {stats['attributed_orders']}")
    print(f"  background (unattributed) orders: {stats['background_orders']}")
    print(f"  of which Consult Only: {stats['consult_only_orders']}")
    print(f"  total orders: {total_orders}")
    print(f"  marketing_spend rows: {num_marketing_rows}")
    print(f"Wrote golden examples to {golden_path}")


if __name__ == "__main__":
    main()
