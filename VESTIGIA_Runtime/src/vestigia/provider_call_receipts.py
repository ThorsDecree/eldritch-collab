from .utils import atomic_write_json, new_id, utc_now_iso


class ReceiptProvider:
    """Record application-level provider calls; SDK network retries are not counted."""

    def __init__(self, provider, home, approval=None):
        self.provider = provider
        self.home = home
        self.approval = approval

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def complete(self, request):
        if self.approval is not None:
            self.approval.admit(self.provider, route=request.model_route,
                                turn_id=request.turn_id, phase="before_dispatch")
        call_id = new_id('pcall')
        path = self.home / 'traces' / 'provider-calls' / f'{call_id}.json'
        record = {
            'schema_version': 'vestigia.provider-call.v0.1',
            'call_id': call_id, 'turn_id': request.turn_id,
            'route': request.model_route, 'started_at': utc_now_iso(),
            'status': 'started', 'requested_model': None,
            'reported_model': None, 'invocation': request.metadata.get('invocation'),
            'round': request.metadata.get('house_tool_round'),
        }
        resolver = getattr(self.provider, 'model_for', None)
        if callable(resolver):
            record['requested_model'] = resolver(request.model_route)
        atomic_write_json(path, record)
        try:
            reply = self.provider.complete(request)
        except Exception as exc:
            record.update(status='uncertain', ended_at=utc_now_iso(), error_type=type(exc).__name__)
            atomic_write_json(path, record)
            raise
        record.update(status='completed', ended_at=utc_now_iso(), provider=reply.provider,
                      reported_model=reply.reported_model,
                      response_id=reply.response_id, usage=reply.usage)
        atomic_write_json(path, record)
        return reply
