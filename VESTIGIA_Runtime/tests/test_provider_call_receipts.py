import json
import tempfile
import unittest
from pathlib import Path

from vestigia.models import ProviderReply, ProviderRequest
from vestigia.provider_call_receipts import ReceiptProvider


class ProviderCallReceiptTests(unittest.TestCase):
    def test_success_records_each_call_without_prompt_content(self):
        class Provider:
            def model_for(self, route):
                return 'requested-model'

            def complete(self, request):
                return ProviderReply(text='private response', provider='fake',
                    model='requested-model', reported_model='provider-version',
                    response_id='response-id', usage={'input_tokens': 12})

        with tempfile.TemporaryDirectory() as tmp:
            provider = ReceiptProvider(Provider(), Path(tmp))
            request = ProviderRequest(turn_id='turn-1', model_route='default',
                messages=({'role': 'user', 'content': 'private prompt'},),
                metadata={'invocation': 'conversation', 'house_tool_round': 0})
            provider.complete(request)
            provider.complete(request)
            records = [json.loads(p.read_text()) for p in (Path(tmp) / 'traces' / 'provider-calls').glob('*.json')]
            self.assertEqual(2, len(records))
            for record in records:
                self.assertEqual('completed', record['status'])
                self.assertEqual('requested-model', record['requested_model'])
                self.assertEqual('provider-version', record['reported_model'])
                self.assertEqual({'input_tokens': 12}, record['usage'])
                self.assertNotIn('private prompt', json.dumps(record))
                self.assertNotIn('private response', json.dumps(record))

    def test_failed_call_records_uncertain_outcome(self):
        class Provider:
            def complete(self, request):
                raise RuntimeError('private exception')

        with tempfile.TemporaryDirectory() as tmp:
            provider = ReceiptProvider(Provider(), Path(tmp))
            with self.assertRaises(RuntimeError):
                provider.complete(ProviderRequest(turn_id='turn-2', model_route='default', messages=()))
            record = json.loads(next((Path(tmp) / 'traces' / 'provider-calls').glob('*.json')).read_text())
            self.assertEqual('uncertain', record['status'])
            self.assertNotIn('private exception', json.dumps(record))

    def test_interruption_leaves_started_record(self):
        class Provider:
            def complete(self, request):
                raise KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmp:
            provider = ReceiptProvider(Provider(), Path(tmp))
            with self.assertRaises(KeyboardInterrupt):
                provider.complete(ProviderRequest(turn_id='turn-3', model_route='default', messages=()))
            record = json.loads(next((Path(tmp) / 'traces' / 'provider-calls').glob('*.json')).read_text())
            self.assertEqual('started', record['status'])
