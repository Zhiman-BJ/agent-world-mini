import unittest
from env_gen.tool_gen.compiler import ToolGenerator, _removed_resource_annotations


class ResourceRepairPreservationTests(unittest.TestCase):
    def original(self):
        return {'outputSchema': {'oneOf': [{'properties': {'data': {'properties': {
            'export_file': {'type': ['string', 'null'], 'x-resource-scope': 'files', 'x-resource-kind': 'file'}
        }}}}]}}

    def test_output_reference_annotation_removal_is_detected(self):
        before = self.original()
        after = self.original()
        field = after['outputSchema']['oneOf'][0]['properties']['data']['properties']['export_file']
        del field['x-resource-scope']
        del field['x-resource-kind']
        self.assertEqual(_removed_resource_annotations(before, after), ['outputSchema.properties.data.properties.export_file'])

    def test_nullable_reference_and_scope_correction_are_preserved(self):
        before = self.original()
        after = self.original()
        after['outputSchema']['oneOf'][0]['properties']['data']['properties']['export_file']['x-resource-scope'] = 'correct_scope'
        self.assertEqual(_removed_resource_annotations(before, after), [])

    def test_alternative_branch_reordering_preserves_reference(self):
        before = self.original()
        after = self.original()
        after['outputSchema']['oneOf'].insert(0, {'type': 'null'})
        self.assertEqual(_removed_resource_annotations(before, after), [])

    def test_repair_prompt_describes_public_references(self):
        prompt = ToolGenerator._build_repair_prompt(1, [{'tool': 'export_data'}])
        self.assertIn('x-resource-scope', prompt)
        self.assertIn('公开结果由 Runtime 验证并保留 Scope 内相对路径', prompt)

    def test_array_reference_annotation_can_move_to_item_schema(self):
        before = {'inputSchema': {'properties': {'files': {
            'type': 'array', 'items': {'type': 'string'},
            'x-resource-scope': 'reports', 'x-resource-kind': 'file',
        }}}}
        after = {'inputSchema': {'properties': {'files': {
            'type': 'array', 'items': {'type': 'string',
                'x-resource-scope': 'reports', 'x-resource-kind': 'file'},
        }}}}
        self.assertEqual(_removed_resource_annotations(before, after), [])


if __name__ == '__main__':
    unittest.main()
