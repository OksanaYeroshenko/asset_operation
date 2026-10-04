from odoo import models, fields


class AssetOperationWizard(models.TransientModel):
    _name = 'asset.operation.wizard'
    _description = 'Assign or Return an Asset'

    equipment_id = fields.Many2one('maintenance.equipment', string='Asset', required=True)
    type = fields.Selection([('assignation', 'Assignation'), ('return', 'Return')], required=True,
                            default='assignation')
    employee_id = fields.Many2one('hr.employee', string='Employee')
    date = fields.Date(string='Operation Date', required=True, default=fields.Date.context_today)

    def action_confirm(self):
        self.ensure_one()
        if self.type == 'assignation':
            operation = self.env['asset.operation'].create({
                'equipment_id': self.equipment_id.id,
                'employee_id': self.employee_id.id,
                'type': 'assignation',
                'date': self.date,
            })
            self.equipment_id.write({
                'equipment_assign_to': 'employee',
                'employee_id': self.employee_id.id,
                'department_id': False,
                'assign_date': self.date,
            })
        else:
            assignation = self.env['asset.operation'].search([
                ('equipment_id', '=', self.equipment_id.id), ('type', '=', 'assignation'), ('is_returned', '=', False),
            ], limit=1)
            operation = assignation and self.env['asset.operation'].create({
                'equipment_id': self.equipment_id.id,
                'employee_id': assignation.employee_id.id,
                'type': 'return',
                'date': self.date,
                'assignation_id': assignation.id,
            })
            self.equipment_id.write({'employee_id': False, 'assign_date': False})
        if not operation:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.operation',
            'res_id': operation.id,
            'view_mode': 'form',
        }
