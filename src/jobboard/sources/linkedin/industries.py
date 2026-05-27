"""LinkedIn industry taxonomy for the f_I guest search filter.

IDs sourced from LinkedIn's job search URL (f_I= parameter) observed in the
LinkedIn UI and confirmed by the developer community.

Note: LinkedIn runs two overlapping taxonomies ("v1" and "v2").  The IDs here
are the ones that appear in the public jobs-search URL and work with the guest
API f_I parameter.
"""
from __future__ import annotations

# {industry_id: display_name}
INDUSTRIES: dict[int, str] = {
    # ── Finance & Banking ────────────────────────────────────────────
    43:  "Financial Services",
    41:  "Banking",
    44:  "Insurance",
    45:  "Investment Management",
    46:  "Capital Markets",
    35:  "Investment Banking",
    37:  "Accounting",

    # ── Technology ───────────────────────────────────────────────────
    96:  "Technology, Information and Internet",
    3:   "IT Services and IT Consulting",
    4:   "Software Development",
    6:   "Internet",
    48:  "Telecommunications",

    # ── Business & Professional Services ────────────────────────────
    10:  "Management Consulting",
    55:  "Staffing and Recruiting",
    73:  "Human Resources",

    # ── Healthcare & Life Sciences ───────────────────────────────────
    14:  "Hospitals and Health Care",
    13:  "Pharmaceutical Manufacturing",
    12:  "Medical Equipment Manufacturing",

    # ── Real Estate ───────────────────────────────────────────────────
    34:  "Real Estate",
    38:  "Construction",

    # ── Consumer & Retail ────────────────────────────────────────────
    27:  "Retail",
    88:  "Consumer Goods",

    # ── Media, Education & Others ────────────────────────────────────
    23:  "Higher Education",
    17:  "Advertising Services",
    18:  "Media Production",
    69:  "Government Administration",
    66:  "Non-profit Organizations",
}
