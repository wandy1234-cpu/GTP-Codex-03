import unittest
import alpha5d_classification as c
class IndustryClassificationTests(unittest.TestCase):
    def boards(self):return [{'f12':'BK'+str(i),'f14':name} for i,name in enumerate(sorted(c.FIRST_LEVEL))]
    def test_one_coherent_level(self):
        boards=self.boards()+[{'f12':'leaf','f14':'半导体'},{'f12':'leaf2','f14':'银行Ⅱ'}]
        self.assertEqual(len(c.select_first_level(boards)),31)
        self.assertNotIn('半导体',{b['f14'] for b in c.select_first_level(boards)})
    def test_missing_industry_rejected(self):
        with self.assertRaises(ValueError):c.select_first_level(self.boards()[:-1])
    def test_duplicate_root_rejected(self):
        boards=self.boards();boards.append(dict(boards[0]))
        with self.assertRaises(ValueError):c.select_first_level(boards)
if __name__=='__main__':unittest.main()
