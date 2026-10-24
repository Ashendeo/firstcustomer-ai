"""Problem-led buying committees, not generic industry-wide job-title defaults.

These roles are hypotheses about B2B budget ownership and evaluation, not
proof that anyone has purchase authority. The verified market skill can
subsequently refine these suggestions using Qwen and source-backed research.
"""
import re

# Each specialization models a specific purchasing problem, not a broad
# industry. Rules are ordered from the most distinctive problem to the most
# general. Strong phrases take precedence over incidental generic words.
SIGNATURES = (
    ("sports_video_analytics",
     ("sports video", "match video", "football video", "player analytics",
      "performance analysis", "sports highlights", "coaching analytics", "shuttle tracking"),
     ("video", "football", "badminton", "match", "sports analytics"),
     (
      ("Head of Sports Performance", "economic_buyer", "Owns performance-analysis priorities and spending"),
      ("Technical Director", "problem_owner", "Owns coaching and player-development workflows"),
      ("Head Coach", "functional_champion", "Uses match footage to improve performance"),
      ("Video Analyst", "technical_evaluator", "Evaluates tracking and analysis outputs"),
     )),
    ("sports_event_management",
     ("football tournament", "sports tournament", "tournament management",
      "sports event management", "match scheduling", "sports league platform",
      "competition management", "sports event organizer"),
     ("tournament", "sports league", "sports event", "competition"),
     (
      ("Head of Sports Operations", "economic_buyer", "Owns event operations and budgets"),
      ("Tournament Director", "problem_owner", "Owns competition setup and scheduling"),
      ("League Operations Manager", "functional_champion", "Runs fixtures and results"),
     )),
    ("employee_sports_and_engagement",
     ("employee engagement", "employee wellness", "corporate sports", "employee sports",
      "workplace wellness", "staff engagement", "company sports league"),
     ("hr", "workplace", "wellness", "employee"),
     (
      ("CHRO", "economic_buyer", "Sponsors employee-experience budgets"),
      ("Head of Employee Engagement", "problem_owner", "Owns engagement programs"),
      ("Head of HR", "functional_champion", "Runs or sponsors employee initiatives"),
     )),
    ("vendor_procurement",
     ("vendor management", "supplier risk", "supplier management", "procurement software",
      "supply chain visibility", "vendor risk", "third party risk", "sourcing platform"),
     ("vendor", "supplier", "procurement", "sourcing"),
     (
      ("Chief Procurement Officer", "economic_buyer", "Owns procurement spending"),
      ("Head of Procurement", "problem_owner", "Owns supplier selection and purchasing workflow"),
      ("Vendor Risk Manager", "functional_champion", "Monitors external supplier risk"),
     )),
    ("sales_prospecting",
     ("sales prospecting", "lead generation", "find customers", "first customer",
      "outbound sales", "sales intelligence", "go to market", "go-to-market",
      "lead enrichment", "customer acquisition", "b2b outreach"),
     ("prospect", "outreach", "sales", "customers", "leads"),
     (
      ("VP Sales", "economic_buyer", "Owns pipeline targets and sales tooling budget"),
      ("Head of Sales Development", "problem_owner", "Owns prospect discovery and outreach"),
      ("Head of Growth", "functional_champion", "Tests customer acquisition channels"),
      ("Revenue Operations Director", "technical_evaluator", "Evaluates CRM and workflow integration"),
     )),
    ("finance_reconciliation",
     ("financial reconciliation", "payment reconciliation", "invoice matching",
      "accounting automation", "accounts payable", "financial close",
      "payment operations", "expense management"),
     ("accounting", "finance", "payment", "invoice", "reconciliation"),
     (
      ("CFO", "economic_buyer", "Owns finance transformation budgets"),
      ("Controller", "problem_owner", "Owns books, controls and reconciliation"),
      ("Head of Finance Operations", "functional_champion", "Runs payments and closing workflows"),
     )),
    ("cybersecurity_operations",
     ("vulnerability management", "threat detection", "cybersecurity", "security operations",
      "soc automation", "security posture", "cloud security", "identity security"),
     ("security", "risk", "cyber", "vulnerability", "compliance"),
     (
      ("CISO", "economic_buyer", "Owns security budget and enterprise risk"),
      ("Head of Security Operations", "problem_owner", "Owns threat investigation and response"),
      ("Security Architect", "technical_evaluator", "Evaluates security integration and controls"),
     )),
    ("developer_platform",
     ("developer platform", "developer productivity", "devops", "ci/cd",
      "observability", "database infrastructure", "data platform",
      "software infrastructure", "api management"),
     ("developer", "engineering", "infrastructure", "database", "observability"),
     (
      ("CTO", "economic_buyer", "Sponsors engineering platform investments"),
      ("VP Engineering", "problem_owner", "Owns developer effectiveness"),
      ("Platform Engineering Lead", "technical_evaluator", "Tests tools, reliability and integration"),
     )),
    ("healthcare_workflow",
     ("clinical workflow", "hospital software", "patient management", "healthcare software",
      "electronic medical", "patient engagement", "medical records"),
     ("clinical", "patient", "hospital", "healthcare"),
     (
      ("Chief Medical Information Officer", "economic_buyer", "Sponsors clinical technology initiatives"),
      ("Director of Clinical Operations", "problem_owner", "Owns care-delivery workflows"),
      ("Healthcare IT Director", "technical_evaluator", "Tests integration and privacy safeguards"),
     )),
    ("customer_support",
     ("customer support", "contact center", "customer service automation",
      "helpdesk", "support ticket", "agent assistance"),
     ("support", "helpdesk", "service desk"),
     (
      ("VP Customer Support", "economic_buyer", "Owns service-operations budget"),
      ("Head of Customer Experience", "problem_owner", "Owns customer-service performance"),
      ("Customer Support Operations Manager", "functional_champion", "Runs support workflows"),
     )),
)


def infer_buying_committee(product: str, problem: str = "") -> dict:
    """Return role hypotheses with reasons, or an explicit unclassified result."""
    text = " ".join([str(product or ""), str(problem or "")]).casefold()
    matches = []
    for order, (key, phrases, keywords, roles) in enumerate(SIGNATURES):
        phrase_hits = [p for p in phrases if p in text]
        token_hits = [w for w in keywords if re.search(
            r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text)]
        strength = len(phrase_hits) * 5 + len(token_hits)
        if strength:
            matches.append((strength, -order, key, phrase_hits, token_hits, roles))
    if not matches:
        return {
            "problem_domain": "unclassified",
            "confidence": "insufficient_evidence",
            "committee": [],
            "evidence": [],
            "reason": "Company description does not identify a specific purchasing problem; roles need review",
        }
    best = max(matches)
    score, _, domain, phrases, tokens, roles = best
    if not phrases and len(tokens) < 2:
        return {
            "problem_domain": "unclassified",
            "confidence": "insufficient_evidence",
            "committee": [],
            "evidence": tokens,
            "reason": "Only a broad category term was observed; assigning budget owners would be speculative",
        }
    return {
        "problem_domain": domain,
        "confidence": "moderate" if score >= 6 else "limited",
        "committee": [
            {"role": title, "function": function, "reason": reason}
            for title, function, reason in roles
        ],
        "evidence": (phrases + tokens)[:8],
        "reason": "Roles inferred from the seller's product and customer problem, not verified individuals",
    }
