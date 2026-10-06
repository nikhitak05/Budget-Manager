import unittest, tempfile, os, sys
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app
class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); app.DB=os.path.join(self.tmp.name,'test.db'); app.init()
    def tearDown(self): self.tmp.cleanup()
    def submit(self,hours=60,fail=False):
        app.mutate(dict(action='submit',name='Test',project=1,hours=hours,deadline=30,config='gpu',fail=fail))
        return app.snapshot()['jobs'][0]['id']
    def test_reservation_and_rejection(self):
        before=app.snapshot()['projects'][0]['available']; jid=self.submit()
        app.mutate(dict(action='approve',id=jid))
        self.assertAlmostEqual(app.snapshot()['projects'][0]['available'],before-64)
        with self.assertRaises(ValueError): app.mutate(dict(action='approve',id=jid))
        expensive=self.submit(10000)
        with self.assertRaises(ValueError): app.mutate(dict(action='approve',id=expensive))
        app.mutate(dict(action='reject',id=expensive))
    def test_run_requires_approval(self):
        with self.assertRaises(ValueError): app.mutate(dict(action='run',id=self.submit()))
    def test_success_and_failed_cost(self):
        for fail in [False,True]:
            jid=self.submit(fail=fail);app.mutate(dict(action='approve',id=jid))
            with app.connect() as c: c.execute("UPDATE jobs SET status='running' WHERE id=?",(jid,))
            with patch('app.time.sleep'): app.run_job(jid)
            j=app.snapshot()['jobs'][0]
            self.assertEqual(j['status'],'failed' if fail else 'succeeded');self.assertGreater(j['actual'],0);self.assertGreater(j['gpu_hours'],0)
            self.assertEqual(app.snapshot()['projects'][0]['reserved'],0)
    def test_invalid_input(self):
        for h in [0,-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): self.submit(h)
if __name__=='__main__': unittest.main()
