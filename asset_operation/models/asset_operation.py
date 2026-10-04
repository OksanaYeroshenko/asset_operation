import base64
import datetime

from odoo import models, fields, api, _, Command
from odoo.exceptions import ValidationError

SIGNATURE_Y = 0.525
SIGNATURE_X_LEFT = 0.174
SIGNATURE_X_RIGHT = 0.601
SIGNATURE_WIDTH = 0.160
SIGNATURE_HEIGHT = 0.050


class AssetOperation(models.Model):
    _name = 'asset.operation'
    _inherit = 'mail.thread'
    _description = 'Asset Operation'
    _order = 'date desc, id desc'
    _rec_name = 'equipment_id'

    equipment_id = fields.Many2one('maintenance.equipment', string='Asset', required=True, index=True)
    category_id = fields.Many2one(related='equipment_id.category_id')
    model = fields.Char(related='equipment_id.model')
    serial_no = fields.Char(related='equipment_id.serial_no')
    company_id = fields.Many2one(related='equipment_id.company_id')
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True, index=True)
    department_id = fields.Many2one(related='employee_id.department_id')
    type = fields.Selection([('assignation', 'Assignation'), ('return', 'Return')], required=True,
                            default='assignation')
    date = fields.Date(required=True)
    assignation_id = fields.Many2one('asset.operation', string='Assignation', index=True,
                                     domain="[('type', '=', 'assignation')]")
    return_ids = fields.One2many('asset.operation', 'assignation_id', string='Return')
    is_returned = fields.Boolean(string='Returned', compute='_compute_is_returned', store=True)
    days_in_use = fields.Integer(string='Days in Use', compute='_compute_days_in_use')
    sign_request_id = fields.Many2one('sign.request', string='E-Signature Request', readonly=True, copy=False)
    sign_state = fields.Selection(related='sign_request_id.state', string='E-Signature')
    paper_act = fields.Binary(string='Paper-Signed Act')
    paper_act_filename = fields.Char()
    act_pdf = fields.Binary(string='Act PDF', compute='_compute_act_pdf', compute_sudo=True)

    @api.depends('return_ids')
    def _compute_is_returned(self):
        for operation in self:
            operation.is_returned = bool(operation.return_ids)

    @api.depends('type', 'date', 'assignation_id.date', 'return_ids.date')
    def _compute_days_in_use(self):
        """Days from the assignation to its return, or to today while the asset is still out."""
        today = fields.Date.context_today(self)
        for operation in self:
            if operation.type == 'assignation':
                start, end = operation.date, operation.return_ids[:1].date or today
            else:
                start, end = operation.assignation_id.date, operation.date or today
            operation.days_in_use = max((end - (start or end)).days, 0)

    @api.depends('paper_act', 'sign_request_id.completed_document', 'sign_request_id.template_id.attachment_id.datas')
    def _compute_act_pdf(self):
        """The scan of an act signed on paper, or else the latest document of its e-signature request."""
        for operation in self:
            request = operation.sign_request_id
            operation.act_pdf = (operation.paper_act or request.completed_document
                                 or request.template_id.attachment_id.datas)

    @api.constrains('type', 'date', 'assignation_id')
    def _check_return(self):
        for operation in self.filtered(lambda operation: operation.type == 'return'):
            if not operation.assignation_id:
                raise ValidationError(_("A return needs the assignation it closes."))
            if operation.date < operation.assignation_id.date:
                raise ValidationError(_("The return date cannot be earlier than the assignation date."))

    @api.constrains('equipment_id', 'type', 'date', 'assignation_id')
    def _check_not_assigned_twice(self):
        for assignation in self.filtered(lambda operation: operation.type == 'assignation') | self.assignation_id:
            # another assignation of the same asset whose period overlaps this one
            overlapping = self.search([
                ('id', '!=', assignation.id),
                ('type', '=', 'assignation'),
                ('equipment_id', '=', assignation.equipment_id.id),
                ('date', '<', assignation.return_ids[:1].date or datetime.date.max),
                '|', ('is_returned', '=', False), ('return_ids.date', '>', assignation.date),
            ], limit=1)
            if overlapping:
                raise ValidationError(_(
                    "%(asset)s is already assigned to %(employee)s on this date.",
                    asset=assignation.equipment_id.display_name, employee=overlapping.employee_id.name))

    def _get_act_name(self):
        self.ensure_one()
        if self.type == 'return':
            return _("Asset Return Act No. %s", self.id)
        return _("Asset Assignation Act No. %s", self.id)

    def action_send_sign_request(self):
        self.ensure_one()
        report = 'asset_operation.action_report_asset_operation'
        pdf = self.env['ir.actions.report']._render_qweb_pdf(report, self.ids)[0]
        custodian = ('sign.sign_item_role_user', self.env.user.partner_id)
        employee = ('sign.sign_item_role_employee', self.employee_id.work_contact_id)
        parties = [custodian, employee] if self.type == 'assignation' else [employee, custodian]
        attachment = self.env['ir.attachment'].create({
            'name': self._get_act_name() + '.pdf',
            'datas': base64.b64encode(pdf),
        })
        template = self.env['sign.template'].create({
            'attachment_id': attachment.id,
            'active': False,
            'sign_item_ids': [Command.create({
                'type_id': self.env.ref('sign.sign_item_type_signature').id,
                'responsible_id': self.env.ref(role).id,
                'page': 1,
                'posX': pos_x,
                'posY': SIGNATURE_Y,
                'width': SIGNATURE_WIDTH,
                'height': SIGNATURE_HEIGHT,
            }) for (role, partner), pos_x in zip(parties, (SIGNATURE_X_LEFT, SIGNATURE_X_RIGHT))],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Send for E-Signature'),
            'res_model': 'sign.send.request',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_template_id': template.id,
                'default_signer_ids': [Command.create({'role_id': self.env.ref(role).id, 'partner_id': partner.id})
                                       for role, partner in parties],
                'asset_operation_id': self.id,
            },
        }

    def action_preview_act(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self._get_act_name(),
            'res_model': 'asset.operation',
            'res_id': self.id,
            'views': [(self.env.ref('asset_operation.view_asset_operation_form_act').id, 'form')],
            'target': 'new',
        }
