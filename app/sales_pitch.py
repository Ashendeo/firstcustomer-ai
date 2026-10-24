"""Grounded sales copy from saved records; no discovery/provider calls."""
import re


def sales_pitch(campaign, lead):
    from . import settings
    from .agent import safe_booking_link
    booking_link = safe_booking_link(campaign.booking_url or settings.CALENDLY_BOOKING_URL,
                                     campaign.id, lead.id)
    booking_cta = (f"Prefer to book directly? Choose a convenient time: {booking_link}\n\n"
                   if booking_link else "")
    record = (lead.evidence or {}).get("company_record") or {}
    description = (record.get("basic_info") or {}).get("description") or lead.description
    card = (lead.evidence or {}).get("scorecard") or {}
    signals = [str(c.get("explanation") or "") for c in card.get("criteria", [])
               if c.get("key") in ("industry", "need", "timing") and c.get("earned", 0) > 0]
    context = str(description or "").strip()[:450]
    if context:
        why = f"Your company profile describes {lead.company_name} as: {context}"
    else:
        why = f"I'm exploring whether {lead.company_name} fits the teams we serve."
    evidence = "\n" + "\n".join(f"- {s[:260]}" for s in signals[:2]) if signals else ""
    first = lead.person_name.split()[0] if lead.person_name else "there"
    return (f"Hi {first},\n\n{campaign.seller_name or 'Our team'} offers: {campaign.product.strip()}\n\n"
            f"Why I'm reaching out to {lead.company_name}: {why}{evidence}\n\n"
            f"We are focusing on {campaign.ideal_customer.strip()[:450]}. "
            "Based on that overlap, there may be a useful fit; this is a hypothesis, "
            "not an assumption about your budget or current priorities.\n\n"
            "Is this relevant to your team? If so, I'd be happy to discuss your use case "
            "and arrange a short conversation.\n\n"
            + booking_cta
            + "If it isn't relevant, let me know and I won't follow up.")


def new_reply_text(text):
    """Remove quoted history before interpreting intent (plain-text email)."""
    lines = []
    for line in text.splitlines():
        if (line.startswith('>') or re.match(r'^On .+wrote:\s*$', line, re.I)
                or re.match(r'^(-+\s*Original Message|From:|_{5,})', line, re.I)):
            break
        lines.append(line)
    return '\n'.join(lines).strip()[:8000]


def reply_intent(body):
    text = re.sub(r'\s+', ' ', new_reply_text(body).lower()).replace('’', "'")
    if any(x in text for x in ('unsubscribe', 'remove me', 'stop emailing', 'do not contact',
                              "don't email", 'opt out', 'stop contacting')):
        return 'opt_out'
    if any(x in text for x in ('not interested', 'no thanks', 'no thank you', 'not relevant',
                              'not a fit', "don't need", 'do not need')):
        return 'negative'
    if any(x in text for x in ('out of office', 'automatic reply', 'on leave', 'on vacation')):
        return 'out_of_office'
    if any(x in text for x in ('not now', 'later', 'next quarter', 'next month', 'not ready')):
        return 'later'
    # Questions and conditional interest need an answer before a scheduling link.
    if '?' in text or any(x in text for x in ('send more', 'tell me more', 'pricing', 'price', 'how much', 'details')):
        return 'question'
    if any(x in text for x in ('interested', "let's talk", 'lets talk', 'schedule', 'book a',
                              "let's meet", 'can we meet', 'sounds good', 'yes please')):
        return 'positive'
    return 'unclear'
