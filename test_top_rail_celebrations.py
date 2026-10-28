import unittest

from flask import Flask

import flask_app as tracker


class TopRailCelebrationConfigTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.add_url_rule(
            '/top_rail_dashboard/celebration_config',
            view_func=tracker.top_rail_celebration_config,
        )
        self.client = self.app.test_client()

    def test_unconfigured_dashboard_can_fall_back_without_error(self):
        response = self.client.get('/top_rail_dashboard/celebration_config')
        self.assertEqual(200, response.status_code)
        self.assertEqual({'api_key': ''}, response.json)
        self.assertEqual('no-store', response.headers['Cache-Control'])

    def test_configuration_uses_current_server_key_without_caching(self):
        for key in ('test-key-one', 'test-key-two'):
            self.app.config['GIPHY_API_KEY'] = key
            response = self.client.get('/top_rail_dashboard/celebration_config')
            self.assertEqual({'api_key': key}, response.json)
            self.assertEqual('no-store', response.headers['Cache-Control'])


if __name__ == '__main__':
    unittest.main()
