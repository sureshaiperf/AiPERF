import unittest
from unittest.mock import Mock
from types import SimpleNamespace

import baselineintelligence.readiness_score as readiness

class TestReadinessOverwrite(unittest.TestCase):
    def test_overwrite_by_timestamp_writes_with_existing_time(self):
        # Prepare a mock client
        mock_client = Mock()

        # Side effect for query: initial analysis query returns empty dict
        # latest_query should return a dict with one point containing a time
        def query_side_effect(q):
            if 'aiperf_analysis' in q:
                return {}
            if 'aiperf_release_readiness' in q and 'ORDER BY time' in q:
                return {'aiperf_release_readiness': [{'time': '2026-01-01T00:00:00Z'}]}
            return {}

        mock_client.query.side_effect = query_side_effect
        mock_client.write_points = Mock()

        # Call run_readiness with the mock client and explicit args
        args = SimpleNamespace(build_id='ci-123', dry_run=False, jenkins_snippet=False)
        env = {}  # no env vars

        readiness.run_readiness(client=mock_client, args=args, env=env)

        # Ensure write_points was called once
        mock_client.write_points.assert_called_once()

        # Inspect the written point to confirm it includes the existing time
        written = mock_client.write_points.call_args[0][0]
        self.assertIsInstance(written, list)
        point = written[0]
        # Should include 'time' equal to existing_point_time
        self.assertIn('time', point)
        self.assertEqual(point['time'], '2026-01-01T00:00:00Z')

if __name__ == '__main__':
    unittest.main()
