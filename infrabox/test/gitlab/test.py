import unittest
import sys
import json


class GitlabTest(unittest.TestCase):

    def setUp(self):
        # preparing gitlab data
        test_data = json.load(open('test_data.json'))
