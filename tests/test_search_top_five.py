from types import SimpleNamespace
import unittest
from unittest.mock import patch
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app.candidate_ranking import outreach_leads
from app import outreach


def lead(rank, score):
    return SimpleNamespace(
        id=str(rank), company_name=f'Company {rank}', domain=f'company{rank}.com',
        evidence={'search_prioritization': {'provider_rank': rank}},
        fit_score=score, source='crustdata', status='draft',
        draft_subject='Pilot', draft_body='Would a pilot be useful?',
        email=f'buyer@company{rank}.com',
    )


def test_outreach_uses_search_rank_not_score_and_does_not_backfill():
    candidates = [lead(21, 100), lead(4, 95), lead(1, 0), lead(5, None), lead(20, 1)]
    assert [x.id for x in outreach_leads(candidates)] == ['1', '4', '5', '20']
    # Already processed leads keep their place: later runs cannot promote #21.
    candidates[2].status = 'sent'
    assert [x.id for x in outreach_leads(candidates)] == ['1', '4', '5', '20']


def test_missing_rank_and_duplicate_company_are_not_promoted():
    a, b, unranked = lead(1, 1), lead(2, 99), lead(None, 100)
    b.domain = a.domain
    assert outreach_leads([b, unranked, a]) == [a]


def test_low_or_missing_score_does_not_block_selected_results():
    db = SimpleNamespace(get=lambda *args: None, scalar=lambda *args: None)
    policy = SimpleNamespace(auto_send=True, prepare_linkedin_drafts=False, min_score=75)
    for score in (0, 42, None):
        assert outreach.policy_decision(db, None, lead(1, score), policy) == 'eligible'
    assert outreach.policy_decision(db, None, lead(21, 100), policy) == 'outside_search_top_twenty'
    assert outreach.policy_decision(db, None, lead(None, 100), policy) == 'missing_search_rank'


def test_campaign_retains_scores_search_order_and_paid_checkpoints():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app import agent, settings
    from app.db import Base
    from app.models import Campaign, Lead, AuditEvent
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    calls = []

    class Provider:
        def company_search(self, *args):
            calls.append('search')
            return [{'basic_info': {'name': f'Company {i}', 'primary_domain': f'company{i}.com'}} for i in range(1, 8)]

        def company_enrich_advanced(self, domain):
            calls.append('enrich:' + domain)
            return {'basic_info': {'description': 'Cloud software for business operations'}}

        def people_search(self, domain, *args, **kwargs):
            calls.append('people:' + domain)
            return [{'basic_profile': {'name': 'Buyer'}, 'social_handles': {
                'professional_network_identifier': {'profile_url': 'https://linkedin.com/in/' + domain}}}]

        def business_email_enrich(self, profile):
            calls.append('email:' + profile)
            domain = profile.rsplit('/', 1)[-1]
            return [] if domain == 'company2.com' else [{'status': 'deliverable', 'email': 'buyer@' + domain}]

    with sessions() as db:
        campaign = Campaign(name='Test', product='Product', ideal_customer='ICP', status='running')
        db.add(campaign)
        db.commit()
        cid = campaign.id
    with patch.object(agent, 'SessionLocal', sessions), patch.object(agent, 'Crustdata', Provider), \
            patch.object(agent, 'llm_json', return_value={'subject': 'Pilot', 'body': 'Hello', 'fit_score': 50}), \
            patch.object(outreach, 'run_outreach', return_value={}), \
            patch.multiple(settings, DEMO_MODE=False, QWEN_PLAN_WITH_MODEL=False, MAX_COMPANIES=7,
                           MAX_PROSPECT_ENRICHMENTS=7, MAX_PEOPLE_SEARCHES=7, MAX_CONTACT_ENRICHMENTS=7):
        agent.run_campaign(cid)
        with sessions() as db:
            c = db.get(Campaign, cid)
            assert c.status == 'ready', c.error
            leads = db.scalars(select(Lead)).all()
            assert {x.domain for x in leads if x.email} == {'company1.com', 'company3.com', 'company4.com', 'company5.com'}
            assert any(x.domain == 'company2.com' and x.status == 'needs_email_verification' for x in leads)
            assert all(x.fit_score is not None for x in leads)
            event = db.scalar(select(AuditEvent).where(AuditEvent.action == 'candidate_final_ranking'))
            assert [x['provider_rank'] for x in event.details['candidates']] == list(range(1, 8))
            assert event.details['candidates'][5]['status'] != 'outside_search_top_twenty'
            c.status = 'running'
            db.commit()
        before = list(calls)
        agent.run_campaign(cid)
        assert calls == before
        assert any('company6.com' in x for x in calls)
    engine.dispose()


class SearchTopTwentyTests(unittest.TestCase):
    def test_selection(self):
        test_outreach_uses_search_rank_not_score_and_does_not_backfill()

    def test_duplicates(self):
        test_missing_rank_and_duplicate_company_are_not_promoted()

    def test_score_independence(self):
        with patch.object(outreach, 'jurisdiction_decision', return_value='allow'):
            test_low_or_missing_score_does_not_block_selected_results()

    def test_checkpointed_campaign(self):
        test_campaign_retains_scores_search_order_and_paid_checkpoints()
