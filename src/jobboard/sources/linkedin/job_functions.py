"""LinkedIn job function taxonomy for the f_F guest search filter.

Codes are short text strings from LinkedIn's official job function taxonomy.
They are stable and work with the guest API f_F parameter.
"""
from __future__ import annotations

# {f_F code: display_name}
JOB_FUNCTIONS: dict[str, str] = {
    "it":   "Information Technology",
    "fin":  "Finance",
    "eng":  "Engineering",
    "sale": "Sales",
    "mrkt": "Marketing",
    "prod": "Product Management",
    "mgmt": "Management",
    "bd":   "Business Development",
    "ops":  "Operations",
    "anls": "Analyst",
    "rsch": "Research",
    "hr":   "Human Resources",
    "cnsl": "Consulting",
    "acct": "Accounting / Auditing",
    "lgl":  "Legal",
    "dsgn": "Design",
    "cust": "Customer Service",
    "adm":  "Administrative",
    "edu":  "Education",
    "hcpr": "Health Care Provider",
    "pr":   "Public Relations",
    "art":  "Art / Creative",
    "wrt":  "Writing / Editing",
    "sci":  "Science",
    "qa":   "Quality Assurance",
    "supv": "Strategy / Planning",
}
