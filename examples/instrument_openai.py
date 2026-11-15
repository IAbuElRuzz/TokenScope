"""Call after your own authenticated SDK request completes; no API keys go to TokenScope."""
from backend.ingest import usage_event, publish
from backend.core.config import api_token, data_directory


def record_response(response, session_id):
    event = usage_event('openai', response.model, session_id, response.id, response.usage.model_dump())
    return publish(event, api_token(data_directory()))
