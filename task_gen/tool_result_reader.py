"""Session-local paging of already observed tool results; never accepts paths."""
import secrets


class ResultReader:
    name = 'read_tool_result'

    def __init__(self, page_chars: int):
        if type(page_chars) is not int or not 1 <= page_chars <= 12000:
            raise ValueError('tool_result_page_chars must be an integer in [1, 12000]')
        self.page_chars = page_chars
        # ponytail: retained in memory for this session; use private disk storage if results become too large.
        self.results: dict[str, str] = {}
        self.tool = {
            'name': self.name,
            'description': 'Read a page of a previous long tool result from this session. '
                           'Only returned result_id values are accepted, never file paths. '
                           'Offsets and lengths count Unicode characters in the original JSON text. '
                           'Follow next_offset to continue. This does not execute an environment tool.',
            'inputSchema': {
                'type': 'object', 'properties': {
                    'result_id': {'type': 'string'},
                    'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
                    'length': {'type': 'integer', 'minimum': 1, 'maximum': page_chars, 'default': page_chars},
                }, 'required': ['result_id'], 'additionalProperties': False,
            },
            'annotations': {'readOnlyHint': True, 'openWorldHint': False},
        }

    def preview(self, text: str) -> dict:
        ident = secrets.token_hex(16)
        self.results[ident] = text
        return self.read({'result_id': ident})

    def read(self, arguments: dict) -> dict:
        if arguments.keys() - {'result_id', 'offset', 'length'}:
            raise ValueError('Only result_id, offset and length are accepted')
        ident = arguments.get('result_id')
        if not isinstance(ident, str) or ident not in self.results:
            raise ValueError('Unknown result_id for this session')
        text = self.results[ident]
        offset, length = arguments.get('offset', 0), arguments.get('length', self.page_chars)
        if type(offset) is not int or not 0 <= offset <= len(text):
            raise ValueError('offset must be an integer within the result')
        if type(length) is not int or not 1 <= length <= self.page_chars:
            raise ValueError(f'length must be an integer in [1, {self.page_chars}]')
        end = min(offset + length, len(text))
        return {'result_id': ident, 'total_chars': len(text), 'offset': offset,
                'next_offset': end, 'has_more': end < len(text),
                'content': text[offset:end], 'read_tool': self.name}
