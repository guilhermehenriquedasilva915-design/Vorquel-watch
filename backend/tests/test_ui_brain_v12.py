import unittest

from vorquel_watch.analyze_drafts import DraftCandidate, DraftEvidenceRef
from vorquel_watch.ui import VorquelWatchUI


class FakeVar:
    def __init__(self, value=''):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class FakeTree:
    def __init__(self, selected=()):
        self.selected = tuple(selected)
        self.deleted = []
        self.rows = {}
    def selection(self):
        return self.selected
    def delete(self, item):
        self.deleted.append(item)
        self.rows.pop(item, None)
    def exists(self, item):
        return item in self.rows or item not in self.deleted
    def get_children(self):
        return tuple(self.rows)
    def insert(self, _parent, _where, *, iid, values):
        self.rows[iid] = tuple(values)


class UiCommandTests(unittest.TestCase):
    def make_ui(self):
        ui = object.__new__(VorquelWatchUI)
        ui.search_var = FakeVar()
        ui.knowledge_id_var = FakeVar()
        ui.draft_source_var = FakeVar()
        ui.draft_domain_var = FakeVar('general')
        ui.draft_start_var = FakeVar('0')
        ui.draft_end_var = FakeVar()
        ui.status_var = FakeVar()
        ui.drafts = FakeTree()
        ui.drafts_by_id = {}
        ui.knowledge_results_by_id = {}
        ui.calls = []
        ui._run_async = lambda args, **kwargs: ui.calls.append((args, kwargs))
        ui._show_json = lambda data, status: ui.calls.append(('show', data, status))
        return ui

    def test_analyze_drafts_is_read_only_cli_action(self):
        ui = self.make_ui()
        ui.draft_source_var.set('src_abc')
        ui.draft_domain_var.set('sales')
        ui.draft_start_var.set('1000')
        ui.draft_end_var.set('5000')
        ui.analyze_source_drafts()
        self.assertEqual(
            ui.calls[0][0],
            [
                'brain', 'analyze-drafts', '--source-id', 'src_abc',
                '--domain', 'sales', '--start-ms', '1000', '--end-ms', '5000',
            ],
        )
        self.assertNotIn('propose-draft', ui.calls[0][0])

    def test_propose_draft_requires_explicit_confirmation(self):
        ui = self.make_ui()
        draft = {'draft_id': 'draft_abc', 'title': 'Reviewed draft'}
        ui.drafts = FakeTree(('draft_abc',))
        ui.drafts_by_id = {'draft_abc': draft}
        import vorquel_watch.ui as ui_module
        old = ui_module.messagebox.askyesno
        try:
            ui_module.messagebox.askyesno = lambda *args, **kwargs: True
            ui.propose_selected_draft()
        finally:
            ui_module.messagebox.askyesno = old
        command = ui.calls[0][0]
        self.assertEqual(command[:3], ['brain', 'propose-draft', '--draft-json'])
        self.assertIn('draft_abc', command[3])

    def test_discard_draft_never_calls_cli(self):
        ui = self.make_ui()
        ui.drafts = FakeTree(('draft_abc',))
        ui.drafts_by_id = {'draft_abc': {'draft_id': 'draft_abc'}}
        ui.discard_selected_draft()
        self.assertEqual(ui.drafts_by_id, {})
        self.assertEqual(ui.calls, [])

    def test_draft_evidence_can_be_inspected(self):
        ui = self.make_ui()
        draft = {'draft_id': 'draft_abc', 'evidence_refs': [{'source_id': 'src_abc'}]}
        ui.drafts = FakeTree(('draft_abc',))
        ui.drafts_by_id = {'draft_abc': draft}
        ui.show_draft_details()
        self.assertEqual(ui.calls[0][0], 'show')
        self.assertEqual(ui.calls[0][1], {'draft': draft})

    def test_draft_can_be_edited_without_persistence(self):
        ui = self.make_ui()
        draft = DraftCandidate(
            draft_id='draft_original',
            source_id='src_abc',
            title='Original title',
            summary='Original summary',
            knowledge_type='CLAIM',
            epistemic_status='DECLARADO',
            domain='sales',
            evidence_refs=(DraftEvidenceRef(source_id='src_abc', start_ms=1000),),
        ).as_dict()
        ui.drafts = FakeTree(('draft_original',))
        ui.drafts.rows['draft_original'] = ()
        ui.drafts_by_id = {'draft_original': draft}
        ui._edit_draft_fields = lambda value: {
            **value,
            'title': 'Reviewed title',
            'summary': 'Reviewed summary',
        }
        ui.edit_selected_draft()
        self.assertNotIn('draft_original', ui.drafts_by_id)
        edited = next(iter(ui.drafts_by_id.values()))
        self.assertEqual(edited['title'], 'Reviewed title')
        self.assertEqual(ui.calls, [])

    def test_proposed_candidate_moves_to_separate_review_queue(self):
        ui = self.make_ui()
        ui.drafts = FakeTree(('draft_abc',))
        ui.drafts.rows['draft_abc'] = ()
        ui.drafts_by_id = {'draft_abc': {'draft_id': 'draft_abc'}}
        ui.refresh_candidates = lambda: ui.calls.append(('refresh_candidates',))
        ui._after_draft_proposed({'candidate': {'candidate_id': 'knd_abc'}}, 'draft_abc')
        self.assertNotIn('draft_abc', ui.drafts_by_id)
        self.assertEqual(ui.calls[-1], ('refresh_candidates',))

    def test_candidate_approval_and_rejection_remain_separate_actions(self):
        ui = self.make_ui()
        ui.candidates = FakeTree()
        ui.candidates.rows['knd_abc'] = ()
        ui.checked_candidate_ids = {'knd_abc'}
        ui._run_batch_review = lambda ids, action: ui.calls.append((ids, action))
        import vorquel_watch.ui as ui_module
        old = ui_module.messagebox.askyesno
        try:
            ui_module.messagebox.askyesno = lambda *args, **kwargs: True
            ui.approve_selected()
            ui.reject_selected()
        finally:
            ui_module.messagebox.askyesno = old
        self.assertEqual(ui.calls, [(['knd_abc'], 'approve'), (['knd_abc'], 'reject')])

    def test_synthesize_delegates_to_cli(self):
        ui = self.make_ui()
        ui.search_var.set('diagnóstico')
        ui.synthesize_knowledge()
        self.assertEqual(ui.calls[0][0], ['brain', 'synthesize', 'diagnóstico'])

    def test_search_selects_single_result_id(self):
        ui = self.make_ui()
        ui._after_knowledge_search({'items': [{'knowledge_id': 'knw_abc'}]})
        self.assertEqual(ui.knowledge_id_var.get(), 'knw_abc')

    def test_withdraw_delegates_after_confirm(self):
        ui = self.make_ui()
        ui.knowledge_id_var.set('knw_abc')
        ui._ask_reason = lambda *_: 'outdated'
        import vorquel_watch.ui as ui_module
        old = ui_module.messagebox.askyesno
        try:
            ui_module.messagebox.askyesno = lambda *args, **kwargs: True
            ui.withdraw_knowledge_ui()
        finally:
            ui_module.messagebox.askyesno = old
        self.assertEqual(
            ui.calls[0][0],
            ['brain', 'withdraw', 'knw_abc', '--reason', 'outdated'],
        )

    def test_supersede_delegates_after_confirm(self):
        ui = self.make_ui()
        ui.knowledge_id_var.set('knw_old')
        answers = iter(['knw_new', 'corrected'])
        ui._ask_text = lambda *_: next(answers)
        ui._ask_reason = lambda *_: next(answers)
        import vorquel_watch.ui as ui_module
        old = ui_module.messagebox.askyesno
        try:
            ui_module.messagebox.askyesno = lambda *args, **kwargs: True
            ui.supersede_knowledge_ui()
        finally:
            ui_module.messagebox.askyesno = old
        self.assertEqual(
            ui.calls[0][0],
            ['brain', 'supersede', 'knw_old', 'knw_new', '--reason', 'corrected'],
        )


if __name__ == '__main__':
    unittest.main()
