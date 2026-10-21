import unittest
from worker import signal, in_regular_session
class WorkerTests(unittest.TestCase):
    def test_insufficient(self):self.assertIsNone(signal([1]*24))
    def test_long(self):self.assertEqual(signal([100]*17+[105]*8),'long')
    def test_short(self):self.assertEqual(signal([105]*17+[100]*8),'short')
    def test_neutral(self):self.assertEqual(signal([100]*25),'neutral')
    def test_closed(self):self.assertFalse(in_regular_session({'is_open':False}))
if __name__=='__main__':unittest.main()
