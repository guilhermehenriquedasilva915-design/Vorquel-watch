import unittest

from vorquel_watch.ui import VorquelWatchUI


class FakeVar:
    def __init__(self, value=''):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class UiCommandTests(unittest.TestCase):
    def make_ui(self):
        ui = object.__new__(VorquelWatchUI)
        ui.search_var = FakeVar()
        ui.knowledge_id_var = FakeVar()
        ui.knowledge_results_by_id = {}
        ui.calls = []
        ui._run_async = lambda args, **kwargs: ui.calls.append((args, kwargs))
        ui._show_json = lambda data, status: ui.calls.append(('show', data, status))
        return ui

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
