import base64
import io
from datetime import date
from unittest.mock import patch

from PIL import Image
from freezegun import freeze_time

from odoo import Command
from odoo.addons.account_asset.tests.common import TestAccountAssetCommon
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged
from odoo.tests.common import new_test_user
from odoo.tools import format_date, formatLang

ACT = 'asset_operation.action_report_asset_operation'


@tagged('post_install', '-at_install')
class TestAssetOperation(TestAccountAssetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the assertions compare English texts, whatever language the database's users have
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.env.user.groups_id += cls.env.ref('asset_operation.group_asset_custodian')
        cls.category = cls.env['maintenance.equipment.category'].create({'name': 'Test Computers'})
        cls.equipment = cls.env['maintenance.equipment'].create({
            'name': 'Test Laptop',
            'category_id': cls.category.id,
            'model': 'ThinkPad X1',
            'serial_no': 'AA-TEST-001',
        })
        cls.other_equipment = cls.env['maintenance.equipment'].create({'name': 'Test Monitor'})
        cls.department = cls.env['hr.department'].create({'name': 'Test IT Department'})
        cls.employee = cls.env['hr.employee'].create({
            'name': 'First Employee',
            'department_id': cls.department.id,
            'job_title': 'Test Developer',
            'work_email': 'first.employee@example.com',
        })
        cls.other_employee = cls.env['hr.employee'].create({'name': 'Second Employee'})
        cls.custodian = new_test_user(
            cls.env, login='asset_custodian', groups='asset_operation.group_asset_custodian')
        cls.blank_pdf = cls._image('PDF', (595, 842))
        cls.user_role = cls.env.ref('sign.sign_item_role_user')
        cls.employee_role = cls.env.ref('sign.sign_item_role_employee')

    @classmethod
    def _image(cls, image_format, size=(1, 1)):
        """A white image in the given format, standing in for the rendered act and for the signatures."""
        data = io.BytesIO()
        Image.new('RGB', size, 'white').save(data, image_format)
        return data.getvalue()

    def _run_wizard(self, operation_type, user=None, **vals):
        wizard = self.env['asset.operation.wizard'].with_user(user or self.env.user).create({
            'equipment_id': self.equipment.id,
            'type': operation_type,
            **vals,
        })
        return wizard.action_confirm()

    def _operations(self, operation_type):
        return self.equipment.operation_ids.filtered(lambda operation: operation.type == operation_type)

    def _render(self, report, operation):
        return operation.env['ir.actions.report']._render_qweb_html(report, operation.ids)[0].decode()

    def _send_for_signature(self, operation, user=None):
        """Open the sign wizard for the act of the operation as the web client would, with the act rendering replaced
        by a blank page."""
        operation = operation.with_user(user or self.env.user)
        with patch.object(self.registry['ir.actions.report'], '_render_qweb_pdf',
                          return_value=(self.blank_pdf, 'pdf')) as render:
            action = operation.action_send_sign_request()
        render.assert_called_once_with(ACT, operation.ids)
        wizard = self.env['sign.send.request'].with_user(operation.env.user).with_context(**action['context'])
        return wizard.create({})

    def _commit_tracking(self):
        """Write the tracking notes of the operations done so far, as committing their transaction would."""
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _sign(self, request):
        """Sign every field of the request as its signer with a one-pixel signature."""
        signature = 'data:image/png;base64,' + base64.b64encode(self._image('PNG')).decode()
        for item in request.request_item_ids:
            boxes = request.template_id.sign_item_ids.filtered(lambda box: box.responsible_id == item.role_id)
            item.sudo()._sign({str(box.id): signature for box in boxes})

    def test_assign(self):
        """Assigning records an assignation, puts the employee on the equipment and opens the operation."""
        self.equipment.write({'equipment_assign_to': 'department', 'department_id': self.department.id})

        action = self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))

        operation = self.equipment.operation_ids
        self.assertEqual((operation.type, operation.employee_id, operation.date, operation.is_returned),
                         ('assignation', self.employee, date(2026, 1, 10), False))
        self.assertEqual(self.equipment.equipment_assign_to, 'employee')
        self.assertEqual(self.equipment.employee_id, self.employee)
        self.assertFalse(self.equipment.department_id)
        self.assertEqual(self.equipment.assign_date, date(2026, 1, 10))
        self.assertEqual(self.employee.equipment_ids, self.equipment)
        self.assertEqual((action['res_model'], action['res_id'], action['view_mode']),
                         ('asset.operation', operation.id, 'form'))

    def test_assign_and_return_leave_no_notes(self):
        """Assigning and returning post no note on the operations nor any tracking note on the equipment, as the
        history shows the operations. The chatters stay for documents and notes."""
        # the accounting test base switches the chatter features off for every test, this one needs them on
        self.env = self.env(context=dict(self.env.context, tracking_disable=False, mail_create_nolog=False,
                                         mail_notrack=False))
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        self._commit_tracking()
        self._run_wizard('return', date=date(2026, 3, 1))
        self._commit_tracking()

        self.assertFalse(self.equipment.operation_ids.message_ids)
        self.assertFalse(self.equipment.message_ids.tracking_value_ids)

    def test_assign_several(self):
        """Different pieces of equipment can be assigned to the same employee at the same time."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10),
                         equipment_id=self.other_equipment.id)

        self.assertEqual(self.employee.equipment_ids, self.equipment + self.other_equipment)

    def test_return(self):
        """Returning records a return closing the assignation, frees the equipment and opens the return."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        assignation = self.equipment.operation_ids

        action = self._run_wizard('return', date=date(2026, 3, 1))

        operation = self.equipment.operation_ids - assignation
        self.assertEqual((operation.type, operation.employee_id, operation.date, operation.assignation_id),
                         ('return', self.employee, date(2026, 3, 1), assignation))
        self.assertTrue(assignation.is_returned)
        self.assertEqual(assignation.return_ids, operation)
        self.assertFalse(self.equipment.employee_id)
        self.assertFalse(self.equipment.assign_date)
        self.assertEqual(action['res_id'], operation.id)

    def test_return_without_running_assignation(self):
        """Returning equipment that was assigned outside the wizard only frees it and closes the wizard."""
        self.equipment.employee_id = self.employee

        action = self._run_wizard('return')

        self.assertFalse(self.equipment.employee_id)
        self.assertFalse(self.equipment.operation_ids)
        self.assertEqual(action, {'type': 'ir.actions.act_window_close'})

    def test_wizard_form(self):
        """The wizard form requires an employee only when assigning."""
        Wizard = self.env['asset.operation.wizard']
        assign = Form(Wizard.with_context(default_equipment_id=self.equipment.id, default_type='assignation'))
        with self.assertRaises(AssertionError):
            assign.save()
        assign.employee_id = self.employee
        assign.save().action_confirm()
        self.assertEqual(self.equipment.employee_id, self.employee)

        unassign = Form(Wizard.with_context(default_equipment_id=self.equipment.id, default_type='return'))
        unassign.save().action_confirm()
        self.assertTrue(self._operations('assignation').is_returned)

    def test_days_in_use(self):
        """Days are counted from the assignation up to today, then up to the return, on both operations."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        assignation = self.equipment.operation_ids

        with freeze_time('2026-01-20'):
            self.assertEqual(assignation.days_in_use, 10)
        self._run_wizard('return', date=date(2026, 1, 25))
        with freeze_time('2026-03-01'):
            self.assertEqual(self.equipment.operation_ids.mapped('days_in_use'), [15, 15])

    def test_days_in_use_before_assignation_date(self):
        """An assignation dated in the future counts no days yet instead of a negative number."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))

        with freeze_time('2026-01-05'):
            self.assertEqual(self.equipment.operation_ids.days_in_use, 0)

    def test_days_in_use_without_dates(self):
        """Unsaved operations without dates count no days, whatever their type."""
        Operation = self.env['asset.operation']

        self.assertEqual(Operation.new({'type': 'assignation'}).days_in_use, 0)
        self.assertEqual(Operation.new({'type': 'return'}).days_in_use, 0)

    def test_cannot_assign_twice(self):
        """Assigned equipment cannot be assigned again until it is returned, even on the return day."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))

        with self.assertRaises(ValidationError):
            self._run_wizard('assignation', employee_id=self.other_employee.id, date=date(2026, 2, 1))
        self.assertEqual(self.equipment.employee_id, self.employee)

        self._run_wizard('return', date=date(2026, 2, 1))
        self._run_wizard('assignation', employee_id=self.other_employee.id, date=date(2026, 2, 1))

        self.assertEqual(self.equipment.employee_id, self.other_employee)
        self.assertEqual(self.equipment.operation_ids.mapped('type'), ['assignation', 'return', 'assignation'])

        self._run_wizard('return', date=date(2026, 2, 10))
        self.assertEqual([(assignation.date, assignation.return_ids.date)
                          for assignation in self._operations('assignation').sorted('date')],
                         [(date(2026, 1, 10), date(2026, 2, 1)), (date(2026, 2, 1), date(2026, 2, 10))])

    def test_cannot_return_before_assignation(self):
        """A return dated before its assignation, or without any assignation, is refused."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 3, 1))

        with self.assertRaises(ValidationError):
            self._run_wizard('return', date=date(2026, 2, 1))
        with self.assertRaises(ValidationError):
            self.env['asset.operation'].create({
                'equipment_id': self.equipment.id, 'employee_id': self.employee.id, 'type': 'return',
                'date': date(2026, 3, 2)})
        self.assertEqual(self.equipment.employee_id, self.employee)

    def test_record_past_assignation(self):
        """A past assignation with its return can be recorded next to the current one unless the periods overlap."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 2, 1))
        past = {'equipment_id': self.equipment.id, 'employee_id': self.other_employee.id, 'date': date(2026, 1, 10)}

        def returned(return_date):
            return {**past, 'return_ids': [Command.create({**past, 'type': 'return', 'date': return_date})]}

        with self.assertRaises(ValidationError):
            self.env['asset.operation'].create(returned(date(2026, 2, 2)))

        assignation = self.env['asset.operation'].create(returned(date(2026, 2, 1)))
        self.assertTrue(assignation.is_returned)
        self.assertEqual(assignation.days_in_use, 22)
        self.assertEqual(assignation.return_ids.days_in_use, 22)

    def test_send_assignation_act_for_signature(self):
        """Sending the assignation act opens the sign wizard on a one-off template of the act, with a signature field
        and a signer for the custodian on the left blank and for the employee on the right one."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        assignation = self.equipment.operation_ids
        parties = [(self.user_role, self.env.user.partner_id), (self.employee_role, self.employee.work_contact_id)]

        self.assertFalse(assignation.act_pdf)
        wizard = self._send_for_signature(assignation)

        template = wizard.template_id
        self.assertFalse(template.active)
        self.assertEqual(template.name, 'Asset Assignation Act No. %s.pdf' % assignation.id)
        self.assertEqual(base64.b64decode(template.datas), self.blank_pdf)
        boxes = template.sign_item_ids.sorted('posX')
        self.assertEqual([(box.type_id.item_type, box.responsible_id) for box in boxes],
                         [('signature', self.user_role), ('signature', self.employee_role)])
        self.assertEqual([(signer.role_id, signer.partner_id) for signer in wizard.signer_ids], parties)
        self.assertTrue(wizard.is_user_signer)

        wizard.send_request()

        request = assignation.sign_request_id
        self.assertEqual(request.template_id, template)
        self.assertEqual([(item.role_id, item.partner_id) for item in request.request_item_ids], parties)
        self.assertEqual(assignation.sign_state, 'sent')
        self.assertEqual(base64.b64decode(assignation.act_pdf), self.blank_pdf)

    def test_send_return_act_for_signature(self):
        """Sending the return act puts the employee on the left blank and the custodian on the right one, on the
        return operation only."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        self._run_wizard('return', date=date(2026, 3, 1))
        operation = self._operations('return')

        wizard = self._send_for_signature(operation)

        template = wizard.template_id
        self.assertEqual(template.name, 'Asset Return Act No. %s.pdf' % operation.id)
        self.assertEqual([box.responsible_id for box in template.sign_item_ids.sorted('posX')],
                         [self.employee_role, self.user_role])
        self.assertEqual([signer.partner_id for signer in wizard.signer_ids],
                         [self.employee.work_contact_id, self.env.user.partner_id])

        wizard.send_request()

        self.assertEqual(operation.sign_request_id.template_id, template)
        self.assertEqual(operation.sign_state, 'sent')
        self.assertFalse(self._operations('assignation').sign_request_id)
        action = operation.action_preview_act()
        self.assertEqual((action['res_id'], action['views'], action['target'], action['name']), (
            operation.id, [(self.env.ref('asset_operation.view_asset_operation_form_act').id, 'form')], 'new',
            'Asset Return Act No. %s' % operation.id))

    def test_signed_act(self):
        """Once the custodian and the employee have signed, the operation shows the act as signed and previews the
        signed document."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        assignation = self.equipment.operation_ids
        self._send_for_signature(assignation).send_request()
        request = assignation.sign_request_id

        self._sign(request)

        self.assertEqual(assignation.sign_state, 'signed')
        self.assertEqual(assignation.act_pdf, request.completed_document)
        self.assertNotEqual(base64.b64decode(assignation.act_pdf), self.blank_pdf)
        self.assertEqual(assignation.action_preview_act()['name'], 'Asset Assignation Act No. %s' % assignation.id)

    def test_paper_signed_act(self):
        """An act signed on paper is uploaded as a scan, which the preview shows instead of any e-signed version."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        assignation = self.equipment.operation_ids
        scan = base64.b64encode(self.blank_pdf)

        assignation.write({'paper_act': scan, 'paper_act_filename': 'act.pdf'})

        self.assertEqual(assignation.act_pdf, scan)
        action = assignation.action_preview_act()
        self.assertEqual((action['res_id'], action['name']),
                         (assignation.id, 'Asset Assignation Act No. %s' % assignation.id))

    def test_custodian_sends_for_signature(self):
        """A custodian can send the act for signature, signing as the handing-over party, without Sign rights."""
        self._run_wizard('assignation', user=self.custodian, employee_id=self.employee.id, date=date(2026, 1, 10))

        self._send_for_signature(self.equipment.operation_ids, user=self.custodian).send_request()

        request = self.equipment.operation_ids.sign_request_id
        self.assertEqual(request.create_uid, self.custodian)
        self.assertEqual([(item.role_id, item.partner_id) for item in request.request_item_ids], [
            (self.user_role, self.custodian.partner_id),
            (self.employee_role, self.employee.work_contact_id),
        ])

    def test_sign_wizard_without_operation(self):
        """Requests sent from the Sign app itself are not linked to any operation."""
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))
        wizard = self._send_for_signature(self.equipment.operation_ids)

        wizard.with_context(asset_operation_id=False).send_request()

        self.assertFalse(self.equipment.operation_ids.sign_request_id)
        self.assertTrue(wizard.template_id.sign_request_ids)

    def test_book_value(self):
        """The equipment shows the book value of its accounting asset, also to a custodian without accounting rights."""
        asset = self.create_asset(1200, 'yearly', 5, already_depreciated_amount_import=480)
        self.assertFalse(self.equipment.book_value)

        self.equipment.account_asset_id = asset

        self.assertEqual(self.equipment.book_value, 720)
        self.assertEqual(self.equipment.currency_id, asset.currency_id)
        self.assertEqual(self.equipment.with_user(self.custodian).book_value, 720)

    def test_assignation_act(self):
        """The assignation act shows the employee, the asset and its book value."""
        self.equipment.account_asset_id = self.create_asset(1200, 'yearly', 5, already_depreciated_amount_import=480)
        self._run_wizard('assignation', employee_id=self.employee.id, date=date(2026, 1, 10))

        html = self._render(ACT, self.equipment.operation_ids)

        for value in ('Asset Assignation Act No.', format_date(self.env, date(2026, 1, 10)), 'First Employee',
                      'Job Position:', 'Test Developer', 'Department:', 'Test IT Department', 'Test Laptop',
                      'Test Computers', 'ThinkPad X1', 'AA-TEST-001', formatLang(self.env, 720),
                      'handed over to the employee', 'Handed over by:', 'Received by:'):
            self.assertIn(value, html)
        self.assertNotIn('Returned by:', html)

    def test_return_act(self):
        """The return act shows the return date and leaves out the lines an employee has no data for."""
        self._run_wizard('assignation', employee_id=self.other_employee.id, date=date(2026, 1, 10))
        self._run_wizard('return', date=date(2026, 3, 1))

        html = self._render(ACT, self._operations('return'))

        for value in ('Asset Return Act No.', 'Second Employee', format_date(self.env, date(2026, 3, 1)),
                      'returned by the employee', 'Returned by:', 'Accepted by:'):
            self.assertIn(value, html)
        self.assertNotIn('Handed over by:', html)
        self.assertNotIn('Job Position:', html)
        self.assertNotIn('Department:', html)
        self.assertNotIn(formatLang(self.env, 0), html)

    def test_custodian_without_accounting_rights(self):
        """A custodian can assign, print and return without any accounting access right."""
        self._run_wizard('assignation', user=self.custodian, employee_id=self.employee.id, date=date(2026, 1, 10))
        assignation = self.equipment.operation_ids.with_user(self.custodian)
        html = self._render(ACT, assignation)
        self._run_wizard('return', user=self.custodian, date=date(2026, 3, 1))

        self.assertIn('First Employee', html)
        self.assertTrue(assignation.is_returned)
        self.assertFalse(self.equipment.employee_id)

    def test_internal_user_has_no_access(self):
        """Users who are not custodians cannot read the operation history."""
        user = new_test_user(self.env, login='internal_user')

        with self.assertRaises(AccessError):
            self.env['asset.operation'].with_user(user).search([])
