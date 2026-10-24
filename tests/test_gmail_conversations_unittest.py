import unittest
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import Campaign, Lead, GmailMessage
from app import settings
from app import gmail_conversations as g
from app.sales_pitch import reply_intent

class GmailFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine)
        self.c=Campaign(name='Test',product='Video analytics for sports teams.',ideal_customer='Sports clubs',status='ready',booking_url='https://calendly.com/test/demo')
        self.db.add(self.c); self.db.flush()
        self.lead=Lead(campaign_id=self.c.id,company_name='Club A',description='A football academy',email='buyer@club.test',draft_subject='Test',draft_body='Test draft')
        self.db.add(self.lead); self.db.commit()
        self.creds=patch.object(g,'credentials',return_value=('owner@gmail.com','abcdefghijklmnop')); self.creds.start()
        self.send=patch.object(g,'smtp_send',return_value={'message_id':'accepted','accepted':True}); self.mock=self.send.start()
        self.conv=g.start_conversation(self.db,self.lead)
    def tearDown(self):
        self.send.stop(); self.creds.stop(); self.db.close(); self.engine.dispose()
    def reply(self,body,identifier='reply1'):
        pitch=g.messages(self.db,self.lead.id)[0]
        m=EmailMessage(); m['From']='owner@gmail.com'; m['To']='owner@gmail.com'; m['Message-ID']=f'<{identifier}@test>'; m['In-Reply-To']=pitch.message_id; m.set_content(body)
        return m.as_bytes()
    def test_pitch_reuse(self):
        g.advance(self.db,self.conv); g.start_conversation(self.db,self.lead); g.advance(self.db,self.conv)
        self.assertEqual(self.mock.call_count,1)
        self.assertIn('football academy',g.messages(self.db,self.lead.id)[0].body)
    def test_positive_and_duplicate_reply(self):
        g.advance(self.db,self.conv)
        raw=self.reply('Interested, sounds good')
        self.assertTrue(g.ingest(self.db,self.conv,raw)); self.assertFalse(g.ingest(self.db,self.conv,raw))
        g.advance(self.db,self.conv); g.advance(self.db,self.conv)
        self.assertEqual(self.mock.call_count,2)
        self.assertIn('utm_content=',g.messages(self.db,self.lead.id)[-1].body)
    def test_negative_stops_nudges(self):
        g.advance(self.db,self.conv); g.ingest(self.db,self.conv,self.reply('Not interested'))
        g.advance(self.db,self.conv,datetime.now(timezone.utc)+timedelta(days=10))
        self.assertEqual(self.mock.call_count,1)
    def test_no_response_bounded_nudges(self):
        g.advance(self.db,self.conv)
        g.advance(self.db,self.conv,datetime.now(timezone.utc)+timedelta(days=4))
        g.advance(self.db,self.conv,datetime.now(timezone.utc)+timedelta(days=8))
        g.advance(self.db,self.conv,datetime.now(timezone.utc)+timedelta(days=9))
        self.assertEqual(self.mock.call_count,3)
    def test_uncertain_smtp_not_retried(self):
        self.mock.side_effect=OSError('timeout')
        with self.assertRaises(OSError): g.advance(self.db,self.conv)
        with self.assertRaises(Exception): g.advance(self.db,self.conv)
        self.assertEqual(self.mock.call_count,1)
    def test_quote_does_not_make_negative_positive(self):
        self.assertEqual(reply_intent('No thanks\n\nOn Friday person wrote:\nInterested?'),'negative')
    def test_question_is_answered_before_booking(self):
        g.advance(self.db,self.conv); g.ingest(self.db,self.conv,self.reply('Interested, what is the price?'))
        with patch.object(g,'answer_question',return_value='I do not have verified pricing.'):
            g.advance(self.db,self.conv)
        self.assertTrue(any(m.kind == 'answer' for m in g.messages(self.db,self.lead.id)))

if __name__=='__main__': unittest.main()
